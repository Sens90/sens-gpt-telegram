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
from urllib.parse import urlsplit, urlunsplit

from flask import Flask
from google import genai
from telegram import Update
from telegram.error import TelegramError, TimedOut, NetworkError, RetryAfter, BadRequest
from telegram.ext import Application, MessageHandler, ContextTypes, filters
from community_features import CommunityFeatures
from player_tracking import extract_brawlzone_ranked
from live_maps import collect_report, render_report, report_csv


TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
ROME = ZoneInfo("Europe/Rome")

client = genai.Client(api_key=GEMINI_API_KEY)

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
        "ladder", "trofei", "trofeo", "coppe", "coppa",
        "push", "pushare", "scalare", "scala trofei"
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
            "trofei" not in lowered or "classificat" not in lowered
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

    if "supercell.com" in url_lower or "brawlstars.com" in url_lower:
        return 0
    if "brawlplanet.com" in url_lower or "brawlplanet.nl" in url_lower:
        return 1
    if "brawlify.com" in url_lower or "brawltime.ninja" in url_lower:
        return 2
    if "noff.gg" in url_lower:
        return 3
    return 4


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
        if "/it/maps/" in url and "brawlplanet.com" in url:
            return 0
        if italian_planet_url(result.get("url", "")):
            return 1
        if result.get("source_role") == "secondary_fallback":
            return 3
        return 2

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

        source_role = (
            "FONTE SECONDARIA - usare solo se Brawl Planet non ha il dato"
            if is_secondary
            else (
                "FONTE PRIMARIA BRAWL PLANET"
                if italian_planet_url(url)
                else "ALTRA FONTE"
            )
        )
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
        context_hint = "trophy ladder trofei current event rotation"
    else:
        context_hint = "compare trophy ladder and Ranked separately"

    is_map_query = any(x in query_lower for x in [
        "mappa", "mappe", "rotazione", "mappa attuale",
        "mappa di oggi", "mappe attuali", "mappa corrente"
    ])

    is_image_subject_query = any(x in query_lower for x in [
        "brawler", "brawlers", "skin", "skins", "costume"
    ])

    if is_map_query:
        search_query = (
            f"site:brawlplanet.com/it Brawl Stars {query} {today} {context_hint} "
            f"Active Maps best brawlers win rate pick rate Star Player team comp "
            f"trophy ladder Ranked"
        )
    elif is_meta_query:
        search_query = (
            f"Brawl Stars current meta {today} {query} {context_hint} "
            f"(site:brawlplanet.nl/it/meta OR site:brawlplanet.com/meta OR "
            f"site:brawlplanet.com/tier-list OR site:brawlplanet.nl/it/tier-list) "
            f"Brawl Planet tier list meta win rate pick rate Star Player current rotation "
            f"latest balance changes competitive "
            f"gadget abilità stellare equipaggiamento overdrive nomi italiani "
            f"Supercell italiano Brawlify Brawl Time Ninja Noff"
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

    if is_map_query:
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




BRAWLER_NAMES_IT = {
    "Amber": "Ambra",
    "Rico": "Stecca",
    "Crow": "Corvo",
    "Gene": "Eugenio",
    "Barley": "Bombardino",
    "Poco": "Pocho",
    "Darryl": "Barryl",
    "Sprout": "Semino",
    "Surge": "Energetik",
    "Gale": "Gelindo",
    "Max": "Maxine",
    "Nani": "Iris",
    "Ruffs": "Ringhio",
    "Colonel Ruffs": "Ringhio"
}


def brawler_name_it(name):
    return BRAWLER_NAMES_IT.get(name, name)


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
        (r"\bstar\s+player\b", "Star Player"),
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

    start_today = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

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


def get_brawlzone_player(player_tag):
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
        club_match = re.search(r'(?:clubName|club_name)\\s*["':]+\\s*["']([^"']{1,80})', decoded, re.I)
        if not club_match:
            club_match = re.search(r'"club".{0,1200}?"name"\\s*:\\s*"([^"]{1,80})"', decoded, re.I | re.S)
        if club_match:
            player["club_name"] = html.unescape(club_match.group(1)).strip()
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


async def deliver_live_map_report(message, report, rendered):
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
            lambda text=part + "\n\n" + chunk: message.reply_text(
                text, disable_web_page_preview=True, **timeouts
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

    if not mentioned and not is_reply:
        return

    question = message.text

    if mentioned:
        question = question.replace(
            f"@{bot_username}",
            ""
        ).strip()

    if await community.continue_registration(message, context):
        return

    if await community.continue_recruitment(message, context):
        return

    if await community.handle_command(message, context, question):
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

    stats_match = re.fullmatch(
        r"(?:stats|profilo|scheda)\s+#?([0289PYLQGRJCUV]{3,15})",
        question.strip(),
        re.I
    )

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

        ranked_current = (
            (member_data or {}).get("ranked_current")
            or player.get("ranked_current")
            or "Non disponibile"
        )
        ranked_season_peak = (
            (member_data or {}).get("ranked_season_peak")
            or player.get("ranked_season_peak")
            or "Non disponibile"
        )
        ranked_peak = (
            (member_data or {}).get("ranked_peak")
            or player.get("ranked_peak")
            or "Non disponibile"
        )

        text = (
            f"{player['name']}\n"
            f"Tag: {player['tag']}\n\n"
            f"Trofei: {format_number_it(player['trophies'])}\n"
            f"Brawler: {format_number_it(player['brawlers'])}\n"
            f"Livello: {format_number_it(player['level'])}\n"
            f"Prestigio: {format_number_it(player['prestige'])}\n"
            f"Ranked attuale: {ranked_current}\n"
            f"Massima stagione: {ranked_season_peak}\n"
            f"Massima carriera: {ranked_peak}\n\n"
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

        await context.bot.send_message(
            chat_id=message.chat_id,
            text=text
        )
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
            await message.reply_text("Non riesco a trovare questo giocatore.")
            return
        save_player_tracking(player)
        lines = [
            f"CLASSIFICATA - {player['name']}",
            f"Tag: {player['tag']}",
            "",
            f"Attuale: {player.get('ranked_current') or 'Non disponibile'}"
            f" ({format_number_it(player.get('ranked_current_elo'))} ELO)",
            f"Massima stagione: {player.get('ranked_season_peak') or 'Non disponibile'}"
            f" ({format_number_it(player.get('ranked_season_peak_elo'))} ELO)",
            f"Massima carriera: {player.get('ranked_career_peak') or 'Non disponibile'}"
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
        await message.reply_text("\n".join(lines))
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
            await message.reply_text("Il caricamento delle statistiche ha incontrato un errore. Non ho ancora una rotazione verificata da mostrarti.")
            return
        # No AI rewriting, translation pass, or all-or-nothing prose validator.
        await deliver_live_map_report(message, report, rendered)
        return

    web_context = ""
    web_sources = []
    web_images = []
    structured_stats_context = ""

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
            rotation_instruction = ""
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
                "- Gerarchia fonti per il meta: 1) Supercell/Brawl Stars ufficiale per patch, buff, nerf e modifiche; 2) Brawl Planet Tier List/Meta per il meta generale aggiornato; 3) Brawl Planet dati specifici, Brawlify e Brawl Time Ninja per statistiche di modalità, mappe e andamento competitivo; 4) Noff come supporto; 5) altre fonti community solo come conferma secondaria.\n"
                "- Una fonte ufficiale stabilisce cosa è cambiato, ma il meta reale va valutato anche con statistiche e dati competitivi aggiornati.\n"
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
                "- META GENERALE: per domande come 'chi è meta?', 'tier list', 'migliori brawler adesso' usa come fonte primaria la Tier List e la pagina Meta aggiornate di Brawl Planet, confrontandole con gli ultimi bilanciamenti ufficiali Supercell.\n"
                "- La Tier List generale di Brawl Planet serve per il meta complessivo e NON deve sostituire i dataset specifici Ladder o Classificata quando l utente specifica uno di quei contesti.\n"
                "- Se la Tier List generale e i dati specifici di una modalità/mappa differiscono, per la risposta contestuale prevalgono i dati specifici della modalità/mappa.\n"
                "- Per statistiche per mappa usa Brawl Planet come fonte prioritaria quando disponibile: distingue Ladder e Ranked e mostra tasso di vittoria, tasso di utilizzo, Miglior Star Player e composizioni.\n"
                "- Non scegliere automaticamente il Brawler con il win rate più alto: valuta insieme tasso di vittoria, tasso di utilizzo, percentuale/frequenza Miglior Star Player, numero di partite/campione e qualità delle composizioni.\n"
                "- DATI PER BRAWLER: ogni Brawler deve avere il proprio blocco completo di statistiche. Non mescolare mai il tasso di vittoria di un Brawler con utilizzo, Star Player, partite o comp di un altro.\n"
                "- Usa prioritariamente le pagine /it di Brawl Planet e conserva i nomi italiani presenti nella fonte; non ritradurli. Rinomina giocatore stella in Miglior Star Player.\n"
                "- Riporta tutte le metriche disponibili per ogni Brawler consigliato: vittorie, utilizzo, Miglior Star Player, campione individuale e posizione media dove presenti. Non chiamare campione individuale il totale delle partite della mappa.\n"
                "- Separa Individuali e Squadre. Per ogni squadra consigliata riporta i componenti esatti e tutte le metriche pubblicate per quella composizione: vittorie, utilizzo, campione o posizione media solo quando presenti. Non mediare o trasferire statistiche individuali alla squadra.\n"
                "- Per ogni blocco specifica mappa, modalità, Trofei o Classificata, eventuale lega/filtro, periodo e aggiornamento se pubblicati. Dato assente: Non disponibile. Se manca un dato consulta le fonti secondarie senza mescolare campioni di fonti diverse.\n"
                "- Per una richiesta su tutte le mappe usa prima il blocco DATI STRUTTURATI BRAWL PLANET: contiene le righe delle tabelle, non fermarti ai soli riepiloghi del titolo.\n"
                "- Per OGNI mappa crea sempre quattro sottosezioni: Trofei - Individuali, Trofei - Squadre, Classificata - Individuali e Classificata - Squadre. Se una tabella non è pubblicata, scrivi Non disponibile.\n"
                "- Nei blocchi Individuali conserva le colonne Brawler, Vitt., Scelta e Stella; nei blocchi Squadre conserva la composizione esatta e Vitt. Non ridurre la risposta alle sole liste 'miglior vittoria' e 'più scelto'.\n"
                "- Per ogni mappa riporta almeno i primi 10 Brawler della sezione Individuale e le prime 10 Squadre pubblicate da Brawl Planet, quando presenti nel blocco strutturato. Per ciascuna riga conserva tutte le metriche effettivamente pubblicate; non fermarti a un solo leader e non inventare colonne mancanti.\n"
                "- Se elenchi più Brawler, per ciascuno riporta separatamente, quando disponibili: Tasso di vittoria, Tasso di utilizzo, Miglior Star Player, Partite analizzate/campione e Comp principali.\n"
                "- Tutti i dati nello stesso blocco devono provenire dallo stesso contesto: stessa mappa, stessa modalità e stesso ambiente Ladder oppure Classificata.\n"
                "- Se una metrica manca per un Brawler, scrivi Non disponibile invece di ricavarla da un altro giocatore o da un altro dataset.\n"
                "- Diffida di percentuali molto alte con utilizzo o campione molto basso; preferisci dati robusti e coerenti tra più indicatori.\n"
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

                "STILE DI RISPOSTA:\n"
                "- Rispondi come l'assistente AI ufficiale della community TITANI ABUSIVI.\n"
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
                "- Per nomi italiani delle mappe e statistiche specifiche per mappa usa Brawl Planet come fonte prioritaria quando disponibile.\n"
                "- Brawl Planet separa Ladder e Classificata: usa sempre il dataset coerente con la domanda dell utente.\n"
                "- Brawl Planet è la fonte primaria anche per la rotazione live: usa la sezione Active Maps e le pagine specifiche delle mappe.\n"
                "- Per ogni mappa usa prima i dati Brawl Planet relativi a quella esatta mappa e al dataset corretto, Trophy ladder oppure Ranked.\n"
                "- Solo quando Brawl Planet non pubblica uno specifico dato, usa nell ordine Brawlify, Brawl Time Ninja e Noff come fonti secondarie.\n"
                "- Non sostituire mai un dato Brawl Planet disponibile con una fonte secondaria. Non usare mappe storiche come se fossero attive.\n"
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
                "Rispondi sempre in italiano, in modo competente, "
                "diretto, chiaro e utile.\n\n"

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
            await context.bot.send_message(
                chat_id=message.chat_id,
                text=chunk,
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
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            answer
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


if __name__ == "__main__":
    threading.Thread(
        target=automatic_trophy_monitor,
        daemon=True
    ).start()

    main()
