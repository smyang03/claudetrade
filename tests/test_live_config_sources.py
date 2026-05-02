from __future__ import annotations

import unittest

from tools.live_preflight import _config_checks, load_effective_config


class LiveConfigSourceTests(unittest.TestCase):
    def test_live_effective_config_has_no_unapproved_conflicts(self) -> None:
        checks, config = _config_checks("live", allow_config_conflicts=False)
        failing = [item for item in checks if item.status == "FAIL"]

        self.assertEqual(failing, [])
        effective = config["effective"]
        self.assertEqual(effective.get("KR_MAX_POSITIONS"), "15")
        self.assertEqual(effective.get("US_MAX_POSITIONS"), "10")
        self.assertEqual(effective.get("V2_MAX_DAILY_ENTRIES"), "20")
        self.assertEqual(effective.get("PATHB_MAX_POSITIONS"), "15")
        self.assertEqual(effective.get("PATHB_MAX_DAILY_ENTRIES"), "20")
        self.assertEqual(effective.get("KR_REENTRY_COOLDOWN_MINUTES"), "120")
        self.assertEqual(effective.get("US_REENTRY_COOLDOWN_MINUTES"), "90")

    def test_start_config_overrides_are_visible(self) -> None:
        config = load_effective_config("live")

        self.assertTrue(config["start_config_loaded"])
        self.assertIn("env_overrides", config["start_config"])
        self.assertEqual(config["effective"].get("PATHB_MODE"), "min_size_live")


if __name__ == "__main__":
    unittest.main()
