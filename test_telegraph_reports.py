import os
import unittest
from datetime import datetime, timezone
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
                self.assertNotIn("Coeff.", summary)
                self.assertIn("CLASSIFICA PROGRESSIONE", "\n".join(obj._publish_telegraph.call_args.args[1]))

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
        self.assertIn("Data:", payload["fallback"])
        self.assertIn("Sessioni:", payload["fallback"])
        self.assertIn("LOG BATTAGLIE", payload["fallback"])
        self.assertIn("Brawler: Nita", payload["fallback"])
        self.assertIn("Risultato: Risultato non disponibile", payload["fallback"])

    def test_progressione_oggi_includes_localized_team_solo_loss_and_bonus(self):
        obj = self.make_features()
        now = datetime.now(timezone.utc).isoformat()
        team_battle = {
            "player_name": "Giorgio", "battle_time": now,
            "brawler_name": "EL PRIMO", "brawler_trophies_before": 1800,
            "mode": "trioShowdown", "result": "victory", "placement": 1,
            "trophy_change": 13, "expected_base_delta": 11,
            "observed_extra": 2, "current_win_streak": None,
            "bonus_type": "bonus_observed", "team_max_brawler_trophies": 1900,
            "team_composition": [
                {"name": "Giorgio", "brawler_name": "EL PRIMO", "brawler_trophies": 1800},
                {"name": "Compagno", "brawler_name": "SURGE", "brawler_trophies": 1900},
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

        report = payload["fallback"]
        self.assertIn("Sopravvivenza in trio", report)
        self.assertIn("Risultato: Vittoria", report)
        self.assertIn("Extra osservato: +2 (Bonus osservato)", report)
        self.assertIn("Compagno — ENERGETIK — 1900", report)
        self.assertIn("Team Value: 1900", report)
        self.assertIn("Squadra: Modalità Solo", report)
        self.assertIn("Punti Progressione: 0 (sconfitta non conteggiata)", report)

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
        obj._get = Mock(return_value=catalog)
        obj._official_owned_skin_ids = Mock()
        result = obj.skin_account_text({"player_tag": "2GU9UV2RG"})
        self.assertIn("SKIN POSSEDUTE — ACCOUNT", result)
        self.assertIn("🎨 Totale: 477/268", result)
        self.assertIn("🔴 Mitiche\nPossedute / totali: 28/85", result)
        self.assertIn("🟡 Leggendarie\nPossedute / totali: 10/59", result)
        self.assertIn("🥈 Argento\nPossedute / totali: 0/n.d.", result)
        self.assertNotIn("⚪ Senza rarità", result)
        self.assertNotIn("STAR SHELLY", result)
        self.assertNotIn("Fonte:", result)
        self.assertNotIn("Altre skin senza rarità", result)
        obj._official_owned_skin_ids.assert_not_called()
        fallback = obj._cached_skin_account_text("2GU9UV2RG")
        self.assertEqual(fallback, result)
        obj._get.assert_any_call("skins_catalog", unittest.mock.ANY)
        self.assertEqual(progression.call_count, 2)

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
        from app import _is_manual_deterministic_command
        for command in (
            "Report", "Classifiche", "Classifica oggi", "Progressione oggi",
            "Guida coefficiente abusivo", "Coefficiente abusivo", "Stats",
            "Skin", "Draft ranked", "registrami #2GU9UV2RG",
            "elenco registrati", "comandi", "quante skin ho",
        ):
            with self.subTest(command=command):
                self.assertTrue(_is_manual_deterministic_command(command))
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
