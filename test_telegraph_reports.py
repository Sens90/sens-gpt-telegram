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


if __name__ == "__main__":
    unittest.main()
