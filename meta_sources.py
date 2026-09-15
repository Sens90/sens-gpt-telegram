import re
import requests

BRAWL_PLANET_IT = "https://www.brawlplanet.com/it"


def _clean(value):
    value = re.sub(r"Image", "", str(value or ""), flags=re.I)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" |\n\t")


def _section(text, start_markers, end_markers=()):
    lower = text.casefold()
    starts = [lower.find(marker.casefold()) for marker in start_markers]
    starts = [pos for pos in starts if pos >= 0]
    if not starts:
        return ""
    start = min(starts)
    ends = [lower.find(marker.casefold(), start + 1) for marker in end_markers]
    ends = [pos for pos in ends if pos > start]
    end = min(ends) if ends else len(text)
    return text[start:end]


def _parse_individual(section):
    rows = []
    pattern = re.compile(
        r"^\s*([A-Za-zÀ-ÿ0-9 .&’'\-]+?)\s*\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*\|\s*([0-9]+(?:\.[0-9]+)?)\s*$",
        re.M,
    )
    for match in pattern.finditer(section):
        name = _clean(match.group(1))
        if name.casefold() in {"brawler", "---"}:
            continue
        rows.append({
            "brawler": name,
            "win_rate": float(match.group(2)),
            "pick_rate": float(match.group(3)),
            "star_player_rate": float(match.group(4)),
        })
    return rows


def _parse_teams(section):
    teams = []
    pattern = re.compile(
        r"(?:Image\s*){0,3}([A-Za-zÀ-ÿ0-9 .&’'\-]+)\s*[·•]\s*"
        r"([A-Za-zÀ-ÿ0-9 .&’'\-]+)\s*[·•]\s*([A-Za-zÀ-ÿ0-9 .&’'\-]+)"
        r"\s*\|\s*([0-9]+(?:\.[0-9]+)?)",
        re.M,
    )
    for match in pattern.finditer(section):
        members = [_clean(match.group(i)) for i in range(1, 4)]
        # Ranked draft cannot contain duplicate brawlers. Reject malformed rows.
        if len({name.casefold() for name in members}) != 3:
            continue
        teams.append({"brawlers": members, "win_rate": float(match.group(4))})
    return teams


def parse_brawlplanet_map_page(text):
    """Keep Trophy ladder and Ranked datasets strictly separated."""
    trophy = _section(
        text,
        ("partite a trofei", "trophy-ladder matches", "trophy ladder"),
        ("partite classificate", "ranked matches", "all ranks"),
    )
    ranked = _section(
        text,
        ("partite classificate", "ranked matches", "all ranks"),
    )
    return {
        "ladder": {
            "individual": _parse_individual(trophy),
            "teams": _parse_teams(trophy),
        },
        "ranked": {
            "individual": _parse_individual(ranked),
            "teams": _parse_teams(ranked),
        },
    }


def leaders(dataset):
    rows = dataset.get("individual") or []
    teams = dataset.get("teams") or []
    return {
        "best_win_rate": max(rows, key=lambda row: row["win_rate"], default=None),
        "most_picked": max(rows, key=lambda row: row["pick_rate"], default=None),
        "best_star_player": max(rows, key=lambda row: row["star_player_rate"], default=None),
        "best_team": max(teams, key=lambda row: row["win_rate"], default=None),
    }


def fetch_brawlplanet_map(slug, timeout=20):
    """Brawl Planet fallback for map/meta data; returns no guessed values."""
    url = f"{BRAWL_PLANET_IT}/maps/{slug.strip('/')}"
    response = requests.get(url, headers={"User-Agent": "SensGPT/1.0"}, timeout=timeout)
    response.raise_for_status()
    parsed = parse_brawlplanet_map_page(response.text)
    parsed["source"] = url
    return parsed
