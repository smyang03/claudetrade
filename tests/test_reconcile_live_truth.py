from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lifecycle.event_store import EventStore, utc_now_iso
from tools import reconcile_live_truth


def _runtime_path(root: Path):
    def _resolve(*parts, make_parents=True):
        path = root.joinpath(*parts)
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return _resolve


class ReconcileLiveTruthTests(unittest.TestCase):
    def test_sofi_like_unresolved_sell_suggests_still_held_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = EventStore(root / "events.db")
            now = utc_now_iso()
            with store.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO v2_path_runs (
                        path_run_id, decision_id, path_type, market, runtime_mode,
                        session_date, ticker, status, plan_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "path_20260515_US_SOFI",
                        "decision1",
                        "claude_price",
                        "US",
                        "live",
                        "2026-05-15",
                        "SOFI",
                        "ORDER_UNKNOWN",
                        json.dumps({"order_no": "buy-1", "session_end_unresolved": True}),
                        now,
                        now,
                    ),
                )
            state_dir = root / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "live_open_positions.json").write_text(
                json.dumps(
                    [
                        {
                            "market": "US",
                            "ticker": "SOFI",
                            "qty": 12,
                            "pathb_path_run_id": "path_20260515_US_SOFI",
                            "pathb_pending_sell_order_no": "0032123235",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (state_dir / "live_pending_orders.json").write_text("[]", encoding="utf-8")

            with patch.object(reconcile_live_truth, "get_runtime_path", side_effect=_runtime_path(root)), patch.object(
                reconcile_live_truth,
                "_load_broker_snapshot",
                return_value={
                    "markets": {
                        "US": {
                            "positions": [{"ticker": "SOFI", "qty": 12}],
                            "open_orders": [],
                            "today_fills": [],
                            "last_success_at": "2026-05-16T01:00:00+00:00",
                        }
                    }
                },
            ):
                result = reconcile_live_truth.reconcile_live_truth(
                    mode="live",
                    market="US",
                    ticker="SOFI",
                    path_run_id="path_20260515_US_SOFI",
                    sell_order_id="0032123235",
                    store_path=root / "events.db",
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        action = result["actions"][0]
        self.assertEqual(action["broker_position_qty"], 12)
        self.assertFalse(action["broker_open_order_match"])
        self.assertFalse(action["broker_today_fill_match"])
        self.assertTrue(action["local_position_match"])
        self.assertEqual(action["local_position_qty"], 12)
        self.assertEqual(action["suggested_action"], "recover_still_held")
        self.assertFalse(action["do_not_start"])

    def test_qty_mismatch_still_requires_manual_review_and_blocks_start(self) -> None:
        local = {"local_position_qty": 12, "local_pending_sell_order_id": "sell1"}
        broker = {
            "broker_position_qty": 7,
            "broker_open_order_evidence": False,
            "broker_sell_fill_evidence": False,
        }

        action, do_not_start = reconcile_live_truth._suggest_action(local, broker, status="ORDER_UNKNOWN")

        self.assertEqual(action, "manual_review")
        self.assertTrue(do_not_start)


if __name__ == "__main__":
    unittest.main()
