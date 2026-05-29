from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from bot.entry_timing import EntryTimingTracker, build_entry_timing_summary
from strategy.opening_range_pullback import diagnostics as orp_diagnostics


class _Clock:
    def __init__(self):
        self.current = datetime(2026, 4, 28, 9, 5, tzinfo=timezone(timedelta(hours=9)))

    def __call__(self):
        return self.current

    def advance(self, minutes=0, seconds=0):
        self.current = self.current + timedelta(minutes=minutes, seconds=seconds)


class EntryTimingTests(unittest.TestCase):
    def test_tracker_records_candidate_signal_order_and_fill_delays(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = _Clock()
            tracker = EntryTimingTracker(runtime_mode="live", log_dir=Path(tmp), now_func=clock)

            tracker.mark_candidates(
                "KR",
                ["001510"],
                source="session_open",
                session_date="2026-04-28",
                price_by_ticker={"001510": 5000},
            )
            clock.advance(minutes=2)
            tracker.mark_signal_check("KR", "001510", session_date="2026-04-28", price=5100)
            clock.advance(minutes=2)
            tracker.mark_signal_fired(
                "KR",
                "001510",
                session_date="2026-04-28",
                price=5150,
                strategy="opening_range_pullback",
                reason="OR pullback",
            )
            clock.advance(minutes=2)
            order_snapshot = tracker.mark_order_sent(
                "KR",
                "001510",
                session_date="2026-04-28",
                price=5200,
                order_no="1001",
                strategy="opening_range_pullback",
                qty=10,
                intraday_high=5400,
            )
            clock.advance(minutes=2)
            fill_snapshot = tracker.mark_filled(
                "KR",
                "001510",
                session_date="2026-04-28",
                fill_price=5210,
                order_no="1001",
                qty=10,
            )

            self.assertEqual(order_snapshot["candidate_source"], "session_open")
            self.assertEqual(order_snapshot["signal_check_count"], 1)
            self.assertEqual(order_snapshot["candidate_to_order_delay_min"], 6.0)
            self.assertEqual(order_snapshot["signal_to_order_delay_min"], 2.0)
            self.assertAlmostEqual(order_snapshot["price_change_candidate_to_order_pct"], 4.0)
            self.assertAlmostEqual(order_snapshot["entry_vs_intraday_high_pct"], -3.7037, places=3)
            self.assertEqual(fill_snapshot["order_to_fill_delay_sec"], 120.0)

            summary = build_entry_timing_summary(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-28",
                log_dir=Path(tmp),
            )
            self.assertFalse(summary["missing"])
            self.assertEqual(summary["events"]["candidate_detected"], 1)
            self.assertEqual(summary["events"]["order_sent"], 1)
            self.assertEqual(summary["events"]["filled"], 1)
            self.assertEqual(summary["averages"]["candidate_to_order_delay_min"], 6.0)
            self.assertEqual(summary["recent"][-1]["event"], "filled")

    def test_summary_handles_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = build_entry_timing_summary(
                market="US",
                runtime_mode="live",
                session_date="2026-04-28",
                log_dir=Path(tmp),
            )
            self.assertTrue(summary["missing"])
            self.assertEqual(summary["row_count"], 0)
            self.assertEqual(summary["recent"], [])

    def test_orp_diagnostics_explain_not_formed_and_expired(self):
        df = pd.DataFrame(
            [
                {"timestamp": f"2026-05-28 22:{30 + idx:02d}", "close": 100 + idx, "volume": 1000, "vol_avg20": 900}
                for idx in range(20)
            ]
        )

        not_formed = orp_diagnostics(
            df,
            10,
            {"or_formed": False, "session_elapsed_min": 30, "or_minutes": 15, "entry_window_min": 60},
        )
        expired = orp_diagnostics(
            df,
            19,
            {
                "or_formed": True,
                "session_elapsed_min": 90,
                "or_minutes": 15,
                "entry_window_min": 60,
                "or_high": 105,
                "or_low": 100,
            },
        )

        self.assertEqual(not_formed["reason"], "orp_not_formed")
        self.assertIn(not_formed["reason_detail"], {"opening_window_rows_missing", "session_time_mismatch_or_or_state_missing"})
        self.assertEqual(not_formed["row_count"], 20)
        self.assertEqual(expired["reason"], "orp_entry_window_expired")
        self.assertEqual(expired["entry_window_expires_at_min"], 75)
        self.assertTrue(expired["volume_column_present"])


if __name__ == "__main__":
    unittest.main()
