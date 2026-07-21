from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from execution.single_symbol_judge import (
    _immediate_buy_allowed,
    validate_immediate_buy_plan,
    normalize_single_symbol_judge_result,
)

_GATE_ON = {"SINGLE_SYMBOL_JUDGE_ALLOW_BUY_READY": "true"}


class ImmediateBuyGateTests(unittest.TestCase):
    def setUp(self) -> None:
        for k in (
            "SINGLE_SYMBOL_JUDGE_ALLOW_BUY_READY",
            "SINGLE_SYMBOL_JUDGE_BUY_READY_MARKETS",
            "SINGLE_SYMBOL_JUDGE_BUY_READY_REGIMES",
            "IMMEDIATE_BUY_MAX_STOP_PCT",
        ):
            os.environ.pop(k, None)

    # ---- 국면 게이트 ----
    def test_gate_off_by_default(self) -> None:
        ok, _ = _immediate_buy_allowed("US", {"market_regime": "MODERATE_BULL"})
        self.assertFalse(ok)

    def test_gate_on_us_strong(self) -> None:
        with patch.dict(os.environ, _GATE_ON):
            ok, _ = _immediate_buy_allowed("US", {"market_regime": "MODERATE_BULL"})
            self.assertTrue(ok)

    def test_gate_blocks_kr(self) -> None:
        with patch.dict(os.environ, _GATE_ON):
            ok, _ = _immediate_buy_allowed("KR", {"market_regime": "MODERATE_BULL"})
            self.assertFalse(ok)  # KR 강세 추격은 반증(-1.30%)

    def test_gate_blocks_weak_regime(self) -> None:
        with patch.dict(os.environ, _GATE_ON):
            ok, _ = _immediate_buy_allowed("US", {"market_regime": "CAUTIOUS"})
            self.assertFalse(ok)

    def test_gate_blocks_unknown_regime_fail_closed(self) -> None:
        with patch.dict(os.environ, _GATE_ON):
            ok, _ = _immediate_buy_allowed("US", {"market_regime": ""})
            self.assertFalse(ok)  # 강세 확인 못 하면 차단

    # ---- 즉시매수 검증 ----
    def test_validate_pass(self) -> None:
        feats = {"current_price": 100.0}
        result = {"sell_target": 105.0, "stop_loss": 98.5, "hold_days": 2, "confidence": 0.7, "invalid_if": "below 98"}
        errors = validate_immediate_buy_plan(result, features=feats, risk_context={"market": "US"})
        self.assertEqual(errors, [])  # RR=5/1.5=3.3, stop 1.5%<3%

    def test_validate_stop_too_wide(self) -> None:
        feats = {"current_price": 100.0}
        result = {"sell_target": 110.0, "stop_loss": 95.0, "hold_days": 2, "confidence": 0.7, "invalid_if": "x"}
        errors = validate_immediate_buy_plan(result, features=feats, risk_context={"market": "US"})
        self.assertIn("stop_loss_too_wide", errors)  # 5% > 3%

    def test_validate_rr_low(self) -> None:
        feats = {"current_price": 100.0}
        result = {"sell_target": 101.0, "stop_loss": 98.5, "hold_days": 2, "confidence": 0.7, "invalid_if": "x"}
        errors = validate_immediate_buy_plan(result, features=feats, risk_context={"market": "US"})
        self.assertIn("reward_risk_below_min", errors)  # RR 0.67 < 1.5

    def test_validate_target_not_above(self) -> None:
        feats = {"current_price": 100.0}
        result = {"sell_target": 99.0, "stop_loss": 98.0, "hold_days": 2, "confidence": 0.7, "invalid_if": "x"}
        errors = validate_immediate_buy_plan(result, features=feats, risk_context={"market": "US"})
        self.assertIn("sell_target_not_above_current", errors)

    def test_validate_missing_fields(self) -> None:
        errors = validate_immediate_buy_plan({}, features={"current_price": 100.0}, risk_context={"market": "US"})
        self.assertIn("missing_sell_target", errors)
        self.assertIn("missing_stop_loss", errors)
        self.assertIn("missing_invalid_if", errors)

    def test_validate_momentum_fade(self) -> None:
        feats = {"current_price": 100.0, "momentum_state": "fade"}
        result = {"sell_target": 105.0, "stop_loss": 98.5, "hold_days": 2, "confidence": 0.7, "invalid_if": "x"}
        errors = validate_immediate_buy_plan(result, features=feats, risk_context={"market": "US"})
        self.assertIn("post_open_momentum_fade", errors)

    # ---- normalize 통합 흐름 ----
    def _buy_ready_raw(self) -> dict:
        return {
            "ticker": "NVDA", "market": "US", "action": "BUY_READY", "route": "plan_a",
            "sell_target": 105.0, "stop_loss": 98.5, "hold_days": 2, "confidence": 0.7, "invalid_if": "below 98",
        }

    def test_normalize_buy_ready_survives_when_gated(self) -> None:
        with patch.dict(os.environ, _GATE_ON):
            out = normalize_single_symbol_judge_result(
                self._buy_ready_raw(),
                features={"current_price": 100.0},
                risk_context={"market": "US", "market_regime": "MODERATE_BULL"},
            )
            self.assertEqual(out["action"], "BUY_READY")
            self.assertEqual(out["route"], "plan_a")
            self.assertTrue(out.get("valid"))

    def test_normalize_buy_ready_demoted_when_gate_off(self) -> None:
        # 게이트 off → BUY_READY가 눌림으로 강등되고, buy_zone 없어 WAIT_RECHECK로 최종 강등
        out = normalize_single_symbol_judge_result(
            self._buy_ready_raw(),
            features={"current_price": 100.0},
            risk_context={"market": "US", "market_regime": "MODERATE_BULL"},
        )
        self.assertEqual(out["action"], "WAIT_RECHECK")

    def test_normalize_buy_ready_invalid_plan_demoted(self) -> None:
        # 게이트 on이나 손절 넓음 → WAIT_RECHECK
        with patch.dict(os.environ, _GATE_ON):
            raw = self._buy_ready_raw()
            raw["stop_loss"] = 92.0  # 8% 손절
            out = normalize_single_symbol_judge_result(
                raw,
                features={"current_price": 100.0},
                risk_context={"market": "US", "market_regime": "MODERATE_BULL"},
            )
            self.assertEqual(out["action"], "WAIT_RECHECK")


if __name__ == "__main__":
    unittest.main()
