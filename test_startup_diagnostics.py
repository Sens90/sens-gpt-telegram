import ast
from pathlib import Path
import unittest


APP_PATH = Path(__file__).with_name("app.py")


class StartupDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_netsons_smoke_is_not_run_at_startup(self):
        self.assertNotIn("_startup_netsons_proxy_smoke", self.source)
        self.assertNotIn("NETSONS PROXY SMOKE", self.source)

    def test_required_startup_workers_remain_enabled(self):
        self.assertIn("target=_startup_supercell_proxy_audit", self.source)
        self.assertIn("target=automatic_trophy_monitor", self.source)
        self.assertIn("target=automatic_battle_monitor", self.source)

    def test_battle_monitor_has_dedicated_short_interval(self):
        self.assertIn('BATTLE_TRACKING_INTERVAL_MINUTES", "3"', self.source)
        trophy_monitor = self.source.split("def automatic_trophy_monitor():", 1)[1]
        trophy_monitor = trophy_monitor.split("def automatic_battle_monitor():", 1)[0]
        self.assertNotIn("save_observed_trophy_battles", trophy_monitor)


if __name__ == "__main__":
    unittest.main()
