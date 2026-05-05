from __future__ import annotations

import unittest

from config.v2 import V2Config
from decision.claude_price_plan import make_price_plan
from execution.safety_gate import PathBSafetyGate, SafetyContext, SafetyGate


def _ctx() -> SafetyContext:
    return SafetyContext(
        market="KR",
        runtime_mode="live",
        ticker="005930",
        price_krw=52_000,
        qty=1,
        order_cost_krw=52_000,
        cash_krw=1_000_000,
        min_order_krw=50_000,
        market_open=True,
        broker_trust_level="trusted",
    )


def _plan(confidence: float = 0.7):
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
        confidence=confidence,
    )


class PathBSafetyTests(unittest.TestCase):
    def test_blocks_disabled_invalid_and_duplicate(self) -> None:
        self.assertEqual(
            PathBSafetyGate(V2Config(pathb_mode="disabled")).evaluate(_ctx(), plan=_plan()).reason_code,
            "PATHB_DISABLED",
        )
        self.assertEqual(PathBSafetyGate().evaluate(_ctx(), plan=None).reason_code, "CLAUDE_PRICE_INVALID")
        self.assertEqual(
            PathBSafetyGate().evaluate(_ctx(), plan=_plan(), patha_holding=True).reason_code,
            "PATH_DUPLICATE_HOLDING",
        )

    def test_blocks_daily_limit_confidence_and_base_gate(self) -> None:
        self.assertEqual(
            PathBSafetyGate().evaluate(_ctx(), plan=_plan(), pathb_daily_count=1).reason_code,
            "PATHB_MAX_DAILY_ENTRIES",
        )
        self.assertEqual(
            PathBSafetyGate().evaluate(_ctx(), plan=_plan(0.3)).reason_code,
            "CLAUDE_PRICE_INVALID",
        )
        bad_ctx = SafetyContext(**{**_ctx().__dict__, "cash_krw": 10})
        self.assertEqual(PathBSafetyGate().evaluate(bad_ctx, plan=_plan()).reason_code, "INSUFFICIENT_CASH")

    def test_daily_loss_limit_uses_realized_pnl_basis(self) -> None:
        gate = SafetyGate(V2Config(daily_loss_limit_pct=-2.0))
        ctx = SafetyContext(
            **{
                **_ctx().__dict__,
                "daily_pnl_pct": 0.0,
                "daily_pnl_basis": "realized",
                "realized_daily_pnl_pct": 0.0,
                "equity_daily_pnl_pct": -3.0,
            }
        )

        decision = gate.evaluate(ctx)

        self.assertTrue(decision.passed, decision)
        self.assertEqual(decision.details["daily_pnl_basis"], "realized")
        self.assertEqual(decision.details["realized_daily_pnl_pct"], 0.0)
        self.assertEqual(decision.details["equity_daily_pnl_pct"], -3.0)

    def test_daily_loss_limit_blocks_realized_loss(self) -> None:
        gate = SafetyGate(V2Config(daily_loss_limit_pct=-2.0))
        ctx = SafetyContext(
            **{
                **_ctx().__dict__,
                "daily_pnl_pct": -2.1,
                "daily_pnl_basis": "realized",
                "realized_daily_pnl_pct": -2.1,
                "equity_daily_pnl_pct": 0.5,
            }
        )

        decision = gate.evaluate(ctx)

        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason_code, "DAILY_LOSS_LIMIT")
        self.assertEqual(decision.details["daily_pnl_basis"], "realized")

    def test_passes_when_all_conditions_ok(self) -> None:
        decision = PathBSafetyGate().evaluate(_ctx(), plan=_plan())
        self.assertTrue(decision.passed, decision)


if __name__ == "__main__":
    unittest.main()
