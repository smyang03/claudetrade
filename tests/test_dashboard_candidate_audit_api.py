from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audit.candidate_audit_store import CandidateAuditStore, candidate_key


class DashboardCandidateAuditApiTests(unittest.TestCase):
    def test_candidate_audit_summary_includes_outcomes_and_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            session_date = "2026-05-08"
            store.upsert_candidate(
                {
                    "call_id": "call_1",
                    "runtime_mode": "live",
                    "market": "KR",
                    "session_date": session_date,
                    "known_at": "2026-05-08T09:00:00",
                    "ticker": "AAA",
                    "price": 100.0,
                    "screener_seen": True,
                    "recommended_strategy": "gap_pullback",
                    "classification": "filled_loss",
                }
            )
            store.update_execution_by_ticker(
                session_date=session_date,
                market="KR",
                runtime_mode="live",
                ticker="AAA",
                values={
                    "filled_count": 1,
                    "strategy_used": "momentum",
                    "pnl_pct": -1.5,
                    "close_reason": "CLOSED_LOSS_CAP",
                },
            )
            key = candidate_key(session_date=session_date, market="KR", call_id="call_1", ticker="AAA")
            store.upsert_outcome(
                {
                    "candidate_key": key,
                    "horizon_min": 60,
                    "target_at": "2026-05-08T10:00:00",
                    "observed_at": "2026-05-08T10:00:00",
                    "observed_price": 103.0,
                    "return_pct": 3.0,
                    "max_runup_pct": 4.0,
                    "max_drawdown_pct": -1.0,
                    "status": "audit_sparse",
                    "source": "audit_candidate_rows",
                    "payload": {"sample_count": 3},
                }
            )

            import dashboard.dashboard_server as dashboard_server

            with patch.object(dashboard_server, "_candidate_audit_db_path", return_value=db_path):
                response = dashboard_server.app.test_client().get(
                    "/api/candidate-audit/summary?market=KR&date=2026-05-08&mode=live"
                )

            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data["ok"])
            self.assertTrue(data["exists"])
            self.assertIn("outcome_status", data)
            self.assertIn("outcome_coverage", data)
            self.assertIn("outcome_buckets", data)
            self.assertIn("route_shadow_summary", data)
            self.assertIn("strategy_mismatch", data)
            self.assertEqual(data["strategy_mismatch"]["mismatch_count"], 1)

    def test_candidate_audit_summary_missing_db_is_non_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.db"
            import dashboard.dashboard_server as dashboard_server

            with patch.object(dashboard_server, "_candidate_audit_db_path", return_value=missing):
                response = dashboard_server.app.test_client().get(
                    "/api/candidate-audit/summary?market=KR&date=2026-05-08&mode=live"
                )

            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data["ok"])
            self.assertFalse(data["exists"])
            self.assertEqual(data["outcome_status"], [])
            self.assertEqual(data["outcome_coverage"], {})
            self.assertEqual(data["outcome_buckets"], [])

    def test_local_realized_dedupe_uses_close_reason_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            decisions = Path(tmp) / "live_decisions.jsonl"
            rows = [
                {
                    "type": "closed",
                    "market": "KR",
                    "session_date": "2026-05-08",
                    "timestamp": "2026-05-08T09:17:37",
                    "ticker": "058430",
                    "pnl_krw": -738.0,
                    "exit_reason": "CLOSED_HARD_STOP",
                },
                {
                    "type": "closed",
                    "market": "KR",
                    "session_date": "2026-05-08",
                    "timestamp": "2026-05-08T09:18:12",
                    "ticker": "058430",
                    "pnl_krw": -720.0,
                    "exit_reason": "CLOSED_CLAUDE_PRICE_PRE_CLOSE",
                },
            ]
            decisions.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
                encoding="utf-8",
            )

            import dashboard.dashboard_server as dashboard_server

            with patch.object(dashboard_server, "_decisions_path", return_value=decisions):
                status = dashboard_server._deduped_local_session_realized_pnl(
                    "KR",
                    "live",
                    "2026-05-08",
                )

            self.assertIsNotNone(status)
            self.assertEqual(status["pnl_krw"], -738.0)
            self.assertEqual(status["primary_close_reasons"]["058430"], "CLOSED_HARD_STOP")
            self.assertEqual(
                status["secondary_close_reasons"]["058430"],
                ["CLOSED_CLAUDE_PRICE_PRE_CLOSE"],
            )


if __name__ == "__main__":
    unittest.main()
