from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from execution.buy_time_confirm_judge import (
    INTERNAL_UNAVAILABLE,
    adverse_context,
    call_buy_time_confirm_judge,
    normalize_buy_time_confirm_result,
    unavailable_result,
)


class _SlowMessages:
    def create(self, **kwargs):
        time.sleep(0.2)
        return SimpleNamespace(
            content=[SimpleNamespace(text='{"decision":"CONFIRM_BUY","confidence":0.8,"reason":"late"}')],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )


class _SlowClient:
    messages = _SlowMessages()


class BuyTimeConfirmJudgeTests(unittest.TestCase):
    def test_confirm_buy_normalizes(self) -> None:
        result = normalize_buy_time_confirm_result(
            {"ticker": "aapl", "market": "US", "decision": "confirm_buy", "confidence": 0.8, "reason": "valid"},
            market="US",
            ticker="AAPL",
            current_context={"risk_mode": "NORMAL", "market_change_severity_bucket": "normal"},
        )

        self.assertEqual(result["decision"], "CONFIRM_BUY")
        self.assertEqual(result["ticker"], "AAPL")
        self.assertTrue(result["valid"])

    def test_defer_and_reject_normalize(self) -> None:
        defer = normalize_buy_time_confirm_result({"decision": "WAIT"}, market="KR", ticker="005930")
        reject = normalize_buy_time_confirm_result({"decision": "NO_TRADE"}, market="KR", ticker="005930")

        self.assertEqual(defer["decision"], "DEFER")
        self.assertEqual(reject["decision"], "REJECT")

    def test_invalid_decision_becomes_internal_unavailable(self) -> None:
        result = normalize_buy_time_confirm_result({"decision": "BUY_READY"}, market="US", ticker="AAPL")

        self.assertEqual(result["decision"], INTERNAL_UNAVAILABLE)
        self.assertEqual(result["confirm_unavailable_reason"], "invalid_decision")
        self.assertFalse(result["valid"])

    def test_unavailable_result_distinct_from_confirm_buy(self) -> None:
        result = unavailable_result(market="US", ticker="AAPL", reason="cap_exceeded")

        self.assertEqual(result["decision"], INTERNAL_UNAVAILABLE)
        self.assertEqual(result["confirm_unavailable_reason"], "cap_exceeded")

    def test_adverse_context_detects_risk_off_and_severe_down(self) -> None:
        self.assertTrue(adverse_context({"risk_mode": "RISK_OFF"}))
        self.assertTrue(adverse_context({"market_change_severity_bucket": "severe_down"}))
        self.assertFalse(adverse_context({"risk_mode": "NORMAL", "market_change_severity_bucket": "normal"}))

    def test_call_times_out_as_unavailable(self) -> None:
        started = time.perf_counter()
        with patch.dict("os.environ", {"BUY_TIME_CONFIRM_TIMEOUT_MS": "10"}, clear=False):
            result = call_buy_time_confirm_judge(
                market="US",
                ticker="AAPL",
                candidate={},
                signal={},
                current_context={"risk_mode": "NORMAL", "market_change_severity_bucket": "normal"},
                selection_context={},
                client=_SlowClient(),
            )

        self.assertLess(time.perf_counter() - started, 0.15)
        self.assertEqual(result["decision"], INTERNAL_UNAVAILABLE)
        self.assertEqual(result["confirm_unavailable_reason"], "timeout")


if __name__ == "__main__":
    unittest.main()
