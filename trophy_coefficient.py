"""Trophy coefficient for Sens GPT.

The score is computed Brawler-by-Brawler from current trophies. It is a
structural difficulty index, not an estimate of historical wins/modes.

Real trophies are always preserved at base weight 1. Difficulty is added as
a marginal premium by trophy band. The premium is capped at 3000 trophies
per Brawler; trophies above 3000 continue to contribute at raw weight 1.
"""

from decimal import Decimal, ROUND_HALF_UP

TROPHY_COEFFICIENT_BANDS = (
    (0, 50, 1.0000),
    (50, 100, 1.0250),
    (100, 200, 1.0285),
    (200, 300, 1.0320),
    (300, 500, 1.0500),
    (500, 600, 1.0590),
    (600, 800, 1.0680),
    (800, 1000, 1.0970),
    (1000, 1100, 1.1180),
    (1100, 1200, 1.1360),
    (1200, 1300, 1.1650),
    (1300, 1500, 1.1900),
    (1500, 1800, 1.2080),
    (1800, 2000, 1.2370),
    (2000, 2200, 1.5500),
    (2200, 2300, 1.5900),
    (2300, 2400, 1.6300),
    (2400, 2500, 1.6700),
    (2500, 2600, 1.7285),
    (2600, 2700, 1.8000),
    (2700, 2800, 1.8800),
    (2800, 3000, 2.0000),
)

PREMIUM_CAP = 3000
WEIGHT_SCALE = 10_000


def score_brawler_trophies(trophies):
    """Return the weighted score for one Brawler's current trophies."""
    try:
        trophies = max(0, int(trophies or 0))
    except (TypeError, ValueError):
        trophies = 0

    # Accumulate scaled integers so every four-decimal band weight is exact.
    # Using binary floats here made the premium above 3,000 differ from the
    # expected raw x1 amount by a tiny rounding residue.
    score_units = 0
    for start, end, weight in TROPHY_COEFFICIENT_BANDS:
        amount = max(0, min(trophies, end) - start)
        weight_units = int(Decimal(str(weight)) * WEIGHT_SCALE)
        score_units += amount * weight_units

    if trophies > PREMIUM_CAP:
        score_units += (trophies - PREMIUM_CAP) * WEIGHT_SCALE
    return Decimal(score_units) / WEIGHT_SCALE


def calculate_trophy_coefficient(brawler_trophies, official_total=None):
    """Return score/coefficient from the current per-Brawler distribution."""
    rows = brawler_trophies if isinstance(brawler_trophies, list) else []
    detail_total = 0
    weighted_total = Decimal(0)

    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            trophies = max(0, int(row.get("trophies") or 0))
        except (TypeError, ValueError):
            trophies = 0
        detail_total += trophies
        weighted_total += score_brawler_trophies(trophies)

    try:
        raw_total = max(0, int(official_total)) if official_total is not None else detail_total
    except (TypeError, ValueError):
        raw_total = detail_total

    if raw_total > detail_total:
        weighted_total += raw_total - detail_total

    rounded_score = int(weighted_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    coefficient = float(weighted_total / raw_total) if raw_total else 1.0
    return {
        "score": rounded_score,
        "coefficient": round(coefficient, 6),
        "detail_total": detail_total,
        "official_total": raw_total,
    }
