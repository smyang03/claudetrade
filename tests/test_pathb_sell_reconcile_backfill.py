from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lifecycle.event_store import EventStore
from tools.pathb_sell_reconcile_backfill import apply_report, build_report


def _write_snapshot(path: Path, *, order_no: str, stale: bool = False) -> None:
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-29T00:00:00+00:00",
                "markets": {
                    "US": {
                        "missing": False,
                        "stale": stale,
                        "fresh": not stale,
                        "trusted": not stale,
                        "error": "",
                        "positions": [],
                        "open_orders": [],
                        "today_fills": [
                            {
                                "ticker": "BBY",
                                "side": "sell",
                                "order_no": order_no,
                                "filled_qty": 2,
                                "qty": 2,
                                "avg_price": 75.0,
                            }
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def _create_sell_run(store: EventStore, *, exit_execution_id: str) -> None:
    store.create_path_run(
        path_run_id="path_bby",
        decision_id="dec_bby",
        path_type="claude_price",
        market="US",
        runtime_mode="live",
        session_date="2026-05-28",
        ticker="BBY",
        status="SELL_ACKED",
        plan={
            "decision_id": "dec_bby",
            "path_run_id": "path_bby",
            "ticker": "BBY",
            "market": "US",
            "session_date": "2026-05-28",
            "buy_zone_low": 70,
            "buy_zone_high": 72,
            "sell_target": 76,
            "stop_loss": 68,
            "hold_days": 1,
            "confidence": 0.8,
            "actual_entry_price": 72,
            "exit_execution_id": exit_execution_id,
            "exit_qty": 2,
            "pending_close_reason": "CLOSED_CLAUDE_PRICE_PRE_CLOSE",
        },
    )


class PathBSellReconcileBackfillTests(unittest.TestCase):
    def test_exact_exit_execution_id_match_can_apply_when_snapshot_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = EventStore(root / "events.db")
            _create_sell_run(store, exit_execution_id="sell1")
            snapshot = root / "snapshot.json"
            _write_snapshot(snapshot, order_no="sell1")

            report = build_report(db_path=root / "events.db", snapshot_path=snapshot, market="US", mode="live")
            result = apply_report(report, db_path=root / "events.db")
            run = store.find_path_run("path_bby")

            self.assertTrue(report["snapshot_fresh"])
            self.assertTrue(report["apply_allowed"])
            self.assertEqual(report["matched"], 1)
            self.assertEqual(report["proposals"][0]["match_mode"], "exit_execution_id")
            self.assertEqual(result["applied"], ["path_bby"])
            self.assertEqual(run["status"], "CLOSED")

    def test_ticker_only_order_mismatch_is_manual_review_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = EventStore(root / "events.db")
            _create_sell_run(store, exit_execution_id="stale-sell")
            snapshot = root / "snapshot.json"
            _write_snapshot(snapshot, order_no="actual-sell")

            report = build_report(db_path=root / "events.db", snapshot_path=snapshot, market="US", mode="live")
            result = apply_report(report, db_path=root / "events.db")
            run = store.find_path_run("path_bby")

            self.assertEqual(report["matched"], 0)
            self.assertEqual(report["unmatched"][0]["match_mode"], "ticker_only_mismatch")
            self.assertEqual(report["unmatched"][0]["manual_review_reason"], "manual_review_required")
            self.assertEqual(result["applied"], [])
            self.assertEqual(run["status"], "SELL_ACKED")

    def test_apply_refuses_unfresh_broker_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = EventStore(root / "events.db")
            _create_sell_run(store, exit_execution_id="sell1")
            snapshot = root / "snapshot.json"
            _write_snapshot(snapshot, order_no="sell1", stale=True)

            report = build_report(db_path=root / "events.db", snapshot_path=snapshot, market="US", mode="live")
            result = apply_report(report, db_path=root / "events.db")
            run = store.find_path_run("path_bby")

            self.assertFalse(report["snapshot_fresh"])
            self.assertFalse(report["apply_allowed"])
            self.assertEqual(report["matched"], 1)
            self.assertEqual(result["errors"][0]["error"], "broker_truth_unfresh_or_untrusted")
            self.assertEqual(run["status"], "SELL_ACKED")


if __name__ == "__main__":
    unittest.main()
