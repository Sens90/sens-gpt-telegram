"""Sens GPT runtime guards for Brawl Stars meta answers and live profiles.

Imported explicitly by app.py after third-party dependencies are available.
"""
import html
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

ROME = ZoneInfo("Europe/Rome")

META_POLICY = r'''
REGOLE RUNTIME OBBLIGATORIE PER IL META:
- Buffie e buff di bilanciamento sono concetti diversi. Non descrivere mai un Buffie come buff, nerf o rework.
- Attribuisci buff, nerf o rework a un Brawler solo se una fonte ufficiale Supercell/Brawl Stars lo conferma esplicitamente per quel singolo Brawler.
- BrawlTrack e la fonte primaria per meta, mappe, statistiche e composizioni. Brawl Planet e solo fallback.
- Non dichiarare un Brawler top o tier S usando soltanto il tasso di vittoria.
- Se BrawlTrack mostra TBD o non pubblica una metrica, non inventarla.
- Ladder e Classificata non devono mai essere fuse.
'''


def _install_gemini_guard():
    try:
        from google.genai import models as genai_models
    except Exception:
        return
    original = getattr(genai_models.Models, "generate_content", None)
    if original is None or getattr(original, "_sens_meta_policy", False):
        return
    def guarded(self, *args, **kwargs):
        contents = kwargs.get("contents")
        if isinstance(contents, str) and "Brawl Stars" in contents:
            kwargs["contents"] = contents + META_POLICY
        return original(self, *args, **kwargs)
    guarded._sens_meta_policy = True
    genai_models.Models.generate_content = guarded


def _is_direct_meta(question):
    q = re.sub(r"\s+", " ", str(question or "").strip().casefold())
    return bool(re.fullmatch(r"(?:il\s+)?meta(?:\s+attuale|\s+di\s+adesso|\s+ora)?[?!.]*", q))


def _search_brawltrack_meta():
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return None
    response = requests.post(
        "https://api.tavily.com/search",
        json={"api_key": key,"query": "Brawl Stars current meta BrawlTrack brawlers win rate usage star rate site:brawltrack.app/brawlers","search_depth": "advanced","max_results": 12,"include_raw_content": True,"include_answer": False,"include_domains": ["brawltrack.app"]},timeout=20)
    response.raise_for_status()
    data = response.json()
    urls = [r.get("url") for r in data.get("results", []) if r.get("url") and "/brawlers/" in r.get("url", "")][:12]
    if urls:
        try:
            ext = requests.post("https://api.tavily.com/extract",json={"api_key": key, "urls": urls, "extract_depth": "advanced"},timeout=30)
            ext.raise_for_status(); extracted = ext.json().get("results", [])
            if extracted: data["results"] = extracted + data.get("results", [])
        except Exception as exc: print("META BRAWLTRACK extract fallback:", repr(exc), flush=True)
    return data


def _install_command_guard():
    try:
        import community_features
        from meta_current import render_current_meta
    except Exception as exc:
        print("META GUARD import failure:", repr(exc), flush=True); return
    original = community_features.CommunityFeatures.handle_command
    if getattr(original, "_sens_direct_meta", False): return
    async def guarded(self, message, context, question):
        if _is_direct_meta(question):
            try:
                await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
                data = _search_brawltrack_meta(); report = render_current_meta(data or {}, "both")
                if report:
                    print("META ATTUALE: risposta deterministica BrawlTrack", flush=True); await message.reply_text(report)
                else:
                    print("META ATTUALE: dati BrawlTrack insufficienti", flush=True); await message.reply_text("META ATTUALE\n\nBrawlTrack non mi ha restituito abbastanza statistiche verificabili in questo momento. Non genero una tier list generica o percentuali non verificabili. Riprova tra poco.")
                return True
            except Exception as exc:
                print("META ATTUALE guard failure:", repr(exc), flush=True); await message.reply_text("META ATTUALE\n\nNon riesco a verificare i dati BrawlTrack in questo momento. Per evitare dati inventati non genero una tier list generica."); return True
        return await original(self, message, context, question)
    guarded._sens_direct_meta = True; community_features.CommunityFeatures.handle_command = guarded


def _install_live_club_guard():
    try: import player_tracking
    except Exception as exc: print("PROFILE CLUB GUARD import failure:", repr(exc), flush=True); return
    def live_club(player_tag, timeout=15):
        tag = str(player_tag or "").upper().replace("#", "").strip()
        if not re.fullmatch(r"[0289PYLQGRJCUV]{3,15}", tag): return None, None
        try:
            url=f"https://brawlify.com/it/player/{tag}?refresh={int(time.time())}"
            response=requests.get(url,headers={"User-Agent":"Mozilla/5.0 (SensGPT-TitaniAbusivi/1.0)","Cache-Control":"no-cache","Pragma":"no-cache","Accept-Language":"it-IT,it;q=0.9,en;q=0.8"},timeout=timeout)
            if response.status_code != 200: return None,None
            page=html.unescape(response.text); href=re.search(r'href=["\']/(?:it/)?club/(?:%23|#)?([0289PYLQGRJCUV]{3,15})(?:[^"\']*)["\']',page,re.I)
            if not href:return None,None
            club_tag="#"+href.group(1).upper(); anchor=re.search(r'<a[^>]+href=["\']/(?:it/)?club/(?:%23|#)?'+re.escape(href.group(1))+r'[^"\']*["\'][^>]*>(.*?)</a>',page,re.I|re.S)
            if anchor:
                name=re.sub(r"<[^>]+>"," ",anchor.group(1)); name=re.sub(r"\s+"," ",html.unescape(name)).strip()
                if name and name.casefold() not in {"club","visualizza club","view club"}: return name,club_tag
            return None,club_tag
        except Exception as exc: print("PROFILE CLUB LIVE failure:",repr(exc),flush=True); return None,None
    player_tracking.get_live_club=live_club; print("PROFILE CLUB LIVE GUARD INSTALLATA",flush=True)


def _rome_trophy_changes(history,current_trophies):
    now_utc=datetime.now(timezone.utc); now_rome=now_utc.astimezone(ROME); start_today=now_rome.replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc); parsed=[]
    for row in history or []:
        try:
            dt=datetime.fromisoformat(str(row["recorded_at"]).replace("Z","+00:00")); dt=dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc); parsed.append((dt.astimezone(timezone.utc),int(row["trophies"])))
        except Exception: continue
    parsed.sort(key=lambda x:x[0])
    def at_or_before(target):
        value=None
        for dt,trophies in parsed:
            if dt<=target:value=trophies
            else:break
        return value
    today=at_or_before(start_today)
    if today is None:
        for dt,trophies in parsed:
            if start_today<=dt<=now_utc:today=trophies;break
    changes={"today":current_trophies-today if today is not None else None}
    for days,key in ((7,"7d"),(15,"15d"),(30,"30d"),(90,"90d")):
        old=at_or_before(now_utc-timedelta(days=days)); changes[key]=current_trophies-old if old is not None else None
    return changes


def _patch_main_profile_runtime():
    for _ in range(2400):
        main=sys.modules.get("__main__")
        if main and hasattr(main,"calculate_trophy_changes") and hasattr(main,"community"):
            main.calculate_trophy_changes=_rome_trophy_changes
            try: main.community.change_calculator=_rome_trophy_changes
            except Exception: pass
            print("PROFILE TROPHY ROME GUARD INSTALLATA",flush=True); return
        time.sleep(0.25)
    print("PROFILE TROPHY ROME GUARD NON INSTALLATA",flush=True)


def _sync_official_catalog_startup():
    """Populate/update the official Brawler catalog after every service start."""
    try:
        time.sleep(5)
        from brawler_catalog_sync import sync_official_brawlers_startup
        count=sync_official_brawlers_startup(timeout=30)
        print("CATALOGO SUPERCELL STARTUP OK:",count,flush=True)
    except Exception as exc:
        print("CATALOGO SUPERCELL STARTUP ERRORE:",repr(exc),flush=True)

_install_gemini_guard()
_install_command_guard()
_install_live_club_guard()
threading.Thread(target=_patch_main_profile_runtime,daemon=True).start()
threading.Thread(target=_sync_official_catalog_startup,daemon=True).start()
