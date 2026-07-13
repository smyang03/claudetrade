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

        attribution = report["candidate_attribution"]
        self.assertEqual(attribution["candidate_count"], 1)
        self.assertEqual(attribution["first_failure_baseline"], {"expired": 1})
        self.assertEqual(
            attribution["first_eval_delay_buckets"],
            {"0_2min": 0, "over_2min": 1, "no_evaluation": 0},
        )
        candidate = attribution["candidates"][0]
        self.assertEqual(candidate["ticker"], "005930")
        self.assertEqual(candidate["selected_to_first_eval_min"], 70.0)
        self.assertEqual(candidate["first_failure_reason"], "expired")
        self.assertEqual(candidate["probe_rows_total"], 1)
        self.assertEqual(candidate["expired_probe_rows"], 1)

    def test_candidate_attribution_first_failure_and_no_evaluation(self) -> None:
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
                # 후보1: 첫 평가가 1분 뒤 orp_not_formed → not_formed / 0_2min
                conn.execute(
                    """
                    INSERT INTO ticker_selection_log
                    VALUES (1, '2026-06-05', 'US', 'NVDA', 'session_open',
                            '2026-06-05T22:35:00+09:00', '2026-06-05T22:35:00+09:00',
                            '', 0, 0, 1,
                            'opening_range_pullback', 'opening_range_pullback', '', 'live')
                    """
                )
                # 후보2: 선정 이후 ORP 평가 자체가 없음 → no_evaluation
                conn.execute(
                    """
                    INSERT INTO ticker_selection_log
                    VALUES (2, '2026-06-05', 'US', 'TSLA', 'session_open',
                            '2026-06-05T23:50:00+09:00', '2026-06-05T23:50:00+09:00',
                            '', 0, 0, 1,
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
                    VALUES (1, '2026-06-05T22:36:00+09:00', '2026-06-05T22:36:00+09:00',
                            '2026-06-05', 'US', 'NVDA', 'opening_range_pullback',
                            5.0, 0, 0, 'orp_not_formed', '', 'live')
                    """
                )
                # 같은 후보의 반복 probe(만료) — 첫 평가 사유는 not_formed로 유지돼야 함
                conn.execute(
                    """
                    INSERT INTO intraday_strategy_log
                    VALUES (2, '2026-06-05T23:59:00+09:00', '2026-06-05T23:59:00+09:00',
                            '2026-06-05', 'US', 'NVDA', 'opening_range_pullback',
                            75.0, 0, 0, 'orp_entry_window_expired', '', 'live')
                    """
                )
                conn.commit()
            finally:
                conn.close()

            report = build_report(
                selection_db=selection_db,
                intraday_db=intraday_db,
                session_date="2026-06-05",
                market="US",
                runtime_mode="live",
            )

        attribution = report["candidate_attribution"]
        self.assertEqual(attribution["candidate_count"], 2)
        self.assertEqual(
            attribution["first_failure_baseline"],
            {"no_evaluation": 1, "not_formed": 1},
        )
        self.assertEqual(
            attribution["first_eval_delay_buckets"],
            {"0_2min": 1, "over_2min": 0, "no_evaluation": 1},
        )
        # TSLA 선정 시각이 마지막 ORP probe(23:59)보다 앞 → ticker_never_evaluated
        self.assertEqual(
            attribution["no_evaluation_causes"], {"ticker_never_evaluated": 1}
        )
        by_ticker = {c["ticker"]: c for c in attribution["candidates"]}
        self.assertEqual(by_ticker["NVDA"]["first_failure_reason"], "not_formed")
        self.assertEqual(by_ticker["NVDA"]["expired_probe_rows"], 1)
        self.assertEqual(by_ticker["TSLA"]["first_failure_reason"], "no_evaluation")


if __name__ == "__main__":
    unittest.main()
