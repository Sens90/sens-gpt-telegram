from decimal import Decimal

from trophy_coefficient import (
    TROPHY_COEFFICIENT_BANDS,
    calculate_trophy_coefficient,
    score_brawler_trophies,
)


def test_thresholds_are_monotonic():
    points = [0, 49, 50, 99, 100, 199, 200, 299, 300, 499, 500, 599,
              600, 799, 800, 999, 1000, 1099, 1100, 1199, 1200, 1999,
              2000, 2199, 2200, 2499, 2500, 2799, 2800, 2999, 3000, 3001, 4000]
    scores = [score_brawler_trophies(value) for value in points]
    assert scores == sorted(scores)


def test_real_trophies_are_never_discounted():
    for trophies in (1, 49, 50, 99, 100, 299, 500, 999, 2000, 3000, 4000):
        assert score_brawler_trophies(trophies) >= trophies


def test_above_3000_keeps_raw_trophies_without_extra_premium():
    assert score_brawler_trophies(3001) - score_brawler_trophies(3000) == 1
    assert score_brawler_trophies(4000) - score_brawler_trophies(3000) == 1000


def test_each_band_uses_its_exact_marginal_weight():
    for start, end, weight in TROPHY_COEFFICIENT_BANDS:
        if end <= start:
            continue
        assert score_brawler_trophies(start + 1) - score_brawler_trophies(start) == Decimal(str(weight))


def test_same_total_distribution_can_score_differently():
    flat = calculate_trophy_coefficient([{"trophies": 1000}, {"trophies": 1000}], 2000)
    pushed = calculate_trophy_coefficient([{"trophies": 1500}, {"trophies": 500}], 2000)
    assert pushed["score"] > flat["score"]
    assert pushed["coefficient"] > flat["coefficient"]


def test_official_total_is_preserved_when_detail_is_incomplete():
    result = calculate_trophy_coefficient([{"trophies": 1000}], 1100)
    assert result["score"] >= 1100
    assert result["official_total"] == 1100
