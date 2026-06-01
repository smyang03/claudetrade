from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.analyze_kr_strategy_redesign import analyze_kr_strategy_redesign


class AnalyzeKrStrategyRedesignTests(unittest.TestCase):
    def _make_ml_db(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                """
                CREATE TABLE v2_learning_performance (
                    v2_decision_id TEXT,
                    market TEXT,
                    runtime_mode TEXT,
                    session_date TEXT,
                    ticker TEXT,
                    status TEXT,
                    route TEXT,
                    path_type TEXT,
                    path_run_id TEXT,
                    strategy TEXT,
                    origin_action TEXT,
                    filled INTEGER,
                    closed INTEGER,
                    pnl_pct REAL,
                    mfe_pct REAL,
                    mae_pct REAL,
                    close_reason TEXT
                )
                """
            )
            rows = [
                (
                    "d1",
                    "KR",
                    "live",
                    "2026-05-07",
                    "078150",
                    "closed",
                    "plan_a",
                    "plan_a",
                    "",
                    "momentum",
                    "BUY_READY",
                    1,
                    1,
                    -2.0,
                    0.0,
                    -2.0,
                    "LOSS_CAP",
                ),
                (
                    "d2",
                    "KR",
                    "live",
                    "2026-05-07",
                    "001780",
                    "closed",
                    "path_b",
                    "claude_price",
                    "path_1",
                    "claude_price",
                    "PULLBACK_WAIT",
                    1,
                    1,
                    7.0,
                    9.0,
                    -0.5,
                    "CLAUDE_PRICE_TARGET",
                ),
                (
                    "d3",
                    "KR",
                    "live",
                    "2026-05-08",
                    "010170",
                    "closed",
                    "path_b",
                    "gap_pullback",
                    "path_2",
                    "gap_pullback",
                    "PULLBACK_WAIT",
                    1,
                    1,
                    -0.8,
                    3.8,
                    -1.2,
                    "TRAILING_STOP",
                ),
                (
                    "d4",
                    "US",
                    "live",
                    "2026-05-08",
                    "AAPL",
                    "closed",
                    "path_b",
                    "claude_price",
                    "path_us",
                    "claude_price",
                    "PULLBACK_WAIT",
                    1,
                    1,
                    1.0,
                    2.0,
                    -0.2,
                    "PRE_CLOSE",
                ),
            ]
            conn.executemany(
                """
                INSERT INTO v2_learning_performance (
                    v2_decision_id, market, runtime_mode, session_date, ticker, status,
                    route, path_type, path_run_id, strategy, origin_action, filled,
                    closed, pnl_pct, mfe_pct, mae_pct, close_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    def _make_event_db(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                """
                CREATE TABLE v2_path_runs (
                    path_run_id TEXT,
                    decision_id TEXT,
                    path_type TEXT,
                    market TEXT,
                    runtime_mode TEXT,
                    session_date TEXT,
                    ticker TEXT,
                    status TEXT,
                    plan_json TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
            rows = [
                (
                    "path_1",
                    "d2",
                    "claude_price",
                    "KR",
                    "live",
                    "2026-05-07",
                    "001780",
                    "CLOSED",
                    json.dumps({"strategy": "claude_price"}),
                    "",
                    "",
                ),
                (
                    "path_2",
                    "d3",
                    "gap_pullback",
                    "KR",
                    "live",
                    "2026-05-08",
                    "010170",
                    "CLOSED",
                    json.dumps({"buy_zone_low": 100.0}),
                    "",
                    "",
                ),
            ]
            conn.executemany(
                """
                INSERT INTO v2_path_runs (
                    path_run_id, decision_id, path_type, market, runtime_mode,
                    session_date, ticker, status, plan_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    def test_read_only_audit_separates_plan_a_pathb_and_metadata_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ml_db = base / "decisions.db"
            event_db = base / "event.db"
            self._make_ml_db(ml_db)
            self._make_event_db(event_db)

            payload = analyze_kr_strategy_redesign(
                market="KR",
                runtime_mode="live",
                ml_db=ml_db,
                event_db=event_db,
                trail_replay=[2.0, 6.0],
            )

        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["performance"]["overall"]["n"], 3)
        self.assertEqual(payload["performance"]["by_strategy"]["momentum"]["loss_cap_count"], 1)
        self.assertEqual(payload["performance"]["by_path_type"]["claude_price"]["wins"], 1)
        self.assertEqual(payload["path_run_metadata"]["missing_strategy_count"], 1)
        self.assertTrue(payload["path_run_metadata"]["live_filter_blocker"])
        self.assertEqual(payload["trailing_replay_approx"]["mfe_to_loss_reversal_count"], 1)


if __name__ == "__main__":
    unittest.main()
