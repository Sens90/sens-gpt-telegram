import os
import unittest
from unittest.mock import Mock, patch


# Importing app reads the two required production variables at module load.
# Tests use inert placeholders so collection never depends on real secrets.
os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

with patch("google.genai.Client", return_value=Mock()):
    import app


class BrawlTrackPriorityTests(unittest.TestCase):
    def test_brawltrack_is_primary_meta_source(self):
        self.assertEqual(app.meta_source_priority("https://brawltrack.app/maps/15000300"), 0)
        self.assertEqual(app.meta_source_priority("https://brawltrack.app/pro/maps/Ring%20Of%20Fire"), 0)
        self.assertGreater(app.meta_source_priority("https://www.brawlplanet.com/it/maps/foo"), 0)

    def test_game_context_stays_separate(self):
        self.assertEqual(app.get_game_context("meta trofei"), "ladder")
        self.assertEqual(app.get_game_context("meta classificata"), "ranked")
        self.assertEqual(app.get_game_context("meta attuale"), "both")


if __name__ == "__main__":
    unittest.main()
