from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from audit import candidate_audit_store as audit_store_module
from audit.candidate_audit_store import CandidateAuditStore, candidate_key
from tools.analyze_candidate_audit import (
    analyze_candidate_audit,
    classify_strategy_match,
    normalize_candidate_action,
    watch_trigger_funnel_summary,
)
from tools.backfill_candidate_audit import backfill_candidate_audit
from tools.backfill_candidate_audit_runtime_evidence import (
    apply_runtime_evidence_backfill,
    build_runtime_evidence_backfill_plan,
)
from tools.candidate_audit_outcome_catchup import build_catchup_plan, main as catchup_main, run_catchup
from tools.update_candidate_audit_outcomes import update_candidate_audit_outcomes
import ticker_selection_db


class CandidateAuditBackfillTests(unittest.TestCase):
    def test_normalize_candidate_action_groups_action_families(self) -> None:
        self.assertEqual(normalize_candidate_action("BUY_READY"), "trade_ready_family")
        self.assertEqual(normalize_candidate_action("ADD_READY"), "trade_ready_family")
        self.assertEqual(normalize_candidate_action("PULLBACK_WAIT"), "watch_family")

    def test_analyze_candidate_audit_uses_latest_rows_and_reports_consistency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            base = {
                "runtime_mode": "live",
                "market": "US",
                "session_date": "2026-05-15",
                "ticker": "AAPL",
                "price": 100.0,
                "in_prompt": True,
                "input_to_claude_reported": True,
                "claude_trade_ready": True,
                "claude_action": "BUY_READY",
            }
            store.upsert_candidate({**base, "call_id": "old", "known_at": "2026-05-15T09:00:00+09:00"})
            store.upsert_candidate(
                {
                    **base,
                    "call_id": "new",
                    "known_at": "2026-05-15T09:05:00+09:00",
                    "price": 0,
                    "in_prompt": False,
                    "claude_action": "WATCH",
                }
            )

            result = analyze_candidate_audit(
                db_path=db_path,
                session_date="2026-05-15",
                market="US",
            )

        self.assertEqual(result["candidate_rows"], 1)
        self.assertEqual(result["row_uniqueness"]["call_level_rows"], 2)
        self.assertEqual(result["row_uniqueness"]["latest_session_ticker_rows"], 1)
        self.assertEqual(result["consistency"]["input_reported_not_in_prompt_count"], 1)
        self.assertEqual(result["consistency"]["legacy_input_reported_mismatch_count"], 1)
        self.assertEqual(result["consistency"]["actual_prompt_mismatch_count"], 0)
        self.assertEqual(result["consistency"]["trade_ready_family_mismatch_count"], 1)
        self.assertEqual(result["consistency"]["invalid_price_count"], 1)
        self.assertEqual(result["consistency"]["invalid_price_reason_counts"]["non_positive_price"], 1)

    def test_analyze_candidate_audit_reports_actual_prompt_bucket_and_shadow_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            base = {
                "runtime_mode": "live",
                "market": "US",
                "session_date": "2026-05-15",
                "known_at": "2026-05-15T09:05:00+09:00",
                "visibility_contract_version": "actual_prompt_v1",
                "selection_trace_id": "US:trace:1",
                "source_file": "trading_bot.prompt_pool",
                "candidate_source": "yf",
                "primary_bucket": "momentum_now",
                "source_tags_json": ["yf"],
                "raw_score_current": 91.0,
                "trainer_prompt_score": 0.72,
                "entry_timing_snapshot_json": {"entry_sequence_of_day": 1},
                "post_open_features_json": {"or_formed": True, "volume_ratio_open": 1.8},
                "filled_count": 1,
                "first_fill_at": "2026-05-15T09:20:00+09:00",
                "position_mfe_pct": 2.5,
                "position_mae_pct": -0.4,
            }
            included = {**base, "call_id": "call1", "ticker": "AAPL", "actual_prompt_included": True}
            not_included = {
                **base,
                "call_id": "call1",
                "ticker": "MSFT",
                "actual_prompt_included": False,
                "primary_bucket": "",
                "candidate_source": "",
                "source_file": "",
                "source_tags_json": [],
                "raw_score_current": None,
            }
            store.upsert_candidate(included)
            store.upsert_candidate(not_included)
            store.upsert_outcomes(
                [
                    {
                        "candidate_key": candidate_key(
                            session_date="2026-05-15",
                            market="US",
                            call_id="call1",
                            ticker="AAPL",
                        ),
                        "horizon_min": 60,
                        "observed_at": "2026-05-15T10:20:00+09:00",
                        "return_pct": 1.2,
                        "max_runup_pct": 2.4,
                        "max_drawdown_pct": -0.5,
                        "status": "audit_sparse",
                    },
                    {
                        "candidate_key": candidate_key(
                            session_date="2026-05-15",
                            market="US",
                            call_id="call1",
                            ticker="MSFT",
                        ),
                        "horizon_min": 60,
                        "observed_at": "2026-05-15T10:20:00+09:00",
                        "return_pct": -0.3,
                        "max_runup_pct": 0.5,
                        "max_drawdown_pct": -1.1,
                        "status": "audit_sparse",
                    },
                ]
            )

            result = analyze_candidate_audit(
                db_path=db_path,
                session_date="2026-05-15",
                market="US",
            )

        prompt = result["actual_prompt_profit_visibility"]
        self.assertEqual(prompt["measured_rows"], 2)
        self.assertEqual(prompt["groups"]["included"]["labeled_rows"], 1)
        self.assertEqual(prompt["groups"]["not_included"]["labeled_rows"], 1)
        self.assertEqual(prompt["delta_included_minus_not_included_mean_return_pct"], 1.5)
        quality = result["bucket_source_score_quality"]
        self.assertEqual(quality["blank_primary_bucket_count"], 1)
        self.assertEqual(quality["blank_source_count"], 1)
        self.assertEqual(quality["raw_score_missing_count"], 1)
        self.assertEqual(quality["bucket_counts"]["momentum_now"], 1)
        shadow = result["entry_exit_shadow_readiness"]
        self.assertEqual(shadow["filled_rows"], 2)
        self.assertEqual(shadow["entry_timing_snapshot_rows"], 2)
        self.assertEqual(shadow["post_open_feature_rows"], 2)
        self.assertIn("sample_gate_not_met", shadow["blockers"])

    def test_analyze_candidate_audit_splits_invalid_price_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            base = {
                "runtime_mode": "live",
                "market": "US",
                "session_date": "2026-05-15",
                "known_at": "2026-05-15T09:05:00+09:00",
                "claude_action": "WATCH",
            }
            store.upsert_candidate(
                {
                    **base,
                    "call_id": "stale",
                    "ticker": "STALE",
                    "price": 0,
                    "payload": {"quote_invalid_reason": "stale_quote"},
                }
            )
            store.upsert_candidate(
                {
                    **base,
                    "call_id": "provider",
                    "ticker": "PROV",
                    "price": 0,
                    "payload": {"price_invalid_reason": "provider_timeout"},
                }
            )
            store.upsert_candidate(
                {
                    **base,
                    "call_id": "unit",
                    "ticker": "UNIT",
                    "price": 0,
                    "quality_data_gaps_json": ["price_unit_normalization_failed"],
                }
            )
            store.upsert_candidate(
                {
                    **base,
                    "call_id": "broad",
                    "ticker": "BROAD",
                    "price": 12.5,
                    "route_runtime_gate_reason": "INVALID_PRICE",
                }
            )
            store.upsert_candidate(
                {
                    **base,
                    "call_id": "legacy",
                    "ticker": "LEGACY",
                    "price": None,
                }
            )
            store.upsert_candidate(
                {
                    **base,
                    "call_id": "missing",
                    "ticker": "MISS",
                    "price": None,
                    "payload": {"quote_invalid_reason": "quote_missing"},
                }
            )
            store.upsert_candidate(
                {
                    **base,
                    "call_id": "failed_ready",
                    "ticker": "FAILRDY",
                    "price": None,
                    "route_reason": "failed_ready",
                }
            )

            result = analyze_candidate_audit(
                db_path=db_path,
                session_date="2026-05-15",
                market="US",
            )

        self.assertEqual(result["consistency"]["invalid_price_count"], 7)
        self.assertEqual(result["consistency"]["invalid_price_reason_counts"]["stale_quote"], 1)
        self.assertEqual(result["consistency"]["invalid_price_reason_counts"]["provider_failure"], 1)
        self.assertEqual(result["consistency"]["invalid_price_reason_counts"]["unit_normalization_issue"], 1)
        self.assertEqual(result["consistency"]["invalid_price_reason_counts"]["unknown_price_issue"], 1)
        self.assertEqual(result["consistency"]["invalid_price_reason_counts"]["legacy_price_unmeasured"], 2)
        self.assertEqual(result["consistency"]["invalid_price_reason_counts"]["missing_quote"], 1)
        reasons_by_ticker = {
            item["ticker"]: item["invalid_price_reason_code"]
            for item in result["consistency"]["invalid_price"]
        }
        self.assertEqual(reasons_by_ticker["STALE"], "stale_quote")
        self.assertEqual(reasons_by_ticker["PROV"], "provider_failure")
        self.assertEqual(reasons_by_ticker["UNIT"], "unit_normalization_issue")
        self.assertEqual(reasons_by_ticker["BROAD"], "unknown_price_issue")
        self.assertEqual(reasons_by_ticker["LEGACY"], "legacy_price_unmeasured")
        self.assertEqual(reasons_by_ticker["MISS"], "missing_quote")
        self.assertEqual(reasons_by_ticker["FAILRDY"], "legacy_price_unmeasured")

    def test_analyze_candidate_audit_ignores_non_executable_selection_meta_price_null(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            base = {
                "runtime_mode": "live",
                "market": "KR",
                "session_date": "2026-06-02",
                "known_at": "2026-06-02T10:17:26+09:00",
                "source_file": "trading_bot.selection_meta",
                "in_prompt": False,
                "input_to_claude_reported": False,
                "final_prompt_included": False,
                "actual_prompt_included": False,
            }
            store.upsert_candidate(
                {
                    **base,
                    "call_id": "compact_only",
                    "ticker": "021080",
                    "price": None,
                    "claude_trade_ready": False,
                    "claude_action": "",
                    "payload": {
                        "actual_prompt_included": False,
                        "prompt_overlay_added": False,
                        "evidence_tickers": ["242040"],
                    },
                }
            )
            store.upsert_candidate(
                {
                    **base,
                    "call_id": "actionable",
                    "ticker": "005930",
                    "price": None,
                    "claude_trade_ready": True,
                    "claude_action": "BUY_READY",
                }
            )

            result = analyze_candidate_audit(
                db_path=db_path,
                session_date="2026-06-02",
                market="KR",
            )

        invalid_rows = result["consistency"]["invalid_price"]
        self.assertEqual(result["consistency"]["invalid_price_count"], 1)
        self.assertEqual(invalid_rows[0]["ticker"], "005930")
        self.assertEqual(result["consistency"]["invalid_price_reason_counts"]["legacy_price_unmeasured"], 1)

    def test_candidate_audit_additive_trainer_columns_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            store.upsert_candidate(
                {
                    "call_id": "call_trainer_1",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": "2026-05-12",
                    "known_at": "2026-05-12T22:30:00+09:00",
                    "ticker": "NVDA",
                    "in_prompt": True,
                    "selection_trace_id": "US:trace:test",
                    "visibility_contract_version": "actual_prompt_v1",
                    "actual_prompt_call_id": "call_trainer_1",
                    "actual_prompt_included": True,
                    "actual_prompt_rank": 1,
                    "reported_input_to_claude": True,
                    "prompt_join_delta_sec": 0.25,
                    "final_prompt_included": True,
                    "raw_rank": 7,
                    "raw_score_current": 91.5,
                    "raw_score_components_json": {"score_current": 91.5, "score_vol_ratio_capped": 8.0},
                    "trainer_score_rank": 1,
                    "trainer_prompt_score": 86.0,
                    "trainer_plan_a_score": 70.0,
                    "trainer_pathb_wait_score": 91.0,
                    "trainer_risk_score": 28.0,
                    "trainer_candidate_state": "PLAN_A",
                    "trainer_score_components_json": {"version": "trainer_quality_v1"},
                    "source_tags_json": ["US:momentum_now", "US:high"],
                    "candidate_quality_score": 82.5,
                    "quality_data_gaps_json": ["flow_missing"],
                    "scorer_input_snapshot_json": {
                        "ticker": "NVDA",
                        "primary_bucket": "momentum_now",
                        "liquidity_bucket": "high",
                    },
                    "scorer_config_hash": "hash123",
                    "stale_cycle": True,
                    "stale_cycle_count": 3,
                    "repeated_failed_ready_count": 2,
                    "no_fill_cycle_count": 1,
                    "failed_ready_reasons_json": ["soft_gate_override_failed"],
                    "candidate_pool_version": "trainer_quality_v1",
                    "prompt_pool_version": "trainer_prompt_pool_v1",
                    "from_high_pct": -1.25,
                    "consensus_mode": "NEUTRAL",
                    "strength_capture_shadow": True,
                    "strength_capture_rules": ["strength_v1_near_high_pct"],
                }
            )

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """
                    SELECT final_prompt_included, raw_rank, trainer_score_rank,
                           selection_trace_id, visibility_contract_version,
                           actual_prompt_call_id, actual_prompt_included,
                           actual_prompt_rank, reported_input_to_claude,
                           prompt_join_delta_sec, raw_score_current,
                           raw_score_components_json,
                           trainer_prompt_score, trainer_candidate_state,
                           trainer_score_components_json, source_tags_json,
                           candidate_quality_score, quality_data_gaps_json,
                           scorer_input_snapshot_json, scorer_config_hash,
                           stale_cycle, stale_cycle_count,
                           repeated_failed_ready_count, no_fill_cycle_count,
                           failed_ready_reasons_json,
                           from_high_pct, consensus_mode,
                           strength_capture_shadow, strength_capture_rules
                    FROM audit_candidate_rows
                    WHERE ticker='NVDA'
                    """
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(row["final_prompt_included"], 1)
            self.assertEqual(row["selection_trace_id"], "US:trace:test")
            self.assertEqual(row["visibility_contract_version"], "actual_prompt_v1")
            self.assertEqual(row["actual_prompt_call_id"], "call_trainer_1")
            self.assertEqual(row["actual_prompt_included"], 1)
            self.assertEqual(row["actual_prompt_rank"], 1)
            self.assertEqual(row["reported_input_to_claude"], 1)
            self.assertEqual(row["prompt_join_delta_sec"], 0.25)
            self.assertEqual(row["raw_score_current"], 91.5)
            self.assertIn("score_vol_ratio_capped", row["raw_score_components_json"])
            self.assertEqual(row["raw_rank"], 7)
            self.assertEqual(row["trainer_score_rank"], 1)
            self.assertEqual(row["trainer_prompt_score"], 86.0)
            self.assertEqual(row["trainer_candidate_state"], "PLAN_A")
            self.assertIn("trainer_quality_v1", row["trainer_score_components_json"])
            self.assertIn("momentum_now", row["source_tags_json"])
            self.assertEqual(row["candidate_quality_score"], 82.5)
            self.assertIn("flow_missing", row["quality_data_gaps_json"])
            self.assertIn("momentum_now", row["scorer_input_snapshot_json"])
            self.assertEqual(row["scorer_config_hash"], "hash123")
            self.assertEqual(row["stale_cycle"], 1)
            self.assertEqual(row["stale_cycle_count"], 3)
            self.assertEqual(row["repeated_failed_ready_count"], 2)
            self.assertEqual(row["no_fill_cycle_count"], 1)
            self.assertIn("soft_gate_override_failed", row["failed_ready_reasons_json"])
            self.assertEqual(row["from_high_pct"], -1.25)
            self.assertEqual(row["consensus_mode"], "NEUTRAL")
            self.assertEqual(row["strength_capture_shadow"], 1)
            self.assertEqual(json.loads(row["strength_capture_rules"]), ["strength_v1_near_high_pct"])

    def test_candidate_audit_preserves_prompt_stage_source_json_and_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            base = {
                "call_id": "call_prompt",
                "runtime_mode": "live",
                "market": "US",
                "session_date": "2026-05-19",
                "known_at": "2026-05-19T22:31:00+09:00",
                "ticker": "ABCD",
            }
            store.upsert_candidate(
                {
                    **base,
                    "source_file": "trading_bot.prompt_pool_excluded",
                    "primary_bucket": "momentum_now",
                    "liquidity_bucket": "high",
                    "source_tags_json": ["US:momentum_now", "US:high"],
                    "quality_data_gaps_json": ["flow_missing"],
                    "payload": {
                        "selection_stage": "trainer_prompt_pool_excluded",
                        "prompt_pool_audit": True,
                        "excluded_reason": "hard_cap_cutoff",
                        "screener_quality": {"screener_quality_state": "ok"},
                        "runtime_gate": {
                            "data_quality": "minute_complete",
                            "data_quality_missing": False,
                            "evidence_data_state": "confirmed",
                            "volume_ratio_open": 2.4,
                        },
                    },
                }
            )
            store.upsert_candidate(
                {
                    **base,
                    "known_at": "2026-05-19T22:40:00+09:00",
                    "source_file": "trading_bot.runtime_filter",
                    "primary_bucket": "later_bucket",
                    "liquidity_bucket": "mid",
                    "route_final_action": "HARD_BLOCK",
                    "route_runtime_gate_reason": "runtime_filtered",
                    "payload": {
                        "runtime_filtered": True,
                        "runtime_filter_reason": "runtime_filtered",
                    },
                }
            )

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """
                    SELECT source_file, primary_bucket, liquidity_bucket,
                           source_tags_json, quality_data_gaps_json,
                           route_final_action, route_runtime_gate_reason,
                           payload_json
                    FROM audit_candidate_rows
                    WHERE ticker='ABCD'
                    """
                ).fetchone()
            finally:
                conn.close()

            payload = json.loads(row["payload_json"])
            self.assertEqual(row["source_file"], "trading_bot.prompt_pool_excluded")
            self.assertEqual(row["primary_bucket"], "later_bucket")
            self.assertEqual(row["liquidity_bucket"], "mid")
            self.assertIn("US:momentum_now", json.loads(row["source_tags_json"]))
            self.assertIn("flow_missing", json.loads(row["quality_data_gaps_json"]))
            self.assertEqual(row["route_final_action"], "HARD_BLOCK")
            self.assertEqual(row["route_runtime_gate_reason"], "runtime_filtered")
            self.assertTrue(payload["runtime_filtered"])
            self.assertEqual(payload["selection_stage"], "trainer_prompt_pool_excluded")
            self.assertTrue(payload["prompt_pool_audit"])
            self.assertEqual(payload["excluded_reason"], "hard_cap_cutoff")
            self.assertEqual(payload["screener_quality"]["screener_quality_state"], "ok")
            self.assertEqual(payload["runtime_gate"]["data_quality"], "minute_complete")
            self.assertFalse(payload["runtime_gate"]["data_quality_missing"])
            self.assertEqual(payload["runtime_gate"]["evidence_data_state"], "confirmed")
            self.assertEqual(payload["runtime_gate"]["volume_ratio_open"], 2.4)

    def test_candidate_audit_extra_evidence_columns_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            store.upsert_candidate(
                {
                    "call_id": "call_evidence",
                    "runtime_mode": "live",
                    "market": "KR",
                    "session_date": "2026-06-02",
                    "known_at": "2026-06-02T09:10:00+09:00",
                    "ticker": "005930",
                    "source_file": "trading_bot.selection_meta",
                    "data_quality": "minute_complete",
                    "data_quality_missing": False,
                    "evidence_data_state": "confirmed",
                    "evidence_missing_fields_json": [],
                    "post_open_features_json": {
                        "data_quality": "minute_complete",
                        "ret_5m_pct": 1.2,
                        "volume_ratio_open": 2.4,
                    },
                    "kr_confirmation_snapshot_json": {
                        "kr_confirmation_state": "confirmed",
                        "kr_confirmation_reason": "or_formed",
                    },
                }
            )

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """
                    SELECT data_quality, data_quality_missing, evidence_data_state,
                           evidence_missing_fields_json, post_open_features_json,
                           kr_confirmation_snapshot_json
                    FROM audit_candidate_rows
                    WHERE ticker='005930'
                    """
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(row["data_quality"], "minute_complete")
            self.assertEqual(row["data_quality_missing"], 0)
            self.assertEqual(row["evidence_data_state"], "confirmed")
            self.assertEqual(json.loads(row["evidence_missing_fields_json"]), [])
            self.assertEqual(json.loads(row["post_open_features_json"])["ret_5m_pct"], 1.2)
            self.assertEqual(
                json.loads(row["kr_confirmation_snapshot_json"])["kr_confirmation_reason"],
                "or_formed",
            )

    def test_runtime_evidence_backfill_dry_run_and_apply_fill_blank_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            store.upsert_candidate(
                {
                    "call_id": "call_payload_only",
                    "runtime_mode": "live",
                    "market": "KR",
                    "session_date": "2026-06-02",
                    "known_at": "2026-06-02T09:10:00+09:00",
                    "ticker": "005930",
                    "payload": {
                        "runtime_gate": {
                            "data_quality": "minute_complete",
                            "data_quality_missing": False,
                            "evidence_data_state": "confirmed",
                            "evidence_missing_fields": [],
                            "kr_confirmation_state": "confirmed",
                            "kr_confirmation_reason": "or_formed",
                            "evidence_pack": {
                                "post_open_confirmation": {
                                    "ret_5m_pct": 1.2,
                                    "volume_ratio_open": 2.4,
                                }
                            },
                        }
                    },
                }
            )

            dry_run = build_runtime_evidence_backfill_plan(
                db_path=db_path,
                runtime_mode="live",
                market="KR",
                session_date="2026-06-02",
            )
            self.assertTrue(dry_run["ok"])
            self.assertEqual(dry_run["eligible_count"], 1)
            self.assertEqual(dry_run["eligible"][0]["updates"]["data_quality"], "minute_complete")

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                before = conn.execute(
                    "SELECT data_quality, data_quality_missing FROM audit_candidate_rows WHERE ticker='005930'"
                ).fetchone()
            finally:
                conn.close()
            self.assertIn(before["data_quality"], (None, ""))
            self.assertIsNone(before["data_quality_missing"])

            applied = apply_runtime_evidence_backfill(db_path, dry_run)
            self.assertEqual(applied, 1)

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                after = conn.execute(
                    """
                    SELECT data_quality, data_quality_missing, evidence_data_state,
                           post_open_features_json, kr_confirmation_snapshot_json
                    FROM audit_candidate_rows
                    WHERE ticker='005930'
                    """
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(after["data_quality"], "minute_complete")
            self.assertEqual(after["data_quality_missing"], 0)
            self.assertEqual(after["evidence_data_state"], "confirmed")
            self.assertEqual(json.loads(after["post_open_features_json"])["volume_ratio_open"], 2.4)
            self.assertEqual(
                json.loads(after["kr_confirmation_snapshot_json"])["kr_confirmation_reason"],
                "or_formed",
            )

    def test_runtime_evidence_backfill_does_not_overwrite_non_empty_columns_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            store.upsert_candidate(
                {
                    "call_id": "call_payload_conflict",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": "2026-06-01",
                    "known_at": "2026-06-01T22:30:00+09:00",
                    "ticker": "AAPL",
                    "data_quality": "manual_quality",
                    "payload": {
                        "runtime_gate": {
                            "data_quality": "minute_complete",
                            "data_quality_missing": False,
                            "evidence_data_state": "confirmed",
                        }
                    },
                }
            )

            plan = build_runtime_evidence_backfill_plan(
                db_path=db_path,
                runtime_mode="live",
                market="US",
                session_date="2026-06-01",
            )

            self.assertEqual(plan["conflict_count"], 1)
            self.assertNotIn("data_quality", plan["eligible"][0]["updates"])
            applied = apply_runtime_evidence_backfill(db_path, plan)
            self.assertEqual(applied, 1)

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT data_quality, data_quality_missing, evidence_data_state FROM audit_candidate_rows WHERE ticker='AAPL'"
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row["data_quality"], "manual_quality")
            self.assertEqual(row["data_quality_missing"], 0)
            self.assertEqual(row["evidence_data_state"], "confirmed")

    def test_candidate_audit_payload_json_fallback_when_payload_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)

            store.upsert_candidate(
                {
                    "call_id": "call_prompt",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": "2026-05-19",
                    "known_at": "2026-05-19T22:31:00+09:00",
                    "ticker": "ABCD",
                    "source_file": "trading_bot.prompt_pool",
                    "payload": None,
                    "payload_json": json.dumps({"selection_stage": "trainer_prompt_pool"}),
                }
            )

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """
                    SELECT payload_json
                    FROM audit_candidate_rows
                    WHERE ticker='ABCD'
                    """
                ).fetchone()
            finally:
                conn.close()

            payload = json.loads(row["payload_json"])
            self.assertEqual(payload["selection_stage"], "trainer_prompt_pool")

    def test_candidate_audit_reverse_runtime_then_prompt_preserves_runtime_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            base = {
                "call_id": "call_prompt",
                "runtime_mode": "live",
                "market": "US",
                "session_date": "2026-05-19",
                "known_at": "2026-05-19T22:31:00+09:00",
                "ticker": "ABCD",
            }

            store.upsert_candidate(
                {
                    **base,
                    "source_file": "trading_bot.runtime_filter",
                    "route_final_action": "HARD_BLOCK",
                    "route_runtime_gate_reason": "runtime_filtered",
                    "payload": {
                        "runtime_filtered": True,
                        "runtime_filter_reason": "runtime_filtered",
                    },
                }
            )
            store.upsert_candidate(
                {
                    **base,
                    "source_file": "trading_bot.prompt_pool",
                    "source_tags_json": ["US:momentum_now"],
                    "payload": {
                        "selection_stage": "trainer_prompt_pool",
                        "prompt_pool_audit": True,
                        "screener_quality": {"screener_quality_state": "ok"},
                    },
                }
            )

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """
                    SELECT source_file, source_tags_json, route_final_action,
                           route_runtime_gate_reason, payload_json
                    FROM audit_candidate_rows
                    WHERE ticker='ABCD'
                    """
                ).fetchone()
            finally:
                conn.close()

            payload = json.loads(row["payload_json"])
            self.assertEqual(row["source_file"], "trading_bot.prompt_pool")
            self.assertIn("US:momentum_now", json.loads(row["source_tags_json"]))
            self.assertEqual(row["route_final_action"], "HARD_BLOCK")
            self.assertEqual(row["route_runtime_gate_reason"], "runtime_filtered")
            self.assertTrue(payload["runtime_filtered"])
            self.assertEqual(payload["runtime_filter_reason"], "runtime_filtered")
            self.assertEqual(payload["selection_stage"], "trainer_prompt_pool")
            self.assertTrue(payload["prompt_pool_audit"])
            self.assertEqual(payload["screener_quality"]["screener_quality_state"], "ok")

    def test_upsert_candidate_rolls_back_base_row_when_extra_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            row = {
                "call_id": "call_rollback",
                "runtime_mode": "live",
                "market": "KR",
                "session_date": "2026-05-12",
                "ticker": "005930",
            }

            with patch.object(audit_store_module, "EXTRA_CANDIDATE_COLUMNS", ("missing_extra_column",)), \
                 patch.object(audit_store_module, "_candidate_extra_value", return_value="boom"):
                with self.assertRaises(sqlite3.OperationalError):
                    store.upsert_candidate(row)

            conn = sqlite3.connect(db_path)
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM audit_candidate_rows WHERE candidate_key=?",
                    [candidate_key(session_date="2026-05-12", market="KR", call_id="call_rollback", ticker="005930")],
                ).fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(count, 0)

    def test_watch_trigger_funnel_summary_counts_shadow_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            funnel_dir = root / "logs" / "funnel"
            funnel_dir.mkdir(parents=True)
            (funnel_dir / "watch_trigger_not_evaluated_20260508_US.jsonl").write_text(
                json.dumps(
                    {
                        "event": "watch_trigger_not_evaluated",
                        "market": "US",
                        "ticker": "AAPL",
                        "reason": "shadow_cycle_cap_exceeded",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (funnel_dir / "watch_trigger_shadow_20260508_US.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "event": "watch_trigger_shadow",
                                "market": "US",
                                "ticker": "AAPL",
                                "result": "would_promote",
                                "strategy": "opening_range_pullback",
                                "strategy_source": "candidate_action.primary_bucket",
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "event": "watch_trigger_shadow",
                                "market": "US",
                                "ticker": "MSFT",
                                "result": "blocked",
                                "blocked_reason": "missing_strategy",
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch(
                "tools.analyze_candidate_audit.get_runtime_path",
                side_effect=lambda *parts, **kwargs: root.joinpath(*parts),
            ):
                summary = watch_trigger_funnel_summary(session_date="2026-05-08", market="US")

        self.assertEqual(summary["watch_trigger_not_evaluated_count"], 1)
        self.assertEqual(summary["watch_trigger_shadow_count"], 2)
        self.assertEqual(summary["watch_trigger_would_promote_count"], 1)
        self.assertEqual(summary["watch_trigger_blocked_count"], 1)
        self.assertEqual(summary["blocked_reason_counts"]["missing_strategy"], 1)
        self.assertEqual(summary["strategy_source_counts"]["candidate_action.primary_bucket"], 1)

    def test_backfill_builds_separate_candidate_audit_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "logs" / "raw_calls").mkdir(parents=True)
            (root / "logs" / "screener_quality").mkdir(parents=True)
            (root / "logs" / "funnel").mkdir(parents=True)
            (root / "data" / "ml").mkdir(parents=True)
            (root / "data").mkdir(parents=True, exist_ok=True)

            raw_call = {
                "timestamp": "2026-05-08T09:00:00",
                "date": "2026-05-08",
                "market": "KR",
                "label": "select_tickers",
                "call_id": "call_1",
                "model": "test-model",
                "prompt_version": "v1",
                "tokens": {"input": 100, "output": 50},
                "prompt": "\n".join(
                    [
                        "후보 종목:",
                        "111111 chg=+3.0% p=1,000 vol=2.0x turn=10억 board=KOSPI liq=mid fit=momentum",
                        "222222 chg=+1.0% p=2,000 vol=1.5x turn=20억 board=KOSDAQ liq=low fit=gap_pullback",
                        "응답:",
                    ]
                ),
                "parsed": {
                    "watchlist": ["111111", "222222"],
                    "trade_ready": ["111111"],
                    "reasons": {"111111": "ready", "222222": "watch"},
                    "candidate_actions": [
                        {"ticker": "111111", "action": "BUY_READY", "reason": "ready"},
                        {"ticker": "222222", "action": "WATCH", "reason": "watch"},
                    ],
                },
            }
            (root / "logs" / "raw_calls" / "20260508_KR_select_tickers_090000_test.json").write_text(
                json.dumps(raw_call, ensure_ascii=False),
                encoding="utf-8",
            )

            screener_rows = [
                {
                    "timestamp": "2026-05-08T09:00:00",
                    "market": "KR",
                    "ticker": "111111",
                    "name": "A",
                    "price": 1000.0,
                    "change_rate": 3.0,
                    "volume_ratio": 2.0,
                    "input_to_claude": True,
                    "primary_bucket": "momentum_now",
                },
                {
                    "timestamp": "2026-05-08T09:00:00",
                    "market": "KR",
                    "ticker": "333333",
                    "name": "C",
                    "price": 3000.0,
                    "change_rate": 5.0,
                    "volume_ratio": 3.0,
                    "input_to_claude": False,
                    "primary_bucket": "volume_surge",
                },
            ]
            (root / "logs" / "screener_quality" / "20260508_KR_candidates.jsonl").write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in screener_rows),
                encoding="utf-8",
            )

            route = {
                "written_at": "2026-05-08T09:00:00",
                "session_date": "2026-05-08",
                "market": "KR",
                "routes": [
                    {
                        "ticker": "111111",
                        "original_action": "BUY_READY",
                        "final_action": "BUY_READY",
                        "route": "PlanA.buy",
                        "reason": "buy_ready",
                    },
                    {
                        "ticker": "222222",
                        "original_action": "WATCH",
                        "final_action": "WATCH",
                        "reason": "watch",
                    },
                ],
            }
            (root / "logs" / "funnel" / "action_routing_shadow_20260508_KR.jsonl").write_text(
                json.dumps(route, ensure_ascii=False),
                encoding="utf-8",
            )

            decisions_db = root / "data" / "ml" / "decisions.db"
            conn = sqlite3.connect(decisions_db)
            try:
                conn.execute(
                    """
                    CREATE TABLE decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts TEXT,
                        session_date TEXT,
                        market TEXT,
                        ticker TEXT,
                        decision TEXT,
                        block_reason TEXT,
                        filled INTEGER,
                        entry_price REAL,
                        exit_price REAL,
                        pnl_pct REAL,
                        exit_reason TEXT,
                        strategy_used TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        ts, session_date, market, ticker, decision, filled,
                        entry_price, exit_price, pnl_pct, exit_reason, strategy_used
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "2026-05-08T09:01:00",
                        "2026-05-08",
                        "KR",
                        "111111",
                        "BUY_SIGNAL",
                        1,
                        1000.0,
                        980.0,
                        -2.0,
                        "loss_cap",
                        "momentum",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            conn = sqlite3.connect(root / "data" / "v2_event_store.db")
            try:
                conn.execute(
                    """
                    CREATE TABLE lifecycle_events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT,
                        market TEXT,
                        runtime_mode TEXT,
                        session_date TEXT,
                        ticker TEXT,
                        occurred_at TEXT,
                        reason_code TEXT,
                        payload_json TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE v2_path_runs (
                        path_run_id TEXT,
                        market TEXT,
                        runtime_mode TEXT,
                        session_date TEXT,
                        ticker TEXT
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            conn = sqlite3.connect(root / "data" / "intraday_strategy_log.db")
            try:
                conn.execute(
                    """
                    CREATE TABLE intraday_strategy_log (
                        session_date TEXT,
                        market TEXT,
                        bot_mode TEXT,
                        ticker TEXT,
                        signal_fired INTEGER,
                        traded INTEGER
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            audit_db = root / "data" / "audit" / "candidate_audit.db"
            summary = backfill_candidate_audit(
                root=root,
                db_path=audit_db,
                session_date="2026-05-08",
                market="KR",
                runtime_mode="live",
            )

            self.assertEqual(summary["calls"]["call_count"], 1)
            store = CandidateAuditStore(audit_db)
            rows = store.rows(session_date="2026-05-08", market="KR", runtime_mode="live", limit=20)
            classes = {row["ticker"]: row["classification"] for row in rows}
            self.assertEqual(classes["111111"], "filled_loss")
            self.assertEqual(classes["222222"], "watch_only")
            self.assertEqual(classes["333333"], "not_in_prompt")

    def test_backfill_candidate_audit_default_does_not_clear_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit_db = root / "data" / "audit" / "candidate_audit.db"
            store = CandidateAuditStore(audit_db)
            store.upsert_candidate(
                {
                    "call_id": "existing_call",
                    "runtime_mode": "live",
                    "market": "KR",
                    "session_date": "2026-05-08",
                    "known_at": "2026-05-08T09:00:00",
                    "ticker": "111111",
                    "classification": "watch_only",
                }
            )

            summary = backfill_candidate_audit(
                root=root,
                db_path=audit_db,
                session_date="2026-05-08",
                market="KR",
                runtime_mode="live",
            )

            rows = store.rows(session_date="2026-05-08", market="KR", runtime_mode="live", limit=20)
            self.assertFalse(summary["reset_session"])
            self.assertEqual([row["ticker"] for row in rows], ["111111"])

    def test_backfill_reads_compact_raw_call_normalized_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "logs" / "raw_calls").mkdir(parents=True)
            (root / "logs" / "screener_quality").mkdir(parents=True)
            (root / "logs" / "funnel").mkdir(parents=True)
            (root / "data").mkdir(parents=True, exist_ok=True)
            db_path = root / "data" / "audit" / "candidate_audit.db"
            raw_call = {
                "timestamp": "2026-05-12T09:05:00",
                "date": "2026-05-12",
                "market": "US",
                "label": "select_tickers",
                "call_id": "compact_call_1",
                "model": "test-model",
                "prompt_version": "selection_rank_v3+compact_v1",
                "tokens": {"input": 100, "output": 80},
                "prompt": "\n".join(
                    [
                        "Candidates:",
                        "AAPL chg=+1.0% p=100 vol=1.2x turn=50 board=NASDAQ liq=high category=opening_range_pullback",
                        "MSFT chg=+0.5% p=200 vol=1.1x turn=40 board=NASDAQ liq=high category=gap_pullback",
                        "Market context:",
                    ]
                ),
                "parsed": {
                    "wl": ["AAPL", "MSFT"],
                    "tr": ["AAPL"],
                    "ca": [{"t": "AAPL", "a": "BUY_READY"}, {"t": "MSFT", "a": "WATCH"}],
                    "_normalized": {
                        "watchlist": ["AAPL", "MSFT"],
                        "trade_ready": ["AAPL"],
                        "reasons": {"AAPL": "OR_PULLBACK_CONFIRMED", "MSFT": "WATCH_ONLY"},
                        "recommended_strategy": {"AAPL": "opening_range_pullback", "MSFT": "gap_pullback"},
                        "candidate_actions": [
                            {
                                "ticker": "AAPL",
                                "action": "BUY_READY",
                                "strategy": "opening_range_pullback",
                                "reason": "OR_PULLBACK_CONFIRMED",
                            },
                            {
                                "ticker": "MSFT",
                                "action": "WATCH",
                                "strategy": "gap_pullback",
                                "reason": "WATCH_ONLY",
                            },
                        ],
                        "price_targets": {"AAPL": {"buy_zone_low": 99, "buy_zone_high": 101}},
                        "_selection_raw_schema": "compact",
                        "_candidate_quality_trainer_version": "trainer_quality_v1",
                        "_prompt_pool_version": "trainer_prompt_pool_v1",
                        "_final_prompt_pool": [
                            {
                                "ticker": "AAPL",
                                "prompt_rank": 1,
                                "final_prompt_included": True,
                                "raw_rank": 2,
                                "trainer_score_rank": 1,
                                "trainer_prompt_score": 82.0,
                                "trainer_plan_a_score": 67.0,
                                "trainer_pathb_wait_score": 88.0,
                                "trainer_risk_score": 24.0,
                                "trainer_candidate_state": "PLAN_A",
                                "trainer_score_components": {"version": "trainer_quality_v1"},
                                "source_tags": ["US:momentum_now"],
                                "candidate_pool_version": "trainer_quality_v1",
                                "prompt_pool_version": "trainer_prompt_pool_v1",
                            },
                            {
                                "ticker": "MSFT",
                                "prompt_rank": 2,
                                "final_prompt_included": True,
                                "raw_rank": 1,
                                "trainer_score_rank": 2,
                                "trainer_prompt_score": 56.0,
                                "trainer_risk_score": 31.0,
                                "trainer_candidate_state": "PLAN_B",
                                "candidate_pool_version": "trainer_quality_v1",
                                "prompt_pool_version": "trainer_prompt_pool_v1",
                            },
                        ],
                        "_excluded_from_prompt": [
                            {
                                "ticker": "WEAK",
                                "reason": "trainer_quarantine",
                                "candidate": {
                                    "ticker": "WEAK",
                                    "raw_rank": 40,
                                    "trainer_score_rank": 40,
                                    "trainer_prompt_score": 12.0,
                                    "trainer_risk_score": 95.0,
                                    "trainer_candidate_state": "QUARANTINE",
                                    "candidate_pool_version": "trainer_quality_v1",
                                    "prompt_pool_version": "trainer_prompt_pool_v1",
                                },
                            }
                        ],
                    },
                },
                "extra": {"compact_schema_enabled": True, "prompt_contract": "selection_compact.v1"},
            }
            (root / "logs" / "raw_calls" / "20260512_US_select_tickers_090500_compact.json").write_text(
                json.dumps(raw_call, ensure_ascii=False),
                encoding="utf-8",
            )

            backfill_candidate_audit(
                root=root,
                db_path=db_path,
                session_date="2026-05-12",
                market="US",
                runtime_mode="live",
            )

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT ticker, claude_action, claude_watchlist, claude_trade_ready, recommended_strategy, primary_bucket
                    FROM audit_candidate_rows
                    WHERE call_id='compact_call_1'
                      AND in_prompt=1
                    ORDER BY prompt_rank
                    """
                ).fetchall()
                trainer_rows = conn.execute(
                    """
                    SELECT ticker, final_prompt_included, raw_rank, trainer_score_rank,
                           trainer_prompt_score, trainer_candidate_state, prompt_excluded_reason
                    FROM audit_candidate_rows
                    WHERE call_id='compact_call_1'
                    ORDER BY COALESCE(prompt_rank, 999), ticker
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertEqual(len(rows), 2)
            self.assertEqual([row["ticker"] for row in rows], ["AAPL", "MSFT"])
            self.assertEqual(rows[0]["claude_action"], "BUY_READY")
            self.assertEqual(rows[0]["claude_trade_ready"], 1)
            self.assertEqual(rows[0]["recommended_strategy"], "opening_range_pullback")
            self.assertEqual(rows[0]["primary_bucket"], "opening_range_pullback")
            self.assertEqual(rows[1]["claude_action"], "WATCH")
            self.assertEqual(rows[1]["claude_watchlist"], 1)
            self.assertEqual(rows[1]["recommended_strategy"], "gap_pullback")
            self.assertEqual(rows[1]["primary_bucket"], "gap_pullback")
            by_ticker = {row["ticker"]: row for row in trainer_rows}
            self.assertEqual(by_ticker["AAPL"]["final_prompt_included"], 1)
            self.assertEqual(by_ticker["AAPL"]["raw_rank"], 2)
            self.assertEqual(by_ticker["AAPL"]["trainer_score_rank"], 1)
            self.assertEqual(by_ticker["AAPL"]["trainer_prompt_score"], 82.0)
            self.assertEqual(by_ticker["AAPL"]["trainer_candidate_state"], "PLAN_A")
            self.assertEqual(by_ticker["WEAK"]["final_prompt_included"], 0)
            self.assertEqual(by_ticker["WEAK"]["prompt_excluded_reason"], "trainer_quarantine")
            self.assertEqual(by_ticker["WEAK"]["trainer_candidate_state"], "QUARANTINE")

    def test_store_persists_discovery_role_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            store.upsert_candidate(
                {
                    "call_id": "call_discovery",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": "2026-05-28",
                    "known_at": "2026-05-28T09:00:00",
                    "ticker": "DISC",
                    "source_file": "trading_bot.prompt_pool",
                    "in_prompt": True,
                    "candidate_pool_role": "DISCOVERY",
                    "discovery_signal_family": "near_breakout,momentum_now",
                    "discovery_reason": "core_cap_signal_candidate",
                    "discovery_action_ceiling": "WATCH",
                    "discovery_baseline_trainer_rank": 36,
                    "discovery_overlay_rank": 1,
                    "discovery_action_ceiling_applied": True,
                    "discovery_demoted_from": "PULLBACK_WAIT",
                }
            )

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """
                    SELECT candidate_pool_role, discovery_signal_family, discovery_reason,
                           discovery_action_ceiling, discovery_baseline_trainer_rank,
                           discovery_overlay_rank, discovery_action_ceiling_applied,
                           discovery_demoted_from
                    FROM audit_candidate_rows
                    WHERE ticker='DISC'
                    """
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(row["candidate_pool_role"], "DISCOVERY")
            self.assertEqual(row["discovery_signal_family"], "near_breakout,momentum_now")
            self.assertEqual(row["discovery_reason"], "core_cap_signal_candidate")
            self.assertEqual(row["discovery_action_ceiling"], "WATCH")
            self.assertEqual(row["discovery_baseline_trainer_rank"], 36)
            self.assertEqual(row["discovery_overlay_rank"], 1)
            self.assertEqual(row["discovery_action_ceiling_applied"], 1)
            self.assertEqual(row["discovery_demoted_from"], "PULLBACK_WAIT")

    def test_outcome_labeler_uses_existing_horizon_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            session_date = "2026-05-08"
            prices = [
                ("call_0", "2026-05-08T09:00:00", 100.0),
                ("call_1", "2026-05-08T09:10:00", 101.0),
                ("call_2", "2026-05-08T09:20:00", 103.0),
                ("call_3", "2026-05-08T09:30:00", 102.0),
                ("call_4", "2026-05-08T10:00:00", 104.0),
            ]
            for call_id, known_at, price in prices:
                store.upsert_candidate(
                    {
                        "call_id": call_id,
                        "runtime_mode": "live",
                        "market": "KR",
                        "session_date": session_date,
                        "known_at": known_at,
                        "ticker": "AAA",
                        "price": price,
                        "screener_seen": True,
                        "classification": "not_in_prompt",
                    }
                )

            summary = update_candidate_audit_outcomes(
                db_path=db_path,
                session_date=session_date,
                market="KR",
                horizons=(30, 60),
            )

            self.assertEqual(summary["candidate_rows"], 5)
            self.assertEqual(summary["outcome_rows"], 10)
            base_key = candidate_key(
                session_date=session_date,
                market="KR",
                call_id="call_0",
                ticker="AAA",
            )
            late_key = candidate_key(
                session_date=session_date,
                market="KR",
                call_id="call_4",
                ticker="AAA",
            )
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                base_30 = dict(
                    conn.execute(
                        """
                        SELECT * FROM audit_candidate_outcomes
                        WHERE candidate_key=? AND horizon_min=30
                        """,
                        (base_key,),
                    ).fetchone()
                )
                late_30 = dict(
                    conn.execute(
                        """
                        SELECT * FROM audit_candidate_outcomes
                        WHERE candidate_key=? AND horizon_min=30
                        """,
                        (late_key,),
                    ).fetchone()
                )
            finally:
                conn.close()

            self.assertEqual(base_30["status"], "audit_sparse")
            self.assertAlmostEqual(base_30["return_pct"], 2.0)
            self.assertAlmostEqual(base_30["max_runup_pct"], 3.0)
            self.assertEqual(late_30["status"], "insufficient_samples")

    def test_outcome_labeler_adds_daily_forward_horizons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "candidate_audit.db"
            price_dir = root / "price"
            kr_dir = price_dir / "kr"
            kr_dir.mkdir(parents=True)
            (kr_dir / "kr_AAA.csv").write_text(
                "\n".join(
                    [
                        "date,open,high,low,close,volume",
                        "2026-05-07,100,105,95,100,1000",
                        "2026-05-08,101,112,98,110,1000",
                        "2026-05-11,111,116,109,115,1000",
                        "2026-05-12,116,121,114,120,1000",
                    ]
                ),
                encoding="utf-8",
            )
            store = CandidateAuditStore(db_path)
            store.upsert_candidate(
                {
                    "call_id": "daily_0",
                    "runtime_mode": "live",
                    "market": "KR",
                    "session_date": "2026-05-07",
                    "known_at": "2026-05-07T09:00:00",
                    "ticker": "AAA",
                    "price": 100.0,
                    "strength_capture_shadow": True,
                    "strength_capture_rules": ["strength_v1_chg25_vol20"],
                }
            )

            ticker_selection_db._price_cache.clear()
            with patch.object(ticker_selection_db, "PRICE_DIR", str(price_dir)):
                summary = update_candidate_audit_outcomes(
                    db_path=db_path,
                    session_date="2026-05-07",
                    market="KR",
                    horizons=(1440, 2880, 4320),
                )

            self.assertEqual(summary["outcome_rows"], 3)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = {
                    row["horizon_min"]: row
                    for row in conn.execute(
                        """
                        SELECT horizon_min, status, observed_at, observed_price,
                               return_pct, max_runup_pct, max_drawdown_pct, payload_json
                        FROM audit_candidate_outcomes
                        ORDER BY horizon_min
                        """
                    )
                }
            finally:
                conn.close()

            self.assertEqual(rows[1440]["status"], "daily_forward")
            self.assertEqual(rows[1440]["observed_at"], "2026-05-08")
            self.assertAlmostEqual(rows[1440]["return_pct"], 10.0)
            self.assertAlmostEqual(rows[2880]["return_pct"], 15.0)
            self.assertAlmostEqual(rows[4320]["return_pct"], 20.0)
            payload = json.loads(rows[2880]["payload_json"])
            self.assertEqual(payload["horizon_kind"], "trading_day_close")
            self.assertEqual(payload["trading_day_offset"], 2)
            self.assertEqual(payload["target_session_date"], "2026-05-11")
            self.assertTrue(payload["strength_capture_shadow"])

    def test_candidate_audit_outcome_catchup_builds_market_date_plan(self) -> None:
        plan = build_catchup_plan(from_date="2026-05-08", to_date="2026-05-09", market="")

        self.assertEqual(
            plan,
            [
                {"session_date": "2026-05-08", "market": "KR"},
                {"session_date": "2026-05-08", "market": "US"},
                {"session_date": "2026-05-09", "market": "KR"},
                {"session_date": "2026-05-09", "market": "US"},
            ],
        )

    def test_candidate_audit_outcome_catchup_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            store.upsert_candidate(
                {
                    "call_id": "dry_0",
                    "runtime_mode": "live",
                    "market": "KR",
                    "session_date": "2026-05-08",
                    "known_at": "2026-05-08T09:00:00",
                    "ticker": "AAA",
                    "price": 100.0,
                }
            )
            summary = run_catchup(
                db_path=db_path,
                date_arg="2026-05-08",
                market="KR",
                runtime_mode="live",
                dry_run=True,
            )

            self.assertTrue(summary["dry_run"])
            self.assertEqual(summary["planned"], [{"session_date": "2026-05-08", "market": "KR"}])
            self.assertEqual(len(summary["results"]), 1)
            self.assertEqual(summary["total_outcome_rows"], 0)
            self.assertGreater(summary["total_planned_outcome_rows"], 0)
            conn = sqlite3.connect(db_path)
            try:
                count = conn.execute("SELECT COUNT(*) FROM audit_candidate_outcomes").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count, 0)

    def test_update_candidate_audit_outcomes_dry_run_missing_db_does_not_create(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "missing" / "candidate_audit.db"

            summary = update_candidate_audit_outcomes(db_path=db_path, dry_run=True)

            self.assertTrue(summary["dry_run"])
            self.assertEqual(summary["status"], "db_not_found")
            self.assertEqual(summary["candidate_rows"], 0)
            self.assertEqual(summary["written_rows"], 0)
            self.assertEqual(summary["outcome_health"], "db_not_found")
            self.assertFalse(db_path.exists())
            self.assertFalse(db_path.parent.exists())

    def test_update_candidate_audit_outcomes_dry_run_existing_db_does_not_migrate_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE audit_candidate_rows (
                        candidate_key TEXT PRIMARY KEY,
                        call_id TEXT NOT NULL,
                        runtime_mode TEXT NOT NULL,
                        market TEXT NOT NULL,
                        session_date TEXT NOT NULL,
                        known_at TEXT,
                        ticker TEXT NOT NULL,
                        price REAL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO audit_candidate_rows (
                        candidate_key, call_id, runtime_mode, market, session_date,
                        known_at, ticker, price
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "live:KR:2026-05-08:AAA:dry_0",
                        "dry_0",
                        "live",
                        "KR",
                        "2026-05-08",
                        "2026-05-08T09:00:00",
                        "AAA",
                        100.0,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            summary = update_candidate_audit_outcomes(db_path=db_path, dry_run=True)

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["candidate_rows"], 1)
            self.assertGreater(summary["planned_outcome_rows"], 0)
            self.assertEqual(summary["written_rows"], 0)
            conn = sqlite3.connect(db_path)
            try:
                outcome_table = conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type='table' AND name='audit_candidate_outcomes'
                    """
                ).fetchone()
            finally:
                conn.close()
            self.assertIsNone(outcome_table)

    def test_update_candidate_audit_outcomes_dry_run_idle_wal_db_does_not_create_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            store.upsert_candidate(
                {
                    "call_id": "dry_wal_0",
                    "runtime_mode": "live",
                    "market": "KR",
                    "session_date": "2026-05-08",
                    "known_at": "2026-05-08T09:00:00",
                    "ticker": "AAA",
                    "price": 100.0,
                }
            )
            sidecars = [Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]
            self.assertFalse(any(path.exists() for path in sidecars))

            summary = update_candidate_audit_outcomes(db_path=db_path, dry_run=True)

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["candidate_rows"], 1)
            self.assertEqual(summary["written_rows"], 0)
            self.assertFalse(any(path.exists() for path in sidecars))

    def test_outcome_update_preserves_existing_non_null_unless_forced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            session_date = "2026-05-08"
            prices = [
                ("call_0", "2026-05-08T09:00:00", 100.0),
                ("call_1", "2026-05-08T09:10:00", 101.0),
                ("call_2", "2026-05-08T09:20:00", 103.0),
                ("call_3", "2026-05-08T09:30:00", 102.0),
            ]
            for call_id, known_at, price in prices:
                store.upsert_candidate(
                    {
                        "call_id": call_id,
                        "runtime_mode": "live",
                        "market": "KR",
                        "session_date": session_date,
                        "known_at": known_at,
                        "ticker": "AAA",
                        "price": price,
                    }
                )
            base_key = candidate_key(
                session_date=session_date,
                market="KR",
                call_id="call_0",
                ticker="AAA",
            )
            store.upsert_outcome(
                {
                    "candidate_key": base_key,
                    "horizon_min": 30,
                    "target_at": "legacy",
                    "observed_at": "legacy",
                    "observed_price": 999.0,
                    "return_pct": 99.0,
                    "max_runup_pct": 99.0,
                    "max_drawdown_pct": 0.0,
                    "status": "legacy_non_null",
                    "source": "test",
                    "label_generated_at": "legacy",
                    "payload": {"legacy": True},
                }
            )

            summary = update_candidate_audit_outcomes(
                db_path=db_path,
                session_date=session_date,
                market="KR",
                horizons=(30,),
                min_samples_by_horizon={30: 1},
            )
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                preserved = dict(
                    conn.execute(
                        """
                        SELECT return_pct, status
                        FROM audit_candidate_outcomes
                        WHERE candidate_key=? AND horizon_min=30
                        """,
                        (base_key,),
                    ).fetchone()
                )
            finally:
                conn.close()
            self.assertEqual(summary["skipped_existing_non_null_rows"], 1)
            self.assertEqual(preserved["return_pct"], 99.0)
            self.assertEqual(preserved["status"], "legacy_non_null")

            forced = update_candidate_audit_outcomes(
                db_path=db_path,
                session_date=session_date,
                market="KR",
                horizons=(30,),
                min_samples_by_horizon={30: 1},
                force_recompute=True,
            )
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                overwritten = dict(
                    conn.execute(
                        """
                        SELECT return_pct, status
                        FROM audit_candidate_outcomes
                        WHERE candidate_key=? AND horizon_min=30
                        """,
                        (base_key,),
                    ).fetchone()
                )
            finally:
                conn.close()
            self.assertGreaterEqual(forced["planned_overwrite_existing_non_null_count"], 1)
            self.assertAlmostEqual(overwritten["return_pct"], 2.0)
            self.assertEqual(overwritten["status"], "audit_sparse")

    def test_candidate_audit_outcome_catchup_cli_writes_requested_report_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            report_dir = Path(tmp) / "reports"
            CandidateAuditStore(db_path)
            stdout = StringIO()
            with redirect_stdout(stdout):
                rc = catchup_main(
                    [
                        "--db",
                        str(db_path),
                        "--date",
                        "2026-05-08",
                        "--market",
                        "KR",
                        "--dry-run",
                        "--write-report",
                        "--report-dir",
                        str(report_dir),
                    ]
                )
            self.assertEqual(rc, 0)
            reports = list(report_dir.glob("candidate_audit_outcome_catchup_*.json"))
            self.assertEqual(len(reports), 1)
            payload = json.loads(reports[0].read_text(encoding="utf-8"))
            self.assertTrue(payload["dry_run"])

    def test_analysis_percentiles_and_strategy_mismatch_are_python_side(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            session_date = "2026-05-08"
            for call_id, ticker, known_at, price, classification in [
                ("a0", "AAA", "2026-05-08T09:00:00", 100.0, "not_in_prompt"),
                ("a1", "AAA", "2026-05-08T09:10:00", 102.0, "not_in_prompt"),
                ("a2", "AAA", "2026-05-08T09:20:00", 101.0, "not_in_prompt"),
                ("b0", "BBB", "2026-05-08T09:00:00", 200.0, "filled_win"),
                ("c0", "CCC", "2026-05-08T09:00:00", 300.0, "filled_loss"),
                ("d0", "DDD", "2026-05-08T09:00:00", 400.0, "ready_no_signal"),
            ]:
                recommended = ""
                if ticker == "BBB":
                    recommended = "momentum,gap_pullback"
                if ticker == "CCC":
                    recommended = "gap_pullback"
                store.upsert_candidate(
                    {
                        "call_id": call_id,
                        "runtime_mode": "live",
                        "market": "KR",
                        "session_date": session_date,
                        "known_at": known_at,
                        "ticker": ticker,
                        "price": price,
                        "screener_seen": True,
                        "recommended_strategy": recommended,
                        "classification": classification,
                    }
                )
            store.update_execution_by_ticker(
                session_date=session_date,
                market="KR",
                runtime_mode="live",
                ticker="BBB",
                values={"filled_count": 1, "strategy_used": "momentum", "pnl_pct": 1.0, "close_reason": "CLOSED_TARGET"},
            )
            store.update_execution_by_ticker(
                session_date=session_date,
                market="KR",
                runtime_mode="live",
                ticker="CCC",
                values={"filled_count": 1, "strategy_used": "momentum", "pnl_pct": -2.0, "close_reason": "CLOSED_LOSS_CAP"},
            )
            update_candidate_audit_outcomes(
                db_path=db_path,
                session_date=session_date,
                market="KR",
                horizons=(30,),
                min_samples_by_horizon={30: 1},
            )

            result = analyze_candidate_audit(
                db_path=db_path,
                session_date=session_date,
                market="KR",
                horizon_min=30,
                latest_only=False,
            )

            not_in_prompt = next(
                bucket for bucket in result["buckets"] if bucket["classification"] == "not_in_prompt"
            )
            self.assertIn("median_return_pct", not_in_prompt)
            self.assertIn("p90_return_pct", not_in_prompt)
            self.assertIn("small_bucket_sample", not_in_prompt)
            self.assertNotIn("small_sample", not_in_prompt)
            self.assertIn("outcome_coverage", result)
            self.assertEqual(result["outcome_coverage"]["30"]["audit_sparse"], 2)
            self.assertIn("classification_counts", result["outcome_coverage"]["30"])
            self.assertIn("coverage_gap_reasons", result["outcome_coverage"]["30"])
            self.assertIn("route_shadow_summary", result)
            top_not_in_prompt = result["top_mfe"]["not_in_prompt"][0]
            self.assertIn("thin_price_sample", top_not_in_prompt)
            self.assertNotIn("small_sample", top_not_in_prompt)
            self.assertEqual(classify_strategy_match("momentum,gap_pullback", "momentum"), "match")
            self.assertEqual(classify_strategy_match("gap_pullback", "momentum"), "mismatch")
            self.assertEqual(result["strategy_mismatch"]["filled_strategy_rows"], 2)
            self.assertEqual(result["strategy_mismatch"]["match_count"], 1)
            self.assertEqual(result["strategy_mismatch"]["mismatch_count"], 1)

    def test_analyze_candidate_audit_decomposes_watch_only_buckets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            session_date = "2026-05-17"
            rows = [
                {
                    "call_id": "call_bucket",
                    "runtime_mode": "live",
                    "market": "KR",
                    "session_date": session_date,
                    "known_at": "2026-05-17T09:00:00+09:00",
                    "ticker": "111111",
                    "classification": "watch_only",
                    "claude_action": "WATCH",
                    "route_final_action": "WATCH",
                    "route_reason": "evidence_ceiling_watch",
                    "evidence_ceiling_applied": True,
                },
                {
                    "call_id": "call_bucket",
                    "runtime_mode": "live",
                    "market": "KR",
                    "session_date": session_date,
                    "known_at": "2026-05-17T09:00:00+09:00",
                    "ticker": "222222",
                    "classification": "watch_only",
                    "claude_action": "PROBE_READY",
                    "route_final_action": "WATCH",
                    "route_reason": "probe_blocked_above_pathb_zone",
                },
                {
                    "call_id": "call_bucket",
                    "runtime_mode": "live",
                    "market": "KR",
                    "session_date": session_date,
                    "known_at": "2026-05-17T09:00:00+09:00",
                    "ticker": "333333",
                    "classification": "ready_no_signal",
                    "claude_action": "BUY_READY",
                    "route_final_action": "BUY_READY",
                    "no_signal_count": 1,
                },
            ]
            for row in rows:
                store.upsert_candidate(row)
                store.upsert_outcome(
                    {
                        "candidate_key": candidate_key(
                            session_date=session_date,
                            market="KR",
                            call_id=row["call_id"],
                            ticker=row["ticker"],
                        ),
                        "horizon_min": 60,
                        "return_pct": 0.5,
                        "max_runup_pct": 3.0,
                        "max_drawdown_pct": -0.5,
                        "status": "ok",
                    }
                )

            result = analyze_candidate_audit(
                db_path=db_path,
                session_date=session_date,
                market="KR",
                horizon_min=60,
            )

        decomp = result["watch_only_bucket_decomposition"]
        counts = {bucket["bucket"]: bucket["rows"] for bucket in decomp["buckets"]}
        self.assertEqual(counts["evidence_ceiling"], 1)
        self.assertEqual(counts["pathb_zone_or_plan"], 1)
        self.assertEqual(counts["strategy_no_signal"], 1)
        self.assertEqual(decomp["rows_considered"], 3)
        self.assertEqual(decomp["examples"]["evidence_ceiling"][0]["ticker"], "111111")

    def test_audit_call_summary_separates_actual_prompt_and_watchlist_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            store.upsert_call(
                {
                    "call_id": "call_counts",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": "2026-05-08",
                    "called_at": "2026-05-08T09:00:00",
                    "prompt_candidate_count": 2,
                    "watchlist_count": 2,
                    "payload": {"actual_prompt_count": 1},
                }
            )

            summary = store.summary(session_date="2026-05-08", market="US", runtime_mode="live")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """
                    SELECT prompt_candidate_count, actual_prompt_count, watchlist_count
                    FROM audit_claude_calls
                    WHERE call_id='call_counts'
                    """
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(row["prompt_candidate_count"], 1)
            self.assertEqual(row["actual_prompt_count"], 1)
            self.assertEqual(row["watchlist_count"], 2)
            self.assertEqual(summary["calls"]["prompt_candidate_rows"], 1)
            self.assertEqual(summary["calls"]["actual_prompt_rows"], 1)
            self.assertEqual(summary["calls"]["watchlist_rows"], 2)

    def test_audit_call_summary_falls_back_when_actual_prompt_count_is_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            store.upsert_call(
                {
                    "call_id": "call_legacy_counts",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": "2026-05-08",
                    "called_at": "2026-05-08T09:00:00",
                    "prompt_candidate_count": 2,
                }
            )

            summary = store.summary(session_date="2026-05-08", market="US", runtime_mode="live")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """
                    SELECT prompt_candidate_count, actual_prompt_count
                    FROM audit_claude_calls
                    WHERE call_id='call_legacy_counts'
                    """
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(row["prompt_candidate_count"], 2)
            self.assertEqual(row["actual_prompt_count"], 0)
            self.assertEqual(summary["calls"]["prompt_candidate_rows"], 2)
            self.assertEqual(summary["calls"]["actual_prompt_rows"], 2)

    def test_analysis_exposes_live_monitoring_operational_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "logs" / "raw_calls").mkdir(parents=True)
            (root / "logs" / "funnel").mkdir(parents=True)
            (root / "logs" / "screener_quality").mkdir(parents=True)
            db_path = root / "data" / "audit" / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            session_date = "2026-05-08"
            store.upsert_call(
                {
                    "call_id": "call_1",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": session_date,
                    "called_at": "2026-05-08T09:00:00",
                    "label": "select_tickers",
                    "prompt_candidate_count": 2,
                }
            )
            store.upsert_candidate(
                {
                    "call_id": "call_1",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": session_date,
                    "known_at": "2026-05-08T09:00:00",
                    "ticker": "AAA",
                    "price": 100.0,
                    "claude_watchlist": True,
                    "classification": "watch_only",
                }
            )
            key = candidate_key(session_date=session_date, market="US", call_id="call_1", ticker="AAA")
            store.upsert_outcome(
                {
                    "candidate_key": key,
                    "horizon_min": 30,
                    "target_at": "2026-05-08T09:30:00",
                    "observed_at": "2026-05-08T09:30:00",
                    "observed_price": 103.0,
                    "return_pct": 3.0,
                    "max_runup_pct": 3.2,
                    "max_drawdown_pct": -1.0,
                    "status": "audit_sparse",
                    "source": "audit_candidate_rows",
                    "payload": {"sample_count": 2},
                }
            )
            (root / "logs" / "raw_calls" / "20260508_US_select_tickers_090500_test.json").write_text(
                json.dumps({"timestamp": "2026-05-08T09:05:00"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (root / "logs" / "funnel" / "candidate_funnel_snapshot_20260508_US.jsonl").write_text(
                json.dumps(
                    {
                        "written_at": "2026-05-08T09:05:00",
                        "session_date": session_date,
                        "market": "US",
                        "full_pool_count": 3,
                        "prompt_pool_count": 2,
                        "selection_stages": {
                            "raw": {"trade_ready": ["AAA", "BBB"]},
                            "normalized": {"trade_ready": ["AAA"]},
                            "applied": {"trade_ready": ["AAA"]},
                        },
                        "runtime_filtered": {"BBB": "slot_cap"},
                        "runtime_filtered_count": 1,
                        "pathb_wait_tickers": ["CCC"],
                        "candidate_action_routes": [
                            {
                                "ticker": "BBB",
                                "original_action": "BUY_READY",
                                "final_action": "WATCH",
                                "reason": "slot_cap",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "logs" / "funnel" / "candidate_cycle_latency_20260508_US.jsonl").write_text(
                json.dumps({"written_at": "2026-05-08T09:05:00", "elapsed_ms": 70000, "alert": True})
                + "\n",
                encoding="utf-8",
            )
            (root / "logs" / "funnel" / "watch_trigger_shadow_20260508_US.jsonl").write_text(
                json.dumps(
                    {
                        "written_at": "2026-05-08T09:05:00",
                        "market": "US",
                        "ticker": "AAA",
                        "result": "blocked",
                        "blocked_reason": "missing_strategy",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            with patch(
                "tools.analyze_candidate_audit.get_runtime_path",
                side_effect=lambda *parts, **kwargs: root.joinpath(*parts),
            ):
                result = analyze_candidate_audit(
                    db_path=db_path,
                    session_date=session_date,
                    market="US",
                    horizon_min=30,
                )

            self.assertEqual(result["freshness"]["status"], "stale")
            self.assertEqual(result["freshness"]["max_lag_sec"], 300)
            self.assertEqual(result["outcome_coverage"]["30"]["maturity"], "ready")
            self.assertEqual(result["missed_winners"][0]["ticker"], "AAA")
            self.assertEqual(result["missed_winners"][0]["miss_stage"], "claude_not_selected")
            self.assertEqual(result["routing_delta"]["raw_trade_ready_count"], 2)
            self.assertEqual(result["routing_delta"]["applied_trade_ready_count"], 1)
            self.assertEqual(result["routing_delta"]["dropped_after_raw"], ["BBB"])
            self.assertEqual(result["latency_sla"]["status"], "critical")
            self.assertTrue(result["watch_trigger_shadow_summary"]["data_gap_dominant"])

    def test_candidate_latest_rows_view_deduplicates_session_ticker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            store.upsert_candidate(
                {
                    "call_id": "call_old",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": "2026-05-08",
                    "known_at": "2026-05-08T09:00:00",
                    "ticker": "AAPL",
                    "route_final_action": "WATCH",
                }
            )
            store.upsert_candidate(
                {
                    "call_id": "call_new",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": "2026-05-08",
                    "known_at": "2026-05-08T09:05:00",
                    "ticker": "AAPL",
                    "route_final_action": "BUY_READY",
                }
            )

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT call_id, route_final_action
                    FROM audit_candidate_latest_rows
                    WHERE runtime_mode='live' AND market='US'
                      AND session_date='2026-05-08' AND ticker='AAPL'
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["call_id"], "call_new")
            self.assertEqual(rows[0]["route_final_action"], "BUY_READY")
            self.assertEqual(
                store.candidate_row_uniqueness(session_date="2026-05-08", market="US"),
                {
                    "call_level_rows": 2,
                    "latest_session_ticker_rows": 1,
                    "duplicate_group_count": 1,
                },
            )

    def test_update_execution_by_ticker_can_target_latest_row_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            for call_id, known_at in (("call_old", "2026-05-08T09:00:00"), ("call_new", "2026-05-08T09:05:00")):
                store.upsert_candidate(
                    {
                        "call_id": call_id,
                        "runtime_mode": "live",
                        "market": "US",
                        "session_date": "2026-05-08",
                        "known_at": known_at,
                        "ticker": "AAPL",
                    }
                )

            updated = store.update_execution_by_ticker(
                session_date="2026-05-08",
                market="US",
                runtime_mode="live",
                ticker="AAPL",
                values={
                    "filled_count": 1,
                    "execution_link_source": "v2_event_store.lifecycle_events",
                    "execution_decision_id": "decision_new",
                    "execution_event_id": 42,
                    "entry_timing_snapshot_json": {"candidate_to_order_delay_min": 12.5},
                    "candidate_health_snapshot_json": {"health_state": "STABLE_READY"},
                    "entry_delay_min": 12.5,
                    "entry_price_vs_first_seen_pct": 1.2,
                    "entry_price_vs_first_ready_pct": -0.4,
                    "position_mfe_pct": 2.1,
                    "position_mae_pct": -1.3,
                    "us_early_entry_window": "active",
                    "us_early_entry_elapsed_min": 30.0,
                    "us_early_entry_size_mult": 0.5,
                    "us_early_entry_confirmation_reason": "us_early_entry_soft_size",
                    "us_early_entry_gate_json": {"active": True, "size_mult": 0.5},
                },
                latest_only=True,
            )

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT call_id, filled_count, execution_link_source,
                           execution_decision_id, execution_event_id,
                           entry_timing_snapshot_json, candidate_health_snapshot_json,
                           entry_delay_min, entry_price_vs_first_seen_pct,
                           entry_price_vs_first_ready_pct, position_mfe_pct,
                           position_mae_pct, us_early_entry_window,
                           us_early_entry_elapsed_min, us_early_entry_size_mult,
                           us_early_entry_confirmation_reason,
                           us_early_entry_gate_json
                    FROM audit_candidate_rows
                    WHERE ticker='AAPL'
                    ORDER BY known_at
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertEqual(updated, 1)
            self.assertEqual(rows[0]["call_id"], "call_old")
            self.assertEqual(rows[0]["filled_count"], 0)
            self.assertIsNone(rows[0]["execution_decision_id"])
            self.assertEqual(rows[1]["call_id"], "call_new")
            self.assertEqual(rows[1]["filled_count"], 1)
            self.assertEqual(rows[1]["execution_link_source"], "v2_event_store.lifecycle_events")
            self.assertEqual(rows[1]["execution_decision_id"], "decision_new")
            self.assertEqual(rows[1]["execution_event_id"], 42)
            self.assertIn("candidate_to_order_delay_min", rows[1]["entry_timing_snapshot_json"])
            self.assertIn("STABLE_READY", rows[1]["candidate_health_snapshot_json"])
            self.assertEqual(rows[1]["entry_delay_min"], 12.5)
            self.assertEqual(rows[1]["entry_price_vs_first_seen_pct"], 1.2)
            self.assertEqual(rows[1]["entry_price_vs_first_ready_pct"], -0.4)
            self.assertEqual(rows[1]["position_mfe_pct"], 2.1)
            self.assertEqual(rows[1]["position_mae_pct"], -1.3)
            self.assertEqual(rows[1]["us_early_entry_window"], "active")
            self.assertEqual(rows[1]["us_early_entry_elapsed_min"], 30.0)
            self.assertEqual(rows[1]["us_early_entry_size_mult"], 0.5)
            self.assertEqual(rows[1]["us_early_entry_confirmation_reason"], "us_early_entry_soft_size")
            self.assertIn("size_mult", rows[1]["us_early_entry_gate_json"])

    def test_analyze_candidate_audit_includes_row_uniqueness_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "logs" / "raw_calls").mkdir(parents=True)
            (root / "logs" / "funnel").mkdir(parents=True)
            (root / "logs" / "screener_quality").mkdir(parents=True)
            db_path = root / "data" / "audit" / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            for call_id, known_at in (("call_old", "2026-05-08T09:00:00"), ("call_new", "2026-05-08T09:05:00")):
                store.upsert_candidate(
                    {
                        "call_id": call_id,
                        "runtime_mode": "live",
                        "market": "US",
                        "session_date": "2026-05-08",
                        "known_at": known_at,
                        "ticker": "AAPL",
                        "classification": "ready_no_signal",
                    }
                )

            with patch(
                "tools.analyze_candidate_audit.get_runtime_path",
                side_effect=lambda *parts, **kwargs: root.joinpath(*parts),
            ):
                result = analyze_candidate_audit(
                    db_path=db_path,
                    session_date="2026-05-08",
                    market="US",
                    horizon_min=30,
                )

            self.assertEqual(
                result["row_uniqueness"],
                {
                    "call_level_rows": 2,
                    "latest_session_ticker_rows": 1,
                    "duplicate_group_count": 1,
                },
            )


if __name__ == "__main__":
    unittest.main()
