from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.orp_timing_attribution_report import build_report


class OrpTimingAttributionReportTests(unittest.TestCase):
    def test_report_joins_selection_signal_delay_to_orp_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selection_db = root / "ticker_selection_log.db"
            intraday_db = root / "intraday_strategy_log.db"
            conn = sqlite3.connect(selection_db)
            try:
                conn.execute(
                    """
                    CREATE TABLE ticker_selection_log (
                        id INTEGER PRIMARY KEY,
                        date TEXT,
                        market TEXT,
                        ticker TEXT,
                        source_type TEXT,
                        selected_at TEXT,
                        created_at TEXT,
                        signal_at TEXT,
                        signal_fired INTEGER,
                        traded INTEGER,
                        trade_ready INTEGER,
                        strategy_name TEXT,
                        recommended_strategy TEXT,
                        blocked_reason TEXT,
                        bot_mode TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO ticker_selection_log
                    VALUES (1, '2026-06-05', 'KR', '005930', 'session_open',
                            '2026-06-05T09:05:00+09:00', '2026-06-05T09:05:00+09:00',
                            '2026-06-05T10:23:00+09:00', 1, 0, 1,
                            'opening_range_pullback', 'opening_range_pullback', '', 'live')
                    """
                )
                conn.commit()
            finally:
                conn.close()

            conn = sqlite3.connect(intraday_db)
            try:
                conn.execute(
                    """
                    CREATE TABLE intraday_strategy_log (
                        id INTEGER PRIMARY KEY,
                        ts TEXT,
                        created_at TEXT,
                        session_date TEXT,
                        market TEXT,
                        ticker TEXT,
                        strategy_name TEXT,
                        entry_window_elapsed_min REAL,
                        signal_fired INTEGER,
                        traded INTEGER,
                        blocked_reason TEXT,
                        note TEXT,
                        bot_mode TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO intraday_strategy_log
                    VALUES (1, '2026-06-05T10:15:00+09:00', '2026-06-05T10:15:00+09:00',
                            '2026-06-05', 'KR', '005930', 'opening_range_pullback',
                            65.0, 0, 0, 'orp_entry_window_expired', '', 'live')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO intraday_strategy_log
                    VALUES (2, '2026-06-06T10:00:00+09:00', '2026-06-06T10:00:00+09:00',
                            '2026-06-06', 'KR', '005930', 'opening_range_pullback',
                            80.0, 0, 0, 'orp_entry_window_expired', '', 'live')
                    """
                )
                conn.commit()
            finally:
                conn.close()

            report = build_report(
                selection_db=selection_db,
                intraday_db=intraday_db,
                session_date="2026-06-05",
                market="KR",
                runtime_mode="live",
            )

        self.assertEqual(report["entry_window_expires_at_min"], 70.0)
        self.assertEqual(report["selection_rows"], 1)
        self.assertEqual(report["expired_join_rows"], 1)
        self.assertEqual(report["expired_after_selected_count"], 1)
        self.assertEqual(report["selected_to_signal_delay_min"]["p90"], 78.0)
        self.assertEqual(report["samples"][0]["selected_to_orp_expired_delay_min"], 70.0)
        self.assertEqual(report["samples"][0]["expired_entry_window_elapsed_min"], 65.0)
        self.assertEqual(report["interpretation"], "orp_window_timing_directly_relevant")


if __name__ == "__main__":
    unittest.main()
