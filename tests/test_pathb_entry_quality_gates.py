"""진입 품질 게이트 검증: PATHB_MIN_REWARD_RISK / US_MIDDAY_ENTRY_BLOCK."""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from decision.claude_price_plan import parse_plan_from_claude
from runtime.pathb_runtime import PathBRuntime


def _raw_plan(*, buy_low=98.0, buy_high=100.0, target=103.0, stop=96.0, confidence=0.6):
    # rr = (target - buy_high) / (buy_low - stop)
    return {
        "buy_zone_low": buy_low,
        "buy_zone_high": buy_high,
        "sell_target": target,
        "stop_loss": stop,
        "hold_days": 1,
        "confidence": confidence,
    }


def _parse(raw, *, min_reward_risk=None):
    return parse_plan_from_claude(
        decision_id="dec_test",
        ticker="TEST",
        market="US",
        session_date="2026-06-10",
        raw=raw,
        min_confidence=0.5,
        min_reward_risk=min_reward_risk,
    )


class MinRewardRiskGateTests(unittest.TestCase):
    def test_rr_below_threshold_is_rejected(self):
        # rr = (102.6 - 100) / (98 - 96) = 1.3 → 1.5 미달
        plan, errors = _parse(_raw_plan(target=102.6), min_reward_risk=1.5)
        self.assertIsNone(plan)
        self.assertIn("reward_risk_below_minimum", errors)

    def test_rr_above_threshold_is_accepted(self):
        # rr = (104 - 100) / (98 - 96) = 2.0
        plan, errors = _parse(_raw_plan(target=104.0), min_reward_risk=1.5)
        self.assertIsNotNone(plan)
        self.assertEqual(errors, [])

    def test_default_threshold_unchanged_for_reload_paths(self):
        # min_reward_risk 미지정(기존 호출처) → 기본 1.2 유지, rr=1.3 통과
        plan, errors = _parse(_raw_plan(target=102.6))
        self.assertIsNotNone(plan)
        self.assertEqual(errors, [])

    def test_declared_reward_risk_also_checked(self):
        raw = _raw_plan(target=104.0)
        raw["reward_risk"] = 1.3  # 선언값이 임계 미달이면 거부
        plan, errors = _parse(raw, min_reward_risk=1.5)
        self.assertIsNone(plan)
        self.assertIn("declared_reward_risk_below_minimum", errors)


class UsMiddayEntryBlockTests(unittest.TestCase):
    def _state(self, market, *, enabled=True, block_hour=None):
        now_hour = datetime.now(timezone.utc).hour
        resolved_block_hour = now_hour if block_hour is None else block_hour
        dummy = types.SimpleNamespace(
            _runtime_bool=lambda key, default=False: enabled,
            _runtime_int=lambda key, default=0: resolved_block_hour,
        )
        return PathBRuntime._pathb_us_midday_entry_block_state(dummy, market)

    def test_us_blocked_during_block_hour(self):
        state = self._state("US")
        self.assertTrue(state["active"])
        self.assertTrue(state["blocked_now"])
        self.assertEqual(state["reason"], "US_MIDDAY_ENTRY_BLOCK")

    def test_us_allowed_outside_block_hour(self):
        other_hour = (datetime.now(timezone.utc).hour + 1) % 24
        state = self._state("US", block_hour=other_hour)
        self.assertTrue(state["active"])
        self.assertFalse(state["blocked_now"])

    def test_kr_market_not_affected(self):
        state = self._state("KR")
        self.assertFalse(state["active"])
        self.assertFalse(state["blocked_now"])

    def test_disabled_env_inactive(self):
        state = self._state("US", enabled=False)
        self.assertFalse(state["active"])
        self.assertFalse(state["blocked_now"])


class MinRewardRiskRuntimeDefaultTests(unittest.TestCase):
    def test_runtime_default_is_1_5(self):
        dummy = types.SimpleNamespace(
            _runtime_float=lambda key, default=0.0: default,
        )
        self.assertEqual(PathBRuntime._pathb_min_reward_risk(dummy), 1.5)


if __name__ == "__main__":
    unittest.main()
