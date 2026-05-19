from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing

from decision.registry import DecisionRegistry
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent
from tools.sync_v2_learning_performance import sync_v2_learning_performance


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
                    payload={"price": 202.0, "pnl_pct": 1.0},
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


if __name__ == "__main__":
    unittest.main()
