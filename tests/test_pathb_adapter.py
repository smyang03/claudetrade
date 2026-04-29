from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from decision.claude_price_plan import make_price_plan
from execution.claude_price_adapter import ClaudePriceAdapter, round_down_to_kr_tick, round_up_to_kr_tick
from lifecycle.event_store import EventStore


class PathBAdapterTests(unittest.TestCase):
    def test_tick_rounding(self) -> None:
        self.assertEqual(round_up_to_kr_tick(51_201), 51_300)
        self.assertEqual(round_down_to_kr_tick(51_299), 51_200)

    def test_register_hit_fill_expire_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            adapter = ClaudePriceAdapter(store)
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
            self.assertEqual(store.find_path_run(path_run_id)["status"], "WAITING")
            self.assertFalse(adapter.check_entry(path_run_id, 51_900).signal)
            signal = adapter.check_entry(path_run_id, 52_100)
            self.assertTrue(signal.signal, signal)
            self.assertGreaterEqual(signal.limit_price, 52_100)

            adapter.mark_hit(path_run_id, price=52_100, runtime_mode="live", brain_snapshot_id="brain1")
            adapter.mark_order_sent(path_run_id, execution_id="ord1", price=52_200, qty=1, runtime_mode="live", brain_snapshot_id="brain1")
            adapter.mark_filled(path_run_id, price=52_200, qty=1, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
            self.assertEqual(store.find_path_run(path_run_id)["status"], "FILLED")
            self.assertFalse(adapter.mark_expired(path_run_id, runtime_mode="live", brain_snapshot_id="brain1"))

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
            path2 = adapter.register_plan(plan2, runtime_mode="live", brain_snapshot_id="brain1")
            self.assertTrue(adapter.cancel_plan(path2, reason="operator", runtime_mode="live", brain_snapshot_id="brain1"))
            self.assertEqual(store.find_path_run(path2)["status"], "CANCELLED")

    def test_zone_edge_blocks_invalid_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            adapter = ClaudePriceAdapter(store)
            plan = make_price_plan(
                decision_id="dec1",
                ticker="005930",
                market="KR",
                session_date="2026-04-27",
                buy_zone_low=51_000,
                buy_zone_high=51_250,
                sell_target=53_000,
                stop_loss=50_000,
                hold_days=1,
                confidence=0.7,
            )
            path_run_id = adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            signal = adapter.check_entry(path_run_id, 51_240)
            self.assertFalse(signal.signal)
            self.assertEqual(signal.reason, "ZONE_EDGE_NO_VALID_LIMIT")


if __name__ == "__main__":
    unittest.main()
