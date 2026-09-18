"""Runtime integration: official Supercell first, BrawlTrack/legacy only for missing data."""
import os, re, requests, threading, time
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
    if isinstance(c,dict):
        return _label(c.get("name") or c.get("clubName") or c.get("club_name")),_label(c.get("tag") or c.get("clubTag") or c.get("club_tag"))
    return _label(_first(data,"activeClubName","active_club_name","clubName","club_name")),_label(_first(data,"activeClubTag","active_club_tag","clubTag","club_tag"))

def fetch_official_player(player_tag):
    tag=_clean_tag(player_tag); url=os.environ.get("BRAWL_OFFICIAL_PROXY_URL"); key=os.environ.get("BRAWL_OFFICIAL_PROXY_KEY")
    if not tag or not url or not key:return None
    try:
        r=requests.get(url,params={"action":"player","tag":tag},headers={"X-Sens-Key":key,"Accept":"application/json","User-Agent":"SensGPT-TitaniAbusivi/1.0"},timeout=20)
        if r.status_code!=200: print("SUPERCELL PROXY HTTP:",r.status_code,flush=True); return None
        d=r.json()
        if not isinstance(d,dict) or not d.get("name") or d.get("trophies") is None:return None
        c=d.get("club") if isinstance(d.get("club"),dict) else {}; ct=str(c.get("tag") or "").strip() or None
        if ct and not ct.startswith("#"):ct="#"+ct
        b=d.get("brawlers") if isinstance(d.get("brawlers"),list) else []
        return {"name":d.get("name"),"tag":str(d.get("tag") or ("#"+tag)),"trophies":_number(d.get("trophies")),"brawlers":len(b),"level":_number(d.get("expLevel")),"wins_3v3":_number(d.get("3vs3Victories")),"wins_solo":_number(d.get("soloVictories")),"wins_duo":_number(d.get("duoVictories")),"club_name":c.get("name") or None,"club_tag":ct,"club":c.get("name") or None,"source":"Supercell Official API","official_brawlers":b}
    except Exception as e: print("ERRORE SUPERCELL OFFICIAL:",tag,repr(e),flush=True); return None

def fetch_brawltrack_player(player_tag):
    tag=_clean_tag(player_tag)
    if not tag:return None
    try:
        r=requests.get(_BRAWLTRACK_PLAYER.format(tag=tag),headers={"User-Agent":"SensGPT-TitaniAbusivi/1.0","Accept":"application/json","Cache-Control":"no-cache"},timeout=15)
        if r.status_code!=200:return None
        data=r.json(); root=data.get("player") if isinstance(data,dict) and isinstance(data.get("player"),dict) else data
        if not isinstance(root,dict):return None
        name=_label(_first(root,"name","playerName","player_name")); trophies=_number(_first(root,"trophies","currentTrophies","current_trophies"))
        if not name or trophies is None:return None
        cn,ct=_club(root)
        return {"name":name,"tag":"#"+tag,"trophies":trophies,"brawlers":_number(_first(root,"brawlersCount","brawlerCount")),"level":_number(_first(root,"expLevel","level")),"prestige":_number(_first(root,"prestige","prestigeLevel")),"wins_3v3":_number(_first(root,"3vs3Victories","3v3Victories","wins_3v3")),"wins_solo":_number(_first(root,"soloVictories","soloWins","wins_solo")),"wins_duo":_number(_first(root,"duoVictories","duoWins","wins_duo")),"club_name":cn,"club_tag":ct,"club":cn,"ranked_current":_label(_first(root,"rankedCurrent","ranked_current","currentRank")),"ranked_current_elo":_number(_first(root,"rankedCurrentElo","ranked_current_elo")),"ranked_season_peak":_label(_first(root,"rankedSeasonPeak","ranked_season_peak","seasonPeak")),"ranked_season_peak_elo":_number(_first(root,"rankedSeasonPeakElo","ranked_season_peak_elo")),"ranked_career_peak":_label(_first(root,"rankedCareerPeak","ranked_career_peak","careerPeak","highestRank")),"ranked_career_peak_elo":_number(_first(root,"rankedCareerPeakElo","ranked_career_peak_elo")),"source":"BrawlTrack"}
    except Exception as e: print("ERRORE BRAWLTRACK PLAYER:",tag,repr(e),flush=True); return None

def _merge_priority(official,secondary):
    if not official:return secondary
    if not secondary:return official
    out=dict(secondary)
    for k,v in official.items():
        if v not in (None,"",[],{}):out[k]=v
    for k in ("name","tag","trophies","club","club_name","club_tag","brawlers","level","wins_3v3","wins_solo","wins_duo"):out[k]=official.get(k)
    out["source"]="Supercell Official API"; return out

def _parse_history_dt(row):
    try:return datetime.fromisoformat(str(row.get("recorded_at") or "").replace("Z","+00:00"))
    except Exception:return None

def _same_day_changes(base_calculator,history,current):
    changes=base_calculator(history,current); now_local=datetime.now(ROME); same=[]
    for row in history or []:
        dt=_parse_history_dt(row)
        if dt and dt.astimezone(ROME).date()==now_local.date():
            try:same.append((dt,int(row["trophies"])))
            except Exception:pass
    if same:same.sort(key=lambda x:x[0]); changes["today"]=int(current)-same[0][1]
    return changes

def _is_direct_meta(q):
    q=re.sub(r"\s+"," ",str(q or "").strip().casefold()); return bool(re.fullmatch(r"(?:il\s+)?meta(?:\s+attuale|\s+di\s+adesso|\s+ora)?[?!.]*",q))

def _search_brawltrack_meta():
    key=os.environ.get("TAVILY_API_KEY")
    if not key:return {}
    r=requests.post("https://api.tavily.com/search",json={"api_key":key,"query":"Brawl Stars current meta BrawlTrack tier list site:brawltrack.app","search_depth":"advanced","max_results":12,"include_raw_content":True,"include_answer":False,"include_domains":["brawltrack.app"]},timeout=20); r.raise_for_status(); return r.json()

_original_init=community_features.CommunityFeatures.__init__; _original_handle=community_features.CommunityFeatures.handle_command

def _patched_init(self,supabase_url,supabase_key,player_fetcher,history_fetcher,change_calculator,number_formatter,change_formatter,snapshot_saver=None):
    legacy=player_fetcher
    def official_first(tag):
        official=fetch_official_player(tag); secondary=fetch_brawltrack_player(tag)
        if secondary is None or any(secondary.get(k) is None for k in ("prestige","ranked_current","ranked_career_peak")):
            try:
                old=legacy(tag)
                if old:
                    if secondary: tmp=dict(old); tmp.update({k:v for k,v in secondary.items() if v not in (None,"",[],{})}); secondary=tmp
                    else: secondary=old
            except Exception as e:print("ERRORE FALLBACK PLAYER:",repr(e),flush=True)
        return _merge_priority(official,secondary)
    def fixed_changes(history,current):return _same_day_changes(change_calculator,history,current)
    _original_init(self,supabase_url,supabase_key,official_first,history_fetcher,fixed_changes,number_formatter,change_formatter,snapshot_saver)

def _abusivo_source_guard(text):
    """Hide backend/source names in normal user-facing replies."""
    s=str(text or "")
    s=re.sub(r"(?i)\\bsecondo\\s+(?:BrawlTrack|Supercell(?: Official API)?|Brawl Planet)\\b[:,]?\\s*","I nostri Sistemi Abusivi indicano: ",s)
    s=re.sub(r"(?i)\\b(?:fonte|source)\\s*:\\s*(?:BrawlTrack|Supercell(?: Official API)?|Brawl Planet)\\s*","",s)
    return s.strip()

def _profile_match(q):return re.fullmatch(r"(?:stats|statistiche|profilo|scheda|status(?:\s+(?:del\s+)?giocatore)?|stato(?:\s+(?:del\s+)?giocatore)?)\s*(?:di\s+)?#?([0289PYLQGRJCUV]{3,15})",q,re.I)

async def _patched_handle(self,message,context,question):
    q=re.sub(r"^[!/]+","",(question or "").strip()).strip().strip("\"'“”‘’ ").strip(); pm=_profile_match(q)
    if pm:
        player=self.player_fetcher(pm.group(1))
        if not player: await message.reply_text("Non riesco a trovare questo giocatore. Controlla che il tag sia corretto e riprova."); return True
        history=self.history_fetcher(player["tag"],days=91); changes=self.change_calculator(history,player["trophies"]); member=self.get_member_by_player_tag(message.chat_id,player["tag"]) or {}
        rc=player.get("ranked_current") or member.get("ranked_current") or "Non disponibile"; rs=player.get("ranked_season_peak") or member.get("ranked_season_peak") or "Non disponibile"; rp=player.get("ranked_career_peak") or player.get("ranked_peak") or member.get("ranked_peak") or "Non disponibile"; nf=self.number_formatter; cf=self.change_formatter
        lines=[player["name"],f"Tag: {player['tag']}",f"Club: {player.get('club_name') or 'Senza club'}",f"Tag club: {player.get('club_tag') or 'Non disponibile'}","",f"Trofei: {nf(player.get('trophies'))}",f"Brawler: {nf(player.get('brawlers'))}",f"Livello: {nf(player.get('level'))}",f"Prestigio: {nf(player.get('prestige'))}",f"Ranked attuale: {rc}",f"Record stagione: {rs}",f"Record massimo: {rp}","","Vittorie:",f"- 3v3: {nf(player.get('wins_3v3'))}",f"- Solo: {nf(player.get('wins_solo'))}",f"- Duo: {nf(player.get('wins_duo'))}","","Andamento trofei:",f"- Oggi: {cf(changes.get('today'))}",f"- 7 giorni: {cf(changes.get('7d'))}",f"- 15 giorni: {cf(changes.get('15d'))}",f"- 30 giorni: {cf(changes.get('30d'))}",f"- 90 giorni: {cf(changes.get('90d'))}"]
        await message.reply_text("\n".join(lines)); return True
    if _is_direct_meta(q):
        try:
            from meta_current import render_current_meta
            await context.bot.send_chat_action(chat_id=message.chat_id,action="typing"); report=render_current_meta(_search_brawltrack_meta(),"both"); await message.reply_text(("I nostri Sistemi Abusivi hanno analizzato il meta attuale.\n\n"+report) if report else "I nostri Sistemi Abusivi non hanno trovato abbastanza dati verificabili in questo momento. Riprova tra poco.")
        except Exception as e: print("META DIRECT ERRORE:",repr(e),flush=True); await message.reply_text("I nostri Sistemi Abusivi non riescono a completare l’analisi del meta in questo momento. Riprova tra poco.")
        return True
    handled=await _original_handle(self,message,context,question)
    return handled

community_features.CommunityFeatures.__init__=_patched_init
community_features.CommunityFeatures.handle_command=_patched_handle
print("SUPERCELL OFFICIAL PRIMARY GUARD INSTALLATA",flush=True)

def _sync_catalog_runtime():
    try:
        time.sleep(5)
        from brawler_catalog_sync import sync_official_brawlers
        count=sync_official_brawlers(timeout=30)
        print("CATALOGO SUPERCELL STARTUP OK:",count,flush=True)
    except Exception as exc:
        print("CATALOGO SUPERCELL STARTUP ERRORE:",repr(exc),flush=True)
threading.Thread(target=_sync_catalog_runtime,daemon=True).start()

# One-shot structured skin master inspection at startup.
def _inspect_skin_master_later():
    import time
    time.sleep(35)
    try:
        from skin_master_sync import sync_verified_rows, inspect_unmapped_relations
        info=sync_verified_rows()
        print("SKIN MASTER SYNC:",info,flush=True)
        unresolved=inspect_unmapped_relations()
        print("SKIN UNMAPPED RELATIONS:",unresolved,flush=True)
    except Exception as exc:
        print("SKIN MASTER INSPECT ERROR:",repr(exc),flush=True)

try:
    threading.Thread(target=_inspect_skin_master_later,daemon=True).start()
except Exception as exc:
    print("SKIN MASTER THREAD ERROR:",repr(exc),flush=True)
