import os
import requests
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from community_features import CommunityFeatures
from coefficient_guide import coefficient_guide_lines


class CoefficientSnapshotDistributionTests(unittest.TestCase):
    @patch("app.requests.post")
    @patch("app.requests.get")
    @patch.dict(os.environ, {
        "TELEGRAM_TOKEN": "000000:test-token",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "test-secret",
    })
    def test_snapshot_stores_only_numeric_brawler_trophy_distribution(self, get, post):
        import app

        get.return_value.raise_for_status.return_value = None
        get.return_value.json.return_value = []
        post.return_value.raise_for_status.return_value = None
        with patch.object(app, "SUPABASE_URL", "https://example.supabase.co"), patch.object(
            app, "SUPABASE_SERVICE_ROLE_KEY", "test-secret"
        ):
            saved = app.save_coefficient_snapshot({
                "tag": "#2GU9UV2RG",
                "name": "Sens",
                "trophies": 1600,
                "brawler_trophies": [
                    {"name": "NITA", "trophies": 500},
                    {"name": "GRIFF", "trophies": "1100"},
                    {"name": "IGNORA", "trophies": None},
                    "invalid-row",
                ],
            })

        self.assertTrue(saved)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["brawler_trophy_values"], [500, 1100, 0])
        self.assertNotIn("brawler_trophies", payload)
        self.assertNotIn("test-secret", str(payload))

    @patch("app.requests.post")
    @patch("app.requests.get")
    @patch.dict(os.environ, {
        "TELEGRAM_TOKEN": "000000:test-token",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "test-secret",
    })
    def test_legacy_snapshot_without_distribution_is_not_deduplicated(self, get, post):
        import app

        get.return_value.raise_for_status.return_value = None
        get.return_value.json.return_value = [{
            "coefficient_score": 500,
            "trophies": 500,
            "formula_version": app.COEFFICIENT_FORMULA_VERSION,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "brawler_trophy_values": None,
        }]
        post.return_value.raise_for_status.return_value = None
        with patch.object(app, "SUPABASE_URL", "https://example.supabase.co"), patch.object(
            app, "SUPABASE_SERVICE_ROLE_KEY", "test-secret"
        ):
            self.assertTrue(app.save_coefficient_snapshot({
                "tag": "#2GU9UV2RG", "name": "Sens", "trophies": 500,
                "brawler_trophies": [{"name": "NITA", "trophies": 500}],
            }))

        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["json"]["brawler_trophy_values"], [500])


class TelegraphReportTests(unittest.IsolatedAsyncioTestCase):
    def make_features(self):
        obj = CommunityFeatures.__new__(CommunityFeatures)
        obj.number_formatter = lambda value: f"{int(value):,}".replace(",", ".")
        return obj

    @patch("community_features.time.sleep")
    @patch("community_features.requests.get")
    def test_transient_supabase_read_retries_before_returning_rows(self, get, sleep):
        obj = self.make_features()
        obj.supabase_url, obj.supabase_key = "https://example.supabase.co", "test-key"
        response = Mock()
        response.json.return_value = [{"player_tag": "AAA"}]
        get.side_effect = [__import__("requests").ConnectionError("connection reset"), response]
        self.assertEqual(obj._get("club_roster_daily"), [{"player_tag": "AAA"}])
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once()

    def test_period_index_rejects_missing_progression_page(self):
        obj = self.make_features()
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/trophies")
        obj.periodic_report_text = Mock(return_value=("REPORT", [
            "REPORT", "Data", "", "👥 Ambito: quattro club", "🏆 CLASSIFICA TROFEI",
            "1. Utente +10", "🔥 CLASSIFICA PROGRESSIONE", "📊 RESOCONTO",
        ], {"TITANI ABUSIVI": {"delta": 10, "players": 2}}))
        obj.coefficient_ranking_text = Mock(return_value="Progressione non disponibile")
        with self.assertRaisesRegex(RuntimeError, "progression page is unavailable"):
            obj._direct_dashboard_snapshot(-1001, 15, None)

    @patch("community_features.requests.post")
    def test_daily_report_lists_only_positive_players_but_preserves_totals(self, post):
        obj = self.make_features()
        obj.supabase_url = "https://example.supabase.co"
        obj.supabase_key = "test-secret"
        obj._get = Mock(return_value=[
            {"player_tag": "AAA", "player_name": "Active"},
            {"player_tag": "BBB", "player_name": "Zero"},
            {"player_tag": "CCC", "player_name": "Loss"},
        ])
        obj.history_fetcher = Mock(side_effect=lambda tag, days: [{"trophies": {"AAA": 110, "BBB": 100, "CCC": 90}[tag]}])
        obj.change_calculator = Mock(side_effect=lambda history, current: {
            key: current - 100 for key in ("today", "7d", "15d", "30d")
        })
        post.return_value.json.return_value = [
            {"player_tag": "AAA", "progression_value": 12, "positive_trophies": 10, "battle_count": 1},
            {"player_tag": "BBB", "progression_value": 0, "positive_trophies": 0, "battle_count": 1},
            {"player_tag": "CCC", "progression_value": -2, "positive_trophies": 0, "battle_count": 1},
        ]
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/test")
        for days in (0, 7, 15, 30):
            with self.subTest(days=days):
                summary, full, _clubs = obj.periodic_report_text(123, "community", days, return_full=True)
                trophy = full[full.index("🏆 CLASSIFICA TROFEI") + 1:full.index("🔥 CLASSIFICA PROGRESSIONE")]
                progression = full[full.index("🔥 CLASSIFICA PROGRESSIONE") + 1:full.index("📊 RESOCONTO")]
                self.assertTrue(any("Active" in line for line in trophy))
                self.assertTrue(any("Active" in line for line in progression))
                self.assertFalse(any("Zero" in line or "Loss" in line for line in trophy + progression + summary.splitlines()))
                self.assertIn("👥 Giocatori monitorati: 3", full)
                self.assertIn("🎮 Battaglie analizzate: 3", full)

    def test_dashboard_has_compact_global_links_for_every_period(self):
        from community_features import _DASHBOARD_CACHE
        _DASHBOARD_CACHE.clear()
        obj = self.make_features()
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/classifiche")
        obj.club_trophy_ranking_text = Mock(side_effect=AssertionError("registered-only ranking must not be used"))
        obj.coefficient_ranking_text = Mock(return_value={"report_url": "https://telegra.ph/progressione"})
        report_full = ["REPORT", "Data", "", "👥 Ambito: quattro club", "🏆 CLASSIFICA TROFEI", "1. Utente +10", "", "🔥 CLASSIFICA PROGRESSIONE", "📊 RESOCONTO", "🏆 Coppe totali reali: 100"]
        obj.periodic_report_text = Mock(return_value=("📊 REPORT COMPLETO: https://telegra.ph/report", report_full, {"TITANI ABUSIVI": {"delta": 10, "players": 2}}))
        payload = obj.rankings_dashboard_text(-1001)
        self.assertEqual(payload["report_url"], "https://telegra.ph/classifiche")
        self.assertNotIn("RESOCONTO", payload["text"])
        self.assertNotIn("Coppe totali reali", payload["text"])
        self.assertIn("OGGI · 7 · 15 · 30", payload["text"])
        self.assertIn("📋 RESOCONTO", obj.rankings_dashboard_text(-1001, 0)["text"])
        self.assertIs(payload, obj.rankings_dashboard_text(-1001))
        self.assertEqual(obj._publish_telegraph.call_count, 13)
        lines = obj._publish_telegraph.call_args.args[1]
        links = [line for line in lines if line.startswith("[[URL:")]
        self.assertEqual(len(links), 12)
        self.assertIn("[[URL:https://telegra.ph/progressione|Apri]]", links)
        self.assertNotIn("[[URL:https://telegra.ph/report|Apri]]", links)
        self.assertFalse(any("[[DASH:" in line for line in lines))
        self.assertEqual(obj.dashboard_command("dash_p_2_0"), "classifica progressione globale club oggi")
        self.assertEqual(obj.dashboard_command("dash_r_10_30"), "report club globale talenti 30")
        self.assertEqual(obj.dashboard_command("dash_t_1_15"), "classifica club 15")
        self.assertEqual(obj.dashboard_command("dash_r_1_0"), "report club oggi")
        self.assertIsNone(obj.dashboard_command("dash_p_11_7"))
        nodes = obj._telegraph_nodes(["CLASSIFICHE & REPORT", "[[DASH:dash_r_10_30|Apri]]"])
        self.assertEqual(nodes[-1]["children"][0]["attrs"]["href"],
                         "https://t.me/SensGPT_TitaniAbusiviBot?start=dash_r_10_30")
        direct_nodes = obj._telegraph_nodes(lines)
        self.assertEqual(sum(node.get("tag") == "h3" and node.get("children") == ["OGGI"] for node in direct_nodes), 1)
        self.assertEqual(sum(node.get("tag") == "h4" for node in direct_nodes), 8)
        self.assertEqual(sum(node.get("tag") == "h3" and "Classifica Trofei" in str(node.get("children"))
                             for node in direct_nodes), 4)
        self.assertEqual(sum(node.get("tag") == "a" and node.get("attrs", {}).get("href", "").startswith("https://telegra.ph/")
                             for item in direct_nodes for node in item.get("children", []) if isinstance(node, dict)), 12)
        self.assertEqual(obj.periodic_report_text.call_count, 4)
        self.assertTrue(all(call.args[1] == "global_clubs" and call.kwargs.get("return_full") and call.kwargs.get("publish") is False for call in obj.periodic_report_text.call_args_list))
        self.assertTrue(all(call.args[1] == "global_clubs" for call in obj.coefficient_ranking_text.call_args_list))

    @patch.dict(os.environ, {"TELEGRAPH_ACCESS_TOKEN": "fake-token"})
    @patch("community_features.requests.get")
    @patch("community_features.requests.post")
    def test_classifiche_reuses_complete_published_dashboard_without_rebuilding(self, post, get):
        from community_features import _DASHBOARD_CACHE, ROME
        _DASHBOARD_CACHE.clear()
        obj = self.make_features()
        obj._direct_dashboard_snapshot = Mock(side_effect=AssertionError("must reuse the published pages"))
        obj.periodic_report_text = Mock(side_effect=AssertionError("must not recalculate reports"))
        url = "https://telegra.ph/Classifiche--TITANI-ABUSIVI-09-25"
        post.return_value.json.return_value = {"ok": True, "result": {"pages": [{"title": "Classifiche — TITANI ABUSIVI", "url": url}]}}
        from datetime import datetime as _datetime
        updated = _datetime.now(ROME).strftime("Aggiornato: %d/%m/%Y %H:%M")
        nodes = [{"tag": "p", "children": [updated]},
                 {"tag": "p", "children": ["Liste: valori positivi verificati · copertura club e coefficiente medio nel Resoconto."]}]
        nodes += [{"tag": "h3", "children": [period]} for period in ("OGGI", "7 GIORNI", "15 GIORNI", "30 GIORNI")]
        nodes += [{"tag": "a", "attrs": {"href": f"https://telegra.ph/Classifica-{i}-09-25"}, "children": ["Apri"]} for i in range(12)]
        get.return_value.json.return_value = {"ok": True, "result": {"content": nodes}}
        payload = obj.rankings_dashboard_text(-123)
        self.assertEqual(payload["report_url"], url)
        self.assertEqual(payload, obj.rankings_dashboard_text(-123))
        post.assert_called_once()
        get.assert_called_once()

    @patch.dict(os.environ, {"TELEGRAPH_ACCESS_TOKEN": "fake-token"})
    @patch("community_features.requests.get")
    @patch("community_features.requests.post")
    def test_reused_global_dashboard_keeps_original_expiration(self, post, get):
        from community_features import _DASHBOARD_CACHE, ROME
        from datetime import timedelta
        _DASHBOARD_CACHE.clear()
        obj = self.make_features()
        obj._direct_dashboard_snapshot = Mock(side_effect=AssertionError("must reuse published dashboard"))
        post.return_value.json.return_value = {"ok": True, "result": {"pages": [
            {"title": "Classifiche — TITANI ABUSIVI", "url": "https://telegra.ph/dashboard"}
        ]}}
        updated = (datetime.now(ROME) - timedelta(minutes=4)).strftime("Aggiornato: %d/%m/%Y %H:%M")
        nodes = [{"tag": "p", "children": [updated]},
                 {"tag": "p", "children": ["Liste: valori positivi verificati · copertura club e coefficiente medio nel Resoconto."]}]
        nodes += [{"tag": "h3", "children": [period]} for period in ("OGGI", "7 GIORNI", "15 GIORNI", "30 GIORNI")]
        nodes += [{"tag": "a", "attrs": {"href": f"https://telegra.ph/ranking-{i}"}, "children": ["Apri"]} for i in range(12)]
        get.return_value.json.return_value = {"ok": True, "result": {"content": nodes}}
        with patch("community_features.time.monotonic", return_value=1000):
            obj.rankings_dashboard_text(-123)
        remaining = _DASHBOARD_CACHE[(-123, "shared")][0] - 1000
        self.assertGreater(remaining, 0)
        self.assertLess(remaining, 60)

    def test_period_dashboard_reuses_persisted_summary_and_exact_scope(self):
        from community_features import _DASHBOARD_CACHE
        _DASHBOARD_CACHE.clear()
        obj = self.make_features()
        obj.supabase_url, obj.supabase_key = "https://example.supabase.co", "test-key"
        payload = {"text": "📋 RESOCONTO\n🏆 Coppe totali reali: 100", "report_url": "https://telegra.ph/periodo-7",
                   "fallback": "CLASSIFICHE — 7 GIORNI", "cached_at": datetime.now(timezone.utc).isoformat(), "cache_revision": 8}
        obj._get = Mock(return_value=[{"payload": payload}])
        obj._direct_dashboard_snapshot = Mock(side_effect=AssertionError("must reuse the exact period"))
        result = obj.rankings_dashboard_text(-123, 7)
        self.assertEqual(result["text"], payload["text"])
        self.assertEqual(result["report_url"], payload["report_url"])
        self.assertEqual(obj._get.call_args.args[0], "scheduled_dashboard_delivery")
        self.assertEqual(obj._get.call_args.args[1]["slot"], "eq.manual:rolling:7")
        self.assertEqual(obj._get.call_args.args[1]["chat_id"], "eq.-123")

    def test_reused_period_cache_expires_at_original_deadline(self):
        from community_features import _DASHBOARD_CACHE
        from datetime import timedelta
        _DASHBOARD_CACHE.clear()
        obj = self.make_features()
        obj.supabase_url, obj.supabase_key = "https://example.supabase.co", "test-key"
        obj._get = Mock(return_value=[{"payload": {
            "report_url": "https://telegra.ph/periodo",
            "cached_at": (datetime.now(timezone.utc) - timedelta(seconds=119)).isoformat(),
            "cache_revision": 8,
        }}])
        obj._direct_dashboard_snapshot = Mock(side_effect=AssertionError("must reuse saved period"))
        with patch("community_features.time.monotonic", return_value=1000):
            obj.rankings_dashboard_text(-123, 0)
        remaining = _DASHBOARD_CACHE[(-123, "period:0")][0] - 1000
        self.assertGreater(remaining, 0)
        self.assertLess(remaining, 2)

    def test_stale_period_cache_is_rebuilt_and_revision_checked(self):
        from community_features import _DASHBOARD_CACHE
        _DASHBOARD_CACHE.clear()
        obj = self.make_features()
        obj.supabase_url, obj.supabase_key = "https://example.supabase.co", "test-key"
        stale = {"report_url": "https://telegra.ph/old", "cached_at": datetime.now(timezone.utc).isoformat()}
        obj._get = Mock(return_value=[{"payload": stale}])
        obj._post = Mock()
        fresh = {"text": "fresh", "report_url": "https://telegra.ph/fresh", "fallback": "fresh"}
        obj._direct_dashboard_snapshot = Mock(return_value=fresh)
        self.assertEqual(obj.rankings_dashboard_text(-123, 0), fresh)
        obj._direct_dashboard_snapshot.assert_called_once()
        self.assertEqual(obj._post.call_args.args[1]["payload"]["cache_revision"], 8)

    @patch("community_features.requests.post")
    def test_global_roster_uses_observed_nonregistered_snapshots_at_period_boundary(self, post):
        obj = self.make_features()
        obj.supabase_url, obj.supabase_key = "https://example.supabase.co", "test-key"
        club = "TITANI ABUSIVI"
        roster = [
            {"player_tag": "AAA", "player_name": "Unregistered", "club_name": club},
            {"player_tag": "BBB", "player_name": "Newcomer", "club_name": club},
        ]
        snapshots = [
            {"player_tag": "AAA", "first_seen_at": "2026-09-17T00:00:00Z", "last_seen_at": "2026-09-25T12:00:00Z",
             "first_trophies": 100, "last_trophies": 120},
            {"player_tag": "BBB", "first_seen_at": "2026-09-20T00:00:00Z", "last_seen_at": "2026-09-25T12:00:00Z",
             "first_trophies": 100, "last_trophies": 140},
        ]
        def get_rows(table, params):
            if table != "club_roster_daily":
                return []
            if params.get("select") == "snapshot_date":
                return [{"snapshot_date": "2026-09-25"}] if params.get("club_name") == f"eq.{club}" else []
            if params.get("select", "").startswith("player_tag,first_trophies"):
                return snapshots
            return roster if params.get("club_name") == f"eq.{club}" else []
        obj._get = Mock(side_effect=get_rows)
        obj.history_fetcher = Mock(return_value=[])
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/global")
        post.return_value.json.return_value = []
        start = datetime(2026, 9, 18, tzinfo=timezone.utc)
        end = datetime(2026, 9, 25, 13, tzinfo=timezone.utc)
        _summary, full, totals = obj.periodic_report_text(123, "global_clubs", 7,
                                                           window=(start, end), return_full=True)
        trophy = full[full.index("🏆 CLASSIFICA TROFEI") + 1:full.index("🔥 CLASSIFICA PROGRESSIONE")]
        self.assertTrue(any("Unregistered" in line and "+20" in line for line in trophy))
        self.assertFalse(any("Newcomer" in line for line in trophy))
        self.assertTrue(any("1 su 2 giocatori" in line for line in trophy))
        self.assertIn("🏆 Coppe totali reali: 260", full)
        self.assertEqual(totals[club]["delta"], 20)
        self.assertEqual((totals[club]["players"], totals[club]["roster"]), (1, 2))
        obj.history_fetcher.assert_not_called()

    @patch("community_features.requests.post")
    def test_progression_ranking_excludes_zero_for_every_period(self, post):
        obj = self.make_features()
        obj.supabase_url, obj.supabase_key = "https://example.supabase.co", "test-key"
        obj._get = Mock(return_value=[{"player_tag": "AAA", "player_name": "Active"},
                                          {"player_tag": "BBB", "player_name": "Zero"}])
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/progression")
        post.return_value.json.return_value = [
            {"player_tag": "AAA", "progression_value": 10, "positive_trophies": 9, "battle_count": 1, "coefficient": 1},
            {"player_tag": "BBB", "progression_value": 0, "positive_trophies": 0, "battle_count": 1, "coefficient": 1},
        ]
        for days in (0, 7, 15, 30):
            with self.subTest(days=days):
                obj.coefficient_ranking_text(123, "community", days)
                published = "\n".join(obj._publish_telegraph.call_args.args[1])
                self.assertIn("Active", published)
                self.assertNotIn("Zero", published)

    def test_period_dashboard_saves_telegram_summary_with_its_page(self):
        from community_features import _DASHBOARD_CACHE
        _DASHBOARD_CACHE.clear()
        obj = self.make_features()
        obj.supabase_url, obj.supabase_key = "https://example.supabase.co", "test-key"
        obj._get = Mock(return_value=[])
        obj._post = Mock(return_value=[])
        obj._direct_dashboard_snapshot = Mock(return_value={"text": "📋 RESOCONTO", "report_url": "https://telegra.ph/oggi", "fallback": "CLASSIFICHE — OGGI"})
        obj.rankings_dashboard_text(-123, 0)
        saved = obj._post.call_args.args[1]
        self.assertEqual((saved["chat_id"], saved["slot"]), (-123, "manual:rolling:0"))
        self.assertEqual(saved["payload"]["text"], "📋 RESOCONTO")
        self.assertIn("cached_at", saved["payload"])

    async def test_period_hubs_and_today_shortcuts_stay_private(self):
        from community_features import _DASHBOARD_CACHE
        _DASHBOARD_CACHE.clear()
        obj = self.make_features()
        obj.rankings_dashboard_text = Mock(return_value={"text": "7 GIORNI", "report_url": "https://telegra.ph/7"})
        obj.ranking_text = Mock(return_value="CLASSIFICA OGGI")
        obj.progression_detail_text = Mock(return_value="PROGRESSIONE OGGI")
        obj._send_ranking_message = AsyncMock(return_value=True)
        message = SimpleNamespace(chat_id=-1001, chat=SimpleNamespace(type="group"),
                                  from_user=SimpleNamespace(id=456), reply_text=AsyncMock())
        context = SimpleNamespace(user_data={"_registered_user": {"player_tag": "2GU9UV2RG"}})
        for days in (7, 15, 30):
            self.assertTrue(await obj.handle_command(message, context, f"Classifica {days}"))
            self.assertEqual(obj.rankings_dashboard_text.call_args.args[1], days)
            self.assertEqual(obj._send_ranking_message.await_args.args[1], 456)
            self.assertTrue(await obj.handle_command(message, context, f"Classifiche {days}"))
            self.assertEqual(obj.rankings_dashboard_text.call_args.args[1], days)
        for command in ("Classifica", "Classifiche"):
            self.assertTrue(await obj.handle_command(message, context, command))
            self.assertEqual(obj.rankings_dashboard_text.call_args.args, (-1001,))
            self.assertEqual(obj._send_ranking_message.await_args.args[1], 456)
        self.assertTrue(await obj.handle_command(message, context, "Classifica oggi"))
        self.assertEqual(obj.rankings_dashboard_text.call_args.args, (-1001, 0))
        obj.ranking_text.assert_not_called()
        self.assertEqual(obj.rankings_dashboard_text.call_count, 9)
        self.assertTrue(await obj.handle_command(message, context, "Progressione oggi"))
        obj.progression_detail_text.assert_called_once_with("2GU9UV2RG", 0)
        self.assertEqual(obj._send_ranking_message.await_args.args[1], 456)

    def test_each_period_index_contains_only_its_own_reports(self):
        from community_features import _DASHBOARD_CACHE
        _DASHBOARD_CACHE.clear()
        obj = self.make_features()
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/periodo")
        obj.club_trophy_ranking_text = Mock(side_effect=AssertionError("registered-only ranking must not be used"))
        obj.coefficient_ranking_text = Mock(return_value={"report_url": "https://telegra.ph/progressione"})
        report_full = ["REPORT", "Data", "", "👥 Ambito: quattro club", "🏆 CLASSIFICA TROFEI", "1. Utente +10", "", "🔥 CLASSIFICA PROGRESSIONE", "📊 RESOCONTO", "🏆 Coppe totali reali: 100"]
        obj.periodic_report_text = Mock(return_value=("📊 REPORT COMPLETO: https://telegra.ph/report", report_full, {"TITANI ABUSIVI": {"delta": 10, "players": 2}}))
        for days, count in ((0, 3), (7, 3), (15, 3), (30, 3)):
            obj.rankings_dashboard_text(-1001, days)
            lines = obj._publish_telegraph.call_args.args[1]
            links = [line for line in lines if line.startswith("[[URL:")]
            self.assertEqual(len(links), count)
            self.assertTrue(all(line.endswith("|Apri]]") for line in links))
            self.assertEqual(obj._publish_telegraph.call_count, 3 * ((0, 7, 15, 30).index(days) + 1))
        self.assertEqual(obj.dashboard_command("dash_t_0_7"), "classifica della community 7")

    def test_command_guide_describes_current_period_hubs(self):
        from community_features import HELP_TEXT
        for command in ("Classifica", "Classifiche", "classifiche oggi", "classifiche 7", "classifiche 15", "classifiche 30",
                        "classifica oggi", "progressione oggi"):
            self.assertIn(f"[[CMDNAME:{command}]]", HELP_TEXT)

    def test_calendar_trophy_snapshot_excludes_next_period(self):
        from datetime import timezone
        obj = self.make_features()
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        end = datetime(2026, 9, 16, tzinfo=timezone.utc)
        history = [
            {"recorded_at": "2026-08-31T23:00:00Z", "trophies": 100},
            {"recorded_at": "2026-09-15T23:00:00Z", "trophies": 110},
            {"recorded_at": "2026-09-16T01:00:00Z", "trophies": 125},
        ]
        self.assertEqual(obj._window_trophy_values(history, start, end), (100, 110))

    def test_scheduled_index_links_open_telegraph_directly(self):
        from datetime import timezone
        obj = self.make_features()
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        end = datetime(2026, 9, 16, tzinfo=timezone.utc)
        captured = []
        def publish(_title, lines):
            captured.append(lines)
            return f"https://telegra.ph/page-{len(captured)}"
        with (
            patch.object(obj, "_publish_telegraph", side_effect=publish),
            patch.object(obj, "club_trophy_ranking_text", side_effect=AssertionError("registered-only ranking must not be used")),
            patch.object(obj, "coefficient_ranking_text", return_value={"report_url": "https://telegra.ph/progression"}),
            patch.object(obj, "periodic_report_text", return_value=("REPORT", ["REPORT", "Data", "", "👥 Ambito: quattro club", "🏆 CLASSIFICA TROFEI", "1. player +10", "", "🔥 CLASSIFICA PROGRESSIONE", "📊 RESOCONTO", "🏆 Coppe totali reali: 100"], {"TITANI ABUSIVI": {"delta": 10, "players": 2}})),
        ):
            result = obj.scheduled_dashboard_snapshot(-1001, 15, (start, end))
        self.assertEqual(result["report_url"], "https://telegra.ph/page-3")
        self.assertEqual(sum("[[URL:" in row for row in captured[-1]), 3)
        self.assertIn("📋 RESOCONTO", result["text"])
        self.assertIn("🏆 Coppe totali reali: 100", result["text"])
        self.assertFalse(any("[[DASH:" in row for row in captured[-1]))
        nodes = obj._telegraph_nodes(["[[URL:https://telegra.ph/report|Apri]]"])
        self.assertEqual(nodes[0]["children"][0]["attrs"]["href"], "https://telegra.ph/report")

    def test_club_page_distinguishes_roster_from_measured_players(self):
        obj = self.make_features()
        published = []
        obj._publish_telegraph = Mock(side_effect=lambda title, lines: (
            published.append((title, lines)) or f"https://telegra.ph/page-{len(published)}"
        ))
        full = ["REPORT", "Data", "", "👥 Ambito: quattro club", "🏆 CLASSIFICA TROFEI",
                "1. player +10", "", "🔥 CLASSIFICA PROGRESSIONE", "📊 RESOCONTO"]
        obj.periodic_report_text = Mock(return_value=("REPORT", full, {
            "TITANI ABUSIVI": {"delta": 10, "players": 2, "roster": 30},
        }))
        obj.coefficient_ranking_text = Mock(return_value={"report_url": "https://telegra.ph/progression"})
        obj._direct_dashboard_snapshot(-1001, 7, None)
        clubs = next(lines for title, lines in published if title == "Classifica 4 Club — 7 GIORNI")
        self.assertIn("1. TITANI ABUSIVI — +10 (2/30 giocatori)", clubs)
        self.assertNotIn("saldo", " ".join(clubs).casefold())
        self.assertNotIn("con storico sufficiente", " ".join(clubs))
        self.assertGreater(clubs.index("📌 Storico Trofei: 2/30 giocatori misurabili nel periodo."),
                           clubs.index("1. TITANI ABUSIVI — +10 (2/30 giocatori)"))
        trophy_page = next(lines for title, lines in published if title == "Trofei Globali 4 Club — 7 GIORNI")
        self.assertTrue(any("🏆 CLASSIFICA TROFEI" in str(node.get("children")) and node.get("tag") == "h3"
                            for node in obj._telegraph_nodes(trophy_page)))

    def test_fifteen_and_thirty_day_club_pages_mark_missing_baseline(self):
        obj = self.make_features()
        for period in (15, 30):
            published = []
            obj._publish_telegraph = Mock(side_effect=lambda title, lines: (
                published.append((title, lines)) or f"https://telegra.ph/page-{len(published)}"
            ))
            full = ["REPORT", "Data", "", "👥 Ambito: quattro club", "🏆 CLASSIFICA TROFEI",
                    "Storico Trofei non ancora sufficiente per calcolare questo periodo.",
                    "", "🔥 CLASSIFICA PROGRESSIONE", "📊 RESOCONTO"]
            obj.periodic_report_text = Mock(return_value=("REPORT", full, {
                "TITANI ABUSIVI": {"delta": 0, "players": 0, "roster": 30},
            }))
            obj.coefficient_ranking_text = Mock(return_value={"report_url": "https://telegra.ph/progression"})
            obj._direct_dashboard_snapshot(-1001, period, None)
            clubs = next(lines for title, lines in published if title == f"Classifica 4 Club — {period} GIORNI")
            self.assertIn("📌 Storico Trofei: 0/30 giocatori misurabili nel periodo.", clubs)
            self.assertTrue(any("Storico Trofei non ancora sufficiente" in line for line in clubs))
            self.assertFalse(any("Nessun club con crescita" in line for line in clubs))

    def test_scheduled_today_index_focuses_on_global_roster(self):
        obj = self.make_features()
        for period in (0, 7, 15, 30):
            lines = obj._build_rankings_dashboard_text(period, publish=False, include_today_reports=True)
            self.assertFalse(any("registrati e non registrati" in line.casefold() for line in lines))
            self.assertTrue(any("roster completi" in line.casefold() for line in lines))
            self.assertEqual([row for row in lines if row.startswith("[[DASH:")], [
                f"[[DASH:dash_t_2_{period}|Apri]]",
                f"[[DASH:dash_p_2_{period}|Apri]]",
                f"[[DASH:dash_t_1_{period}|Apri]]",
            ])
            self.assertNotIn(f"[[DASH:dash_r_2_{period}|Apri]]", lines)
        self.assertEqual(obj.dashboard_command("dash_r_0_0"), "report community oggi")

    async def test_report_today_from_dashboard_uses_requested_scope(self):
        obj = self.make_features()
        obj.periodic_report_text = Mock(return_value="REPORT OGGI")
        message = SimpleNamespace(chat_id=-1001, chat=SimpleNamespace(type="group"),
                                  from_user=SimpleNamespace(id=456), reply_text=AsyncMock())
        context = SimpleNamespace(user_data={"_registered_user": {"player_tag": "2GU9UV2RG"}})
        self.assertTrue(await obj.handle_command(message, context, "report club oggi"))
        obj.periodic_report_text.assert_called_once_with(-1001, "community_club", 0)

    @patch("community_features.requests.post")
    def test_periodic_report_summary_and_scope(self, post):
        obj = self.make_features()
        obj.supabase_url = "https://example.supabase.co"
        obj.supabase_key = "test-secret"
        members = [
            {"player_tag": "AAA", "player_name": "Titan", "club_name": "TITANI ABUSIVI"},
            {"player_tag": "BBB", "player_name": "Outside", "club_name": "OTHER"},
        ]
        roster = [
            {"player_tag": "AAA", "player_name": "Titan", "club_name": "TITANI ABUSIVI"},
            {"player_tag": "CCC", "player_name": "Unregistered", "club_name": "TITANI ABUSIVI"},
        ]
        def get_rows(table, params):
            if table == "community_members":
                return members
            if table == "club_roster_daily":
                if params.get("select") == "snapshot_date":
                    return [{"snapshot_date": "2026-09-24"}]
                return roster if params.get("club_name") == "eq.TITANI ABUSIVI" else []
            return []
        obj._get = Mock(side_effect=get_rows)
        obj.history_fetcher = Mock(side_effect=lambda tag, days: [{"trophies": {"AAA": 100, "BBB": 200, "CCC": 300}[tag]}])
        obj.change_calculator = Mock(side_effect=lambda history, current: {"7d": None if current == 100 else 10})
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/full")
        post.return_value.json.return_value = [{
            "player_tag": "AAA", "player_name": "Titan", "progression_value": 15,
            "positive_trophies": 10, "battle_count": 1,
        }]
        post.return_value.raise_for_status.return_value = None

        for scope, expected_tags, expected_cups in (
            ("community", {"AAA", "BBB"}, "300"),
            ("community_club", {"AAA"}, "100"),
            ("global_clubs", {"AAA", "CCC"}, "400"),
            ("global_single:titani", {"AAA", "CCC"}, "400"),
        ):
            with self.subTest(scope=scope):
                summary = obj.periodic_report_text(123, scope, 7)
                sent_tags = set(post.call_args.kwargs["json"]["p_player_tags"])
                self.assertEqual(sent_tags, expected_tags)
                self.assertIn("🏆 Coppe totali reali: " + expected_cups, summary)
                self.assertIn("🏆 CLASSIFICA TROFEI", summary)
                self.assertIn("📋 RESOCONTO", summary)
                self.assertIn("📊 REPORT COMPLETO", summary)
                self.assertNotIn("CLASSIFICA PROGRESSIONE", summary)
                self.assertIn("🧮 Coeff. medio Progressione: 1,5000", summary)
                self.assertNotIn("🧮 Coeff. Progressione:", summary)
                self.assertIn("CLASSIFICA PROGRESSIONE", "\n".join(obj._publish_telegraph.call_args.args[1]))
                self.assertIn("🧮 Coeff. medio Progressione: 1,5000", obj._publish_telegraph.call_args.args[1])

        published_before = obj._publish_telegraph.call_count
        summary, full, club_totals = obj.periodic_report_text(123, "global_clubs", 7, return_full=True, publish=False)
        self.assertEqual(obj._publish_telegraph.call_count, published_before)
        self.assertNotIn("📊 REPORT COMPLETO", summary)
        self.assertIn("1. Unregistered", "\n".join(full))
        self.assertEqual(full[3], "👥 Ambito: roster completo dei 4 club ABUSIVI")
        trophy_section = full[full.index("🏆 CLASSIFICA TROFEI") + 1:full.index("🔥 CLASSIFICA PROGRESSIONE")]
        self.assertTrue(trophy_section[-2].startswith("📌 Storico Trofei disponibile:"))
        self.assertTrue(any(line.startswith("1. Unregistered") for line in trophy_section[:-2]))
        self.assertIn("🔥 CLASSIFICA PROGRESSIONE", full)
        self.assertIn("📊 RESOCONTO", full)
        self.assertEqual(club_totals["TITANI ABUSIVI"]["players"], 1)

        # The same three scopes use the exact calendar boundaries and retain
        # their own current-roster membership instead of sharing a tag set.
        obj.history_fetcher = Mock(side_effect=lambda tag, days: [
            {"recorded_at": "2026-09-01T00:00:00Z", "trophies": {"AAA": 90, "BBB": 180, "CCC": 290}[tag]},
            {"recorded_at": "2026-09-15T22:00:00Z", "trophies": {"AAA": 100, "BBB": 200, "CCC": 300}[tag]},
        ])
        window = (datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 16, tzinfo=timezone.utc))
        for scope, expected_tags in (("community", {"AAA", "BBB"}),
                                     ("community_club", {"AAA"}),
                                     ("global_single:titani", {"AAA", "CCC"})):
            with self.subTest(calendar_scope=scope):
                obj.periodic_report_text(123, scope, 15, window=window)
                self.assertEqual(set(post.call_args.kwargs["json"]["p_player_tags"]), expected_tags)
                self.assertEqual(post.call_args.kwargs["json"]["p_start"], window[0].isoformat())
                self.assertIn("coefficient_progression_rows_range", post.call_args.args[0])

    @patch("community_features.requests.post")
    def test_large_report_fetches_independent_histories_concurrently(self, post):
        from threading import Lock
        import time as clock
        obj = self.make_features()
        obj.supabase_url, obj.supabase_key = "https://example.supabase.co", "test-key"
        members = [{"player_tag": f"TAG{i}", "player_name": f"Player {i}"} for i in range(18)]
        obj._get = Mock(return_value=members)
        lock = Lock()
        active = peak = 0
        def history(tag, days):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            clock.sleep(0.01)
            with lock:
                active -= 1
            return [{"trophies": 100}]
        obj.history_fetcher = Mock(side_effect=history)
        obj.change_calculator = Mock(return_value={"7d": 5})
        post.return_value.json.return_value = []
        summary, full, _ = obj.periodic_report_text(-100, "community", 7, return_full=True, publish=False)
        self.assertGreater(peak, 1)
        self.assertEqual(obj.history_fetcher.call_count, 18)
        self.assertIn("🏆 Coppe totali reali: 1.800", summary)
        self.assertEqual(sum(line.startswith(tuple(f"{i}. " for i in range(1, 19))) for line in full), 18)

    def test_large_ranking_keeps_member_results_with_parallel_history(self):
        from threading import Lock
        import time as clock
        obj = self.make_features()
        obj.members = Mock(return_value=[{"player_tag": f"TAG{i}", "player_name": f"Player {i}"} for i in range(18)])
        lock = Lock()
        active = peak = 0
        def history(tag, days):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            clock.sleep(0.005)
            with lock:
                active -= 1
            return [{"recorded_at": "2026-09-25T12:00:00Z", "trophies": 100 + int(tag[3:])}]
        obj.history_fetcher = Mock(side_effect=history)
        obj.change_calculator = Mock(return_value={"today": 5})
        rows = obj.ranking(-123, 0)
        self.assertGreater(peak, 1)
        self.assertEqual(len(rows), 18)
        self.assertEqual(rows[0]["tag"], "TAG17")
        self.assertTrue(all(row["delta"] == 5 for row in rows))

    async def test_coefficient_guide_routes_directly_to_telegraph(self):
        obj = self.make_features()
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/guida-coefficiente")
        obj._send_ranking_message = AsyncMock(return_value=True)
        message = SimpleNamespace(chat_id=123, chat=SimpleNamespace(type="group"), from_user=SimpleNamespace(id=456))
        context = SimpleNamespace(user_data={})
        self.assertTrue(await obj.handle_command(message, context, "guida coefficiente abusivo"))
        obj._publish_telegraph.assert_called_once()
        payload = obj._send_ranking_message.await_args.args[2]
        self.assertEqual(payload["report_url"], "https://telegra.ph/guida-coefficiente")
        self.assertIn("IL PRIMO E UNICO SISTEMA AL MONDO", payload["fallback"])

    def test_coefficient_guide_matches_current_model_and_cause_limits(self):
        guide = "\n".join(coefficient_guide_lines())
        for term in ("2.000", "3.000", "Bonus osservato +X", "Team Value", "Pareggio", "Sopravvivenza in singolo"):
            self.assertIn(term, guide)
        self.assertIn("×1,2370", guide)
        self.assertIn("×1,5500", guide)

    @patch.dict(os.environ, {"TELEGRAPH_ACCESS_TOKEN": "test-token"})
    @patch("community_features.requests.post")
    def test_publish_success_returns_url_without_logging_token(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.status_code = 200
        response.json.return_value = {"ok": True, "result": {"url": "https://telegra.ph/report-09-23"}}
        post.return_value = response
        with patch("builtins.print") as printed:
            url = self.make_features()._publish_telegraph("Report", ["TITOLO", "Dato: 1"])
        self.assertEqual(url, "https://telegra.ph/report-09-23")
        logs = " ".join(str(call) for call in printed.call_args_list)
        self.assertIn("TELEGRAPH PAGE CREATED", logs)
        self.assertNotIn("test-token", logs)

    @patch.dict(os.environ, {"TELEGRAPH_ACCESS_TOKEN": "test-token"})
    @patch("community_features.requests.post")
    def test_publish_api_error_is_safe(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.status_code = 200
        response.json.return_value = {"ok": False, "error": "CONTENT_TOO_BIG"}
        post.return_value = response
        with self.assertLogs("community_features", level="ERROR") as captured:
            self.assertIsNone(self.make_features()._publish_telegraph("Report", ["Dato"]))
        logs = "\n".join(captured.output)
        self.assertIn("CONTENT_TOO_BIG", logs)
        self.assertNotIn("test-token", logs)

    @patch.dict(os.environ, {"TELEGRAPH_ACCESS_TOKEN": "test-token"})
    @patch("community_features.requests.post")
    def test_large_report_is_split_and_returns_index_page(self, post):
        counter = {"value": 0}

        def response_for_page(*args, **kwargs):
            counter["value"] += 1
            response = Mock()
            response.raise_for_status.return_value = None
            response.status_code = 200
            response.json.return_value = {
                "ok": True,
                "result": {"url": f"https://telegra.ph/page-{counter['value']}"},
            }
            return response

        post.side_effect = response_for_page
        obj = self.make_features()
        obj._telegraph_nodes = Mock(return_value=[
            {"tag": "p", "children": ["x" * 2000]} for _ in range(60)
        ])

        url = obj._publish_telegraph("Progressione completa", ["dati"])

        self.assertGreater(post.call_count, 2)
        self.assertEqual(url, f"https://telegra.ph/page-{post.call_count}")
        index_payload = post.call_args.kwargs["data"]["content"]
        self.assertIn("Apri parte 1/", index_payload)
        self.assertNotIn("test-token", index_payload)

    async def test_sender_uses_inline_report_button(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        payload = self.make_features()._telegraph_reply(
            ["Riepilogo"], "https://telegra.ph/report-09-23", ["Report completo"]
        )
        with patch("builtins.print") as printed:
            self.assertTrue(await self.make_features()._send_ranking_message(context, 123, payload))
        kwargs = bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["text"], "Riepilogo")
        self.assertIsNotNone(kwargs["reply_markup"])
        self.assertIn("TELEGRAPH REPORT DELIVERED", " ".join(str(call) for call in printed.call_args_list))

    async def test_plain_ranking_becomes_top_ten_plus_full_report(self):
        obj = self.make_features()
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/classifica-completa")
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        full = "CLASSIFICA TROFEI\n\n" + "\n".join(f"{i}. Player {i}" for i in range(1, 16))
        self.assertTrue(await obj._send_ranking_message(context, 123, full))
        kwargs = bot.send_message.await_args.kwargs
        self.assertIn("10. Player 10", kwargs["text"])
        self.assertNotIn("11. Player 11", kwargs["text"])
        self.assertIsNotNone(kwargs["reply_markup"])

    async def test_ranking_telegraph_failure_is_logged_and_telegram_still_sends(self):
        obj = self.make_features()
        obj._publish_telegraph = Mock(return_value=None)
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        full = "CLASSIFICA OGGI\n\n1. Giorgio — +42\n2. Anna — +20"
        with self.assertLogs("community_features", level="WARNING") as captured:
            delivered = await obj._send_ranking_message(context, 123, full)
        self.assertTrue(delivered)
        self.assertEqual(bot.send_message.await_args.kwargs["text"], full)
        self.assertIsNone(bot.send_message.await_args.kwargs["reply_markup"])
        self.assertIn("TELEGRAPH RANKING FALLBACK", " ".join(captured.output))

    @patch.dict(os.environ, {"TELEGRAPH_ACCESS_TOKEN": ""})
    def test_missing_telegraph_configuration_reports_safe_reason(self):
        with self.assertLogs("community_features", level="WARNING") as captured:
            self.assertIsNone(self.make_features()._publish_telegraph("Report", ["Dato"]))
        self.assertIn("access token not configured", " ".join(captured.output))

    @patch.dict(os.environ, {"TELEGRAPH_ACCESS_TOKEN": "test-token"})
    @patch("community_features.time.sleep")
    @patch("community_features.requests.post")
    def test_telegraph_respects_flood_wait_and_retries_same_page(self, post, sleep):
        post.side_effect = [
            SimpleNamespace(status_code=200, raise_for_status=Mock(), json=Mock(return_value={"ok": False, "error": "FLOOD_WAIT_5"})),
            SimpleNamespace(status_code=200, raise_for_status=Mock(), json=Mock(return_value={"ok": True, "result": {"url": "https://telegra.ph/report-test"}})),
        ]
        self.assertEqual(self.make_features()._publish_telegraph("Report test", ["Dati"]), "https://telegra.ph/report-test")
        sleep.assert_called_once_with(6)
        self.assertEqual(post.call_count, 2)

    async def test_numbered_battle_log_is_not_republished_as_ranking(self):
        obj = self.make_features()
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/should-not-be-used")
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        battle_log = "PROGRESSIONE GRIFF\n\n1. 14:30 — Footbrawl"
        self.assertTrue(await obj._send_ranking_message(context, 123, battle_log))
        obj._publish_telegraph.assert_not_called()
        self.assertEqual(bot.send_message.await_args.kwargs["text"], battle_log)

    async def test_progressione_club_is_routed_to_club_ranking(self):
        obj = self.make_features()
        obj.coefficient_ranking_text = Mock(return_value="CLASSIFICA PROGRESSIONE CLUB")
        obj._send_ranking_message = AsyncMock(return_value=True)
        message = SimpleNamespace(
            chat_id=123,
            chat=SimpleNamespace(type="group"),
            from_user=SimpleNamespace(id=456),
            reply_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={})

        self.assertTrue(await obj.handle_command(message, context, "Progressione club"))
        obj.coefficient_ranking_text.assert_called_once_with(123, "community_club", None)
        obj._send_ranking_message.assert_awaited_once_with(
            context, 456, "CLASSIFICA PROGRESSIONE CLUB"
        )

    def test_progressione_oggi_builds_report_payload(self):
        obj = self.make_features()
        obj._get = Mock(return_value=[{
            "player_name": "Sens",
            "battle_time": datetime.now(timezone.utc).isoformat(),
            "brawler_name": "Nita",
            "brawler_trophies_before": 500,
            "mode": "brawlBall",
            "trophy_change": 8,
            "observed_extra": 0,
            "bonus_type": None,
        }])
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/progressione-oggi")
        payload = obj.progression_detail_text("2GU9UV2RG", 0)
        self.assertEqual(payload["report_url"], "https://telegra.ph/progressione-oggi")
        self.assertIn("Partite osservate valide: 1", payload["text"])
        self.assertIn("Vittorie: 0 · Sconfitte: 0 · Win rate: n.d.", payload["text"])
        self.assertIn("Data:", payload["fallback"])
        self.assertIn("🦸 Nita", payload["fallback"])
        self.assertIn("[[URL:https://telegra.ph/progressione-oggi|Apri]]", payload["fallback"])
        detail = obj._publish_telegraph.call_args_list[0].args[1]
        self.assertNotIn("SESSIONI", detail)
        self.assertIn("Sessioni osservate: 1", detail)
        self.assertEqual(detail.index("Sessioni osservate: 1"), next(i for i, line in enumerate(detail) if line.startswith("Fasce:")) + 1)
        self.assertIn("LOG BATTAGLIE", detail)
        self.assertIn("Brawler: Nita", detail)
        self.assertIn("Risultato: Risultato non disponibile", detail)
        self.assertIs(obj.progression_detail_text("2GU9UV2RG", 0), payload)
        self.assertEqual(obj._publish_telegraph.call_count, 2)
        self.assertEqual(obj._get.call_count, 2)

    def test_telegraph_brawler_links_with_unicode_slugs_render_as_links(self):
        obj = self.make_features()
        url = "https://telegra.ph/Progressione-TAƬσρσᵍⁱᵍⁱᵒ--OGGI-09-25"
        nodes = obj._telegraph_nodes(["PROGRESSIONE ABUSIVA — TA", "DETTAGLIO BRAWLER", f"[[URL:{url}|Apri]]"])
        self.assertTrue(any(node.get("tag") == "p" and any(
            isinstance(child, dict) and child.get("tag") == "a" and child.get("attrs", {}).get("href") == url
            for child in node.get("children", [])
        ) for node in nodes))
        self.assertNotIn("[[URL:", str(nodes))
        self.assertEqual(obj._telegram_fallback_links(f"[[URL:{url}|Apri]]"), f"📖 Apri il Telegraph: {url}")

    def test_interleaved_battles_are_numbered_per_brawler_and_separated(self):
        obj = self.make_features()
        start = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
        rows = []
        for index, brawler in enumerate(("Emz", "Emz", "Jessie", "Emz")):
            rows.append({"player_name": "Topo Gigio", "battle_time": (start + timedelta(minutes=index * 5)).isoformat(),
                         "brawler_name": brawler, "brawler_trophies_before": 800, "mode": "brawlBall",
                         "result": "victory" if index != 2 else "defeat", "trophy_change": 8 if index != 2 else -6,
                         "team_composition": None, "raw_battle": {}})
        obj._get = Mock(side_effect=[rows, []])
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/progressione")
        obj.progression_detail_text("2LVRCLV8LV")
        emz = obj._publish_telegraph.call_args_list[0].args[1]
        logs = emz[emz.index("LOG BATTAGLIE") + 1:]
        numbers = [line.split(".", 1)[0] for line in logs if line[:1].isdigit() and ". " in line]
        self.assertEqual(numbers, ["1", "2", "3"])
        nodes = obj._telegraph_nodes(emz)
        self.assertFalse(any(node.get("tag") == "h3" and node.get("children") == ["👥 SQUADRA"] for node in nodes))
        self.assertGreaterEqual(sum(node.get("children") == ["\u00a0"] for node in nodes), 2)
        self.assertIn("Vittorie: 3 · Sconfitte: 1 · Win rate: 75,0%", obj._publish_telegraph.call_args_list[-1].args[1])

    def test_progressione_oggi_includes_localized_team_solo_loss_and_bonus(self):
        obj = self.make_features()
        now = datetime.now(timezone.utc).isoformat()
        team_battle = {
            "player_name": "Giorgio", "battle_time": now,
            "brawler_name": "EL PRIMO", "brawler_trophies_before": 1800,
            "mode": "trioShowdown", "result": "victory", "placement": 1,
            "trophy_change": 13, "expected_base_delta": 11,
            "observed_extra": 2, "current_win_streak": None,
            "bonus_type": "bonus_observed", "team_max_brawler_trophies": 2000,
            "team_composition": [
                {"name": "Giorgio", "brawler_name": "EL PRIMO", "brawler_trophies": 1800},
                {"name": "Compagno", "brawler_name": "SURGE", "brawler_trophies": 1900},
                {"name": "Capitano", "brawler_name": "NITA", "brawler_trophies": 2000},
            ],
            "raw_battle": {},
        }
        solo_loss = {
            "player_name": "Giorgio", "battle_time": now,
            "brawler_name": "NITA", "brawler_trophies_before": 1000,
            "mode": "soloShowdown", "result": None, "placement": 9,
            "trophy_change": -10, "expected_base_delta": -10,
            "observed_extra": 0, "current_win_streak": None,
            "bonus_type": None, "team_max_brawler_trophies": None,
            "team_composition": None, "raw_battle": {"battle": {}},
        }
        obj._get = Mock(side_effect=[
            [team_battle, solo_loss],
            [
                {"name_en": "EL PRIMO", "name_it": "EL PRIMO"},
                {"name_en": "SURGE", "name_it": "ENERGETIK"},
                {"name_en": "NITA", "name_it": "NITA"},
            ],
        ])
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/progressione-giorgio")

        payload = obj.progression_detail_text("2LVRCLV8LV", 0)

        report = "\n".join("\n".join(call.args[1]) for call in obj._publish_telegraph.call_args_list[:-1])
        self.assertIn("Sopravvivenza in trio", report)
        self.assertIn("Risultato: Vittoria", report)
        self.assertIn("Extra osservato: +2 (Bonus osservato)", report)
        self.assertIn("Compagno — ENERGETIK — 1900", report)
        self.assertLess(report.index("Capitano — NITA — 2000"), report.index("Compagno — ENERGETIK — 1900"))
        self.assertLess(report.index("Compagno — ENERGETIK — 1900"), report.index("Giorgio — EL PRIMO — 1800"))
        self.assertLess(report.index("Extra osservato: +2"), report.index("Squadra:\n🥇 Capitano"))
        self.assertIn("Squadra:\n🥇 Capitano — NITA — 2000", report)
        self.assertIn("\n🥈 Compagno — ENERGETIK — 1900", report)
        self.assertIn("\n🥉 Giorgio — EL PRIMO — 1800", report)
        nodes = obj._telegraph_nodes(obj._publish_telegraph.call_args_list[0].args[1])
        self.assertFalse(any(node.get("tag") == "h3" and node.get("children") == ["👥 SQUADRA"] for node in nodes))
        self.assertTrue(any(node.get("tag") == "p" and any(isinstance(child, dict) and child.get("children") == ["👥 Squadra:"] for child in node.get("children", [])) for node in nodes))
        self.assertIn("Squadra: Modalità Solo", report)
        self.assertIn("Punti Progressione: 0 (sconfitta non conteggiata)", report)
        self.assertIn("🦸 EL PRIMO", payload["fallback"])
        self.assertIn("🦸 NITA", payload["fallback"])

    def test_comandi_links_open_telegraph_reference_pages(self):
        from community_features import HELP_TEXT, _DASHBOARD_CACHE
        _DASHBOARD_CACHE.clear()
        obj = self.make_features()
        published = []
        def publish(title, lines):
            published.append((title, lines))
            return f"https://telegra.ph/comandi-{len(published)}"
        obj._publish_telegraph = Mock(side_effect=publish)
        payload = obj._publish_command_guide()
        self.assertEqual(len(published), 12)
        self.assertEqual(payload["report_url"], "https://telegra.ph/comandi-12")
        guide = published[-1][1]
        self.assertEqual(sum(line.startswith("[[GUIDE:") for line in guide),
                         sum(line.startswith(("[[CMDNAME:", "[[CMD:")) for line in HELP_TEXT.splitlines()))
        self.assertFalse(any(line.startswith(("[[CMDNAME:", "[[CMD:")) for line in guide))
        nodes = obj._telegraph_nodes(guide)
        self.assertFalse(any("t.me/" in str(node) for node in nodes))
        self.assertTrue(any(node.get("tag") == "h3" and node.get("children") == ["🏆 CLASSIFICHE & REPORT"] for node in nodes))
        self.assertTrue(any(node.get("tag") == "h3" and node.get("children") == ["📑 REPORT PERIODICI — TROFEI + PROGRESSIONE"] for node in nodes))
        self.assertIs(obj._publish_command_guide(), payload)
        self.assertEqual(len(published), 12)

    def test_progressione_brawler_recovers_and_localizes_raw_team(self):
        obj = self.make_features()
        battle = {
            "player_name": "Giorgio",
            "battle_time": datetime.now(timezone.utc).isoformat(),
            "brawler_name": "EL PRIMO",
            "brawler_trophies_before": 1026,
            "mode": "duoShowdown",
            "result": None,
            "placement": 1,
            "trophy_change": 8,
            "expected_base_delta": 8,
            "observed_extra": 0,
            "current_win_streak": None,
            "bonus_type": None,
            "team_max_brawler_trophies": None,
            "team_composition": None,
            "raw_battle": {"battle": {"teams": [[
                {"tag": "#2LVRCLV8LV", "name": "Giorgio", "brawler": {"name": "EL PRIMO", "trophies": 1026}},
                {"tag": "#TEAMMATE", "name": "Compagno", "brawler": {"name": "SURGE", "trophies": 1660}},
            ]]}}
        }
        obj._get = Mock(side_effect=[
            [battle],
            [
                {"name_en": "EL PRIMO", "name_it": "EL PRIMO"},
                {"name_en": "SURGE", "name_it": "ENERGETIK"},
            ],
        ])
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/progressione-el-primo")

        payload = obj.progression_brawler_text("2LVRCLV8LV", "El Primo")

        self.assertIn("Squadra:", payload["fallback"])
        self.assertIn("Compagno — ENERGETIK — 1660", payload["fallback"])
        self.assertNotIn("Squadra: non disponibile", payload["fallback"])

    def test_progressione_brawler_marks_solo_team_as_not_expected(self):
        obj = self.make_features()
        battle = {
            "player_name": "Giorgio",
            "battle_time": datetime.now(timezone.utc).isoformat(),
            "brawler_name": "EL PRIMO",
            "brawler_trophies_before": 1026,
            "mode": "soloShowdown",
            "result": None,
            "placement": 3,
            "trophy_change": 2,
            "expected_base_delta": 2,
            "observed_extra": 0,
            "current_win_streak": None,
            "bonus_type": None,
            "team_max_brawler_trophies": None,
            "team_composition": None,
            "raw_battle": {"battle": {}},
        }
        obj._get = Mock(side_effect=[
            [battle],
            [{"name_en": "EL PRIMO", "name_it": "El Primo"}],
        ])
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/progressione-el-primo")

        payload = obj.progression_brawler_text("2LVRCLV8LV", "El Primo")

        self.assertIn("Squadra: Modalità Solo", payload["fallback"])
        self.assertNotIn("Squadra: non disponibile", payload["fallback"])

    def test_my_accounts_remains_plain_text(self):
        obj = self.make_features()
        obj.get_registered_user = Mock(return_value={"player_name": "Sens", "player_tag": "2GU9UV2RG"})
        obj.additional_accounts = Mock(return_value=[])
        self.assertEqual(obj.my_accounts_text(123), "I TUOI ACCOUNT\n\nPRINCIPALE - Sens #2GU9UV2RG")

    def test_progression_terms_are_always_localized(self):
        obj = self.make_features()
        self.assertEqual(obj._progression_mode_it("brawlBall"), "Footbrawl")
        self.assertEqual(obj._progression_mode_it("gemGrab"), "Arraffagemme")
        self.assertEqual(obj._progression_result_it("victory"), "Vittoria")
        self.assertEqual(obj._progression_result_it("defeat"), "Sconfitta")
        self.assertEqual(obj._progression_bonus_it("win_streak"), "Serie di vittorie")
        self.assertEqual(obj._progression_mode_it("futureTechnicalMode"), "Modalità non riconosciuta")

    @patch("community_features.requests.post")
    def test_report_coefficient_is_unavailable_without_positive_cups(self, post):
        obj = self.make_features()
        obj.supabase_url, obj.supabase_key = "https://example.supabase.co", "test-key"
        obj._get = Mock(return_value=[])
        post.return_value.json.return_value = []
        summary, full, _ = obj.periodic_report_text(123, "community", 7, return_full=True, publish=False)
        self.assertIn("🧮 Coeff. medio Progressione: n.d.", summary)
        self.assertIn("🧮 Coeff. medio Progressione: n.d.", full)

    def test_telegraph_ranking_has_uniform_rows_and_top_three_medals(self):
        nodes = self.make_features()._telegraph_nodes([
            "CLASSIFICA OGGI", "1. SUPERLUIGI — +668", "2. Persinox — +437",
            "3. Anna — +364", "10. NICCOLÒ — +75", "15. LEO — +46",
        ])
        self.assertEqual(nodes[0]["tag"], "h3")
        for node in nodes[1:]:
            self.assertEqual(node["tag"], "p")
        self.assertIn("🥇", str(nodes[1]))
        self.assertIn("🥈", str(nodes[2]))
        self.assertIn("🥉", str(nodes[3]))
        self.assertNotIn("h3", str(nodes[4:]))

    def test_report_progression_players_are_separated_without_spreading_trophy_rows(self):
        nodes = self.make_features()._telegraph_nodes([
            "🔥 REPORT UTENTI REGISTRATI — 7 GIORNI", "🏆 CLASSIFICA TROFEI",
            "1. Anna — +15", "2. Luca — +10", "",
            "🔥 CLASSIFICA PROGRESSIONE", "1. Anna", "🎮 Partite: 3",
            "🏆 Coppe: +15", "", "2. Luca", "🎮 Partite: 2",
            "🏆 Coppe: +10", "", "📊 RESOCONTO", "👥 Giocatori: 2",
        ])
        rendered = [str(node.get("children")) for node in nodes]
        trophy_second = next(i for i, value in enumerate(rendered) if "2. Luca — +10" in value)
        progression_second = next(i for i, value in enumerate(rendered) if "2. Luca'" in value)
        self.assertNotEqual(rendered[trophy_second - 1], "['\\xa0']")
        self.assertEqual(rendered[progression_second - 1], "['\\xa0']")
        self.assertTrue(any(node.get("tag") == "h3" and "CLASSIFICA PROGRESSIONE" in str(node) for node in nodes))

    @patch("player_tracking._brawlytix_progression")
    def test_skin_account_uses_same_owned_counts_as_stats(self, progression):
        obj = self.make_features()
        progression.return_value = {"skins_owned": 477, "skin_rarity_counts": {
            "mythic": 28, "legendary": 10, "true silver": 0, "rare": 103,
        }}
        catalog = ([{"external_id": str(i), "rarity": "MYTHIC"} for i in range(85)]
                   + [{"external_id": str(100 + i), "rarity": "LEGENDARY"} for i in range(59)]
                   + [{"external_id": str(200 + i), "rarity": "RARE"} for i in range(122)]
                   + [{"external_id": "500", "name_en": "STAR SHELLY"},
                      {"external_id": "501", "name_en": "WIZARD BARLEY"}])
        obj._get = Mock(side_effect=lambda table, *_: catalog if table == "skins_catalog" else [])
        obj._official_owned_skin_ids = Mock()
        result = obj.skin_account_text({"player_tag": "2GU9UV2RG"})
        self.assertIn("SKIN POSSEDUTE — ACCOUNT", result)
        self.assertIn("🎨 Totale: 477/268", result)
        self.assertIn("🔴 Mitiche\n28/85", result)
        self.assertIn("🟡 Leggendarie\n10/59", result)
        self.assertIn("🥈 Argento\n0/n.d.", result)
        self.assertNotIn("⚪ Senza rarità", result)
        self.assertNotIn("STAR SHELLY", result)
        self.assertNotIn("Fonte:", result)
        self.assertNotIn("Altre skin senza rarità", result)
        obj._official_owned_skin_ids.assert_not_called()
        fallback = obj._cached_skin_account_text("2GU9UV2RG")
        self.assertEqual(fallback, result)
        obj._get.assert_any_call("skins_catalog", unittest.mock.ANY)
        self.assertEqual(progression.call_count, 2)

    def test_skin_catalog_categories_keep_regular_pass_separate_from_pro(self):
        obj = self.make_features()
        self.assertEqual(obj._skin_category_label({"rarity": "EPIC", "acquisition_type": "brawl_pass"}), "Brawl Pass")
        self.assertEqual(obj._skin_category_label({"rarity": "RANKED_PASS", "acquisition_type": "pass"}), "Pass Pro")
        self.assertEqual(obj._skin_category_label({"source_payload": {"tid": "TID_BROCK_PROPASS_PROGRESSION_SKIN_1"}}), "Pass Pro")
        self.assertEqual(obj._skin_category_label({"source_payload": {"tid": "TID_UNDERTAKER_HAT_SKIN"}}), "Base (varianti)")

    def test_skin_brawler_groups_owned_and_missing_without_account_fallback(self):
        obj = self.make_features()
        catalog = [
            {"external_id": "1", "brawler_id": 1, "brawler_name": "MOE", "name_en": "Moe One", "rarity": "RARE"},
            {"external_id": "2", "brawler_id": 1, "brawler_name": "MOE", "name_en": "Moe Two", "rarity": "RARE"},
        ]
        obj._get = Mock(side_effect=lambda table, *_: catalog if table == "skins_catalog" else [{"name_it": "Moe"}])
        obj._official_owned_skin_ids = Mock(return_value={1})
        result = obj.skin_account_text({"player_tag": "ABC"}, brawler_name="Moe", mode="full")
        self.assertIn("🎨 Rare — 1/2", result)
        self.assertIn("✅ Possedute: Moe One", result)
        self.assertIn("❌ Mancanti: Moe Two", result)
        obj._official_owned_skin_ids.side_effect = requests.RequestException("bridge down")
        unavailable = obj.skin_account_text({"player_tag": "ABC"}, brawler_name="Moe", mode="full")
        self.assertIn("fonte dei nomi", unavailable)
        self.assertNotIn("SKIN POSSEDUTE — ACCOUNT", unavailable)

    @patch("player_tracking._brawlytix_progression", return_value={})
    def test_skin_account_does_not_invent_ownership_when_stats_unavailable(self, progression):
        result = self.make_features().skin_account_text({"player_tag": "2GU9UV2RG"})
        self.assertIn("non sono disponibili", result)
        self.assertNotIn("CATALOGO", result)

    def test_skin_telegraph_has_spacing_and_category_headings(self):
        obj = self.make_features()
        nodes = obj._telegraph_nodes([
            "SKIN POSSEDUTE — ACCOUNT", "", "🎨 Totale possedute: 38", "",
            "📊 PER RARITÀ", "", "🔴 Mitiche", "Possedute / totali: 28/85", "",
            "🟡 Leggendarie", "Possedute / totali: 10/59",
        ])
        headings = [i for i, node in enumerate(nodes) if node.get("tag") == "h4"]
        self.assertEqual(len(headings), 2)
        self.assertTrue(all(nodes[i - 1] == {"tag": "p", "children": ["\u00a0"]} for i in headings))

    async def test_skin_output_is_read_from_private_telegraph_button(self):
        obj = self.make_features()
        obj._publish_telegraph = Mock(return_value="https://telegra.ph/skin-possedute")
        obj._send_ranking_message = AsyncMock(return_value=True)
        await obj.send_skin_telegraph(SimpleNamespace(), 456, "SKIN POSSEDUTE — ACCOUNT\n🔴 Mitiche\nPossedute: 28")
        payload = obj._send_ranking_message.await_args.args[2]
        self.assertEqual(obj._send_ranking_message.await_args.args[1], 456)
        self.assertEqual(payload["report_url"], "https://telegra.ph/skin-possedute")
        self.assertNotIn("Possedute: 28", payload["text"])
        self.assertIn("Possedute: 28", "\n".join(obj._publish_telegraph.call_args.args[1]))

    @patch.dict(os.environ, {"TELEGRAM_TOKEN": "000000:test-token", "GEMINI_API_KEY": "test-key"})
    def test_explicit_stats_send_uses_private_destination_for_group(self):
        from app import _manual_command_reply_chat_id
        member = SimpleNamespace(id=437136453)
        group = SimpleNamespace(chat_id=-1001083451734, chat=SimpleNamespace(type="supergroup"), from_user=member)
        private = SimpleNamespace(chat_id=437136453, chat=SimpleNamespace(type="private"), from_user=member)
        self.assertEqual(_manual_command_reply_chat_id(group), 437136453)
        self.assertEqual(_manual_command_reply_chat_id(private), 437136453)

    def test_stats_telegraph_categories_have_space_between_sections(self):
        nodes = self.make_features()._telegraph_nodes([
            "STATS PLAYER", "PROFILO", "Trofei: 100", "RANKED", "Ranked attuale: Oro I",
            "COLLEZIONE", "Skin: 50",
        ])
        for i, node in enumerate(nodes):
            if node.get("tag") == "h3" and i:
                self.assertEqual(nodes[i - 1], {"tag": "p", "children": ["\u00a0"]})

    @patch.dict(os.environ, {"TELEGRAM_TOKEN": "000000:test-token", "GEMINI_API_KEY": "test-key"})
    async def test_group_command_is_deleted_and_reply_goes_private_with_group_scope(self):
        from app import _private_group_command
        bot = SimpleNamespace(send_chat_action=AsyncMock(), delete_message=AsyncMock(), send_message=AsyncMock())
        message = SimpleNamespace(chat_id=-100123, message_id=42,
                                  chat=SimpleNamespace(type="supergroup"), from_user=SimpleNamespace(id=456))
        routed = await _private_group_command(message, SimpleNamespace(bot=bot), "leggi")
        self.assertEqual(routed.chat_id, -100123)
        bot.delete_message.assert_awaited_once_with(chat_id=-100123, message_id=42)
        await routed.reply_text("Risposta")
        bot.send_message.assert_awaited_once_with(chat_id=456, text="Risposta")

    @patch.dict(os.environ, {"TELEGRAM_TOKEN": "000000:test-token", "GEMINI_API_KEY": "test-key"})
    def test_all_common_manual_command_families_are_private(self):
        from app import _is_manual_deterministic_command, _is_public_group_command
        for command in (
            "Report", "Classifiche", "Classifica oggi", "Progressione oggi",
            "Guida coefficiente abusivo", "Coefficiente abusivo", "Stats",
            "Skin", "Draft ranked", "registrami #2GU9UV2RG",
            "elenco registrati", "elenco utenti", "elenco inattivi", "comandi", "quante skin ho",
        ):
            with self.subTest(command=command):
                self.assertTrue(_is_manual_deterministic_command(command))
                self.assertEqual(
                    _is_public_group_command(command),
                    command in {"registrami #2GU9UV2RG", "elenco registrati", "elenco utenti", "elenco inattivi"},
                )
        self.assertFalse(_is_manual_deterministic_command("Ciao, come va?"))

    def test_telegraph_battle_numbers_do_not_receive_ranking_medals(self):
        nodes = self.make_features()._telegraph_nodes([
            "PROGRESSIONE NITA", "LOG BATTAGLIE", "1. 10:00 — Footbrawl",
            "2. 10:03 — Arraffagemme", "3. 10:06 — Trio",
        ])
        rendered = str(nodes)
        self.assertNotIn("🥇", rendered)
        self.assertNotIn("🥈", rendered)
        self.assertNotIn("🥉", rendered)

    def test_telegraph_fields_get_coherent_icons(self):
        nodes = self.make_features()._telegraph_nodes([
            "PROGRESSIONE GRIFF", "Data: 23/09/2026", "Coppe: 500 → 508 (+8)",
            "Risultato: Vittoria", "Risultato: Sconfitta", "Risultato: Pareggio",
            "Squadra: non disponibile",
        ])
        rendered = str(nodes)
        for icon in ("📈", "📅", "🏆", "✅", "❌", "🤝", "👥"):
            self.assertIn(icon, rendered)


if __name__ == "__main__":
    unittest.main()
