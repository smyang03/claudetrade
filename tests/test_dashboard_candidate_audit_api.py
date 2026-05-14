from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from audit.candidate_audit_store import CandidateAuditStore, candidate_key


class DashboardCandidateAuditApiTests(unittest.TestCase):
    def test_candidate_audit_page_renders_monitor_tab(self) -> None:
        import dashboard.dashboard_server as dashboard_server

        response = dashboard_server.app.test_client().get("/candidate-audit")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"/api/candidate-audit/summary", response.data)
        self.assertIn(b"/api/candidate-audit/rows", response.data)
        self.assertIn(b"candidate-audit-status", response.data)
        self.assertIn(b"candidate-audit-freshness", response.data)
        self.assertIn(b"candidate-audit-missed", response.data)
        self.assertIn(b"candidate-audit-watch-trigger", response.data)
        self.assertIn(b"candidate-audit-trainer", response.data)
        self.assertIn(b"/candidate-audit", response.data)

    def test_candidate_audit_page_honors_market_and_mode_query_params(self) -> None:
        import dashboard.dashboard_server as dashboard_server

        response = dashboard_server.app.test_client().get("/candidate-audit?market=US&mode=live")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("URL_PARAMS.get('market')", body)
        self.assertIn("URL_PARAMS.get('mode')", body)
        self.assertIn("localStorage.setItem('market', MARKET)", body)

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
            self.assertIn("freshness", data)
            self.assertIn("missed_winners", data)
            self.assertIn("routing_delta", data)
            self.assertIn("latency_sla", data)
            self.assertIn("watch_trigger_shadow_summary", data)
            self.assertIn("trainer_summary", data)
            self.assertEqual(data["strategy_mismatch"]["mismatch_count"], 1)
            self.assertFalse(data["trainer_summary"]["available"])

    def test_candidate_audit_api_exposes_trainer_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            store.upsert_candidate(
                {
                    "call_id": "call_trainer",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": "2026-05-12",
                    "known_at": "2026-05-12T22:30:00+09:00",
                    "ticker": "NVTS",
                    "prompt_rank": 1,
                    "in_prompt": True,
                    "final_prompt_included": True,
                    "raw_rank": 4,
                    "trainer_score_rank": 1,
                    "trainer_prompt_score": 82.5,
                    "trainer_plan_a_score": 68.0,
                    "trainer_pathb_wait_score": 74.0,
                    "trainer_risk_score": 24.5,
                    "trainer_candidate_state": "PLAN_A",
                    "prompt_pool_version": "trainer_prompt_pool_v1",
                    "classification": "watch_only",
                    "payload": {
                        "screener_quality": {
                            "screener_quality_state": "DEGRADED_COUNT",
                            "screener_degraded": True,
                            "screener_degraded_reason": "fresh_count_below_min_cache_count",
                            "screener_cache_skipped_reason": "fresh_count_below_min_cache_count",
                        }
                    },
                }
            )
            store.upsert_candidate(
                {
                    "call_id": "call_trainer",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": "2026-05-12",
                    "known_at": "2026-05-12T22:30:00+09:00",
                    "ticker": "WEAK",
                    "in_prompt": False,
                    "final_prompt_included": False,
                    "raw_rank": 40,
                    "trainer_score_rank": 40,
                    "trainer_prompt_score": 18.0,
                    "trainer_risk_score": 88.0,
                    "trainer_candidate_state": "QUARANTINE",
                    "prompt_excluded_reason": "trainer_quarantine",
                    "prompt_pool_version": "trainer_prompt_pool_v1",
                    "classification": "not_in_prompt",
                }
            )

            import dashboard.dashboard_server as dashboard_server

            with patch.object(dashboard_server, "_candidate_audit_db_path", return_value=db_path):
                summary = dashboard_server.app.test_client().get(
                    "/api/candidate-audit/summary?market=US&date=2026-05-12&mode=live"
                )
                rows = dashboard_server.app.test_client().get(
                    "/api/candidate-audit/rows?market=US&date=2026-05-12&mode=live"
                )

            self.assertEqual(summary.status_code, 200)
            summary_data = summary.get_json()
            self.assertTrue(summary_data["trainer_summary"]["available"])
            self.assertEqual(summary_data["trainer_summary"]["rows"], 2)
            self.assertEqual(summary_data["trainer_summary"]["final_prompt_rows"], 1)
            self.assertEqual(summary_data["trainer_summary"]["excluded_rows"], 1)
            self.assertIn(
                "PLAN_A",
                {row["state"] for row in summary_data["trainer_summary"]["states"]},
            )

            self.assertEqual(rows.status_code, 200)
            rows_data = rows.get_json()
            by_ticker = {row["ticker"]: row for row in rows_data["rows"]}
            self.assertEqual(by_ticker["NVTS"]["trainer_candidate_state"], "PLAN_A")
            self.assertEqual(by_ticker["NVTS"]["trainer_score_rank"], 1)
            self.assertEqual(by_ticker["NVTS"]["screener_quality_state"], "DEGRADED_COUNT")
            self.assertTrue(by_ticker["NVTS"]["screener_degraded"])
            self.assertEqual(by_ticker["WEAK"]["prompt_excluded_reason"], "trainer_quarantine")

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

    def test_candidate_audit_default_session_date_is_iso_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.db"
            import dashboard.dashboard_server as dashboard_server

            with patch.object(dashboard_server, "_candidate_audit_db_path", return_value=missing), patch.object(
                dashboard_server,
                "resolve_session_date",
                return_value=date(2026, 5, 8),
            ):
                response = dashboard_server.app.test_client().get(
                    "/api/candidate-audit/summary?market=KR&mode=live"
                )

            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["session_date"], "2026-05-08")

    def test_candidate_audit_default_session_date_uses_latest_db_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            store.upsert_candidate(
                {
                    "call_id": "old_call",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": "2026-05-07",
                    "known_at": "2026-05-08T04:56:24",
                    "ticker": "OLD",
                    "price": 10.0,
                    "classification": "watch_only",
                }
            )
            store.upsert_candidate(
                {
                    "call_id": "new_call",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": "2026-05-08",
                    "known_at": "2026-05-08T04:56:24",
                    "ticker": "NEW",
                    "price": 20.0,
                    "classification": "watch_only",
                }
            )

            import dashboard.dashboard_server as dashboard_server

            with patch.object(dashboard_server, "_candidate_audit_db_path", return_value=db_path), patch.object(
                dashboard_server,
                "resolve_session_date",
                return_value=date(2026, 5, 7),
            ):
                response = dashboard_server.app.test_client().get(
                    "/api/candidate-audit/rows?market=US&mode=live"
                )

            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["session_date"], "2026-05-08")
            self.assertEqual([row["ticker"] for row in data["rows"]], ["NEW"])

    def test_candidate_audit_empty_requested_date_falls_back_to_latest_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            store.upsert_candidate(
                {
                    "call_id": "new_call",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": "2026-05-08",
                    "known_at": "2026-05-08T23:59:39",
                    "ticker": "NEW",
                    "price": 20.0,
                    "classification": "watch_only",
                }
            )

            import dashboard.dashboard_server as dashboard_server

            with patch.object(dashboard_server, "_candidate_audit_db_path", return_value=db_path):
                response = dashboard_server.app.test_client().get(
                    "/api/candidate-audit/summary?market=US&mode=live&date=2026-05-09"
                )

            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["requested_session_date"], "2026-05-09")
            self.assertEqual(data["session_date"], "2026-05-08")
            self.assertTrue(data["session_date_fallback"])
            self.assertEqual(data["totals"]["candidate_rows"], 1)

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
