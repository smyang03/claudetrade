from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing

from decision.registry import DecisionRegistry
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent
from tools.sync_v2_learning_performance import _apply_decision_repair, ensure_schema, sync_v2_learning_performance


class V2LearningPerformanceSyncTests(unittest.TestCase):
    def test_sync_creates_learning_row_from_v2_events_and_path_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
                strategy_hint="momentum",
                timing_style="pullback",
            )
            store.create_path_run(
                path_run_id="path_1",
                decision_id=decision_id,
                path_type="claude_price",
                market="KR",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="005930",
                status="FILLED",
                plan={"origin_action": "PULLBACK_WAIT", "buy_zone_high": 70000},
            )
            for event in (
                LifecycleEvent(
                    event_type="ORDER_SENT",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"path_run_id": "path_1", "path_type": "claude_price"},
                ),
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"price": 70000, "qty": 2, "path_run_id": "path_1", "path_type": "claude_price"},
                ),
                LifecycleEvent(
                    event_type="CLOSED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="sell1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={
                        "exit_price": 71400,
                        "qty": 2,
                        "pnl_krw": 2800,
                        "pnl_pct": 2.0,
                        "mfe_pct": 3.1,
                        "mae_pct": -0.4,
                        "close_reason": "CLOSED_CLAUDE_PRICE_TARGET",
                        "path_run_id": "path_1",
                        "path_type": "claude_price",
                    },
                ),
                LifecycleEvent(
                    event_type="FORWARD_MEASURED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"horizon": "close"},
                ),
            ):
                store.append(event)

            summary = sync_v2_learning_performance(event_db=event_db, ml_db=ml_db, market="KR", dry_run=False)

            self.assertEqual(summary["written"], 1)
            self.assertEqual(summary["filled"], 1)
            self.assertEqual(summary["closed"], 1)
            self.assertEqual(summary["learning_allowed"], 1)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM v2_learning_performance WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(row["route"], "path_b")
            self.assertEqual(row["path_run_id"], "path_1")
            self.assertEqual(row["origin_action"], "PULLBACK_WAIT")
            self.assertEqual(row["entry_price"], 70000)
            self.assertEqual(row["exit_price"], 71400)
            self.assertEqual(row["mfe_pct"], 3.1)
            self.assertEqual(row["mae_pct"], -0.4)
            self.assertEqual(row["quality_grade"], "CLEAN")
            self.assertEqual(row["learning_allowed"], 1)

    def test_sync_uses_audited_broker_native_exit_price_and_qty_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            cases = [
                ("APLD", {"exit_price_native": 45.2, "broker_sell_filled_qty": 7}, 47.3, 7, 45.2),
                (
                    "NOK",
                    {
                        "broker_sell_fill_price_native": 15.79,
                        "broker_sell_filled_qty": 18,
                        "broker_sell_remaining_qty": 0,
                    },
                    16.11,
                    18,
                    15.79,
                ),
            ]
            decision_ids: dict[str, str] = {}
            for ticker, close_payload, entry_price, qty, _exit_price in cases:
                decision_id = registry.register_trade_ready(
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-26",
                    ticker=ticker,
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    strategy_hint="claude_price",
                )
                decision_ids[ticker] = decision_id
                for event in (
                    LifecycleEvent(
                        event_type="ORDER_SENT",
                        market="US",
                        runtime_mode="live",
                        session_date="2026-05-26",
                        ticker=ticker,
                        decision_id=decision_id,
                        execution_id=f"buy_{ticker}",
                        prompt_version="v2",
                        brain_snapshot_id="brain_us",
                        payload={"price": entry_price, "qty": qty},
                    ),
                    LifecycleEvent(
                        event_type="FILLED",
                        market="US",
                        runtime_mode="live",
                        session_date="2026-05-26",
                        ticker=ticker,
                        decision_id=decision_id,
                        execution_id=f"buy_{ticker}",
                        prompt_version="v2",
                        brain_snapshot_id="brain_us",
                        payload={"price": entry_price, "qty": qty},
                    ),
                    LifecycleEvent(
                        event_type="CLOSED",
                        market="US",
                        runtime_mode="live",
                        session_date="2026-05-26",
                        ticker=ticker,
                        decision_id=decision_id,
                        execution_id=f"sell_{ticker}",
                        prompt_version="v2",
                        brain_snapshot_id="brain_us",
                        reason_code="CLOSED_AUDITED_BROKER_SELL",
                        payload={
                            **close_payload,
                            "close_reason": "CLOSED_AUDITED_BROKER_SELL",
                            "pnl_pct": -1.0,
                        },
                    ),
                ):
                    store.append(event)

            summary = sync_v2_learning_performance(event_db=event_db, ml_db=ml_db, market="US", dry_run=False)

            self.assertEqual(summary["strategy_attribution_counts"]["audited_broker_backfill"], 2)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                rows = {
                    row["ticker"]: row
                    for row in conn.execute(
                        """
                        SELECT ticker, exit_price, qty, pnl_krw, portfolio_realized,
                               strategy_attribution, learning_allowed
                        FROM v2_learning_performance
                        WHERE v2_decision_id IN (?, ?)
                        """,
                        (decision_ids["APLD"], decision_ids["NOK"]),
                    ).fetchall()
                }

            self.assertEqual(rows["APLD"]["exit_price"], 45.2)
            self.assertEqual(rows["APLD"]["qty"], 7)
            self.assertEqual(rows["NOK"]["exit_price"], 15.79)
            self.assertEqual(rows["NOK"]["qty"], 18)
            for row in rows.values():
                self.assertIsNone(row["pnl_krw"])
                self.assertEqual(row["portfolio_realized"], 1)
                self.assertEqual(row["strategy_attribution"], "audited_broker_backfill")
                self.assertEqual(row["learning_allowed"], 0)

    def test_sync_marks_price_without_qty_as_degraded_and_blocks_learning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="US",
                runtime_mode="live",
                session_date="2026-05-26",
                ticker="MISSQ",
                prompt_version="v2",
                brain_snapshot_id="brain_us",
                strategy_hint="momentum",
            )
            for event in (
                LifecycleEvent(
                    event_type="ORDER_SENT",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-26",
                    ticker="MISSQ",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"price": 100.0, "qty": 1},
                ),
                LifecycleEvent(
                    event_type="FILLED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-26",
                    ticker="MISSQ",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"price": 100.0, "qty": 1},
                ),
                LifecycleEvent(
                    event_type="CLOSED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-26",
                    ticker="MISSQ",
                    decision_id=decision_id,
                    execution_id="sell1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"exit_price_native": 101.0, "pnl_pct": 1.0},
                ),
                LifecycleEvent(
                    event_type="FORWARD_MEASURED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-26",
                    ticker="MISSQ",
                    decision_id=decision_id,
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"complete": True},
                ),
            ):
                store.append(event)

            summary = sync_v2_learning_performance(event_db=event_db, ml_db=ml_db, market="US", dry_run=False)

            self.assertEqual(summary["degraded"], 1)
            self.assertEqual(summary["degraded_reason_counts"], {"MISSING_CLOSE_QTY": 1})
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT exit_price, qty, learning_allowed, quality_reasons_json FROM v2_learning_performance WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()

            self.assertEqual(row["exit_price"], 101.0)
            self.assertEqual(row["qty"], 1)
            self.assertEqual(row["learning_allowed"], 0)
            self.assertIn("MISSING_CLOSE_QTY", json.loads(row["quality_reasons_json"]))

    def test_sync_reports_unchanged_on_semantic_rerun_without_duplicate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
                strategy_hint="momentum",
            )
            for event in (
                LifecycleEvent(
                    event_type="ORDER_SENT",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"price": 70000, "qty": 2},
                ),
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"price": 70000, "qty": 2},
                ),
                LifecycleEvent(
                    event_type="CLOSED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="sell1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"exit_price": 71400, "qty": 2, "pnl_pct": 2.0},
                ),
                LifecycleEvent(
                    event_type="FORWARD_MEASURED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"complete": True},
                ),
            ):
                store.append(event)

            first = sync_v2_learning_performance(event_db=event_db, ml_db=ml_db, market="KR", dry_run=False)
            second = sync_v2_learning_performance(event_db=event_db, ml_db=ml_db, market="KR", dry_run=True)

            self.assertEqual(first["insert"], 1)
            self.assertEqual(second["insert"], 0)
            self.assertEqual(second["update"], 0)
            self.assertEqual(second["unchanged"], 1)
            self.assertIn("skipped", second)
            with closing(sqlite3.connect(ml_db)) as conn:
                count = conn.execute("SELECT COUNT(*) FROM v2_learning_performance WHERE v2_decision_id=?", (decision_id,)).fetchone()[0]
            self.assertEqual(count, 1)

    def test_sync_separates_discovery_origin_performance_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            ids = registry.register_trade_ready_batch(
                market="US",
                runtime_mode="live",
                session_date="2026-05-28",
                tickers=["DISC"],
                prompt_version="v2",
                brain_snapshot_id="brain_us",
                selection_meta={
                    "trade_ready": ["DISC"],
                    "_final_prompt_pool": [
                        {
                            "ticker": "DISC",
                            "candidate_pool_role": "DISCOVERY",
                            "discovery_action_ceiling": "WATCH",
                            "discovery_signal_family": "near_breakout,momentum_now",
                            "discovery_reason": "core_cap_signal_candidate",
                            "discovery_overlay_rank": 1,
                        }
                    ],
                    "_discovery_role_by_ticker": {"DISC": "DISCOVERY"},
                    "_discovery_action_ceiling_by_ticker": {"DISC": "WATCH"},
                },
            )
            decision_id = ids["DISC"]
            for event in (
                LifecycleEvent(
                    event_type="FILLED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-28",
                    ticker="DISC",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"price": 40.0, "qty": 3, "entry_route": "PlanA.buy"},
                ),
                LifecycleEvent(
                    event_type="CLOSED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-28",
                    ticker="DISC",
                    decision_id=decision_id,
                    execution_id="sell1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"price": 42.0, "pnl_pct": 5.0},
                ),
            ):
                store.append(event)

            summary = sync_v2_learning_performance(event_db=event_db, ml_db=ml_db, market="US", dry_run=False)

            self.assertEqual(summary["experiment_bucket_counts"], {"discovery_live": 1})
            self.assertEqual(summary["discovery_live_experiment"], 1)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                learning = conn.execute(
                    "SELECT * FROM v2_learning_performance WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()
                canonical = conn.execute(
                    "SELECT * FROM v2_canonical_performance WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()

            for row in (learning, canonical):
                self.assertIsNotNone(row)
                self.assertEqual(row["candidate_pool_role"], "DISCOVERY")
                self.assertEqual(row["experiment_bucket"], "discovery_live")
                self.assertEqual(row["discovery_live_experiment"], 1)
                self.assertEqual(row["discovery_action_ceiling"], "WATCH")
                self.assertEqual(row["discovery_signal_family"], "near_breakout,momentum_now")
                self.assertEqual(row["discovery_reason"], "core_cap_signal_candidate")
                self.assertEqual(row["discovery_overlay_rank"], 1)

    def test_ensure_schema_adds_experiment_columns_before_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ml_db = Path(tmp) / "decisions.db"
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute(
                    """
                    CREATE TABLE v2_learning_performance (
                        v2_decision_id TEXT PRIMARY KEY,
                        market TEXT,
                        runtime_mode TEXT,
                        session_date TEXT,
                        ticker TEXT,
                        filled INTEGER,
                        closed INTEGER,
                        learning_allowed INTEGER,
                        quality_grade TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE v2_canonical_performance (
                        v2_decision_id TEXT PRIMARY KEY,
                        market TEXT,
                        runtime_mode TEXT,
                        session_date TEXT,
                        ticker TEXT,
                        filled INTEGER,
                        closed INTEGER,
                        learning_allowed INTEGER,
                        quality_grade TEXT
                    )
                    """
                )

                ensure_schema(conn)

                learning_columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(v2_learning_performance)").fetchall()
                }
                canonical_columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(v2_canonical_performance)").fetchall()
                }
                learning_index = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_v2_learning_perf_experiment'"
                ).fetchone()
                canonical_index = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_v2_canonical_perf_experiment'"
                ).fetchone()

            self.assertIn("experiment_bucket", learning_columns)
            self.assertIn("candidate_pool_role", learning_columns)
            self.assertIn("experiment_bucket", canonical_columns)
            self.assertIn("candidate_pool_role", canonical_columns)
            self.assertIsNotNone(learning_index)
            self.assertIsNotNone(canonical_index)

    def test_sync_prefers_fill_payload_path_run_over_first_registered_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="US",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="AAPL",
                prompt_version="v2",
                brain_snapshot_id="brain_us",
            )
            store.create_path_run(
                path_run_id="path_old",
                decision_id=decision_id,
                path_type="claude_price",
                market="US",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="AAPL",
                status="CANCELLED",
                plan={"origin_action": "OLD_WAIT"},
            )
            store.create_path_run(
                path_run_id="path_filled",
                decision_id=decision_id,
                path_type="claude_price",
                market="US",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="AAPL",
                status="FILLED",
                plan={"origin_action": "PULLBACK_WAIT"},
            )
            for event in (
                LifecycleEvent(
                    event_type="FILLED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="AAPL",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"price": 100.0, "qty": 1, "path_run_id": "path_filled", "path_type": "claude_price"},
                ),
                LifecycleEvent(
                    event_type="CLOSED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="AAPL",
                    decision_id=decision_id,
                    execution_id="sell1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"price": 101.0, "pnl_pct": 1.0, "path_run_id": "path_filled", "path_type": "claude_price"},
                ),
                LifecycleEvent(
                    event_type="FORWARD_MEASURED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="AAPL",
                    decision_id=decision_id,
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"horizon": "close"},
                ),
            ):
                store.append(event)

            sync_v2_learning_performance(event_db=event_db, ml_db=ml_db, market="US", dry_run=False)

            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT path_run_id, origin_action FROM v2_learning_performance WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()

            self.assertEqual(row["path_run_id"], "path_filled")
            self.assertEqual(row["origin_action"], "PULLBACK_WAIT")

    def test_sync_prefers_close_payload_path_run_over_later_cancelled_run_when_fill_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="US",
                runtime_mode="live",
                session_date="2026-05-29",
                ticker="SMCI",
                prompt_version="v2",
                brain_snapshot_id="brain_us",
            )
            store.create_path_run(
                path_run_id="path_closed",
                decision_id=decision_id,
                path_type="claude_price",
                market="US",
                runtime_mode="live",
                session_date="2026-05-29",
                ticker="SMCI",
                status="CLOSED",
                plan={"origin_action": "PULLBACK_WAIT"},
            )
            store.create_path_run(
                path_run_id="path_later_cancelled",
                decision_id=decision_id,
                path_type="claude_price",
                market="US",
                runtime_mode="live",
                session_date="2026-05-29",
                ticker="SMCI",
                status="CANCELLED",
                plan={"origin_action": "REENTRY_WAIT"},
            )
            for event in (
                LifecycleEvent(
                    event_type="ORDER_SENT",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-29",
                    ticker="SMCI",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"price": 45.7, "qty": 3, "path_run_id": "path_closed", "path_type": "claude_price", "side": "buy"},
                ),
                LifecycleEvent(
                    event_type="CLOSED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-29",
                    ticker="SMCI",
                    decision_id=decision_id,
                    execution_id="sell1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"price": 47.24, "pnl_pct": 3.36, "path_run_id": "path_closed", "path_type": "claude_price"},
                ),
                LifecycleEvent(
                    event_type="CLAUDE_PRICE_CANCELLED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-29",
                    ticker="SMCI",
                    decision_id=decision_id,
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"path_run_id": "path_later_cancelled", "path_type": "claude_price"},
                ),
            ):
                store.append(event)

            sync_v2_learning_performance(event_db=event_db, ml_db=ml_db, market="US", dry_run=False)

            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT path_run_id, origin_action, filled, closed FROM v2_learning_performance WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()

            self.assertEqual(row["path_run_id"], "path_closed")
            self.assertEqual(row["origin_action"], "PULLBACK_WAIT")
            self.assertEqual(row["filled"], 0)
            self.assertEqual(row["closed"], 1)

    def test_sync_writes_canonical_performance_with_event_dedupe_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="US",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="AAPL",
                prompt_version="v2",
                brain_snapshot_id="brain_us",
            )
            for event in (
                LifecycleEvent(
                    event_type="FILLED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="AAPL",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    occurred_at="2026-05-08T13:31:00+00:00",
                    payload={"price": 100.0, "qty": 1},
                ),
                LifecycleEvent(
                    event_type="FILLED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="AAPL",
                    decision_id=decision_id,
                    execution_id="buy1-dup",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    occurred_at="2026-05-08T13:32:00+00:00",
                    payload={"price": 100.5, "qty": 1},
                ),
                LifecycleEvent(
                    event_type="CLOSED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="AAPL",
                    decision_id=decision_id,
                    execution_id="sell1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    occurred_at="2026-05-08T14:00:00+00:00",
                    payload={"price": 101.0, "pnl_pct": 1.0},
                ),
                LifecycleEvent(
                    event_type="CLOSED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="AAPL",
                    decision_id=decision_id,
                    execution_id="sell2",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    occurred_at="2026-05-08T14:10:00+00:00",
                    payload={"price": 102.0, "pnl_pct": 2.0},
                ),
            ):
                store.append(event)

            summary = sync_v2_learning_performance(event_db=event_db, ml_db=ml_db, market="US", dry_run=False)

            self.assertEqual(summary["canonical_written"], 1)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM v2_canonical_performance WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(row["raw_fill_event_count"], 2)
            self.assertEqual(row["raw_close_event_count"], 2)
            self.assertEqual(row["earliest_fill_at"], "2026-05-08T13:31:00+00:00")
            self.assertEqual(row["first_closed_at"], "2026-05-08T14:00:00+00:00")
            self.assertEqual(row["last_closed_at"], "2026-05-08T14:10:00+00:00")
            self.assertEqual(row["entry_price"], 100.0)
            self.assertEqual(row["first_exit_price"], 101.0)
            self.assertEqual(row["last_exit_price"], 102.0)
            contract = json.loads(row["metric_contract_json"])
            self.assertEqual(contract["dedupe_axis"], "v2_decision_id_market_session_ticker")

    def test_sync_repairs_legacy_decisions_fill_from_canonical_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
            )
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.execute(
                    """
                    CREATE TABLE decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market TEXT,
                        ticker TEXT,
                        session_date TEXT,
                        decision TEXT,
                        filled INTEGER DEFAULT 0,
                        order_status TEXT,
                        entry_price REAL,
                        exit_price REAL,
                        pnl_pct REAL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        market, ticker, session_date, decision, filled, order_status
                    ) VALUES ('KR', '005930', '2026-05-08', 'BUY_SIGNAL', 0, '')
                    """
                )
                conn.commit()
            for event in (
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"price": 70000, "qty": 2},
                ),
                LifecycleEvent(
                    event_type="CLOSED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="sell1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"exit_price": 71400, "pnl_pct": 2.0},
                ),
            ):
                store.append(event)

            summary = sync_v2_learning_performance(
                event_db=event_db,
                ml_db=ml_db,
                market="KR",
                dry_run=False,
                repair_decisions=True,
            )

            self.assertEqual(summary["decision_links_matched"], 1)
            self.assertEqual(summary["decision_links_repaired"], 1)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                decision = conn.execute("SELECT * FROM decisions WHERE id=1").fetchone()
                link = conn.execute(
                    "SELECT * FROM v2_decision_fill_links WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()

            self.assertEqual(decision["filled"], 1)
            self.assertEqual(decision["order_status"], "FILLED")
            self.assertEqual(decision["entry_price"], 70000)
            self.assertEqual(decision["exit_price"], 71400)
            self.assertEqual(decision["pnl_pct"], 2.0)
            self.assertEqual(link["link_status"], "MATCHED")
            self.assertEqual(link["legacy_filled_before"], 0)
            self.assertEqual(link["legacy_filled_after"], 1)

    def test_sync_does_not_count_repair_when_legacy_values_already_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
            )
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.execute(
                    """
                    CREATE TABLE decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market TEXT,
                        ticker TEXT,
                        session_date TEXT,
                        decision TEXT,
                        filled INTEGER DEFAULT 0,
                        order_status TEXT,
                        entry_price REAL,
                        exit_price REAL,
                        pnl_pct REAL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        market, ticker, session_date, decision, filled, order_status,
                        entry_price, exit_price, pnl_pct
                    ) VALUES ('KR', '005930', '2026-05-08', 'BUY_SIGNAL', 1, 'FILLED',
                              70000, 71400, 2.0)
                    """
                )
                conn.commit()
            for event in (
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"price": 70000, "qty": 2},
                ),
                LifecycleEvent(
                    event_type="CLOSED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="sell1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"exit_price": 71400, "pnl_pct": 2.0},
                ),
            ):
                store.append(event)

            summary = sync_v2_learning_performance(
                event_db=event_db,
                ml_db=ml_db,
                market="KR",
                dry_run=False,
                repair_decisions=True,
            )

            self.assertEqual(summary["decision_links_matched"], 1)
            self.assertEqual(summary["decision_links_repaired"], 0)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                link = conn.execute(
                    "SELECT * FROM v2_decision_fill_links WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()

            self.assertEqual(link["link_status"], "MATCHED")
            self.assertEqual(link["repaired"], 0)
            self.assertEqual(link["legacy_filled_before"], 1)
            self.assertEqual(link["legacy_filled_after"], 1)

    def test_sync_does_not_repair_simulated_only_legacy_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
            )
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.execute(
                    """
                    CREATE TABLE decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market TEXT,
                        ticker TEXT,
                        session_date TEXT,
                        decision TEXT,
                        filled INTEGER DEFAULT 0,
                        order_status TEXT,
                        entry_price REAL,
                        is_simulated INTEGER,
                        data_source TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        market, ticker, session_date, decision, filled, order_status,
                        entry_price, is_simulated, data_source
                    ) VALUES ('KR', '005930', '2026-05-08', 'BUY_SIGNAL', 0, '',
                              NULL, 1, 'backfill')
                    """
                )
                conn.commit()
            store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"price": 70000, "qty": 1},
                )
            )

            summary = sync_v2_learning_performance(
                event_db=event_db,
                ml_db=ml_db,
                market="KR",
                dry_run=False,
                repair_decisions=True,
            )

            self.assertEqual(summary["decision_links_matched"], 0)
            self.assertEqual(summary["decision_links_repaired"], 0)
            self.assertEqual(summary["decision_links_unmatched"], 1)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                decision = conn.execute("SELECT * FROM decisions WHERE id=1").fetchone()
                link = conn.execute(
                    "SELECT * FROM v2_decision_fill_links WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()

            self.assertEqual(decision["filled"], 0)
            self.assertEqual(decision["order_status"], "")
            self.assertIsNone(decision["entry_price"])
            self.assertEqual(decision["is_simulated"], 1)
            self.assertEqual(decision["data_source"], "backfill")
            self.assertEqual(link["link_status"], "UNMATCHED_NO_LIVE_ROW")
            self.assertIsNone(link["legacy_decision_id"])
            self.assertEqual(link["repaired"], 0)
            self.assertEqual(link["unmatched_reason"], "simulated_legacy_rows_excluded")

    def test_sync_repairs_live_legacy_decision_when_simulated_row_also_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
            )
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.execute(
                    """
                    CREATE TABLE decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market TEXT,
                        ticker TEXT,
                        session_date TEXT,
                        decision TEXT,
                        filled INTEGER DEFAULT 0,
                        order_status TEXT,
                        entry_price REAL,
                        is_simulated INTEGER,
                        data_source TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        market, ticker, session_date, decision, filled, order_status,
                        entry_price, is_simulated, data_source
                    ) VALUES ('KR', '005930', '2026-05-08', 'BUY_SIGNAL', 0, '',
                              NULL, 1, 'backfill')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        market, ticker, session_date, decision, filled, order_status,
                        entry_price, is_simulated, data_source
                    ) VALUES ('KR', '005930', '2026-05-08', 'BUY_SIGNAL', 0, '',
                              NULL, 0, 'live')
                    """
                )
                live_id = int(conn.execute("SELECT id FROM decisions WHERE data_source='live'").fetchone()[0])
                conn.commit()
            store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"price": 70000, "qty": 1},
                )
            )

            summary = sync_v2_learning_performance(
                event_db=event_db,
                ml_db=ml_db,
                market="KR",
                dry_run=False,
                repair_decisions=True,
            )

            self.assertEqual(summary["decision_links_matched"], 1)
            self.assertEqual(summary["decision_links_repaired"], 1)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                simulated = conn.execute("SELECT * FROM decisions WHERE data_source='backfill'").fetchone()
                live = conn.execute("SELECT * FROM decisions WHERE data_source='live'").fetchone()
                link = conn.execute(
                    "SELECT * FROM v2_decision_fill_links WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()

            self.assertEqual(simulated["filled"], 0)
            self.assertEqual(simulated["order_status"], "")
            self.assertIsNone(simulated["entry_price"])
            self.assertEqual(live["filled"], 1)
            self.assertEqual(live["order_status"], "FILLED")
            self.assertEqual(live["entry_price"], 70000)
            self.assertEqual(link["link_status"], "MATCHED")
            self.assertEqual(link["legacy_decision_id"], live_id)
            self.assertEqual(link["repaired"], 1)

    def test_sync_keeps_legacy_lookup_for_decisions_schema_without_simulated_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
            )
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.execute(
                    """
                    CREATE TABLE decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market TEXT,
                        ticker TEXT,
                        session_date TEXT,
                        decision TEXT,
                        filled INTEGER DEFAULT 0,
                        order_status TEXT,
                        entry_price REAL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        market, ticker, session_date, decision, filled, order_status,
                        entry_price
                    ) VALUES ('KR', '005930', '2026-05-08', 'BUY_SIGNAL', 0, '', NULL)
                    """
                )
                conn.commit()
            store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"price": 70000, "qty": 1},
                )
            )

            summary = sync_v2_learning_performance(
                event_db=event_db,
                ml_db=ml_db,
                market="KR",
                dry_run=False,
                repair_decisions=True,
            )

            self.assertEqual(summary["decision_links_matched"], 1)
            self.assertEqual(summary["decision_links_repaired"], 1)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                decision = conn.execute("SELECT * FROM decisions WHERE id=1").fetchone()

            self.assertEqual(decision["filled"], 1)
            self.assertEqual(decision["order_status"], "FILLED")
            self.assertEqual(decision["entry_price"], 70000)

    def test_apply_decision_repair_skips_simulated_legacy_row_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ml_db = Path(tmp) / "decisions.db"
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute(
                    """
                    CREATE TABLE decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filled INTEGER DEFAULT 0,
                        order_status TEXT,
                        entry_price REAL,
                        is_simulated INTEGER,
                        data_source TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        filled, order_status, entry_price, is_simulated, data_source
                    ) VALUES (0, '', NULL, 1, 'backfill')
                    """
                )
                link_row = {
                    "link_status": "MATCHED",
                    "matched_by": "payload_legacy_decision_id",
                    "legacy_decision_id": 1,
                    "legacy_filled_before": 0,
                    "legacy_filled_after": 0,
                    "legacy_order_status_before": "",
                    "legacy_order_status_after": "",
                    "repaired": 0,
                    "unmatched_reason": "",
                }

                updated_link = _apply_decision_repair(
                    conn,
                    {"filled": 1, "entry_price": 70000},
                    link_row,
                )
                decision = conn.execute("SELECT * FROM decisions WHERE id=1").fetchone()

            self.assertEqual(decision["filled"], 0)
            self.assertEqual(decision["order_status"], "")
            self.assertIsNone(decision["entry_price"])
            self.assertEqual(updated_link["link_status"], "REPAIR_SKIPPED_SIMULATED")
            self.assertEqual(updated_link["matched_by"], "")
            self.assertEqual(updated_link["legacy_filled_after"], 0)
            self.assertEqual(updated_link["legacy_order_status_after"], "")
            self.assertEqual(updated_link["repaired"], 0)
            self.assertEqual(updated_link["unmatched_reason"], "simulated_legacy_row_excluded_at_repair")

    def test_sync_excludes_payload_simulated_legacy_decision_id_from_link_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
            )
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.execute(
                    """
                    CREATE TABLE decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market TEXT,
                        ticker TEXT,
                        session_date TEXT,
                        decision TEXT,
                        filled INTEGER DEFAULT 0,
                        order_status TEXT,
                        entry_price REAL,
                        is_simulated INTEGER,
                        data_source TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        market, ticker, session_date, decision, filled, order_status,
                        entry_price, is_simulated, data_source
                    ) VALUES ('KR', '005930', '2026-05-08', 'BUY_SIGNAL', 0, '',
                              NULL, 1, 'backfill')
                    """
                )
                conn.commit()
            store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"price": 70000, "qty": 1, "legacy_decision_id": 1},
                )
            )

            summary = sync_v2_learning_performance(
                event_db=event_db,
                ml_db=ml_db,
                market="KR",
                dry_run=False,
                repair_decisions=False,
            )

            self.assertEqual(summary["decision_links_matched"], 0)
            self.assertEqual(summary["decision_links_repaired"], 0)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                decision = conn.execute("SELECT * FROM decisions WHERE id=1").fetchone()
                link = conn.execute(
                    "SELECT * FROM v2_decision_fill_links WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()

            self.assertEqual(decision["filled"], 0)
            self.assertEqual(link["link_status"], "UNMATCHED_NO_LIVE_ROW")
            self.assertIsNone(link["legacy_decision_id"])
            self.assertEqual(link["unmatched_reason"], "payload_simulated_legacy_row_excluded")

    def test_sync_excludes_payload_legacy_decision_id_with_scope_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
            )
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.execute(
                    """
                    CREATE TABLE decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market TEXT,
                        ticker TEXT,
                        session_date TEXT,
                        decision TEXT,
                        filled INTEGER DEFAULT 0,
                        order_status TEXT,
                        entry_price REAL,
                        is_simulated INTEGER,
                        data_source TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        market, ticker, session_date, decision, filled, order_status,
                        entry_price, is_simulated, data_source
                    ) VALUES ('US', 'AAPL', '2026-05-08', 'BUY_SIGNAL', 0, '',
                              NULL, 0, 'live')
                    """
                )
                conn.commit()
            store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"price": 70000, "qty": 1, "legacy_decision_id": 1},
                )
            )

            summary = sync_v2_learning_performance(
                event_db=event_db,
                ml_db=ml_db,
                market="KR",
                dry_run=False,
                repair_decisions=True,
            )

            self.assertEqual(summary["decision_links_matched"], 0)
            self.assertEqual(summary["decision_links_repaired"], 0)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                decision = conn.execute("SELECT * FROM decisions WHERE id=1").fetchone()
                link = conn.execute(
                    "SELECT * FROM v2_decision_fill_links WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()

            self.assertEqual(decision["filled"], 0)
            self.assertEqual(decision["order_status"], "")
            self.assertIsNone(decision["entry_price"])
            self.assertEqual(link["link_status"], "PAYLOAD_LEGACY_MISMATCH")
            self.assertIsNone(link["legacy_decision_id"])
            self.assertEqual(link["unmatched_reason"], "payload_legacy_market_mismatch")

    def test_apply_decision_repair_skips_legacy_row_scope_mismatch_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ml_db = Path(tmp) / "decisions.db"
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute(
                    """
                    CREATE TABLE decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market TEXT,
                        ticker TEXT,
                        session_date TEXT,
                        filled INTEGER DEFAULT 0,
                        order_status TEXT,
                        entry_price REAL,
                        is_simulated INTEGER,
                        data_source TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        market, ticker, session_date, filled, order_status,
                        entry_price, is_simulated, data_source
                    ) VALUES ('US', 'AAPL', '2026-05-08', 0, '', NULL, 0, 'live')
                    """
                )
                link_row = {
                    "link_status": "MATCHED",
                    "matched_by": "payload_legacy_decision_id",
                    "legacy_decision_id": 1,
                    "legacy_filled_before": 0,
                    "legacy_filled_after": 0,
                    "legacy_order_status_before": "",
                    "legacy_order_status_after": "",
                    "repaired": 0,
                    "unmatched_reason": "",
                }

                updated_link = _apply_decision_repair(
                    conn,
                    {
                        "filled": 1,
                        "market": "KR",
                        "ticker": "005930",
                        "session_date": "2026-05-08",
                        "entry_price": 70000,
                    },
                    link_row,
                )
                decision = conn.execute("SELECT * FROM decisions WHERE id=1").fetchone()

            self.assertEqual(decision["filled"], 0)
            self.assertEqual(decision["order_status"], "")
            self.assertIsNone(decision["entry_price"])
            self.assertEqual(updated_link["link_status"], "REPAIR_SKIPPED_SCOPE_MISMATCH")
            self.assertEqual(updated_link["matched_by"], "")
            self.assertEqual(updated_link["legacy_filled_after"], 0)
            self.assertEqual(updated_link["legacy_order_status_after"], "")
            self.assertEqual(updated_link["repaired"], 0)
            self.assertEqual(updated_link["unmatched_reason"], "payload_legacy_market_mismatch")

    def test_sync_does_not_match_legacy_decision_when_canonical_has_no_fill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            store.create_decision(
                decision_id="v2_unfilled",
                market="KR",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
                status="CLAUDE_TRADE_READY",
            )
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.execute(
                    """
                    CREATE TABLE decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market TEXT,
                        ticker TEXT,
                        session_date TEXT,
                        decision TEXT,
                        filled INTEGER DEFAULT 0,
                        order_status TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        market, ticker, session_date, decision, filled, order_status
                    ) VALUES ('KR', '005930', '2026-05-08', 'BUY_SIGNAL', 0, '')
                    """
                )
                conn.commit()

            summary = sync_v2_learning_performance(
                event_db=event_db,
                ml_db=ml_db,
                market="KR",
                dry_run=False,
                repair_decisions=True,
            )

            self.assertEqual(summary["decision_links_matched"], 0)
            self.assertEqual(summary["decision_links_repaired"], 0)
            self.assertEqual(summary["decision_links_unmatched"], 1)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                link = conn.execute("SELECT * FROM v2_decision_fill_links WHERE v2_decision_id='v2_unfilled'").fetchone()

            self.assertEqual(link["link_status"], "NO_CANONICAL_FILL")
            self.assertIsNone(link["legacy_decision_id"])
            self.assertEqual(link["filled_from_canonical"], 0)
            self.assertEqual(link["unmatched_reason"], "canonical_has_no_fill")

    def test_sync_does_not_let_unfilled_row_create_shared_legacy_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            filled_decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
            )
            store.create_decision(
                decision_id="v2_unfilled",
                market="KR",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
                status="CLAUDE_TRADE_READY",
            )
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.execute(
                    """
                    CREATE TABLE decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market TEXT,
                        ticker TEXT,
                        session_date TEXT,
                        decision TEXT,
                        filled INTEGER DEFAULT 0,
                        order_status TEXT,
                        entry_price REAL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        market, ticker, session_date, decision, filled, order_status
                    ) VALUES ('KR', '005930', '2026-05-08', 'BUY_SIGNAL', 0, '')
                    """
                )
                conn.commit()
            store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=filled_decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"price": 70000, "qty": 1},
                )
            )

            summary = sync_v2_learning_performance(
                event_db=event_db,
                ml_db=ml_db,
                market="KR",
                dry_run=False,
                repair_decisions=True,
            )

            self.assertEqual(summary["decision_links_matched"], 1)
            self.assertEqual(summary["decision_links_repaired"], 1)
            self.assertEqual(summary["decision_links_unmatched"], 1)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                links = conn.execute(
                    "SELECT v2_decision_id, link_status, legacy_decision_id, unmatched_reason "
                    "FROM v2_decision_fill_links ORDER BY v2_decision_id"
                ).fetchall()

            by_id = {row["v2_decision_id"]: row for row in links}
            self.assertEqual(by_id[filled_decision_id]["link_status"], "MATCHED")
            self.assertEqual(by_id[filled_decision_id]["legacy_decision_id"], 1)
            self.assertEqual(by_id["v2_unfilled"]["link_status"], "NO_CANONICAL_FILL")
            self.assertIsNone(by_id["v2_unfilled"]["legacy_decision_id"])
            self.assertEqual(by_id["v2_unfilled"]["unmatched_reason"], "canonical_has_no_fill")

    def test_sync_marks_shared_legacy_decision_as_ambiguous_before_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            decision_ids = ["v2_decision_a", "v2_decision_b"]
            for decision_id in decision_ids:
                store.create_decision(
                    decision_id=decision_id,
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    status="CLAUDE_TRADE_READY",
                )
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.execute(
                    """
                    CREATE TABLE decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market TEXT,
                        ticker TEXT,
                        session_date TEXT,
                        decision TEXT,
                        filled INTEGER DEFAULT 0,
                        order_status TEXT,
                        entry_price REAL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        market, ticker, session_date, decision, filled, order_status
                    ) VALUES ('KR', '005930', '2026-05-08', 'BUY_SIGNAL', 0, '')
                    """
                )
                conn.commit()
            for index, decision_id in enumerate(decision_ids):
                store.append(
                    LifecycleEvent(
                        event_type="FILLED",
                        market="KR",
                        runtime_mode="live",
                        session_date="2026-05-08",
                        ticker="005930",
                        decision_id=decision_id,
                        execution_id=f"buy{index}",
                        prompt_version="v2",
                        brain_snapshot_id="brain_kr",
                        payload={"price": 70000 + index, "qty": 1},
                    )
                )

            summary = sync_v2_learning_performance(
                event_db=event_db,
                ml_db=ml_db,
                market="KR",
                dry_run=False,
                repair_decisions=True,
            )

            self.assertEqual(summary["decision_links_matched"], 0)
            self.assertEqual(summary["decision_links_repaired"], 0)
            self.assertEqual(summary["decision_links_unmatched"], 2)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                decision = conn.execute("SELECT * FROM decisions WHERE id=1").fetchone()
                links = conn.execute(
                    "SELECT link_status, legacy_decision_id, repaired, unmatched_reason "
                    "FROM v2_decision_fill_links ORDER BY v2_decision_id"
                ).fetchall()

            self.assertEqual(decision["filled"], 0)
            self.assertEqual([row["link_status"] for row in links], ["AMBIGUOUS_SHARED_LEGACY", "AMBIGUOUS_SHARED_LEGACY"])
            self.assertEqual([row["legacy_decision_id"] for row in links], [1, 1])
            self.assertEqual([row["repaired"] for row in links], [0, 0])
            self.assertEqual([row["unmatched_reason"] for row in links], ["shared_legacy_decision_id", "shared_legacy_decision_id"])

    def test_sync_uses_fill_price_native_for_entry_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="US",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="STM",
                prompt_version="v2",
                brain_snapshot_id="brain_us",
            )
            for event in (
                LifecycleEvent(
                    event_type="ORDER_SENT",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="STM",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                ),
                LifecycleEvent(
                    event_type="FILLED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="STM",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"fill_price_native": 61.35, "qty": 1},
                ),
                LifecycleEvent(
                    event_type="FORWARD_MEASURED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="STM",
                    decision_id=decision_id,
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"horizon": "close"},
                ),
            ):
                store.append(event)

            sync_v2_learning_performance(event_db=event_db, ml_db=ml_db, market="US", dry_run=False)

            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT entry_price FROM v2_learning_performance WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(row["entry_price"], 61.35)

    def test_sync_blocks_paper_rows_from_learning_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="US",
                runtime_mode="paper",
                session_date="2026-05-08",
                ticker="AAPL",
                prompt_version="v2",
                brain_snapshot_id="brain_us",
            )
            for event in (
                LifecycleEvent(
                    event_type="ORDER_SENT",
                    market="US",
                    runtime_mode="paper",
                    session_date="2026-05-08",
                    ticker="AAPL",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                ),
                LifecycleEvent(
                    event_type="FILLED",
                    market="US",
                    runtime_mode="paper",
                    session_date="2026-05-08",
                    ticker="AAPL",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"price": 200.0, "qty": 1},
                ),
                LifecycleEvent(
                    event_type="FORWARD_MEASURED",
                    market="US",
                    runtime_mode="paper",
                    session_date="2026-05-08",
                    ticker="AAPL",
                    decision_id=decision_id,
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"horizon": "close"},
                ),
            ):
                store.append(event)

            summary = sync_v2_learning_performance(
                event_db=event_db,
                ml_db=ml_db,
                market="US",
                runtime_mode="paper",
                dry_run=False,
            )

            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT quality_grade, learning_allowed FROM v2_learning_performance WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()

            self.assertEqual(summary["learning_allowed"], 0)
            self.assertIsNotNone(row)
            self.assertEqual(row["quality_grade"], "CLEAN")
            self.assertEqual(row["learning_allowed"], 0)

    def test_sync_quality_recalculation_overrides_provisional_quality_mark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="US",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="MSFT",
                prompt_version="v2",
                brain_snapshot_id="brain_us",
            )
            for event in (
                LifecycleEvent(
                    event_type="ORDER_SENT",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="MSFT",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                ),
                LifecycleEvent(
                    event_type="FILLED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="MSFT",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"price": 200.0, "qty": 1},
                ),
                LifecycleEvent(
                    event_type="CLOSED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="MSFT",
                    decision_id=decision_id,
                    execution_id="sell1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"price": 202.0, "qty": 1, "pnl_pct": 1.0},
                ),
                LifecycleEvent(
                    event_type="QUALITY_MARKED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="MSFT",
                    decision_id=decision_id,
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    data_quality="LEGACY_UNKNOWN",
                    payload={"quality": "LEGACY_UNKNOWN", "learning_allowed": False},
                ),
                LifecycleEvent(
                    event_type="FORWARD_MEASURED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="MSFT",
                    decision_id=decision_id,
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"horizon": "close"},
                ),
            ):
                store.append(event)

            sync_v2_learning_performance(event_db=event_db, ml_db=ml_db, market="US", dry_run=False)

            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT quality_grade, learning_allowed FROM v2_learning_performance WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()

            self.assertEqual(row["quality_grade"], "CLEAN")
            self.assertEqual(row["learning_allowed"], 1)

    def test_dry_run_does_not_create_ml_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            registry.register_trade_ready(
                market="US",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="AAPL",
                prompt_version="v2",
                brain_snapshot_id="brain_us",
            )

            summary = sync_v2_learning_performance(event_db=event_db, ml_db=ml_db, market="US", dry_run=True)

            self.assertEqual(summary["selected"], 1)
            self.assertEqual(summary["written"], 0)
            self.assertFalse(ml_db.exists())

    def test_dry_run_previews_decision_links_without_mutating_legacy_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
            )
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.execute(
                    """
                    CREATE TABLE decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market TEXT,
                        ticker TEXT,
                        session_date TEXT,
                        decision TEXT,
                        filled INTEGER DEFAULT 0,
                        order_status TEXT,
                        entry_price REAL,
                        is_simulated INTEGER,
                        data_source TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        market, ticker, session_date, decision, filled, order_status,
                        entry_price, is_simulated, data_source
                    ) VALUES ('KR', '005930', '2026-05-08', 'BUY_SIGNAL', 0, '',
                              NULL, 0, 'live')
                    """
                )
                conn.commit()
            store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-08",
                    ticker="005930",
                    decision_id=decision_id,
                    execution_id="buy1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"price": 70000, "qty": 1},
                )
            )

            summary = sync_v2_learning_performance(
                event_db=event_db,
                ml_db=ml_db,
                market="KR",
                dry_run=True,
                repair_decisions=True,
            )

            self.assertEqual(summary["selected"], 1)
            self.assertEqual(summary["written"], 0)
            self.assertEqual(summary["canonical_written"], 0)
            self.assertEqual(summary["decision_links_written"], 0)
            self.assertEqual(summary["decision_links_matched"], 1)
            self.assertEqual(summary["decision_links_repaired"], 1)
            self.assertEqual(summary["decision_link_sample"][0]["link_status"], "MATCHED")
            self.assertEqual(summary["decision_link_sample"][0]["repaired"], 1)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                decision = conn.execute("SELECT * FROM decisions WHERE id=1").fetchone()
                link_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_decision_fill_links'"
                ).fetchone()

            self.assertEqual(decision["filled"], 0)
            self.assertEqual(decision["order_status"], "")
            self.assertIsNone(decision["entry_price"])
            self.assertIsNone(link_table)


if __name__ == "__main__":
    unittest.main()
