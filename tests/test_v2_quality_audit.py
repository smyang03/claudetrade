from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from decision.registry import DecisionRegistry
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent
from tools.v2_quality_audit import build_lifecycle_reconciliation, build_stop_loss_forensics


class V2QualityAuditTests(unittest.TestCase):
    def test_lifecycle_reconciliation_dedupes_fills_and_finds_missing_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            registry = DecisionRegistry(store)
            closed_id = registry.register_trade_ready(
                market="US",
                runtime_mode="live",
                session_date="2026-05-04",
                ticker="NVDA",
                prompt_version="v2",
                brain_snapshot_id="brain_us",
            )
            open_id = registry.register_trade_ready(
                market="US",
                runtime_mode="live",
                session_date="2026-05-04",
                ticker="CRCL",
                prompt_version="v2",
                brain_snapshot_id="brain_us",
            )
            for _ in range(2):
                store.append(
                    LifecycleEvent(
                        event_type="FILLED",
                        market="US",
                        runtime_mode="live",
                        session_date="2026-05-04",
                        ticker="NVDA",
                        decision_id=closed_id,
                        execution_id="buy1",
                        position_id="pos1",
                        prompt_version="v2",
                        brain_snapshot_id="brain_us",
                        payload={"side": "buy", "qty": 1, "price": 100},
                    )
                )
            store.append(
                LifecycleEvent(
                    event_type="CLOSED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-04",
                    ticker="NVDA",
                    decision_id=closed_id,
                    execution_id="sell1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"close_reason": "CLOSED_LOSS_CAP"},
                )
            )
            store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-04",
                    ticker="CRCL",
                    decision_id=open_id,
                    execution_id="buy2",
                    position_id="pos2",
                    prompt_version="v2",
                    brain_snapshot_id="brain_us",
                    payload={"side": "buy", "qty": 1, "price": 50},
                )
            )

            report = build_lifecycle_reconciliation(store, session_date="2026-05-04", runtime_mode="live", markets=["US"])

            self.assertEqual(report["raw_fill_events"], 3)
            self.assertEqual(report["unique_fill_count"], 2)
            self.assertEqual(report["unique_fill_with_close"], 1)
            self.assertEqual(report["unique_fill_without_close"], 1)
            self.assertEqual(report["unique_fill_without_close_examples"][0]["ticker"], "CRCL")
            self.assertEqual(report["unique_fill_path_coverage"]["by_path_type"][0]["path_type"], "patha_or_legacy")

    def test_stop_loss_forensics_uses_v2_plan_stop_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = EventStore(root / "events.db")
            registry = DecisionRegistry(store)
            paper_decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="paper",
                session_date="2026-04-28",
                ticker="002780",
                prompt_version="v2",
                brain_snapshot_id="brain_kr_paper",
            )
            store.create_path_run(
                path_run_id="paper_path",
                decision_id=paper_decision_id,
                path_type="claude_price",
                market="KR",
                runtime_mode="paper",
                session_date="2026-04-28",
                ticker="002780",
                status="FILLED",
                plan={"stop_loss": 999},
            )
            store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="paper",
                    session_date="2026-04-28",
                    ticker="002780",
                    decision_id=paper_decision_id,
                    execution_id="paper_buy1",
                    position_id="paper_pos1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr_paper",
                    payload={"side": "buy", "qty": 10, "price": 1500},
                )
            )
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-28",
                ticker="002780",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
            )
            store.create_path_run(
                path_run_id="path1",
                decision_id=decision_id,
                path_type="claude_price",
                market="KR",
                runtime_mode="live",
                session_date="2026-04-28",
                ticker="002780",
                status="FILLED",
                plan={"stop_loss": 1400},
            )
            store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-28",
                    ticker="002780",
                    decision_id=decision_id,
                    execution_id="buy1",
                    position_id="pos1",
                    prompt_version="v2",
                    brain_snapshot_id="brain_kr",
                    payload={"side": "buy", "qty": 10, "price": 1535},
                )
            )
            live_path = root / "live_decisions.jsonl"
            live_path.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-04-28T09:13:47+09:00",
                        "session_date": "2026-04-28",
                        "market": "KR",
                        "ticker": "002780",
                        "strategy": "claude_price",
                        "exit_reason": "stop_loss",
                        "exit_price": 1387,
                        "pnl_pct": -9.8,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            report = build_stop_loss_forensics(store, live_decisions_path=live_path, runtime_mode="live")

            self.assertEqual(report["count"], 1)
            item = report["items"][0]
            self.assertEqual(item["path"], "claude_price")
            self.assertEqual(item["planned_stop_loss"], 1400.0)
            self.assertEqual(item["stop_slippage_pct"], -0.9286)


if __name__ == "__main__":
    unittest.main()
