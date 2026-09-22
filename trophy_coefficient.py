"""Trophy coefficient for Sens GPT.

The score is computed Brawler-by-Brawler from current trophies.  It is a
structural difficulty index, not an estimate of historical wins/modes.

Sources/rationale (Feb 2026 trophy system):
- Supercell: win-streak/underdog/bots are enabled through 1999 and disabled
  from 2000; Prestige gates are every 1000 trophies.
- Current trophy-economy tables provide the marginal pressure calibration.
The premium is intentionally capped at 3000 trophies per Brawler; trophies
above 3000 still contribute their raw value (1 point each), so account score
never discards real trophies.
"""

# Half-open intervals [start, end), with marginal score per trophy.
# The finer 2k+ bands preserve Showdown sub-thresholds instead of flattening
# them into only the 3v3 breakpoints.
TROPHY_COEFFICIENT_BANDS = (
    (0, 1100, 1.0000),
    (1100, 1200, 1.1304),
    (1200, 1300, 1.3552),
    (1300, 1500, 1.5589),
    (1500, 1800, 1.8227),
    (1800, 2000, 2.0646),
    (2000, 2200, 2.6184),
    (2200, 2300, 2.6534),
    (2300, 2400, 2.7228),
    (2400, 2500, 2.7994),
    (2500, 2600, 3.8610),
    (2600, 2700, 3.8912),
    (2700, 2800, 3.9228),
    (2800, 3000, 5.6845),
)

PREMIUM_CAP = 3000


def score_brawler_trophies(trophies):
    """Return the weighted score for one Brawler's current trophies."""
    try:
        trophies = max(0, int(trophies or 0))
    except (TypeError, ValueError):
        trophies = 0

    score = 0.0
    for start, end, weight in TROPHY_COEFFICIENT_BANDS:
        amount = max(0, min(trophies, end) - start)
        score += amount * weight

    # "Fino a 3k": stop adding difficulty premium, but never throw away raw
    # account trophies above the cap.
    if trophies > PREMIUM_CAP:
        score += trophies - PREMIUM_CAP
    return score


def calculate_trophy_coefficient(brawler_trophies, official_total=None):
    """Return score/coefficient from the current per-Brawler distribution.

    official_total is used as denominator/raw baseline when supplied. This
    keeps the displayed account total authoritative even if a future API
    response temporarily omits a Brawler from the detail array.
    """
    rows = brawler_trophies if isinstance(brawler_trophies, list) else []
    detail_total = 0
    weighted_total = 0.0

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

    # If detail is incomplete, preserve every official trophy at base weight 1.
    if raw_total > detail_total:
        weighted_total += raw_total - detail_total

    coefficient = weighted_total / raw_total if raw_total else 1.0
    return {
        "score": int(round(weighted_total)),
        "coefficient": round(coefficient, 6),
        "detail_total": detail_total,
        "official_total": raw_total,
    }
