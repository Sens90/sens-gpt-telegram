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

def _headers():
    token=(os.getenv("BRAWL_STARS_API_TOKEN") or os.getenv("BRAWL_API_TOKEN") or "").strip()
    if not token: raise RuntimeError("Brawl Stars API token missing")
    return {"Authorization":"Bearer "+token,"Accept":"application/json","User-Agent":"SensGPT/1.0"}

def battlelog(tag):
    clean=str(tag).strip().lstrip("#")
    r=requests.get(f"{API}/players/%23{clean}/battlelog",headers=_headers(),timeout=15)
    r.raise_for_status()
    return r.json().get("items") or []

def normalize_match(row):
    event=row.get("event") or {}; battle=row.get("battle") or {}
    if str(battle.get("type") or "").casefold() not in RANKED_TYPES:return None
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
    return {"key":key,"time":row.get("battleTime"),"mode":event.get("mode") or battle.get("mode"),
            "map":event.get("map"),"teams":[a,b],"result":result}

def collect(seed_tags,max_players=250,sleep_s=.08):
    queue=[str(x).strip().lstrip("#") for x in seed_tags if str(x).strip()]
    seen_players=set(); matches={}
    while queue and len(seen_players)<max_players:
        tag=queue.pop(0)
        if tag in seen_players:continue
        seen_players.add(tag)
        try: rows=battlelog(tag)
        except Exception: continue
        for raw in rows:
            m=normalize_match(raw)
            if not m:continue
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
    return list(matches.values())

def aggregate(matches,min_sample=20):
    # For each exact map/mode pair, measure candidate win rate when facing target.
    stats=defaultdict(lambda:[0,0])
    for m in matches:
        if m.get("winner") is None or not m.get("map") or not m.get("mode"):continue
        for side in (0,1):
            enemy=1-side
            won=1 if m["winner"]==side else 0
            for _,candidate in m["teams"][side]:
                for _,target in m["teams"][enemy]:
                    rec=stats[(target,candidate,str(m["mode"]),str(m["map"]))]
                    rec[0]+=won;rec[1]+=1
    rows=[]
    for (target,candidate,mode,map_name),(wins,n) in stats.items():
        if n<min_sample:continue
        # score is empirical head-to-head win rate. Keep sample_size alongside it.
        rows.append({"brawler_id":target,"counter_brawler_id":candidate,"mode":mode,"map_name":map_name,
                     "score":round(100*wins/n,3),"sample_size":n,"source":"Supercell Ranked battlelog"})
    return rows

def upsert(rows):
    url=(os.getenv("SUPABASE_URL") or "").rstrip("/")
    key=(os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY") or "").strip()
    if not url or not key:raise RuntimeError("Supabase credentials missing")
    h={"apikey":key,"Authorization":"Bearer "+key,"Content-Type":"application/json","Prefer":"resolution=merge-duplicates"}
    # Requires a unique constraint on (brawler_id,counter_brawler_id,mode,map_name).
    for i in range(0,len(rows),500):
        r=requests.post(url+"/rest/v1/brawler_counters?on_conflict=brawler_id,counter_brawler_id,mode,map_name",
                        headers=h,json=rows[i:i+500],timeout=30);r.raise_for_status()

if __name__=="__main__":
    seeds=[x for x in (os.getenv("RANKED_SYNC_SEEDS") or "").replace(" ","").split(",") if x]
    if not seeds:raise SystemExit("RANKED_SYNC_SEEDS is empty")
    matches=collect(seeds,max_players=int(os.getenv("RANKED_SYNC_MAX_PLAYERS","250")))
    rows=aggregate(matches,min_sample=int(os.getenv("RANKED_SYNC_MIN_SAMPLE","20")))
    upsert(rows)
    print(f"ranked matchup sync: matches={len(matches)} rows={len(rows)}",flush=True)
