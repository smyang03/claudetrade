from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from runtime.price_csv_health import load_price_csv_frame
from tools import repair_price_csv_ohlc
from tools import repair_price_csv_quality as repair_tool
from tools.repair_price_csv_quality import repair_price_csv_quality


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _good_rows(start: pd.Timestamp, count: int = 35) -> pd.DataFrame:
    rows = []
    for idx in range(count):
        day = start + pd.Timedelta(days=idx)
        price = 100.0 + idx
        rows.append(
            {
                "date": day.strftime("%Y-%m-%d"),
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price + 0.25,
                "volume": 1000 + idx,
            }
        )
    return pd.DataFrame(rows)


class RepairPriceCsvQualityTests(unittest.TestCase):
    def test_ohlc_wrapper_uses_quality_repair_implementation(self) -> None:
        self.assertIs(repair_price_csv_ohlc.repair_price_csv_ohlc, repair_price_csv_quality)

    def test_dry_run_reports_latest_flat_zero_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "price" / "us" / "us_HOLX.csv"
            _write_csv(
                path,
                [
                    {"date": "2026-05-15", "open": 88.0, "high": 88.0, "low": 88.0, "close": 88.0, "volume": 0},
                ],
            )

            report = repair_price_csv_quality(market="US", tickers=["HOLX"], root=root, apply=False)

        self.assertTrue(report["ok"])
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["results"][0]["ticker"], "HOLX")
        self.assertIn("latest_flat_ohlc_zero_volume", report["results"][0]["issues"])

    def test_apply_replaces_only_after_verified_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "price" / "us" / "us_HOLX.csv"
            _write_csv(
                path,
                [
                    {"date": "2026-05-15", "open": 88.0, "high": 88.0, "low": 88.0, "close": 88.0, "volume": 0},
                ],
            )

            report = repair_price_csv_quality(
                market="US",
                tickers=["HOLX"],
                root=root,
                apply=True,
                fetcher=lambda ticker, start, end: _good_rows(pd.Timestamp(end) - pd.Timedelta(days=34)),
            )
            frame, result = load_price_csv_frame(path, "US", "HOLX")

        self.assertTrue(report["results"][0]["applied"])
        self.assertEqual(result.status, "ok")
        self.assertIsNotNone(frame)
        self.assertEqual(result.rows, 35)
        self.assertFalse(result.latest_flat_ohlc_zero_volume)

    def test_apply_quarantines_bad_tmp_without_replacing_existing_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "price" / "us" / "us_HOLX.csv"
            _write_csv(
                path,
                [
                    {"date": "2026-05-15", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000},
                ],
            )
            bad_fetch = pd.DataFrame(
                [
                    {"date": "2026-05-16", "open": 100.0, "high": 90.0, "low": 89.0, "close": 95.0, "volume": 1000},
                ]
            )

            report = repair_price_csv_quality(
                market="US",
                tickers=["HOLX"],
                root=root,
                apply=True,
                force=True,
                fetcher=lambda ticker, start, end: bad_fetch,
            )
            frame, result = load_price_csv_frame(path, "US", "HOLX")
            bad_files = list((root / "data" / "price" / "_bad").glob("**/*.bad.csv"))

        self.assertFalse(report["results"][0]["applied"])
        self.assertEqual(report["results"][0]["repair"]["error"], "verification_failed")
        self.assertEqual(result.status, "ok")
        self.assertIsNotNone(frame)
        self.assertEqual(result.rows, 1)
        self.assertGreaterEqual(len(bad_files), 1)

    def test_candidate_tickers_include_quality_tickers_beyond_samples(self) -> None:
        summary = {
            "quality_tickers": {
                "ohlc_logic_error": ["ABT"],
                "latest_ohlc_logic_error": ["AA"],
                "latest_flat_ohlc_zero_volume": ["HOLX"],
                "too_few_rows": ["SPEGR"],
            },
            "samples": {},
        }
        with patch.object(repair_tool, "price_csv_health_summary", return_value=summary):
            tickers = repair_tool._candidate_tickers(Path("."), "US")

        self.assertEqual(tickers, ["AA", "ABT", "HOLX", "SPEGR"])

    def test_candidate_tickers_do_not_treat_min_rows_as_quality_problem(self) -> None:
        summary = {
            "quality_tickers": {},
            "samples": {
                "ok": [
                    {
                        "ticker": "AAPL",
                        "quality": {
                            "flat_ohlc_rows": 0,
                            "zero_volume_rows": 0,
                            "flat_ohlc_zero_volume_rows": 0,
                            "latest_flat_ohlc_zero_volume": False,
                            "too_few_rows": False,
                            "min_rows": 30,
                        },
                    }
                ]
            },
        }
        with patch.object(repair_tool, "price_csv_health_summary", return_value=summary):
            tickers = repair_tool._candidate_tickers(Path("."), "US")

        self.assertEqual(tickers, [])


if __name__ == "__main__":
    unittest.main()
