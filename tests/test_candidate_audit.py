from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
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
from tools.candidate_audit_outcome_catchup import build_catchup_plan, run_catchup
from tools.update_candidate_audit_outcomes import update_candidate_audit_outcomes


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
        self.assertEqual(result["consistency"]["trade_ready_family_mismatch_count"], 1)
        self.assertEqual(result["consistency"]["invalid_price_count"], 1)

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
                    "final_prompt_included": True,
                    "raw_rank": 7,
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
                }
            )

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """
                    SELECT final_prompt_included, raw_rank, trainer_score_rank,
                           trainer_prompt_score, trainer_candidate_state,
                           trainer_score_components_json, source_tags_json,
                           candidate_quality_score, quality_data_gaps_json,
                           scorer_input_snapshot_json, scorer_config_hash,
                           stale_cycle, stale_cycle_count,
                           repeated_failed_ready_count, no_fill_cycle_count,
                           failed_ready_reasons_json
                    FROM audit_candidate_rows
                    WHERE ticker='NVDA'
                    """
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(row["final_prompt_included"], 1)
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
            summary = run_catchup(
                db_path=db_path,
                date_arg="2026-05-08",
                market="KR",
                runtime_mode="live",
                dry_run=True,
            )

        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["planned"], [{"session_date": "2026-05-08", "market": "KR"}])
        self.assertEqual(summary["results"], [])

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
            self.assertIn("route_shadow_summary", result)
            top_not_in_prompt = result["top_mfe"]["not_in_prompt"][0]
            self.assertIn("thin_price_sample", top_not_in_prompt)
            self.assertNotIn("small_sample", top_not_in_prompt)
            self.assertEqual(classify_strategy_match("momentum,gap_pullback", "momentum"), "match")
            self.assertEqual(classify_strategy_match("gap_pullback", "momentum"), "mismatch")
            self.assertEqual(result["strategy_mismatch"]["filled_strategy_rows"], 2)
            self.assertEqual(result["strategy_mismatch"]["match_count"], 1)
            self.assertEqual(result["strategy_mismatch"]["mismatch_count"], 1)

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
            self.assertEqual(result["missed_winners"][0]["miss_stage"], "claude_watch")
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
