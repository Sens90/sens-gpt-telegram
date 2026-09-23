"""Evidence-backed classification for the 2026 trophy economy.

Only rules repeatedly confirmed by official battle-log observations are encoded.
Unknown, protected/bot and survival cases intentionally remain unclassified.
"""


SURVIVAL_MODES = {"soloShowdown", "duoShowdown"}
SURVIVAL_PLACEMENT_BASES = {
    "soloShowdown": {1: 13, 2: 10, 3: 9, 4: 5, 5: 2, 6: 1},
    "duoShowdown": {1: 11, 2: 5},
}

# Exact negative deltas repeatedly observed for the same range and placement.
# Partial coverage is deliberate: possible Underdog reductions remain unknown.
SURVIVAL_LOSS_BASES = (
    ("soloShowdown", 300, 599, {7: -1, 8: -1, 9: -2, 10: -2}),
    ("soloShowdown", 600, 799, {7: -2, 8: -2, 9: -3}),
    ("soloShowdown", 800, 999, {7: -2, 9: -4, 10: -5}),
    ("soloShowdown", 1300, 1499, {10: -10}),
    ("duoShowdown", 300, 599, {4: -2}),
    ("duoShowdown", 1000, 1099, {4: -6}),
    ("duoShowdown", 1300, 1499, {4: -9}),
    ("duoShowdown", 1500, 1799, {3: -5, 4: -10}),
)

# Repeated exact losses observed across multiple ordinary team modes.
TEAM_LOSS_BY_RANGE = (
    (300, 599, -2),
    (600, 799, -3),
    (800, 999, -4),
    (1000, 1099, -5),
    (1100, 1199, -6),
    (1200, 1299, -7),
    (1300, 1499, -8),
    (1500, 1799, -9),
)


def classify_trophy_change(mode, result, trophies_before, trophy_change, placement=None):
    """Return (base delta, observed extra, type), or unknown Nones.

    The API does not expose whether a positive extra is streak, underdog or
    another bonus. We therefore record the measurable extra without inventing
    its cause.
    """
    try:
        trophies = int(trophies_before)
        change = int(trophy_change)
    except (TypeError, ValueError):
        return None, None, None

    if not mode or trophies >= 2000:
        return None, None, None

    if mode in SURVIVAL_MODES:
        try:
            rank = int(placement)
        except (TypeError, ValueError):
            return None, None, None
        base = SURVIVAL_PLACEMENT_BASES.get(mode, {}).get(rank)
        if base is not None and 300 <= trophies <= 1999 and base <= change <= base + 10:
            extra = change - base
            return base, extra, "observed_extra_unresolved" if extra else None
        for loss_mode, lower, upper, placement_bases in SURVIVAL_LOSS_BASES:
            expected = placement_bases.get(rank)
            if (mode == loss_mode and lower <= trophies <= upper
                    and expected is not None and expected <= change <= expected + 4):
                extra = change - expected
                return expected, extra, "observed_extra_unresolved" if extra else None
        return None, None, None

    normalized_result = str(result or "").lower()
    if normalized_result == "victory" and 300 <= trophies <= 1799:
        # +1 victories occur in protected/bot matches. +10 is the repeatedly
        # observed ordinary team base; +11..+20 carry an unresolved bonus.
        if 10 <= change <= 20:
            extra = change - 10
            return 10, extra, "observed_extra_unresolved" if extra else None
        return None, None, None

    if normalized_result == "defeat":
        for lower, upper, expected in TEAM_LOSS_BY_RANGE:
            if lower <= trophies <= upper and expected <= change <= expected + 4:
                extra = change - expected
                return expected, extra, "observed_extra_unresolved" if extra else None

    return None, None, None
