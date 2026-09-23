import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-role-key")

with patch("google.genai.Client", return_value=Mock()):
    import app


class AutomaticRankingSlotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        app._AUTO_RANKING_IN_FLIGHT.clear()
        self.context = SimpleNamespace(bot=SimpleNamespace())
        self.settings = [{"chat_id": -100123, "last_auto_ranking_slot": "2026-09-23-1200"}]

    async def test_failed_telegram_delivery_does_not_complete_slot(self):
        with (
            patch.object(app.community, "_get", return_value=self.settings),
            patch.object(app.community, "ranking_text", return_value="CLASSIFICA TROFEI"),
            patch.object(app.community, "coefficient_ranking_text", return_value={"text": "PROGRESSIONE"}),
            patch.object(app.community, "_send_ranking_message", new=AsyncMock(side_effect=[True, False])),
            patch.object(app.requests, "patch") as database_patch,
        ):
            await app._send_auto_ranking_slot(
                self.context, datetime(2026, 9, 23, 18, 0, tzinfo=app.ROME)
            )

        database_patch.assert_not_called()
        self.assertFalse(app._AUTO_RANKING_IN_FLIGHT)

    async def test_slot_completes_only_after_both_reports_are_delivered(self):
        events = []

        async def deliver(*_args):
            events.append("telegram")
            return True

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [{"chat_id": -100123}]

        def complete(*_args, **_kwargs):
            events.append("database")
            return response

        with (
            patch.object(app.community, "_get", return_value=self.settings),
            patch.object(app.community, "ranking_text", return_value="CLASSIFICA TROFEI"),
            patch.object(app.community, "coefficient_ranking_text", return_value={"text": "PROGRESSIONE"}),
            patch.object(app.community, "_send_ranking_message", new=AsyncMock(side_effect=deliver)) as sender,
            patch.object(app.requests, "patch", side_effect=complete) as database_patch,
        ):
            await app._send_auto_ranking_slot(
                self.context, datetime(2026, 9, 23, 18, 0, tzinfo=app.ROME)
            )

        self.assertEqual(sender.await_count, 2)
        self.assertEqual(events, ["telegram", "telegram", "database"])
        self.assertEqual(database_patch.call_args.kwargs["json"]["last_auto_ranking_slot"], "2026-09-23-1800")

    async def test_2359_slot_keeps_all_end_of_day_reports(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [{"chat_id": -100123}]
        with (
            patch.object(app.community, "_get", return_value=self.settings),
            patch.object(app.community, "ranking_text", return_value="TROFEI"),
            patch.object(app.community, "coefficient_ranking_text", return_value="PROGRESSIONE"),
            patch.object(app.community, "club_trophy_ranking_text", return_value="CLUB"),
            patch.object(app.community, "global_ranking_text", return_value="GLOBALE"),
            patch.object(app.community, "global_club_ranking_text", return_value="CLUB GLOBALE"),
            patch.object(app.community, "_send_ranking_message", new=AsyncMock(return_value=True)) as sender,
            patch.object(app.requests, "patch", return_value=response) as database_patch,
        ):
            await app._send_auto_ranking_slot(
                self.context, datetime(2026, 9, 23, 23, 59, tzinfo=app.ROME)
            )

        self.assertEqual(sender.await_count, 5)
        self.assertEqual(database_patch.call_args.kwargs["json"]["last_auto_ranking_slot"], "2026-09-23-2359")


class CompleteRosterRetryTests(unittest.TestCase):
    def test_transient_empty_save_is_retried_once(self):
        clubs = {"TITANI ABUSIVI": "UG9Q8PC"}
        roster = [{"player_tag": "ABC", "player_name": "Giocatore", "trophies": 100000}]
        with (
            patch.object(app.community, "CLUB_TAGS", clubs),
            patch.object(app, "fetch_supercell_proxy_club_roster", return_value=roster),
            patch.object(app, "save_complete_roster_daily", side_effect=[0, 1]) as save,
            patch("time.sleep") as sleep,
        ):
            saved = app.refresh_complete_club_rosters()

        self.assertEqual(saved, 1)
        self.assertEqual(save.call_count, 2)
        sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
