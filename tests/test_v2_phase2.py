from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone
import tempfile
import unittest

from config.v2 import DEFAULT_V2_CONFIG, V2Config
from execution.order_state import OrderUnknownEscalator, PartialFillPolicy
from execution.safety_gate import SafetyContext, SafetyGate
from execution.sizing import FixedSizer
from lifecycle.validation import V2PhaseValidator
from runtime.rate_limiter import V2RateLimiter


class V2Phase2Tests(unittest.TestCase):
    def test_fixed_sizer_uses_configured_kr_and_us_not_atr(self) -> None:
        sizer = FixedSizer(DEFAULT_V2_CONFIG)
        self.assertEqual(sizer.size(market="KR", price_krw=25_000, cash_krw=1_000_000).qty, 4)
        us = sizer.size(market="US", price_krw=35_000, usd_krw=1_400, cash_krw=1_000_000)
        self.assertEqual(us.budget_krw, 70_000)
        self.assertEqual(us.qty, 2)

        krw_target = FixedSizer(V2Config(us_fixed_order_krw=100_000, us_min_order_krw=100_000))
        us_dynamic = krw_target.size(market="US", price_krw=35_000, usd_krw=1_350, cash_krw=1_000_000)
        self.assertEqual(us_dynamic.budget_krw, 100_000)
        self.assertEqual(us_dynamic.min_order_krw, 100_000)
        self.assertEqual(us_dynamic.qty, 3)
        self.assertTrue(us_dynamic.min_order_met)

        kr_min = FixedSizer(V2Config(kr_fixed_order_krw=100_000, kr_min_order_krw=100_000))
        kr_dynamic = kr_min.size(market="KR", price_krw=52_000, cash_krw=1_000_000)
        self.assertEqual(kr_dynamic.qty, 2)
        self.assertTrue(kr_dynamic.min_order_met)

    def test_safety_gate_blocks_standard_reasons(self) -> None:
        gate = SafetyGate(DEFAULT_V2_CONFIG)
        base = dict(
            market="KR",
            runtime_mode="live",
            ticker="005930",
            price_krw=25_000,
            qty=4,
            order_cost_krw=100_000,
            cash_krw=1_000_000,
            min_order_krw=50_000,
            market_open=True,
        )
        self.assertTrue(gate.evaluate(SafetyContext(**base)).passed)
        self.assertEqual(
            gate.evaluate(SafetyContext(**{**base, "pending_orders": [{"market": "KR", "ticker": "005930"}]})).reason_code,
            "PENDING_ORDER_EXISTS",
        )
        self.assertEqual(
            gate.evaluate(SafetyContext(**{**base, "stopped_tickers": {"005930"}})).reason_code,
            "SAME_DAY_REENTRY_AFTER_STOP",
        )
        stale_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        self.assertEqual(
            gate.evaluate(SafetyContext(**{**base, "last_market_data_at": stale_at})).reason_code,
            "STALE_MARKET_DATA",
        )

    def test_order_unknown_escalation_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            escalator = OrderUnknownEscalator(Path(tmp) / "unknown.json")
            self.assertEqual(
                escalator.record_unknown(market="KR", ticker="005930", execution_id="e1")["scope"],
                "ticker",
            )
            self.assertEqual(
                escalator.record_unknown(market="KR", ticker="000660", execution_id="e2")["scope"],
                "market",
            )
            escalator.record_unknown(market="US", ticker="NVDA", execution_id="e3")
            self.assertEqual(
                escalator.record_unknown(market="US", ticker="MSFT", execution_id="e4")["scope"],
                "global",
            )

    def test_partial_fill_policy_and_rate_limiter(self) -> None:
        partial = PartialFillPolicy(DEFAULT_V2_CONFIG).apply(market="US", original_qty=10, newly_filled_qty=3)
        self.assertEqual(partial.status, "PARTIAL_FILLED")
        self.assertEqual(partial.remaining_qty, 7)
        self.assertEqual(partial.ttl_sec, 180)

        limiter = V2RateLimiter(max_calls=2, per_seconds=60)
        self.assertTrue(limiter.allow("orders", 0.0))
        self.assertTrue(limiter.allow("orders", 1.0))
        self.assertFalse(limiter.allow("orders", 2.0))
        self.assertTrue(limiter.allow("orders", 61.0))

    def test_phase2_gate_runs_phase1_and_phase2(self) -> None:
        report = V2PhaseValidator(Path(__file__).resolve().parent.parent).validate(2)
        self.assertTrue(report["ok"], report)
        self.assertEqual([phase["phase"] for phase in report["phases"]], [1, 2])


if __name__ == "__main__":
    unittest.main()
