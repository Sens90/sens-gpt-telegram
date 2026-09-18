"""Cache BrawlTrack API data and enrich it from public brawler pages."""
from __future__ import annotations
import json, os, re, time
from datetime import datetime, timezone
from typing import Any, Dict
import requests
from bs4 import BeautifulSoup
from brawltrack_client import brawlers, normalize_brawler_catalog

SUPABASE_URL=os.getenv("SUPABASE_URL","").rstrip("/")
SUPABASE_KEY=os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY","")
TIMEOUT=float(os.getenv("SUPABASE_TIMEOUT","15"))
PAGE_TIMEOUT=float(os.getenv("BRAWLTRACK_PAGE_TIMEOUT","15"))
PAGE_DELAY=max(0.0,float(os.getenv("BRAWLTRACK_PAGE_DELAY","0.12")))
UPSERT_BATCH=max(1,int(os.getenv("BRAWLTRACK_UPSERT_BATCH","12")))
UA="SensGPT-TitaniAbusivi/1.0"

def _num(row,*keys):
    for key in keys:
        value=row.get(key)
        if value is None: continue
        if isinstance(value,str): value=value.strip().rstrip("%").replace(",",".")
        try:return float(value)
        except (TypeError,ValueError): pass
    return None

def _first(row,*keys):
    for key in keys:
        value=row.get(key)
        if value not in (None,"",[],{}): return value
    return None

def _headers():
    if not SUPABASE_URL or not SUPABASE_KEY: raise RuntimeError("SUPABASE_URL/service role key missing")
    return {"apikey":SUPABASE_KEY,"Authorization":f"Bearer {SUPABASE_KEY}","Content-Type":"application/json","Prefer":"resolution=merge-duplicates,return=minimal"}

def _known_ids():
    r=requests.get(f"{SUPABASE_URL}/rest/v1/brawlers_catalog",params={"select":"brawler_id"},headers=_headers(),timeout=TIMEOUT);r.raise_for_status()
    return {int(x["brawler_id"]) for x in r.json()}

def _section(text,start,ends):
    pos=text.find(start)
    if pos<0:return ""
    pos+=len(start); candidates=[text.find(e,pos) for e in ends]; candidates=[p for p in candidates if p>=0]
    return text[pos:min(candidates) if candidates else len(text)].strip()

def _parse_builds(raw):
    if not raw:return []
    head=raw.split("Hypercharge",1)[0]
    chunks=re.split(r"(?=#\s*\d+\b)",head)
    out=[]
    for chunk in chunks:
        m=re.search(r"#\s*(\d+)\s*(?:Most Used)?\s*(.*?)(?:Use Rate\s*)?(\d+(?:\.\d+)?)\s*%\s*(?:Use Rate)?",chunk,re.I)
        if not m: continue
        names=re.sub(r"\b(?:Alternative Loadouts|Current popularity|Based on tracked loadouts)\b"," ",m.group(2),flags=re.I)
        names=[x.strip() for x in re.split(r"\s{2,}|\s+(?=[A-Z][A-Z '&-]+(?:\s|$))",names) if x.strip()]
        out.append({"rank":int(m.group(1)),"items_raw":" ".join(names),"use_rate":float(m.group(3))})
    return out

def _parse_modes(raw):
    if not raw or raw.strip().casefold()=="insufficient mode data.":return []
    # BrawlTrack renders this section as a flat sequence:
    # MODE LABEL -> WIN RATE %.  Parse boundaries from percentages instead of
    # maintaining a mode allow-list, so event/special modes are preserved.
    rate=re.compile(r"(\d+(?:\.\d+)?)\s*%")
    out=[];pos=0
    for m in rate.finditer(raw):
        label=raw[pos:m.start()].strip()
        pos=m.end()
        if not label:continue
        out.append({"mode":label,"win_rate":float(m.group(1))})
    return out

def _parse_maps(raw):
    if not raw or raw.strip().casefold()=="no map data found.":return []
    heading=re.compile("(.+?)" + r"\s+" + r"(\d+)" + r"\s+Maps\s+")
    entry=re.compile("(.+?)" + r"\s+" + r"(\d+)" + r"\s+Battles\s+" + r"(\d+(?:\.\d+)?)" + r"\s*%")
    out=[];pos=0
    while pos<len(raw):
        h=heading.match(raw,pos)
        if not h:break
        mode=h.group(1).strip();count=int(h.group(2));pos=h.end()
        for _ in range(count):
            m=entry.match(raw,pos)
            if not m:return out
            out.append({"mode":mode,"map":m.group(1).strip(),"battles":int(m.group(2)),"win_rate":float(m.group(3))})
            pos=m.end()
            while pos<len(raw) and raw[pos].isspace():pos+=1
    return out

def _page_enrichment(brawler_id):
    url=f"https://brawltrack.app/brawlers/{brawler_id}"
    r=requests.get(url,headers={"User-Agent":UA,"Accept":"text/html"},timeout=PAGE_TIMEOUT);r.raise_for_status()
    text=BeautifulSoup(r.text,"html.parser").get_text(" ",strip=True)
    def pct(label):
        m=re.search(re.escape(label)+r"\s+(\d+(?:[.,]\d+)?)%",text,re.I)
        return float(m.group(1).replace(",",".")) if m else None
    build_raw=_section(text,"Popular Builds",("Best Teammates","Star Powers","Gadgets","Best Game Modes"))
    modes_raw=_section(text,"Best Game Modes",("Best Maps","Meta Performance Check","Trivia & Mechanics"))
    maps_raw=_section(text,"Best Maps",("Meta Performance Check","Trivia & Mechanics"))
    builds={"source":"brawltrack_public_page","items":_parse_builds(build_raw),"raw_text":build_raw[:8000]}
    modes={"source":"brawltrack_public_page","items":_parse_modes(modes_raw),"maps":_parse_maps(maps_raw),"raw_text":modes_raw[:12000],"best_maps_raw_text":maps_raw[:12000]}
    return {"win_rate":pct("Win Rate"),"pick_rate":pct("Meta Usage"),"star_rate":pct("Star Rate"),"popular_builds":builds,"modes":modes,"page_url":url}

_CATALOG_CACHE={}
def _catalog_rows(table,brawler_id=None):
    # These catalogs are static during one sync. Fetch each table once instead
    # of issuing gadgets/stars/gears requests for every one of 108 Brawlers.
    rows=_CATALOG_CACHE.get(table)
    if rows is None:
        r=requests.get(f"{SUPABASE_URL}/rest/v1/{table}",params={"select":"*"},headers=_headers(),timeout=TIMEOUT);r.raise_for_status()
        rows=r.json();_CATALOG_CACHE[table]=rows
    if brawler_id is None:return rows
    return [x for x in rows if int(x.get("brawler_id") or -1)==int(brawler_id)]

def _resolve_builds(brawler_id,builds):
    if not isinstance(builds,dict) or not isinstance(builds.get("items"),list): return builds
    gadgets=_catalog_rows("gadgets_catalog",brawler_id)
    stars=_catalog_rows("star_powers_catalog",brawler_id)
    gears=_catalog_rows("gears_catalog")
    candidates=[]
    for kind,rows,idkey in (("gadget",gadgets,"gadget_id"),("star_power",stars,"star_power_id"),("gear",gears,"gear_id")):
        for x in rows:
            name=str(x.get("name_en") or "").upper().strip()
            if name: candidates.append((name,kind,x.get(idkey),x.get("name_it") or x.get("name_en")))
    candidates.sort(key=lambda x:len(x[0]),reverse=True)
    resolved=[]
    for item in builds["items"]:
        remaining=str(item.get("items_raw") or "").upper().strip(); parts=[]
        while remaining:
            hit=None
            for cand in candidates:
                if remaining.startswith(cand[0]) and (len(remaining)==len(cand[0]) or remaining[len(cand[0])]==" "):
                    hit=cand;break
            if not hit: break
            name,kind,obj_id,name_it=hit
            parts.append({"type":kind,"id":obj_id,"name_en":name,"name_it":name_it})
            remaining=remaining[len(name):].strip()
        row=dict(item); row["components"]=parts; row["unmatched_raw"]=remaining or None
        resolved.append(row)
    out=dict(builds);out["items"]=resolved
    return out

def _build_row(brawler_id,row,enrich=None):
    stats=row.get("stats") if isinstance(row.get("stats"),dict) else {}; merged={**row,**stats}; enrich=enrich or {}; now=datetime.now(timezone.utc).isoformat()
    builds=enrich.get("popular_builds") or _first(merged,"popularBuilds","popular_builds","builds","loadouts") or []
    builds=_resolve_builds(brawler_id,builds)
    modes=enrich.get("modes") or _first(merged,"modes","gameModes","game_modes","modeStats") or {}
    payload=dict(row)
    if enrich: payload["public_page_enrichment"]={k:v for k,v in enrich.items() if k!="page_url"}
    return {"brawler_id":brawler_id,"brawler_name":_first(row,"name","brawlerName","brawler_name"),
      "win_rate":enrich.get("win_rate") if enrich.get("win_rate") is not None else _num(merged,"winRate","win_rate","winrate"),
      "pick_rate":enrich.get("pick_rate") if enrich.get("pick_rate") is not None else _num(merged,"pickRate","pick_rate","usageRate","usage_rate","metaUsage","meta_usage","usage"),
      "star_rate":enrich.get("star_rate") if enrich.get("star_rate") is not None else _num(merged,"starRate","star_rate","starPlayerRate","star_player_rate","mvpRate","mvp_rate"),
      "rank_label":_first(merged,"rank","tier","rankLabel","rank_label"),"popular_builds":builds,"modes":modes,
      "source_url":enrich.get("page_url") or f"https://brawltrack.app/brawlers/{brawler_id}","source_payload":payload,"source_updated_at":now,"updated_at":now}

def _upsert_rows(rows):
    if not rows:return 0
    r=requests.post(f"{SUPABASE_URL}/rest/v1/brawltrack_meta_cache?on_conflict=brawler_id",headers=_headers(),data=json.dumps(rows,ensure_ascii=False),timeout=max(TIMEOUT,30));r.raise_for_status()
    return len(rows)

def sync():
    catalog=normalize_brawler_catalog(brawlers());known=_known_ids();pending=[];cached=0;enriched=0;page_errors=0
    for brawler_id,row in catalog.items():
        if brawler_id not in known:continue
        print("BRAWLTRACK META BRAWLER START:",brawler_id,row.get("name") or "",flush=True)
        extra={}
        try: extra=_page_enrichment(brawler_id); enriched+=1
        except Exception as exc: page_errors+=1; print("BRAWLTRACK PAGE ENRICH ERROR:",brawler_id,repr(exc),flush=True)
        pending.append(_build_row(brawler_id,row,extra))
        if len(pending)>=UPSERT_BATCH:
            cached+=_upsert_rows(pending)
            print("BRAWLTRACK META PROGRESS: cached=%s/%s enriched=%s errors=%s" % (cached,len(known),enriched,page_errors),flush=True)
            pending=[]
        if PAGE_DELAY:time.sleep(PAGE_DELAY)
    if pending:
        cached+=_upsert_rows(pending)
        print("BRAWLTRACK META PROGRESS: cached=%s/%s enriched=%s errors=%s" % (cached,len(known),enriched,page_errors),flush=True)
    return {"seen":len(catalog),"known":len(known),"cached":cached,"enriched":enriched,"page_errors":page_errors}

if __name__=="__main__": print(json.dumps(sync(),ensure_ascii=False))
