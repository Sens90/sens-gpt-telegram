"""Ranked matchup collector for Sens GPT.

Collects only final team lineups exposed by the official Brawl Stars battle log.
It never invents bans. Matches are deduplicated before aggregation.

Run as a periodic job with a set of seed player tags. The collector expands one
hop through players found in Ranked matches and upserts exact map/mode matchup
rows into Supabase brawler_counters.
"""
import hashlib, os, time
from collections import defaultdict
import requests

API="https://api.brawlstars.com/v1"
RANKED_TYPES={"ranked","soloranked","teamranked"}
RANKED_MODES={"gemGrab","brawlBall","hotZone","bounty","heist","knockout"}
RANKED_WIKI_URL=os.getenv("RANKED_WIKI_URL","https://brawlstars.fandom.com/wiki/Version_History")
_ranked_pool_cache={"ts":0.0,"pairs":None}

# Current Ranked base pool as published by Brawl Stars Wiki. The two Featured
# maps are supplied by the current official Supercell season notes below.
# Keep English canonical map names internally; user-facing localization is
# handled elsewhere.
RANKED_WIKI_BASE_POOL={
    "gemGrab":{"Double Swoosh","Gem Fort","Hard Rock Mine","Undermine"},
    "heist":{"Bridge Too Far","Hot Potato","Kaboom Canyon","Safe Zone"},
    "bounty":{"Dry Season","Hideout","Layer Cake","Shooting Star"},
    "brawlBall":{"Center Stage","Pinball Dreams","Sneaky Fields","Triple Dribble"},
    "hotZone":{"Dueling Beetles","Open Business","Parallel Plays","Ring of Fire"},
    "knockout":{"Belle's Rock","Flaring Phoenix","New Horizons","Out in the Open"},
}
# Supercell August 2026 release notes, Ranked Season 2.
RANKED_FEATURED_MODE="hotZone"
RANKED_FEATURED_MAPS={"In the Liminal","Quick Travel"}

def current_ranked_pool():
    """Return the verified Ranked pool: Wiki base rotation + official Featured maps.

    Fail closed if the expected 26-map/4-4-4-4-4-6 structure is ever broken.
    This intentionally no longer scrapes BrawlZone.
    """
    now=time.time()
    if _ranked_pool_cache["pairs"] is not None and now-_ranked_pool_cache["ts"]<21600:
        return _ranked_pool_cache["pairs"]
    try:
        pairs={(mode,name) for mode,names in RANKED_WIKI_BASE_POOL.items() for name in names}
        pairs.update((RANKED_FEATURED_MODE,name) for name in RANKED_FEATURED_MAPS)
        counts={mode:sum(1 for m,_ in pairs if m==mode) for mode in RANKED_MODES}
        distribution=sorted(counts.values())
        if len(pairs)!=26 or distribution!=[4,4,4,4,4,6]:
            raise RuntimeError(f"Ranked pool suspicious: total={len(pairs)} counts={counts}")
        featured_modes=[mode for mode,count in counts.items() if count==6]
        if featured_modes != [RANKED_FEATURED_MODE]:
            raise RuntimeError(f"Featured mode mismatch: expected={RANKED_FEATURED_MODE} actual={featured_modes}")
        maps_by_mode={mode:sorted(name for m,name in pairs if m==mode) for mode in sorted(RANKED_MODES)}
        print(f"RANKED POOL OK: source=wiki+supercell total={len(pairs)} counts={counts} featured={RANKED_FEATURED_MODE} maps={maps_by_mode}",flush=True)
        _ranked_pool_cache.update({"ts":now,"pairs":pairs})
        return pairs
    except Exception as exc:
        print(f"RANKED POOL ERROR: {type(exc).__name__}: {exc}",flush=True)
        return set()


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
