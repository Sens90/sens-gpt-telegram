from observed_trophy_battles import observed_battle_rows


def test_unverified_team_change_stays_unclassified():
    payload = {
        "items": [{
            "battleTime": "20260923T013712.000Z",
            "event": {"id": 15000000, "mode": "gemGrab"},
            "battle": {
                "mode": "gemGrab", "result": "victory", "trophyChange": 8,
                "teams": [[{"tag": "#2GU9UV2RG", "name": "DeSS", "brawler": {
                    "name": "SHELLY", "trophies": 1000,
                }}]],
            },
        }],
    }
    rows = observed_battle_rows("2GU9UV2RG", "DeSS", payload)
    assert len(rows) == 1
    row = rows[0]
    assert row["battle_time"] == "2026-09-23T01:37:12+00:00"
    assert row["brawler_trophies_before"] == 1000
    assert row["trophy_change"] == 8
    assert row["mode"] == "gemGrab"
    assert row["result"] == "victory"
    assert row["expected_base_delta"] is None
    assert row["observed_extra"] is None
    assert row["bonus_type"] is None


def test_verified_team_base_and_unresolved_extra_are_classified():
    payload = {"items": [{
        "battleTime": "20260923T013712.000Z",
        "event": {"mode": "brawlBall"},
        "battle": {
            "mode": "brawlBall", "result": "victory", "trophyChange": 17,
            "teams": [[{"tag": "#2GU9UV2RG", "brawler": {
                "name": "SHELLY", "trophies": 1200,
            }}]],
        },
    }]}
    row = observed_battle_rows("2GU9UV2RG", "DeSS", payload)[0]
    assert row["expected_base_delta"] == 10
    assert row["observed_extra"] == 7
    assert row["bonus_type"] == "observed_extra_unresolved"


def test_verified_team_loss_is_classified_without_bonus():
    payload = {"items": [{
        "battleTime": "20260923T013712.000Z",
        "event": {"mode": "heist"},
        "battle": {
            "mode": "heist", "result": "defeat", "trophyChange": -8,
            "teams": [[{"tag": "#2GU9UV2RG", "brawler": {
                "name": "COLT", "trophies": 1400,
            }}]],
        },
    }]}
    row = observed_battle_rows("2GU9UV2RG", "DeSS", payload)[0]
    assert row["expected_base_delta"] == -8
    assert row["observed_extra"] == 0
    assert row["bonus_type"] is None


def test_protected_and_2000_plus_cases_stay_unclassified():
    from trophy_economy import classify_trophy_change

    assert classify_trophy_change("knockout", "victory", 500, 1) == (None, None, None)
    assert classify_trophy_change("siege", "victory", 2100, 15) == (None, None, None)


def test_verified_survival_positive_placement_bases_are_classified():
    from trophy_economy import classify_trophy_change

    assert classify_trophy_change("soloShowdown", None, 900, 23, 1) == (
        13, 10, "observed_extra_unresolved"
    )
    assert classify_trophy_change("duoShowdown", None, 1500, 11, 1) == (11, 0, None)
    assert classify_trophy_change("duoShowdown", None, 1500, 7, 2) == (
        5, 2, "observed_extra_unresolved"
    )
    assert classify_trophy_change("soloShowdown", None, 500, 15, 4) == (
        5, 10, "observed_extra_unresolved"
    )
    assert classify_trophy_change("soloShowdown", None, 900, 2, 5) == (2, 0, None)
    assert classify_trophy_change("soloShowdown", None, 700, 1, 6) == (1, 0, None)
    assert classify_trophy_change("soloShowdown", None, 700, -2, 7) == (None, None, None)
    assert classify_trophy_change("soloShowdown", None, 2100, 13, 1) == (None, None, None)


def test_missing_trophy_change_is_not_persisted():
    payload = {"items": [{
        "battleTime": "20260923T013712.000Z",
        "event": {"mode": "friendly"},
        "battle": {"mode": "friendly", "players": [{"tag": "#2GU9UV2RG"}]},
    }]}
    assert observed_battle_rows("2GU9UV2RG", "DeSS", payload) == []


def test_solo_placement_and_explicit_streak_are_preserved():
    payload = {"items": [{
        "battleTime": "20260923T020000Z",
        "event": {"mode": "soloShowdown"},
        "battle": {
            "mode": "soloShowdown", "rank": 2, "trophyChange": 6,
            "players": [{"tag": "#2GU9UV2RG", "brawler": {
                "name": "CROW", "trophies": 1500, "currentWinStreak": 4,
            }}],
        },
    }]}
    row = observed_battle_rows("#2GU9UV2RG", "DeSS", payload)[0]
    assert row["placement"] == 2
    assert row["current_win_streak"] == 4
    assert row["brawler_trophies_before"] == 1500
