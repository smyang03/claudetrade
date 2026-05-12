from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.adaptive_live_condition_accuracy_report import build_report


class AdaptiveLiveConditionAccuracyReportTests(unittest.TestCase):
    def test_build_report_groups_shadow_suggestions_with_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE audit_candidate_rows (
                        candidate_key TEXT PRIMARY KEY,
                        market TEXT,
                        session_date TEXT,
                        ticker TEXT,
                        claude_action TEXT,
                        payload_json TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE audit_candidate_outcomes (
                        candidate_key TEXT,
                        horizon_min INTEGER,
                        return_pct REAL
                    )
                    """
                )
                payload = {
                    "adaptive_live_condition": {
                        "decisions": {
                            "005930": {
                                "suggested_claude_action": "PROBE_READY",
                                "suggested_size_intent": "small",
                            }
                        }
                    }
                }
                conn.execute(
                    """
                    INSERT INTO audit_candidate_rows
                    (candidate_key, market, session_date, ticker, claude_action, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("KR:2026-05-12:005930", "KR", "2026-05-12", "005930", "PROBE_READY", json.dumps(payload)),
                )
                conn.execute(
                    """
                    INSERT INTO audit_candidate_outcomes
                    (candidate_key, horizon_min, return_pct)
                    VALUES (?, ?, ?)
                    """,
                    ("KR:2026-05-12:005930", 30, 1.25),
                )
                conn.commit()
            finally:
                conn.close()

            report = build_report(db_path=db_path, session_date="2026-05-12", market="KR")

        self.assertEqual(report["matched_suggestions"], 1)
        bucket = report["by_suggestion"]["PROBE_READY"]
        self.assertEqual(bucket["count"], 1)
        self.assertEqual(bucket["ret30_avg"], 1.25)
        self.assertEqual(bucket["claude_agreement_ratio"], 1.0)


if __name__ == "__main__":
    unittest.main()
