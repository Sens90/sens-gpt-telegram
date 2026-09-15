"""Runtime integration for live BrawlTrack profiles and deterministic meta."""
import os, re, requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import community_features

_ALLOWED_TAG=re.compile(r"[0289PYLQGRJCUV]{3,15}",re.I)
_BRAWLTRACK_PLAYER="https://brawltrack.app/api/player/{tag}"
ROME=ZoneInfo("Europe/Rome")

def _clean_tag(v):
    t=str(v or "").upper().replace("#","").strip(); return t if _ALLOWED_TAG.fullmatch(t) else None

def _first(data,*keys):
    wanted={k.casefold() for k in keys}
    def walk(v):
        if isinstance(v,dict):
            for k,x in v.items():
                if str(k).casefold() in wanted and x not in (None,"",[],{}): return x
            for x in v.values():
                y=walk(x)
                if y not in (None,"",[],{}): return y
        elif isinstance(v,list):
            for x in v:
                y=walk(x)
                if y not in (None,"",[],{}): return y
        return None
    return walk(data)

def _number(v):
    if v is None or isinstance(v,bool): return None
    if isinstance(v,(int,float)): return int(v)
    m=re.search(r"-?\d[\d.,]*",str(v));
    if not m: return None
    d=re.sub(r"\D","",m.group(0)); return int(d) if d else None

def _label(v):
    if v in (None,"",[],{}): return None
    if isinstance(v,str): return v.strip() or None
    if isinstance(v,(int,float)): return str(v)
    if isinstance(v,dict):
        for k in ("name","rank","tier","division","label","title"):
            if v.get(k) not in (None,""): return str(v[k]).strip()
    return None

def _club(data):
    c=_first(data,"activeClub","active_club","club")
    if isinstance(c,dict):
        return _label(c.get("name") or c.get("clubName") or c.get("club_name")), _label(c.get("tag") or c.get("clubTag") or c.get("club_tag"))
    return _label(_first(data,"activeClubName","active_club_name","clubName","club_name")), _label(_first(data,"activeClubTag","active_club_tag","clubTag","club_tag"))

def fetch_brawltrack_player(player_tag):
    tag=_clean_tag(player_tag)
    if not tag: return None
    try:
        r=requests.get(_BRAWLTRACK_PLAYER.format(tag=tag),headers={"User-Agent":"SensGPT-TitaniAbusivi/1.0","Accept":"application/json","Cache-Control":"no-cache","Pragma":"no-cache"},params={"_":int(datetime.now(timezone.utc).timestamp())},timeout=15)
        if r.status_code!=200: return None
        data=r.json()
        if not isinstance(data,dict): return None
        root=data.get("player") if isinstance(data.get("player"),dict) else data
        name=_label(_first(root,"name","playerName","player_name")); trophies=_number(_first(root,"trophies","currentTrophies","current_trophies"))
        if not name or trophies is None: return None
        # BrawlTrack /api/player/{tag} is authoritative for the player's ACTIVE club.
        # Never overwrite it with a scraped/cached club from another site.
        club_name,club_tag=_club(root)
        if not club_name and root is not data:
            club_name,club_tag=_club(data)
        if club_tag:
            clean_club_tag=_clean_tag(club_tag)
            club_tag="#"+clean_club_tag if clean_club_tag else str(club_tag).strip()
        p={"name":name,"tag":"#"+tag,"trophies":trophies,"brawlers":_number(_first(root,"brawlersCount","brawlerCount","brawlers_count","brawler_count")),"level":_number(_first(root,"expLevel","level","accountLevel","account_level")),"prestige":_number(_first(root,"prestige","prestigeLevel","prestige_level")),"wins_3v3":_number(_first(root,"3vs3Victories","3v3Victories","wins3v3","wins_3v3","victories3v3")),"wins_solo":_number(_first(root,"soloVictories","soloWins","wins_solo","solo_wins")),"wins_duo":_number(_first(root,"duoVictories","duoWins","wins_duo","duo_wins")),"club_name":club_name,"club_tag":club_tag,"club":f"{club_name} ({club_tag})" if club_name and club_tag else club_name,"ranked_current":_label(_first(root,"rankedCurrent","ranked_current","currentRank","current_rank")),"ranked_current_elo":_number(_first(root,"rankedCurrentElo","ranked_current_elo","currentElo","current_elo")),"ranked_season_peak":_label(_first(root,"rankedSeasonPeak","ranked_season_peak","seasonPeak","season_peak","bestRankThisSeason")),"ranked_season_peak_elo":_number(_first(root,"rankedSeasonPeakElo","ranked_season_peak_elo","seasonPeakElo","season_peak_elo")),"ranked_career_peak":_label(_first(root,"rankedCareerPeak","ranked_career_peak","careerPeak","career_peak","highestRank","highest_rank")),"ranked_career_peak_elo":_number(_first(root,"rankedCareerPeakElo","ranked_career_peak_elo","careerPeakElo","career_peak_elo","highestElo","highest_elo")),"source":"BrawlTrack"}
        p["ranked_peak"]=p.get("ranked_career_peak"); return p
    except Exception as e:
        print("ERRORE BRAWLTRACK PLAYER:",tag,repr(e),flush=True); return None

def _merge(live,fallback):
    if not live:return fallback
    if not fallback:return live
    out=dict(fallback)
    for k,v in live.items():
        if v not in (None,"",[],{}): out[k]=v
    # Club is dynamic and BrawlTrack is authoritative. If the live payload says
    # no club, do not resurrect a stale club from the legacy fallback.
    for k in ("club","club_name","club_tag"):
        out[k]=live.get(k)
    return out

def _parse_history_dt(row):
    try:return datetime.fromisoformat(str(row.get("recorded_at") or "").replace("Z","+00:00"))
    except Exception:return None

def _same_day_changes(base_calculator,history,current):
    changes=base_calculator(history,current)
    if changes.get("today") is not None:return changes
    now_local=datetime.now(ROME); today=now_local.date(); same=[]
    for row in history or []:
        dt=_parse_history_dt(row)
        if dt and dt.astimezone(ROME).date()==today:
            try:same.append((dt,int(row["trophies"])))
            except Exception:pass
    if same:
        same.sort(key=lambda x:x[0]); changes["today"]=int(current)-same[0][1]
    return changes

def _is_direct_meta(q):
    q=re.sub(r"\s+"," ",str(q or "").strip().casefold()); return bool(re.fullmatch(r"(?:il\s+)?meta(?:\s+attuale|\s+di\s+adesso|\s+ora)?[?!.]*",q))

def _search_brawltrack_meta():
    key=os.environ.get("TAVILY_API_KEY")
    if not key:return {}
    r=requests.post("https://api.tavily.com/search",json={"api_key":key,"query":"Brawl Stars current meta BrawlTrack tier list site:brawltrack.app","search_depth":"advanced","max_results":12,"include_raw_content":True,"include_answer":False,"include_domains":["brawltrack.app"]},timeout=20); r.raise_for_status(); return r.json()

_original_init=community_features.CommunityFeatures.__init__
_original_handle=community_features.CommunityFeatures.handle_command

def _patched_init(self,supabase_url,supabase_key,player_fetcher,history_fetcher,change_calculator,number_formatter,change_formatter):
    legacy=player_fetcher
    def live_first(tag):
        live=fetch_brawltrack_player(tag); fallback=None
        if live is None or any(live.get(k) is None for k in ("brawlers","wins_3v3","ranked_current","ranked_career_peak")):
            try:fallback=legacy(tag)
            except Exception as e:print("ERRORE FALLBACK PLAYER:",repr(e),flush=True)
        return _merge(live,fallback)
    def fixed_changes(history,current): return _same_day_changes(change_calculator,history,current)
    _original_init(self,supabase_url,supabase_key,live_first,history_fetcher,fixed_changes,number_formatter,change_formatter)

async def _patched_handle(self,message,context,question):
    q=re.sub(r"^[!/]+","",(question or "").strip()).strip().strip("\"'“”‘’ ").strip()
    if _is_direct_meta(q):
        try:
            from meta_current import render_current_meta
            await context.bot.send_chat_action(chat_id=message.chat_id,action="typing")
            report=render_current_meta(_search_brawltrack_meta(),"both")
            await message.reply_text(report or "META ATTUALE\n\nBrawlTrack non restituisce abbastanza dati verificabili in questo momento. Riprova tra poco.")
        except Exception as e:
            print("META DIRECT ERRORE:",repr(e),flush=True); await message.reply_text("META ATTUALE\n\nNon riesco a verificare i dati BrawlTrack in questo momento.")
        return True
    return await _original_handle(self,message,context,question)

community_features.CommunityFeatures.__init__=_patched_init
community_features.CommunityFeatures.handle_command=_patched_handle
