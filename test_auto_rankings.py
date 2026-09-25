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
            patch.object(app.community, "scheduled_dashboard_snapshot", return_value={"text": "DASHBOARD", "report_url": "https://telegra.ph/dashboard"}),
            patch.object(app.community, "_send_ranking_message", new=AsyncMock(return_value=False)),
            patch.object(app.requests, "patch") as database_patch,
        ):
            await app._send_auto_ranking_slot(
                self.context, datetime(2026, 9, 23, 18, 0, tzinfo=app.ROME)
            )

        database_patch.assert_not_called()
        self.assertEqual(self.checkpoint.call_count, 1)
        self.assertFalse(app._AUTO_RANKING_IN_FLIGHT)

    async def test_slot_completes_only_after_dashboard_is_delivered(self):
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
            patch.object(app.community, "scheduled_dashboard_snapshot", return_value={"text": "DASHBOARD", "report_url": "https://telegra.ph/dashboard"}),
            patch.object(app.community, "_send_ranking_message", new=AsyncMock(side_effect=deliver)) as sender,
            patch.object(app.requests, "patch", side_effect=complete) as database_patch,
        ):
            await app._send_auto_ranking_slot(
                self.context, datetime(2026, 9, 23, 18, 0, tzinfo=app.ROME)
            )

        self.assertEqual(sender.await_count, 1)
        self.assertEqual(events, ["telegram", "database"])
        self.assertEqual(database_patch.call_args.kwargs["json"]["last_auto_ranking_slot"], "2026-09-23-1800")
        self.assertIsNone(database_patch.call_args.kwargs["json"]["auto_ranking_pending"])
        self.assertEqual(self.checkpoint.call_count, 2)

    async def test_2359_slot_freezes_single_dashboard(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [{"chat_id": -100123}]
        with (
            patch.object(app.community, "_get", return_value=self.settings),
            patch.object(app.community, "scheduled_dashboard_snapshot", return_value={"text": "DASHBOARD", "report_url": "https://telegra.ph/dashboard"}) as snapshot,
            patch.object(app.community, "_send_ranking_message", new=AsyncMock(return_value=True)) as sender,
            patch.object(app.requests, "patch", return_value=response) as database_patch,
        ):
            await app._send_auto_ranking_slot(
                self.context, datetime(2026, 9, 23, 23, 59, tzinfo=app.ROME)
            )

        self.assertEqual(sender.await_count, 1)
        snapshot.assert_called_once_with(-100123, 0, (datetime(2026, 9, 23, 0, 0, tzinfo=app.ROME), datetime(2026, 9, 23, 23, 59, tzinfo=app.ROME)))
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
        sender = AsyncMock(side_effect=[False, True])
        with (
            patch.object(app.community, "_get", return_value=self.settings),
            patch.object(app.community, "scheduled_dashboard_snapshot", return_value={"text": "DASHBOARD", "report_url": "https://telegra.ph/dashboard"}) as snapshot,
            patch.object(app.community, "_send_ranking_message", new=sender),
            patch.object(app.requests, "patch", return_value=response) as database_patch,
        ):
            slot = datetime(2026, 9, 23, 23, 59, tzinfo=app.ROME)
            await app._send_auto_ranking_slot(self.context, slot)
            self.assertEqual(app._AUTO_RANKING_PENDING[(-100123, "2026-09-23-2359")]["next_index"], 0)
            await app._send_auto_ranking_slot(self.context, slot, frozen_only=True)

        self.assertEqual(sender.await_count, 2)
        snapshot.assert_called_once()
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


class AutomaticPeriodicReportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        app._AUTO_PERIODIC_IN_FLIGHT.clear()

    def test_calendar_windows(self):
        rome = app.ROME
        weekly = app._scheduled_period_window(datetime(2026, 9, 28, 6, tzinfo=rome), 7)
        self.assertEqual((weekly[0].day, weekly[1].day), (21, 28))
        first = app._scheduled_period_window(datetime(2026, 10, 16, 6, tzinfo=rome), 15)
        self.assertEqual((first[0].day, first[1].day), (1, 16))
        second = app._scheduled_period_window(datetime(2026, 11, 1, 6, tzinfo=rome), 15)
        self.assertEqual((second[0].month, second[0].day, second[1].month), (10, 16, 11))
        monthly = app._scheduled_period_window(datetime(2026, 3, 1, 6, tzinfo=rome), 30)
        self.assertEqual((monthly[0].month, monthly[0].day, monthly[1].day), (2, 1, 1))

    async def test_periodic_dashboard_is_frozen_before_delivery(self):
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        fake_datetime = Mock()
        fake_datetime.now.return_value = datetime(2026, 9, 28, 6, tzinfo=app.ROME)
        dashboard = {"text": "DASHBOARD", "report_url": "https://telegra.ph/dashboard"}
        with (
            patch.object(app, "datetime", fake_datetime),
            patch.object(app.community, "_get", side_effect=[[{"chat_id": -100123}], []]),
            patch.object(app.community, "scheduled_dashboard_snapshot", return_value=dashboard) as snapshot,
            patch.object(app.community, "_post") as checkpoint,
            patch.object(app.community, "_patch") as complete,
            patch.object(app.community, "_send_ranking_message", new=AsyncMock(return_value=True)) as ranking_send,
        ):
            await app.automatic_periodic_report_job(SimpleNamespace(job=SimpleNamespace(data={"days": 7}), bot=context.bot))
        snapshot.assert_called_once()
        self.assertEqual(snapshot.call_args.args[1], 7)
        self.assertEqual(checkpoint.call_args.args[1]["payload"], dashboard)
        self.assertEqual(ranking_send.await_count, 1)
        self.assertEqual(ranking_send.await_args.args[1:], (-100123, dashboard))
        complete.assert_called_once()
        self.assertFalse(app._AUTO_PERIODIC_IN_FLIGHT)

    async def test_periodic_retry_uses_saved_page_without_regeneration(self):
        fake_datetime = Mock()
        fake_datetime.now.return_value = datetime(2026, 9, 28, 6, 10, tzinfo=app.ROME)
        dashboard = {"text": "FROZEN", "report_url": "https://telegra.ph/frozen"}
        with (
            patch.object(app, "datetime", fake_datetime),
            patch.object(app.community, "_get", side_effect=[[{"chat_id": -100123}],
                                                           [{"payload": dashboard, "sent_at": None}]]),
            patch.object(app.community, "scheduled_dashboard_snapshot") as rebuild,
            patch.object(app.community, "_post") as checkpoint,
            patch.object(app.community, "_patch") as complete,
            patch.object(app.community, "_send_ranking_message", new=AsyncMock(return_value=True)) as send,
        ):
            await app.automatic_periodic_report_catchup_job(SimpleNamespace(bot=SimpleNamespace()))
        rebuild.assert_not_called()
        checkpoint.assert_not_called()
        send.assert_awaited_once()
        self.assertEqual(send.await_args.args[2], dashboard)
        complete.assert_called_once()


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
    def test_registered_tag_set_is_never_none(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {"player_tag": "#abc"},
            {"player_tag": "ABC"},
            {"player_tag": None},
        ]
        with patch.object(app.requests, "get", return_value=response):
            tags = app.get_registered_player_tags()

        self.assertEqual(tags, {"ABC"})
        self.assertIsInstance(tags, set)

    def test_registered_tag_failure_returns_empty_set(self):
        with patch.object(app.requests, "get", side_effect=RuntimeError("offline")):
            tags = app.get_registered_player_tags()

        self.assertEqual(tags, set())

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


class DeterministicCommandNormalizationTests(unittest.TestCase):
    def test_stats_variants_normalize_to_same_command(self):
        normalize = app.normalize_deterministic_command
        self.assertEqual(normalize("Stats", "SensGPT_TitaniAbusiviBot"), "Stats")
        self.assertEqual(normalize("@ stats", "SensGPT_TitaniAbusiviBot"), "stats")
        self.assertEqual(
            normalize("@SensGPT_TitaniAbusiviBot Stats", "SensGPT_TitaniAbusiviBot"),
            "Stats",
        )

    def test_other_user_mentions_are_preserved(self):
        command = app.normalize_deterministic_command(
            "registra utente @GiorgioBs111111 #2LVRCLV8LV",
            "SensGPT_TitaniAbusiviBot",
        )
        self.assertIn("@GiorgioBs111111", command)


if __name__ == "__main__":
    unittest.main()
