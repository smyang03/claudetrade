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


class BuyReadyShadowMarketTest(unittest.TestCase):
    """즉시매수 shadow 시장 — 관측하되 실주문은 절대 나가지 않아야 한다.

    KR은 즉시매수가 한 번도 관측된 적이 없다. 금지 근거는 judge가 '거부한' 건이
    나빴다는 것인데(WAIT_RECHECK 반사실 -1.557%), 그건 judge가 '승인한' 건이
    나쁘다는 증거가 아니다(US는 거부 -1.075% vs 승인 +2.89%로 정반대였다).
    거래가 없어 데이터가 안 쌓이는 교착을 리스크 0으로 깨기 위한 경로다.
    """

    ENV = {"SINGLE_SYMBOL_JUDGE_ALLOW_BUY_READY": "true",
           "SINGLE_SYMBOL_JUDGE_BUY_READY_MARKETS": "US",
           "SINGLE_SYMBOL_JUDGE_BUY_READY_SHADOW_MARKETS": "KR",
           "SINGLE_SYMBOL_JUDGE_BUY_READY_REGIMES": "MILD_BULL,MODERATE_BULL,AGGRESSIVE"}

    @staticmethod
    def _plan(market: str, ticker: str, price: float) -> dict:
        return {"ticker": ticker, "market": market, "action": "BUY_READY", "route": "plan_a",
                "confidence": 0.7, "reference_price": price, "sell_target": price * 1.06,
                "stop_loss": price * 0.98, "hold_days": 2,
                "reason": "strong continuation", "invalid_if": "below stop"}

    def test_shadow_market_never_emits_order_route(self) -> None:
        """가장 중요한 계약 — shadow 시장은 유효한 플랜이어도 실주문 경로가 끊긴다."""
        with patch.dict(os.environ, self.ENV):
            out = normalize_single_symbol_judge_result(
                self._plan("KR", "005930", 70000.0),
                features={"current_price": 70000.0},
                risk_context={"market": "KR", "market_regime": "MODERATE_BULL"})
            self.assertEqual(out["action"], "WAIT_RECHECK")
            self.assertEqual(out["route"], "wait")
            self.assertEqual(out["immediate_buy_gate"], "shadow_observe")

    def test_shadow_preserves_judgement_for_later_review(self) -> None:
        """관측이 목적이므로 판정 내용이 보존되어야 반사실 검증이 가능하다."""
        with patch.dict(os.environ, self.ENV):
            out = normalize_single_symbol_judge_result(
                self._plan("KR", "005930", 70000.0),
                features={"current_price": 70000.0},
                risk_context={"market": "KR", "market_regime": "MODERATE_BULL"})
            self.assertTrue(out["immediate_buy_shadow"])
            self.assertTrue(out["immediate_buy_shadow_valid"])
            plan = out["immediate_buy_shadow_plan"]
            self.assertAlmostEqual(plan["reference_price"], 70000.0)
            self.assertTrue(plan["sell_target"] > plan["reference_price"] > plan["stop_loss"])

    def test_live_market_is_unaffected(self) -> None:
        """US(실주문 시장)는 기존대로 BUY_READY가 살아 있어야 한다."""
        with patch.dict(os.environ, self.ENV):
            out = normalize_single_symbol_judge_result(
                self._plan("US", "AAPL", 200.0),
                features={"current_price": 200.0},
                risk_context={"market": "US", "market_regime": "MODERATE_BULL"})
            self.assertEqual(out["action"], "BUY_READY")
            self.assertEqual(out["route"], "plan_a")
            self.assertEqual(out["immediate_buy_gate"], "allowed")
            self.assertIsNone(out.get("immediate_buy_shadow"))

    def test_unset_shadow_keeps_previous_block(self) -> None:
        """shadow 미설정이면 KR은 기존대로 완전 차단(회귀 방지)."""
        env = dict(self.ENV)
        env["SINGLE_SYMBOL_JUDGE_BUY_READY_SHADOW_MARKETS"] = ""
        with patch.dict(os.environ, env):
            allowed, reason = _immediate_buy_allowed("KR", {"market_regime": "MODERATE_BULL"})
            self.assertFalse(allowed)
            self.assertEqual(reason, "buy_ready_market_not_allowed")


class BuyReadyGateLoggingTest(unittest.TestCase):
    """게이트 판정 사유가 남는지 검증한다.

    사유가 없으면 "왜 즉시매수가 한 건도 안 나왔나"를 사후에 알 수 없다
    (2026-07-22: BUY_READY 로그 0건인데 게이트 차단인지 judge 미선택인지 구분 불가였다).
    """

    def setUp(self) -> None:
        from execution import single_symbol_judge as m

        m._BUY_READY_GATE_LOGGED.clear()

    def test_reason_is_logged_once_per_market_and_reason(self) -> None:
        from execution import single_symbol_judge as m

        with patch("logger.get_trading_logger") as logger:
            m._log_buy_ready_gate("US", False, "buy_ready_regime_blocked:CAUTIOUS",
                                  {"market_regime": "CAUTIOUS"})
            m._log_buy_ready_gate("US", False, "buy_ready_regime_blocked:CAUTIOUS",
                                  {"market_regime": "CAUTIOUS"})   # 같은 조합 → 억제
            m._log_buy_ready_gate("KR", False, "buy_ready_market_not_allowed", {})
            self.assertEqual(logger.return_value.info.call_count, 2,
                             "같은 (시장,사유)는 1회만 남아야 한다")

    def test_gate_reasons_are_distinguishable(self) -> None:
        """차단 사유가 서로 구분되어야 원인 추적이 된다."""
        with patch.dict(os.environ, {**_GATE_ON,
                                     "SINGLE_SYMBOL_JUDGE_BUY_READY_MARKETS": "US",
                                     "SINGLE_SYMBOL_JUDGE_BUY_READY_REGIMES": "MILD_BULL"}):
            self.assertEqual(
                _immediate_buy_allowed("KR", {"market_regime": "MILD_BULL"})[1],
                "buy_ready_market_not_allowed")
            self.assertEqual(
                _immediate_buy_allowed("US", {"market_regime": "CAUTIOUS"})[1],
                "buy_ready_regime_blocked:CAUTIOUS")
            self.assertEqual(
                _immediate_buy_allowed("US", {})[1], "buy_ready_regime_unknown")
            self.assertTrue(
                _immediate_buy_allowed("US", {"market_regime": "MILD_BULL"})[0])


if __name__ == "__main__":
    unittest.main()
