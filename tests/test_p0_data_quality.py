from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from phase1_trainer import supplement_collector
import phase1_trainer.digest_builder as digest_builder
from phase1_trainer.digest_builder import (
    _clean_data_quality_flags,
    _merge_live_context_with_supp,
    build_breadth_summary,
)


class P0DataQualityTests(unittest.TestCase):
    def test_yfinance_close_never_uses_future_or_latest_period_for_target_date(self) -> None:
        calls = []

        class _Hist:
            def __init__(self, rows):
                self.index = [row[0] for row in rows]
                self._close = [row[1] for row in rows]
                self.empty = not rows

            def __getitem__(self, key):
                if key != "Close":
                    raise AssertionError(key)
                return self._close

        class _Ticker:
            def __init__(self, symbol):
                self.symbol = symbol

            def history(self, **kwargs):
                calls.append(kwargs)
                return _Hist([
                    (supplement_collector.datetime(2026, 5, 4), 999.0),
                    (supplement_collector.datetime(2026, 5, 5), 1000.0),
                ])

        fake_yf = SimpleNamespace(Ticker=_Ticker)

        with patch.dict(sys.modules, {"yfinance": fake_yf}):
            value = supplement_collector._yf_close_for_date("SPY", "2026-05-01")

        self.assertIsNone(value)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("period", calls[0])

    def test_yfinance_close_allows_prior_row_within_target_window(self) -> None:
        class _Hist:
            def __init__(self, rows):
                self.index = [row[0] for row in rows]
                self._close = [row[1] for row in rows]
                self.empty = not rows

            def __getitem__(self, key):
                if key != "Close":
                    raise AssertionError(key)
                return self._close

        class _Ticker:
            def __init__(self, symbol):
                self.symbol = symbol

            def history(self, **kwargs):
                return _Hist([
                    (supplement_collector.datetime(2026, 4, 30), 101.5),
                    (supplement_collector.datetime(2026, 5, 4), 999.0),
                ])

        fake_yf = SimpleNamespace(Ticker=_Ticker)

        with patch.dict(sys.modules, {"yfinance": fake_yf}):
            value = supplement_collector._yf_close_for_date("SPY", "2026-05-01")

        self.assertEqual(value, 101.5)

    def test_us_supplement_writes_nulls_and_quality_flags(self) -> None:
        with TemporaryDirectory() as tmp:
            supp_dir = Path(tmp)
            (supp_dir / "us").mkdir()
            (supp_dir / "kr").mkdir()

            with (
                patch.object(supplement_collector, "SUPP_DIR", supp_dir),
                patch.object(supplement_collector, "fetch_vix_detail", return_value=supplement_collector._metric_result(None, "test")),
                patch.object(supplement_collector, "fetch_dxy_detail", return_value=supplement_collector._metric_result(None, "test")),
                patch.object(supplement_collector.time, "sleep"),
            ):
                supplement_collector.collect_us_supplement("2026-05-02")

            payload = json.loads((supp_dir / "us" / "2026-05-02.json").read_text(encoding="utf-8"))
            self.assertIsNone(payload["vix"])
            self.assertIsNone(payload["dxy"])
            self.assertIsNone(payload["oil_wti"])
            self.assertIn("vix_missing", payload["data_quality_flags"])
            self.assertIn("dxy_missing", payload["data_quality_flags"])
            self.assertIn("oil_wti_missing", payload["data_quality_flags"])
            self.assertEqual(payload["sources"]["vix"], "test")

    def test_kr_supplement_preserves_usd_krw_and_marks_missing_vkospi(self) -> None:
        with TemporaryDirectory() as tmp:
            supp_dir = Path(tmp)
            (supp_dir / "us").mkdir()
            (supp_dir / "kr").mkdir()

            with (
                patch.object(supplement_collector, "SUPP_DIR", supp_dir),
                patch.object(supplement_collector, "_kis_token", side_effect=RuntimeError("no token")),
                patch.object(
                    supplement_collector,
                    "fetch_usd_krw_detail",
                    return_value=supplement_collector._metric_result(1470.25, "test"),
                ),
                patch.object(supplement_collector, "fetch_vkospi_detail", return_value=supplement_collector._metric_result(None, "test")),
                patch.object(supplement_collector.time, "sleep"),
            ):
                supplement_collector.collect_kr_supplement("2026-05-02")

            payload = json.loads((supp_dir / "kr" / "2026-05-02.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["usd_krw"], 1470.25)
            self.assertIsNone(payload["vkospi"])
            self.assertIn("vkospi_missing", payload["data_quality_flags"])
            self.assertNotIn("usd_krw_missing", payload["data_quality_flags"])

    def test_missing_supplement_metrics_do_not_override_live_context(self) -> None:
        merged = _merge_live_context_with_supp(
            {"vix": 19.5, "dxy": 103.2, "usd_krw": 1465.0},
            {"vix": None, "dxy": 0, "usd_krw": 0, "fomc": False},
        )

        self.assertEqual(merged["vix"], 19.5)
        self.assertEqual(merged["dxy"], 103.2)
        self.assertEqual(merged["usd_krw"], 1465.0)

    def test_clean_quality_flags_removes_flags_filled_by_live_context(self) -> None:
        flags = _clean_data_quality_flags(
            ["vix_missing", "dxy_missing", "oil_wti_missing"],
            {"vix": 19.5, "dxy": None, "oil_wti": 77.0},
        )

        self.assertNotIn("vix_missing", flags)
        self.assertIn("dxy_missing", flags)
        self.assertNotIn("oil_wti_missing", flags)

    def test_breadth_summary_is_json_safe_with_numpy_scalars(self) -> None:
        summary = build_breadth_summary(
            "US",
            {
                "AAPL": {
                    "name": "Apple",
                    "change_pct": np.float64(1.25),
                    "rsi": np.float64(55.0),
                    "vol_ratio": np.float64(1.7),
                    "pos_52w": np.int64(96),
                }
            },
            {"vix": None, "dxy": 0},
        )

        encoded = json.dumps(summary)
        self.assertIn("AAPL", encoded)
        self.assertIsInstance(summary["top_positive"][0]["change_pct"], float)

    def test_historical_digest_rebuild_does_not_use_live_macro_fallback(self) -> None:
        with TemporaryDirectory() as tmp:
            digest_dir = Path(tmp)
            supp = {
                "vix": None,
                "dxy": None,
                "oil_wti": None,
                "data_quality_flags": ["vix_missing", "dxy_missing", "oil_wti_missing"],
                "sources": {"vix": "backfill_offline"},
                "fallback_used": {"vix": False},
            }
            live_ctx = {"sp500": {"change_pct": 1.0}, "vix": 19.5, "dxy": 103.2, "oil_wti": 77.0}

            with (
                patch.object(digest_builder, "DIGEST_DIR", digest_dir),
                patch.object(digest_builder, "load_supplement", return_value=supp),
                patch.object(digest_builder, "load_news_day", return_value={}),
                patch.object(digest_builder, "load_prev_result", return_value={}),
                patch.object(digest_builder, "_ticker_map", return_value={}),
                patch.object(digest_builder, "_is_current_session_build", return_value=False),
                patch.object(digest_builder, "fetch_live_context_us", return_value=live_ctx) as live_fetch,
            ):
                digest = digest_builder.build_us_digest("2026-05-01")

        live_fetch.assert_not_called()
        self.assertIsNone(digest["context"]["vix"])
        self.assertIsNone(digest["context"]["dxy"])
        self.assertIn("vix_missing", digest["context"]["data_quality_flags"])
        self.assertIn("dxy_missing", digest["context"]["data_quality_flags"])

    def test_current_session_digest_can_fill_missing_live_macro_context(self) -> None:
        with TemporaryDirectory() as tmp:
            digest_dir = Path(tmp)
            supp = {
                "vix": None,
                "dxy": None,
                "data_quality_flags": ["vix_missing", "dxy_missing"],
                "sources": {"vix": "backfill_offline"},
                "fallback_used": {"vix": False},
            }
            live_ctx = {"sp500": {"change_pct": 1.0}, "vix": 19.5, "dxy": 103.2}

            with (
                patch.object(digest_builder, "DIGEST_DIR", digest_dir),
                patch.object(digest_builder, "load_supplement", return_value=supp),
                patch.object(digest_builder, "load_news_day", return_value={}),
                patch.object(digest_builder, "load_prev_result", return_value={}),
                patch.object(digest_builder, "_ticker_map", return_value={}),
                patch.object(digest_builder, "_is_current_session_build", return_value=True),
                patch.object(digest_builder, "fetch_live_context_us", return_value=live_ctx),
            ):
                digest = digest_builder.build_us_digest("2026-05-01")

        self.assertEqual(digest["context"]["vix"], 19.5)
        self.assertEqual(digest["context"]["dxy"], 103.2)
        self.assertNotIn("vix_missing", digest["context"]["data_quality_flags"])
        self.assertNotIn("dxy_missing", digest["context"]["data_quality_flags"])


if __name__ == "__main__":
    unittest.main()
