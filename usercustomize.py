"""Sens GPT runtime guards for Brawl Stars meta answers."""
import os
import re
import requests

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
        json={
            "api_key": key,
            "query": "Brawl Stars current meta BrawlTrack brawlers win rate usage star rate site:brawltrack.app/brawlers",
            "search_depth": "advanced",
            "max_results": 12,
            "include_raw_content": True,
            "include_answer": False,
            "include_domains": ["brawltrack.app"],
        },
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    urls = [r.get("url") for r in data.get("results", []) if r.get("url") and "/brawlers/" in r.get("url", "")][:12]
    if urls:
        try:
            ext = requests.post(
                "https://api.tavily.com/extract",
                json={"api_key": key, "urls": urls, "extract_depth": "advanced"},
                timeout=30,
            )
            ext.raise_for_status()
            extracted = ext.json().get("results", [])
            if extracted:
                data["results"] = extracted + data.get("results", [])
        except Exception as exc:
            print("META BRAWLTRACK extract fallback:", repr(exc), flush=True)
    return data


def _install_command_guard():
    try:
        import community_features
        from meta_current import render_current_meta
    except Exception as exc:
        print("META GUARD import failure:", repr(exc), flush=True)
        return
    original = community_features.CommunityFeatures.handle_command
    if getattr(original, "_sens_direct_meta", False):
        return

    async def guarded(self, message, context, question):
        if _is_direct_meta(question):
            try:
                await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
                data = _search_brawltrack_meta()
                report = render_current_meta(data or {}, "both")
                if report:
                    print("META ATTUALE: risposta deterministica BrawlTrack", flush=True)
                    await message.reply_text(report)
                else:
                    print("META ATTUALE: dati BrawlTrack insufficienti", flush=True)
                    await message.reply_text(
                        "META ATTUALE\n\nBrawlTrack non mi ha restituito abbastanza statistiche verificabili in questo momento. "
                        "Non genero una tier list generica o percentuali non verificabili. Riprova tra poco."
                    )
                return True
            except Exception as exc:
                print("META ATTUALE guard failure:", repr(exc), flush=True)
                await message.reply_text(
                    "META ATTUALE\n\nNon riesco a verificare i dati BrawlTrack in questo momento. "
                    "Per evitare dati inventati non genero una tier list generica."
                )
                return True
        return await original(self, message, context, question)

    guarded._sens_direct_meta = True
    community_features.CommunityFeatures.handle_command = guarded


_install_gemini_guard()
_install_command_guard()
