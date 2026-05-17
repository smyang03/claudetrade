from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import live_preflight


class LivePreflightPriceCsvTests(unittest.TestCase):
    def test_active_latest_flat_zero_volume_promotes_integrity_to_fail(self) -> None:
        summary = {
            "market": "US",
            "total": 1,
            "fresh_count": 1,
            "fresh_ratio": 1.0,
            "counts": {
                "ok": 1,
                "missing_csv": 0,
                "malformed_csv": 0,
                "stale_csv": 0,
                "ohlc_logic_error_csv": 0,
                "ohlc_logic_error_rows": 0,
                "latest_ohlc_logic_error_csv": 0,
                "flat_ohlc_zero_volume_csv": 1,
                "flat_ohlc_zero_volume_rows": 1,
                "latest_flat_ohlc_zero_volume_csv": 1,
                "too_few_rows_csv": 0,
            },
            "quality_tickers": {
                "latest_ohlc_logic_error": [],
                "latest_flat_ohlc_zero_volume": ["HOLX"],
                "too_few_rows": [],
            },
            "expected_last_date": "2026-05-15",
            "oldest_last_date": "2026-05-15",
            "newest_last_date": "2026-05-15",
            "samples": {},
        }

        with patch("runtime.price_csv_health.price_csv_health_summary", return_value=summary), patch.object(
            live_preflight,
            "_price_csv_active_universe",
            return_value={"market": "US", "tickers": ["HOLX"], "sources": {}, "selection_date": "2026-05-15"},
        ):
            checks = live_preflight._price_csv_checks()

        integrity = next(item for item in checks if item.name == "data.price_csv_integrity.us")
        self.assertEqual(integrity.status, "FAIL")
        self.assertEqual(integrity.data["active_quality_issues"]["latest_flat_ohlc_zero_volume"], ["HOLX"])
        self.assertIn("active_latest_flat_zero=1", integrity.detail)

    def test_inactive_price_csv_quality_issue_is_warn_not_fail(self) -> None:
        summary = {
            "market": "US",
            "total": 2,
            "fresh_count": 2,
            "fresh_ratio": 1.0,
            "counts": {
                "ok": 1,
                "missing_csv": 0,
                "malformed_csv": 1,
                "stale_csv": 0,
                "ohlc_logic_error_csv": 1,
                "ohlc_logic_error_rows": 1,
                "latest_ohlc_logic_error_csv": 1,
                "flat_ohlc_zero_volume_csv": 0,
                "flat_ohlc_zero_volume_rows": 0,
                "latest_flat_ohlc_zero_volume_csv": 0,
                "too_few_rows_csv": 0,
            },
            "quality_tickers": {
                "latest_ohlc_logic_error": ["ACLX"],
                "latest_flat_ohlc_zero_volume": [],
                "too_few_rows": [],
            },
            "expected_last_date": "2026-05-15",
            "oldest_last_date": "2026-05-15",
            "newest_last_date": "2026-05-15",
            "samples": {
                "malformed_csv": [{"ticker": "ACLX"}],
                "ohlc_logic_error_csv": [{"ticker": "ACLX"}],
            },
        }

        with patch("runtime.price_csv_health.price_csv_health_summary", return_value=summary), patch.object(
            live_preflight,
            "_price_csv_active_universe",
            return_value={"market": "US", "tickers": ["SOFI", "MSFT"], "sources": {}, "selection_date": "2026-05-15"},
        ):
            checks = live_preflight._price_csv_checks()

        integrity = next(item for item in checks if item.name == "data.price_csv_integrity.us")
        self.assertEqual(integrity.status, "WARN")
        self.assertEqual(integrity.data["active_blocking_issues"], {})
        self.assertEqual(integrity.data["active_quality_issues"]["latest_ohlc_logic_error"], [])
        self.assertIn("active_blocking_issues=0", integrity.detail)


if __name__ == "__main__":
    unittest.main()
