from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audit.candidate_audit_store import CandidateAuditStore
from tools.monitoring_ops_report import _pead_manual_review_report, build_monitoring_ops_report


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
                        learning_allowed INTEGER,
                        synced_at TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE v2_learning_performance (
                        market TEXT,
                        runtime_mode TEXT,
                        quality_reasons_json TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO v2_canonical_performance
                    VALUES ('KR', 'live', 'blocked', 0, '2026-05-28T00:00:00')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO v2_canonical_performance
                    VALUES ('KR', 'live', 'clean', 1, '2026-05-28T01:00:00')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO v2_learning_performance
                    VALUES ('KR', 'live', '["ORDER_UNKNOWN_UNRESOLVED"]')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO v2_learning_performance
                    VALUES ('KR', 'live', '["DIRTY_BROKER_TRUTH", "ORDER_UNKNOWN_UNRESOLVED"]')
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
            self.assertEqual(payload["v2_learning_gate"]["top_quality_reasons"]["ORDER_UNKNOWN_UNRESOLVED"], 2)
            self.assertEqual(payload["v2_learning_gate"]["top_quality_reasons"]["DIRTY_BROKER_TRUTH"], 1)
            self.assertEqual(payload["v2_learning_gate"]["focus_exclusion_reasons"]["ORDER_UNKNOWN_UNRESOLVED"], 2)
            self.assertFalse(payload["v2_learning_gate"]["policy_change_allowed"])
            self.assertEqual(payload["pead_manual_review"]["promotion_gate_state"], "blocked_manual_review")
            self.assertEqual(len(payload["pead_manual_review"]["prompt_leak_candidates"]), 1)
            self.assertEqual(payload["hold_advisor_cache_shadow"]["requests"], 2)
            self.assertEqual(payload["hold_advisor_cache_shadow"]["would_hit"], 1)
            self.assertFalse(payload["hold_advisor_cache_shadow"]["cache_enable_allowed"])
            self.assertFalse(payload["gate_summary"]["pead_policy_change_allowed"])
            self.assertTrue(Path(payload["report_paths"]["json"]).exists())
            self.assertTrue(Path(payload["report_paths"]["md"]).exists())

    def test_gate_summary_uses_candidate_consistency_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_db = root / "candidate_audit.db"
            candidate_db.touch()
            consistency = {
                "actual_prompt_mismatch_count": 2,
                "invalid_price_count": 1,
                "invalid_price_reason_counts": {"missing_quote": 1},
            }
            with patch(
                "tools.monitoring_ops_report.analyze_candidate_audit",
                return_value={"available": True, "consistency": consistency},
            ):
                payload = build_monitoring_ops_report(
                    candidate_db=candidate_db,
                    learning_db=root / "missing_decisions.db",
                    mode="live",
                    session_date="2026-05-28",
                    market="KR",
                    pead_state=root / "missing_pead_state.json",
                    pead_log_dir=root / "missing_pead_logs",
                    hold_decision_dir=root / "missing_hold_logs",
                )

            self.assertEqual(payload["gate_summary"]["actual_prompt_visibility"], consistency)

    def test_report_surfaces_audit_reason_and_pathb_miss_read_only_views(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_db = root / "candidate_audit.db"
            store = CandidateAuditStore(candidate_db)
            base = {
                "call_id": "call_us",
                "runtime_mode": "live",
                "market": "US",
                "session_date": "2026-06-03",
                "known_at": "2026-06-03T22:30:00+09:00",
            }
            store.upsert_candidate(
                {
                    **base,
                    "ticker": "DISC",
                    "source_file": "trading_bot.selection_meta",
                    "candidate_pool_role": "DISCOVERY",
                    "discovery_signal_family": "near_breakout,momentum_now",
                    "discovery_reason": "core_cap_signal_candidate",
                    "discovery_action_ceiling": "WATCH",
                    "route_runtime_gate_reason": "same_day_reentry_blocked",
                    "payload": {"runtime_gate": {"reason": "same_day_reentry_blocked"}},
                }
            )
            store.upsert_candidate(
                {
                    **base,
                    "ticker": "MISS",
                    "source_file": "trading_bot.selection_meta",
                    "no_submit_reason_code": "NO_SIGNAL",
                    "payload": {"runtime_gate": {"reason": "fallback_reason"}},
                }
            )
            event_db = root / "v2_event_store.db"
            conn = sqlite3.connect(event_db)
            try:
                conn.execute(
                    """
                    CREATE TABLE pathb_miss_quality (
                        market TEXT,
                        runtime_mode TEXT,
                        session_date TEXT,
                        ticker TEXT,
                        cancel_reason TEXT,
                        zone_reentered_after_cancel INTEGER,
                        mfe_30m_pct REAL,
                        mae_30m_pct REAL,
                        followup_status TEXT,
                        quote_sample_count INTEGER
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO pathb_miss_quality
                    VALUES ('US','live','2026-06-03','DISC','INVALID_PRICE',1,1.25,-0.2,'filled',3)
                    """
                )
                conn.commit()
            finally:
                conn.close()

            with patch("tools.monitoring_ops_report.analyze_candidate_audit", return_value={"available": True}):
                payload = build_monitoring_ops_report(
                    candidate_db=candidate_db,
                    learning_db=root / "missing_decisions.db",
                    event_db=event_db,
                    mode="live",
                    session_date="2026-06-03",
                    market="US",
                    pead_state=root / "missing_pead_state.json",
                    pead_log_dir=root / "missing_pead_logs",
                    hold_decision_dir=root / "missing_hold_logs",
                )

        metadata = payload["candidate_metadata_coverage"]
        self.assertEqual(metadata["discovery_metadata_rows"], 1)
        self.assertEqual(metadata["expansion_role_rows"], 0)
        self.assertFalse(metadata["trade_behavior_change_allowed"])
        reasons = payload["candidate_resolved_reason"]
        self.assertEqual(reasons["reason_counts"]["same_day_reentry_blocked"], 1)
        self.assertEqual(reasons["reason_counts"]["NO_SIGNAL"], 1)
        miss = payload["pathb_missed_opportunity"]
        self.assertEqual(miss["rows"], 1)
        self.assertEqual(miss["by_cancel_reason"][0]["cancel_reason"], "INVALID_PRICE")
        self.assertEqual(miss["source_overlay"]["candidate_audit"], "candidate_resolved_reason")
        self.assertEqual(miss["candidate_resolved_reason_counts"]["NO_SIGNAL"], 1)
        self.assertFalse(miss["trade_behavior_change_allowed"])

    def test_pead_gate_requires_complete_manual_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "pead"
            logs.mkdir()
            state = root / "pead_shadow_state.json"
            state.write_text(
                json.dumps(
                    {
                        "trading_days_observed": 5,
                        "prompt_surprise_enabled": True,
                        "manual_review_checklist": {},
                    }
                ),
                encoding="utf-8",
            )
            payload = _pead_manual_review_report(state_path=state, log_dir=logs)
            self.assertEqual(payload["promotion_gate_state"], "blocked_manual_review")

            state.write_text(
                json.dumps(
                    {
                        "trading_days_observed": 5,
                        "prompt_surprise_enabled": True,
                        "manual_review_checklist": {"null_rate_reviewed": True, "prompt_leak_reviewed": False},
                    }
                ),
                encoding="utf-8",
            )
            payload = _pead_manual_review_report(state_path=state, log_dir=logs)
            self.assertEqual(payload["promotion_gate_state"], "blocked_manual_review")

            state.write_text(
                json.dumps(
                    {
                        "trading_days_observed": 5,
                        "prompt_surprise_enabled": True,
                        "manual_review_checklist": {"null_rate_reviewed": True, "prompt_leak_reviewed": True},
                    }
                ),
                encoding="utf-8",
            )
            payload = _pead_manual_review_report(state_path=state, log_dir=logs)
            self.assertEqual(payload["promotion_gate_state"], "pass")


if __name__ == "__main__":
    unittest.main()
