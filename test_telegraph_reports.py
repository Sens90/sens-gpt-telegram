import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from community_features import CommunityFeatures


class TelegraphReportTests(unittest.IsolatedAsyncioTestCase):
    def make_features(self):
        obj = CommunityFeatures.__new__(CommunityFeatures)
        obj.number_formatter = lambda value: f"{int(value):,}".replace(",", ".")
        return obj

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
            context, 123, "CLASSIFICA PROGRESSIONE CLUB"
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
