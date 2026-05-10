from __future__ import annotations

import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
