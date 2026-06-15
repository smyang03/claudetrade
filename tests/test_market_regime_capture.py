"""진입 시점 시장국면(모드) → v2_learning_performance.market_regime end-to-end 캡처 테스트.

PathB 진입 시 _attach_pathb_position_metadata가 pos["entry_market_regime"]을 캡처하고,
청산 시 exit_meta→CLOSED payload로 흘러가며, sync가 v2_learning_performance.market_regime에
기록한다. 모드별 적중확률 측정의 데이터 고리를 검증한다.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from decision.registry import DecisionRegistry
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent
from tools.sync_v2_learning_performance import sync_v2_learning_performance


class MarketRegimeCaptureTests(unittest.TestCase):
    def _sync_and_get_regime(self, close_extra: dict) -> object:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_db = root / "events.db"
            ml_db = root / "decisions.db"
            store = EventStore(event_db)
            registry = DecisionRegistry(store)
            did = registry.register_trade_ready(
                market="US", runtime_mode="live", session_date="2026-06-15",
                ticker="AAPL", prompt_version="v2", brain_snapshot_id="b",
                strategy_hint="momentum", timing_style="pullback",
            )
            store.create_path_run(
                path_run_id="p1", decision_id=did, path_type="claude_price",
                market="US", runtime_mode="live", session_date="2026-06-15",
                ticker="AAPL", status="FILLED", plan={"origin_action": "BUY_ZONE"},
            )
            for event in (
                LifecycleEvent(
                    event_type="FILLED", market="US", runtime_mode="live",
                    session_date="2026-06-15", ticker="AAPL", decision_id=did,
                    execution_id="b1", prompt_version="v2", brain_snapshot_id="b",
                    payload={"price": 100, "qty": 1, "path_run_id": "p1", "path_type": "claude_price"},
                ),
                LifecycleEvent(
                    event_type="CLOSED", market="US", runtime_mode="live",
                    session_date="2026-06-15", ticker="AAPL", decision_id=did,
                    execution_id="s1", prompt_version="v2", brain_snapshot_id="b",
                    payload={
                        "exit_price": 102, "qty": 1, "pnl_krw": 2, "pnl_pct": 2.0,
                        "close_reason": "CLOSED_CLAUDE_PRICE_TARGET",
                        "path_run_id": "p1", "path_type": "claude_price",
                        **close_extra,
                    },
                ),
                LifecycleEvent(
                    event_type="FORWARD_MEASURED", market="US", runtime_mode="live",
                    session_date="2026-06-15", ticker="AAPL", decision_id=did,
                    prompt_version="v2", brain_snapshot_id="b", payload={"horizon": "close"},
                ),
            ):
                store.append(event)
            sync_v2_learning_performance(event_db=event_db, ml_db=ml_db, market="US", dry_run=False)
            with closing(sqlite3.connect(ml_db)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT market_regime FROM v2_learning_performance WHERE v2_decision_id=?",
                    (did,),
                ).fetchone()
            return row["market_regime"] if row else None

    def test_market_regime_captured_from_close_payload(self) -> None:
        regime = self._sync_and_get_regime({"entry_market_regime": "MODERATE_BULL"})
        self.assertEqual(regime, "MODERATE_BULL")

    def test_market_regime_empty_when_absent(self) -> None:
        regime = self._sync_and_get_regime({})
        self.assertIn(regime, ("", None))


if __name__ == "__main__":
    unittest.main()
