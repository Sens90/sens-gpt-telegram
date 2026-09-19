"""Ranked matchup collector for Sens GPT.

Collects only final team lineups exposed by the official Brawl Stars battle log.
It never invents bans. Matches are deduplicated before aggregation.

Run as a periodic job with a set of seed player tags. The collector expands one
hop through players found in Ranked matches and upserts exact map/mode matchup
rows into Supabase brawler_counters.
"""
import hashlib, os, re, time
from collections import defaultdict
import requests

API="https://api.brawlstars.com/v1"
RANKED_TYPES={"ranked","soloranked","teamranked"}
RANKED_MODES={"gemGrab","brawlBall","hotZone","bounty","heist","knockout"}
RANKED_WIKI_URL=os.getenv("RANKED_WIKI_URL","https://brawlstars.fandom.com/wiki/Ranked")
RANKED_WIKI_API=os.getenv("RANKED_WIKI_API","https://brawlstars.fandom.com/api.php")
_ranked_pool_cache={"ts":0.0,"pairs":None,"season":None}
_KNOWN_GOOD_RANKED_POOL={
    ("bounty","Dry Season"),("bounty","Hideout"),("bounty","Layer Cake"),("bounty","Shooting Star"),
    ("brawlBall","Center Stage"),("brawlBall","Pinball Dreams"),("brawlBall","Sneaky Fields"),("brawlBall","Triple Dribble"),
    ("gemGrab","Double Swoosh"),("gemGrab","Gem Fort"),("gemGrab","Hard Rock Mine"),("gemGrab","Undermine"),
    ("heist","Bridge Too Far"),("heist","Hot Potato"),("heist","Kaboom Canyon"),("heist","Safe Zone"),
    ("hotZone","Dueling Beetles"),("hotZone","In the Liminal"),("hotZone","Open Business"),("hotZone","Parallel Plays"),("hotZone","Quick Travel"),("hotZone","Ring of Fire"),
    ("knockout","Belle's Rock"),("knockout","Flaring Phoenix"),("knockout","New Horizons"),("knockout","Out in the Open"),
}
_ranked_pool_failure_until=0.0

_WIKI_MODE_CATEGORIES={
    "Gem Grab Maps":"gemGrab",
    "Brawl Ball Maps":"brawlBall",
    "Hot Zone Maps":"hotZone",
    "Bounty Maps":"bounty",
    "Heist Maps":"heist",
    "Knockout Maps":"knockout",
}

def _wiki_active_map_names():
    """Read the current Ranked Active maps block from the Wiki.

    Parse the full Ranked page because Fandom exposes "Active maps (Season N)"
    in the article body rather than as a child heading of the Maps section.
    The season number is discovered dynamically.
    """
    from bs4 import BeautifulSoup
    common={"action":"parse","page":"Ranked","format":"json","origin":"*","prop":"text"}
    headers={"User-Agent":"SensGPT-TitaniAbusivi/1.0","Accept":"application/json"}
    r=requests.get(RANKED_WIKI_API,params=common,headers=headers,timeout=20)
    r.raise_for_status()
    html=(((r.json().get("parse") or {}).get("text") or {}).get("*") or "")
    if not html:
        raise RuntimeError("Wiki Ranked page empty")
    soup=BeautifulSoup(html,"html.parser")

    candidates=[]
    for h in soup.find_all(re.compile(r"^h[1-6]$")):
        label=h.get_text(" ",strip=True)
        match=re.search(r"active\\s+maps(?:\\s*\\(\\s*season\\s*(\\d+)\\s*\\))?",label,re.I)
        if match:
            candidates.append((int(match.group(1)) if match.group(1) else -1,h,label))
    if not candidates:
        raise RuntimeError("Wiki Active maps heading not found on Ranked page")
    season,heading,label=max(candidates,key=lambda x:x[0])

    names=[]
    start_level=int(heading.name[1])
    for node in heading.find_all_next():
        if re.fullmatch(r"h[1-6]",str(node.name)) and int(node.name[1])<=start_level:
            break
        if node.name!="a":
            continue
        href=str(node.get("href") or "")
        name=node.get_text(" ",strip=True)
        if not name or name in names:
            continue
        if not (href.startswith("/wiki/") or "brawlstars.fandom.com/wiki/" in href):
            continue
        if any(x in href for x in ("/File:","/Category:","/Help:","/Ranked")):
            continue
        names.append(name)
    if len(names)<24 or len(names)>40:
        raise RuntimeError(f"Wiki Active maps suspicious season={season} count={len(names)} names={names}")
    return names,(season if season>=0 else None)


def _wiki_map_mode(name):
    """Resolve one Ranked map to its game mode using Wiki page categories."""
    r=requests.get(
        RANKED_WIKI_API,
        params={"action":"query","prop":"categories","cllimit":"max","titles":name,"format":"json","origin":"*"},
        headers={"User-Agent":"SensGPT-TitaniAbusivi/1.0"},
        timeout=15,
    )
    r.raise_for_status()
    pages=((r.json().get("query") or {}).get("pages") or {})
    cats=[]
    for page in pages.values():
        cats.extend(str(x.get("title") or "").removeprefix("Category:") for x in (page.get("categories") or []))
    modes={api for cat,api in _WIKI_MODE_CATEGORIES.items() if cat in cats}
    if len(modes)!=1:
        raise RuntimeError(f"Wiki mode ambiguous for {name!r}: categories={cats} modes={sorted(modes)}")
    return next(iter(modes))

def _fetch_wiki_ranked_pool():
    candidates,season=_wiki_active_map_names()
    pairs=set()
    for name in candidates:
        try:
            pairs.add((_wiki_map_mode(name),name))
        except Exception as exc:
            print(f"RANKED WIKI MAP MODE ERROR: map={name!r} error={exc!r}",flush=True)
            continue
    counts={mode:sum(1 for m,_ in pairs if m==mode) for mode in RANKED_MODES}
    # The current Ranked format has at least four maps per supported mode.
    # Reject incomplete/stale parsing instead of feeding a partial pool.
    if set(counts)!=RANKED_MODES or any(counts[m]<4 for m in RANKED_MODES):
        raise RuntimeError(f"Wiki Ranked pool structurally suspicious: total={len(pairs)} counts={counts}")
    if len(pairs)<24 or len(pairs)>40:
        raise RuntimeError(f"Wiki Ranked pool suspicious total={len(pairs)} counts={counts}")
    return pairs,counts,season


_BRAWLZONE_MODE_LABELS={
    "gem grab":"gemGrab","brawl ball":"brawlBall","hot zone":"hotZone",
    "bounty":"bounty","heist":"heist","knockout":"knockout",
}

def _fetch_brawlzone_ranked_pool():
    """Secondary live source: parse the published Full map pool from page text."""
    from bs4 import BeautifulSoup
    url=os.getenv("RANKED_SECONDARY_URL","https://brawlzone.net/ranked")
    r=requests.get(url,headers={"User-Agent":"SensGPT-TitaniAbusivi/1.0","Accept":"text/html"},timeout=20)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    page_text=" ".join(soup.stripped_strings)
    season=None
    # Prefer the season attached to Full map pool; page may also mention next/current seasons.
    m=re.search(r"Full\\s+map\\s+pool\\s*\\(\\s*season\\s*(\\d+)\\s*\\)",page_text,re.I)
    if m: season=int(m.group(1))
    advertised=None
    m=re.search(r"(\\d+)\\s+maps\\s+in\\s+the\\s+pool",page_text,re.I)
    if m: advertised=int(m.group(1))

    marker=re.search(r"Full\\s+map\\s+pool(?:\\s*\\(\\s*season\\s*\\d+\\s*\\))?",page_text,re.I)
    if not marker:
        raise RuntimeError("secondary Full map pool marker not found")
    tail=page_text[marker.end():]
    stop=re.search(r"How\\s+these\\s+picks\\s+are\\s+chosen",tail,re.I)
    pool_text=tail[:stop.start()] if stop else tail

    # Parse mode-delimited text. Mode labels are stable and map names occupy the
    # text between consecutive labels. Validate names against the map cards/links
    # present elsewhere on the same page to avoid splitting multi-word map names.
    mode_order=[("Gem Grab","gemGrab"),("Heist","heist"),("Bounty","bounty"),
                ("Brawl Ball","brawlBall"),("Hot Zone","hotZone"),("Knockout","knockout")]
    anchors=[]
    for a in soup.find_all("a"):
        name=" ".join(a.get_text(" ",strip=True).split())
        href=str(a.get("href") or "")
        if name and ("/maps/" in href or "/map/" in href):
            anchors.append(name)
    # Map cards are headings on current rendering; include those too.
    for h in soup.find_all(re.compile(r"^h[2-4]$")):
        name=" ".join(h.get_text(" ",strip=True).split())
        if name and name not in anchors and name.casefold() not in _BRAWLZONE_MODE_LABELS:
            anchors.append(name)
    pairs=set()
    for i,(label,api) in enumerate(mode_order):
        mm=re.search(re.escape(label)+r"(?:\\s+Featured)?",pool_text,re.I)
        if not mm: continue
        rest=pool_text[mm.end():]
        ends=[]
        for next_label,_ in mode_order[i+1:]:
            nm=re.search(re.escape(next_label)+r"(?:\\s+Featured)?",rest,re.I)
            if nm: ends.append(nm.start())
        chunk=rest[:min(ends)] if ends else rest
        for name in anchors:
            if re.search(r"(?<!\\w)"+re.escape(name)+r"(?!\\w)",chunk,re.I):
                pairs.add((api,name))

    counts={mode:sum(1 for m,_ in pairs if m==mode) for mode in RANKED_MODES}
    if any(counts[m]<4 or counts[m]>6 for m in RANKED_MODES):
        raise RuntimeError(f"secondary Full map pool structurally suspicious total={len(pairs)} counts={counts}")
    if advertised is not None and advertised!=len(pairs):
        raise RuntimeError(f"secondary Full map pool incomplete parsed={len(pairs)} advertised={advertised} counts={counts}")
    if len(pairs)<24 or len(pairs)>36:
        raise RuntimeError(f"secondary Full map pool suspicious total={len(pairs)} counts={counts}")
    return pairs,counts,season


def current_ranked_pool():
    """Prefer validated Wiki data, then a validated live secondary pool, then known-good safety data."""
    global _ranked_pool_failure_until
    now=time.time()
    if _ranked_pool_cache["pairs"] is not None and now-_ranked_pool_cache["ts"]<21600:
        return _ranked_pool_cache["pairs"]
    if now < _ranked_pool_failure_until:
        return _KNOWN_GOOD_RANKED_POOL
    errors=[]
    for source,fetcher in (("wiki-live",_fetch_wiki_ranked_pool),("secondary-live",_fetch_brawlzone_ranked_pool)):
        try:
            fetched=fetcher()
            if len(fetched)==3:
                pairs,counts,season=fetched
            else:
                pairs,counts=fetched; season=None
            maps_by_mode={mode:sorted(name for m,name in pairs if m==mode) for mode in sorted(RANKED_MODES)}
            expanded=[mode for mode,count in counts.items() if count>4]
            season_label=f" season={season}" if season is not None else ""
            print(f"RANKED POOL OK: source={source}{season_label} total={len(pairs)} counts={counts} expanded={expanded} maps={maps_by_mode}",flush=True)
            _ranked_pool_cache.update({"ts":now,"pairs":pairs,"season":season})
            _ranked_pool_failure_until=0.0
            return pairs
        except Exception as exc:
            print(f"RANKED POOL SOURCE ERROR: source={source} error={type(exc).__name__}: {exc}",flush=True)
            errors.append(f"{source}={type(exc).__name__}: {exc}")
    _ranked_pool_failure_until=now+3600
    counts={mode:sum(1 for m,_ in _KNOWN_GOOD_RANKED_POOL if m==mode) for mode in RANKED_MODES}
    print(f"RANKED POOL FALLBACK: source=known-good total={len(_KNOWN_GOOD_RANKED_POOL)} counts={counts} reason={' | '.join(errors)}",flush=True)
    return _KNOWN_GOOD_RANKED_POOL


def _headers():
    token=(os.getenv("BRAWL_STARS_API_TOKEN") or os.getenv("BRAWL_API_TOKEN") or "").strip()
    if not token: raise RuntimeError("Brawl Stars API token missing")
    return {"Authorization":"Bearer "+token,"Accept":"application/json","User-Agent":"SensGPT/1.0"}

def battlelog(tag):
    clean=str(tag).strip().lstrip("#")
    proxy_url=(os.getenv("BRAWL_OFFICIAL_PROXY_URL") or "").strip()
    proxy_key=(os.getenv("BRAWL_OFFICIAL_PROXY_KEY") or "").strip()
    if proxy_url and proxy_key:
        r=requests.get(
            proxy_url,
            params={"action":"battlelog","tag":clean},
            headers={"X-Sens-Key":proxy_key,"Accept":"application/json","User-Agent":"SensGPT-TitaniAbusivi/1.0"},
            timeout=20,
        )
    else:
        r=requests.get(f"{API}/players/%23{clean}/battlelog",headers=_headers(),timeout=15)
    r.raise_for_status()
    data=r.json()
    if isinstance(data,dict) and isinstance(data.get("items"),list):
        return data["items"]
    raise RuntimeError("Battlelog response missing items")

def normalize_match(row):
    event=row.get("event") or {}; battle=row.get("battle") or {}
    if str(battle.get("type") or "").casefold() not in RANKED_TYPES:return None
    mode=event.get("mode") or battle.get("mode")
    if mode not in RANKED_MODES:return None
    map_name=str(event.get("map") or "").strip()
    pool=current_ranked_pool()
    if not pool or (mode,map_name) not in pool:return None
    teams=battle.get("teams")
    if not isinstance(teams,list) or len(teams)!=2 or any(len(t)!=3 for t in teams):return None
    def side(team):
        return [(str(p.get("tag") or "").lstrip("#"),int((p.get("brawler") or {}).get("id") or 0)) for p in team]
    a,b=side(teams[0]),side(teams[1])
    if any(not tag or not bid for tag,bid in a+b):return None
    tags=sorted(tag for tag,_ in a+b)
    key=hashlib.sha256((str(row.get("battleTime") or "")+"|"+",".join(tags)).encode()).hexdigest()
    result=str(battle.get("result") or "").casefold()
    # Result belongs to the harvested player. Locate that player outside this
    # function before assigning a winner.
    return {"key":key,"time":row.get("battleTime"),"mode":mode,
            "map":map_name,"teams":[a,b],"result":result}

def collect(seed_tags,max_players=250,sleep_s=.08):
    queue=[str(x).strip().lstrip("#") for x in seed_tags if str(x).strip()]
    seen_players=set(); matches={}; diagnostics=defaultdict(int)
    while queue and len(seen_players)<max_players:
        tag=queue.pop(0)
        if tag in seen_players:continue
        seen_players.add(tag)
        try: rows=battlelog(tag)
        except requests.HTTPError as exc:
            diagnostics["http_"+str(getattr(exc.response,"status_code","error"))]+=1;continue
        except Exception as exc:
            diagnostics["error_"+type(exc).__name__]+=1;continue
        diagnostics["battlelog_ok"]+=1;diagnostics["raw_battles"]+=len(rows)
        for raw in rows:
            battle=raw.get("battle") or {}
            diagnostics["type_"+str(battle.get("type") or "missing").casefold()]+=1
            m=normalize_match(raw)
            if not m:continue
            diagnostics["ranked_matches_seen"]+=1
            # Preserve the first copy, but attach perspective needed for W/L.
            if m["key"] not in matches:
                for idx,team in enumerate(m["teams"]):
                    if any(t==tag for t,_ in team):
                        if m["result"]=="victory":m["winner"]=idx
                        elif m["result"]=="defeat":m["winner"]=1-idx
                        else:m["winner"]=None
                        break
                matches[m["key"]]=m
            for team in m["teams"]:
                for other,_ in team:
                    if other not in seen_players and other not in queue and len(queue)<max_players*3:queue.append(other)
        time.sleep(sleep_s)
    return list(matches.values()),dict(diagnostics)

def aggregate(matches,min_sample=20):
    """Build every variable observable from final Ranked battle logs.

    Scopes:
      map_pick: Brawler performance on exact map/mode.
      synergy: pair performance when two Brawlers are teammates.
      counter: candidate performance while facing an opponent Brawler.
    Ban/order/tier are deliberately not fabricated because battle logs do not
    expose them reliably.
    """
    pick=defaultdict(lambda:[0,0]); synergy=defaultdict(lambda:[0,0]); counter=defaultdict(lambda:[0,0])
    for m in matches:
        if m.get("winner") is None or not m.get("map") or not m.get("mode"):continue
        mode,map_name=str(m["mode"]),str(m["map"])
        for side in (0,1):
            enemy=1-side; won=1 if m["winner"]==side else 0
            team_ids=[bid for _,bid in m["teams"][side]]
            enemy_ids=[bid for _,bid in m["teams"][enemy]]
            for candidate in team_ids:
                p=pick[(mode,map_name,candidate)];p[0]+=won;p[1]+=1
                for mate in team_ids:
                    if mate==candidate:continue
                    s=synergy[(mode,map_name,candidate,mate)];s[0]+=won;s[1]+=1
                for target in enemy_ids:
                    x=counter[(mode,map_name,candidate,target)];x[0]+=won;x[1]+=1
    rows=[]
    def add(scope,mode,map_name,bid,other,wins,n):
        if n<min_sample:return
        rows.append({"stat_scope":scope,"mode":mode,"map_name":map_name,"brawler_id":bid,
                     "other_brawler_id":other,"games":n,"wins":wins,"losses":n-wins,
                     "win_rate":round(100*wins/n,3),"source":"Supercell Ranked battlelog"})
    for (mode,map_name,bid),(wins,n) in pick.items():add("map_pick",mode,map_name,bid,0,wins,n)
    for (mode,map_name,bid,mate),(wins,n) in synergy.items():add("synergy",mode,map_name,bid,mate,wins,n)
    for (mode,map_name,bid,target),(wins,n) in counter.items():add("counter",mode,map_name,bid,target,wins,n)
    return rows

def registered_seeds():
    """Use registered community accounts as safe automatic crawl seeds."""
    url=(os.getenv("SUPABASE_URL") or "").rstrip("/")
    key=(os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY") or "").strip()
    if not url or not key:return []
    h={"apikey":key,"Authorization":"Bearer "+key,"Accept":"application/json"}
    r=requests.get(url+"/rest/v1/community_members?select=player_tag&is_active=eq.true&player_tag=not.is.null&limit=1000",
                   headers=h,timeout=20);r.raise_for_status()
    return [str(x.get("player_tag") or "").strip().lstrip("#") for x in r.json() if x.get("player_tag")]

def upsert(rows):
    url=(os.getenv("SUPABASE_URL") or "").rstrip("/")
    key=(os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY") or "").strip()
    if not url or not key:raise RuntimeError("Supabase credentials missing")
    h={"apikey":key,"Authorization":"Bearer "+key,"Content-Type":"application/json","Prefer":"resolution=merge-duplicates"}
    # Primary key makes repeated syncs idempotent for each exact evidence cell.
    for i in range(0,len(rows),500):
        r=requests.post(url+"/rest/v1/ranked_draft_stats?on_conflict=stat_scope,mode,map_name,brawler_id,other_brawler_id",
                        headers=h,json=rows[i:i+500],timeout=30);r.raise_for_status()

def sync_once():
    seeds=[x for x in (os.getenv("RANKED_SYNC_SEEDS") or "").replace(" ","").split(",") if x]
    seeds=list(dict.fromkeys(seeds+registered_seeds()))
    if not seeds:return {"seeds":0,"matches":0,"rows":0}
    matches,diagnostics=collect(seeds,max_players=int(os.getenv("RANKED_SYNC_MAX_PLAYERS","250")))
    rows=aggregate(matches,min_sample=int(os.getenv("RANKED_SYNC_MIN_SAMPLE","20")))
    upsert(rows)
    return {"seeds":len(seeds),"matches":len(matches),"rows":len(rows),"diagnostics":diagnostics}

if __name__=="__main__":
    result=sync_once()
    print("ranked matchup sync: "+str(result),flush=True)
