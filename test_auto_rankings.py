import os
import unittest
from datetime import datetime
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlsplit
from tornado.testing import AsyncHTTPTestCase

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-role-key")

with patch("google.genai.Client", return_value=Mock()):
    import app


class BattleLogWebhookTests(AsyncHTTPTestCase):
    def get_app(self):
        return app.SensWebhookApp("/telegram", None, __import__("asyncio").Queue())

    def test_live_webhook_server_handles_signed_player_link(self):
        start = datetime.now(app.timezone.utc) - app.timedelta(hours=1)
        end = datetime.now(app.timezone.utc) - app.timedelta(minutes=1)
        with patch.dict(os.environ, {"TELEGRAPH_ACCESS_TOKEN": "unit-test-token"}):
            link = app.community._battle_log_link("2GU9UV2RG", start, end)
            uri = urlsplit(link)
            with patch.object(app.community, "player_battle_log_page",
                              return_value="https://telegra.ph/Battaglie-Giorgio") as publish:
                response = self.fetch(uri.path + "?" + uri.query, follow_redirects=False)
                self.assertEqual(response.code, 302)
                self.assertEqual(response.headers["Location"], "https://telegra.ph/Battaglie-Giorgio")
                publish.assert_called_once()
                invalid = self.fetch(uri.path + "?" + uri.query.replace("sig=", "sig=x"))
                self.assertEqual(invalid.code, 403)
            unicode_url = "https://telegra.ph/Battaglie-𝔸𝕟𝕟𝕒--1-09-28"
            with patch.object(app.community, "player_battle_log_page", return_value=unicode_url):
                response = self.fetch(uri.path + "?" + uri.query, follow_redirects=False)
                self.assertEqual(response.code, 302)
                self.assertIn("telegra.ph/Battaglie-", response.headers["Location"])
            with patch.object(app.community, "player_battle_log_page",
                              return_value="https://other.example/Battaglie"):
                self.assertEqual(self.fetch(uri.path + "?" + uri.query).code, 503)


class PrivateCommandStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_classifica_oggi_offers_one_tap_resume_when_dm_is_closed(self):
        from telegram.error import Forbidden
        message = SimpleNamespace(chat_id=-100123, message_id=42,
                                  chat=SimpleNamespace(type="supergroup"),
                                  from_user=SimpleNamespace(id=456), reply_text=AsyncMock())
        bot = SimpleNamespace(username="SensGPT_TitaniAbusiviBot",
                              send_chat_action=AsyncMock(side_effect=Forbidden("bot can't initiate conversation")),
                              delete_message=AsyncMock())
        context = SimpleNamespace(bot=bot, user_data={})
        routed = await app._private_group_command(message, context, "classifica oggi")
        self.assertIsNone(routed)
        bot.delete_message.assert_awaited_once_with(chat_id=-100123, message_id=42)
        button = message.reply_text.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.url,
                         "https://t.me/SensGPT_TitaniAbusiviBot?start=cmd_classifica_oggi")
        await app._private_group_command(message, context, "classifica oggi")
        message.reply_text.assert_awaited_once()

    async def test_private_start_runs_classifica_oggi_without_retyping(self):
        message = SimpleNamespace(chat_id=456, chat=SimpleNamespace(type="private"),
                                  from_user=SimpleNamespace(id=456), reply_text=AsyncMock())
        context = SimpleNamespace(args=["cmd_classifica_oggi"], user_data={}, bot=SimpleNamespace())
        with (patch.object(app.community, "get_registered_user", return_value={"player_tag": "2GU9UV2RG"}),
              patch.object(app.community, "handle_command", new=AsyncMock(return_value=True)) as handler):
            await app.start_command(SimpleNamespace(effective_message=message), context)
        handler.assert_awaited_once_with(message, context, "classifica oggi")

    async def test_temporary_typing_failure_keeps_private_route(self):
        from telegram.error import NetworkError
        message = SimpleNamespace(chat_id=-100123, message_id=42,
                                  chat=SimpleNamespace(type="supergroup"),
                                  from_user=SimpleNamespace(id=456), reply_text=AsyncMock())
        bot = SimpleNamespace(send_chat_action=AsyncMock(side_effect=NetworkError("temporary")),
                              delete_message=AsyncMock())
        routed = await app._private_group_command(message, SimpleNamespace(bot=bot, user_data={}), "classifica oggi")
        self.assertEqual(routed.chat_id, -100123)
        message.reply_text.assert_not_awaited()

    def test_generic_resume_payload_keeps_base64_case(self):
        payload = app._private_start_payload("Progressione oggi")
        self.assertTrue(payload.startswith("run_"))
        encoded = payload[4:]
        self.assertEqual(__import__("base64").urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)),
                         b"progressione oggi")


class AutomaticRankingSlotTests(unittest.IsolatedAsyncioTestCase):
    def test_progressi_oggi_alias_is_a_private_deterministic_command(self):
        for text in ("Progressi oggi", "Progressi pggi", "Progressione oggi"):
            command = app.normalize_deterministic_command(text)
            self.assertEqual(command, "progressione oggi")
            self.assertTrue(app._is_manual_deterministic_command(command))
            self.assertFalse(app._is_public_group_command(command))

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
            patch.object(app.community, "_post") as archive,
            patch.object(app.requests, "patch", return_value=response) as database_patch,
        ):
            await app._send_auto_ranking_slot(
                self.context, datetime(2026, 9, 23, 23, 59, tzinfo=app.ROME)
            )

        self.assertEqual(sender.await_count, 1)
        archive.assert_called_once()
        self.assertEqual(archive.call_args.args[1]["slot"], "daily:2359:2026-09-23")
        snapshot.assert_called_once_with(-100123, 0, (datetime(2026, 9, 23, 0, 0, tzinfo=app.ROME), datetime(2026, 9, 23, 23, 59, tzinfo=app.ROME)))
        self.assertEqual(database_patch.call_args.kwargs["json"]["last_auto_ranking_slot"], "2026-09-23-2359")
        self.assertFalse(app._AUTO_RANKING_PENDING)

    async def test_same_day_slot_samples_rosters_before_live_cutoff(self):
        slot = datetime(2026, 9, 28, 6, 0, tzinfo=app.ROME)
        first = datetime(2026, 9, 28, 6, 1, tzinfo=app.ROME)
        cutoff = datetime(2026, 9, 28, 6, 2, tzinfo=app.ROME)
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [{"chat_id": -100123}]
        with (
            patch.object(app, "datetime") as clock,
            patch.object(app, "refresh_rosters_for_rankings") as refresh,
            patch.object(app.community, "_get", return_value=self.settings),
            patch.object(app.community, "scheduled_dashboard_snapshot", return_value={"text": "DASHBOARD", "report_url": "https://telegra.ph/dashboard"}) as snapshot,
            patch.object(app.community, "_send_ranking_message", new=AsyncMock(return_value=True)),
            patch.object(app.requests, "patch", return_value=response),
        ):
            clock.now.side_effect = lambda *_args: cutoff if refresh.called else first
            await app._send_auto_ranking_slot(self.context, slot)
        refresh.assert_called_once()
        snapshot.assert_called_once_with(-100123, 0, (slot.replace(hour=0), cutoff))

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
            patch.object(app.community, "_post"),
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
            patch.object(app.community, "_post"),
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
        second = app._scheduled_period_window(datetime(2026, 10, 31, 23, 59, 59, tzinfo=rome), 15)
        self.assertEqual((second[0].month, second[0].day, second[1].month, second[1].day), (10, 16, 10, 31))
        self.assertIsNone(app._scheduled_period_window(datetime(2026, 11, 1, 6, tzinfo=rome), 15))
        self.assertIsNone(app._scheduled_period_window(datetime(2026, 10, 30, 23, 59, 59, tzinfo=rome), 15))
        february = app._scheduled_period_window(datetime(2027, 2, 28, 23, 59, 59, tzinfo=rome), 15)
        self.assertEqual((february[0].day, february[1].day), (16, 28))
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

    async def test_last_day_half_month_retries_frozen_page_before_monthly_send(self):
        fake_datetime = Mock()
        fake_datetime.now.return_value = datetime(2026, 11, 1, 0, 5, tzinfo=app.ROME)
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
        complete.assert_called_once()


class CompleteRosterRetryTests(unittest.TestCase):
    def test_battle_log_link_is_signed_and_redirects_to_published_page(self):
        from urllib.parse import urlsplit
        from datetime import datetime, timedelta, timezone
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = datetime.now(timezone.utc) - timedelta(minutes=1)
        with patch.dict(os.environ, {"TELEGRAPH_ACCESS_TOKEN": "unit-test-token"}):
            link = app.community._battle_log_link("2GU9UV2RG", start, end)
            self.assertIsNotNone(link)
            parsed = urlsplit(link)
            client = app.app.test_client()
            with patch.object(app.community, "player_battle_log_page", return_value="https://telegra.ph/Battaglie-09-28") as publish:
                response = client.get(parsed.path + "?" + parsed.query)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], "https://telegra.ph/Battaglie-09-28")
                publish.assert_called_once()
                self.assertEqual(client.get(parsed.path + "?" + parsed.query.replace("sig=", "sig=x")).status_code, 403)
                publish.assert_called_once()

    def test_existing_player_rows_are_refreshed_concurrently_without_overwriting_first_sample(self):
        players = [{"player_tag": tag, "player_name": tag, "trophies": 100 + i}
                   for i, tag in enumerate(("AAA", "BBB", "CCC"))]
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [{"player_tag": row["player_tag"], "first_trophies": 50}
                                      for row in players]
        barrier = Barrier(3)
        def save(*_args, **_kwargs):
            barrier.wait(timeout=3)
            return response
        with (
            patch.object(app, "SUPABASE_URL", "https://example.supabase.co"),
            patch.object(app, "SUPABASE_SERVICE_ROLE_KEY", "test-key"),
            patch.object(app.requests, "get", return_value=response),
            patch.object(app.requests, "patch", side_effect=save) as updates,
            patch.object(app.requests, "post") as inserts,
        ):
            self.assertEqual(app.save_complete_roster_daily("TITANI ABUSIVI", "UG9Q8PC", players), 3)
        self.assertEqual(updates.call_count, 3)
        inserts.assert_not_called()
        self.assertTrue(all("first_trophies" not in call.kwargs["json"]
                            for call in updates.call_args_list))

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
        self.assertTrue(any(call.kwargs["params"].get("is_active") == "eq.true"
                            for call in request.call_args_list))

    def test_battle_monitor_keeps_other_clubs_when_one_roster_read_fails(self):
        def get(_url, headers, params, timeout):
            response = Mock()
            response.raise_for_status.return_value = None
            if params.get("is_active") == "eq.true":
                response.json.return_value = [{"player_tag": "#AAA", "player_name": "Registered"}]
            elif params.get("club_name") == "eq.TALENTI ABUSIVI":
                raise ConnectionError("temporary club roster error")
            elif params.get("select") == "snapshot_date":
                response.json.return_value = [{"snapshot_date": "2026-09-25"}]
            elif params.get("club_name") == "eq.TITANI ABUSIVI":
                response.json.return_value = [{"player_tag": "#BBB", "player_name": "Unregistered"}]
            else:
                response.json.return_value = []
            return response
        with patch.object(app.requests, "get", side_effect=get):
            players = app.get_registered_players_for_battle_monitor()
        self.assertEqual({player["tag"] for player in players}, {"AAA", "BBB"})

    def test_trophy_monitor_keeps_club_roster_if_other_source_fails(self):
        def get(url, headers, params, timeout):
            if url.endswith("/community_members"):
                raise ConnectionError("temporary member read error")
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = ([{"player_tag": "BBB"}]
                                          if url.endswith("/club_roster_daily") else [])
            return response
        with patch.object(app.requests, "get", side_effect=get):
            self.assertEqual(app.get_tracked_player_tags(), ["BBB"])


class DeterministicCommandNormalizationTests(unittest.TestCase):
    def test_group_chat_recognizes_only_full_bot_mention(self):
        username = "SensGPT_TitaniAbusiviBot"
        self.assertTrue(app.is_explicit_bot_mention(
            "@SensGPT_TitaniAbusiviBot cosa ne pensi del gruppo community inattivo", username
        ))
        self.assertTrue(app.is_explicit_bot_mention("Ciao @sensgpt_titaniabusivibot!", username))
        self.assertFalse(app.is_explicit_bot_mention("@SensGPT_TitaniAbusiviBotFake ciao", username))
        self.assertFalse(app.is_explicit_bot_mention("Ciao gruppo", username))

    def test_stats_variants_normalize_to_same_command(self):
        normalize = app.normalize_deterministic_command
        self.assertEqual(normalize("Stats", "SensGPT_TitaniAbusiviBot"), "Stats")
        self.assertEqual(normalize("@ stats", "SensGPT_TitaniAbusiviBot"), "stats")
        self.assertEqual(normalize("@SensGPT_TitaniAbusiviBot Stats", "SensGPT_TitaniAbusiviBot"), "Stats")

    def test_other_user_mentions_are_preserved(self):
        command = app.normalize_deterministic_command(
            "registra utente @GiorgioBs111111 #2LVRCLV8LV", "SensGPT_TitaniAbusiviBot",
        )
        self.assertIn("@GiorgioBs111111", command)

    def test_mentioned_yesterday_question_remains_deterministic_when_username_unavailable(self):
        command = app.normalize_deterministic_command(
            "@SensGPT_TitaniAbusiviBot quanti trofei ho fatto ieri?", None)
        self.assertEqual(command, "quanti trofei ho fatto ieri?")
        self.assertTrue(app._is_manual_deterministic_command(command))
        self.assertTrue(app._is_manual_deterministic_command("Quanti trofei avevo ieri"))


class GroupVoiceExceptionsTests(unittest.IsolatedAsyncioTestCase):
    def message(self, text, selected):
        return SimpleNamespace(
            text=text, chat_id=-100123, message_id=42,
            chat=SimpleNamespace(type="supergroup"),
            from_user=SimpleNamespace(id=456), reply_to_message=selected,
            reply_text=AsyncMock(),
        )

    def context(self):
        return SimpleNamespace(
            bot=SimpleNamespace(username="SensGPT_TitaniAbusiviBot", id=123,
                                send_message=AsyncMock(), send_voice=AsyncMock()),
            user_data={},
        )

    async def test_leggi_selected_group_message_stays_in_group(self):
        selected = SimpleNamespace(text="Messaggio da leggere", caption=None,
                                   from_user=SimpleNamespace(id=789))
        message = self.message("@SensGPT_TitaniAbusiviBot leggi", selected)
        context = self.context()
        with (patch.object(app, "_private_group_command", new=AsyncMock()) as private,
              patch.object(app, "send_voice_reply", new=AsyncMock(return_value=True)) as voice):
            await app.answer(SimpleNamespace(effective_message=message), context)
        private.assert_not_awaited()
        voice.assert_awaited_once_with(context, -100123, "Messaggio da leggere")

    async def test_rispondi_a_voce_selected_group_message_is_not_private(self):
        selected = SimpleNamespace(text="Cosa ne pensi?", caption=None,
                                   from_user=SimpleNamespace(id=789))
        message = self.message("Rispondi a voce", selected)
        context = self.context()
        with (patch.object(app, "_private_group_command", new=AsyncMock()) as private,
              patch.object(app.community, "handle_command", new=AsyncMock(return_value=True)) as route):
            await app.answer(SimpleNamespace(effective_message=message), context)
        private.assert_not_awaited()
        route.assert_awaited_once_with(message, context, "Rispondi a voce")

    async def test_explicit_voice_answer_uses_group_destination(self):
        message = self.message("Rispondi a voce", None)
        context = self.context()
        context.user_data["_request_voice_mode"] = "voice"
        with patch.object(app, "send_voice_reply", new=AsyncMock(return_value=True)) as voice:
            await app.send_mode_aware_text(message, context, "Risposta")
        voice.assert_awaited_once_with(context, -100123, "Risposta")
        context.bot.send_message.assert_not_awaited()

if __name__ == "__main__":
    unittest.main()



class DailyRankingRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        app._AUTO_RANKING_IN_FLIGHT.clear()
        app._AUTO_RANKING_PENDING.clear()

    async def test_midnight_recovers_missing_payload_with_original_window(self):
        slot = datetime(2026, 10, 9, 23, 59, tzinfo=app.ROME)
        now = Mock()
        now.now.return_value = datetime(2026, 10, 10, 0, 40, tzinfo=app.ROME)
        rows = [{"chat_id": -100123, "last_auto_ranking_slot": "2026-10-09-1800"}]
        response = Mock()
        response.json.return_value = rows
        payload = {"text": "RIEPILOGO 09/10", "report_url": None}
        with (patch.object(app, "datetime", now),
              patch.object(app.community, "_get", return_value=rows),
              patch.object(app.community, "scheduled_dashboard_snapshot", return_value=payload) as build,
              patch.object(app.community, "_send_ranking_message", new=AsyncMock(return_value=True)) as send,
              patch.object(app.community, "_post") as archive,
              patch.object(app, "_persist_auto_ranking_pending") as checkpoint,
              patch.object(app.requests, "patch", return_value=response),
              patch.object(app, "refresh_rosters_for_rankings") as refresh):
            await app.automatic_today_ranking_catchup_job(SimpleNamespace(bot=SimpleNamespace()))
        build.assert_called_once_with(-100123, 0, (slot.replace(hour=0, minute=0), slot))
        refresh.assert_not_called()
        send.assert_awaited_once()
        self.assertEqual(checkpoint.call_args_list[0].args[2]["slot"], "2026-10-09-2359")
        self.assertEqual(archive.call_args.args[1]["slot"], "daily:2359:2026-10-09")
        self.assertEqual(archive.call_args.args[1]["payload"], payload)

    async def test_midnight_does_not_repeat_completed_slot(self):
        now = Mock()
        now.now.return_value = datetime(2026, 10, 10, 0, 40, tzinfo=app.ROME)
        rows = [{"chat_id": -100123, "last_auto_ranking_slot": "2026-10-09-2359"}]
        with (patch.object(app, "datetime", now),
              patch.object(app.community, "_get", return_value=rows),
              patch.object(app, "_send_auto_ranking_slot", new=AsyncMock()) as send):
            await app.automatic_today_ranking_catchup_job(SimpleNamespace())
        send.assert_not_awaited()

    def test_telegraph_failure_preserves_period_stats_and_available_pages(self):
        from community_features import CommunityFeatures
        start = datetime(2026, 10, 9, tzinfo=app.ROME)
        end = start.replace(hour=23, minute=59)
        for failed_stage in ("club_details", "progression", "rankings", "index"):
            with self.subTest(stage=failed_stage):
                obj = CommunityFeatures.__new__(CommunityFeatures)
                obj.number_formatter = str
                obj._player_link_name = str
                obj._build_rankings_dashboard_text = Mock(return_value=["CLASSIFICHE", "live"])
                full = ["REPORT", "", "", "PERIODO", "🏆 CLASSIFICA TROFEI",
                        "1. Tony +100", "🔥 CLASSIFICA PROGRESSIONE", "📊 RESOCONTO", "Battaglie: 20"]
                obj.periodic_report_text = Mock(return_value=("Trofei: +100\nBattaglie: 20", full,
                    {"TITANI": {"delta": 100, "players": 1, "roster": 1}}))
                obj._club_battle_detail_links = Mock(return_value={})
                obj._club_average_progression_line = Mock(return_value="Coefficiente: 1,2")
                obj.ranked_window_ranking_text = Mock(return_value=["RANKED"])
                obj._publish_inline_ranking = Mock(return_value="https://telegra.ph/ranked")
                obj.coefficient_ranking_text = Mock(return_value={"report_url":
                    None if failed_stage == "progression" else "https://telegra.ph/progression"})
                def publish(title, lines):
                    if ((failed_stage == "club_details" and title.startswith("Dettagli 4 Club"))
                            or (failed_stage == "rankings" and title.startswith("Classifica 4 Club"))
                            or (failed_stage == "index" and title == "Classifiche — OGGI")):
                        return None
                    return "https://telegra.ph/published"
                obj._publish_telegraph = Mock(side_effect=publish)
                result = obj.scheduled_dashboard_snapshot(-100123, 0, (start, end))
                self.assertIsNone(result["report_url"])
                self.assertIn("09/10/2026 00:00 – 09/10/2026 23:59", result["text"])
                self.assertIn("Trofei: +100", result["text"])
                self.assertIn("Battaglie: 20", result["text"])
                self.assertIn("1. TITANI — +100", result["text"])
                self.assertNotIn("[[", result["text"])
                self.assertNotIn("calcolato adesso", result["text"])
                self.assertLessEqual(len(result["text"]), 4096)
                obj.periodic_report_text.assert_called_once()
                self.assertEqual(obj.periodic_report_text.call_args.kwargs["window"], (start, end))
