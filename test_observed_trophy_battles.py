from observed_trophy_battles import observed_battle_rows


def test_team_battle_is_normalized_without_inventing_bonus():
    payload = {
        "items": [{
            "battleTime": "20260923T013712.000Z",
            "event": {"id": 15000000, "mode": "gemGrab"},
            "battle": {
                "mode": "gemGrab", "result": "victory", "trophyChange": 8,
                "teams": [[{"tag": "#2GU9UV2RG", "name": "DeSS", "brawler": {
                    "name": "SHELLY", "trophies": 1008,
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
                "name": "CROW", "trophies": 1506, "currentWinStreak": 4,
            }}],
        },
    }]}
    row = observed_battle_rows("#2GU9UV2RG", "DeSS", payload)[0]
    assert row["placement"] == 2
    assert row["current_win_streak"] == 4
    assert row["brawler_trophies_before"] == 1500
