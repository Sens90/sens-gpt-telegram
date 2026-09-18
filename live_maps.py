"""Live Brawl Stars map rotation and fallback statistics.

BrawlTrack is the primary meta/map/comp source. Brawl Planet remains only a
fallback for verified rotation/statistics when a BrawlTrack datum is missing.
"""
import csv, gzip, io, json, logging, math, os, re, time, requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE="https://storage.googleapis.com/brawlanalyzer-public/"; ROME=ZoneInfo("Europe/Rome"); LOG=logging.getLogger(__name__); _CACHE={}
SECTIONS={"individual":"Individuali","teams":"Squadre","solo":"Solo — individuali","duo_individual":"Duo — individuali","duo_team":"Duo — squadre","trio_individual":"Trio — individuali","trio_team":"Trio — squadre"}
METRICS={"wr":("Vittorie","%"),"win_rate":("Vittorie","%"),"ur":("Utilizzo","%"),"use_rate":("Utilizzo","%"),"sr":("Miglior StarPlayer","%"),"starplayer_rate":("Miglior StarPlayer","%"),"avg_rank":("Piazzamento medio",""),"tm":("Partite","")}
MODES={"brawlBall":"Brawl Ball","gemGrab":"Gem Grab","hotZone":"Hot Zone","bounty":"Bounty","heist":"Heist","knockout":"Knockout","showdown":"Showdown","airHockey":"Brawl Hockey","brawlArena":"Brawl Arena","deathmatch5v5":"Wipeout 5v5","wipeout":"Wipeout","basketBrawl":"Basket Brawl","payload":"Payload"}
# Supercell event-mode names are not always identical to brawlanalyzer dataset filenames.
ANALYZER_MODE_FILES={"soloShowdown":"showdown","duoShowdown":"showdown","trioShowdown":"showdown","tagTeam":None}

def analyzer_mode_file(mode):
    return ANALYZER_MODE_FILES.get(str(mode),str(mode))

def get_json(path,ttl=300):
    cached=_CACHE.get(path)
    if cached and time.monotonic()-cached[0]<ttl:return cached[1]
    url=path if path.startswith("https://") else BASE+path
    with urlopen(Request(url,headers={"User-Agent":"SensGPT/1.0"}),timeout=30) as response:raw=response.read(8_000_001)
    if len(raw)>8_000_000:raise ValueError("Source response too large")
    if raw[:2]==b"\x1f\x8b":raw=gzip.decompress(raw)
    result=json.loads(raw);_CACHE[path]=(time.monotonic(),result);return result

def safe_get(path,ttl=300):
    try:return get_json(path,60 if path=="event_rotation.json.gz" else ttl)
    except Exception as error:LOG.warning("LIVE_MAPS source unavailable path=%s error=%s",path,type(error).__name__);return None

SUPERcell_EVENTS="https://api.brawlstars.com/v1/events/rotation"

def get_official_rotation(ttl=60):
    """Official Supercell rotation, preferably through the existing TITANI ABUSIVI proxy."""
    cached=_CACHE.get(SUPERcell_EVENTS)
    if cached and time.monotonic()-cached[0]<ttl:
        LOG.info("LIVE_MAPS official rotation cache HIT age=%.1fs events=%s",time.monotonic()-cached[0],len(cached[1]) if isinstance(cached[1],list) else "?")
        return cached[1]
    print("LIVE_MAPS official rotation cache MISS",flush=True)
    proxy_url=(os.environ.get("BRAWL_OFFICIAL_PROXY_URL") or "").strip()
    proxy_key=(os.environ.get("BRAWL_OFFICIAL_PROXY_KEY") or "").strip()
    token=(os.environ.get("BRAWL_STARS_API_TOKEN") or os.environ.get("BRAWL_API_TOKEN") or "").strip()
    print("LIVE_MAPS official rotation attempt proxy_url=%s proxy_key=%s token=%s" % (bool(proxy_url),bool(proxy_key),bool(token)),flush=True)
    try:
        if proxy_url and proxy_key:
            response=requests.get(proxy_url,params={"action":"events"},headers={"X-Sens-Key":proxy_key,"Accept":"application/json","User-Agent":"SensGPT/1.0"},timeout=15)
            source="proxy"
        elif token:
            response=requests.get(SUPERcell_EVENTS,headers={"Authorization":f"Bearer {token}","Accept":"application/json","User-Agent":"SensGPT/1.0"},timeout=15)
            source="direct"
        else:
            LOG.warning("LIVE_MAPS official rotation unavailable: official proxy/token not configured")
            return None
        response.raise_for_status();rows=response.json()
        if not isinstance(rows,list):raise ValueError("unexpected official rotation payload")
        _CACHE[SUPERcell_EVENTS]=(time.monotonic(),rows)
        print("LIVE_MAPS official rotation OK source=%s events=%s" % (source,len(rows)),flush=True)
        return rows
    except Exception as error:
        status=getattr(getattr(error,"response",None),"status_code",None)
        print("LIVE_MAPS official rotation unavailable error=%s status=%s" % (type(error).__name__,status),flush=True);return None

def normalize_official_events(rows):
    """Normalize Supercell Event objects to the internal brawlanalyzer event shape."""
    out=[]
    for row in rows if isinstance(rows,list) else []:
        event=row.get("event") if isinstance(row,dict) else None
        if not isinstance(event,dict):continue
        mode=str(event.get("mode") or "")
        map_name=str(event.get("map") or "")
        if not mode or not map_name:continue
        key=re.sub(r"[^a-z0-9]+","_",map_name.casefold()).strip("_")
        out.append({"start_time":row.get("startTime"),"end_time":row.get("endTime"),"event_mode":mode,"event_map":map_name,"event_map_id":key,"event_id":event.get("id"),"_official":True})
    return out

def brawltrack_pro_map_url(map_name):
    """Stable BrawlTrack competitive-map URL; name is encoded by requests/web clients."""
    from urllib.parse import quote
    clean=str(map_name or "").strip()
    return f"https://brawltrack.app/pro/maps/{quote(clean, safe='')}" if clean else None

def brawltrack_pro_map_image(map_name,ttl=300):
    """Return BrawlTrack's map image only when the page identifies the same map."""
    url=brawltrack_pro_map_url(map_name)
    if not url:return None
    key="btproimg:"+url;cached=_CACHE.get(key)
    if cached and time.monotonic()-cached[0]<ttl:return cached[1]
    try:
        from bs4 import BeautifulSoup
        response=requests.get(url,headers={"User-Agent":"SensGPT-TitaniAbusivi/1.0","Accept":"text/html"},timeout=15);response.raise_for_status()
        soup=BeautifulSoup(response.text,"html.parser");text=soup.get_text(" ",strip=True)
        mid=re.search(r"MAP ID:\s*(\d+)",text,re.I);target=str(map_name or "").strip().casefold()
        candidates=[(img.get("src") or "").strip() for img in soup.find_all("img") if (img.get("alt") or "").strip().casefold()==target and (img.get("src") or "").strip()]
        result={"source":"BrawlTrack Pro","source_url":url,"map_id":int(mid.group(1)) if mid else None,"image_url":candidates[0] if candidates else None,"verified":bool(mid and candidates)}
        _CACHE[key]=(time.monotonic(),result);return result
    except Exception as error:
        LOG.warning("LIVE_MAPS BrawlTrack map image unavailable map=%s error=%s",map_name,type(error).__name__);return None

def brawltrack_pro_map_stats(map_name,ttl=300):
    """Parse BrawlTrack Pro map data, preserving its competitive scope."""
    url=brawltrack_pro_map_url(map_name)
    if not url:return None
    cache_key="btpro:"+url;cached=_CACHE.get(cache_key)
    if cached and time.monotonic()-cached[0]<ttl:return cached[1]
    try:
        from bs4 import BeautifulSoup
        response=requests.get(url,headers={"User-Agent":"SensGPT-TitaniAbusivi/1.0","Accept":"text/html"},timeout=15);response.raise_for_status()
        text=BeautifulSoup(response.text,"html.parser").get_text(" ",strip=True)
        mid=re.search(r"MAP ID:\s*(\d+)",text,re.I)
        def between(a,b):
            upper=text.upper();i=upper.find(a);j=upper.find(b,i+len(a)) if i>=0 else -1
            return text[i+len(a):j] if i>=0 and j>i else ""
        picks_block=between("PRIORITY PICKS","TEAMS ON MAP")
        # BrawlTrack renders: rank NAME tier ROLE •use% USE WIN RATE wr%
        pick_pat=re.compile(r"(\d+)\s+([A-Z][A-Z0-9 .'-]*?)\s+[A-S]\s+(?:SNIPER|TANK|ASSASSIN|THROWER|SUPPORT|CONTROLLER|DAMAGE)(?:\s+DEALER)?\s*[•·]?\s*(\d+(?:\.\d+)?)\s*%\s+USE\s+WIN RATE\s+(\d+(?:\.\d+)?)\s*%",re.I)
        picks=[{"rank":int(m.group(1)),"brawler":re.sub(r"\s+"," ",m.group(2)).strip().upper(),"use_rate":float(m.group(3)),"win_rate":float(m.group(4))} for m in pick_pat.finditer(picks_block)]
        comps_block=between("COMMON FINAL COMPS","PRO MATCHUP MATRIX")
        # BeautifulSoup text can omit image alt names for team compositions. Parse
        # the cards from DOM so the three brawler names are retained.
        comp_cards=[]
        # BrawlTrack renders one image per composition; its alt contains all three
        # brawlers (e.g. "BOLT + MOE + PEARL"). Walk up to the card that also
        # contains the sets/WR values.
        soup=BeautifulSoup(response.text,"html.parser")
        for img in soup.find_all("img"):
            alt=(img.get("alt") or "").strip().upper()
            if alt.count(" + ") != 2: continue
            card=img
            stats=None
            for _ in range(8):
                card=card.parent if card else None
                if not card: break
                stats=re.search(r"(\d+)\s+sets?\s+(\d+(?:\.\d+)?)\s*%\s+WR",card.get_text(" ",strip=True),re.I)
                if stats: break
            if stats:
                team=[part.strip() for part in alt.split(" + ")]
                item={"team":team,"sets":int(stats.group(1)),"win_rate":float(stats.group(2))}
                if item not in comp_cards: comp_cards.append(item)
        # Text extraction may keep or drop the image label/colon.
        comp_pat=re.compile(r"(?:IMAGE:\s*)?([A-Z][A-Z0-9 .'-]*?)\s*\+\s*([A-Z][A-Z0-9 .'-]*?)\s*\+\s*([A-Z][A-Z0-9 .'-]*?)\s+(\d+)\s+sets?\s+(\d+(?:\.\d+)?)%\s+WR",re.I)
        comps=comp_cards or [{"team":[m.group(i).strip().upper() for i in (1,2,3)],"sets":int(m.group(4)),"win_rate":float(m.group(5))} for m in comp_pat.finditer(comps_block)]
        result={"source":"BrawlTrack Pro","scope":"competitive_pro","source_url":url,"map_id":int(mid.group(1)) if mid else None,"priority_picks":picks,"final_comps":comps}
        if not comps:LOG.warning("LIVE_MAPS BrawlTrack pro comps empty map=%s html=%r",map_name,response.text[response.text.upper().find("COMMON FINAL COMPS"):response.text.upper().find("PRO MATCHUP MATRIX")][:6000])
        _CACHE[cache_key]=(time.monotonic(),result);return result
    except Exception as error:
        LOG.warning("LIVE_MAPS BrawlTrack pro unavailable map=%s error=%s",map_name,type(error).__name__);return None

def brawltrack_brawler_counter_evidence(brawler_name,ttl=300):
    """BrawlTrack-first counter evidence.

    BrawlTrack's public brawler pages expose meta/build/teammate statistics but
    currently do not expose a brawler-vs-brawler counter matrix. Do not infer
    counters from tier rank, teammate WR, or the map TEAM-vs-TEAM matrix.
    Return only explicitly published matchup evidence when it becomes available.
    """
    from urllib.parse import quote
    clean=str(brawler_name or "").strip()
    if not clean:return []
    url=f"https://brawltrack.app/pro/brawlers/{quote(clean.upper(),safe='')}"
    key="btcounter:"+url;cached=_CACHE.get(key)
    if cached and time.monotonic()-cached[0]<ttl:return cached[1]
    try:
        from bs4 import BeautifulSoup
        response=requests.get(url,headers={"User-Agent":"SensGPT-TitaniAbusivi/1.0","Accept":"text/html"},timeout=15)
        response.raise_for_status()
        soup=BeautifulSoup(response.text,"html.parser")
        text=soup.get_text(" ",strip=True)
        # Guardrail: BrawlTrack's current "PRO MATCHUP MATRIX" on map pages is
        # team-vs-team, not brawler-vs-brawler. We only parse a future explicit
        # brawler counter/matchup section, never manufacture one from win rates.
        heading=re.search(r"(?:BRAWLER\s+MATCHUPS|COUNTERS|COUNTER\s+MATCHUPS)",text,re.I)
        result=[]
        if heading:
            LOG.info("BRAWLTRACK counter section detected for %s; parser requires verified schema",clean)
        _CACHE[key]=(time.monotonic(),result)
        return result
    except Exception as error:
        LOG.warning("BRAWLTRACK counter evidence unavailable brawler=%s error=%s",clean,type(error).__name__)
        return []

def event_time(value):
    """Parse Supercell event timestamps with or without fractional seconds."""
    if not isinstance(value,str):return None
    for fmt in ("%Y%m%dT%H%M%S.%fZ","%Y%m%dT%H%M%SZ"):
        try:return datetime.strptime(value,fmt).replace(tzinfo=timezone.utc)
        except ValueError:pass
    return None

def active_events(rows,now):
    active={}
    for row in rows if isinstance(rows,list) else []:
        if not isinstance(row,dict):continue
        start,end=event_time(row.get("start_time")),event_time(row.get("end_time"));key,mode=row.get("event_map_id",""),row.get("event_mode","")
        if not start or not end or not start<=now<end or not re.fullmatch(r"[a-z0-9_]+",key) or not re.fullmatch(r"[A-Za-z0-9]+",mode):continue
        if key not in active:active[key]=dict(row)
        elif end<event_time(active[key]["end_time"]):active[key]["end_time"]=row["end_time"]
    return sorted(active.values(),key=lambda row:(row["event_mode"],row["event_map_id"]))

def localized(names,category,value):
    if str(value).upper() in {"MR. P","MISTER P"}:return "Mr. P"
    return names.get(category,{}).get(str(value).upper(),str(value))

def valid_rows(rows):
    result=[]
    for row in rows if isinstance(rows,list) else []:
        if not isinstance(row,dict):continue
        team=row.get("team")
        if team is not None:
            if not isinstance(team,list) or len(team)<2 or not all(isinstance(v,str) and v for v in team) or len(set(v.casefold() for v in team))!=len(team):continue
        elif not isinstance(row.get("brawler",row.get("brawler_name")),str):continue
        clean={k:v for k,v in row.items() if k in ("brawler","brawler_name","team")}
        for key,value in row.items():
            if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0:
                if key in ("wr","win_rate","sr","starplayer_rate") and value>100:continue
                clean[key]=value
        if any(k in clean for k in ("wr","win_rate","avg_rank")):result.append(clean)
    return result

def row_text(row,names):
    identity=row.get("team") or [row.get("brawler",row.get("brawler_name"))];label=", ".join(localized(names,"brawlers",name) for name in identity);metrics=[]
    for key,value in row.items():
        if key in ("team","brawler","brawler_name"):continue
        title,suffix=METRICS.get(key,(key,""));number=f"{value:g}" if key=="tm" else f"{value:.2f}".rstrip("0").rstrip(".");metrics.append(f"{title} {number.replace('.',',')}{suffix}")
    return label+(" — "+" · ".join(metrics) if metrics else "")

def collect_report(dataset="both",now=None,fetch=safe_get,secondary=None):
    now=now or datetime.now(timezone.utc)
    # Resolve the authoritative Supercell rotation first. Fallback datasets are enrichment only.
    official=get_official_rotation();official_events=active_events(normalize_official_events(official),now) if official else []
    paths=["event_rotation.json.gz","i18n/names.it.json.gz"]
    with ThreadPoolExecutor(max_workers=4) as pool:initial=dict(zip(paths,pool.map(fetch,paths)))
    fallback_events=active_events(initial[paths[0]],now);names=initial[paths[1]] if isinstance(initial[paths[1]],dict) else {}
    # Supercell is authoritative for active event timing/rotation. The public analyzer manifest remains a safe fallback.
    # Keep analyzer map IDs when map+mode match so existing BrawlTrack/stat datasets continue to join correctly.
    if official_events:
        fallback_by_name={(str(e.get("event_mode","")).casefold(),str(e.get("event_map","")).casefold()):e for e in fallback_events}
        events=[]
        for event in official_events:
            match=fallback_by_name.get((str(event.get("event_mode","")).casefold(),str(event.get("event_map","")).casefold()))
            if match:event["event_map_id"]=match.get("event_map_id",event["event_map_id"])
            events.append(event)
        rotation_source="Supercell"
    else:events=fallback_events;rotation_source="brawlanalyzer fallback"
    if not events:return {"now":now,"events":[],"names":names,"maps":[],"rotation_missing":True,"rotation_source":rotation_source}
    mode_files={e["event_mode"]:analyzer_mode_file(e["event_mode"]) for e in events}
    paths=[f"normal-results/{mode}.json.gz" for mode in sorted({m for m in mode_files.values() if m})]
    if dataset!="ladder":paths.append("pl-results.json.gz")
    with ThreadPoolExecutor(max_workers=6) as pool:data=dict(zip(paths,pool.map(fetch,paths)))
    data={key:value if isinstance(value,dict) else {} for key,value in data.items()};maps=[]
    for event in events:
        key=event["event_map_id"];mode_file=mode_files.get(event["event_mode"]);normal=(data.get(f"normal-results/{mode_file}.json.gz") or {}).get(key,{}) if mode_file else {};ranked=(data.get("pl-results.json.gz") or {}).get(key,{})
        normal=normal if isinstance(normal,dict) else {};ranked=ranked if isinstance(ranked,dict) else {};competitive=brawltrack_pro_map_stats(event.get("event_map"));map_image=brawltrack_pro_map_image(event.get("event_map"));canonical_id=competitive.get("map_id") if competitive else None;identity_verified=bool(canonical_id and map_image and map_image.get("verified") and map_image.get("map_id")==canonical_id);entry={"event":event,"datasets":[],"secondary":None,"map_name_it":localized(names,"maps",event.get("event_map")),"mode_name_it":localized(names,"modes",MODES.get(event.get("event_mode"),event.get("event_mode"))),"canonical_map_id":canonical_id,"map_identity_verified":identity_verified,"rotation_source":rotation_source,"map_image":map_image,"brawltrack_pro_url":brawltrack_pro_map_url(event.get("event_map")),"competitive":competitive}
        for label,raw in (("Trofei",normal),("Classificata",ranked)):
            if (dataset=="ladder" and label!="Trofei") or (dataset=="ranked" and label!="Classificata"):continue
            sections={k:valid_rows(raw.get(k)) for k in SECTIONS};entry["datasets"].append({"label":label,"raw":raw,"sections":sections})
        maps.append(entry)
    return {"now":now,"events":events,"names":names,"maps":maps,"rotation_missing":False,"rotation_source":rotation_source}

def render_report(report,limit=5):
    if report["rotation_missing"]:return "Non riesco a verificare gli orari della rotazione attiva."
    names=report["names"];lines=[f"Mappe attive — {report['now'].astimezone(ROME):%d/%m/%Y %H:%M} (Italia)"]
    for entry in report["maps"]:
        event=entry["event"];raw_mode=next((d["raw"].get("modeFormatted") for d in entry["datasets"] if d["raw"].get("modeFormatted")),MODES.get(event["event_mode"],event["event_mode"]));mode=localized(names,"modes",raw_mode);map_name=localized(names,"maps",event["event_map"]);event["map_name_it"]=map_name;event["mode_name_it"]=mode;end=event_time(event["end_time"]).astimezone(ROME);lines += ["",f"{mode} — {map_name}",f"Attiva fino alle {end:%H:%M}"]
        for data in entry["datasets"]:
            if not any(data["sections"].values()):continue
            lines.append(data["label"])
            for section,rows in data["sections"].items():
                if not rows:continue
                selected=[row for row in rows if row.get("ur",row.get("use_rate",0))>=1] if section in ("individual","solo","duo_individual","trio_individual") else [row for row in rows if "team" in row]
                selected.sort(key=lambda r:(r.get("ur",r.get("use_rate",0)),r.get("wr",r.get("win_rate",0))),reverse=True)
                if selected:lines.append(SECTIONS[section]+":");lines.extend("• "+row_text(row,names) for row in selected[:limit])
    return "\n".join(lines)

def report_csv(report):
    out=io.StringIO();writer=csv.writer(out);writer.writerow(["mappa","modalita","dataset","tabella","brawler_o_squadra","metrica","valore","campione_mappa","ultima_partita_unix","fonte"])
    for entry in report["maps"]:
        event=entry["event"]
        for data in entry["datasets"]:
            for section,rows in data["sections"].items():
                for row in rows:
                    identity=row.get("team") or [row.get("brawler",row.get("brawler_name"))]
                    for metric,value in row.items():
                        if metric in ("team","brawler","brawler_name"):continue
                        writer.writerow([localized(report["names"],"maps",event["event_map"]),event["event_mode"],data["label"],SECTIONS[section],", ".join(localized(report["names"],"brawlers",name) for name in identity),metric,value,data["raw"].get("match_count",""),data["raw"].get("latest_match_time",""),"Brawl Planet fallback"])
    return out.getvalue().encode("utf-8-sig")

def _install_direct_meta_route():
    try:
        import community_features
        from meta_current import render_current_meta
    except Exception as exc:print("META IMPORT ROUTE ERRORE:",repr(exc),flush=True);return
    original=community_features.CommunityFeatures.handle_command
    if getattr(original,"_sens_meta_direct_import",False):return
    async def routed(self,message,context,question):
        q=re.sub(r"\s+"," ",str(question or "").strip().casefold())
        if re.fullmatch(r"(?:il\s+)?meta(?:\s+attuale|\s+di\s+adesso|\s+ora)?[?!.]*",q):
            try:
                await context.bot.send_chat_action(chat_id=message.chat_id,action="typing")
                merged={"results":[]}
                # Tier List: fetch the canonical BrawlTrack page directly. Search engines often
                # index the homepage/ranked page instead and therefore hid the actual tier list.
                tier_url="https://brawltrack.app/tier-list"
                tier_response=requests.get(tier_url,headers={"User-Agent":"Mozilla/5.0 SensGPT/1.0","Accept-Language":"it-IT,it;q=0.9,en;q=0.8"},timeout=20)
                if tier_response.ok and tier_response.text:
                    merged["results"].append({"url":tier_url,"title":"BrawlTrack Tier List","raw_content":tier_response.text,"content":tier_response.text})
                key=os.environ.get("TAVILY_API_KEY")
                if key:
                    response=requests.post("https://api.tavily.com/search",json={"api_key":key,"query":"BrawlTrack PRO ACTIVE maps PRIORITY PICKS COMMON FINAL COMPS site:brawltrack.app/pro/maps","search_depth":"advanced","max_results":12,"include_raw_content":True,"include_answer":False,"include_domains":["brawltrack.app"]},timeout=20)
                    response.raise_for_status();merged["results"].extend(response.json().get("results",[]))
                report=render_current_meta(merged,"both")
                if report:
                    print("META APP IMPORT: BrawlTrack tier-list diretta+maps; Gemini BLOCCATO",flush=True);await message.reply_text(report)
                else:
                    print("META APP IMPORT: tier list diretta non parsabile; Gemini BLOCCATO",flush=True);await message.reply_text("META ATTUALE\n\nBrawlTrack non espone la Tier List in un formato leggibile dal bot in questo momento. Non uso Brawler casuali come sostituto.")
            except Exception as exc:
                print("META APP IMPORT ERRORE; Gemini BLOCCATO:",repr(exc),flush=True);await message.reply_text("META ATTUALE\n\nNon riesco a verificare la Tier List BrawlTrack in questo momento. Per evitare un meta errato non genero una lista alternativa.")
            return True
        return await original(self,message,context,question)
    routed._sens_meta_direct_import=True;community_features.CommunityFeatures.handle_command=routed;print("META APP IMPORT ROUTE INSTALLATA",flush=True)
_install_direct_meta_route()
