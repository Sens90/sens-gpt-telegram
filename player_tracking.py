import re


RANK_NAMES_IT = {
    "bronze": "Bronzo",
    "silver": "Argento",
    "gold": "Oro",
    "diamond": "Diamante",
    "mythic": "Mitico",
    "legendary": "Leggendario",
    "masters": "Maestri",
    "master": "Maestri",
    "pro": "Pro",
}


def _number(value):
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else None


def translate_rank(rank):
    rank = re.sub(r"\s+", " ", (rank or "").strip())
    if not rank:
        return None
    parts = rank.split(" ", 1)
    translated = RANK_NAMES_IT.get(parts[0].casefold(), parts[0])
    return f"{translated} {parts[1]}" if len(parts) > 1 else translated


def extract_brawlzone_ranked(page):
    """Extract current, seasonal and career Ranked values from a BrawlZone page."""
    labels = {
        "ranked_current": "Current ranked",
        "ranked_season_peak": "Season best",
        "ranked_career_peak": "All-time best",
    }
    result = {}

    for key, label in labels.items():
        match = re.search(
            rf">\s*{re.escape(label)}\s*</p>\s*"
            rf"<p[^>]*>\s*([^<]+?)\s*</p>\s*"
            rf"<p[^>]*>\s*([\d.,]+)\s*elo\s*</p>",
            page,
            re.I | re.S,
        )
        if not match:
            # Next.js also embeds the same cards in its streamed payload.
            match = re.search(
                rf'children\\?":\\?"{re.escape(label)}\\?".*?'
                rf'children\\?":\\?"([^"\\]+)\\?".*?'
                rf'children\\?":\\?"([\d.,]+)\s*elo\\?"',
                page,
                re.I | re.S,
            )
        if match:
            result[key] = translate_rank(match.group(1))
            result[f"{key}_elo"] = _number(match.group(2))

    # Compatibility names used by the existing registration/profile code.
    result["ranked_peak"] = result.get("ranked_career_peak")
    result["ranked_peak_elo"] = result.get("ranked_career_peak_elo")
    return result
