import json
import io
import asyncio
import html
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

import os
import requests
import re
import threading
from urllib.parse import urlsplit, urlunsplit, quote

from flask import Flask
from google import genai
from telegram import Update
from telegram.error import TelegramError, TimedOut, NetworkError, RetryAfter, BadRequest
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
from community_features import CommunityFeatures
from profile_card_generator import build_profile_card
from ai_profile_experience import build_visual_prompt, choose_scene
from ai_profile_generator import generate_scene, overlay_stats, quota_status, consume_quota
from brawler_reference import resolve_ai_brawler_reference
from player_tracking import extract_brawlzone_ranked, get_brawltrack_player
from live_maps import collect_report, render_report, report_csv, brawltrack_pro_map_stats, safe_get, localized
from premium_ai import handle_premium_command


TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
ROME = ZoneInfo("Europe/Rome")

client = genai.Client(api_key=GEMINI_API_KEY)

FISH_AUDIO_API_KEY = os.environ.get("FISH_AUDIO_API_KEY")
VOICE_REFERENCE_BUCKET = "sens-private-voice"
VOICE_REFERENCE_OBJECT = "Voce 003.m4a"
VOICE_REFERENCE_TRANSCRIPT = (
    "Ciao a tutti, sono Sens, presidente dei TITANI ABUSIVI. Benvenuti nella nostra community. "
    "Qui si gioca, si scherza e soprattutto si pusha insieme. Sens GPT è pronto ad aiutarvi con "
    "Brawler, mappe, classificata, statistiche e strategie. Se avete bisogno di qualcosa, chiedete pure. "
    "E ricordate: TITANI ABUSIVI, la fiducia viene prima di tutto. Ora basta parlare, entriamo su "
    "Brawl Stars e andiamo a prenderci un po' di trofei!"
)
_voice_reference_cache = None


def _supabase_headers():
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY or "",
        "Authorization": "Bearer " + (SUPABASE_SERVICE_ROLE_KEY or ""),
        "Content-Type": "application/json",
    }


def get_voice_mode(chat_id, telegram_user_id):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return "text"
    try:
        r = requests.get(
            SUPABASE_URL + "/rest/v1/voice_preferences",
            headers=_supabase_headers(),
            params={
                "select": "mode",
                "chat_id": "eq." + str(chat_id),
                "telegram_user_id": "eq." + str(telegram_user_id),
                "limit": "1",
            },
            timeout=10,
        )
        r.raise_for_status()
        rows = r.json()
        return rows[0].get("mode", "text") if rows else "text"
    except Exception as exc:
        print("VOICE MODE GET ERROR:", repr(exc), flush=True)
        return "text"


def set_voice_mode(chat_id, telegram_user_id, mode):
    if mode not in {"text", "voice", "both"}:
        raise ValueError("invalid voice mode")
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Supabase unavailable")
    headers = _supabase_headers()
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    r = requests.post(
        SUPABASE_URL + "/rest/v1/voice_preferences",
        headers=headers,
        params={"on_conflict": "chat_id,telegram_user_id"},
        json={
            "chat_id": int(chat_id),
            "telegram_user_id": int(telegram_user_id),
            "mode": mode,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        timeout=10,
    )
    r.raise_for_status()


def get_private_voice_reference():
    global _voice_reference_cache
    if _voice_reference_cache:
        return _voice_reference_cache
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Supabase voice storage unavailable")
    path = quote(VOICE_REFERENCE_OBJECT, safe="")
    r = requests.get(
        SUPABASE_URL + "/storage/v1/object/authenticated/" + VOICE_REFERENCE_BUCKET + "/" + path,
        headers={
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": "Bearer " + SUPABASE_SERVICE_ROLE_KEY,
        },
        timeout=20,
    )
    r.raise_for_status()
    if not r.content:
        raise RuntimeError("empty voice reference")
    _voice_reference_cache = bytes(r.content)
    return _voice_reference_cache


def fish_tts(text):
    """Generate Telegram-ready Opus with zero-shot reference; free Fish model only."""
    if not FISH_AUDIO_API_KEY:
        raise RuntimeError("FISH_AUDIO_API_KEY missing")
    import ormsgpack
    reference_audio = get_private_voice_reference()
    payload = {
        "text": str(text)[:4000],
        "references": [{
            "audio": reference_audio,
            "text": VOICE_REFERENCE_TRANSCRIPT,
        }],
        "prosody": {
            "speed": 0.95,
            "volume": 0,
            "normalize_loudness": True,
        },
        "temperature": 0.7,
        "top_p": 0.7,
        "format": "opus",
        "sample_rate": 48000,
        "opus_bitrate": 32000,
        "latency": "balanced",
        "normalize": True,
    }
    r = requests.post(
        "https://api.fish.audio/v1/tts",
        data=ormsgpack.packb(payload),
        headers={
            "Authorization": "Bearer " + FISH_AUDIO_API_KEY,
            "Content-Type": "application/msgpack",
            "model": "s2.1-pro-free",
        },
        timeout=90,
    )
    r.raise_for_status()
    if not r.content:
        raise RuntimeError("Fish Audio returned empty audio")
    return bytes(r.content)


async def send_voice_reply(context, chat_id, text):
    try:
        audio = await asyncio.to_thread(fish_tts, text)
        stream = io.BytesIO(audio)
        stream.name = "sens_gpt.opus"
        await context.bot.send_voice(chat_id=chat_id, voice=stream)
        return True
    except Exception as exc:
        print("VOICE TTS ERROR:", repr(exc), flush=True)
        return False


def request_voice_mode(text):
    """Output mode is explicit per request. Default: text."""
    raw = re.sub(r"\s+", " ", (text or "").strip().casefold())
    if re.search(r"\brispondi\s+(?:testo\s*(?:\+|e)?\s*voce|voce\s*(?:\+|e)\s*testo)\s*$", raw):
        return "both"
    if re.search(r"\brispondi\s+a\s+voce\s*$", raw):
        return "voice"
    if re.search(r"\brispondi\s+(?:a\s+)?testo\s*$", raw):
        return "text"
    return "text"


async def send_mode_aware_text(message, context, text, disable_web_page_preview=True):
    # Fundamental rule: every request starts in text mode unless that request
    # explicitly asks for "voce" or "voce + testo". No persistent mode leaks
    # into later requests.
    mode = context.user_data.get("_request_voice_mode") or request_voice_mode(message.text)
    if mode in ("text", "both"):
        await context.bot.send_message(
            chat_id=message.chat_id,
            text=text,
            disable_web_page_preview=disable_web_page_preview,
        )
    if mode in ("voice", "both"):
        ok = await send_voice_reply(context, message.chat_id, text)
        if not ok and mode == "voice":
            await context.bot.send_message(
                chat_id=message.chat_id,
                text="La risposta vocale non è disponibile in questo momento. Riprova tra poco.",
            )


async def voice_mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.from_user:
        return
    raw = " ".join(context.args or []).strip().casefold()
    aliases = {
        "testo": "text", "text": "text",
        "voce": "voice", "vocale": "voice", "voice": "voice",
        "entrambi": "both", "testo+voce": "both", "testo voce": "both", "both": "both",
    }
    if not raw:
        current = await asyncio.to_thread(
            get_voice_mode, message.chat_id, message.from_user.id
        )
        label = {"text": "solo testo", "voice": "solo voce", "both": "testo + voce"}[current]
        await message.reply_text(
            "Modalità risposta attuale: " + label +
            ".\nUsa /voce testo, /voce voce oppure /voce entrambi."
        )
        return
    mode = aliases.get(raw)
    if not mode:
        await message.reply_text("Usa /voce testo, /voce voce oppure /voce entrambi.")
        return
    try:
        await asyncio.to_thread(set_voice_mode, message.chat_id, message.from_user.id, mode)
        label = {"text": "solo testo", "voice": "solo voce", "both": "testo + voce"}[mode]
        await message.reply_text("Modalità risposta impostata su: " + label + ".")
    except Exception as exc:
        print("VOICE MODE SET ERROR:", repr(exc), flush=True)
        await message.reply_text("Non riesco a salvare la modalità voce in questo momento.")


app = Flask(__name__)


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


@app.route("/")
def home():
    return "Sens GPT - TITANI ABUSIVI ONLINE"


def needs_web_search(question):
    keywords = [
        "oggi", "ieri", "domani", "attuale", "attualmente",
        "adesso", "ora", "ultimo", "ultimi", "ultima",
        "nuovo", "nuova", "novità", "novita",
        "aggiornamento", "aggiornamenti", "patch",
        "buff", "nerf", "bilanciamento", "meta", "push", "pushare", "pushare adesso", "cosa giocare", "giocare adesso", "cosa devo giocare",
        "tier list", "tierlist", "miglior brawler", "migliori brawler",
        "ranked", "competitivo", "pick rate", "win rate",
        "stagione", "evento", "eventi",
        "classifica", "classifiche",
        "quando esce", "uscito", "uscita", "rilascio",
        "prezzo", "quanto costa",
        "brawler", "brawlers", "rotazione", "rotazione attuale", "mappa attuale", "mappe attuali", "mappa corrente", "mappe correnti", "mappa di oggi", "mappe di oggi", "miglior comp", "migliore comp", "composizione", "composizione migliore",
        "modalità", "modalita",
        "gadget", "ingranaggio", "star power",
        "ipercarica", "overdrive",
        "shade", "leon", "mortis"
    ]

    question_lower = question.lower()

    return any(keyword in question_lower for keyword in keywords)


def is_current_meta_query(question):
    question_lower = question.lower()

    meta_keywords = [
        "meta", "tier list", "tierlist", "push", "pushare", "cosa pushare", "cosa giocare", "giocare adesso", "cosa devo giocare",
        "miglior brawler", "migliori brawler",
        "ranked", "competitivo",
        "pick rate", "win rate",
        "buff", "nerf", "bilanciamento"
    ]

    return any(keyword in question_lower for keyword in meta_keywords)


def get_game_context(question):
    q = (question or "").lower()

    ranked_terms = [
        "classificata", "classificate", "ranked", "draft", "ban",
        "lega", "leghe", "power league"
    ]
    ladder_terms = [
        "ladder", "scalata", "scalare", "push", "pushare",
        "trofei", "trofeo", "coppe", "coppa", "scala trofei"
    ]

    if any(term in q for term in ranked_terms):
        return "ranked"

    if any(term in q for term in ladder_terms):
        return "ladder"

    return "both"


def is_exhaustive_current_maps_query(question):
    """True for requests covering today's maps across several/all modes."""
    q = (question or "").lower()
    current_terms = ["oggi", "attual", "adesso", "ora", "rotazione", "corrent"]
    scope_terms = [
        "ogni mappa", "tutte le mappe", "per ogni mappa",
        "ogni modalità", "ogni modalita", "tutte le modalità",
        "tutte le modalita"
    ]
    return any(term in q for term in current_terms) and any(
        term in q for term in scope_terms
    )


def is_all_maps_request(question):
    q = (question or "").casefold()
    return is_exhaustive_current_maps_query(question) or any(
        phrase in q for phrase in (
            "ogni brawler consigliato per ogni mappa",
            "ogni brawler per ogni mappa",
            "tutte le mappe di ogni modalità",
            "tutte le mappe di ogni modalita",
            "mappe di oggi"
        )
    )


def invalid_exhaustive_map_answer(
    text,
    rotation_manifest=None,
    expected_context="both"
):
    """Reject common hallucinations in large, current map recommendations."""
    if not text:
        return True

    lowered = text.casefold()
    invented_mode_names = [
        "fotoria", "acuffobia", "zona telone"
    ]
    if any(name in lowered for name in invented_mode_names):
        return True

    refusal_markers = [
        "rotazione completa", "non è interamente verificabile",
        "non e interamente verificabile", "scegliere una singola modalità",
        "scegli una singola modalità"
    ]
    if any(marker in lowered for marker in refusal_markers):
        return True

    if rotation_manifest is not None and len(rotation_manifest) < 2:
        return True

    if rotation_manifest:
        if "individual" not in lowered or "squadre" not in lowered:
            return True
        if any(token not in lowered for token in ("vitt", "scelta", "stella")):
            return True
        if expected_context == "both" and (
            ("ladder" not in lowered and "scalata" not in lowered) or "classificat" not in lowered
        ):
            return True
        # The request is exhaustive: a response mentioning only one map is
        # incomplete even when the one map's statistics are correct.
        expected_maps = {
            entry.get("map", "").casefold() for entry in rotation_manifest
            if entry.get("map")
        }
        mentioned = sum(1 for map_name in expected_maps if map_name in lowered)
        if mentioned < len(expected_maps):
            return True

    # A genuinely map-specific answer should not recycle one identical trio
    # across three or more maps. Order is ignored when comparing trios.
    trios = []
    for line in text.splitlines():
        if ":" not in line:
            continue
        value = line.split(":", 1)[1]
        names = [part.strip().casefold() for part in value.split(",")]
        if len(names) == 3 and all(re.fullmatch(r"[\w .’'-]+", name) for name in names):
            trios.append(tuple(sorted(names)))

        if "squadra" in line.casefold():
            members = [part.strip().casefold() for part in re.split(r"[,·|]", value)]
            if len(members) == 3 and len(set(members)) < 3:
                return True

    return any(trios.count(trio) >= 3 for trio in set(trios))


def meta_source_priority(url):
    url_lower = (url or "").lower()

    # BrawlTrack is the primary statistics source for meta/maps/comps.
    # Official Supercell pages remain authoritative for game announcements only.
    if "brawltrack.app" in url_lower:
        return 0
    if "supercell.com" in url_lower or "brawlstars.com" in url_lower:
        return 1
    if "brawlplanet.com" in url_lower or "brawlplanet.nl" in url_lower:
        return 2
    if "brawlify.com" in url_lower or "brawltime.ninja" in url_lower:
        return 3
    if "noff.gg" in url_lower:
        return 4
    return 5


def italian_planet_url(url):
    parts = urlsplit(url or "")
    if parts.hostname not in {"brawlplanet.com", "www.brawlplanet.com"}:
        return None
    path = parts.path
    path = re.sub(r"^/(?:en|it|pt|es|de|fr|nl|fi|id|ms|tr|pl)(?=/|$)", "", path)
    return urlunsplit(("https", "www.brawlplanet.com", "/it" + (path or "/"), parts.query, ""))


def telegram_text_chunks(text, limit=3500):
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit + 1)
        if cut <= 0:
            cut = limit
        yield text[:cut]
        text = text[cut:].lstrip("\n")
    if text:
        yield text


def compact_source_text(text, limit):
    """Keep prompts bounded while preserving the start and end of a page.

    Brawl Planet puts the individual table near the top of a map page and the
    team table further down. Keeping both ends retains the two sections without
    sending the entire HTML/markdown page to Gemini.
    """
    text = str(text or "")
    if len(text) <= limit:
        return text

    marker = "\n...[contenuto ridotto per il limite AI]...\n"
    if limit <= len(marker):
        return text[:limit]
    remaining = limit - len(marker)
    head = max(1, int(remaining * 0.62))
    tail = max(1, remaining - head)
    return (
        text[:head]
        + marker
        + text[-tail:]
    )


def build_web_context(results, exhaustive=False):
    """Build a bounded, source-labelled context for the generation call.

    A broad "all maps" request can return dozens of full pages. Passing all
    raw pages to Gemini exceeded the free-tier input-token quota, so primary
    Brawl Planet pages get priority and a smaller reserved slice is kept for
    secondary sources. URLs are deduplicated because Tavily returns the same
    page both from search and from extract.
    """
    # Leave room for the compact, parsed Brawl Planet tables that are added
    # separately to the prompt. This keeps the total Gemini input well below
    # the provider's free-tier limit even for the full daily rotation.
    total_budget = 150000 if exhaustive else 100000
    primary_budget = 125000 if exhaustive else 75000
    secondary_budget = 25000 if exhaustive else 25000
    context_parts = []
    sources = []
    seen_urls = set()
    primary_used = 0
    secondary_used = 0

    def rank(result):
        url = (result.get("url") or "").lower()
        if "brawltrack.app" in url and ("/maps/" in url or "/pro/maps/" in url):
            return 0
        if "/it/maps/" in url and "brawlplanet.com" in url:
            return 1
        if italian_planet_url(result.get("url", "")):
            return 2
        if result.get("source_role") == "secondary_fallback":
            return 4
        return 3

    ordered = sorted(
        results or [],
        key=rank if exhaustive else (lambda result: 0)
    )

    for result in ordered:
        url = result.get("url", "") or ""
        url_key = url.casefold()
        if url_key and url_key in seen_urls:
            continue
        if url_key:
            seen_urls.add(url_key)

        title = str(result.get("title", "") or "")
        content = str(result.get("content", "") or "")
        raw_content = str(result.get("raw_content", "") or "")
        if not (title or content or raw_content):
            continue

        is_secondary = result.get("source_role") == "secondary_fallback"
        is_map_page = "/it/maps/" in url.casefold() and "brawlplanet.com" in url.casefold()
        if is_map_page:
            raw_limit = 7000
        elif italian_planet_url(url):
            raw_limit = 9000
        elif is_secondary:
            raw_limit = 4500
        else:
            raw_limit = 5000

        if "brawltrack.app" in url.casefold():
            source_role = "FONTE PRIMARIA BRAWLTRACK"
        elif italian_planet_url(url):
            source_role = "FONTE FALLBACK BRAWL PLANET - usare solo se BrawlTrack non ha il dato"
        elif is_secondary:
            source_role = "FONTE SECONDARIA - usare solo se BrawlTrack e Brawl Planet non hanno il dato"
        else:
            source_role = "ALTRA FONTE"
        block = (
            f"\nRuolo fonte: {source_role}\n"
            f"Titolo: {compact_source_text(title, 1200)}\n"
            f"Contenuto: {compact_source_text(content, 2600)}\n"
            f"Contenuto completo: {compact_source_text(raw_content, raw_limit)}\n"
            f"Fonte: {url}\n"
            "---\n"
        )

        if is_secondary:
            if secondary_used >= secondary_budget:
                continue
            available = secondary_budget - secondary_used
        else:
            if primary_used >= primary_budget:
                continue
            available = primary_budget - primary_used

        if len(block) > available:
            # A final shortened block can still carry the title, labels and
            # the URL when the source budget is nearly exhausted.
            if available < 700:
                continue
            block = compact_source_text(block, available)

        context_parts.append(block)
        if is_secondary:
            secondary_used += len(block)
        else:
            primary_used += len(block)
        if primary_used + secondary_used >= total_budget:
            break
        if url:
            sources.append(url)

    return "".join(context_parts), sources


def brawlplanet_map_urls(results):
    """Collect localized Brawl Planet map detail URLs from extracted pages."""
    urls = []
    seen = set()
    pattern = re.compile(
        r"(?:https?://(?:www\.)?brawlplanet\.com)?/it/maps/"
        r"([a-z0-9][a-z0-9_-]+)", re.I
    )
    for result in results or []:
        text = "\n".join(str(result.get(key) or "") for key in (
            "url", "title", "content", "raw_content"
        ))
        for slug in pattern.findall(text):
            url = f"https://www.brawlplanet.com/it/maps/{slug}"
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def brawlplanet_rotation_manifest(results):
    """Return map/mode labels found in Brawl Planet detail pages."""
    manifest = []
    seen = set()
    for result in results or []:
        url = result.get("url") or ""
        if "/maps/" not in url:
            continue
        text = "\n".join(str(result.get(key) or "") for key in (
            "title", "content", "raw_content"
        ))
        match = re.search(
            r"(?:Migliori Brawler per|Best Brawlers for)\s+(.+?)\s*(?:\||-)\s*([^\n|]+)",
            text, re.I
        )
        if not match:
            match = re.search(r"^#\s+([^\n]+)\n##\s+([^\n]+)", text, re.M)
        if not match:
            continue
        map_name = re.sub(r"\s+", " ", match.group(1)).strip()
        mode_name = mode_name_it(
            re.sub(r"\s+", " ", match.group(2)).strip()
        )
        key = (map_name.casefold(), mode_name.casefold())
        if key in seen:
            continue
        seen.add(key)
        manifest.append({"map": map_name, "mode": mode_name, "url": url})
    return manifest


def clean_brawlplanet_cell(value):
    """Remove Tavily/browser citation markup from a table cell."""
    value = str(value or "")
    # Browser extracts wrap visible text as ``citeid†Text``. Preserve
    # Text while dropping the citation token; image-only citations are then
    # removed by the second expression.
    value = re.sub(r"cite[^†]*†([^]*)", r"\1", value)
    value = re.sub(r"cite[^]*", "", value)
    value = re.sub(r"\bImage(?:†[^\s|]+)?\b", "", value, flags=re.I)
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def brawlplanet_page_labels(result):
    """Read the localized map and mode labels from one detail page."""
    text = "\n".join(
        str(result.get(key) or "")
        for key in ("title", "content", "raw_content")
    )
    match = re.search(
        r"(?:Migliori Brawler per|Best Brawlers for)\s+(.+?)\s*(?:\||-)\s*([^\n|]+)",
        text,
        re.I
    )
    if not match:
        match = re.search(r"^#\s+([^\n]+)\n##\s+([^\n]+)", text, re.M)
    if not match:
        return None, None
    return (
        re.sub(r"\s+", " ", clean_brawlplanet_cell(match.group(1))).strip(),
        mode_name_it(
            re.sub(r"\s+", " ", clean_brawlplanet_cell(match.group(2))).strip()
        )
    )


def brawlplanet_table_rows(text, header_size):
    """Extract markdown rows after a Brawl Planet table header."""
    rows = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        cells = [clean_brawlplanet_cell(cell) for cell in line.split("|")]
        cells = [cell for cell in cells if cell]
        if len(cells) < header_size:
            continue
        if all(re.fullmatch(r"[-: ]+", cell) for cell in cells[:header_size]):
            continue
        lowered = cells[0].casefold()
        if (
            lowered.startswith("brawler")
            or lowered.startswith("squadra")
        ):
            continue
        rows.append(cells[:header_size])
    return rows


def brawlplanet_structured_stats(results, max_rows=10):
    """Extract compact Individuale/Squadre data from Brawl Planet pages.

    The page contains two repeated blocks: one for Trofei and one for
    Classificata. Keeping the parsed rows separate prevents Gemini from
    accidentally combining datasets when it writes the answer.
    """
    blocks = []
    seen = set()
    for result in results or []:
        url = result.get("url") or ""
        if "/maps/" not in url.casefold() or "brawlplanet.com" not in url.casefold():
            continue
        map_name, mode_name = brawlplanet_page_labels(result)
        if not map_name or not mode_name:
            continue
        text = "\n".join(
            str(result.get(key) or "")
            for key in ("title", "content", "raw_content")
        )
        individual_matches = list(re.finditer(
            r"^#{2,3}\s+Individuale\s*$", text, re.I | re.M
        ))
        if not individual_matches:
            continue

        for index, individual_match in enumerate(individual_matches):
            start = individual_match.start()
            end = (
                individual_matches[index + 1].start()
                if index + 1 < len(individual_matches)
                else len(text)
            )
            segment = text[start:end]
            prefix = text[:start]
            dataset_markers = re.findall(
                r"Percentuali di vittoria[^\n]*|Win percentages[^\n]*",
                prefix,
                re.I
            )
            marker = dataset_markers[-1].casefold() if dataset_markers else ""
            if "classificat" in marker or "ranked" in marker:
                dataset = "Classificata"
            elif "trofei" in marker or "trophy" in marker:
                dataset = "Trofei"
            else:
                dataset = "Classificata" if index else "Trofei"

            team_match = re.search(
                r"^#{2,3}\s+Squadre\s*$([\s\S]*?)(?=^#{2,3}\s+Altre mappe|\Z)",
                segment,
                re.I | re.M
            )
            individual_part = segment
            if team_match:
                individual_part = segment[:team_match.start()]

            individual_rows = brawlplanet_table_rows(individual_part, 4)[:max_rows]
            team_rows = (
                brawlplanet_table_rows(team_match.group(1), 2)[:max_rows]
                if team_match else []
            )
            if not individual_rows and not team_rows:
                continue

            key = (url.casefold(), dataset)
            if key in seen:
                continue
            seen.add(key)
            lines = [f"MAPPA: {map_name} | MODALITÀ: {mode_name} | DATASET: {dataset}"]
            if individual_rows:
                lines.append("INDIVIDUALI (Brawler | Vitt. | Scelta | Stella):")
                lines.extend("- " + " | ".join(row) for row in individual_rows)
            else:
                lines.append("INDIVIDUALI: Non disponibile")
            if team_rows:
                lines.append("SQUADRE (Composizione | Vitt.):")
                lines.extend("- " + " | ".join(row) for row in team_rows)
            else:
                lines.append("SQUADRE: Non disponibile")
            blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def web_search(query):
    query_lower = query.lower()
    today = datetime.now(timezone.utc).date().isoformat()
    is_meta_query = is_current_meta_query(query)
    game_context = get_game_context(query)

    if game_context == "ranked":
        context_hint = "Ranked Classificata current ranked map pool draft ban"
    elif game_context == "ladder":
        context_hint = "Ladder Scalata current event rotation"
    else:
        context_hint = "compare trophy ladder and Ranked separately"

    is_map_query = any(x in query_lower for x in [
        "mappa", "mappe", "rotazione", "mappa attuale",
        "mappa di oggi", "mappe attuali", "mappa corrente"
    ])

    is_image_subject_query = any(x in query_lower for x in [
        "brawler", "brawlers", "skin", "skins", "costume"
    ])

    brawler_map_performance_query = is_map_query and any(term in query_lower for term in [
        "win rate", "percentuale di vittoria", "percentuali di vittoria",
        "mappe migliori", "migliori mappe", "mappa migliore", "che mappa",
        "quale mappa", "mappe con", "per ogni modalità", "per ogni modalita"
    ])

    if brawler_map_performance_query:
        search_query = (
            f"Brawl Stars {query} {today} {context_hint} "
            f"site:brawltrack.app/brawlers "
            f"BrawlTrack Best Maps Best Game Modes win rate battles"
        )
    elif is_map_query:
        search_query = (
            f"Brawl Stars {query} {today} {context_hint} "
            f"site:brawltrack.app/maps OR site:brawltrack.app/pro/maps "
            f"BrawlTrack map preview Priority Picks win rate use rate Common Final Comps "
            f"Ladder Scalata Ranked"
        )
    elif is_meta_query:
        search_query = (
            f"Brawl Stars current meta {today} {query} {context_hint} "
            f"(site:brawltrack.app/brawlers OR site:brawltrack.app/ranked OR site:brawltrack.app/maps) "
            f"BrawlTrack tier list meta win rate Meta Usage Star Rate current maps team comps "
            f"latest balance changes competitive "
            f"gadget abilità stellare equipaggiamento overdrive nomi italiani "
            f"site:brawltrack.app OR site:brawlplanet.com"
        )
    elif is_image_subject_query:
        search_query = (
            f"Brawl Stars {query} "
            f"site:brawlstars.wiki"
        )
    else:
        search_query = (
            f"Brawl Stars {query} "
            f"brawler aggiornamenti notizie informazioni"
        )

    response = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_API_KEY,
            "query": search_query,
            "search_depth": "advanced",
            "max_results": 15 if is_map_query else 8,
            "include_answer": True,
            "include_raw_content": True,
            "include_images": True,
            "exclude_domains": [
                "pinterest.com",
                "youtube.com",
                "youtu.be",
                "ytimg.com",
                "tiktok.com",
                "vimeo.com"
            ]
        },
        timeout=20
    )

    response.raise_for_status()
    data = response.json()

    if is_meta_query and not is_map_query:
        # Meta is authoritative only when supported by BrawlTrack or Brawl Planet.
        # Do not let Brawl Time Ninja/Brawlify/Noff statistics become a meta tier list.
        allowed = []
        for result in data.get("results", []):
            url = (result.get("url") or "").casefold()
            if "brawltrack.app" in url:
                result["source_role"] = "primary_brawltrack"
                allowed.append(result)
            elif "brawlplanet.com" in url or "brawlplanet.nl" in url:
                result["source_role"] = "fallback_brawlplanet"
                allowed.append(result)
        data["results"] = allowed
        data["answer"] = None
        data["meta_sources_verified"] = bool(allowed)

    if is_map_query:
        # BrawlTrack-first: extract the exact map pages returned by search.
        track_urls = list(dict.fromkeys(
            result.get("url") for result in data.get("results", [])
            if result.get("url") and "brawltrack.app" in result.get("url", "").casefold()
            and ("/maps/" in result.get("url", "") or "/pro/maps/" in result.get("url", ""))
        ))[:20]
        if track_urls:
            try:
                extracted = requests.post(
                    "https://api.tavily.com/extract",
                    json={"api_key": TAVILY_API_KEY, "urls": track_urls, "extract_depth": "advanced"},
                    timeout=30
                )
                extracted.raise_for_status()
                for result in extracted.json().get("results", []):
                    result["source_role"] = "primary_brawltrack"
                data["results"] = extracted.json().get("results", []) + data.get("results", [])
            except Exception as e:
                print("BRAWLTRACK: estrazione mappe non disponibile", repr(e), flush=True)

        # Brawl Planet is retained strictly as fallback when BrawlTrack lacks a field/page.
        # Read localized page contents, not just search snippets. Keep team
        # tables that often appear after the complete individual leaderboard.
        planet_urls = list(dict.fromkeys(
            localized for result in data.get("results", [])
            if (localized := italian_planet_url(result.get("url", "")))
        ))[:5]
        if is_all_maps_request(query):
            planet_urls = list(dict.fromkeys([
                "https://www.brawlplanet.com/it/maps",
                "https://www.brawlplanet.com/it"
            ] + planet_urls))[:20]
        if planet_urls:
            try:
                extracted = requests.post(
                    "https://api.tavily.com/extract",
                    json={"api_key": TAVILY_API_KEY, "urls": planet_urls,
                          "extract_depth": "advanced"}, timeout=25
                )
                extracted.raise_for_status()
                localized_results = extracted.json().get("results", [])
                data["results"] = localized_results + data.get("results", [])

                if is_all_maps_request(query):
                    detail_urls = brawlplanet_map_urls(localized_results)
                    if detail_urls:
                        detail_extract = requests.post(
                            "https://api.tavily.com/extract",
                            json={"api_key": TAVILY_API_KEY,
                                  "urls": detail_urls[:20],
                                  "extract_depth": "advanced"},
                            timeout=35
                        )
                        detail_extract.raise_for_status()
                        detail_results = detail_extract.json().get("results", [])
                        data["results"] = (
                            detail_results + localized_results + data.get("results", [])
                        )
            except Exception as e:
                # Tavily can return a non-JSON/partial extract response. A
                # failed enrichment must not abort the whole Telegram answer.
                print(
                    "BRAWL PLANET: estrazione italiana non disponibile",
                    repr(e),
                    flush=True
                )

        data["brawlplanet_rotation"] = brawlplanet_rotation_manifest(
            data.get("results", [])
        )
        secondary_response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": (
                    f"Brawl Stars {query} {today} {context_hint} current maps "
                    "best brawlers win rate pick rate team comp"
                ),
                "search_depth": "advanced",
                "max_results": 8,
                "include_answer": False,
                "include_raw_content": True,
                "include_images": False,
                "include_domains": [
                    "brawlify.com", "brawltime.ninja", "noff.gg"
                ]
            },
            timeout=20
        )
        secondary_response.raise_for_status()
        secondary_data = secondary_response.json()

        seen_urls = {
            result.get("url") for result in data.get("results", [])
        }
        for result in secondary_data.get("results", []):
            if result.get("url") not in seen_urls:
                result["source_role"] = "secondary_fallback"
                data.setdefault("results", []).append(result)


    return data



def extract_brawl_ball_map(search_data):
    results = search_data.get("results", [])

    for domain in [
        "brawlinsights.com",
        "brawlify.com"
    ]:
        for result in results:
            url = (result.get("url") or "").lower()

            if domain not in url:
                continue

            text = (
                (result.get("title") or "")
                + "\n"
                + (result.get("content") or "")
                + "\n"
                + (result.get("raw_content") or "")
            )

            print(
                "DATI ROTAZIONE DA",
                domain,
                ":",
                text[text.find("| Date and Time (UTC) | Mode | Map |"):][:12000] if "| Date and Time (UTC) | Mode | Map |" in text else text[-12000:],
                flush=True
            )

    print(
        "MAPPA BRAWL BALL LIVE NON ANCORA ESTRATTA",
        flush=True
    )

    return None


def get_verified_map_comp(map_name):
    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": f"\"{map_name}\" \"Best Teams\" Brawl Ball",
                "search_depth": "advanced",
                "max_results": 5,
                "include_answer": False,
                "include_raw_content": True,
                "include_images": False,
                "include_domains": [
                    "brawltime.ninja",
                    "powerleagueprodigy.com"
                ]
            },
            timeout=20
        )

        response.raise_for_status()
        data = response.json()

        for result in data.get("results", []):
            url = (result.get("url") or "").lower()
            text = (
                (result.get("raw_content") or "")
                + "\n"
                + (result.get("content") or "")
            )

            if "brawltime.ninja" in url:
                team_match = re.search(
                    r"Best Teams.*?\n\s*1\s*\|\s*([^\n|]+(?:,\s*[^\n|]+){2})",
                    text,
                    re.I | re.S
                )

                if not team_match:
                    team_match = re.search(
                        r"1\s*\|\s*([^\n|]+(?:,\s*[^\n|]+){2})\s*\|\s*\d+",
                        text,
                        re.I
                    )

                if team_match:
                    team = [
                        x.strip()
                        for x in team_match.group(1).split(",")
                        if x.strip()
                    ]

                    if len(team) >= 3:
                        verified = team[:3]

                        print(
                            "COMP VERIFICATA BRAWLTIME:",
                            map_name,
                            verified,
                            flush=True
                        )

                        return verified

        for result in data.get("results", []):
            url = (result.get("url") or "").lower()

            if "powerleagueprodigy.com" not in url:
                continue

            text = (
                (result.get("raw_content") or "")
                + "\n"
                + (result.get("content") or "")
            )

            section_match = re.search(
                r"##\s*" + re.escape(map_name) +
                r"\s*(.*?)(?=\n##\s|\Z)",
                text,
                re.I | re.S
            )

            if not section_match:
                continue

            section = section_match.group(1)

            if (
                "brawl ball" not in section.lower()
                or "top brawlers" not in section.lower()
            ):
                continue

            top_section = section.split("Top Brawlers", 1)[1]

            names = re.findall(
                r"Image:\s*([^\n]+)\s*\n+\s*\1\s*\n",
                top_section,
                re.I
            )

            clean_names = []

            for name in names:
                name = name.strip()

                if name and name.lower() not in [
                    "brawl ball",
                    map_name.lower()
                ]:
                    if name not in clean_names:
                        clean_names.append(name)

            if len(clean_names) >= 3:
                verified = clean_names[:3]

                print(
                    "COMP VERIFICATA FALLBACK:",
                    map_name,
                    verified,
                    flush=True
                )

                return verified

        print(
            "COMP VERIFICATA NON TROVATA:",
            map_name,
            flush=True
        )

    except Exception as e:
        print(
            "ERRORE COMP VERIFICATA:",
            repr(e),
            flush=True
        )

    return []

def search_map_comp(map_name):
    response = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_API_KEY,
            "query": f"\"{map_name}\" Brawl Stars Brawl Ball",
            "search_depth": "advanced",
            "max_results": 8,
            "include_answer": False,
            "include_raw_content": True,
            "include_images": False,
            "include_domains": [
                "noff.gg"
            ]
        },
        timeout=20
    )
    response.raise_for_status()

    data = response.json()

    specific_results = []

    map_key = re.sub(r"[^a-z0-9]+", "-", map_name.lower()).strip("-")

    for result in data.get("results", []):
        url = (result.get("url") or "").lower()
        title = (result.get("title") or "").lower()
        content = (
            (result.get("content") or "")
            + "\n"
            + (result.get("raw_content") or "")
        ).lower()

        if (
            f"/brawl-stars/map/{map_key}" in url
            or (
                map_name.lower() in title
                and "brawl ball" in content
            )
        ):
            specific_results.append(result)

    if specific_results:
        print(
            "NOFF MAPPA SPECIFICA TROVATA:",
            map_name,
            flush=True
        )
        return {
            "results": specific_results
        }

    print(
        "NOFF MAPPA SPECIFICA NON TROVATA, USO FALLBACK:",
        map_name,
        flush=True
    )

    fallback_response = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_API_KEY,
            "query": f"\"{map_name}\" \"Brawl Ball\" Brawl Stars best brawlers win rate",
            "search_depth": "advanced",
            "max_results": 8,
            "include_answer": False,
            "include_raw_content": True,
            "include_images": False
        },
        timeout=20
    )

    fallback_response.raise_for_status()
    return fallback_response.json()


def get_noff_map_image(map_name):
    try:
        response = requests.get(
            "https://api.brawlapi.com/v1/maps",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20
        )
        response.raise_for_status()

        data = response.json()

        target = re.sub(
            r"[^a-z0-9]+",
            "",
            map_name.lower()
        )

        for map_data in data.get("list", []):
            name = map_data.get("name") or ""

            name_key = re.sub(
                r"[^a-z0-9]+",
                "",
                name.lower()
            )

            if name_key != target:
                continue

            image_url = map_data.get("imageUrl")

            if image_url:
                print(
                    "IMMAGINE MAPPA TROVATA:",
                    name,
                    image_url,
                    flush=True
                )
                return image_url

        print(
            "IMMAGINE MAPPA NON TROVATA:",
            map_name,
            flush=True
        )

    except Exception as e:
        print(
            "ERRORE IMMAGINE MAPPA:",
            repr(e),
            flush=True
        )

    return None


def image_search(query):
    search_query = (
        f"Brawl Stars {query} "
        f"site:liquipedia.net/brawlstars "
        f"map brawler skin image"
    )

    response = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_API_KEY,
            "query": search_query,
            "search_depth": "advanced",
            "max_results": 5,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": True,
            "include_domains": [
                "liquipedia.net"
            ]
        },
        timeout=20
    )

    response.raise_for_status()
    return response.json()


async def send_relevant_images(context, chat_id, question, images):
    question_lower = question.lower()

    image_keywords = [
        "mappa", "mappe", "mappa attuale", "mappa di oggi",
        "brawler", "brawlers", "skin", "skins", "costume"
    ]

    if not any(keyword in question_lower for keyword in image_keywords):
        return

    sent = 0
    seen = set()

    for image in images:
        if isinstance(image, dict):
            image_url = image.get("url")
            description = (image.get("description") or "").lower()
        else:
            image_url = image
            description = ""
        if any(k in question_lower for k in ["brawler", "brawlers", "skin", "skins", "costume"]):
            ignored_words = {"brawler", "brawlers", "skin", "skins", "costume", "parlami", "dimmi", "qual", "quale", "della", "delle", "del", "dei", "degli", "una", "uno", "con", "per", "che", "come", "migliore", "miglior"}
            search_terms = [word.strip(".,!?():;") for word in question_lower.split() if len(word.strip(".,!?():;")) >= 3 and word.strip(".,!?():;") not in ignored_words]
            image_text = f"{description} {image_url or ''}".lower()
            if search_terms and not any(term in image_text for term in search_terms):
                print("IMMAGINE NON PERTINENTE:", image_url, description, flush=True)
                continue

        if not image_url or image_url in seen:
            continue

        seen.add(image_url)

        try:
            image_response = requests.get(
                image_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )

            image_response.raise_for_status()

            content_type = image_response.headers.get("Content-Type", "").lower()

            if not content_type.startswith("image/"):
                print("IMMAGINE SCARTATA:", image_url, content_type, flush=True)
                continue

            if len(image_response.content) > 10 * 1024 * 1024:
                print("IMMAGINE TROPPO GRANDE:", image_url, flush=True)
                continue

            await context.bot.send_photo(
                chat_id=chat_id,
                photo=image_response.content
            )

            sent += 1

            if sent >= 1:
                break

        except Exception as e:
            print("ERRORE INVIO IMMAGINE:", repr(e), flush=True)




def brawler_name_it(name):
    """Translate a canonical Brawler name through the verified Italian catalogue."""
    raw=str(name or "").strip()
    if not raw:return name
    try:
        names=(safe_get("i18n/names.it.json.gz") or {}).get("brawlers",{})
        return names.get(raw.upper(),raw)
    except Exception:
        return raw
MODE_NAMES_IT = {
    "Brawl Ball": "Footbrawl",
    "Hot Zone": "Dominio",
    "Gem Grab": "Arraffagemme",
    "Heist": "Rapina",
    "Knockout": "K.O.",
    "Bounty": "Ricercati",
    "Showdown": "Sopravvivenza",
    "Wipeout": "Annientamento",
    "Duels": "Duelli",
    "Payload": "Corsa dei carrelli",
    "Basket Brawl": "Basket Brawl",
    "Brawl Hockey": "Brawl Hockey",
    "Brawl Arena": "Arena dei Brawler",
}


def translate_mode_names_in_text(text):
    if not text:
        return text
    translated = text
    for english_name in sorted(MODE_NAMES_IT, key=len, reverse=True):
        translated = re.sub(
            r"(?<![A-Za-z0-9])" + re.escape(english_name) + r"(?![A-Za-z0-9])",
            MODE_NAMES_IT[english_name],
            translated,
            flags=re.I
        )
    return translated


def mode_name_it(name):
    if not name:
        return name
    for english_name, italian_name in MODE_NAMES_IT.items():
        if english_name.casefold() == name.casefold():
            return italian_name
    return name


MAP_NAMES_IT = {
    # Ricercati / Bounty
    "No Excuses": "Senza scuse",
    "Shooting Star": "Piana delle stelle",
    "Snake Prairie": "Prateria dei serpenti",
    "Layer Cake": "Arena stratificata",
    "Hideout": "Nascondiglio",

    # Footbrawl / Brawl Ball
    "Second Try": "Secondo tentativo",
    "Trickey": "Red Brawl Arena",
    "Pinhole Punt": "Camp Brawl",
    "Center Stage": "Stamford Brawl",
    "Sneaky Fields": "Campetto incolto",
    "Pinball Dreams": "Brawlacanã",
    "Triple Dribble": "Brawl Trafford",
    "Beach Ball": "Campo da beach brawl",
    "Backyard Bowl": "Campetto",
    "Sunny Soccer": "Campetto sabbioso",
    "Super Beach": "Superspiaggia",
    "Penalty Kick": "Stadio delle Brawlpi",
    "Spiraling Out": "Spirale della vittoria",

    # Duelli
    "Petticoat Duel": "Duello meschino",
    "No Surrender": "Arena dei coraggiosi",
    "Shrouding Serpent": "Serpente sibilante",
    "Warrior's Way": "Via del guerriero",
    "Warrior’s Way": "Via del guerriero",
    "Monkey Maze": "Labirinto delle scimmie",
    "Zen Garden": "Giardino zen",

    # Arraffagemme / Gem Grab
    "Forest Clearing": "Radura nella foresta",
    "The cooler Hard Rock": "Miniera delle acca",
    "Crystal Arcade": "Sala giochi di cristallo",
    "Deathcap Trap": "Antro velenoso",
    "Last Stop": "Ultima fermata",
    "Hard Rock Mine": "Miniera Rocciadura",
    "Double Swoosh": "Arco doppio",
    "Gem Fort": "Fortino delle gemme",
    "Rustic Arcade": "Sala giochi rustica",
    "Open Space": "Campo aperto",
    "Undermine": "Miniera minacciosa",
    "Minecart Madness": "Miniera preziosa",
    "Dungeon Train": "Treno sotterraneo",

    # Rapina / Heist
    "Safe(r) Zone": "Santuario dei santuari",
    "The Great Lake": "Gran lago",
    "GG 2.0": "Colpo grosso",
    "G.G. Mortuary": "Obitorio G.G.",
    "Kaboom Canyon": "Canyon Bum Bum",
    "Safe Zone": "Santuario",
    "Hot Potato": "Battigia ustionante",
    "Hot-Potato": "Battigia ustionante",

    # Dominio / Hot Zone
    "Watersport": "Piscine del dolore",
    "Noisy Neighbors": "Vicini chiassosi",
    "Open Business": "Campo aperto",
    "Dueling Beetles": "Distesa degli scarafaggi",
    "Ring of Fire": "Ring di fuoco",
    "Parallel Plays": "Giocate parallele",
    "In the Liminal": "Spazio liminale",
    "Quick Travel": "Scorrimento veloce",
    "Tread Carefully": "Cautela estrema",

    # K.O. / Knockout
    "H for…": "Acca boschiva",
    "H for...": "Acca boschiva",
    "Two Rivers": "Doppio fiume",
    "Deep Forest": "Foresta fitta",
    "Tiny Islands": "Isolette",
    "Temple of Vroom": "Tempio del tuono",
    "Overgrown Ruins": "Rovine infestate",
    "Goldarm Gulch": "Burrone di Bracciodoro",
    "Out in the Open": "Alla luce del sole",
    "Belle's Rock": "Rupe di Belle",
    "Belle’s Rock": "Rupe di Belle",
    "New Horizons": "Nuovi orizzonti",
    "Flaring Phoenix": "Fenice sfolgorante",
    "Four Levels": "Quattro livelli",
    "Stroke of Luck": "Colpo di fortuna",

    # Annientamento / Wipeout
    "Layer Bake": "Strati verdi",
    "Quad Damage": "Danno quadruplo",
    "The Great Open": "Apertura massima",
    "Infinite Doom": "Rovina infinita",
    "Spice Production": "Arena delle spezie",
    "Slayer's Paradise": "Sogno degli sterminatori",
    "Slayer’s Paradise": "Sogno degli sterminatori",

    # Sopravvivenza / Showdown
    "Acid Lakes": "Laghi acidi",
    "Island Invasion": "Isola invasa",
    "Skull Creek": "Torrente del teschio",
    "Dark Passage": "Passaggio spettrale",
    "Flying Fantasies": "Pianura volante",
    "Rockwall Brawl": "Valle rocciosa",
    "Safety Center": "Rifugio d'emergenza",
    "Feast or Famine": "Piana della fame",
    "Cavern Churn": "Caverna rumorosa",
    "Double Trouble": "Landa dei pericoli",
    "Dried Up River": "Fiume prosciugato",
    "Marksman's Paradise": "Paradiso dei cecchini",
    "Marksman’s Paradise": "Paradiso dei cecchini",

    # Mappe ufficiali introdotte/attive nel 2026
    "In Demand": "Centrocampo affollato",
    "Pump It Up": "Trance agonistica",
    "Stone Skipping": "Rimbalzello",
    "False Sense Of Security": "Mischia ferroviaria",
    "Alchemy": "Alchimia",
    "Net Presence": "Presenza in campo",
    "Triple Threat": "Tripla minaccia",
    "Brawler's Rift": "Faglia dei brawler",
    "Brawler’s Rift": "Faglia dei brawler"
}


def map_name_it(name):
    if not name:
        return name

    lowered = name.casefold()
    for english_name, italian_name in MAP_NAMES_IT.items():
        if english_name.casefold() == lowered:
            return italian_name

    return name


def translate_map_names_in_text(text):
    if not text:
        return text

    translated = text

    for english_name in sorted(MAP_NAMES_IT, key=len, reverse=True):
        italian_name = MAP_NAMES_IT[english_name]
        translated = re.sub(
            r"(?<![A-Za-z0-9])" + re.escape(english_name) + r"(?![A-Za-z0-9])",
            italian_name,
            translated,
            flags=re.I
        )

    return translated


def translate_game_terms_in_text(text):
    if not text:
        return text

    replacements = [
        (r"\bStar\s*Power\b", "abilità stellare"),
        (r"\bStar\s*Powers\b", "abilità stellari"),
        (r"\bGear\b", "equipaggiamento"),
        (r"\bGears\b", "equipaggiamenti"),
        (r"\bHypercharge\b", "overdrive"),
        (r"\bHypercharges\b", "overdrive"),
        (r"\bBuild\b", "configurazione"),
        (r"\bLoadout\b", "configurazione"),
        (r"\bgiocatori?\s+stella\b", "Miglior Star Player"),
        (r"\bstar\s+player\b", "Miglior Star Player"),
    ]

    translated = text
    for pattern, replacement in replacements:
        translated = re.sub(
            pattern,
            replacement,
            translated,
            flags=re.I
        )

    return translated


def resolve_map_source_name(name):
    if not name:
        return None

    cleaned = name.strip()
    lowered = cleaned.casefold()

    # Nome inglese/canonico già utilizzabile dalla API mappe.
    for english_name in MAP_NAMES_IT:
        if english_name.casefold() == lowered:
            return english_name

    # Se il modello restituisce il nome italiano, risali al nome sorgente.
    for english_name, italian_name in MAP_NAMES_IT.items():
        if italian_name.casefold() == lowered:
            return english_name

    # Consenti comunque nomi non presenti nel dizionario: get_noff_map_image
    # farà la verifica contro l'elenco reale delle mappe di BrawlAPI.
    return cleaned

def get_noff_brawler_image(brawler_name):
    try:
        slug = re.sub(r"[^a-z0-9]+", "_", brawler_name.lower()).strip("_")
        image_url = f"https://www.noff.gg/brawl-stars/res/img/brawlers/{slug}.webp"

        response = requests.get(
            image_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15
        )

        if response.status_code == 200 and response.headers.get("Content-Type", "").lower().startswith("image/"):
            print("IMMAGINE BRAWLER TROVATA:", brawler_name, image_url, flush=True)
            return image_url

        print("IMMAGINE BRAWLER NON TROVATA:", brawler_name, response.status_code, flush=True)

    except Exception as e:
        print("ERRORE IMMAGINE BRAWLER:", brawler_name, repr(e), flush=True)

    return None


async def send_comp_brawler_images(context, chat_id, brawler_names):
    for brawler_name in brawler_names:
        try:
            image_url = get_noff_brawler_image(brawler_name)

            if not image_url:
                continue

            image_response = requests.get(
                image_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15
            )
            image_response.raise_for_status()

            content_type = image_response.headers.get(
                "Content-Type",
                ""
            ).lower()

            if not content_type.startswith("image/"):
                continue

            await context.bot.send_photo(
                chat_id=chat_id,
                photo=image_response.content,
                caption=brawler_name_it(brawler_name)
            )

        except Exception as e:
            print(
                "ERRORE INVIO IMMAGINE BRAWLER:",
                brawler_name,
                repr(e),
                flush=True
            )



def get_brawltrack_meta_context(question):
    """Return verified BrawlTrack meta rows with official Italian build names."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return ""
    q=(question or "").casefold()
    if not any(term in q for term in ("meta","brawler","build","configurazione","gadget","abilità stellare","abilita stellare","equipaggiamento","gear","overdrive","hypercharge","win rate","pick rate","star rate","modalità","modalita","mappa")):
        return ""
    try:
        response=requests.get(f"{SUPABASE_URL}/rest/v1/brawltrack_meta_cache",headers={"apikey":SUPABASE_SERVICE_ROLE_KEY,"Authorization":f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"},params={"select":"brawler_id,brawler_name,win_rate,pick_rate,star_rate,rank_label,popular_builds,modes,source_updated_at","order":"brawler_name.asc","limit":"108"},timeout=15)
        response.raise_for_status(); rows=response.json(); selected=[]
        # Resolve user-facing names through the official Brawler ID catalogue. The ID is
        # the authoritative join key with BrawlTrack; names are aliases only for input.
        cat=requests.get(f"{SUPABASE_URL}/rest/v1/brawlers_catalog",headers={"apikey":SUPABASE_SERVICE_ROLE_KEY,"Authorization":f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"},params={"select":"brawler_id,name_en,name_it","limit":"200"},timeout=15)
        cat.raise_for_status(); catalog=cat.json()
        # Official Italian localization is keyed by the canonical English Brawler
        # name; Brawler ID remains the authoritative join key with BrawlTrack.
        try: official_brawler_names=(safe_get("i18n/names.it.json.gz") or {}).get("brawlers",{})
        except Exception: official_brawler_names={}
        catalog_by_id={};aliases_by_id={}
        for b in catalog:
            bid=int(b.get("brawler_id")) if b.get("brawler_id") is not None else None
            if bid is None:continue
            en=str(b.get("name_en") or "").strip()
            it=str(b.get("name_it") or "").strip() or str(official_brawler_names.get(en.upper()) or "").strip()
            catalog_by_id[bid]={"name_en":en,"name_it":it}
            aliases_by_id[bid]={x.casefold() for x in (en,it) if x}
        for row in rows:
            bid=int(row.get("brawler_id")) if row.get("brawler_id") is not None else None
            aliases=set(aliases_by_id.get(bid,set()))
            aliases.add(str(row.get("brawler_name") or "").strip().casefold())
            aliases.discard("")
            if any(re.search(r"(?<![a-z0-9])"+re.escape(alias)+r"(?![a-z0-9])",q) for alias in aliases): selected.append(row)
        if not selected and any(term in q for term in ("meta","tier list","tierlist","miglior brawler","migliori brawler")): selected=rows
        if not selected: return ""
        payload=[]
        for row in selected:
            builds=row.get("popular_builds") if isinstance(row.get("popular_builds"),dict) else {}; clean=[]
            for item in (builds.get("items") or []):
                clean.append({"rank":item.get("rank"),"use_rate":item.get("use_rate"),"components":[{"type":c.get("type"),"name_it":c.get("name_it")} for c in (item.get("components") or []) if c.get("name_it")]})
            hyper=builds.get("hypercharge") if isinstance(builds.get("hypercharge"),dict) else None
            modes=row.get("modes") if isinstance(row.get("modes"),dict) else {}
            best_modes=modes.get("items") or []; best_maps=modes.get("maps") or []
            try: official_names=safe_get("i18n/names.it.json.gz") or {}
            except Exception: official_names={}
            official_modes=official_names.get("modes",{}) if isinstance(official_names,dict) else {}
            official_maps=official_names.get("maps",{}) if isinstance(official_names,dict) else {}
            def exact_official(table,value):
                raw=str(value or "").strip()
                if not raw:return None
                # Reuse the live-map catalogue resolver: its canonical keys are uppercase.
                translated=localized({"verified":table},"verified",raw)
                return translated if translated!=raw else None
            def loc_mode(value):
                raw=str(value or "").strip()
                # Keep BrawlTrack's canonical label unless the exact same source label
                # exists in the verified catalogue. Composite variants must never be
                # synthesized from their component words.
                return exact_official(official_modes,raw) or raw
            def loc_map(value):
                raw=str(value or "").strip()
                # BrawlTrack map identity is authoritative for meta pairing. The
                # generated names catalogue is not authoritative for display here.
                return raw
            # Rank modes by their actual win rate, but only expose a mode when at least
            # one map exists for that exact source label. This prevents unrelated pairings.
            ranked_modes=sorted(best_modes,key=lambda m:float(m.get("win_rate") or 0),reverse=True)
            top_pairs=[]; target=str(row.get("brawler_name") or "").strip().casefold()
            for bm in ranked_modes:
                raw_mode=str(bm.get("mode") or "").strip()
                same=[m for m in best_maps if str(m.get("mode") or "").strip().casefold()==raw_mode.casefold()]
                if not same: continue
                chosen_map=same[0]
                comp=None
                try:
                    pro=brawltrack_pro_map_stats(chosen_map.get("map")) or {}
                    matching=[c for c in (pro.get("final_comps") or []) if any(str(x).strip().casefold()==target for x in (c.get("team") or []))]
                    if matching:
                        chosen=max(matching,key=lambda c:(int(c.get("sets") or 0),float(c.get("win_rate") or 0)))
                        comp=[brawler_name_it(x) for x in (chosen.get("team") or [])]
                except Exception as comp_error: print("BRAWLTRACK BRAWLER COMP ERROR:",repr(comp_error),flush=True)
                top_pairs.append({"mode":loc_mode(raw_mode),"map":loc_map(chosen_map.get("map")),"verified_comp":comp})
                if len(top_pairs)>=3: break
            top_maps=sorted(best_maps,key=lambda m:(float(m.get("win_rate") or 0),int(m.get("battles") or 0)),reverse=True)[:3]
            top_maps=[{"map":loc_map(m.get("map")),"mode":loc_mode(m.get("mode"))} for m in top_maps]
            # Prefer the best currently active trophy map for this Brawler.
            # Rotation comes from Supercell; performance comes from the active-map dataset.
            active_best=None
            try:
                report=collect_report("ladder")
                candidates=[]
                for entry in (report.get("maps") or []):
                    for data in (entry.get("datasets") or []):
                        if data.get("label")!="Trofei":continue
                        for section,items in (data.get("sections") or {}).items():
                            if section not in ("individual","solo","duo_individual","trio_individual"):continue
                            for stat in items:
                                stat_name=str(stat.get("brawler",stat.get("brawler_name")) or "").strip()
                                if stat_name.casefold()!=target:continue
                                wr=stat.get("wr",stat.get("win_rate"))
                                if wr is None:continue
                                event=entry.get("event") or {}
                                candidates.append({"mode":entry.get("mode_name_it") or event.get("event_mode"),"map":entry.get("map_name_it") or event.get("event_map"),"win_rate":float(wr),"sample":int(stat.get("tm",0) or 0)})
                if candidates:
                    # Keep the three strongest active maps, highest win rate first.
                    # Sample size is only the tie-breaker.
                    active_best=sorted(candidates,key=lambda x:(x["win_rate"],x["sample"]),reverse=True)[:3]
            except Exception as active_error: print("ACTIVE BRAWLER MAP ERROR:",repr(active_error),flush=True)
            bid=int(row.get("brawler_id")) if row.get("brawler_id") is not None else None
            display=(catalog_by_id.get(bid) or {}).get("name_it") or (catalog_by_id.get(bid) or {}).get("name_en") or row.get("brawler_name")
            payload.append({"brawler":display,"win_rate":row.get("win_rate"),"meta_usage":row.get("pick_rate"),"star_rate":row.get("star_rate"),"rank":row.get("rank_label"),"popular_builds":clean,"overdrive":({"name_it":hyper.get("name_it")} if hyper and hyper.get("name_it") else None),"recommended_active_map":active_best,"top_3_mode_maps":top_pairs,"top_3_maps":top_maps,"updated_at":row.get("source_updated_at")})
        return "DATI META STRUTTURATI E LOCALIZZATI (PRIORITARI):\n"+json.dumps(payload,ensure_ascii=False,separators=(",",":"))
    except Exception as e:
        print("BRAWLTRACK META CONTEXT ERROR:",repr(e),flush=True); return ""


def render_structured_brawler_meta(context_text):
    """Deterministic compact rendering for a single Brawler meta/build request."""
    prefix="DATI META STRUTTURATI E LOCALIZZATI (PRIORITARI):\n"
    if not context_text or not context_text.startswith(prefix):return None
    try:
        rows=json.loads(context_text[len(prefix):])
        if not isinstance(rows,list) or len(rows)!=1:return None
        row=rows[0]; builds=row.get("popular_builds") or []
        if not builds:return None
        build=builds[0]; lines=["I nostri sistemi abusivi hanno tirato fuori i dati freschi per "+str(row.get("brawler") or "")+".",""]
        rate=build.get("use_rate")
        title="Configurazione più usata"+(f" ({float(rate):.2f}%):" if rate is not None else ":")
        lines.append(title)
        labels={"gadget":"Gadget","star_power":"Abilità stellare","gear":"Equipaggiamento"}
        gears=[]
        for c in build.get("components") or []:
            typ=str(c.get("type") or ""); name=str(c.get("name_it") or "").strip()
            if not name:continue
            if typ=="gear":gears.append(name)
            elif typ in labels:lines.append(f"- {labels[typ]}: {name}")
        if gears:lines.append("- Equipaggiamenti: "+", ".join(gears))
        over=row.get("overdrive") or {}
        if over.get("name_it"):lines.append("- Overdrive: "+str(over["name_it"]))
        active=row.get("recommended_active_map")
        if isinstance(active,list) and active:
            lines += ["","Top 3 mappe consigliate tra quelle attualmente in rotazione in game:"]
            for idx,item in enumerate(active[:3],1):
                lines.append(f"{idx}. {item.get('mode')} — {item.get('map')} — Win rate: {float(item.get('win_rate')):.2f}%")
        else:
            lines += ["","Mappe consigliate in rotazione: dati insufficienti al momento."]
        return "\n".join(lines)
    except Exception as e:
        print("META DETERMINISTIC RENDER ERROR:",repr(e),flush=True);return None


def save_trophy_snapshot(player_tag, player_name, trophies):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("SUPABASE NON CONFIGURATO", flush=True)
        return False

    try:
        tag = player_tag.upper().replace("#", "").strip()

        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/trophy_history",
            headers={
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal"
            },
            json={
                "player_tag": tag,
                "player_name": player_name,
                "trophies": int(trophies)
            },
            timeout=15
        )

        response.raise_for_status()
        return True

    except Exception as e:
        print("ERRORE SALVATAGGIO TROFEI:", repr(e), flush=True)
        return False


def get_trophy_history(player_tag, days=90):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return []

    try:
        tag = player_tag.upper().replace("#", "").strip()
        since = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()

        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/trophy_history",
            headers={
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"
            },
            params={
                "player_tag": f"eq.{tag}",
                "recorded_at": f"gte.{since}",
                "select": "trophies,recorded_at",
                "order": "recorded_at.asc"
            },
            timeout=15
        )

        response.raise_for_status()
        return response.json()

    except Exception as e:
        print("ERRORE LETTURA STORICO:", repr(e), flush=True)
        return []


def trophy_value_at_or_before(history, target_time):
    selected = None

    for row in history:
        try:
            dt = datetime.fromisoformat(
                row["recorded_at"].replace("Z", "+00:00")
            )

            if dt <= target_time:
                selected = int(row["trophies"])
            else:
                break

        except Exception:
            continue

    return selected


def calculate_trophy_changes(history, current_trophies):
    now = datetime.now(timezone.utc)
    now_rome = now.astimezone(ROME)

    # "Oggi" segue il giorno italiano, non la mezzanotte UTC.
    start_today = now_rome.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    ).astimezone(timezone.utc)

    targets = {
        "today": start_today,
        "7d": now - timedelta(days=7),
        "15d": now - timedelta(days=15),
        "30d": now - timedelta(days=30),
        "90d": now - timedelta(days=90)
    }

    changes = {}

    for key, target in targets.items():
        old_value = trophy_value_at_or_before(
            history,
            target
        )

        # On the first monitored day there may be no snapshot at/before
        # midnight. In that case the registration/first snapshot of that
        # Italian calendar day is the baseline for OGGI. This makes an
        # immediate classifica query show positive or negative movement
        # from the moment monitoring started, without pretending that the
        # value existed at midnight.
        if key == "today" and old_value is None:
            for row in history:
                try:
                    dt = datetime.fromisoformat(
                        row["recorded_at"].replace("Z", "+00:00")
                    )
                    if dt >= start_today:
                        old_value = int(row["trophies"])
                        break
                except Exception:
                    continue

        changes[key] = (
            current_trophies - old_value
            if old_value is not None
            else None
        )

    return changes


def format_trophy_change(value):
    if value is None:
        return "Storico non disponibile"

    if value > 0:
        return f"+{value:,}".replace(",", ".")

    return f"{value:,}".replace(",", ".")


def get_tracked_player_tags():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return []

    try:
        headers = {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"
        }
        tags = []
        sources = (
            ("community_members", {"select": "player_tag", "player_tag": "not.is.null", "is_active": "eq.true", "limit": "5000"}),
            ("trophy_history", {"select": "player_tag", "order": "recorded_at.desc", "limit": "5000"}),
        )
        for table, params in sources:
            response = requests.get(
                f"{SUPABASE_URL}/rest/v1/{table}", headers=headers,
                params=params, timeout=20
            )
            response.raise_for_status()
            for row in response.json():
                tag = str(row.get("player_tag", "")).strip().upper()
                if tag and tag not in tags:
                    tags.append(tag)

        return tags

    except Exception as e:
        print("ERRORE LETTURA TAG MONITORATI:", repr(e), flush=True)
        return []


def _tracking_headers(prefer=None):
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def save_player_tracking(player):
    """Persist the latest profile and a Ranked event only when a value changes."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return False

    tag = player["tag"].replace("#", "").upper()
    now = datetime.now(timezone.utc).isoformat()
    try:
        state_response = requests.get(
            f"{SUPABASE_URL}/rest/v1/player_tracking_state",
            headers=_tracking_headers(),
            params={"player_tag": f"eq.{tag}", "select": "*", "limit": "1"},
            timeout=15,
        )
        state_response.raise_for_status()
        rows = state_response.json()
        previous = rows[0] if rows else {}

        ranked_fields = {
            "ranked_current": player.get("ranked_current"),
            "ranked_current_elo": player.get("ranked_current_elo"),
            "ranked_season_peak": player.get("ranked_season_peak"),
            "ranked_season_peak_elo": player.get("ranked_season_peak_elo"),
            "ranked_career_peak": player.get("ranked_career_peak"),
            "ranked_career_peak_elo": player.get("ranked_career_peak_elo"),
        }
        changed = any(
            value is not None and previous.get(key) != value
            for key, value in ranked_fields.items()
        )

        state = {
            "player_tag": tag,
            "player_name": player.get("name"),
            "trophies": player.get("trophies"),
            **ranked_fields,
            "last_checked_at": now,
        }
        requests.post(
            f"{SUPABASE_URL}/rest/v1/player_tracking_state",
            headers=_tracking_headers("resolution=merge-duplicates,return=minimal"),
            params={"on_conflict": "player_tag"}, json=state, timeout=15,
        ).raise_for_status()

        if changed:
            requests.post(
                f"{SUPABASE_URL}/rest/v1/ranked_history",
                headers=_tracking_headers("return=minimal"),
                json={"player_tag": tag, "player_name": player.get("name"), **ranked_fields},
                timeout=15,
            ).raise_for_status()

        member_update = {key: value for key, value in ranked_fields.items() if value is not None}
        # A monthly reset can legitimately make current Ranked unavailable.
        # In that case clear the stale previous-season ELO instead of preserving it.
        if str(player.get("ranked_current") or "").casefold() == "non classificato":
            member_update["ranked_current"] = "Non classificato"
            member_update["ranked_current_elo"] = None
            member_update["ranked_season_peak"] = "Non classificato"
            member_update["ranked_season_peak_elo"] = None
        if player.get("ranked_career_peak"):
            member_update["ranked_peak"] = player["ranked_career_peak"]
        member_update["player_name"] = player.get("name")
        member_update["player_last_updated_at"] = now
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/community_members",
            headers=_tracking_headers("return=minimal"),
            params={"player_tag": f"eq.{tag}"}, json=member_update, timeout=15,
        ).raise_for_status()
        return True
    except Exception as e:
        print(f"ERRORE TRACKING COMPLETO {tag}:", repr(e), flush=True)
        return False


def get_ranked_history(player_tag, limit=10):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return []
    tag = player_tag.upper().replace("#", "").strip()
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/ranked_history",
            headers=_tracking_headers(),
            params={
                "player_tag": f"eq.{tag}",
                "select": "ranked_current,ranked_current_elo,ranked_season_peak,ranked_career_peak,recorded_at",
                "order": "recorded_at.desc",
                "limit": str(max(1, min(30, limit))),
            },
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"ERRORE STORICO RANKED {tag}:", repr(e), flush=True)
        return []


def automatic_trophy_monitor():
    import time

    time.sleep(60)

    while True:
        try:
            tags = get_tracked_player_tags()

            print(
                f"MONITOR TROFEI: {len(tags)} giocatori",
                flush=True
            )

            for tag in tags:
                try:
                    player = get_brawlzone_player(tag)

                    if player:
                        save_trophy_snapshot(
                            player["tag"],
                            player["name"],
                            player["trophies"]
                        )
                        save_player_tracking(player)

                        print(
                            f"TROFEI AGGIORNATI: {player["name"]} "
                            f"{player["trophies"]}",
                            flush=True
                        )

                    time.sleep(5)

                except Exception as e:
                    print(
                        f"ERRORE MONITOR TAG {tag}:",
                        repr(e),
                        flush=True
                    )

        except Exception as e:
            print(
                "ERRORE MONITOR TROFEI:",
                repr(e),
                flush=True
            )

        interval_minutes = max(5, int(os.environ.get("TRACKING_INTERVAL_MINUTES", "15")))
        time.sleep(interval_minutes * 60)



def create_trophy_chart(player_tag, player_name, history, days=30):
    try:
        if not history or len(history) < 2:
            return None

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        dates = []
        trophies = []

        for row in history:
            try:
                dt = datetime.fromisoformat(
                    row["recorded_at"].replace("Z", "+00:00")
                )

                if dt >= cutoff:
                    dates.append(dt)
                    trophies.append(int(row["trophies"]))

            except Exception:
                continue

        if len(dates) < 2:
            return None

        fig, ax = plt.subplots(figsize=(10, 5))

        ax.plot(
            dates,
            trophies,
            marker="o",
            linewidth=2
        )

        safe_name = player_name.encode(
            "ascii",
            "replace"
        ).decode("ascii")

        ax.set_title(
            f"Andamento trofei - {safe_name}"
        )

        ax.set_xlabel("Data")
        ax.set_ylabel("Trofei")
        ax.grid(True, alpha=0.3)

        same_day = dates[0].date() == dates[-1].date()

        if same_day:
            ax.xaxis.set_major_formatter(
                mdates.DateFormatter("%d/%m %H:%M")
            )
        else:
            ax.xaxis.set_major_formatter(
                mdates.DateFormatter("%d/%m")
            )

        min_trophies = min(trophies)
        max_trophies = max(trophies)

        if min_trophies == max_trophies:
            margin = 50
        else:
            margin = max(
                20,
                int((max_trophies - min_trophies) * 0.20)
            )

        ax.set_ylim(
            min_trophies - margin,
            max_trophies + margin
        )

        fig.autofmt_xdate()
        fig.tight_layout()

        image = io.BytesIO()

        fig.savefig(
            image,
            format="png",
            dpi=150
        )

        plt.close(fig)

        image.seek(0)
        image.name = f"{player_tag}_trofei.png"

        return image

    except Exception as e:
        print("ERRORE GRAFICO TROFEI:", repr(e), flush=True)
        return None


def create_aggregate_trophy_chart(scope_name, member_histories, days=30):
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        daily = {}
        for history in member_histories:
            per_day = {}
            for row in history or []:
                try:
                    dt = datetime.fromisoformat(row["recorded_at"].replace("Z", "+00:00"))
                    if dt < cutoff:
                        continue
                    per_day[dt.date()] = int(row["trophies"])
                except Exception:
                    continue
            for day, value in per_day.items():
                daily.setdefault(day, []).append(value)
        points = sorted((day, sum(values)) for day, values in daily.items() if values)
        if len(points) < 2:
            return None
        dates=[datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) for day,_ in points]
        totals=[value for _,value in points]
        fig,ax=plt.subplots(figsize=(10,5))
        ax.plot(dates, totals, marker="o", linewidth=2)
        ax.set_title(f"Andamento trofei - {scope_name}")
        ax.set_xlabel("Data"); ax.set_ylabel("Trofei totali"); ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
        margin=max(50,int((max(totals)-min(totals))*0.20)) if max(totals)!=min(totals) else 50
        ax.set_ylim(min(totals)-margin,max(totals)+margin)
        fig.autofmt_xdate(); fig.tight_layout()
        image=io.BytesIO(); fig.savefig(image,format="png",dpi=150); plt.close(fig)
        image.seek(0); image.name="andamento_community.png"
        return image
    except Exception as e:
        print("ERRORE GRAFICO AGGREGATO:",repr(e),flush=True); return None


def get_brawlzone_player(player_tag):
    # BrawlTrack is the primary live source. BrawlZone is only a fallback.
    primary = get_brawltrack_player(player_tag)
    if primary and primary.get("name") and primary.get("trophies") is not None:
        primary["club_name"] = primary.get("club") or primary.get("club_name")
        return primary
    try:
        tag = player_tag.upper().replace("#", "").strip()

        if not re.fullmatch(r"[0289PYLQGRJCUV]{3,15}", tag):
            return None

        response = requests.get(
            f"https://brawlzone.net/player/{tag}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20
        )

        if response.status_code != 200:
            return None

        decoded = html.unescape(response.text)

        title_match = re.search(
            rf"<title>(.*?) \(#{re.escape(tag)}\) · BrawlZone</title>",
            decoded,
            re.I | re.S
        )

        description_match = re.search(
            r"has ([\d,.]+) trophies and (\d+) brawlers",
            decoded,
            re.I
        )

        if not title_match or not description_match:
            return None

        def number(value):
            return int(re.sub(r"\D", "", value))

        def find_stat(label):
            match = re.search(
                rf"children\\\":\\\"{re.escape(label)}\\\".*?children\\\":\\\"([\d,.]+)\\\"",
                decoded,
                re.I | re.S
            )
            return number(match.group(1)) if match else None

        level_match = re.search(
            r"\\\"Level \\\",\s*(\d+)",
            decoded
        )

        prestige_match = re.search(
            r"\\\"Prestige \\\",\s*\\\"?(\d+)",
            decoded
        )

        player = {
            "name": title_match.group(1).strip(),
            "tag": f"#{tag}",
            "trophies": number(description_match.group(1)),
            "brawlers": int(description_match.group(2)),
            "level": int(level_match.group(1)) if level_match else None,
            "prestige": int(prestige_match.group(1)) if prestige_match else None,
            "wins_3v3": find_stat("3v3 wins"),
            "wins_solo": find_stat("Solo SD wins"),
            "wins_duo": find_stat("Duo SD wins")
        }
        player.update(extract_brawlzone_ranked(decoded))
        return player

    except Exception as e:
        print("ERRORE BRAWLZONE PLAYER:", repr(e), flush=True)
        return None


def format_number_it(value):
    if value is None:
        return "Non disponibile"

    return f"{value:,}".replace(",", ".")


community = CommunityFeatures(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
    get_brawlzone_player,
    get_trophy_history,
    calculate_trophy_changes,
    format_number_it,
    format_trophy_change,
    save_trophy_snapshot,
)


async def telegram_report_send(operation, label):
    """Retry only the unconfirmed operation; never restart the entire report.

    Telegram has no send idempotency key. A read timeout can produce a duplicate
    even when retrying only this part; numbered parts make that recognizable.
    """
    for attempt in range(3):
        try:
            await operation()
            print("LIVE_MAPS delivered:", label, flush=True)
            return True
        except RetryAfter as error:
            value = error.retry_after
            delay = value.total_seconds() if hasattr(value, "total_seconds") else float(value)
            delay = max(0, delay) + 1
        except BadRequest:
            print("LIVE_MAPS delivery rejected:", label, flush=True)
            return False
        except (TimedOut, NetworkError):
            delay = 2 ** (attempt + 1)
        except TelegramError as error:
            print("LIVE_MAPS delivery failed:", label, type(error).__name__, flush=True)
            return False
        print("LIVE_MAPS retry:", label, "attempt:", attempt + 1, flush=True)
        if attempt < 2:
            await asyncio.sleep(delay)
    print("LIVE_MAPS delivery exhausted:", label, flush=True)
    return False


async def deliver_live_map_report(message, context, report, rendered):
    timeouts = dict(read_timeout=30, write_timeout=60, connect_timeout=20, pool_timeout=20)
    missing = []
    if report["maps"]:
        csv_bytes = report_csv(report)
        # Recreate the stream for each attempt, including after a consumed upload.
        sent = await telegram_report_send(
            lambda: message.reply_document(
                document=io.BytesIO(csv_bytes), filename="statistiche_mappe.csv",
                caption="Statistiche complete di Brawl Planet. Segue il riepilogo in messaggi numerati.",
                **timeouts,
            ), "CSV",
        )
        if not sent:
            missing.append("CSV")
        await asyncio.sleep(1.1)
    chunks = list(telegram_text_chunks(rendered, limit=3200))
    for number, chunk in enumerate(chunks, 1):
        part = f"Parte {number}/{len(chunks)}"
        sent = await telegram_report_send(
            lambda text=part + "\n\n" + chunk: send_mode_aware_text(
                message, context, text, disable_web_page_preview=True
            ), part,
        )
        if not sent:
            missing.append(part)
        await asyncio.sleep(1.1)
    if missing:
        await telegram_report_send(
            lambda: message.reply_text(
                "Telegram non ha confermato la consegna di: " + ", ".join(missing) +
                ". Le parti confermate restano disponibili.", **timeouts
            ), "delivery status",
        )
    print("LIVE_MAPS delivery finished: parts=", len(chunks), "unconfirmed=", missing, flush=True)


def secondary_live_map_stats(event):
    """Try secondary sources for one verified event, keeping table cells intact."""
    map_name = event["event_map"]
    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": TAVILY_API_KEY,
                  "query": f'"{map_name}" Brawl Stars map win rate pick rate statistics',
                  "include_domains": ["brawlify.com", "brawltime.ninja", "noff.gg"],
                  "search_depth": "advanced", "max_results": 4,
                  "include_raw_content": True, "include_answer": False},
            timeout=20,
        )
        response.raise_for_status()
        for result in response.json().get("results", []):
            raw = result.get("raw_content") or result.get("content") or ""
            title = result.get("title") or ""
            url = result.get("url") or ""
            if map_name.casefold() not in title.casefold():
                continue
            # Do not silently substitute Ranked statistics for a live event.
            if re.search(r"\b(rank(?:ed)?|classificata)\b", title, re.I):
                continue
            headers = None
            rows = []
            for line in raw.splitlines():
                if "|" not in line:
                    if rows:
                        break
                    continue
                cells = [clean_brawlplanet_cell(c) for c in line.strip().strip("|").split("|")]
                if any("brawler" in c.casefold() for c in cells) and any(
                    re.search(r"win|vitt", c, re.I) for c in cells
                ):
                    headers = cells
                    continue
                if not headers or len(cells) != len(headers):
                    continue
                if not cells[0] or re.fullmatch(r"[-: ]+", cells[0]):
                    continue
                if not all(re.fullmatch(r"[0-9.,]+\s*%?", c) for c in cells[1:]):
                    continue
                # Preserve the source's labels: wins and win percentages differ.
                rows.append(cells[0].replace("Mister P", "Mr. P") + " — " + " · ".join(
                    f"{h}: {v}" for h, v in zip(headers[1:], cells[1:])
                ))
            if rows:
                print("LIVE_MAPS secondary table:", map_name, url, len(rows), flush=True)
                return ("Fonte secondaria — statistiche pubblicate sulla mappa; "
                        "finestra temporale e campione non verificati:\n" +
                        "\n".join("• " + row for row in rows[:5]) + "\n" + url)
        print("LIVE_MAPS no verified secondary table:", map_name, flush=True)
    except Exception as error:
        print("LIVE_MAPS secondary failure:", map_name, type(error).__name__, flush=True)
    return "Ho consultato le fonti secondarie: nessuna tabella utilizzabile per questa mappa. Le altre mappe restano disponibili."


async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.text:
        return

    # Explicit mode on the current request always wins. Otherwise, replying
    # directly to a voice/audio message sent by Sens GPT inherits voice mode.
    explicit_mode = request_voice_mode(message.text)
    has_explicit_mode = bool(re.search(
        r"\\brispondi\\s+(?:a\\s+voce|(?:a\\s+)?testo|testo\\s*(?:\\+|e)?\\s*voce|voce\\s*(?:\\+|e)\\s*testo)\\s*$",
        message.text.strip().casefold(),
    ))
    replied = message.reply_to_message
    reply_to_bot_voice = bool(
        replied
        and replied.from_user
        and replied.from_user.id == context.bot.id
        and (replied.voice is not None or replied.audio is not None)
    )
    context.user_data["_request_voice_mode"] = (
        explicit_mode if has_explicit_mode
        else ("voice" if reply_to_bot_voice else "text")
    )

    community.track_activity(message)

    bot_username = context.bot.username

    if not bot_username:
        return

    mentioned = (
        f"@{bot_username.lower()}" in message.text.lower()
    )

    is_reply = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user is not None
        and message.reply_to_message.from_user.id == context.bot.id
    )

    is_voice_input = bool(context.user_data.pop("_voice_input", False))
    if not mentioned and not is_reply and not is_voice_input:
        return

    question = message.text
    question = re.sub(
        r"\\s+rispondi\\s+(?:a\\s+voce|(?:a\\s+)?testo|testo\\s*(?:\\+|e)?\\s*voce|voce\\s*(?:\\+|e)\\s*testo)\\s*$",
        "",
        question,
        flags=re.I,
    ).strip()

    if mentioned:
        question = question.replace(
            f"@{bot_username}",
            ""
        ).strip()

    if await handle_premium_command(
        message, context, question, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
    ):
        return

    if await community.continue_registration(message, context):
        return

    if await community.continue_recruitment(message, context):
        return

    if await community.handle_command(message, context, question):
        return

    meta_chart_match=re.fullmatch(r"(?:grafico|andamento)\\s+(?:meta\\s+)?(?:di\\s+)?(.+?)(?:\\s+(win rate|utilizzo|pick rate|star rate))?",question.strip(),re.I)
    if meta_chart_match and any(x in question.casefold() for x in ("meta","win rate","utilizzo","pick rate","star rate")):
        target=meta_chart_match.group(1).strip()
        metric=(meta_chart_match.group(2) or "meta").casefold()
        ctx=get_brawltrack_meta_context("meta "+target)
        prefix="DATI META STRUTTURATI E LOCALIZZATI (PRIORITARI):\\n"
        try:
            rows=json.loads(ctx[len(prefix):]) if ctx.startswith(prefix) else []
            if len(rows)!=1:raise ValueError("brawler")
            row=rows[0]
            labels=[];values=[]
            if metric in ("win rate","meta"):
                labels.append("Win rate");values.append(float(row.get("win_rate") or 0))
            if metric in ("utilizzo","pick rate","meta"):
                labels.append("Utilizzo");values.append(float(row.get("meta_usage") or 0))
            if metric in ("star rate","meta"):
                labels.append("Star rate");values.append(float(row.get("star_rate") or 0))
            fig,ax=plt.subplots(figsize=(7,4.5));bars=ax.bar(labels,values);ax.set_ylabel("Percentuale (%)");ax.set_title("Meta - "+str(row.get("brawler") or target))
            upper=max(values) if values else 0;ax.set_ylim(0,max(100,upper*1.2))
            for bar,value in zip(bars,values):ax.text(bar.get_x()+bar.get_width()/2,bar.get_height()+max(1,upper*.02),f"{value:.2f}%",ha="center",va="bottom")
            fig.tight_layout();image=io.BytesIO();fig.savefig(image,format="png",dpi=150);plt.close(fig);image.seek(0);image.name="meta_brawler.png"
            await context.bot.send_photo(chat_id=message.chat_id,photo=image,caption=f"Meta di {row.get('brawler')}: dati aggiornati.")
        except Exception as exc:
            print("ERRORE GRAFICO META:",repr(exc),flush=True);await send_mode_aware_text(message, context, "Non riesco a creare il grafico meta per questo Brawler.")
        return

    detail_chart_match=re.fullmatch(r"(?:grafico|andamento)\\s+(?:di\\s+)?(.+?)\\s+(mappe|modalità|modalita|build)",question.strip(),re.I)
    if detail_chart_match:
        target=detail_chart_match.group(1).strip();kind=detail_chart_match.group(2).casefold()
        ctx=get_brawltrack_meta_context("meta "+target);prefix="DATI META STRUTTURATI E LOCALIZZATI (PRIORITARI):\\n"
        try:
            rows=json.loads(ctx[len(prefix):]) if ctx.startswith(prefix) else []
            if len(rows)!=1:raise ValueError("brawler")
            row=rows[0];labels=[];values=[];ylabel="Win rate (%)"
            if kind=="mappe":
                for item in row.get("recommended_active_map") or []:
                    labels.append(str(item.get("map") or ""));values.append(float(item.get("win_rate") or 0))
                if not labels:raise ValueError("maps")
            elif kind in ("modalità","modalita"):
                for item in row.get("top_3_mode_maps") or []:
                    labels.append(str(item.get("mode") or ""));values.append(1)
                ylabel="Classifica";values=list(range(len(labels),0,-1))
                if not labels:raise ValueError("modes")
            else:
                ylabel="Utilizzo (%)"
                for item in row.get("popular_builds") or []:
                    rate=item.get("use_rate")
                    if rate is None:continue
                    comps=[str(x.get("name_it") or "") for x in item.get("components") or [] if x.get("name_it")]
                    labels.append(" + ".join(comps) or "Build "+str(item.get("rank") or ""));values.append(float(rate))
                if not labels:raise ValueError("builds")
            fig,ax=plt.subplots(figsize=(8,4.8));bars=ax.bar(labels,values);ax.set_ylabel(ylabel);ax.set_title(str(row.get("brawler") or target)+" - "+kind.capitalize());ax.tick_params(axis="x",rotation=20)
            upper=max(values) if values else 0
            for bar,value in zip(bars,values):
                suffix="%" if ylabel.endswith("(%)") else ""
                ax.text(bar.get_x()+bar.get_width()/2,bar.get_height()+max(.05,upper*.02),f"{value:.2f}{suffix}" if suffix else str(int(value)),ha="center",va="bottom")
            fig.tight_layout();image=io.BytesIO();fig.savefig(image,format="png",dpi=150);plt.close(fig);image.seek(0);image.name="dettaglio_meta_brawler.png"
            await context.bot.send_photo(chat_id=message.chat_id,photo=image,caption=f"{kind.capitalize()} di {row.get('brawler')}: dati aggiornati.")
        except Exception as exc:
            print("ERRORE GRAFICO META DETTAGLIO:",repr(exc),flush=True);await send_mode_aware_text(message, context, "Non ci sono dati sufficienti per creare questo grafico.")
        return

    natural_chart_match = re.fullmatch(
        r"(?:fammi\\s+vedere\\s+|mostrami\\s+|crea(?:mi)?\\s+|genera(?:mi)?\\s+)?(?:il\\s+)?(?:grafico|andamento)(?:\\s+(?:dei\\s+)?trofei)?\\s+(?:di\\s+)?(.+?)(?:\\s+(?:negli\\s+)?ultimi)?\\s+(7|15|30|90)\\s+giorni",
        question.strip(), re.I
    )
    if natural_chart_match:
        who=natural_chart_match.group(1).strip()
        days=int(natural_chart_match.group(2))
        normalized=re.sub(r"[^a-z0-9]+","",who.casefold())
        member=None
        for candidate in community.members(message.chat_id):
            aliases=[candidate.get("player_name"),candidate.get("display_name"),candidate.get("telegram_username")]
            if any(re.sub(r"[^a-z0-9]+","",str(x).casefold())==normalized for x in aliases if x):
                member=candidate;break
        if not member or not member.get("player_tag"):
            await send_mode_aware_text(message, context, "Non trovo un giocatore registrato con questo nome.")
            return
        player=get_brawlzone_player(member["player_tag"])
        if not player:
            await send_mode_aware_text(message, context, "Giocatore non trovato.")
            return
        save_trophy_snapshot(player["tag"],player["name"],player["trophies"])
        history=get_trophy_history(player["tag"],days=max(days,90))
        chart=create_trophy_chart(player["tag"],player["name"],history,days=days)
        if not chart:
            await send_mode_aware_text(message, context, f"Non ci sono ancora abbastanza dati per creare il grafico degli ultimi {days} giorni.")
            return
        await context.bot.send_photo(chat_id=message.chat_id,photo=chart,caption=f"Andamento trofei di {player['name']}\\nPeriodo: ultimi {days} giorni\\nTrofei attuali: {format_number_it(player['trophies'])}")
        return

    chart_match = re.fullmatch(
        r"grafico(?:\s+(7|15|30|90))?\s+#?([0289PYLQGRJCUV]{3,15})",
        question.strip(),
        re.I
    )

    if chart_match:
        days = int(chart_match.group(1) or 30)
        player_tag = chart_match.group(2).upper()

        await context.bot.send_chat_action(
            chat_id=message.chat_id,
            action="upload_photo"
        )

        player = get_brawlzone_player(player_tag)

        if not player:
            await context.bot.send_message(
                chat_id=message.chat_id,
                text="Giocatore non trovato."
            )
            return

        save_trophy_snapshot(
            player["tag"],
            player["name"],
            player["trophies"]
        )

        history = get_trophy_history(
            player["tag"],
            days=max(days, 90)
        )

        chart = create_trophy_chart(
            player["tag"],
            player["name"],
            history,
            days=days
        )

        if not chart:
            await context.bot.send_message(
                chat_id=message.chat_id,
                text=(
                    f"Non ci sono ancora abbastanza dati per creare "
                    f"il grafico degli ultimi {days} giorni."
                )
            )
            return

        await context.bot.send_photo(
            chat_id=message.chat_id,
            photo=chart,
            caption=(
                f"Andamento trofei di {player["name"]}\n"
                f"Periodo: ultimi {days} giorni\n"
                f"Trofei attuali: {format_number_it(player["trophies"])}"
            )
        )
        return

    aggregate_chart_match = re.fullmatch(
        r"grafico\s+(community|titani(?: abusivi)?|tamarri(?: abusivi)?|tornadi(?: abusivi)?|talenti(?: abusivi)?)(?:\s+(7|15|30|90))?",
        question.strip(), re.I
    )
    if aggregate_chart_match:
        scope_key=aggregate_chart_match.group(1).lower()
        days=int(aggregate_chart_match.group(2) or 30)
        club_name=None if scope_key=="community" else community.CLUB_ALIASES.get(scope_key)
        histories=[]
        included=0
        for member in community.members(message.chat_id):
            tag=member.get("player_tag")
            if not tag: continue
            if club_name:
                player=get_brawlzone_player(tag)
                actual=community._club_name_from_player(player) if player else None
                if (actual or "").casefold()!=club_name.casefold(): continue
            history=get_trophy_history(tag,days=max(days,90))
            if history:
                histories.append(history); included+=1
        chart=create_aggregate_trophy_chart(club_name or "COMMUNITY ABUSIVI",histories,days)
        if not chart:
            await send_mode_aware_text(message, context, f"Non ci sono ancora abbastanza dati per il grafico degli ultimi {days} giorni.")
            return
        await context.bot.send_photo(chat_id=message.chat_id,photo=chart,caption=f"Andamento trofei - {club_name or 'COMMUNITY ABUSIVI'}\nPeriodo: ultimi {days} giorni\nGiocatori inclusi: {included}")
        return

    if re.fullmatch(r"(?:quante\\s+)?generazion(?:e|i)(?:\\s+(?:ai|profilo ai))?(?:\\s+(?:mi\\s+)?(?:rimangono|rimaste|restano|restanti))?", question.strip(), re.I) or re.fullmatch(r"(?:quante\\s+)?generazion(?:e|i)\\s+(?:ho|mi restano|mi rimangono)", question.strip(), re.I):
        await handle_premium_command(message, context, "generazioni", SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        return

    profile_image_match = re.fullmatch(
        r"(?:profilo ai|profilo grafico|immagine profilo|profile image)(?:\s+(sorprendimi|brawl|cinematic|pixar|epico|fantascienza|fantasy))?\s*#?([0289PYLQGRJCUV]{3,15})(?:\s+(?:con\s+)?(.+?))?(?:\s+ambientazione\s+(.+))?",
        question.strip(), re.I
    )
    if profile_image_match:
        category_map = {
            "sorprendimi": "random",
            "brawl": "official",
            "cinematic": "cinematic",
            "pixar": "pixar",
            "epico": "epic",
            "fantascienza": "scifi",
            "fantasy": "fantasy",
        }
        mode_key = re.sub(r"\s+", " ", (profile_image_match.group(1) or "sorprendimi").strip().lower())
        category = category_map.get(mode_key, "random")
        requested_brawler = (profile_image_match.group(3) or "").strip() or None
        custom_environment = (profile_image_match.group(4) or "").strip() or None
        # Backward-compatible natural syntax: `profilo ai ... #TAG al cinema`
        # means environment, never rendering style.
        if requested_brawler and requested_brawler.casefold() == "al cinema" and not custom_environment:
            custom_environment = "al cinema"
            requested_brawler = None
        player_environment = custom_environment
        telegram_user_id = message.from_user.id

        try:
            quota = await asyncio.to_thread(quota_status, telegram_user_id)
        except Exception as error:
            print("AI PROFILE QUOTA CHECK:", repr(error), flush=True)
            await send_mode_aware_text(message, context, "Il controllo della quota AI non è disponibile. Riprova tra poco.")
            return

        if not quota["unlimited"] and quota["used"] >= quota["limit"]:
            await message.reply_text(
                f"Hai già usato le {quota['limit']} generazioni Profilo AI disponibili questo mese. "
                "Puoi chiedere al Presidente Sens o ad Anna di generarlo per te."
            )
            return

        tag = profile_image_match.group(2).upper()
        player = await asyncio.to_thread(get_brawltrack_player, tag)
        if not player:
            player = await asyncio.to_thread(get_brawlzone_player, tag)
        if not player:
            await send_mode_aware_text(message, context, "Non riesco a trovare questo giocatore.")
            return

        if str(player.get("tag") or "").upper().replace("#", "") == "2VQYLG0RU8":
            player["club"] = player["club_name"] = "TALENTI ABUSIVI"

        member_data = community.get_member_by_player_tag(message.chat_id, player.get("tag")) or {}
        for key in (
            "ranked_current", "ranked_season_peak", "ranked_career_peak", "ranked_peak",
            "ranked_current_elo", "ranked_season_peak_elo", "ranked_career_peak_elo", "prestige"
        ):
            if not player.get(key) and member_data.get(key):
                player[key] = member_data[key]

        # Resolve a REAL visual reference before spending any AI generation.
        # Manual `con BRAWLER` has priority; otherwise recover the public
        # profile-icon id and map it to its associated Brawler when possible.
        reference_ok, reference_error = await asyncio.to_thread(
            resolve_ai_brawler_reference, player, requested_brawler
        )
        if player_environment:
            player["requested_environment"] = player_environment
        if not reference_ok:
            await send_mode_aware_text(message, context, reference_error or "Riferimento Brawler non disponibile.")
            return

        await context.bot.send_chat_action(chat_id=message.chat_id, action="upload_photo")
        progress = await message.reply_text("Sto creando il tuo Profilo AI TITANI ABUSIVI…")
        try:
            scene_bytes, scene = await asyncio.to_thread(generate_scene, player, category)
            card, card_filename = await asyncio.to_thread(overlay_stats, scene_bytes, player)
            # Pass the file object itself to Telegram. Passing the complete
            # (BytesIO, filename) tuple makes the HTTP layer try to serialize
            # BytesIO as JSON and the upload fails.
            card.name = card_filename
            card.seek(0)
            await context.bot.send_photo(
                chat_id=message.chat_id,
                photo=card,
                caption=(
                    f"Profilo AI TITANI ABUSIVI • {player.get('name','Giocatore')}\n"
                    f"Scena: {scene.get('place','cinematografica')}"
                )
            )
            quota = await asyncio.to_thread(consume_quota, telegram_user_id)
            quota_text = (
                "Profilo AI • quota illimitata" if quota["unlimited"]
                else f"Profilo AI generato • Questo mese {quota['used']}/{quota['limit']}"
            )
            await progress.edit_text(quota_text)
        except Exception as error:
            print("AI PROFILE GENERATION:", repr(error), flush=True)
            await progress.edit_text("La generazione AI non è riuscita. La quota non è stata consumata.")
        return

    stats_match = re.fullmatch(
        r"(?:stats|statistiche|profilo|scheda|status(?:\s+(?:del\s+)?giocatore)?|stato(?:\s+(?:del\s+)?giocatore)?)\s*(?:di\s+)?#?([0289PYLQGRJCUV]{3,15})",
        question.strip(),
        re.I
    )
    if not stats_match:
        # Natural-language safety net: a player tag plus a clear profile intent
        # must never be sent to Gemini.
        tag_match = re.search(r"#([0289PYLQGRJCUV]{3,15})", question, re.I)
        intent = re.search(r"\b(status|stato|statistiche|stats|profilo|scheda|giocatore)\b", question, re.I)
        if tag_match and intent:
            stats_match = tag_match

    if stats_match:
        player_tag = stats_match.group(1).upper()

        await context.bot.send_chat_action(
            chat_id=message.chat_id,
            action="typing"
        )

        player = get_brawlzone_player(player_tag)

        if not player:
            await context.bot.send_message(
                chat_id=message.chat_id,
                text=(
                    "Non riesco a trovare questo giocatore.\n"
                    "Controlla che il tag sia corretto e riprova."
                )
            )
            return

        save_trophy_snapshot(
            player["tag"],
            player["name"],
            player["trophies"]
        )

        history = get_trophy_history(
            player["tag"],
            days=91
        )

        changes = calculate_trophy_changes(
            history,
            player["trophies"]
        )

        member_data = community.get_member_by_player_tag(
            message.chat_id,
            player["tag"]
        )

        if str(player.get("tag") or "").upper().replace("#", "") == "2VQYLG0RU8":
            player["club"] = "TALENTI ABUSIVI"
            player["club_name"] = "TALENTI ABUSIVI"

        ranked_current = (
            player.get("ranked_current")
            or (member_data or {}).get("ranked_current")
            or "Non disponibile"
        )
        ranked_season_peak = (
            player.get("ranked_season_peak")
            or (member_data or {}).get("ranked_season_peak")
            or "Non disponibile"
        )
        ranked_peak = (
            player.get("ranked_career_peak")
            or player.get("ranked_peak")
            or (member_data or {}).get("ranked_peak")
            or "Non disponibile"
        )

        text = (
            f"{player['name']}\n"
            f"Tag: {player['tag']}\n"
            f"Club: {player.get('club') or player.get('club_name') or (member_data or {}).get('club_name') or 'Senza club / non disponibile'}\n\n"
            f"Trofei: {format_number_it(player['trophies'])}\n"
            f"Brawler: {format_number_it(player['brawlers'])}\n"
            f"Livello: {format_number_it(player['level'])}\n"
            f"Prestigio: {format_number_it(player['prestige'])}\n"
            f"Ranked attuale: {ranked_current}\n"
            f"Record stagione: {ranked_season_peak}\n"
            f"Record massimo: {ranked_peak}\n\n"
            f"Vittorie:\n"
            f"- 3v3: {format_number_it(player['wins_3v3'])}\n"
            f"- Solo: {format_number_it(player['wins_solo'])}\n"
            f"- Duo: {format_number_it(player['wins_duo'])}\n\n"
            f"Andamento trofei:\n"
            f"- Oggi: {format_trophy_change(changes.get('today'))}\n"
            f"- 7 giorni: {format_trophy_change(changes.get('7d'))}\n"
            f"- 15 giorni: {format_trophy_change(changes.get('15d'))}\n"
            f"- 30 giorni: {format_trophy_change(changes.get('30d'))}\n"
            f"- 90 giorni: {format_trophy_change(changes.get('90d'))}"
        )

        await send_mode_aware_text(message, context, text)
        return

    ranked_match = re.fullmatch(
        r"(?:ranked|classificata)(?:\s+storico|\s+cronologia)?\s+#?([0289PYLQGRJCUV]{3,15})",
        question.strip(),
        re.I,
    )
    ranked_history_match = re.fullmatch(
        r"(?:storico|cronologia)\s+(?:ranked|classificata)\s+#?([0289PYLQGRJCUV]{3,15})",
        question.strip(),
        re.I,
    )
    if ranked_match or ranked_history_match:
        player_tag = (ranked_match or ranked_history_match).group(1).upper()
        player = get_brawlzone_player(player_tag)
        if not player:
            await send_mode_aware_text(message, context, "Non riesco a trovare questo giocatore.")
            return
        save_player_tracking(player)
        lines = [
            f"CLASSIFICATA - {player['name']}",
            f"Tag: {player['tag']}",
            "",
            f"Attuale: {player.get('ranked_current') or 'Non disponibile'}"
            f" ({format_number_it(player.get('ranked_current_elo'))} ELO)",
            f"Record stagione: {player.get('ranked_season_peak') or 'Non disponibile'}"
            f" ({format_number_it(player.get('ranked_season_peak_elo'))} ELO)",
            f"Record massimo: {player.get('ranked_career_peak') or 'Non disponibile'}"
            f" ({format_number_it(player.get('ranked_career_peak_elo'))} ELO)",
        ]
        wants_history = bool(ranked_history_match) or any(
            word in question.lower() for word in ("storico", "cronologia")
        )
        if wants_history:
            history = get_ranked_history(player_tag)
            lines.extend(["", "ULTIME VARIAZIONI"])
            if history:
                for row in history:
                    recorded = datetime.fromisoformat(row["recorded_at"].replace("Z", "+00:00"))
                    lines.append(
                        f"- {recorded.astimezone(ROME).strftime('%d/%m/%Y %H:%M')}: "
                        f"{row.get('ranked_current') or 'Non disponibile'} "
                        f"({format_number_it(row.get('ranked_current_elo'))} ELO)"
                    )
            else:
                lines.append("Lo storico inizierà dal primo aggiornamento automatico.")
        await send_mode_aware_text(message, context, "\n".join(lines))
        return

    original_message = ""

    if is_reply:
        replied_message = message.reply_to_message

        if replied_message and replied_message.text:
            original_message = replied_message.text.strip()

    if not question:
        if original_message:
            question = (
                "Analizza e rispondi al seguente messaggio:\n"
                f"{original_message}"
            )
        else:
            await context.bot.send_message(
                chat_id=message.chat_id,
                text=(
                    "Sono Sens GPT, l'AI ufficiale dei TITANI ABUSIVI. "
                    "Fammi una domanda su Brawl Stars."
                )
            )
            return

    if original_message and question:
        question_for_ai = (
            "L'utente sta rispondendo a questo messaggio:\n"
            f"\"{original_message}\"\n\n"
            "La sua domanda o risposta è:\n"
            f"\"{question}\"\n\n"
            "Usa il messaggio originale come contesto."
        )
    else:
        question_for_ai = question

    if is_all_maps_request(question):
        try:
            await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
            report = await asyncio.to_thread(
                collect_report, get_game_context(question), secondary=secondary_live_map_stats
            )
            rendered = render_report(report)
            print("LIVE_MAPS report:", len(report["events"]), "maps; chars:", len(rendered), flush=True)
        except Exception as error:
            print("LIVE_MAPS report failure:", type(error).__name__, str(error), flush=True)
            await send_mode_aware_text(message, context, "Il caricamento delle statistiche ha incontrato un errore. Non ho ancora una rotazione verificata da mostrarti.")
            return
        # No AI rewriting, translation pass, or all-or-nothing prose validator.
        await deliver_live_map_report(message, context, report, rendered)
        return

    web_context = ""
    web_sources = []
    web_images = []
    structured_stats_context = ""
    rotation_instruction = ""

    if needs_web_search(question_for_ai):
        try:
            search_data = web_search(question_for_ai)

            search_results = search_data.get("results", [])

            if is_current_meta_query(question_for_ai):
                search_results = sorted(
                    search_results,
                    key=lambda result: meta_source_priority(
                        result.get("url", "")
                    )
                )

            web_context, web_sources = build_web_context(
                search_results,
                exhaustive=is_all_maps_request(question_for_ai)
            )

            structured_stats_context = brawlplanet_structured_stats(
                search_results
            )
            if structured_stats_context:
                structured_stats_context = compact_source_text(
                    structured_stats_context,
                    80000 if is_all_maps_request(question_for_ai) else 30000
                )
                web_context = (
                    "\nDATI STRUTTURATI BRAWL PLANET (priorità per la risposta):\n"
                    + structured_stats_context
                    + "\nFINE DATI STRUTTURATI BRAWL PLANET\n"
                    + web_context
                )

            rotation_manifest = search_data.get("brawlplanet_rotation", [])
            if is_all_maps_request(question_for_ai):
                if rotation_manifest:
                    rotation_rows = "\n".join(
                        f"- {entry['mode']}: {entry['map']}"
                        for entry in rotation_manifest
                    )
                    rotation_instruction = (
                        "\nELENCO MAPPE ATTIVE DA BRAWL PLANET (OBBLIGATORIO):\n"
                        f"{rotation_rows}\n"
                        "Devi coprire ogni riga dell'elenco. Non aggiungere mappe "
                        "non presenti e non ridurre la risposta a una sola mappa.\n"
                    )
                else:
                    rotation_instruction = (
                        "\nBRAWL PLANET NON HA RESTITUITO UN ELENCO STRUTTURATO "
                        "DELLE MAPPE ATTIVE: non inventare una rotazione.\n"
                    )

            current_map = extract_brawl_ball_map(search_data)
            verified_comp = []

            if current_map:
                verified_comp = get_verified_map_comp(current_map)

            if current_map:
                try:
                    comp_data = search_map_comp(current_map)

                    web_context += f"\nDATI META PER MAPPA: {current_map}\n"


                    for result in comp_data.get("results", []):
                        title = result.get("title", "")
                        content = result.get("content", "")
                        raw_content = result.get("raw_content", "") or ""
                        url = result.get("url", "")

                        combined_text = (title + "\n" + content + "\n" + raw_content).lower()
                        map_name_lower = current_map.lower()
                        url_lower = url.lower()

                        if map_name_lower not in combined_text or "brawl ball" not in combined_text:
                            print("FONTE COMP SCARTATA:", url, flush=True)
                            continue

                        if "brawltime.ninja/tier-list/mode/brawl-ball" in url_lower and "/map/" not in url_lower:
                            print("FONTE COMP GENERICA SCARTATA:", url, flush=True)
                            continue

                        if title or content:
                            web_context += (
                                f"Titolo: {title}\n"
                                f"Contenuto: {content}\n"
                                f"Contenuto completo: {raw_content[:5000]}\n"
                                f"Fonte: {url}\n"
                                f"---\n"
                            )

                        if url:
                            web_sources.append(url)

                    print("MAPPA ESTRATTA:", current_map, flush=True)

                except Exception as e:
                    print("ERRORE RICERCA COMP MAPPA:", repr(e), flush=True)


            try:
                image_data = (
                    {"images": []}
                    if is_all_maps_request(question_for_ai)
                    else image_search(question_for_ai)
                )
                for image in image_data.get("images", []):
                    if isinstance(image, str) and image.startswith("http"):
                        web_images.append({"url": image, "description": ""})
                    elif isinstance(image, dict):
                        image_url = image.get("url") or image.get("image_url")
                        description = image.get("description") or image.get("title") or ""
                        if image_url and image_url.startswith("http"):
                            web_images.append({"url": image_url, "description": description})

            except Exception as e:
                print("ERRORE RICERCA IMMAGINI:", repr(e), flush=True)
            print(
                f"TAVILY: trovati {len(web_sources)} risultati e {len(web_images)} immagini",
                flush=True
            )

        except Exception as e:
            print(
                "ERRORE TAVILY:",
                repr(e),
                flush=True
            )

    try:
        brawltrack_meta_context = get_brawltrack_meta_context(question_for_ai)
        deterministic_meta = render_structured_brawler_meta(brawltrack_meta_context)
        if deterministic_meta:
            # Structured meta replies must obey the same per-request
            # text / voice / text+voice rule as normal AI replies.
            await send_mode_aware_text(
                message,
                context,
                deterministic_meta,
                disable_web_page_preview=True,
            )
            return
        if brawltrack_meta_context:
            web_context = brawltrack_meta_context + "\n\n" + web_context
        if web_context:
            instructions = (
                "Sei Sens GPT, l'intelligenza artificiale ufficiale "
                "della community TITANI ABUSIVI.\n\n"

                "Sei specializzato in Brawl Stars e devi conoscere "
                "il gioco in modo approfondito.\n\n"

                "Sono state effettuate ricerche web specifiche per "
                "rispondere alla domanda dell'utente.\n\n"

                "USA LE INFORMAZIONI WEB FORNITE.\nNON MOSTRARE MAI LE FONTI, GLI URL O I LINK ALL UTENTE.\n\n"
                "REGOLE PER META E DATI ATTUALI:\n"
                "- Se la domanda riguarda meta, tier list, Ranked, migliori Brawler, pick rate, win rate, buff, nerf o bilanciamenti attuali, considera la ricerca web come obbligatoria.\n"
                "- Per affermazioni sul meta attuale NON usare la memoria interna del modello come fonte principale.\n"
                "- Dai priorità ai risultati più recenti e coerenti con l'ultima patch o stagione verificabile.\n"
                "- Gerarchia fonti per il meta: 1) BrawlTrack per meta, mappe, statistiche e composizioni; 2) Supercell/Brawl Stars ufficiale per patch, buff, nerf e modifiche; 3) Brawl Planet solo come fallback quando BrawlTrack non ha il dato; 4) Brawlify, Brawl Time Ninja e Noff solo come supporto secondario.\n"
                "- Una fonte ufficiale stabilisce cosa è cambiato, ma il meta reale va valutato anche con statistiche e dati competitivi aggiornati.\n"
                "- REGOLE BILANCIAMENTO: cita un buff, nerf, rework o altra modifica solo se una fonte ufficiale Supercell/Brawl Stars aggiornata conferma esplicitamente quella modifica per quel singolo Brawler.\n"
                "- Non dedurre mai buff o nerf da win rate, utilizzo, posizione nella tier list, variazioni del meta o prestazioni statistiche.\n"
                "- Verifica ogni Brawler separatamente: una patch che modifica altri Brawler non autorizza ad attribuire la stessa modifica a nomi non citati.\n"
                "- Se la modifica non è verificata individualmente, non menzionarla nella risposta.\n"
                "- Confronta più risultati quando possibile: non dichiarare un Brawler 'meta' basandoti su una sola fonte debole.\n"
                "- Se i risultati web non permettono di verificare il meta attuale con sufficiente affidabilità, dichiaralo chiaramente invece di indovinare.\n"
                "- Se l'utente chiede cosa pushare o come pushare un Brawler, struttura la risposta con: modalità consigliate, mappe favorevoli attuali se verificabili, configurazione consigliata, comp/sinergie, matchup da evitare e un piano pratico di push.\n"
                "- DISTINZIONE LADDER/CLASSIFICATA OBBLIGATORIA:\n"
                "  - Ladder/trofei: usa esclusivamente statistiche trophy-ladder e mappe della rotazione a trofei attuale.\n"
                "  - Classificata/Ranked: usa esclusivamente statistiche Ranked e mappe del pool Classificata attuale; considera draft, ban, counterpick e sinergie.\n"
                "  - Non mescolare mai percentuali Ladder e Ranked nella stessa raccomandazione.\n"
                "  - Non fondere statistiche appartenenti a mappe diverse, modalità diverse o Brawler diversi.\n"
                "  - Se la domanda non specifica Ladder o Classificata e i due contesti portano a consigli diversi, separa la risposta in due sezioni: Ladder e Classificata.\n"
                "  - Una richiesta sulle mappe di oggi, senza parole come Classificata, Ranked, draft o ban, riguarda prima di tutto la rotazione eventi/trofei: non presentarla come rotazione Classificata.\n"
                "  - Se una mappa è indicata come solo Ranked, non proporla per Ladder. Se è archiviata o fuori pool, non proporla come attuale.\n"
                "- META GENERALE: per domande come 'chi è meta?', 'tier list', 'migliori brawler adesso' usa BrawlTrack come fonte primaria e confronta con gli ultimi bilanciamenti ufficiali Supercell; usa Brawl Planet solo come fallback.\n"
                "- RISPOSTA META GENERICA: se l utente chiede semplicemente il meta attuale, chi è meta o i migliori Brawler senza specificare mappa, modalità, Ladder/Classificata o statistiche, mostra SOLO i 5 migliori Brawler verificabili, numerati da 1 a 5, senza percentuali, win rate, pick rate, utilizzo, Star Player, spiegazioni lunghe o altre metriche.\n"
                "- Dopo la Top 5 aggiungi una sola domanda breve: Vuoi sapere su quale mappa o modalità rendono meglio, distinguendo tra Ladder e Classificata, oppure vuoi le statistiche dettagliate di un Brawler?\n"
                "- Se l utente specifica mappa, modalità, Ladder o Classificata, rispondi direttamente nel contesto richiesto e non applicare il formato Top 5 generale quando servono dati contestuali.\n"
                "- Se l utente chiede statistiche dettagliate di un Brawler, allora puoi mostrare le metriche disponibili e verificate per quel Brawler, mantenendo separati Ladder e Classificata.\n"
                "- RISPOSTA BUILD/CONFIGURAZIONE BRAWLER: sii compatto. Non spiegare gli effetti di gadget, abilità stellari, equipaggiamenti o overdrive salvo richiesta esplicita dell utente.\n"
                "- Per ogni configurazione usa i componenti name_it dei DATI BRAWLTRACK STRUTTURATI E LOCALIZZATI e mostra il relativo Use Rate quando disponibile.\n"
                "- Non chiamare una configurazione migliore se BrawlTrack la indica soltanto come più usata: scrivi Configurazione più usata e la sua percentuale.\n"
                "- Dopo la configurazione mostra una sola Modalità migliore e subito sotto la migliore mappa disponibile per quella modalità usando recommended_mode_map. Non mostrare percentuali.\n"
                "- Se esistono più configurazioni popolari, associa ciascuna a modalità/mappe solo quando la fonte fornisce realmente quel collegamento. Se BrawlTrack non collega direttamente una build a una singola mappa o modalità, non inventare il collegamento: mostra Configurazioni più usate e, separatamente, Modalità migliori e Mappe migliori.\n"
                "- In Mappe migliori mostra la relativa modalità ma nessuna percentuale o numero di partite.\n"
                "- Se best_verified_comp è presente, mostra Miglior composizione per la mappa e modalità consigliate. Deve includere il Brawler richiesto. Se è nullo, non inventare una composizione.\n"
                "- Non nominare mai all utente BrawlTrack, Supabase, API, database, CDN o altre fonti/sistemi tecnici interni. I dati possono essere presentati come dati trovati o verificati dai nostri sistemi abusivi.\n"
                "- Se nei DATI BRAWLTRACK STRUTTURATI E LOCALIZZATI il campo overdrive contiene name_it, includi SEMPRE Overdrive: <name_it> nella configurazione.\n"

                "- Per modalità e mappe usa esclusivamente la localizzazione italiana ufficiale disponibile nel sistema; non inventare traduzioni. Se non esiste una localizzazione verificata, conserva il nome sorgente.\n"
                "- La Tier List generale di Brawl Planet serve per il meta complessivo e NON deve sostituire i dataset specifici Ladder o Classificata quando l utente specifica uno di quei contesti.\n"
                "- Se la Tier List generale e i dati specifici di una modalità/mappa differiscono, per la risposta contestuale prevalgono i dati specifici della modalità/mappa.\n"
                "- Per statistiche per mappa usa BrawlTrack come fonte prioritaria. Brawl Planet è solo fallback. Mantieni distinti Ladder, Classificata e Competitivo.\n"
                "- Non scegliere automaticamente il Brawler con il win rate più alto: valuta insieme tasso di vittoria, tasso di utilizzo, percentuale/frequenza Miglior Star Player, numero di partite/campione e qualità delle composizioni.\n"
                "- DATI PER BRAWLER: ogni Brawler deve avere il proprio blocco completo di statistiche. Non mescolare mai il tasso di vittoria di un Brawler con utilizzo, Star Player, partite o comp di un altro.\n"
                "- Conserva i nomi italiani/localizzati forniti dalla fonte. Usa sempre la dicitura Miglior Star Player.\n"
                "- Riporta tutte le metriche disponibili per ogni Brawler consigliato: vittorie, utilizzo, Miglior Star Player, campione individuale e posizione media dove presenti. Non chiamare campione individuale il totale delle partite della mappa.\n"
                "- Separa Individuali e Squadre. Per ogni squadra consigliata riporta i componenti esatti e tutte le metriche pubblicate per quella composizione: vittorie, utilizzo, campione o posizione media solo quando presenti. Non mediare o trasferire statistiche individuali alla squadra.\n"
                "- Per ogni blocco specifica mappa, modalità e Ladder o Classificata quando utile. Non riempire la risposta con campi Non disponibile: ometti le metriche assenti. Se manca un dato consulta il fallback senza mescolare campioni di fonti diverse.\n"
                "- Per una richiesta su tutte le mappe usa prima il blocco DATI STRUTTURATI BRAWL PLANET: contiene le righe delle tabelle, non fermarti ai soli riepiloghi del titolo.\n"
                "- Usa le etichette Ladder - Individuali, Ladder - Squadre, Classificata - Individuali e Classificata - Squadre solo quando il relativo dataset è realmente disponibile. Se la Classificata non è verificata per quella mappa, dillo in una sola frase senza creare sezioni vuote.\n"
                "- Nei blocchi Individuali conserva le colonne Brawler, Vitt., Scelta e Stella; nei blocchi Squadre conserva la composizione esatta e Vitt. Non ridurre la risposta alle sole liste 'miglior vittoria' e 'più scelto'.\n"
                "- Per ogni mappa riporta almeno i primi 10 Brawler della sezione Individuale e le prime 10 Squadre pubblicate da Brawl Planet, quando presenti nel blocco strutturato. Per ciascuna riga conserva tutte le metriche effettivamente pubblicate; non fermarti a un solo leader e non inventare colonne mancanti.\n"
                "- Se elenchi più Brawler, per ciascuno riporta separatamente, quando disponibili: Tasso di vittoria, Tasso di utilizzo, Miglior Star Player, Partite analizzate/campione e Comp principali.\n"
                "- Tutti i dati nello stesso blocco devono provenire dallo stesso contesto: stessa mappa, stessa modalità e stesso ambiente Ladder oppure Classificata.\n"
                "- Se una metrica manca per un Brawler, omettila e non ricavarla da un altro giocatore o dataset.\n"
                "- Escludi dai consigli meta Ladder i Brawler con utilizzo inferiore all 1% quando il tasso di utilizzo è disponibile. Diffida di percentuali alte con campione basso. Scarta sempre composizioni con lo stesso Brawler ripetuto.\n"
                "- TERMINOLOGIA ITALIANA OBBLIGATORIA: usa sempre i termini ufficiali del gioco in italiano. Gear = equipaggiamento/equipaggiamenti; Star Power = abilità stellare/abilità stellari; Hypercharge = overdrive; Gadget resta gadget.\n"
                "- Per gadget, abilità stellari, equipaggiamenti e overdrive specifici usa il NOME UFFICIALE ITALIANO mostrato in Brawl Stars, non il nome inglese.\n"
                "- Per i nomi localizzati dai priorità alle pagine italiane ufficiali di Supercell e alle fonti italiane affidabili.\n"
                "- Se il nome italiano ufficiale di una specifica abilità non è verificabile, non tradurlo a intuito e non mostrare il nome inglese: descrivi semplicemente l effetto o il tipo di scelta consigliata.\n"
                "- Questa regola vale per tutti gli elementi del gioco mostrati all utente: modalità, mappe, gadget, abilità stellari, equipaggiamenti, overdrive, eventi e oggetti.\n\n"

                + (
                    f"MAPPA CORRENTE IDENTIFICATA DAL SISTEMA: {current_map}\n"
                    "- Se la domanda riguarda la mappa corrente, scrivi sempre questo nome esatto nella risposta.\n"
                    "- Non sostituire questa mappa con nomi trovati in altre fonti.\n"
                    "- Se proponi una composizione, deve riferirsi esclusivamente a questa mappa e alla modalità richiesta.\n\n"
                    if "current_map" in locals() and current_map
                    else ""
                )

                + "Regole fondamentali:\n"
                "- Non inventare informazioni.\n"
                "- Non affermare che un Brawler, modalità, evento o "
                "funzione non esiste solamente perché non compare "
                "in una fonte ufficiale.\n"
                "- Considera Reddit, YouTube, wiki e siti specializzati "
                "di Brawl Stars.\n"
                "- Dai priorità alle fonti ufficiali quando si parla "
                "di informazioni ufficialmente annunciate.\n"
                "- Usa fonti della community per informazioni storiche, "
                "guide, statistiche e informazioni consolidate.\n"
                "- Distingui chiaramente informazioni ufficiali, "
                "informazioni della community e rumor/leak.\n"
                "- Se trovi fonti discordanti, spiegalo.\n"
                "- Se una informazione è vecchia, considera la data "
                "della fonte prima di rispondere.\n"
                "- Per domande su un Brawler specifico, cerca e usa "
                "le informazioni relative a quel Brawler anche se "
                "non sono presenti sul sito ufficiale di Supercell.\n"
                "- Non dire che non esiste un Brawler solo perché "
                "non lo trovi nelle fonti ufficiali.\n\n"

                "STILE DI RISPOSTA E PERSONALITA SENS GPT:\n"
                "- Sei Sens GPT: un membro digitale dei TITANI ABUSIVI, non un assistente generico che si presenta a ogni messaggio.\n"
                "- Hai una personalita riconoscibile: competente sui dati, diretto, sveglio, leggermente ironico e con il linguaggio abusivo come firma della community.\n"
                "- Non iniziare mai con presentazioni come Ciao, sono Sens GPT o Sono l'intelligenza artificiale dei TITANI ABUSIVI.\n"
                "- Varia naturalmente le aperture quando servono. Puoi usare formule come I nostri sistemi abusivi hanno scovato..., Il radar abusivo segnala..., Dai dati abusivi salta fuori..., Le nostre analisi abusive hanno trovato..., ma NON ripetere sempre la stessa frase e non sei obbligato a usare un apertura speciale in ogni risposta.\n"
                "- L ironia deve essere breve e leggera: mai sacrificare precisione, chiarezza o dati verificati per una battuta.\n"
                "- Se un dato non e verificato, dillo con personalita ma senza inventare, per esempio i sistemi abusivi non hanno abbastanza prove.\n"
                "- I dati strutturati, percentuali, nomi ufficiali, build, mappe e modalita sono intoccabili: la personalita modifica solo il modo di presentarli.\n"                "- Non citare all utente i nomi delle fonti tecniche interne come BrawlTrack, Supabase, API, database o CDN.\n"
                "- Mantieni uno stile diretto, professionale, chiaro e naturale.\n"
                "- Usa un italiano corretto, semplice e non eccessivamente formale.\n"
                "- Vai subito al punto senza introduzioni inutili.\n"
                "- Non usare espressioni ripetitive come \"Bella Titano!\" o \"Ciao Titano!\".\n"
                "- Non usare Markdown decorativo.\n"
                "- Non usare asterischi per grassetto o corsivo.\n"
                "- Non usare #, ## o ### per creare titoli.\n"
                "- Non usare formattazioni elaborate o inutili.\n"
                "- Usa paragrafi brevi e facilmente leggibili.\n"
                "- Usa elenchi con il carattere - solo quando migliorano realmente la leggibilità.\n"
                "- Non ripetere la domanda dell'utente prima di rispondere.\n"
                "- Non aggiungere conclusioni inutili o frasi come \"Spero di esserti stato utile\".\n"
                "- Non inventare informazioni mancanti.\n"
                "- Se non sei sicuro di un dato, dichiaralo chiaramente.\n"
                "- Usa le fonti web internamente per verificare i dati, ma NON mostrare fonti, URL, link o una sezione Fonti nella risposta.\n"
                "- Quando consigli una mappa specifica attualmente disponibile, aggiungi una riga tecnica: MAPPA_IMMAGINE: NomeMappa.\n"
                "- In MAPPA_IMMAGINE usa il nome esatto della mappa trovato nelle fonti web; preferisci il nome inglese/canonico della fonte per permettere al sistema di recuperare l immagine corretta.\n"
                "- Inserisci MAPPA_IMMAGINE solo se quella mappa è stata verificata come attuale/disponibile; non usarla per mappe storiche o non verificate.\n"
                "- La riga MAPPA_IMMAGINE è un dato tecnico e verrà rimossa prima di mostrare la risposta all utente.\n"
                "- La risposta deve sembrare scritta da un assistente ufficiale della community, non da un chatbot che cerca di essere simpatico.\n\n"
                "FONTE PRIORITARIA PER LE MAPPE E LE STATISTICHE:\n"
                "- Per meta, mappe, statistiche e composizioni usa BrawlTrack come fonte primaria quando il dato è disponibile.\n"
                "- Brawl Planet separa Ladder e Classificata: usa sempre il dataset coerente con la domanda dell utente.\n"
                "- Brawl Planet è solo fallback per rotazione o dati che BrawlTrack non rende disponibili in modo verificabile.\n"
                "- Per ogni mappa usa prima BrawlTrack sulla mappa esatta e sul contesto corretto Ladder oppure Classificata; usa Brawl Planet solo per campi mancanti.\n"
                "- Solo quando BrawlTrack e Brawl Planet non pubblicano uno specifico dato, usa Brawlify, Brawl Time Ninja e Noff come fonti secondarie.\n"
                "- Non sostituire un dato BrawlTrack disponibile con un fallback. Non usare mappe storiche come se fossero attive.\n"
                "- Se la rotazione attuale non è verificabile, dichiaralo chiaramente e non indovinare.\n"
                "- Quando l utente chiede su quale mappa usare un Brawler, NON proporre mappe storiche, rimosse o fuori dal pool attuale.\n"
                "- Una mappa può essere consigliata solo se i risultati web aggiornati mostrano che è attualmente disponibile nella rotazione o nel pool della modalità pertinente.\n"
                "- Se una buona mappa per quel Brawler esiste storicamente ma non è disponibile adesso, non consigliarla come scelta attuale.\n"
                "- Se non riesci a verificare almeno una mappa attualmente disponibile, consiglia la modalità e spiega che la mappa attiva non è verificabile, senza inventare.\n\n"

                "- I nomi delle mappe mostrati all utente devono essere SEMPRE quelli ufficiali italiani usati nel gioco.\n"
                "- I nomi italiani canonici delle modalità principali sono: Arraffagemme, Sopravvivenza, Footbrawl, Ricercati, Rapina, Dominio, K.O., Annientamento e Duelli. Non tradurre creativamente i nomi delle modalità e non usare mai Fotoria, Acuffobia o Zona Telone.\n"
                "- Le fonti web possono contenere i nomi inglesi: usali solo internamente per la ricerca e non mostrarli nella risposta se esiste il nome ufficiale italiano.\n"
                "- Non inventare traduzioni di nomi ufficiali.\n"
                "- Per una miglior composizione identifica prima la mappa corrente e poi scegli i Brawler più adatti a quella specifica mappa.\n\n"
                "- Se è stata identificata una mappa specifica, usa solo dati e statistiche relativi a quella mappa per scegliere la composizione.\n"
                "- Non usare tier list, win rate o composizioni generali della modalità come prova della miglior comp per una mappa specifica.\n"
                "- Se non trovi dati affidabili per quella specifica mappa, dichiaralo chiaramente invece di sostituirli con dati generali della modalità.\n"
                "- Per usare una fonte come prova della miglior comp, verifica che nella fonte compaiano sia il nome esatto della mappa sia la modalità esatta richiesta.\n"
                "- Non associare statistiche di una mappa usata in un altra modalità alla modalità richiesta dall utente.\n"
                "- Brawl Planet, Brawl Insights, Brawl Time Ninja, Noff, Brawlify e Power League Prodigy sono fonti community/statistiche: non definirle fonti ufficiali Supercell.\n"
                "- Quando l utente chiede la miglior comp, indica esattamente 3 Brawler specifici.\n"
                "- Non rispondere con categorie generiche come tank, tiratori, supporti o brawler da mischia.\n"
                "- Se i dati disponibili non permettono di determinare una comp affidabile, dichiaralo chiaramente e non inventare.\n"
                "- Per richieste estese come 'ogni mappa di ogni modalità oggi', includi solo mappe dimostrate attive dai risultati forniti e raccomandazioni statistiche riferite proprio a ciascuna mappa. Non copiare lo stesso terzetto su mappe diverse. Se non puoi verificare in modo strutturato l intero elenco, spiega che la rotazione completa non è verificabile in quel momento e chiedi di scegliere una modalità: non completare l elenco a intuito.\n"
                "- Per una richiesta estesa non aggiungere MAPPA_IMMAGINE e non scegliere una sola mappa da inviare come immagine: l'utente ha chiesto l'intera rotazione.\n"

                "Alla fine della risposta aggiungi:\n"
                "Fonti:\n"
                "e indica le fonti web realmente utilizzate.\n\n"

                f"RISULTATI DELLA RICERCA WEB:\n"
                f"{rotation_instruction}\n{web_context}\n\n"

                f"DOMANDA E CONTESTO:\n"
                f"{question_for_ai}"
            )

        else:
            instructions = (
                "Sei Sens GPT, l'intelligenza artificiale ufficiale "
                "della community TITANI ABUSIVI.\n\n"

                "Sei specializzato in Brawl Stars. "
                "Rispondi sempre in italiano, in modo competente, diretto, chiaro e utile. "
                "Hai una personalita riconoscibile da membro digitale dei TITANI ABUSIVI: sveglio, leggermente ironico e con richiami abusivi variati e naturali. "
                "Non presentarti con formule come Ciao, sono Sens GPT. Non ripetere sempre la stessa apertura e non forzare una battuta in ogni risposta. "
                "La personalita riguarda solo lo stile: non modificare mai dati, percentuali, nomi ufficiali, build, mappe o modalita.\n\n"

                "Non inventare informazioni. "
                "Se non conosci con certezza una informazione, "
                "dillo chiaramente.\n\n"

                "Usa sempre la terminologia ufficiale italiana di Brawl Stars. "
                "Scrivi equipaggiamento invece di gear, abilità stellare invece di Star Power "
                "e overdrive invece di Hypercharge. Per i nomi specifici di gadget, abilità "
                "stellari, equipaggiamenti e overdrive usa solo il nome italiano ufficiale; "
                "se non sei sicuro del nome ufficiale, descrivi l effetto senza inventare una traduzione.\n\n"

                f"DOMANDA E CONTESTO:\n"
                f"{question_for_ai}"
            )

        # Keep a final hard ceiling even if a future Tavily response contains
        # unexpectedly large fields. The normal context builder stays well
        # below this value; this is only a last-resort safety net.
        if len(instructions) > 235000:
            instructions = compact_source_text(instructions, 235000)
        print(
            "GEMINI INPUT CARATTERI:",
            len(instructions),
            flush=True
        )

        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=instructions
            )
        except Exception as generation_error:
            error_lower = str(generation_error).lower()
            # If a provider-side input-token limit is hit, retry once with a
            # much smaller prompt. This also makes the bot recover from an
            # unusually large single Tavily page without returning the generic
            # AI error to the user.
            if (
                ("429" in error_lower or "resource_exhausted" in error_lower)
                and "input_token" in error_lower
                and len(instructions) > 90000
            ):
                reduced_instructions = compact_source_text(instructions, 90000)
                print(
                    "GEMINI: nuovo tentativo con contesto ridotto",
                    len(reduced_instructions),
                    flush=True
                )
                response = client.models.generate_content(
                    model="gemini-3.5-flash-lite",
                    contents=reduced_instructions
                )
            else:
                raise

        response_text = response.text or ""
        broad_request = is_all_maps_request(question_for_ai)
        validation_manifest = locals().get("rotation_manifest") or []

        # A long rotation answer is easy for a generative model to compress
        # into only the headline win/pick lists. If that happens, make one
        # compact corrective call using the parsed Brawl Planet tables before
        # falling back to the safe "dati non completi" response.
        if broad_request and len(validation_manifest) >= 2:
            candidate = translate_map_names_in_text(response_text)
            if invalid_exhaustive_map_answer(
                candidate,
                validation_manifest,
                expected_context=get_game_context(question_for_ai)
            ):
                retry_material = structured_stats_context or web_context
                retry_material = compact_source_text(retry_material, 90000)
                retry_instructions = (
                    "Sei Sens GPT. La risposta precedente era incompleta. "
                    "Riscrivi l'elenco completo delle mappe attive usando "
                    "esclusivamente i dati strutturati forniti.\n"
                    "Per ogni mappa scrivi quattro sezioni: Trofei - Individuali, "
                    "Trofei - Squadre, Classificata - Individuali, Classificata - Squadre. "
                    "Nelle Individuali usa Brawler | Vitt. | Scelta | Stella; "
                    "nelle Squadre usa Composizione | Vitt. Se un dato manca scrivi "
                    "Non disponibile. Non usare MAPPA_IMMAGINE, non inviare immagini "
                    "e non ridurre l'elenco a cinque Brawler. Mantieni i nomi italiani.\n\n"
                    f"ELENCO MAPPE ATTIVE:\n{rotation_instruction}\n"
                    f"DATI BRAWL PLANET:\n{retry_material}\n\n"
                    f"DOMANDA:\n{question_for_ai}"
                )
                try:
                    retry_response = client.models.generate_content(
                        model="gemini-3.5-flash-lite",
                        contents=retry_instructions
                    )
                    if retry_response.text:
                        response_text = retry_response.text
                        print(
                            "GEMINI: risposta correttiva generata per tabelle Individuali/Squadre",
                            flush=True
                        )
                except Exception as retry_error:
                    # Keep the original candidate; the validator below will
                    # prevent an incomplete answer from being shown.
                    print(
                        "GEMINI: correzione tabellare non disponibile",
                        repr(retry_error),
                        flush=True
                    )

        final_text = response_text
        comp_brawlers = []
        recommended_map = None

        match_map = re.search(
            r"^MAPPA_IMMAGINE:\s*(.+)$",
            final_text,
            re.I | re.M
        )

        if match_map:
            if not is_all_maps_request(question_for_ai):
                recommended_map = resolve_map_source_name(
                    match_map.group(1).strip()
                )
            else:
                print(
                    "MAPPA IMMAGINE IGNORATA: richiesta su tutta la rotazione",
                    match_map.group(1).strip(),
                    flush=True
                )

            final_text = re.sub(
                r"^MAPPA_IMMAGINE:\s*.+$",
                "",
                final_text,
                flags=re.I | re.M
            ).strip()

            print(
                "MAPPA IMMAGINE ESTRATTA:",
                recommended_map,
                flush=True
            )

        match_brawlers = re.search(
            r"^BRAWLERS_IMMAGINI:\s*(.+)$",
            final_text,
            re.I | re.M
        )

        if match_brawlers:
            comp_brawlers = [
                name.strip()
                for name in match_brawlers.group(1).split("|")
                if name.strip()
            ]

            final_text = re.sub(
                r"^BRAWLERS_IMMAGINI:\s*.+$",
                "",
                final_text,
                flags=re.I | re.M
            ).strip()

            print(
                "BRAWLERS COMP ESTRATTI:",
                comp_brawlers,
                flush=True
            )

        final_text = re.split(
            r"\n\s*(?:Fonti|Fonti utilizzate|Sources)\s*:?\s*",
            final_text,
            maxsplit=1,
            flags=re.I
        )[0].strip()

        if comp_brawlers:
            comp_brawlers = [
                name
                for name in comp_brawlers
                if re.search(
                    r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])",
                    final_text,
                    re.I
                )
            ][:3]

            print(
                "BRAWLERS IMMAGINI CONFERMATI NELLA RISPOSTA:",
                comp_brawlers,
                flush=True
            )

        final_text = re.sub(
            r"https?://\S+",
            "",
            final_text,
            flags=re.I
        ).strip()

        # Converte i nomi inglesi delle mappe e la terminologia generica
        # nei termini ufficiali italiani prima di mostrare la risposta.
        # Structured meta already resolved names against the verified Italian catalogue.
        # Do not run the legacy hand-written dictionaries over it afterwards.
        if not brawltrack_meta_context:
            final_text = translate_map_names_in_text(final_text)
            final_text = translate_mode_names_in_text(final_text)
        final_text = translate_game_terms_in_text(final_text)

        if (
            is_exhaustive_current_maps_query(question_for_ai)
            and invalid_exhaustive_map_answer(
                final_text,
                locals().get("rotation_manifest"),
                expected_context=get_game_context(question_for_ai)
            )
        ):
            final_text = (
                "In questo momento Brawl Planet e le fonti secondarie non mi hanno "
                "restituito dati completi e verificabili per tutte le mappe attive. "
                "Riprova tra poco: non inserirò consigli generici o inventati."
            )
            comp_brawlers = []
            recommended_map = None
            print(
                "RISPOSTA MAPPE ESTESE BLOCCATA: dati non verificati o terzetti duplicati",
                flush=True
            )

        if (
            not is_all_maps_request(question_for_ai)
            and "verified_comp" in locals()
            and len(verified_comp) == 3
        ):
            comp_brawlers = verified_comp[:3]

            display_map = map_name_it(current_map)

            final_text = (
                f"FOOTBRAWL - {display_map.upper()}\n\n"
                "Comp consigliata:\n"
                + "\n".join(
                    brawler_name_it(name)
                    for name in comp_brawlers
                )
                + "\n\nMeta aggiornato."
            )

            print(
                "RISPOSTA COMP BLOCCATA SU:",
                comp_brawlers,
                flush=True
            )


        map_photo_sent = False

        map_to_send = None
        if (
            not is_all_maps_request(question_for_ai)
            and "current_map" in locals()
            and current_map
        ):
            map_to_send = current_map
        elif not is_all_maps_request(question_for_ai) and recommended_map:
            map_to_send = recommended_map

        if map_to_send:
            try:
                map_image = get_noff_map_image(map_to_send)

                if map_image:
                    image_response = requests.get(
                        map_image,
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=15
                    )
                    image_response.raise_for_status()

                    await context.bot.send_photo(
                        chat_id=message.chat_id,
                        photo=image_response.content,
                        caption=f"Mappa: {map_name_it(map_to_send)}"
                    )

                    map_photo_sent = True
                    print(
                        "IMMAGINE MAPPA INVIATA:",
                        map_to_send,
                        flush=True
                    )
                else:
                    print(
                        "NESSUNA IMMAGINE DISPONIBILE PER MAPPA:",
                        map_to_send,
                        flush=True
                    )

            except Exception as e:
                print(
                    "ERRORE INVIO IMMAGINE MAPPA:",
                    repr(e),
                    flush=True
                )

        if "current_map" in locals() and current_map:
            final_text = re.sub(
                r"^" + re.escape(current_map) + r"\s*",
                "",
                final_text,
                count=1,
                flags=re.I
            ).strip()

            final_text = re.sub(
                r"Per\s+la\s+mappa\s+" + re.escape(current_map),
                "Per questa mappa",
                final_text,
                flags=re.I
            )

            final_text = re.sub(
                re.escape(current_map),
                "questa mappa",
                final_text,
                flags=re.I
            )

        final_text = final_text.replace("*", "").strip()

        for chunk in telegram_text_chunks(final_text):
            await send_mode_aware_text(
                message,
                context,
                chunk,
                disable_web_page_preview=True
            )

        if False and any(k in question_for_ai.lower() for k in ["mappa", "mappe", "rotazione"]):
            try:
                web_images = []
                if current_map:
                    map_image_data = image_search(f"Brawl Stars map {current_map}")
                    map_key = re.sub(r"[^a-z0-9]+", "", current_map.lower())
                    for image in map_image_data.get("images", []):
                        if isinstance(image, str):
                            image_url = image
                            description = ""
                        elif isinstance(image, dict):
                            image_url = image.get("url") or image.get("image_url")
                            description = image.get("description") or image.get("title") or ""
                        else:
                            continue
                        if not image_url or not image_url.startswith("http"):
                            continue
                        image_url_key = re.sub(r"[^a-z0-9]+", "", image_url.lower())
                        description_key = re.sub(r"[^a-z0-9]+", "", description.lower())

                        if (
                            map_key
                            and (
                                map_key in image_url_key
                                or map_key in description_key
                            )
                        ):
                            web_images.append({
                                "url": image_url,
                                "description": description
                            })
                            print(
                                "IMMAGINE MAPPA ACCETTATA:",
                                current_map,
                                image_url,
                                flush=True
                            )
                            break

                        print(
                            "IMMAGINE MAPPA SCARTATA:",
                            image_url,
                            description,
                            flush=True
                        )
            except Exception as e:
                print("ERRORE IMMAGINE MAPPA:", repr(e), flush=True)


        explicit_image_request = any(
            k in question_for_ai.lower()
            for k in [
                "foto",
                "immagine",
                "immagini",
                "mostrami",
                "fammi vedere"
            ]
        )

        is_map_or_comp_request = any(
            k in question_for_ai.lower()
            for k in [
                "mappa",
                "mappe",
                "rotazione",
                "miglior comp",
                "migliore comp",
                "composizione"
            ]
        )

        if explicit_image_request and not is_map_or_comp_request:
            await send_relevant_images(
                context,
                message.chat_id,
                question_for_ai,
                web_images
            )

    except Exception as e:
        print(
            "ERRORE GEMINI:",
            repr(e),
            flush=True
        )

        error_lower = str(e).lower()
        if (
            "429" in error_lower
            or "resource_exhausted" in error_lower
            or "quota" in error_lower
        ):
            user_error = (
                "Il servizio AI ha raggiunto temporaneamente il limite di richieste. "
                "Ho già ridotto il contenuto inviato al modello: riprova tra circa un minuto."
            )
        else:
            user_error = (
                "Ho avuto un problema con il sistema AI. "
                "Riprova tra poco."
            )

        await context.bot.send_message(
            chat_id=message.chat_id,
            text=user_error
        )



async def transcribe_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transcribe Telegram voice/audio and route it through the same Sens GPT brain."""
    message = update.effective_message
    if not message or not message.from_user:
        return
    media = message.voice or message.audio
    if not media:
        return
    try:
        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
        tg_file = await context.bot.get_file(media.file_id)
        audio = await tg_file.download_as_bytearray()
        mime = getattr(media, "mime_type", None) or "audio/ogg"
        # Gemini accepts inline audio and returns only the transcript here.
        prompt = (
            "Trascrivi fedelmente questo messaggio audio in italiano. "
            "Se contiene nomi o termini di Brawl Stars, mantienili corretti. "
            "Restituisci esclusivamente la trascrizione, senza commenti."
        )
        result = await asyncio.to_thread(
            client.models.generate_content,
            model=os.environ.get("VOICE_STT_MODEL", "gemini-3.5-flash-lite"),
            contents=[prompt, {"inline_data": {"mime_type": mime, "data": bytes(audio)}}]
        )
        transcript = (result.text or "").strip()
        if not transcript:
            await message.reply_text("I nostri Sistemi Abusivi non sono riusciti a capire questo audio. Riprova con un vocale più chiaro.")
            return
        # Reuse the normal answer pipeline without showing the internal transcript.
        original_text = message.text
        try:
            message.text = transcript
            context.user_data["_voice_input"] = True
            await answer(update, context)
        finally:
            context.user_data.pop("_voice_input", None)
            context.user_data.pop("_request_voice_mode", None)
            message.text = original_text
    except Exception as exc:
        print("VOICE STT ERRORE:", repr(exc), flush=True)
        await message.reply_text("I nostri Sistemi Abusivi non riescono a elaborare il vocale in questo momento. Riprova tra poco.")


async def generazioni_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.from_user:
        return
    await handle_premium_command(
        message, context, "generazioni", SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
    )

def main():
    application = Application.builder().token(
        TELEGRAM_TOKEN
    ).build()

    if application.job_queue:
        application.job_queue.run_repeating(
            community.scheduled_jobs,
            interval=3600,
            first=45,
            name="community_jobs"
        )

    application.add_handler(
        CommandHandler("generazioni", generazioni_command)
    )
    application.add_handler(
        CommandHandler("voce", voice_mode_command)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            answer
        )
    )

    application.add_handler(
        MessageHandler(
            filters.VOICE | filters.AUDIO,
            transcribe_voice
        )
    )

    port = int(os.environ.get("PORT", 10000))

    base_url = os.environ.get(
        "RENDER_EXTERNAL_URL",
        "https://sens-gpt-telegram.onrender.com"
    ).rstrip("/")

    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path="telegram",
        webhook_url=f"{base_url}/telegram"
    )


def _startup_brawltrack_map_smoke():
    """One-shot production smoke check for the BrawlTrack competitive map parser."""
    try:
        from live_maps import brawltrack_pro_map_stats, brawltrack_pro_map_image
        data=brawltrack_pro_map_stats("Hard Rock Mine",ttl=0) or {}
        image=brawltrack_pro_map_image("Hard Rock Mine",ttl=0) or {}
        picks=data.get("priority_picks") or []
        comps=data.get("final_comps") or []
        same_id=data.get("map_id")==image.get("map_id")==15000007
        ok=same_id and bool(picks) and bool(comps) and image.get("verified") and bool(image.get("image_url"))
        print("BRAWLTRACK MAP SMOKE %s: map_id=%s picks=%s comps=%s image=%s image_id=%s" % (
            "OK" if ok else "FAILED",data.get("map_id"),len(picks),len(comps),
            bool(image.get("image_url")),image.get("map_id")
        ),flush=True)
    except Exception as exc:
        print("BRAWLTRACK MAP SMOKE ERROR: %s: %s" % (type(exc).__name__,exc),flush=True)

_startup_brawltrack_map_smoke()

def _startup_structured_meta_smoke():
    try:
        for label,question in (("Stecca","build migliore di stecca"),("Wendy","build migliore di wendy")):
            ctx=get_brawltrack_meta_context(question); rendered=render_structured_brawler_meta(ctx)
            print("STRUCTURED META SMOKE %s: %s" % (label,"OK" if ctx and rendered else "FAILED"),flush=True)
    except Exception as exc:
        print("STRUCTURED META SMOKE ERROR: %s: %s" % (type(exc).__name__,exc),flush=True)

_startup_structured_meta_smoke()

def _startup_brawler_it_sync():
    """Populate only missing Italian Brawler names from the verified localization catalogue."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:return
    try:
        names=(safe_get("i18n/names.it.json.gz") or {}).get("brawlers",{})
        headers={"apikey":SUPABASE_SERVICE_ROLE_KEY,"Authorization":"Bearer "+SUPABASE_SERVICE_ROLE_KEY,"Content-Type":"application/json","Prefer":"return=minimal"}
        r=requests.get(SUPABASE_URL+"/rest/v1/brawlers_catalog",headers=headers,params={"select":"brawler_id,name_en,name_it","limit":"200"},timeout=15);r.raise_for_status()
        rows=r.json();resolved=[];missing=[]
        for b in rows:
            if str(b.get("name_it") or "").strip():continue
            en=str(b.get("name_en") or "").strip(); it=str(names.get(en.upper()) or "").strip()
            if it and it.casefold()!=en.casefold():resolved.append((int(b["brawler_id"]),en,it))
            elif it:resolved.append((int(b["brawler_id"]),en,it))
            else:missing.append((b.get("brawler_id"),en))
        for bid,en,it in resolved:
            u=requests.patch(SUPABASE_URL+"/rest/v1/brawlers_catalog",headers=headers,params={"brawler_id":"eq."+str(bid),"name_it":"is.null"},json={"name_it":it},timeout=15);u.raise_for_status()
        print("BRAWLER IT SYNC: resolved=%s missing=%s total=%s" % (len(resolved),len(missing),len(rows)),flush=True)
        if missing:print("BRAWLER IT SYNC MISSING:",missing,flush=True)
    except Exception as exc:
        print("BRAWLER IT SYNC ERROR: %s: %s" % (type(exc).__name__,exc),flush=True)

_startup_brawler_it_sync()

def _startup_brawltrack_meta_sync_once():
    """Refresh BrawlTrack meta after parser changes; safe upsert by brawler_id."""
    try:
        from brawltrack_meta_sync import sync
        result=sync()
        print("BRAWLTRACK META SYNC ONCE:",result,flush=True)
    except Exception as exc:
        print("BRAWLTRACK META SYNC ONCE ERROR: %s: %s" % (type(exc).__name__,exc),flush=True)

if __name__ == "__main__":
    threading.Thread(
        target=automatic_trophy_monitor,
        daemon=True
    ).start()

    main()
