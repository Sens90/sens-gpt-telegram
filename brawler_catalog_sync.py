import os
import re
import threading
from datetime import datetime, timezone
import requests

_STARTUP_SYNC_LOCK = threading.Lock()
_STARTUP_SYNC_COUNT = None

def _headers():
    key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key: raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY non configurata")
    return {"apikey":key,"Authorization":f"Bearer {key}","Content-Type":"application/json","Prefer":"resolution=merge-duplicates,return=minimal"}

def _proxy_brawlers(timeout=30):
    url=os.environ.get("BRAWL_OFFICIAL_PROXY_URL"); key=os.environ.get("BRAWL_OFFICIAL_PROXY_KEY")
    if not url or not key: raise RuntimeError("Proxy ufficiale Supercell non configurato")
    r=requests.get(url,params={"action":"brawlers"},headers={"X-Sens-Key":key,"Accept":"application/json","User-Agent":"SensGPT-TitaniAbusivi/1.0"},timeout=timeout); r.raise_for_status(); d=r.json() or {}; return d.get("items") or d.get("list") or []

def _normalize_player_name(v): return re.sub(r"[^a-z0-9]+","",str(v or "").casefold())
def _extract_trophy_player_query(q):
    for p in (r"quante\s+(?:coppe|trofei)\s+(?:ha|possiede)\s+(.+?)[?!.]*$",r"(?:coppe|trofei)\s+(?:di\s+)?(.+?)[?!.]*$",r"(?:quante\s+)?(?:coppe|trofei)\s+(?:ha\s+)?(?:il\s+giocatore\s+)?(.+?)[?!.]*$"):
        m=re.fullmatch(p,q,re.I)
        if m:return m.group(1).strip().strip("\"'“”‘’ ")
    return None
def _extract_daily_trophy_query(q):
    for p in (r"quant[ei]\s+(?:coppe|trofei)\s+(?:ha\s+)?(?:fatto|fatti|guadagnato|guadagnati|preso|presi)\s+(?:oggi\s+)?(.+?)(?:\s+oggi)?[?!.]*$",r"(?:oggi\s+)?quant[ei]\s+(?:coppe|trofei)\s+(?:ha\s+)?(?:fatto|fatti|guadagnato|guadagnati|preso|presi)\s+(.+?)[?!.]*$",r"(?:coppe|trofei)\s+(?:fatti|guadagnati|presi)\s+(?:oggi\s+)?(?:da\s+)?(.+?)(?:\s+oggi)?[?!.]*$",r"(?:andamento|variazione)\s+(?:di\s+)?oggi\s+(?:di\s+)?(.+?)[?!.]*$"):
        m=re.fullmatch(p,q,re.I)
        if m:return m.group(1).strip().strip("\"'“”‘’ ")
    return None
def _registered_player_matches(self,chat_id,name):
    needle=_normalize_player_name(name); out=[]
    if not needle:return []
    for m in self.members(chat_id):
        vals=[_normalize_player_name(v) for v in (m.get("player_name"),m.get("display_name"),str(m.get("telegram_username") or "").lstrip("@"),str(m.get("player_tag") or "").replace("#","")) if v]
        score=2 if needle in vals else (1 if any(needle in v or v in needle for v in vals) else 0)
        if score:out.append((score,m))
    if not out:return []
    best=max(x[0] for x in out); return [m for s,m in out if s==best]

def install_club_ranking_router():
    import community_features
    current=community_features.CommunityFeatures.handle_command
    if getattr(current,"_sens_club_ranking_router",False):return
    aliases={"titani":"TITANI ABUSIVI","titani abusivi":"TITANI ABUSIVI","tamarri":"TAMARRI ABUSIVI","tamarri abusivi":"TAMARRI ABUSIVI","tornadi":"TORNADI ABUSIVI","tornadi abusivi":"TORNADI ABUSIVI","talenti":"TALENTI ABUSIVI","talenti abusivi":"TALENTI ABUSIVI"}
    async def resolve(self,msg,name):
        ms=_registered_player_matches(self,msg.chat_id,name)
        if not ms:await msg.reply_text(f"Non trovo un giocatore registrato con il nome {name}.");return None
        if len(ms)>1:
            rows=[]
            for m in ms[:8]:
                n=m.get("player_name") or m.get("display_name") or m.get("player_tag") or "Sconosciuto"; t=str(m.get("player_tag") or "").upper().replace("#",""); rows.append(f"- {n}"+(f" (#{t})" if t else ""))
            await msg.reply_text("Ho trovato piu giocatori compatibili. Quale intendi?\n"+"\n".join(rows));return None
        return ms[0]
    async def routed(self,message,context,question):
        q=re.sub(r"^[!/]+","",str(question or "").strip()).strip().strip("\"'“”‘’ ").strip(); m=re.fullmatch(r"classific(?:a|he)\s+(titani(?: abusivi)?|tamarri(?: abusivi)?|tornadi(?: abusivi)?|talenti(?: abusivi)?)",q,re.I)
        if m:club=aliases[m.group(1).casefold()];await message.reply_text(self.stat_ranking_text(message.chat_id,"trofei",club));return True
        name=_extract_daily_trophy_query(q)
        if name:
            member=await resolve(self,message,name)
            if member is None:return True
            tag=str(member.get("player_tag") or "").upper().replace("#",""); p=self.player_fetcher(tag) if tag else None; n=(p or {}).get("name") or member.get("player_name") or name; cur=(p or {}).get("trophies")
            if cur is None:cur=self._member_current_trophies(member)
            if cur is None:await message.reply_text(f"{n} e registrato, ma al momento non riesco a recuperare i suoi trofei.");return True
            ch=self.change_calculator(self.history_fetcher(tag,days=10),int(cur)); delta=ch.get("today")
            if delta is None:await message.reply_text(f"{n} e registrato, ma non ho ancora abbastanza storico di oggi per calcolare la variazione dei trofei.");return True
            await message.reply_text(f"Oggi {n} ha fatto {'+' if int(delta)>0 else ''}{self.number_formatter(int(delta))} trofei. Attualmente ne ha {self.number_formatter(int(cur))}.");return True
        name=_extract_trophy_player_query(q)
        if name:
            member=await resolve(self,message,name)
            if member is None:return True
            tag=str(member.get("player_tag") or "").upper().replace("#",""); p=self.player_fetcher(tag) if tag else None; n=(p or {}).get("name") or member.get("player_name") or name; t=(p or {}).get("trophies")
            if t is None:t=self._member_current_trophies(member)
            if t is None:await message.reply_text(f"{n} e registrato, ma al momento non riesco a recuperare i suoi trofei.");return True
            await message.reply_text(f"{n} ha {self.number_formatter(int(t))} trofei.");return True
        return await current(self,message,context,question)
    routed._sens_club_ranking_router=True;community_features.CommunityFeatures.handle_command=routed;print("CLASSIFICA CLUB + TROFEI GIOCATORE ROUTER INSTALLATO",flush=True)

def _upsert(url,table,conflict,rows,timeout):
    if not rows:return
    r=requests.post(f"{url}/rest/v1/{table}?on_conflict={conflict}",headers=_headers(),json=rows,timeout=timeout);r.raise_for_status()

def sync_official_brawlers(timeout=30):
    url=(os.environ.get("SUPABASE_URL") or "").rstrip("/")
    if not url:raise RuntimeError("SUPABASE_URL non configurata")
    install_club_ranking_router();items=_proxy_brawlers(timeout);now=datetime.now(timezone.utc).isoformat();brawlers=[];gadgets=[];stars=[];gears_by_id={};hypers=[]
    for item in items:
        bid=item.get("id");name=item.get("name")
        if bid is None or not name:continue
        bid=int(bid);brawlers.append({"brawler_id":bid,"name_en":str(name),"source":"supercell_official","source_payload":item,"source_updated_at":now,"updated_at":now})
        for x in item.get("gadgets") or []:
            if x.get("id") is not None and x.get("name"):gadgets.append({"gadget_id":int(x["id"]),"brawler_id":bid,"name_en":str(x["name"]),"source":"supercell_official","source_payload":x,"updated_at":now})
        for x in item.get("starPowers") or []:
            if x.get("id") is not None and x.get("name"):stars.append({"star_power_id":int(x["id"]),"brawler_id":bid,"name_en":str(x["name"]),"source":"supercell_official","source_payload":x,"updated_at":now})
        for x in item.get("gears") or []:
            if x.get("id") is not None and x.get("name"):
                gid=int(x["id"]);gears_by_id[gid]={"gear_id":gid,"name_en":str(x["name"]),"source":"supercell_official","source_payload":x,"updated_at":now}
        for x in item.get("hyperCharges") or []:
            if x.get("id") is not None and x.get("name"):hypers.append({"hypercharge_id":int(x["id"]),"brawler_id":bid,"name_en":str(x["name"]),"source":"supercell_official","source_payload":x,"updated_at":now})
    _upsert(url,"brawlers_catalog","brawler_id",brawlers,timeout);_upsert(url,"gadgets_catalog","gadget_id",gadgets,timeout);_upsert(url,"star_powers_catalog","star_power_id",stars,timeout);_upsert(url,"gears_catalog","gear_id",list(gears_by_id.values()),timeout);_upsert(url,"hypercharges_catalog","hypercharge_id",hypers,timeout)
    details={"via":"brawl-proxy","official":True,"brawlers":len(brawlers),"gadgets":len(gadgets),"star_powers":len(stars),"gears":len(gears_by_id),"hypercharges":len(hypers)}
    state={"dataset":"brawlers","source":"supercell_official","last_success_at":now,"last_attempt_at":now,"last_status":"ok","records_seen":len(brawlers),"details":details,"updated_at":now};_upsert(url,"content_sync_state","dataset",[state],timeout)
    print("CATALOGO SUPERCELL COMPLETO:",details,flush=True);return len(brawlers)

def sync_official_brawlers_startup(timeout=30):
    """Serialize duplicate startup hooks and cache only a successful result."""
    global _STARTUP_SYNC_COUNT
    with _STARTUP_SYNC_LOCK:
        if _STARTUP_SYNC_COUNT is not None:
            return _STARTUP_SYNC_COUNT
        count = sync_official_brawlers(timeout=timeout)
        _STARTUP_SYNC_COUNT = count
        return count

if __name__=="__main__":sync_official_brawlers()
