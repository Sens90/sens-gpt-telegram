import html
import os
import re

import requests

RANK_NAMES_IT = {
    "bronze": "Bronzo", "silver": "Argento", "gold": "Oro",
    "diamond": "Diamante", "mythic": "Mito", "legendary": "Leggenda",
    "masters": "Campione", "master": "Campione", "pro": "Pro",
}
BRAWLZONE_BASE_URL = "https://brawlzone.net/player"


def _number(value):
    if value is None or value == "": return None
    if isinstance(value, (int, float)): return int(value)
    digits = re.sub(r"\D", "", str(value))
    return int(digits) if digits else None


def translate_rank(rank):
    rank = re.sub(r"\s+", " ", (rank or "").strip())
    if not rank: return None
    parts = rank.split(" ", 1)
    translated = RANK_NAMES_IT.get(parts[0].casefold(), parts[0])
    return f"{translated} {parts[1]}" if len(parts) > 1 else translated


def _clean_tag(value):
    if not value: return None
    tag = str(value).upper().replace("#", "").strip()
    return f"#{tag}" if re.fullmatch(r"[0289PYLQGRJCUV]{3,15}", tag) else None


def _extract_brawlzone_ranked_only(page):
    labels = {
        "ranked_current": "Current ranked",
        "ranked_season_peak": "Season best",
        "ranked_career_peak": "All-time best",
    }
    result = {}
    for key, label in labels.items():
        match = re.search(
            rf">\s*{re.escape(label)}\s*</p>\s*<p[^>]*>\s*([^<]+?)\s*</p>\s*<p[^>]*>\s*([\d.,]+)\s*elo\s*</p>",
            page, re.I | re.S,
        )
        if not match:
            match = re.search(
                rf'children\\?":\\?"{re.escape(label)}\\?".*?children\\?":\\?"([^"\\]+)\\?".*?children\\?":\\?"([\d.,]+)\s*elo\\?"',
                page, re.I | re.S,
            )
        if match:
            result[key] = translate_rank(match.group(1))
            result[f"{key}_elo"] = _number(match.group(2))
    prestige = re.search(r'\\\"Prestige \\\",\s*\\\"?(\d+)', page or "")
    if prestige:
        result["prestige"] = int(prestige.group(1))
    result["ranked_peak"] = result.get("ranked_career_peak")
    result["ranked_peak_elo"] = result.get("ranked_career_peak_elo")
    return result


def _legacy_enrichment(tag, timeout=15):
    """Only fields that the official API does not expose (Ranked/Prestigio)."""
    try:
        response = requests.get(
            f"{BRAWLZONE_BASE_URL}/{tag}",
            headers={"User-Agent": "Mozilla/5.0 (SensGPT-TitaniAbusivi/1.0)"},
            timeout=timeout,
        )
        if response.status_code != 200: return {}
        return _extract_brawlzone_ranked_only(html.unescape(response.text))
    except Exception as error:
        print("ERRORE ARRICCHIMENTO RANKED:", repr(error), flush=True)
        return {}


def get_brawltrack_player(player_tag, timeout=20):
    """Runtime player source. Supercell official is authoritative for every field it exposes."""
    tag = str(player_tag or "").upper().replace("#", "").strip()
    if not re.fullmatch(r"[0289PYLQGRJCUV]{3,15}", tag): return None

    proxy_url = os.environ.get("BRAWL_OFFICIAL_PROXY_URL")
    proxy_key = os.environ.get("BRAWL_OFFICIAL_PROXY_KEY")
    if not proxy_url or not proxy_key:
        print("SUPERCELL PROXY NON CONFIGURATO", flush=True)
        return None

    try:
        response = requests.get(
            proxy_url,
            params={"action": "player", "tag": tag},
            headers={
                "X-Sens-Key": proxy_key,
                "Accept": "application/json",
                "User-Agent": "SensGPT-TitaniAbusivi/1.0",
            },
            timeout=timeout,
        )
        if response.status_code != 200:
            print("SUPERCELL PROXY HTTP:", response.status_code, response.text[:300], flush=True)
            return None
        data = response.json()
        if not isinstance(data, dict) or not data.get("name") or data.get("trophies") is None:
            return None

        club = data.get("club") if isinstance(data.get("club"), dict) else {}
        club_name = club.get("name") or None
        club_tag = _clean_tag(club.get("tag"))
        brawlers = data.get("brawlers") if isinstance(data.get("brawlers"), list) else []

        # The old profile renderer prints only the `club` field. Include a newline
        # so Tag club is visible until all legacy renderers are removed.
        club_display = club_name or "Senza club"
        if club_tag:
            club_display = f"{club_display}\nTag club: {club_tag}"

        result = {
            "name": data.get("name"),
            "tag": _clean_tag(data.get("tag")) or f"#{tag}",
            "trophies": _number(data.get("trophies")),
            "brawlers": len(brawlers),
            "level": _number(data.get("expLevel")),
            "wins_3v3": _number(data.get("3vs3Victories")),
            "wins_solo": _number(data.get("soloVictories")),
            "wins_duo": _number(data.get("duoVictories")),
            "club": club_display,
            "club_name": club_name,
            "club_tag": club_tag,
            "source": "Supercell Official API",
        }
        # Ranked/Prestigio are not supplied by the official player endpoint.
        enrichment = _legacy_enrichment(tag, timeout=min(timeout, 15))
        for key, value in enrichment.items():
            if value is not None:
                result[key] = value
        print("SUPERCELL OFFICIAL PLAYER:", tag, club_name, club_tag, flush=True)
        return result
    except Exception as error:
        print("ERRORE SUPERCELL OFFICIAL:", tag, repr(error), flush=True)
        return None


def get_live_club(player_tag, timeout=15):
    player = get_brawltrack_player(player_tag, timeout=timeout)
    if not player: return None, None
    return player.get("club_name"), player.get("club_tag")


def extract_brawlzone_ranked(page):
    """Compatibility hook: official player identity + legacy Ranked enrichment."""
    fallback = _extract_brawlzone_ranked_only(page)
    tag_match = re.search(r"\(#([0289PYLQGRJCUV]{3,15})\)", page or "", re.I)
    if not tag_match:
        return fallback
    primary = get_brawltrack_player(tag_match.group(1)) or {}
    merged = dict(fallback)
    merged.update({k: v for k, v in primary.items() if v is not None})
    merged["ranked_peak"] = merged.get("ranked_career_peak") or merged.get("ranked_peak")
    merged["ranked_peak_elo"] = merged.get("ranked_career_peak_elo") or merged.get("ranked_peak_elo")
    return merged
