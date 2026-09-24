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
        app._AUTO_RANKING_PENDING.clear()
        self.checkpoint_patch = patch.object(app, "_persist_auto_ranking_pending")
        self.checkpoint = self.checkpoint_patch.start()
        self.addCleanup(self.checkpoint_patch.stop)
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
        self.assertEqual(self.checkpoint.call_count, 2)
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
        self.assertIsNone(database_patch.call_args.kwargs["json"]["auto_ranking_pending"])
        self.assertEqual(self.checkpoint.call_count, 3)

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
        self.assertFalse(app._AUTO_RANKING_PENDING)

    async def test_2359_restart_restores_frozen_reports_from_database(self):
        slot = datetime(2026, 9, 23, 23, 59, tzinfo=app.ROME)
        frozen = {"slot": "2026-09-23-2359", "payloads": [
            ["trofei", "CLASSIFICA TROFEI 23/09"],
            ["progressione", "CLASSIFICA PROGRESSIONE 23/09"],
        ], "next_index": 1}
        self.settings = [{"chat_id": -100123,
                          "last_auto_ranking_slot": "2026-09-23-1800",
                          "auto_ranking_pending": frozen}]
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [{"chat_id": -100123}]
        with (
            patch.object(app.community, "_get", return_value=self.settings),
            patch.object(app.community, "ranking_text") as rebuild,
            patch.object(app.community, "_send_ranking_message", new=AsyncMock(return_value=True)) as send,
            patch.object(app.requests, "patch", return_value=response) as finish,
        ):
            await app._send_auto_ranking_slot(self.context, slot, frozen_only=True)
        rebuild.assert_not_called()
        self.assertEqual(send.await_count, 1)
        self.assertEqual(send.await_args.args[-1], "CLASSIFICA PROGRESSIONE 23/09")
        self.assertEqual(finish.call_args.kwargs["json"]["last_auto_ranking_slot"], "2026-09-23-2359")

    async def test_watchdog_recovers_database_payload_after_restart(self):
        key = "2026-09-23-2359"
        settings = [{"chat_id": -100123, "last_auto_ranking_slot": "2026-09-23-1800",
                     "auto_ranking_pending": {"slot": key, "payloads": [["trofei", "frozen"]], "next_index": 0}}]
        fake_datetime = Mock()
        fake_datetime.now.return_value = datetime(2026, 9, 24, 0, 1, tzinfo=app.ROME)
        with (
            patch.object(app, "datetime", fake_datetime),
            patch.object(app.community, "_get", return_value=settings),
            patch.object(app, "_send_auto_ranking_slot", new=AsyncMock()) as retry,
        ):
            await app.automatic_today_ranking_catchup_job(self.context)
        retry.assert_awaited_once_with(
            self.context, datetime(2026, 9, 23, 23, 59, tzinfo=app.ROME), frozen_only=True
        )

    async def test_2359_failure_resumes_frozen_payload_after_midnight(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [{"chat_id": -100123}]
        sender = AsyncMock(side_effect=[True, False, True, True, True, True])
        with (
            patch.object(app.community, "_get", return_value=self.settings),
            patch.object(app.community, "ranking_text", return_value="TROFEI") as trophies,
            patch.object(app.community, "coefficient_ranking_text", return_value="PROGRESSIONE") as progression,
            patch.object(app.community, "club_trophy_ranking_text", return_value="CLUB") as club,
            patch.object(app.community, "global_ranking_text", return_value="GLOBALE") as global_ranking,
            patch.object(app.community, "global_club_ranking_text", return_value="CLUB GLOBALE") as global_club,
            patch.object(app.community, "_send_ranking_message", new=sender),
            patch.object(app.requests, "patch", return_value=response) as database_patch,
        ):
            slot = datetime(2026, 9, 23, 23, 59, tzinfo=app.ROME)
            await app._send_auto_ranking_slot(self.context, slot)
            self.assertEqual(app._AUTO_RANKING_PENDING[(-100123, "2026-09-23-2359")]["next_index"], 1)
            await app._send_auto_ranking_slot(self.context, slot, frozen_only=True)

        self.assertEqual(sender.await_count, 6)
        for builder in (trophies, progression, club, global_ranking, global_club):
            builder.assert_called_once()
        database_patch.assert_called_once()
        self.assertFalse(app._AUTO_RANKING_PENDING)

    async def test_watchdog_retries_frozen_2359_payload_after_midnight(self):
        slot = datetime(2026, 9, 23, 23, 59, tzinfo=app.ROME)
        key = "2026-09-23-2359"
        app._AUTO_RANKING_PENDING[(-100123, key)] = {
            "payloads": [("trofei", "TROFEI")],
            "next_index": 0,
        }
        fake_datetime = Mock()
        fake_datetime.now.return_value = datetime(2026, 9, 24, 0, 1, tzinfo=app.ROME)
        with (
            patch.object(app, "datetime", fake_datetime),
            patch.object(app, "_send_auto_ranking_slot", new=AsyncMock()) as retry,
        ):
            await app.automatic_today_ranking_catchup_job(self.context)

        retry.assert_awaited_once_with(self.context, slot, frozen_only=True)


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


class RegisteredBattleMonitorTests(unittest.TestCase):
    def test_registered_roster_is_normalized_and_deduplicated(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {"player_tag": "#abc", "player_name": "Uno"},
            {"player_tag": "ABC", "player_name": "Duplicato"},
            {"player_tag": " def ", "player_name": "Due"},
            {"player_tag": None, "player_name": "Senza tag"},
        ]
        with patch.object(app.requests, "get", return_value=response) as request:
            players = app.get_registered_players_for_battle_monitor()

        self.assertEqual(players, [
            {"tag": "ABC", "name": "Uno"},
            {"tag": "DEF", "name": "Due"},
        ])
        self.assertEqual(request.call_args.kwargs["params"]["is_active"], "eq.true")


if __name__ == "__main__":
    unittest.main()
