from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from phase1_trainer import price_collector


class PriceCollectorIncrementalTests(unittest.TestCase):
    def test_has_weekday_between_detects_weekend_only_gap(self) -> None:
        self.assertFalse(
            price_collector._has_weekday_between(
                pd.Timestamp("2026-05-09"),
                pd.Timestamp("2026-05-10"),
            )
        )
        self.assertTrue(
            price_collector._has_weekday_between(
                pd.Timestamp("2026-05-09"),
                pd.Timestamp("2026-05-11"),
            )
        )

    def test_normalize_date_window_drops_stale_rows(self) -> None:
        rows = pd.DataFrame(
            [
                {"date": "2026-05-08", "close": 100.0},
                {"date": "2026-05-11", "close": 101.0},
            ]
        )

        filtered = price_collector._normalize_date_window(
            rows,
            pd.Timestamp("2026-05-09"),
            pd.Timestamp("2026-05-11"),
        )

        self.assertEqual(filtered["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-05-11"])

    def test_save_warns_instead_of_raising_when_no_date_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.csv"

            price_collector._save(
                path,
                pd.DataFrame(),
                pd.Timestamp("2026-05-01"),
                pd.Timestamp("2026-05-11"),
                "277990(277990)",
            )

            self.assertFalse(path.exists())

    def test_kr_incremental_skips_weekend_only_forward_gap_without_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            price_dir = root / "price"
            kr_dir = price_dir / "kr"
            kr_dir.mkdir(parents=True)
            (kr_dir / "kr_417200.csv").write_text(
                "date,open,high,low,close,volume\n"
                "2026-05-08,100,101,99,100,1000\n",
                encoding="utf-8",
            )
            kis = Mock(return_value=pd.DataFrame())
            yf = Mock(return_value=pd.DataFrame())

            with patch.object(price_collector, "PRICE_DIR", price_dir), \
                 patch.object(price_collector, "KR_TICKERS", {"417200": "417200"}), \
                 patch.object(price_collector, "fetch_kr_daily", kis), \
                 patch.object(price_collector, "fetch_kr_daily_yfinance", yf), \
                 patch.object(price_collector.time, "sleep", lambda *_: None):
                price_collector.collect_kr_incremental(
                    pd.Timestamp("2026-05-08"),
                    pd.Timestamp("2026-05-10"),
                )

            kis.assert_not_called()
            yf.assert_not_called()

    def test_kr_incremental_saves_backfill_before_weekend_forward_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            price_dir = root / "price"
            kr_dir = price_dir / "kr"
            kr_dir.mkdir(parents=True)
            path = kr_dir / "kr_417200.csv"
            path.write_text(
                "date,open,high,low,close,volume\n"
                "2026-05-08,100,101,99,100,1000\n",
                encoding="utf-8",
            )
            backfill = pd.DataFrame(
                [
                    {
                        "date": pd.Timestamp("2026-05-07"),
                        "open": 90,
                        "high": 91,
                        "low": 89,
                        "close": 90,
                        "volume": 900,
                    }
                ]
            )
            kis = Mock(return_value=backfill)
            yf = Mock(return_value=pd.DataFrame())

            with patch.object(price_collector, "PRICE_DIR", price_dir), \
                 patch.object(price_collector, "KR_TICKERS", {"417200": "417200"}), \
                 patch.object(price_collector, "fetch_kr_daily", kis), \
                 patch.object(price_collector, "fetch_kr_daily_yfinance", yf), \
                 patch.object(price_collector.time, "sleep", lambda *_: None):
                price_collector.collect_kr_incremental(
                    pd.Timestamp("2026-05-07"),
                    pd.Timestamp("2026-05-10"),
                )

            saved = pd.read_csv(path)
            self.assertEqual(saved["date"].astype(str).tolist(), ["2026-05-07", "2026-05-08"])
            self.assertEqual(kis.call_count, 1)
            yf.assert_not_called()

    def test_us_incremental_continues_forward_refresh_after_gap_fill(self) -> None:
        class FakeDate:
            @staticmethod
            def today():
                return date(2026, 5, 20)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            price_dir = root / "price"
            us_dir = price_dir / "us"
            us_dir.mkdir(parents=True)
            path = us_dir / "us_AAPL.csv"
            path.write_text(
                "date,open,high,low,close,volume\n"
                "2026-05-01,100,101,99,100,1000\n"
                "2026-05-06,105,106,104,105,1000\n",
                encoding="utf-8",
            )
            gap_df = pd.DataFrame(
                [
                    {
                        "date": pd.Timestamp("2026-05-08"),
                        "open": 108,
                        "high": 109,
                        "low": 107,
                        "close": 108,
                        "volume": 1000,
                    }
                ]
            )
            forward_df = pd.DataFrame(
                [
                    {
                        "date": pd.Timestamp("2026-05-20"),
                        "open": 120,
                        "high": 121,
                        "low": 119,
                        "close": 120,
                        "volume": 1000,
                    }
                ]
            )
            fetch = Mock(side_effect=[gap_df, forward_df])
            gap_audit = {"gaps": [pd.Timestamp("2026-05-08")], "duplicate_dates": 0, "calendar_source": "test"}

            with patch.object(price_collector, "PRICE_DIR", price_dir), \
                 patch.object(price_collector, "US_TICKERS", {"AAPL": "Apple"}), \
                 patch.object(price_collector, "fetch_us_daily_yfinance", fetch), \
                 patch.object(price_collector, "_audit_csv_date_gaps", return_value=gap_audit), \
                 patch.object(price_collector, "_gap_ranges", return_value=[(pd.Timestamp("2026-05-08"), pd.Timestamp("2026-05-08"))]), \
                 patch.object(price_collector, "date", FakeDate), \
                 patch.object(price_collector.time, "sleep", lambda *_: None):
                price_collector.collect_us_incremental(
                    pd.Timestamp("2026-05-01"),
                    pd.Timestamp("2026-05-20"),
                )

            self.assertEqual(fetch.call_count, 2)
            saved = pd.read_csv(path)
            self.assertIn("2026-05-08", saved["date"].astype(str).tolist())
            self.assertIn("2026-05-20", saved["date"].astype(str).tolist())

    def test_csv_gap_audit_uses_trading_calendar_and_counts_duplicates(self) -> None:
        rows = pd.DataFrame(
            [
                {"date": "2026-05-07", "close": 100.0},
                {"date": "2026-05-07", "close": 101.0},
                {"date": "2026-05-11", "close": 102.0},
            ]
        )
        expected = [
            pd.Timestamp("2026-05-07"),
            pd.Timestamp("2026-05-08"),
            pd.Timestamp("2026-05-11"),
        ]

        with patch.object(price_collector, "_expected_trading_days", return_value=(expected, "test_calendar")):
            audit = price_collector._audit_csv_date_gaps(rows, "KR")

        self.assertEqual(audit["duplicate_dates"], 1)
        self.assertEqual([day.strftime("%Y-%m-%d") for day in audit["gaps"]], ["2026-05-08"])
        self.assertEqual(audit["calendar_source"], "test_calendar")

    def test_save_removes_duplicate_dates_and_sorts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.csv"
            rows = pd.DataFrame(
                [
                    {"date": "2026-05-11", "open": 101, "high": 102, "low": 100, "close": 101, "volume": 10},
                    {"date": "2026-05-08", "open": 99, "high": 100, "low": 98, "close": 99, "volume": 9},
                    {"date": "2026-05-08", "open": 98, "high": 99, "low": 97, "close": 98, "volume": 8},
                ]
            )

            price_collector._save(
                path,
                rows,
                pd.Timestamp("2026-05-08"),
                pd.Timestamp("2026-05-11"),
                "TEST",
            )

            saved = pd.read_csv(path)
            self.assertEqual(saved["date"].astype(str).tolist(), ["2026-05-08", "2026-05-11"])


if __name__ == "__main__":
    unittest.main()
