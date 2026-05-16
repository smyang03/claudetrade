from __future__ import annotations

import unittest
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from runtime import price_csv_health


KST = ZoneInfo("Asia/Seoul")


class PriceCsvFreshnessTests(unittest.TestCase):
    def _write_price_csv(self, root: Path, market: str, ticker: str, dates: list[str]) -> None:
        market_key = market.lower()
        path = root / "data" / "price" / market_key / f"{market_key}_{ticker}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for idx, day in enumerate(dates):
            price = 100.0 + idx
            rows.append(
                {
                    "date": day,
                    "open": price,
                    "high": price + 1,
                    "low": price - 1,
                    "close": price,
                    "volume": 1000,
                }
            )
        pd.DataFrame(rows).to_csv(path, index=False)

    def test_weekend_uses_latest_completed_session_and_keeps_csv_fresh(self) -> None:
        status = price_csv_health.price_csv_freshness_status(
            "KR",
            "2026-05-15",
            now=datetime(2026, 5, 17, 10, 0, tzinfo=KST),
        )

        self.assertTrue(status["fresh"])
        self.assertEqual(status["latest_completed"], "2026-05-15")
        self.assertEqual(status["missing_sessions"], 0)

    def test_kr_before_close_uses_previous_completed_daily_bar(self) -> None:
        last, source = price_csv_health.expected_last_trading_day(
            "KR",
            pd.Timestamp("2026-05-13"),
            now=datetime(2026, 5, 13, 8, 30, tzinfo=KST),
        )

        self.assertEqual(last, pd.Timestamp("2026-05-12"))
        self.assertIn(source, {"exchange_calendars", "weekday_fallback"})

    def test_exchange_calendar_three_missing_sessions_is_stale(self) -> None:
        with patch.object(
            price_csv_health,
            "expected_last_trading_day",
            return_value=(pd.Timestamp("2026-05-15"), "exchange_calendars"),
        ), patch.object(
            price_csv_health,
            "expected_trading_days",
            return_value=(
                [pd.Timestamp("2026-05-13"), pd.Timestamp("2026-05-14"), pd.Timestamp("2026-05-15")],
                "exchange_calendars",
            ),
        ):
            status = price_csv_health.price_csv_freshness_status(
                "KR",
                "2026-05-12",
                now=datetime(2026, 5, 17, 10, 0, tzinfo=KST),
            )

        self.assertFalse(status["fresh"])
        self.assertEqual(status["missing_sessions"], 3)
        self.assertEqual(status["threshold"], 2)
        self.assertEqual(status["calendar_source"], "exchange_calendars")

    def test_kr_weekday_fallback_allows_long_holiday_gap(self) -> None:
        with patch.object(
            price_csv_health,
            "expected_last_trading_day",
            return_value=(pd.Timestamp("2026-10-02"), "weekday_fallback"),
        ), patch.object(
            price_csv_health,
            "expected_trading_days",
            return_value=([pd.Timestamp(f"2026-09-{day:02d}") for day in range(24, 29)], "weekday_fallback"),
        ):
            status = price_csv_health.price_csv_freshness_status(
                "KR",
                "2026-09-23",
                now=datetime(2026, 10, 4, 10, 0, tzinfo=KST),
            )

        self.assertTrue(status["fresh"])
        self.assertEqual(status["missing_sessions"], 5)
        self.assertEqual(status["threshold"], 7)

    def test_us_weekday_fallback_flags_four_missing_sessions(self) -> None:
        with patch.object(
            price_csv_health,
            "expected_last_trading_day",
            return_value=(pd.Timestamp("2026-05-15"), "weekday_fallback"),
        ), patch.object(
            price_csv_health,
            "expected_trading_days",
            return_value=(
                [pd.Timestamp("2026-05-12"), pd.Timestamp("2026-05-13"), pd.Timestamp("2026-05-14"), pd.Timestamp("2026-05-15")],
                "weekday_fallback",
            ),
        ):
            status = price_csv_health.price_csv_freshness_status(
                "US",
                "2026-05-11",
                now=datetime(2026, 5, 17, 10, 0, tzinfo=KST),
            )

        self.assertFalse(status["fresh"])
        self.assertEqual(status["missing_sessions"], 4)
        self.assertEqual(status["threshold"], 3)

    def test_health_summary_uses_same_freshness_threshold_as_bot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_price_csv(root, "KR", "005930", ["2026-05-13", "2026-05-14"])

            with patch.object(
                price_csv_health,
                "expected_last_trading_day",
                return_value=(pd.Timestamp("2026-05-15"), "exchange_calendars"),
            ), patch.object(
                price_csv_health,
                "expected_trading_days",
                return_value=([pd.Timestamp("2026-05-15")], "exchange_calendars"),
            ):
                summary = price_csv_health.price_csv_health_summary(root, "KR")

        self.assertEqual(summary["counts"]["ok"], 1)
        self.assertEqual(summary["counts"]["stale_csv"], 0)
        self.assertEqual(summary["samples"]["ok"][0]["freshness"]["missing_sessions"], 1)
        self.assertEqual(summary["samples"]["ok"][0]["freshness"]["threshold"], 2)

    def test_health_summary_marks_stale_when_freshness_threshold_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_price_csv(root, "KR", "005930", ["2026-05-11", "2026-05-12"])

            with patch.object(
                price_csv_health,
                "expected_last_trading_day",
                return_value=(pd.Timestamp("2026-05-15"), "exchange_calendars"),
            ), patch.object(
                price_csv_health,
                "expected_trading_days",
                return_value=(
                    [pd.Timestamp("2026-05-13"), pd.Timestamp("2026-05-14"), pd.Timestamp("2026-05-15")],
                    "exchange_calendars",
                ),
            ):
                summary = price_csv_health.price_csv_health_summary(root, "KR")

        self.assertEqual(summary["counts"]["ok"], 0)
        self.assertEqual(summary["counts"]["stale_csv"], 1)
        stale = summary["samples"]["stale_csv"][0]
        self.assertIn("missing_sessions=3", stale["detail"])
        self.assertEqual(stale["freshness"]["threshold"], 2)


if __name__ == "__main__":
    unittest.main()
