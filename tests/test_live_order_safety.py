from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from config.v2 import V2Config
from decision.claude_price_plan import make_price_plan
from execution.claude_price_adapter import ClaudePriceAdapter
from execution.safety_gate import PathBSafetyGate, SafetyContext
from lifecycle.event_store import EventStore
from runtime.pathb_runtime import PathBRuntime


class LiveOrderSafetyTests(unittest.TestCase):
    def _plan(self):
        return make_price_plan(
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

    def test_qty_zero_is_blocked_before_broker_order(self) -> None:
        ctx = SafetyContext(
            market="KR",
            runtime_mode="live",
            ticker="005930",
            price_krw=200_000,
            qty=0,
            order_cost_krw=0,
            cash_krw=100_000,
            min_order_krw=100_000,
            market_open=True,
            broker_trust_level="trusted",
        )
        decision = PathBSafetyGate(V2Config()).evaluate(ctx, plan=self._plan())

        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason_code, "INVALID_PRICE")

    def test_pathb_qty_does_not_exceed_fixed_budget_for_high_price(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.config = V2Config(
            pathb_fixed_order_krw=100_000,
            kr_min_order_krw=100_000,
            pathb_allow_one_share_over_budget=False,
        )

        qty = runtime._pathb_qty("KR", 287_000, cash_krw=2_700_000)

        self.assertEqual(qty, 0)

    def test_pathb_qty_allows_one_share_over_budget_within_cap(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.config = V2Config(
            pathb_fixed_order_krw=200_000,
            kr_min_order_krw=100_000,
            pathb_allow_one_share_over_budget=True,
            pathb_one_share_over_budget_max_krw=500_000,
            pathb_one_share_over_budget_max_account_pct=30.0,
        )

        qty = runtime._pathb_qty("KR", 287_000, cash_krw=2_700_000)

        self.assertEqual(qty, 1)

    def test_pathb_qty_blocks_one_share_over_budget_above_exception_cap(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.config = V2Config(
            pathb_fixed_order_krw=200_000,
            kr_min_order_krw=100_000,
            pathb_allow_one_share_over_budget=True,
            pathb_one_share_over_budget_max_krw=250_000,
            pathb_one_share_over_budget_max_account_pct=30.0,
        )

        qty = runtime._pathb_qty("KR", 287_000, cash_krw=2_700_000)

        self.assertEqual(qty, 0)

    def test_pathb_qty_allows_min_order_only_within_fixed_budget(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.config = V2Config(pathb_fixed_order_krw=100_000, kr_min_order_krw=100_000)

        self.assertEqual(runtime._pathb_qty("KR", 50_000, cash_krw=2_700_000), 2)
        self.assertEqual(runtime._pathb_qty("KR", 60_000, cash_krw=2_700_000), 0)

    def test_duplicate_pathb_plan_is_visible_as_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            adapter = ClaudePriceAdapter(store)
            plan = self._plan()

            adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            active = store.active_path_runs_for_ticker(
                market="KR",
                ticker="005930",
                session_date="2026-04-27",
                runtime_mode="live",
            )

            self.assertEqual(len(active), 1)
            self.assertEqual(active[0]["path_type"], "claude_price")


if __name__ == "__main__":
    unittest.main()
