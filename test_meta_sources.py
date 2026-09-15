from meta_sources import parse_brawlplanet_map_page, leaders


def test_ladder_and_ranked_are_not_mixed():
    page = """
    Win rates from 100 trophy-ladder matches on this map.
    Brawler | Win | Pick | Star
    Colt | 60.0 | 40.0 | 20.0
    Jessie | 55.0 | 50.0 | 30.0
    Teams
    Colt · Jessie · Brock | 70.0
    All ranks
    Win rates from 200 Ranked matches on this map, across all leagues.
    Brawler | Win | Pick | Star
    Piper | 65.0 | 35.0 | 45.0
    Nani | 62.0 | 30.0 | 50.0
    Teams
    Piper · Nani · Gus | 75.0
    """
    parsed = parse_brawlplanet_map_page(page)
    ladder = leaders(parsed["ladder"])
    ranked = leaders(parsed["ranked"])

    assert ladder["best_win_rate"]["brawler"] == "Colt"
    assert ladder["most_picked"]["brawler"] == "Jessie"
    assert ladder["best_star_player"]["brawler"] == "Jessie"
    assert ladder["best_team"]["brawlers"] == ["Colt", "Jessie", "Brock"]

    assert ranked["best_win_rate"]["brawler"] == "Piper"
    assert ranked["most_picked"]["brawler"] == "Piper"
    assert ranked["best_star_player"]["brawler"] == "Nani"
    assert ranked["best_team"]["brawlers"] == ["Piper", "Nani", "Gus"]
