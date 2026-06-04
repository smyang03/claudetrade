from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.sub_screener_uplift_report import build_report, source_bucket


class SubScreenerUpliftReportTests(unittest.TestCase):
    def _db(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "candidate_audit.db"
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                """
                CREATE TABLE audit_candidate_latest_rows (
                    candidate_key TEXT,
                    session_date TEXT, market TEXT, runtime_mode TEXT, ticker TEXT,
                    candidate_source TEXT, source_file TEXT, source_tags_json TEXT, payload_json TEXT,
                    prompt_pool_version TEXT, candidate_pool_role TEXT,
                    discovery_signal_family TEXT, discovery_reason TEXT,
                    final_prompt_included INTEGER, actual_prompt_included INTEGER,
                    claude_trade_ready INTEGER, route_final_action TEXT,
                    buy_signal_count INTEGER, filled_count INTEGER, first_ready_at TEXT,
                    pnl_pct REAL, position_mfe_pct REAL, candidate_quality_score REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE audit_candidate_outcomes (
                    candidate_key TEXT, horizon_min INTEGER, return_pct REAL,
                    max_runup_pct REAL, max_drawdown_pct REAL, status TEXT
                )
                """
            )
            rows = [
                ("key-aapl", "2026-06-04", "US", "live", "AAPL", "", "", "[]", "{}", "", "", "", "", 1, 1, 0, "WATCH", 0, 0, "", None, 1.0, 80.0),
                ("key-spot", "2026-06-04", "US", "live", "SPOT", "sub_screener_triage", "", '["sub_screener"]', "{}", "", "", "", "", 0, 0, 1, "BUY_READY", 1, 1, "2026-06-04T10:00:00", 2.0, 3.0, 90.0),
                ("key-disc", "2026-06-04", "US", "live", "DISC", "", "", "[]", "{}", "", "DISCOVERY", "near_breakout", "overlay", 0, 0, 0, "PULLBACK_WAIT", 0, 0, "", None, 2.0, 85.0),
            ]
            conn.executemany(
                """
                INSERT INTO audit_candidate_latest_rows VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                rows,
            )
            conn.executemany(
                """
                INSERT INTO audit_candidate_outcomes VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("key-aapl", 60, 0.1, 0.5, -0.2, "observed"),
                    ("key-spot", 30, 1.2, 2.0, -0.1, "observed"),
                    ("key-spot", 60, 2.2, 3.5, -0.4, "observed"),
                    ("key-spot", 120, 1.8, 4.0, -0.6, "observed"),
                ],
            )
            conn.commit()
        finally:
            conn.close()
        return path

    def test_source_bucket_identifies_sub_screener(self) -> None:
        self.assertEqual(source_bucket({"candidate_source": "sub_screener_triage"}), "sub_screener")

    def test_build_report_groups_uplift_sources(self) -> None:
        report = build_report(self._db(), session_date="2026-06-04", market="US")

        self.assertEqual(report["total_rows"], 3)
        self.assertEqual(report["buckets"]["base_prompt"]["rows"], 1)
        self.assertEqual(report["buckets"]["sub_screener"]["actionable_rows"], 1)
        self.assertEqual(report["buckets"]["sub_screener"]["horizon_60_rows"], 1)
        self.assertEqual(report["buckets"]["sub_screener"]["horizon_60_avg_max_runup_pct"], 3.5)
        self.assertEqual(report["buckets"]["discovery"]["actionable_rows"], 1)
        self.assertIn("sub_screener", report["delta_vs_base_prompt"])
        self.assertEqual(report["delta_vs_base_prompt"]["sub_screener"]["horizon_60_avg_max_runup_pct"], 3.0)


if __name__ == "__main__":
    unittest.main()
