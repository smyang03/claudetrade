from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import live_preflight


def _base_config(effective: dict[str, str]) -> dict:
    defaults = {
        "ENABLED_MARKETS": "KR,US",
        "PATHB_ENABLED": "true",
        "PATHB_MAX_POSITIONS": "1",
        "PATHB_MAX_DAILY_ENTRIES": "1",
        "PATHB_INTRADAY_ONLY": "true",
        "PATHB_EMERGENCY_DISABLE": "false",
        "US_FIXED_ORDER_KRW": "100000",
        "US_MIN_ORDER_KRW": "50000",
        "USD_KRW_RATE": "1300",
        "CLAUDE_SELECTION_MAX_TOKENS": "6000",
        "CLAUDE_SELECTION_RETRY_MAX_TOKENS": "3500",
        "KIS_IS_PAPER_US": "false",
        "KIS_US_CREDENTIAL_FALLBACK_ACCEPTED": "false",
    }
    merged = {**defaults, **effective}
    return {
        "env_path": "E:/code/claudetrade/.missing-test-env",
        "start_config": {},
        "base_env": {},
        "overrides": {},
        "effective": merged,
    }


def _credential_check(effective: dict[str, str]):
    with patch.object(live_preflight, "load_effective_config", return_value=_base_config(effective)):
        checks, _config = live_preflight._config_checks("live", allow_config_conflicts=True)
    return next(check for check in checks if check.name == "kis.us_credentials")


class LivePreflightCredentialModeTests(unittest.TestCase):
    def test_us_credentials_reports_fallback_shared_kr(self) -> None:
        check = _credential_check(
            {
                "KIS_ACCOUNT_NO": "11111111-01",
                "KIS_APP_KEY": "kr-key",
                "KIS_APP_SECRET": "kr-secret",
                "KIS_ACCOUNT_NO_US": "",
                "KIS_APP_KEY_US": "",
                "KIS_APP_SECRET_US": "",
            }
        )

        self.assertEqual(check.status, "WARN")
        self.assertIn("app_key=missing", check.detail)
        self.assertIn("app_secret=missing", check.detail)
        self.assertEqual(check.data["credential_mode"], "fallback_shared_kr")
        self.assertTrue(check.data["fallback_to_kr_allowed"])
        self.assertFalse(check.data["accepted_exception"])
        self.assertTrue(check.data["remediation_required"])

    def test_us_credentials_reports_secret_only_missing(self) -> None:
        check = _credential_check(
            {
                "KIS_ACCOUNT_NO": "11111111-01",
                "KIS_APP_KEY": "kr-key",
                "KIS_APP_SECRET": "kr-secret",
                "KIS_ACCOUNT_NO_US": "22222222-01",
                "KIS_APP_KEY_US": "us-key",
                "KIS_APP_SECRET_US": "",
            }
        )

        self.assertEqual(check.status, "WARN")
        self.assertIn("app_key=present", check.detail)
        self.assertIn("app_secret=missing", check.detail)
        self.assertEqual(check.data["credential_mode"], "fallback_shared_kr")
        self.assertTrue(check.data["remediation_required"])

    def test_us_credentials_can_mark_shared_fallback_as_accepted_exception(self) -> None:
        check = _credential_check(
            {
                "KIS_ACCOUNT_NO": "11111111-01",
                "KIS_APP_KEY": "kr-key",
                "KIS_APP_SECRET": "kr-secret",
                "KIS_ACCOUNT_NO_US": "",
                "KIS_APP_KEY_US": "",
                "KIS_APP_SECRET_US": "",
                "KIS_US_CREDENTIAL_FALLBACK_ACCEPTED": "true",
            }
        )

        self.assertEqual(check.status, "WARN")
        self.assertEqual(check.data["credential_mode"], "fallback_shared_kr")
        self.assertTrue(check.data["accepted_exception"])
        self.assertFalse(check.data["remediation_required"])

    def test_us_credentials_reports_separate_us(self) -> None:
        check = _credential_check(
            {
                "KIS_ACCOUNT_NO": "11111111-01",
                "KIS_APP_KEY": "kr-key",
                "KIS_APP_SECRET": "kr-secret",
                "KIS_ACCOUNT_NO_US": "22222222-01",
                "KIS_APP_KEY_US": "us-key",
                "KIS_APP_SECRET_US": "us-secret",
            }
        )

        self.assertEqual(check.status, "PASS")
        self.assertEqual(check.data["credential_mode"], "separate_us")
        self.assertFalse(check.data["fallback_to_kr_allowed"])
        self.assertFalse(check.data["remediation_required"])

    def test_patha_timing_lifecycle_can_be_explicitly_disabled(self) -> None:
        checks = live_preflight._static_code_checks({"PATHA_TIMING_LIFECYCLE_ENABLED": "false"})
        check = next(item for item in checks if item.name == "code.wait_timing_recorded")

        self.assertEqual(check.status, "PASS")
        self.assertFalse(check.data["remediation_required"])


if __name__ == "__main__":
    unittest.main()
