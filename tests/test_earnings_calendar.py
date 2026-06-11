"""실적 캘린더 — 태그/차단 윈도우/fail-open 검증."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime import earnings_calendar as ec


def _write_cache(root: Path, by_symbol: dict):
    path = root / "data" / "earnings_calendar.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "fetched_at": date.today().isoformat() + "T08:00:00",
        "by_symbol": by_symbol,
    }), encoding="utf-8")


class EarningsCalendarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        ec._MEM_CACHE["loaded_at"] = 0.0
        ec._MEM_CACHE["data"] = None
        self._path_patch = patch.object(
            ec, "_cache_path", return_value=self.root / "data" / "earnings_calendar.json"
        )
        self._path_patch.start()

    def tearDown(self):
        self._path_patch.stop()
        ec._MEM_CACHE["loaded_at"] = 0.0
        ec._MEM_CACHE["data"] = None
        self.tmp.cleanup()

    def test_block_window_d_minus1_to_d_plus1(self):
        today = date.today()
        _write_cache(self.root, {
            "TOMORROW": {"date": (today + timedelta(days=1)).isoformat(), "hour": "amc"},
            "YESTERDAY": {"date": (today - timedelta(days=1)).isoformat(), "hour": "bmo"},
            "NEXTWEEK": {"date": (today + timedelta(days=6)).isoformat(), "hour": "amc"},
        })
        self.assertTrue(ec.earnings_window_block("TOMORROW", "US")["blocked"])   # D-1
        self.assertTrue(ec.earnings_window_block("YESTERDAY", "US")["blocked"])  # D+1 (ORCL 케이스)
        self.assertFalse(ec.earnings_window_block("NEXTWEEK", "US")["blocked"])  # D-6 허용

    def test_missing_data_fails_open(self):
        _write_cache(self.root, {})
        self.assertFalse(ec.earnings_window_block("UNKNOWN", "US")["blocked"])
        self.assertEqual(ec.earnings_tag("UNKNOWN", "US"), "")

    def test_kr_market_ignored(self):
        today = date.today()
        _write_cache(self.root, {"005930": {"date": today.isoformat(), "hour": "amc"}})
        self.assertIsNone(ec.earnings_info("005930", "KR"))
        self.assertFalse(ec.earnings_window_block("005930", "KR")["blocked"])

    def test_tag_format_and_range(self):
        today = date.today()
        _write_cache(self.root, {
            "DM1": {"date": (today + timedelta(days=1)).isoformat(), "hour": "amc"},
            "D0": {"date": today.isoformat(), "hour": "bmo"},
            "FAR": {"date": (today + timedelta(days=10)).isoformat(), "hour": ""},
        })
        self.assertEqual(ec.earnings_tag("DM1", "US"), "earn=D-1(amc)")
        self.assertEqual(ec.earnings_tag("D0", "US"), "earn=D0(bmo)")
        self.assertEqual(ec.earnings_tag("FAR", "US"), "")  # ±3일 밖

    def test_disabled_flag(self):
        today = date.today()
        _write_cache(self.root, {"AAA": {"date": today.isoformat(), "hour": "amc"}})
        with patch.dict("os.environ", {"EARNINGS_WINDOW_BLOCK_ENABLED": "false"}):
            self.assertFalse(ec.earnings_window_block("AAA", "US")["blocked"])
        with patch.dict("os.environ", {"EARNINGS_CALENDAR_ENABLED": "false"}):
            self.assertIsNone(ec.earnings_info("AAA", "US"))


if __name__ == "__main__":
    unittest.main()
