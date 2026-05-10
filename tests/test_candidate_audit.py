from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audit.candidate_audit_store import CandidateAuditStore, candidate_key
from tools.analyze_candidate_audit import analyze_candidate_audit, classify_strategy_match, watch_trigger_funnel_summary
from tools.backfill_candidate_audit import backfill_candidate_audit
from tools.update_candidate_audit_outcomes import update_candidate_audit_outcomes


class CandidateAuditBackfillTests(unittest.TestCase):
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
