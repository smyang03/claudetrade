"""진입 품질 게이트 검증: PATHB_MIN_REWARD_RISK / US_MIDDAY_ENTRY_BLOCK."""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
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

    def test_default_threshold_uses_single_source_policy(self):
        # No hidden 1.2 default: parsing uses the effective PATHB policy.
        with patch.dict("os.environ", {"PATHB_MIN_REWARD_RISK": "1.5"}, clear=False):
            plan, errors = _parse(_raw_plan(target=102.6))
        self.assertIsNone(plan)
        self.assertIn("reward_risk_below_minimum", errors)

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

    def _dummy(self, overrides):
        return types.SimpleNamespace(
            _runtime_float=lambda key, default=0.0: overrides.get(key, default),
            _runtime_value=lambda key, default=None: overrides.get(key, default),
        )

    def test_market_override_kr_used_when_set(self):
        dummy = self._dummy({"PATHB_MIN_REWARD_RISK_KR": "1.1"})
        self.assertEqual(PathBRuntime._pathb_min_reward_risk(dummy, "KR"), 1.1)
        # US는 override 미설정 → 글로벌 기본 유지
        self.assertEqual(PathBRuntime._pathb_min_reward_risk(dummy, "US"), 1.5)

    def test_market_override_unset_falls_back_to_global(self):
        dummy = self._dummy({"PATHB_MIN_REWARD_RISK": 1.5})
        self.assertEqual(PathBRuntime._pathb_min_reward_risk(dummy, "KR"), 1.5)

    def test_market_override_invalid_falls_back(self):
        dummy = self._dummy({"PATHB_MIN_REWARD_RISK_KR": "abc"})
        self.assertEqual(PathBRuntime._pathb_min_reward_risk(dummy, "KR"), 1.5)


class UsMiddayMultiHourBlockTests(unittest.TestCase):
    """차단 시간대 복수 지원 — 64개 조합 스캔에서 15+16시가 최선이었다.

    게이트 통과 건 기준: 16시만 +14.10%(현행·7위) / 15+16시 +20.92%(+6.82%p).
    17시는 +9.77%라 확장 대상이 아니고, 18시(n=10)·19시(n=7)는 표본이 작아 제외한다.
    값은 현행 16시를 유지하며(오늘 이미 두 레버를 바꿔 원인 분리가 필요), 배선만 넣는다.
    """

    def _state(self, market, values):
        dummy = types.SimpleNamespace(
            _runtime_bool=lambda key, default=False: bool(values.get(key, default)),
            _runtime_int=lambda key, default=0: int(values.get(key, default)),
            _runtime_value=lambda key, default=None: values.get(key, default),
        )
        return PathBRuntime._pathb_us_midday_entry_block_state(dummy, market)

    def test_multi_hour_list_is_honored(self) -> None:
        now = datetime.now(timezone.utc).hour
        other = (now + 3) % 24
        st = self._state("US", {
            "US_MIDDAY_ENTRY_BLOCK_ENABLED": True,
            "US_MIDDAY_ENTRY_BLOCK_UTC_HOURS": f"{now},{other}",
        })
        self.assertEqual(sorted(st["block_hours_utc"]), sorted([now, other]))
        self.assertTrue(st["blocked_now"])

    def test_falls_back_to_single_key_when_absent(self) -> None:
        now = datetime.now(timezone.utc).hour
        st = self._state("US", {
            "US_MIDDAY_ENTRY_BLOCK_ENABLED": True,
            "US_MIDDAY_ENTRY_BLOCK_UTC_HOUR": now,
        })
        self.assertEqual(st["block_hours_utc"], [now])
        self.assertTrue(st["blocked_now"])

    def test_blank_multi_key_falls_back(self) -> None:
        now = datetime.now(timezone.utc).hour
        st = self._state("US", {
            "US_MIDDAY_ENTRY_BLOCK_ENABLED": True,
            "US_MIDDAY_ENTRY_BLOCK_UTC_HOURS": "   ",
            "US_MIDDAY_ENTRY_BLOCK_UTC_HOUR": now,
        })
        self.assertEqual(st["block_hours_utc"], [now])

    def test_malformed_tokens_are_skipped_not_fatal(self) -> None:
        """오염된 값이 진입을 전면 차단하거나 예외를 내지 않는다."""
        now = datetime.now(timezone.utc).hour
        st = self._state("US", {
            "US_MIDDAY_ENTRY_BLOCK_ENABLED": True,
            "US_MIDDAY_ENTRY_BLOCK_UTC_HOURS": f"abc,,{now}",
        })
        self.assertEqual(st["block_hours_utc"], [now])
        self.assertTrue(st["blocked_now"])

    def test_kr_unaffected(self) -> None:
        st = self._state("KR", {"US_MIDDAY_ENTRY_BLOCK_ENABLED": True,
                                "US_MIDDAY_ENTRY_BLOCK_UTC_HOURS": "15,16"})
        self.assertFalse(st["active"])
        self.assertFalse(st["blocked_now"])


class UsWeekdayEntryBlockTests(unittest.TestCase):
    """US 요일 진입 게이트 — 기본 shadow(관측만).

    두 독립 소스가 같은 방향을 지지한다(2026-07-22):
      forward(요일당 2,600~4,200건): 금 비대칭 1.51/+1.25% … 월 0.56/-2.37%
      우리 net(국면게이트 통과분): 금 +40.11%(승률 55.0%) / 화 -39.79%(n=26, 승률 11.5%)
    화요일만 차단해도 +39.55%p이고 거래는 15%만 준다. 다만 표본이 작고 "5개 중 최악을
    뺀다"는 구조라 과적합 위험이 있어 기본은 shadow다.
    """

    def _state(self, market, values):
        dummy = types.SimpleNamespace(_runtime_value=lambda key, default=None: values.get(key, default))
        return PathBRuntime._pathb_us_weekday_entry_block_state(dummy, market)

    def _today_et(self) -> int:
        return (datetime.now(timezone.utc) - timedelta(hours=4)).weekday()

    def test_shadow_observes_without_blocking(self) -> None:
        st = self._state("US", {"US_WEEKDAY_ENTRY_BLOCK_DAYS": str(self._today_et())})
        self.assertTrue(st["active"])
        self.assertTrue(st["would_block"])
        self.assertFalse(st["blocked_now"], "shadow는 관측만 하고 막지 않는다")

    def test_enforce_blocks_target_day(self) -> None:
        st = self._state("US", {
            "US_WEEKDAY_ENTRY_BLOCK_MODE": "enforce",
            "US_WEEKDAY_ENTRY_BLOCK_DAYS": str(self._today_et()),
        })
        self.assertTrue(st["blocked_now"])

    def test_other_day_not_blocked(self) -> None:
        other = (self._today_et() + 2) % 7
        st = self._state("US", {
            "US_WEEKDAY_ENTRY_BLOCK_MODE": "enforce",
            "US_WEEKDAY_ENTRY_BLOCK_DAYS": str(other),
        })
        self.assertFalse(st["would_block"])
        self.assertFalse(st["blocked_now"])

    def test_mode_off_disables(self) -> None:
        st = self._state("US", {"US_WEEKDAY_ENTRY_BLOCK_MODE": "off"})
        self.assertFalse(st["active"])

    def test_empty_days_disables(self) -> None:
        """요일 목록이 비면 게이트를 끈다 — 빈 값이 전면 차단이 되지 않게."""
        st = self._state("US", {"US_WEEKDAY_ENTRY_BLOCK_DAYS": "  "})
        self.assertFalse(st["active"])

    def test_malformed_tokens_skipped(self) -> None:
        st = self._state("US", {"US_WEEKDAY_ENTRY_BLOCK_DAYS": f"abc,9,{self._today_et()}"})
        self.assertEqual(st["block_days"], [self._today_et()])

    def test_kr_unaffected(self) -> None:
        st = self._state("KR", {"US_WEEKDAY_ENTRY_BLOCK_MODE": "enforce"})
        self.assertFalse(st["active"])
        self.assertFalse(st["blocked_now"])


if __name__ == "__main__":
    unittest.main()
