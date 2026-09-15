import html
import re

import requests


RANK_NAMES_IT = {
    "bronze": "Bronzo", "silver": "Argento", "gold": "Oro",
    "diamond": "Diamante", "mythic": "Mito", "legendary": "Leggenda",
    "masters": "Campione", "master": "Campione", "pro": "Pro",
}
BRAWLTRACK_BASE_URL = "https://brawltrack.app"
BRAWLIFY_BASE_URL = "https://brawlify.com/it/player"


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


def _first(data, *paths):
    for path in paths:
        current = data
        for key in path.split("."):
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if current not in (None, "", [], {}): return current
    return None


def _club(data):
    club = _first(data, "club", "activeClub", "player.club", "profile.club")
    if isinstance(club, dict):
        return (
            club.get("name") or club.get("clubName"),
            club.get("tag") or club.get("clubTag") or club.get("id"),
        )
    if isinstance(club, str):
        return club, _first(data, "clubTag", "player.clubTag", "profile.clubTag")
    return (
        _first(data, "clubName", "player.clubName", "profile.clubName"),
        _first(data, "clubTag", "player.clubTag", "profile.clubTag"),
    )


def _clean_tag(value):
    if not value:
        return None
    tag = str(value).upper().replace("#", "").strip()
    return f"#{tag}" if re.fullmatch(r"[0289PYLQGRJCUV]{3,15}", tag) else None


def get_live_club(player_tag, timeout=15):
    """Refresh club independently so a cached profile club cannot win."""
    tag = str(player_tag or "").upper().replace("#", "").strip()
    if not re.fullmatch(r"[0289PYLQGRJCUV]{3,15}", tag):
        return None, None
    try:
        response = requests.get(
            f"{BRAWLIFY_BASE_URL}/{tag}?refresh={int(__import__('time').time())}",
            headers={
                "User-Agent": "Mozilla/5.0 (SensGPT-TitaniAbusivi/1.0)",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
            timeout=timeout,
        )
        if response.status_code != 200:
            return None, None
        page = html.unescape(response.text)
        anchors = re.findall(
            r'<a[^>]+href=["\']/(?:it/)?club/(?:%23|#)?([0289PYLQGRJCUV]{3,15})[^"\']*["\'][^>]*>(.*?)</a>',
            page,
            re.I | re.S,
        )
        for club_tag, body in anchors:
            name = re.sub(r"<[^>]+>", " ", body)
            name = re.sub(r"\s+", " ", html.unescape(name)).strip()
            if name and name.casefold() not in {"club", "visualizza club", "view club"}:
                return name, _clean_tag(club_tag)
        match = re.search(
            r'"club"\s*:\s*\{[^{}]{0,1000}?"tag"\s*:\s*"#?([0289PYLQGRJCUV]{3,15})"[^{}]{0,1000}?"name"\s*:\s*"([^"]+)"',
            page,
            re.I | re.S,
        )
        if not match:
            match = re.search(
                r'"club"\s*:\s*\{[^{}]{0,1000}?"name"\s*:\s*"([^"]+)"[^{}]{0,1000}?"tag"\s*:\s*"#?([0289PYLQGRJCUV]{3,15})"',
                page,
                re.I | re.S,
            )
            if match:
                return html.unescape(match.group(1)).strip(), _clean_tag(match.group(2))
        elif match:
            return html.unescape(match.group(2)).strip(), _clean_tag(match.group(1))
        return None, None
    except Exception as error:
        print("ERRORE CLUB LIVE:", repr(error), flush=True)
        return None, None


def _rank_value(data, *keys):
    value = _first(data, *keys)
    if isinstance(value, dict):
        name = value.get("name") or value.get("rank") or value.get("tier") or value.get("division")
        elo = value.get("elo") or value.get("points") or value.get("score")
        return translate_rank(str(name)) if name else None, _number(elo)
    return (translate_rank(str(value)) if value else None), None


def get_brawltrack_player(player_tag, timeout=20):
    """Primary player source: BrawlTrack, with independently refreshed club."""
    tag = str(player_tag or "").upper().replace("#", "").strip()
    if not re.fullmatch(r"[0289PYLQGRJCUV]{3,15}", tag): return None
    try:
        response = requests.get(
            f"{BRAWLTRACK_BASE_URL}/api/player/{tag}",
            headers={"User-Agent": "SensGPT-TitaniAbusivi/1.0", "Accept": "application/json", "Cache-Control": "no-cache"},
            timeout=timeout,
        )
        if response.status_code == 404: return None
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict): return None
        root = data.get("player") if isinstance(data.get("player"), dict) else data
        brawlers = _first(root, "brawlers", "stats.brawlers", "profile.brawlers")
        brawlers = len(brawlers) if isinstance(brawlers, list) else _number(brawlers)
        current_rank, current_elo = _rank_value(root, "ranked.current", "rankedCurrent", "currentRanked", "ranked.currentRank")
        season_rank, season_elo = _rank_value(root, "ranked.seasonPeak", "ranked.seasonBest", "rankedSeasonPeak", "seasonBest")
        career_rank, career_elo = _rank_value(root, "ranked.careerPeak", "ranked.allTimeBest", "rankedCareerPeak", "allTimeBest")
        current_elo = current_elo or _number(_first(root, "ranked.currentElo", "rankedCurrentElo", "currentElo"))
        season_elo = season_elo or _number(_first(root, "ranked.seasonPeakElo", "ranked.seasonBestElo", "seasonPeakElo"))
        career_elo = career_elo or _number(_first(root, "ranked.careerPeakElo", "ranked.allTimeBestElo", "careerPeakElo"))
        club_name, club_tag = _club(root)
        if not club_name:
            club_name, club_tag = _club(data)
        live_name, live_tag = get_live_club(tag)
        if live_name:
            club_name, club_tag = live_name, live_tag
        club_tag = _clean_tag(club_tag)
        club_display = club_name
        if club_name and club_tag:
            club_display = f"{club_name} ({club_tag})"
        result = {
            "name": _first(root, "name", "playerName", "profile.name") or _first(data, "name", "playerName"),
            "tag": f"#{tag}",
            "trophies": _number(_first(root, "trophies", "stats.trophies", "profile.trophies")),
            "brawlers": brawlers,
            "level": _number(_first(root, "level", "expLevel", "profile.level")),
            "prestige": _number(_first(root, "prestige", "profile.prestige")),
            "wins_3v3": _number(_first(root, "wins3v3", "stats.wins3v3", "battleStats.wins3v3")),
            "wins_solo": _number(_first(root, "winsSolo", "soloVictories", "stats.winsSolo")),
            "wins_duo": _number(_first(root, "winsDuo", "duoVictories", "stats.winsDuo")),
            "club": club_display,
            "club_name": club_name,
            "club_tag": club_tag,
            "ranked_current": current_rank, "ranked_current_elo": current_elo,
            "ranked_season_peak": season_rank, "ranked_season_peak_elo": season_elo,
            "ranked_career_peak": career_rank, "ranked_career_peak_elo": career_elo,
            "source": "BrawlTrack",
        }
        result["ranked_peak"] = career_rank
        result["ranked_peak_elo"] = career_elo
        return result
    except Exception as error:
        print("ERRORE BRAWLTRACK PLAYER:", repr(error), flush=True)
        return None


def _extract_brawlzone_ranked_only(page):
    labels = {"ranked_current": "Current ranked", "ranked_season_peak": "Season best", "ranked_career_peak": "All-time best"}
    result = {}
    for key, label in labels.items():
        match = re.search(rf">\s*{re.escape(label)}\s*</p>\s*<p[^>]*>\s*([^<]+?)\s*</p>\s*<p[^>]*>\s*([\d.,]+)\s*elo\s*</p>", page, re.I | re.S)
        if not match:
            match = re.search(rf'children\\?":\\?"{re.escape(label)}\\?".*?children\\?":\\?"([^"\\]+)\\?".*?children\\?":\\?"([\d.,]+)\s*elo\\?"', page, re.I | re.S)
        if match:
            result[key] = translate_rank(match.group(1)); result[f"{key}_elo"] = _number(match.group(2))
    result["ranked_peak"] = result.get("ranked_career_peak")
    result["ranked_peak_elo"] = result.get("ranked_career_peak_elo")
    return result


def extract_brawlzone_ranked(page):
    """BrawlTrack first; if it 404s, refresh club separately before returning fallback data."""
    fallback = _extract_brawlzone_ranked_only(page)
    tag_match = re.search(r"\(#([0289PYLQGRJCUV]{3,15})\)", page or "", re.I)
    if not tag_match:
        return fallback
    tag = tag_match.group(1)
    primary = get_brawltrack_player(tag) or {}
    merged = {k: v for k, v in primary.items() if v is not None}
    for key, value in fallback.items():
        if merged.get(key) is None: merged[key] = value

    # Critical fallback path: BrawlTrack currently returns 404 for some valid
    # players. Never let the BrawlZone fallback keep a stale club. Resolve the
    # current club independently on every profile request and overwrite all
    # club fields only when a live club is actually verified.
    live_name, live_tag = get_live_club(tag)
    if live_name or live_tag:
        merged["club_name"] = live_name
        merged["club_tag"] = live_tag
        merged["club"] = (
            f"{live_name} ({live_tag})" if live_name and live_tag
            else live_name or live_tag
        )

    merged["ranked_peak"] = merged.get("ranked_career_peak") or merged.get("ranked_peak")
    merged["ranked_peak_elo"] = merged.get("ranked_career_peak_elo") or merged.get("ranked_peak_elo")
    return merged
