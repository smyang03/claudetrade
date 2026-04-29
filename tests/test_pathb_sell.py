from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from decision.claude_price_plan import make_price_plan
from execution.claude_price_adapter import ClaudePriceAdapter
from execution.claude_price_sell_manager import ClaudePriceSellManager
from lifecycle.event_store import EventStore


class PathBSellTests(unittest.TestCase):
    def _filled(self, adapter: ClaudePriceAdapter) -> str:
        plan = make_price_plan(
            decision_id="dec1",
            ticker="005930",
            market="KR",
            session_date="2026-04-27",
            buy_zone_low=52_000,
            buy_zone_high=52_500,
            sell_target=54_500,
            stop_loss=51_000,
            hold_days=1,
            confidence=0.7,
        )
        path_run_id = adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
        adapter.mark_filled(path_run_id, price=52_200, qty=1, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
        return path_run_id

    def _partial_filled(self, adapter: ClaudePriceAdapter) -> str:
        plan = make_price_plan(
            decision_id="dec_partial",
            ticker="005930",
            market="KR",
            session_date="2026-04-27",
            buy_zone_low=52_000,
            buy_zone_high=52_500,
            sell_target=54_500,
            stop_loss=51_000,
            hold_days=1,
            confidence=0.7,
        )
        path_run_id = adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
        adapter.mark_partial_filled(
            path_run_id,
            price=52_200,
            qty=1,
            execution_id="ord_partial",
            runtime_mode="live",
            brain_snapshot_id="brain1",
        )
        return path_run_id

    def test_exit_priority_and_mark_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ClaudePriceAdapter(EventStore(Path(tmp) / "events.db"))
            manager = ClaudePriceSellManager(adapter)
            path_run_id = self._filled(adapter)

            hard = manager.check_exit(path_run_id, 50_900, hard_stop_price=51_500)
            self.assertTrue(hard.signal)
            self.assertEqual(hard.close_reason, "CLOSED_HARD_STOP")

            target = manager.check_exit(path_run_id, 54_600)
            self.assertTrue(target.signal)
            self.assertEqual(target.close_reason, "CLOSED_CLAUDE_PRICE_TARGET")
            manager.mark_closed(
                path_run_id,
                close_reason=target.close_reason,
                price=54_600,
                pnl_pct=4.6,
                runtime_mode="live",
                brain_snapshot_id="brain1",
                execution_id="sell1",
            )
            self.assertEqual(adapter.store.find_path_run(path_run_id)["status"], "CLOSED")

    def test_stop_revision_and_non_filled_no_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ClaudePriceAdapter(EventStore(Path(tmp) / "events.db"))
            manager = ClaudePriceSellManager(adapter)
            path_run_id = self._filled(adapter)

            lowered = manager.request_stop_revision(path_run_id, new_stop_loss=50_500, runtime_mode="live", brain_snapshot_id="brain1")
            self.assertFalse(lowered.ok)
            self.assertEqual(lowered.reason, "stop_lowering_forbidden")
            raised = manager.request_stop_revision(path_run_id, new_stop_loss=51_500, runtime_mode="live", brain_snapshot_id="brain1")
            self.assertTrue(raised.ok)

            plan2 = make_price_plan(
                decision_id="dec2",
                ticker="000660",
                market="KR",
                session_date="2026-04-27",
                buy_zone_low=120_000,
                buy_zone_high=121_000,
                sell_target=125_000,
                stop_loss=118_000,
                hold_days=1,
                confidence=0.7,
            )
            waiting = adapter.register_plan(plan2, runtime_mode="live", brain_snapshot_id="brain1")
            self.assertFalse(manager.check_exit(waiting, 126_000).signal)

    def test_buy_partial_fill_is_protected_by_exit_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ClaudePriceAdapter(EventStore(Path(tmp) / "events.db"))
            manager = ClaudePriceSellManager(adapter)
            path_run_id = self._partial_filled(adapter)

            stop = manager.check_exit(path_run_id, 50_900)
            self.assertTrue(stop.signal)
            self.assertEqual(stop.close_reason, "CLOSED_CLAUDE_PRICE_STOP")
            self.assertTrue(manager.pre_close_exit_needed(path_run_id, minutes_to_close=5, config_cutoff=10))


if __name__ == "__main__":
    unittest.main()
