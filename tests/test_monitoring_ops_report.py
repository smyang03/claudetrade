from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.monitoring_ops_report import build_monitoring_ops_report


class MonitoringOpsReportTests(unittest.TestCase):
    def test_report_is_read_only_and_surfaces_learning_and_pead_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learning_db = root / "decisions.db"
            conn = sqlite3.connect(learning_db)
            try:
                conn.execute(
                    """
                    CREATE TABLE v2_canonical_performance (
                        market TEXT,
                        runtime_mode TEXT,
                        quality_grade TEXT,
                        quality_reasons_json TEXT,
                        learning_allowed INTEGER,
                        synced_at TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO v2_canonical_performance
                    VALUES ('KR', 'live', 'blocked', '["ORDER_UNKNOWN_UNRESOLVED"]', 0, '2026-05-28T00:00:00')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO v2_canonical_performance
                    VALUES ('KR', 'live', 'clean', '[]', 1, '2026-05-28T01:00:00')
                    """
                )
                conn.commit()
            finally:
                conn.close()

            pead_state = root / "pead_shadow_state.json"
            pead_state.write_text(
                json.dumps(
                    {
                        "trading_days_observed": 5,
                        "prompt_surprise_enabled": False,
                        "manual_review_checklist": {"null_rate_reviewed": True},
                    }
                ),
                encoding="utf-8",
            )
            pead_logs = root / "pead"
            pead_logs.mkdir()
            (pead_logs / "20260528_shadow.jsonl").write_text(
                json.dumps(
                    {
                        "market": "KR",
                        "ticker": "005930",
                        "session_date": "2026-05-28",
                        "surprise_sign": "positive",
                        "prompt_applied": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            hold_logs = root / "hold_advisor"
            hold_logs.mkdir()
            (hold_logs / "decisions_20260528.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "ts": "2026-05-28T09:00:00+09:00",
                                "market": "KR",
                                "ticker": "005930",
                                "decision_stage": "INTRADAY_REVIEW",
                                "decision": "HOLD",
                                "reason": "same_position",
                                "duration_ms": 1000,
                            }
                        ),
                        json.dumps(
                            {
                                "ts": "2026-05-28T09:05:00+09:00",
                                "market": "KR",
                                "ticker": "005930",
                                "decision_stage": "INTRADAY_REVIEW",
                                "decision": "HOLD",
                                "reason": "same_position",
                                "duration_ms": 900,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            report_dir = root / "reports"

            payload = build_monitoring_ops_report(
                candidate_db=root / "missing_candidate_audit.db",
                learning_db=learning_db,
                mode="live",
                session_date="2026-05-28",
                market="KR",
                pead_state=pead_state,
                pead_log_dir=pead_logs,
                hold_decision_dir=hold_logs,
                write_report=True,
                report_dir=report_dir,
            )

            self.assertFalse(payload["candidate_analysis"]["available"])
            self.assertEqual(payload["v2_learning_gate"]["learning_allowed"], 1)
            self.assertEqual(payload["v2_learning_gate"]["learning_excluded"], 1)
            self.assertEqual(payload["v2_learning_gate"]["top_quality_reasons"]["ORDER_UNKNOWN_UNRESOLVED"], 1)
            self.assertFalse(payload["v2_learning_gate"]["policy_change_allowed"])
            self.assertEqual(payload["pead_manual_review"]["promotion_gate_state"], "blocked_manual_review")
            self.assertEqual(len(payload["pead_manual_review"]["prompt_leak_candidates"]), 1)
            self.assertEqual(payload["hold_advisor_cache_shadow"]["requests"], 2)
            self.assertEqual(payload["hold_advisor_cache_shadow"]["would_hit"], 1)
            self.assertFalse(payload["hold_advisor_cache_shadow"]["cache_enable_allowed"])
            self.assertFalse(payload["gate_summary"]["pead_policy_change_allowed"])
            self.assertTrue(Path(payload["report_paths"]["json"]).exists())
            self.assertTrue(Path(payload["report_paths"]["md"]).exists())


if __name__ == "__main__":
    unittest.main()
