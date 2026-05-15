from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from tools.live_preflight import (
    _config_checks,
    _runtime_config_drift_check,
    _runtime_config_drift_payload,
    load_effective_config,
)


class LiveConfigSourceTests(unittest.TestCase):
    def test_live_effective_config_has_no_unapproved_conflicts(self) -> None:
        checks, config = _config_checks("live", allow_config_conflicts=False)
        failing = [item for item in checks if item.status == "FAIL"]

        self.assertEqual(failing, [])
        effective = config["effective"]
        self.assertEqual(effective.get("MAX_ORDER_KRW"), "300000")
        self.assertEqual(effective.get("KR_FIXED_ORDER_KRW"), "300000")
        self.assertEqual(effective.get("KR_MAX_POSITIONS"), "15")
        self.assertEqual(effective.get("US_MAX_POSITIONS"), "10")
        self.assertEqual(effective.get("V2_MAX_DAILY_ENTRIES"), "40")
        self.assertEqual(effective.get("KR_DAILY_ENTRY_CAP"), "40")
        self.assertEqual(effective.get("US_DAILY_ENTRY_CAP"), "40")
        self.assertEqual(effective.get("PATHB_MAX_POSITIONS"), "15")
        self.assertEqual(effective.get("PATHB_MAX_DAILY_ENTRIES"), "40")
        self.assertEqual(effective.get("PATHB_FIXED_ORDER_KRW"), "300000")
        self.assertEqual(effective.get("PATHB_ONE_SHARE_OVER_BUDGET_MAX_KRW"), "700000")
        self.assertEqual(effective.get("PATHB_KR_LIVE_ENABLED"), "false")
        self.assertEqual(effective.get("PATHB_US_LIVE_ENABLED"), "true")
        self.assertEqual(effective.get("KR_REENTRY_COOLDOWN_MINUTES"), "120")
        self.assertEqual(effective.get("US_REENTRY_COOLDOWN_MINUTES"), "90")

    def test_start_config_overrides_are_visible(self) -> None:
        config = load_effective_config("live")

        self.assertTrue(config["start_config_loaded"])
        self.assertIn("env_overrides", config["start_config"])
        self.assertEqual(config["effective"].get("PATHB_MODE"), "min_size_live")

    def test_runtime_config_drift_payload_compares_operational_caps(self) -> None:
        config = {
            "effective": {
                "US_DAILY_ENTRY_CAP": "40",
                "PATHB_MAX_DAILY_ENTRIES": "40",
                "PATHB_US_LIVE_ENABLED": "true",
            }
        }
        snapshot = {
            "effective": {
                "US_DAILY_ENTRY_CAP": "1",
                "PATHB_MAX_DAILY_ENTRIES": "20",
                "PATHB_US_LIVE_ENABLED": "true",
            }
        }

        drift = _runtime_config_drift_payload(config, snapshot)

        self.assertEqual(drift["US_DAILY_ENTRY_CAP"]["file_effective"], "40")
        self.assertEqual(drift["US_DAILY_ENTRY_CAP"]["runtime_snapshot"], "1")
        self.assertEqual(drift["PATHB_MAX_DAILY_ENTRIES"]["runtime_snapshot"], "20")
        self.assertNotIn("PATHB_US_LIVE_ENABLED", drift)

    def test_runtime_config_drift_check_describes_snapshot_not_process_memory(self) -> None:
        config = {"effective": {"US_DAILY_ENTRY_CAP": "40"}}
        snapshot = {
            "written_at": "2026-05-16T00:00:00+09:00",
            "runtime_mode": "live",
            "effective": {"US_DAILY_ENTRY_CAP": "1"},
        }

        with patch(
            "tools.live_preflight._latest_runtime_config_snapshot",
            return_value=(Path("effective_config_live.redacted.json"), snapshot),
        ):
            check = _runtime_config_drift_check(config, "live")

        self.assertEqual(check.status, "WARN")
        self.assertIn("latest runtime config snapshot differs from files", check.detail)
        self.assertNotIn("running process", check.detail)
        self.assertEqual(check.data["drift"]["US_DAILY_ENTRY_CAP"]["runtime_snapshot"], "1")


if __name__ == "__main__":
    unittest.main()
