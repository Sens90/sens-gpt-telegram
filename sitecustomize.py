"""Runtime integration for live BrawlTrack profiles and deterministic meta."""
import html, os, re, requests, sys, threading, time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import community_features
import player_tracking

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
    m=re.search(r"-?\d[\d.,]*",str(v))
    if not m:return None
    d=re.sub(r"\D","",m.group(0)); return int(d) if d else None

def _label(v):
    if v in (None,"",[],{}):return None
    if isinstance(v,str):return v.strip() or None
    if isinstance(v,(int,float)):return str(v)
    if isinstance(v,dict):
        for k in ("name","rank","tier","division","label","title"):
            if v.get(k) not in (None,""):return str(v[k]).strip()
    return None

def _club(data):
    c=_first(data,"activeClub","active_club","club")
    if isinstance(c,dict):return _label(c.get("name") or c.get("clubName") or c.get("club_name")),_label(c.get("tag") or c.get("clubTag") or c.get("club_tag"))
    return _label(_first(data,"activeClubName","active_club_name","clubName","club_name")),_label(_first(data,"activeClubTag","active_club_tag","clubTag","club_tag"))

def fetch_brawltrack_player(player_tag):
    tag=_clean_tag(player_tag)
    if not tag:return None
    try:
        r=requests.get(_BRAWLTRACK_PLAYER.format(tag=tag),headers={"User-Agent":"SensGPT-TitaniAbusivi/1.0","Accept":"application/json","Cache-Control":"no-cache","Pragma":"no-cache"},params={"_":int(datetime.now(timezone.utc).timestamp())},timeout=15)
        if r.status_code!=200:return None
        data=r.json()
        if not isinstance(data,dict):return None
        root=data.get("player") if isinstance(data.get("player"),dict) else data
        name=_label(_first(root,"name","playerName","player_name")); trophies=_number(_first(root,"trophies","currentTrophies","current_trophies"))
        if not name or trophies is None:return None
        club_name,club_tag=_club(root)
        if not club_name and root is not data:club_name,club_tag=_club(data)
        if club_tag:
            clean=_clean_tag(club_tag);club_tag="#"+clean if clean else str(club_tag).strip()
        p={"name":name,"tag":"#"+tag,"trophies":trophies,"brawlers":_number(_first(root,"brawlersCount","brawlerCount","brawlers_count","brawler_count")),"level":_number(_first(root,"expLevel","level","accountLevel","account_level")),"prestige":_number(_first(root,"prestige","prestigeLevel","prestige_level")),"wins_3v3":_number(_first(root,"3vs3Victories","3v3Victories","wins3v3","wins_3v3","victories3v3")),"wins_solo":_number(_first(root,"soloVictories","soloWins","wins_solo","solo_wins")),"wins_duo":_number(_first(root,"duoVictories","duoWins","wins_duo","duo_wins")),"club_name":club_name,"club_tag":club_tag,"club":club_name,"ranked_current":_label(_first(root,"rankedCurrent","ranked_current","currentRank","current_rank")),"ranked_current_elo":_number(_first(root,"rankedCurrentElo","ranked_current_elo","currentElo","current_elo")),"ranked_season_peak":_label(_first(root,"rankedSeasonPeak","ranked_season_peak","seasonPeak","season_peak","bestRankThisSeason")),"ranked_season_peak_elo":_number(_first(root,"rankedSeasonPeakElo","ranked_season_peak_elo","seasonPeakElo","season_peak_elo")),"ranked_career_peak":_label(_first(root,"rankedCareerPeak","ranked_career_peak","careerPeak","career_peak","highestRank","highest_rank")),"ranked_career_peak_elo":_number(_first(root,"rankedCareerPeakElo","ranked_career_peak_elo","careerPeakElo","career_peak_elo","highestElo","highest_elo")),"source":"BrawlTrack"}
        p["ranked_peak"]=p.get("ranked_career_peak");return p
    except Exception as e:print("ERRORE BRAWLTRACK PLAYER:",tag,repr(e),flush=True);return None

def _live_club(player_tag,timeout=15):
    tag=_clean_tag(player_tag)
    if not tag:return None,None
    try:
        r=requests.get(f"https://brawlify.com/player/{tag}?refresh={int(time.time())}",headers={"User-Agent":"Mozilla/5.0 (SensGPT-TitaniAbusivi/1.0)","Cache-Control":"no-cache","Pragma":"no-cache","Accept-Language":"it-IT,it;q=0.9,en;q=0.8"},timeout=timeout)
        if r.status_code!=200:return None,None
        page=html.unescape(r.text)
        href=re.search(r'href=["\']/(?:it/)?club/(?:%23|#)?([0289PYLQGRJCUV]{3,15})(?:[^"\']*)["\']',page,re.I)
        if not href:return None,None
        raw=href.group(1).upper();club_tag="#"+raw
        anchor=re.search(r'<a[^>]+href=["\']/(?:it/)?club/(?:%23|#)?'+re.escape(raw)+r'[^"\']*["\'][^>]*>(.*?)</a>',page,re.I|re.S)
        if anchor:
            name=re.sub(r"\s+"," ",html.unescape(re.sub(r"<[^>]+>"," ",anchor.group(1)))).strip()
            if name and name.casefold() not in {"club","visualizza club","view club"}:return name,club_tag
        cr=requests.get(f"https://brawlify.com/club/{raw}?refresh={int(time.time())}",headers={"User-Agent":"Mozilla/5.0 (SensGPT-TitaniAbusivi/1.0)","Cache-Control":"no-cache"},timeout=timeout)
        if cr.status_code==200:
            cp=html.unescape(cr.text);title=re.search(r"<title>\s*(.*?)\s*(?:#|—|-).*?</title>",cp,re.I|re.S)
            if title:
                name=re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",title.group(1))).strip()
                if name:return name,club_tag
        return None,club_tag
    except Exception as e:print("PROFILE CLUB LIVE failure:",repr(e),flush=True);return None,None

player_tracking.get_live_club=_live_club
_original_extract=player_tracking.extract_brawlzone_ranked
def _extract_with_live_club(page):
    data=_original_extract(page)
    tag_match=re.search(r"\(#([0289PYLQGRJCUV]{3,15})\)",page or "",re.I)
    if tag_match:
        name,tag=_live_club(tag_match.group(1))
        if name or tag:
            data["club_name"]=name;data["club_tag"]=tag
            data["club"]=(name or "Senza club / non disponibile")+(f"\nTag club: {tag}" if tag else "")
    return data
player_tracking.extract_brawlzone_ranked=_extract_with_live_club
print("PROFILE CLUB LIVE SITECUSTOMIZE INSTALLATA",flush=True)

def _merge(live,fallback):
    if not live:return fallback
    if not fallback:return live
    out=dict(fallback)
    for k,v in live.items():
        if v not in (None,"",[],{}):out[k]=v
    for k in ("club","club_name","club_tag"):out[k]=live.get(k)
    return out

def _parse_history_dt(row):
    try:
        dt=datetime.fromisoformat(str(row.get("recorded_at") or "").replace("Z","+00:00"));return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:return None

def _rome_changes(history,current):
    now=datetime.now(timezone.utc);start=now.astimezone(ROME).replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc);parsed=[]
    for row in history or []:
        dt=_parse_history_dt(row)
        try:
            if dt:parsed.append((dt.astimezone(timezone.utc),int(row["trophies"])))
        except Exception:pass
    parsed.sort(key=lambda x:x[0])
    def before(target):
        value=None
        for dt,trophies in parsed:
            if dt<=target:value=trophies
            else:break
        return value
    today=before(start)
    if today is None:
        for dt,trophies in parsed:
            if start<=dt<=now:today=trophies;break
    changes={"today":int(current)-today if today is not None else None}
    for days,key in ((7,"7d"),(15,"15d"),(30,"30d"),(90,"90d")):
        old=before(now-timedelta(days=days));changes[key]=int(current)-old if old is not None else None
    return changes

def _is_direct_meta(q):
    q=re.sub(r"\s+"," ",str(q or "").strip().casefold());return bool(re.fullmatch(r"(?:il\s+)?meta(?:\s+attuale|\s+di\s+adesso|\s+ora)?[?!.]*",q))
def _search_brawltrack_meta():
    key=os.environ.get("TAVILY_API_KEY")
    if not key:return {}
    r=requests.post("https://api.tavily.com/search",json={"api_key":key,"query":"Brawl Stars current meta BrawlTrack tier list site:brawltrack.app","search_depth":"advanced","max_results":12,"include_raw_content":True,"include_answer":False,"include_domains":["brawltrack.app"]},timeout=20);r.raise_for_status();return r.json()

_original_init=community_features.CommunityFeatures.__init__;_original_handle=community_features.CommunityFeatures.handle_command
def _patched_init(self,supabase_url,supabase_key,player_fetcher,history_fetcher,change_calculator,number_formatter,change_formatter):
    legacy=player_fetcher
    def live_first(tag):
        live=fetch_brawltrack_player(tag);fallback=None
        if live is None or any(live.get(k) is None for k in ("brawlers","wins_3v3","ranked_current","ranked_career_peak")):
            try:fallback=legacy(tag)
            except Exception as e:print("ERRORE FALLBACK PLAYER:",repr(e),flush=True)
        return _merge(live,fallback)
    _original_init(self,supabase_url,supabase_key,live_first,history_fetcher,_rome_changes,number_formatter,change_formatter)
async def _patched_handle(self,message,context,question):
    q=re.sub(r"^[!/]+","",(question or "").strip()).strip().strip("\"'“”‘’ ").strip()
    if _is_direct_meta(q):
        try:
            from meta_current import render_current_meta
            await context.bot.send_chat_action(chat_id=message.chat_id,action="typing");report=render_current_meta(_search_brawltrack_meta(),"both");await message.reply_text(report or "META ATTUALE\n\nBrawlTrack non restituisce abbastanza dati verificabili in questo momento. Riprova tra poco.")
        except Exception as e:print("META DIRECT ERRORE:",repr(e),flush=True);await message.reply_text("META ATTUALE\n\nNon riesco a verificare i dati BrawlTrack in questo momento.")
        return True
    return await _original_handle(self,message,context,question)
community_features.CommunityFeatures.__init__=_patched_init;community_features.CommunityFeatures.handle_command=_patched_handle

def _patch_app_runtime():
    for _ in range(2400):
        main=sys.modules.get("__main__")
        if main and hasattr(main,"calculate_trophy_changes") and hasattr(main,"community"):
            main.calculate_trophy_changes=_rome_changes
            try:main.community.change_calculator=_rome_changes
            except Exception:pass
            print("PROFILE TROPHY ROME SITECUSTOMIZE INSTALLATA",flush=True);return
        time.sleep(0.25)
    print("PROFILE TROPHY ROME SITECUSTOMIZE NON INSTALLATA",flush=True)
threading.Thread(target=_patch_app_runtime,daemon=True).start()
