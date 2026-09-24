import unittest
from unittest.mock import patch

import brawler_catalog_sync


class BrawlerCatalogStartupTests(unittest.TestCase):
    def setUp(self):
        brawler_catalog_sync._STARTUP_SYNC_COUNT = None

    def tearDown(self):
        brawler_catalog_sync._STARTUP_SYNC_COUNT = None

    def test_successful_startup_sync_is_reused(self):
        with patch.object(brawler_catalog_sync, "sync_official_brawlers", return_value=108) as sync:
            self.assertEqual(brawler_catalog_sync.sync_official_brawlers_startup(), 108)
            self.assertEqual(brawler_catalog_sync.sync_official_brawlers_startup(), 108)
        sync.assert_called_once_with(timeout=30)

    def test_failed_startup_sync_remains_retryable(self):
        with patch.object(
            brawler_catalog_sync,
            "sync_official_brawlers",
            side_effect=[RuntimeError("temporary failure"), 108],
        ) as sync:
            with self.assertRaises(RuntimeError):
                brawler_catalog_sync.sync_official_brawlers_startup()
            self.assertEqual(brawler_catalog_sync.sync_official_brawlers_startup(), 108)
        self.assertEqual(sync.call_count, 2)


if __name__ == "__main__":
    unittest.main()
