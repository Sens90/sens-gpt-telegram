import html
import os
import re

import requests

from brawltrack_client import BrawlTrackError, player as brawltrack_player

RANK_NAMES_IT = {
    "bronze": "Bronzo", "silver": "Argento", "gold": "Oro",
    "diamond": "Diamante", "mythic": "Mito", "legendary": "Leggenda",
    "masters": "Campione", "master": "Campione", "pro": "Pro",
}
BRAWLZONE_BASE_URL = "https://brawlzone.net/player"


# Ranked seasons reset monthly. If the enrichment source reports Unranked/Unknown,
# its numeric "current ELO" can actually be a previous-season/career value. Never
# promote that stale number into the current season.
def normalize_ranked_fields(player):
    if not isinstance(player, dict):
        return player
    raw = str(player.get("ranked_current") or "").strip()
    if raw.casefold() in {"unranked", "unknown", "ranked unknown", "–", "-"}:
        player["ranked_current"] = "Non classificato"
        player["ranked_current_elo"] = None
        # The same source uses the old score as season peak before a player has
        # established a rank in the new monthly season.
        season_raw = str(player.get("ranked_season_peak") or "").strip()
        if season_raw.casefold() in {"", "unranked", "unknown", "ranked unknown", "–", "-"}:
            player["ranked_season_peak"] = "Non classificato"
            player["ranked_season_peak_elo"] = None
    player["ranked_peak"] = player.get("ranked_career_peak") or player.get("ranked_peak")
    player["ranked_peak_elo"] = player.get("ranked_career_peak_elo") or player.get("ranked_peak_elo")
    return player

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
    if not any(result.get(k) for k in ("ranked_current", "ranked_season_peak", "ranked_career_peak")):
        # Safe structural diagnostic: labels/nearby markup only, no credentials.
        compact = re.sub(r"\\s+", " ", page or "")
        snippets = []
        for token in ("ranked", "season best", "all-time best", "highest rank", "best rank", "elo"):
            pos = compact.casefold().find(token)
            if pos >= 0:
                snippets.append(compact[max(0, pos-120):pos+260])
        diagnostic = " || ".join(snippets[:6])[:2200]
        if not diagnostic:
            # The current page can encode profile data in serialized React/Next
            # payloads without the legacy English labels. Log only safe nearby
            # profile markup/serialized text so the parser can be adapted from
            # real structure rather than guessed regexes.
            safe_page = re.sub(
                r'(?i)(api[_-]?key|authorization|token|secret|password)(.{0,80})',
                r'\\1=[REDACTED]',
                compact,
            )
            tag_pos = safe_page.find("(#")
            if tag_pos < 0:
                tag_pos = safe_page.casefold().find("prestige")
            if tag_pos < 0:
                tag_pos = safe_page.casefold().find("troph")
            if tag_pos >= 0:
                diagnostic = safe_page[max(0, tag_pos-300):tag_pos+1800]
            else:
                diagnostic = safe_page[:1800]
        print("BRAWLZONE RANKED PARSE MISS:", diagnostic, flush=True)
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



def _fallback_brawlzone_player(tag, timeout=15, enrich_ranked=True):
    """Legacy player source used before the official Supercell integration."""
    try:
        response = requests.get(
            f"{BRAWLZONE_BASE_URL}/{tag}",
            headers={"User-Agent": "Mozilla/5.0 (SensGPT-TitaniAbusivi/1.0)"},
            timeout=timeout,
        )
        if response.status_code != 200:
            print("BRAWLZONE PLAYER FALLBACK HTTP:", tag, response.status_code, flush=True)
            return None
        decoded = html.unescape(response.text)
        title_match = re.search(
            rf"<title>(.*?) \(#{re.escape(tag)}\) · BrawlZone</title>",
            decoded, re.I | re.S,
        )
        description_match = re.search(
            r"has ([\d,.]+) trophies and (\d+) brawlers", decoded, re.I
        )
        if not title_match or not description_match:
            print("BRAWLZONE PLAYER FALLBACK PARSE:", tag, flush=True)
            return None

        def find_stat(label):
            match = re.search(
                rf'children\\":\\"{re.escape(label)}\\".*?children\\":\\"([\d,.]+)\\"',
                decoded, re.I | re.S,
            )
            return _number(match.group(1)) if match else None

        level_match = re.search(r'\\\"Level \\\",\s*(\d+)', decoded)
        prestige_match = re.search(r'\\\"Prestige \\\",\s*\\\"?(\d+)', decoded)
        club_name = None
        club_tag = None
        anchors = re.findall(
            r'<a[^>]+href=["\\\']/(?:it/)?club/(?:%23|#)?([0289PYLQGRJCUV]{3,15})[^"\\\']*["\\\'][^>]*>(.*?)</a>',
            decoded, re.I | re.S,
        )
        for raw_club_tag, raw_name in anchors:
            clean_name = re.sub(r"<[^>]+>", " ", raw_name)
            clean_name = re.sub(r"\\s+", " ", html.unescape(clean_name)).strip()
            if clean_name and clean_name.casefold() not in {"club", "visualizza club", "view club"}:
                club_name = clean_name
                club_tag = _clean_tag(raw_club_tag)
                break
        if not club_name:
            club_match = re.search(
                r'"club"\\s*:\\s*\\{[^{}]{0,1200}?"tag"\\s*:\\s*"#?([0289PYLQGRJCUV]{3,15})"[^{}]{0,1200}?"name"\\s*:\\s*"([^"]+)"',
                decoded, re.I | re.S,
            )
            if club_match:
                club_tag = _clean_tag(club_match.group(1))
                club_name = html.unescape(club_match.group(2)).strip()

        result = {
            "name": html.unescape(title_match.group(1)).strip(),
            "tag": f"#{tag}",
            "trophies": _number(description_match.group(1)),
            "brawlers": _number(description_match.group(2)),
            "level": _number(level_match.group(1)) if level_match else None,
            "prestige": _number(prestige_match.group(1)) if prestige_match else None,
            "wins_3v3": find_stat("3v3 wins"),
            "wins_solo": find_stat("Solo SD wins"),
            "wins_duo": find_stat("Duo SD wins"),
            "club": club_name or "Senza club / non disponibile",
            "club_name": club_name,
            "club_tag": club_tag,
            "source": "BrawlZone legacy fallback",
        }
        if enrich_ranked:
            ranked_enrichment = _extract_brawlzone_ranked_only(decoded)
            for key, value in ranked_enrichment.items():
                if value is not None:
                    result[key] = value
            normalize_ranked_fields(result)
            print(
                "BRAWLZONE RANKED ENRICH:",
                tag,
                "current=", result.get("ranked_current"),
                "season=", result.get("ranked_season_peak"),
                "career=", result.get("ranked_career_peak"),
                flush=True,
            )
        else:
            print("BRAWLZONE RANKED ENRICH SKIPPED:", tag, flush=True)
        print("BRAWLZONE PLAYER FALLBACK OK:", tag, flush=True)
        return result
    except Exception as error:
        print("BRAWLZONE PLAYER FALLBACK ERROR:", tag, type(error).__name__, flush=True)
        return None


_ICON_CACHE = None

def _profile_icon_url(icon_id, timeout=10):
    global _ICON_CACHE
    if icon_id is None:
        return None
    try:
        if _ICON_CACHE is None:
            r=requests.get("https://api.brawlapi.com/v1/icons", timeout=timeout)
            r.raise_for_status()
            _ICON_CACHE=(r.json() or {}).get("player", {})
        item=_ICON_CACHE.get(str(icon_id)) or {}
        return item.get("imageUrl") or item.get("imageUrl2")
    except Exception as exc:
        print("ERRORE ICONA PROFILO:", repr(exc), flush=True)
        return None


def _fallback_brawltrack_player(tag, timeout=15, enrich_ranked=True):
    """Fallback player source when the official Supercell proxy is unavailable."""
    try:
        data = brawltrack_player(tag)
        if isinstance(data, dict):
            data = data.get("player") or data.get("data") or data
        if not isinstance(data, dict) or not data.get("name") or data.get("trophies") is None:
            return None
        raw_tag = data.get("tag") or data.get("playerTag") or data.get("player_tag")
        resolved_tag = _clean_tag(raw_tag) or f"#{tag}"
        if resolved_tag.replace("#", "") != tag:
            return None
        club = data.get("club") if isinstance(data.get("club"), dict) else {}
        club_name = club.get("name") or data.get("clubName") or data.get("club_name") or None
        club_tag = _clean_tag(club.get("tag") or data.get("clubTag") or data.get("club_tag"))
        raw_brawlers = data.get("brawlers")
        brawler_count = len(raw_brawlers) if isinstance(raw_brawlers, list) else _number(data.get("brawlerCount") or data.get("brawlersCount"))
        icon = data.get("icon") if isinstance(data.get("icon"), dict) else {}
        icon_id = _number(icon.get("id") or data.get("iconId") or data.get("icon_id"))
        club_display = club_name or "Senza club"
        if club_tag:
            club_display = f"{club_display}\nTag club: {club_tag}"
        result = {
            "name": data.get("name"),
            "tag": resolved_tag,
            "trophies": _number(data.get("trophies")),
            "brawlers": brawler_count,
            "level": _number(data.get("expLevel") or data.get("level")),
            "wins_3v3": _number(data.get("3vs3Victories") or data.get("wins3v3")),
            "wins_solo": _number(data.get("soloVictories") or data.get("soloWins")),
            "wins_duo": _number(data.get("duoVictories") or data.get("duoWins")),
            "club": club_display,
            "club_name": club_name,
            "club_tag": club_tag,
            "icon_id": icon_id,
            "icon_url": _profile_icon_url(icon_id),
            "source": "BrawlTrack fallback",
        }
        if enrich_ranked:
            enrichment = _legacy_enrichment(tag, timeout=min(timeout, 15))
            for key, value in enrichment.items():
                if value is not None:
                    result[key] = value
            normalize_ranked_fields(result)
        print("BRAWLTRACK PLAYER FALLBACK OK:", tag, flush=True)
        return result
    except (BrawlTrackError, ValueError, TypeError) as error:
        print("BRAWLTRACK PLAYER FALLBACK ERROR:", tag, type(error).__name__, flush=True)
        return _fallback_brawlzone_player(tag, timeout=timeout, enrich_ranked=enrich_ranked)


def get_brawltrack_player(player_tag, timeout=20, enrich_ranked=True):
    """Runtime player source. Supercell official is authoritative for every field it exposes."""
    tag = str(player_tag or "").upper().replace("#", "").strip()
    if not re.fullmatch(r"[0289PYLQGRJCUV]{3,15}", tag): return None

    api_proxy_key = str(os.environ.get("BRAWL_PROXY_API_KEY") or "").strip()
    proxy_url = os.environ.get("BRAWL_OFFICIAL_PROXY_URL")
    proxy_key = os.environ.get("BRAWL_OFFICIAL_PROXY_KEY")
    if not api_proxy_key and (not proxy_url or not proxy_key):
        print("SUPERCELL PROXY NON CONFIGURATO - uso fallback BrawlZone", flush=True)
        return _fallback_brawlzone_player(tag, timeout=timeout, enrich_ranked=enrich_ranked)

    try:
        if api_proxy_key:
            response = requests.get(
                "https://bsproxy.royaleapi.dev/v1/players/%23" + tag,
                headers={
                    "Authorization": "Bearer " + api_proxy_key,
                    "Accept": "application/json",
                },
                timeout=timeout,
            )
        else:
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
            # Safe diagnostic: distinguish a proxy/Imunify rejection from an
            # upstream Supercell error without logging secrets or full payloads.
            try:
                error_payload = response.json()
            except Exception:
                error_payload = None
            if isinstance(error_payload, dict):
                print(
                    "SUPERCELL PROXY HTTP:",
                    response.status_code,
                    "error=", error_payload.get("error"),
                    "upstream_status=", error_payload.get("upstream_status"),
                    flush=True,
                )
            else:
                content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0]
                print(
                    "SUPERCELL PROXY HTTP:",
                    response.status_code,
                    "non_json=1",
                    "content_type=", content_type or "unknown",
                    flush=True,
                )
            fallback = _fallback_brawlzone_player(tag, timeout=timeout, enrich_ranked=enrich_ranked)
            if fallback:
                return fallback
            return None
        data = response.json()
        if not isinstance(data, dict) or not data.get("name") or data.get("trophies") is None:
            return None

        club = data.get("club") if isinstance(data.get("club"), dict) else {}
        club_name = club.get("name") or None
        club_tag = _clean_tag(club.get("tag"))
        brawlers = data.get("brawlers") if isinstance(data.get("brawlers"), list) else []
        icon_data = data.get("icon") if isinstance(data.get("icon"), dict) else {}
        icon_id = _number(icon_data.get("id"))
        icon_url = _profile_icon_url(icon_id)

        # The old profile renderer prints only the `club` field. Include a newline
        # so Tag club is visible until all legacy renderers are removed.
        club_display = club_name or "Senza club"
        if club_tag:
            club_display = f"{club_display}\nTag club: {club_tag}"

        power_levels = {}
        collection = {"gadgets": 0, "star_powers": 0, "gears": 0, "hypercharges": 0, "buffies": 0}
        for brawler in brawlers:
            if not isinstance(brawler, dict):
                continue
            power = _number(brawler.get("power"))
            if power and 1 <= power <= 11:
                power_levels[power] = power_levels.get(power, 0) + 1
            collection["gadgets"] += len(brawler.get("gadgets") or [])
            collection["star_powers"] += len(brawler.get("starPowers") or [])
            collection["gears"] += len(brawler.get("gears") or [])
            collection["hypercharges"] += len(brawler.get("hyperCharges") or [])
            collection["buffies"] += len(brawler.get("buffies") or [])

        result = {
            "name": data.get("name"),
            "tag": _clean_tag(data.get("tag")) or f"#{tag}",
            "trophies": _number(data.get("trophies")),
            "brawlers": len(brawlers),
            "level": _number(data.get("expLevel")),
            "prestige": _number(data.get("totalPrestigeLevel")),
            "wins_3v3": _number(data.get("3vs3Victories")),
            "wins_solo": _number(data.get("soloVictories")),
            "wins_duo": _number(data.get("duoVictories")),
            "club": club_display,
            "club_name": club_name,
            "club_tag": club_tag,
            "icon_id": icon_id,
            "icon_url": icon_url,
            "power_levels": power_levels,
            "gadgets_owned": collection["gadgets"],
            "star_powers_owned": collection["star_powers"],
            "gears_owned": collection["gears"],
            "hypercharges_owned": collection["hypercharges"],
            "buffies_owned": collection["buffies"],
            "source": "Supercell Official API",
        }
        # Ranked/Prestigio are not supplied by the official player endpoint.
        if enrich_ranked:
            enrichment = _legacy_enrichment(tag, timeout=min(timeout, 15))
            for key, value in enrichment.items():
                if value is not None:
                    result[key] = value
            normalize_ranked_fields(result)
        print("SUPERCELL OFFICIAL PLAYER:", tag, club_name, club_tag, flush=True)
        return result
    except Exception as error:
        print("ERRORE SUPERCELL OFFICIAL:", tag, repr(error), flush=True)
        fallback = _fallback_brawlzone_player(tag, timeout=timeout, enrich_ranked=enrich_ranked)
        if fallback:
            return fallback
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
