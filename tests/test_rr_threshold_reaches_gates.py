from __future__ import annotations

import unittest
from unittest.mock import patch

from decision.claude_price_plan import PricePlan
from execution.single_symbol_judge import judge_min_reward_risk
from runtime.selection_compact_schema import _compact_price_targets


class JudgeMarketRewardRiskTests(unittest.TestCase):
    """★2026-07-13: judge가 시장 무관 단일 RR(1.5)로 잘라 운영자가 연 KR 1.1 밴드가 상류에서 죽었다.

    실측: 7월 KR 플랜 RR 최소 1.642 — 1.1~1.5 밴드 플랜이 한 건도 생성되지 않았다(통과율 0%).
    완화가 아니라 "설정대로 동작"이다.
    """

    def test_market_override_wins(self) -> None:
        env = {
            "SINGLE_SYMBOL_JUDGE_MIN_REWARD_RISK": "1.5",
            "SINGLE_SYMBOL_JUDGE_MIN_REWARD_RISK_KR": "1.1",
            "SINGLE_SYMBOL_JUDGE_MIN_REWARD_RISK_US": "1.5",
        }
        with patch.dict("os.environ", env, clear=False):
            self.assertAlmostEqual(judge_min_reward_risk("KR"), 1.1)
            self.assertAlmostEqual(judge_min_reward_risk("US"), 1.5)

    def test_falls_back_to_global_when_no_override(self) -> None:
        env = {"SINGLE_SYMBOL_JUDGE_MIN_REWARD_RISK": "1.5"}
        with patch.dict("os.environ", env, clear=False):
            with patch.dict("os.environ", {}, clear=False):
                import os

                os.environ.pop("SINGLE_SYMBOL_JUDGE_MIN_REWARD_RISK_KR", None)
                os.environ.pop("SINGLE_SYMBOL_JUDGE_MIN_REWARD_RISK_US", None)
                self.assertAlmostEqual(judge_min_reward_risk("KR"), 1.5)

    def test_unknown_market_is_treated_as_kr(self) -> None:
        env = {"SINGLE_SYMBOL_JUDGE_MIN_REWARD_RISK_KR": "1.1"}
        with patch.dict("os.environ", env, clear=False):
            self.assertAlmostEqual(judge_min_reward_risk(""), 1.1)


class PlanValidateHonorsMarketRrTests(unittest.TestCase):
    """PricePlan.validate의 기본값 1.2가 호출부에서 넘긴 시장별 임계로 대체돼야 한다."""

    def _plan(self, *, zone_high: float, target: float, stop: float) -> PricePlan:
        return PricePlan(
            decision_id="dec_x",
            path_run_id="run_x",
            ticker="005930",
            market="KR",
            session_date="2026-07-13",
            buy_zone_low=zone_high - 1.0,
            buy_zone_high=zone_high,
            sell_target=target,
            stop_loss=stop,
            hold_days=2,
            confidence=0.6,
            prompt_stage="PRE_SESSION",
        )

    def test_rr_between_kr_and_default_is_rejected_by_default_but_passes_with_kr_threshold(self) -> None:
        # 라이브와 동일하게 위험 분모를 존 상단으로 고정한다(PATHB_CONSISTENT_REWARD_RISK=true).
        # RR = (target - zone_high) / (zone_high - stop) = 1.15 → 기본 1.2엔 걸리고 KR 1.1엔 통과
        plan = self._plan(zone_high=100.0, target=111.5, stop=90.0)
        with patch.dict("os.environ", {"PATHB_CONSISTENT_REWARD_RISK": "true"}, clear=False):
            self.assertIn("reward_risk_below_minimum", plan.validate(min_confidence=0.5))
            self.assertNotIn(
                "reward_risk_below_minimum",
                plan.validate(min_confidence=0.5, min_reward_risk=1.1),
            )

    def test_us_threshold_is_stricter_than_default(self) -> None:
        # RR = 1.3 → 기본 1.2는 통과하지만 US 1.5는 거부해야 한다
        plan = self._plan(zone_high=100.0, target=113.0, stop=90.0)
        with patch.dict("os.environ", {"PATHB_CONSISTENT_REWARD_RISK": "true"}, clear=False):
            self.assertNotIn("reward_risk_below_minimum", plan.validate(min_confidence=0.5))
            self.assertIn(
                "reward_risk_below_minimum",
                plan.validate(min_confidence=0.5, min_reward_risk=1.5),
            )


class CompactSchemaTargetBasisTests(unittest.TestCase):
    """★target_basis 복원: compact schema 화이트리스트 밖이라 통째로 버려졌다.

    plan_json 실측: 4월 152건 → 6·7월 0건. target_basis는 목표 캘리브레이션 레버의 진단 입력이다.
    서술 필드라 숫자 파서(_to_float)로 처리하면 조용히 None이 되어 사라진다.
    """

    def test_target_basis_survives(self) -> None:
        warnings: list[str] = []
        out = _compact_price_targets(
            {"lo": 100, "hi": 102, "tgt": 110, "stp": 97, "days": 2, "conf": 0.6,
             "target_basis": "OR high + 1.5R"},
            reference_price=101.0,
            warnings=warnings,
        )
        self.assertEqual(out["target_basis"], "OR high + 1.5R")
        self.assertNotIn("price_target_extra_keys", warnings)

    def test_short_key_alias_works(self) -> None:
        out = _compact_price_targets(
            {"lo": 100, "hi": 102, "tgt": 110, "stp": 97, "days": 2, "conf": 0.6, "tb": "prior swing"},
            reference_price=101.0,
            warnings=[],
        )
        self.assertEqual(out["target_basis"], "prior swing")

    def test_absent_target_basis_is_not_invented(self) -> None:
        out = _compact_price_targets(
            {"lo": 100, "hi": 102, "tgt": 110, "stp": 97, "days": 2, "conf": 0.6},
            reference_price=101.0,
            warnings=[],
        )
        self.assertNotIn("target_basis", out)

    def test_long_target_basis_is_truncated(self) -> None:
        out = _compact_price_targets(
            {"lo": 100, "hi": 102, "tgt": 110, "stp": 97, "days": 2, "conf": 0.6,
             "target_basis": "x" * 500},
            reference_price=101.0,
            warnings=[],
        )
        self.assertLessEqual(len(out["target_basis"]), 120)


if __name__ == "__main__":
    unittest.main()
