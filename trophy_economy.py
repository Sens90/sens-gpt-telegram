"""Evidence-backed classification for the 2026 trophy economy.

Only rules repeatedly confirmed by official battle-log observations are encoded.
Unknown, protected/bot and survival cases intentionally remain unclassified.
"""


SURVIVAL_MODES = {"soloShowdown", "duoShowdown", "trioShowdown"}
SOLO_SHOWDOWN_BASES = (
    (0, 49, (13, 10, 10, 8, 6, 5, 5, 5, 5, 5)),
    (50, 99, (13, 10, 9, 7, 5, 4, 3, 2, 1, -1)),
    (100, 199, (13, 10, 9, 7, 4, 3, 2, 1, 0, -1)),
    (200, 299, (13, 10, 8, 5, 3, 2, 1, -1, -1, -1)),
    (300, 499, (13, 10, 8, 5, 3, 2, 1, -1, -2, -2)),
    (500, 599, (13, 10, 8, 5, 2, 1, -1, -1, -2, -2)),
    (600, 799, (13, 10, 8, 5, 2, 1, -2, -2, -3, -4)),
    (800, 999, (13, 10, 8, 5, 2, 1, -2, -3, -4, -5)),
    (1000, 1099, (13, 10, 8, 5, 1, -2, -3, -4, -5, -6)),
    (1100, 1199, (13, 10, 8, 5, 1, -2, -3, -4, -5, -7)),
    (1200, 1299, (13, 10, 8, 5, 1, -2, -4, -5, -6, -8)),
    (1300, 1499, (13, 10, 8, 5, 1, -2, -4, -6, -7, -10)),
    (1500, 1799, (13, 10, 8, 5, 0, -3, -5, -6, -8, -11)),
    (1800, 1999, (13, 10, 8, 5, 0, -3, -5, -7, -9, -12)),
)

# Current Duo/Trio Showdown placement tables. Positive trophyChange may
# include Win Streak / Underdog; the table stores the ordinary base delta.
SURVIVAL_PLACEMENT_BASES = {}
DUO_SHOWDOWN_BASES = (
    (0, 49, (12, 6, 5, 5, 5)), (50, 99, (12, 6, 4, 2, -1)),
    (100, 199, (12, 6, 3, 1, -1)), (200, 299, (12, 6, 2, -1, -1)),
    (300, 599, (12, 6, 2, -1, -2)), (600, 799, (12, 6, 2, -2, -3)),
    (800, 999, (12, 6, 2, -2, -4)), (1000, 1099, (12, 6, -1, -3, -5)),
    (1100, 1199, (12, 6, -1, -4, -6)), (1200, 1299, (12, 6, -1, -4, -7)),
    (1300, 1499, (12, 6, -2, -5, -8)), (1500, 1799, (12, 6, -2, -5, -9)),
    (1800, 1999, (12, 6, -2, -6, -10)),
)
TRIO_SHOWDOWN_BASES = (
    (0, 49, (12, 5, 5, 5)), (50, 99, (11, 5, 4, -1)),
    (100, 199, (11, 5, 3, -1)), (200, 299, (11, 5, 2, -1)),
    (300, 499, (11, 5, 2, -2)), (500, 599, (11, 5, 1, -2)),
    (600, 799, (11, 5, 1, -3)), (800, 999, (11, 5, 1, -4)),
    (1000, 1099, (11, 5, 0, -6)), (1100, 1199, (11, 5, 0, -7)),
    (1200, 1299, (11, 5, 0, -8)), (1300, 1499, (11, 5, 0, -9)),
    (1500, 1799, (11, 5, -5, -10)), (1800, 1999, (11, 5, -5, -11)),
)

# Exact negative deltas repeatedly observed for the same range and placement.
# Partial coverage is deliberate: possible Underdog reductions remain unknown.
SURVIVAL_LOSS_BASES = (
    ("soloShowdown", 300, 599, {7: -1, 8: -1, 9: -2, 10: -2}),
    ("soloShowdown", 600, 799, {7: -2, 8: -2, 9: -3}),
    ("soloShowdown", 800, 999, {7: -2, 9: -4, 10: -5}),
    ("soloShowdown", 1300, 1499, {10: -10}),
    ("duoShowdown", 1000, 1099, {4: -6}),
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
        base = None
        if mode == "soloShowdown" and 1 <= rank <= 10:
            for lower, upper, placement_bases in SOLO_SHOWDOWN_BASES:
                if lower <= trophies <= upper:
                    base = placement_bases[rank - 1]
                    break
        else:
            tables = DUO_SHOWDOWN_BASES if mode == "duoShowdown" else TRIO_SHOWDOWN_BASES
            max_rank = 5 if mode == "duoShowdown" else 4
            if 1 <= rank <= max_rank:
                for lower, upper, placement_bases in tables:
                    if lower <= trophies <= upper:
                        base = placement_bases[rank - 1]
                        break
        if base is not None and 0 <= trophies <= 1999 and base <= change <= base + 14:
            extra = change - base
            # The official battle log exposes the measurable extra but no
            # reliable cause field. Placement or extra size alone cannot prove
            # Win Streak or Underdog, so keep the cause explicitly unresolved.
            return base, extra, "bonus_observed" if extra else None
        for loss_mode, lower, upper, placement_bases in SURVIVAL_LOSS_BASES:
            expected = placement_bases.get(rank)
            if (mode == loss_mode and lower <= trophies <= upper
                    and expected is not None and expected <= change <= expected + 4):
                extra = change - expected
                return expected, extra, "bonus_observed" if extra else None
        return None, None, None

    normalized_result = str(result or "").lower()
    if normalized_result == "victory" and 0 <= trophies <= 1999:
        # +1 victories occur in protected/bot matches. +10 is the repeatedly
        # observed ordinary team base; +11..+20 carry an unresolved bonus.
        if 10 <= change <= 24:
            extra = change - 10
            return 10, extra, "bonus_observed" if extra else None
        return None, None, None

    if normalized_result == "defeat":
        for lower, upper, expected in TEAM_LOSS_BY_RANGE:
            if lower <= trophies <= upper and expected <= change <= expected + 4:
                extra = change - expected
                return expected, extra, "bonus_observed" if extra else None

    return None, None, None
