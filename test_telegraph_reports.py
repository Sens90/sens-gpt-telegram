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
        with self.assertLogs("community_features", level="INFO") as captured:
            url = self.make_features()._publish_telegraph("Report", ["TITOLO", "Dato: 1"])
        self.assertEqual(url, "https://telegra.ph/report-09-23")
        self.assertNotIn("test-token", "\n".join(captured.output))

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

    async def test_sender_uses_inline_report_button(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        payload = self.make_features()._telegraph_reply(
            ["Riepilogo"], "https://telegra.ph/report-09-23", ["Report completo"]
        )
        self.assertTrue(await self.make_features()._send_ranking_message(context, 123, payload))
        kwargs = bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["text"], "Riepilogo")
        self.assertIsNotNone(kwargs["reply_markup"])

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
