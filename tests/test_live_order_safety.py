from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from config.v2 import V2Config
from decision.claude_price_plan import make_price_plan
from execution.claude_price_adapter import ClaudePriceAdapter
from execution.safety_gate import PathBSafetyGate, SafetyContext, SafetyGate
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
        self.assertEqual(decision.reason_code, "INVALID_QTY")

    def test_invalid_price_is_reserved_for_non_positive_price(self) -> None:
        ctx = SafetyContext(
            market="US",
            runtime_mode="live",
            ticker="MRVL",
            price_krw=0,
            qty=1,
            order_cost_krw=0,
            cash_krw=1_000_000,
            market_open=True,
            broker_trust_level="trusted",
        )

        decision = SafetyGate(V2Config()).evaluate(ctx)

        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason_code, "INVALID_PRICE")
        self.assertEqual(decision.details["price_krw"], 0.0)

    def test_qty_zero_after_early_gate_uses_order_size_too_small_gate(self) -> None:
        ctx = SafetyContext(
            market="US",
            runtime_mode="live",
            ticker="MRVL",
            price_krw=310_000,
            qty=0,
            order_cost_krw=0,
            cash_krw=1_000_000,
            original_budget_krw=450_000,
            effective_budget_krw=225_000,
            early_gate_applied=True,
            early_gate_size_mult=0.5,
            can_buy_1_share=True,
            fixed_sizing=True,
            market_open=True,
            broker_trust_level="trusted",
        )

        decision = SafetyGate(V2Config()).evaluate(ctx)

        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason_code, "ORDER_SIZE_TOO_SMALL_GATE")
        self.assertTrue(decision.details["early_gate_applied"])
        self.assertTrue(decision.details["can_buy_1_share"])

    def test_high_price_budget_block_is_separate_from_invalid_price(self) -> None:
        ctx = SafetyContext(
            market="US",
            runtime_mode="live",
            ticker="APP",
            price_krw=790_000,
            qty=0,
            order_cost_krw=0,
            cash_krw=1_000_000,
            original_budget_krw=450_000,
            effective_budget_krw=225_000,
            early_gate_applied=True,
            early_gate_size_mult=0.5,
            can_buy_1_share=False,
            fixed_sizing=True,
            market_open=True,
            broker_trust_level="trusted",
        )

        decision = SafetyGate(V2Config()).evaluate(ctx)

        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason_code, "HIGH_PRICE_BUDGET_BLOCK")
        self.assertFalse(decision.details["can_buy_1_share"])

    def test_pathb_qty_does_not_exceed_fixed_budget_for_high_price(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.config = V2Config(
            pathb_fixed_order_krw=100_000,
            kr_min_order_krw=100_000,
            pathb_allow_one_share_over_budget=False,
        )

        qty = runtime._pathb_qty("KR", 287_000, cash_krw=2_700_000)

        self.assertEqual(qty, 0)

    def test_pathb_qty_context_keeps_invalid_price_fast_path(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.config = V2Config(pathb_fixed_order_krw=450_000)

        qty, sizing_context = runtime._pathb_qty_with_context("US", 0, cash_krw=1_000_000)

        self.assertEqual(qty, 0)
        self.assertEqual(sizing_context["sizing_reason"], "invalid_price")
        self.assertEqual(sizing_context["sizing_details"]["pathb_sizing"]["blocker"], "invalid_price")

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

    def test_pathb_qty_applies_us_early_entry_soft_gate(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.config = V2Config(
            pathb_fixed_order_krw=100_000,
            us_min_order_krw=30_000,
            pathb_allow_one_share_over_budget=False,
        )
        runtime.bot = type(
            "Bot",
            (),
            {
                "usd_krw_rate": 1000,
                "_us_early_entry_soft_gate": lambda self, market: {
                    "active": True,
                    "size_mult": 0.5,
                    "elapsed_min": 30.0,
                },
            },
        )()

        qty = runtime._pathb_qty("US", 10_000, cash_krw=2_700_000)

        self.assertEqual(qty, 5)

    def test_pathb_qty_context_classifies_early_gate_one_share_as_order_size_gate(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.config = V2Config(
            pathb_fixed_order_krw=450_000,
            us_min_order_krw=30_000,
            pathb_allow_one_share_over_budget=True,
        )
        runtime.bot = type(
            "Bot",
            (),
            {
                "usd_krw_rate": 1350,
                "_us_early_entry_soft_gate": lambda self, market: {
                    "active": True,
                    "size_mult": 0.5,
                    "elapsed_min": 30.0,
                },
            },
        )()

        qty, sizing_context = runtime._pathb_qty_with_context("US", 310_000, cash_krw=1_000_000)

        self.assertEqual(qty, 0)
        self.assertTrue(sizing_context["early_gate_applied"])
        self.assertEqual(sizing_context["original_budget_krw"], 450_000)
        self.assertEqual(sizing_context["effective_budget_krw"], 225_000)
        self.assertTrue(sizing_context["can_buy_1_share"])

        decision = SafetyGate(V2Config()).evaluate(
            SafetyContext(
                market="US",
                runtime_mode="live",
                ticker="MRVL",
                price_krw=310_000,
                qty=qty,
                order_cost_krw=0,
                cash_krw=1_000_000,
                market_open=True,
                broker_trust_level="trusted",
                original_budget_krw=sizing_context["original_budget_krw"],
                effective_budget_krw=sizing_context["effective_budget_krw"],
                early_gate_applied=sizing_context["early_gate_applied"],
                early_gate_size_mult=sizing_context["early_gate_size_mult"],
                can_buy_1_share=sizing_context["can_buy_1_share"],
                fixed_sizing=sizing_context["fixed_sizing"],
                sizing_reason=sizing_context["sizing_reason"],
                sizing_details=sizing_context["sizing_details"],
            )
        )

        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason_code, "ORDER_SIZE_TOO_SMALL_GATE")
        self.assertEqual(decision.details["pathb_sizing"]["blocker"], "high_price_one_share_blocked")

    def test_pathb_qty_context_classifies_price_above_pre_gate_budget(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.config = V2Config(
            pathb_fixed_order_krw=450_000,
            us_min_order_krw=30_000,
            pathb_allow_one_share_over_budget=True,
        )
        runtime.bot = type(
            "Bot",
            (),
            {
                "usd_krw_rate": 1350,
                "_us_early_entry_soft_gate": lambda self, market: {
                    "active": True,
                    "size_mult": 0.5,
                    "elapsed_min": 30.0,
                },
            },
        )()

        qty, sizing_context = runtime._pathb_qty_with_context("US", 790_000, cash_krw=1_000_000)

        self.assertEqual(qty, 0)
        self.assertFalse(sizing_context["can_buy_1_share"])

        decision = SafetyGate(V2Config()).evaluate(
            SafetyContext(
                market="US",
                runtime_mode="live",
                ticker="APP",
                price_krw=790_000,
                qty=qty,
                order_cost_krw=0,
                cash_krw=1_000_000,
                market_open=True,
                broker_trust_level="trusted",
                original_budget_krw=sizing_context["original_budget_krw"],
                effective_budget_krw=sizing_context["effective_budget_krw"],
                early_gate_applied=sizing_context["early_gate_applied"],
                early_gate_size_mult=sizing_context["early_gate_size_mult"],
                can_buy_1_share=sizing_context["can_buy_1_share"],
                fixed_sizing=sizing_context["fixed_sizing"],
                sizing_reason=sizing_context["sizing_reason"],
                sizing_details=sizing_context["sizing_details"],
            )
        )

        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason_code, "HIGH_PRICE_BUDGET_BLOCK")
        self.assertEqual(decision.details["pathb_sizing"]["blocker"], "high_price_one_share_blocked")

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
