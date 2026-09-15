from datetime import datetime, timezone
from unittest.mock import patch

import app


def test_today_uses_first_snapshot_of_day_when_no_midnight_snapshot():
    history = [
        {"trophies": 1000, "recorded_at": "2026-09-15T08:00:00+00:00"},
        {"trophies": 1025, "recorded_at": "2026-09-15T12:00:00+00:00"},
    ]
    changes = app.calculate_trophy_changes(history, 1025)
    assert changes["today"] == 25
    assert changes["7d"] is None
