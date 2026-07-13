from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from tools import profit_path_forward_monitor as monitor
from tools import train_profit_path_shadow as trainer


def _portable(*, ceiling: float, selected_n: int, promotion: bool = False) -> dict:
    return {
        "metadata": {
            "model_version": "profit_path_shadow_KR_test",
            "validation_selected_n": selected_n,
            "promotion_eligible_backtest": promotion,
        },
        "probability_calibrator": {"x": [0.1, 0.5, 0.9], "y": [0.05, 0.3, ceiling]},
    }


class PolicyViabilityTests(unittest.TestCase):
    """트레이너 fail-fast: 발화 불가 정책을 런타임에 배포하지 않는다."""

    def test_ceiling_below_hurdle_is_dead_policy(self) -> None:
        # IsotonicRegression(out_of_bounds="clip")이라 보정확률 상한 = 학습 y 최댓값.
        # 상한이 임계보다 낮으면 어떤 입력에도 p >= 임계가 성립하지 않는다.
        with patch.dict("os.environ", {"PROFIT_EVIDENCE_MIN_PROB": "0.55"}, clear=False):
            result = trainer.policy_viability(_portable(ceiling=0.4917, selected_n=0), market="KR")
        self.assertFalse(result["policy_can_fire"])
        self.assertIn("calibrated_probability_ceiling_below_hurdle", result["blockers"])
        self.assertIn("validation_policy_selects_nothing", result["blockers"])

    def test_selected_nothing_is_dead_even_when_ceiling_ok(self) -> None:
        with patch.dict("os.environ", {"PROFIT_EVIDENCE_MIN_PROB": "0.55"}, clear=False):
            result = trainer.policy_viability(_portable(ceiling=0.80, selected_n=0), market="KR")
        self.assertFalse(result["policy_can_fire"])
        self.assertEqual(result["blockers"], ["validation_policy_selects_nothing"])

    def test_viable_policy_passes(self) -> None:
        with patch.dict("os.environ", {"PROFIT_EVIDENCE_MIN_PROB": "0.55"}, clear=False):
            result = trainer.policy_viability(_portable(ceiling=0.72, selected_n=45), market="KR")
        self.assertTrue(result["policy_can_fire"])
        self.assertEqual(result["blockers"], [])

    def test_market_specific_hurdle_is_honored(self) -> None:
        env = {"PROFIT_EVIDENCE_MIN_PROB": "0.55", "PROFIT_EVIDENCE_MIN_PROB_US": "0.25"}
        with patch.dict("os.environ", env, clear=False):
            result = trainer.policy_viability(_portable(ceiling=0.2975, selected_n=30), market="US")
        self.assertTrue(result["policy_can_fire"])

    def test_path_specific_hurdles_are_honored(self) -> None:
        env = {
            "PROFIT_EVIDENCE_MIN_PROB": "0.55",
            "PROFIT_EVIDENCE_MIN_PROB_US_PATH_A": "0.60",
            "PROFIT_EVIDENCE_MIN_PROB_US_PATH_B": "0.25",
        }
        with patch.dict("os.environ", env, clear=False):
            result = trainer.policy_viability(_portable(ceiling=0.30, selected_n=3), market="US")
        self.assertTrue(result["policy_can_fire"])
        self.assertEqual(result["probability_hurdles_by_path"], {"PATH_A": 0.60, "PATH_B": 0.25})

    def test_validation_rows_use_the_same_path_specific_thresholds_as_runtime(self) -> None:
        env = {
            "PROFIT_EVIDENCE_MIN_PROB": "0.55",
            "PROFIT_EVIDENCE_MIN_PROB_KR_PATH_A": "0.65",
            "PROFIT_EVIDENCE_MIN_PROB_KR_PATH_B": "0.35",
        }
        frame = pd.DataFrame({"recommended_strategy": ["momentum", "claude_price"]})
        with patch.dict("os.environ", env, clear=False):
            thresholds = trainer.policy_thresholds("KR")
            values = trainer._threshold_array(frame, thresholds=thresholds, key="min_probability")
        self.assertEqual(values.tolist(), [0.65, 0.35])

    def test_shipped_artifacts_are_currently_dead(self) -> None:
        # 회귀 고정: 현재 배포된 KR/US 아티팩트는 발화 불가다(2026-07-13 실측).
        # 이 테스트가 깨지면 모델이 재설계된 것 — 그때 승격 논의를 재개한다.
        for market in ("KR", "US"):
            path = Path("state/models") / f"profit_path_{market}.json"
            if not path.exists():
                continue
            portable = json.loads(path.read_text(encoding="utf-8"))
            with patch.dict("os.environ", {"PROFIT_EVIDENCE_MIN_PROB": "0.55"}, clear=False):
                result = trainer.policy_viability(portable, market=market)
            self.assertFalse(result["policy_can_fire"], f"{market} 정책이 발화 가능해졌다면 승격 논의 재개")
            self.assertLess(result["calibrated_probability_ceiling"], result["probability_hurdle"])


class PromotionBlockersTests(unittest.TestCase):
    """모니터: evaluable_n 증가를 승격 진행률로 오독하지 않게 한다."""

    def test_missing_artifact_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = monitor.promotion_blockers("KR", models_dir=Path(tmp))
        self.assertTrue(result["promotion_blocked"])
        self.assertEqual(result["reasons"], ["model_artifact_missing"])

    def test_ceiling_below_hurdle_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profit_path_KR.json"
            path.write_text(json.dumps(_portable(ceiling=0.4917, selected_n=0)), encoding="utf-8")
            with patch.dict("os.environ", {"PROFIT_EVIDENCE_MIN_PROB": "0.55"}, clear=False):
                result = monitor.promotion_blockers("KR", models_dir=Path(tmp))
        self.assertTrue(result["promotion_blocked"])
        self.assertIn("calibrated_probability_ceiling_below_hurdle", result["reasons"])

    def test_healthy_artifact_is_not_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profit_path_KR.json"
            path.write_text(
                json.dumps(_portable(ceiling=0.72, selected_n=45, promotion=True)), encoding="utf-8"
            )
            with patch.dict("os.environ", {"PROFIT_EVIDENCE_MIN_PROB": "0.55"}, clear=False):
                result = monitor.promotion_blockers("KR", models_dir=Path(tmp))
        self.assertFalse(result["promotion_blocked"])
        self.assertEqual(result["reasons"], [])

    def test_threshold_change_after_training_blocks_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profit_path_KR.json"
            portable = _portable(ceiling=0.72, selected_n=45, promotion=True)
            portable["metadata"]["policy_thresholds"] = {
                "PATH_A": {"min_probability": 0.55, "min_expected_net_pct": 0.25, "max_uncertainty": 0.25},
                "PATH_B": {"min_probability": 0.55, "min_expected_net_pct": 0.25, "max_uncertainty": 0.25},
            }
            path.write_text(json.dumps(portable), encoding="utf-8")
            with patch.dict("os.environ", {"PROFIT_EVIDENCE_MIN_PROB_KR_PATH_B": "0.40"}, clear=False):
                result = monitor.promotion_blockers("KR", models_dir=Path(tmp))
        self.assertTrue(result["promotion_blocked"])
        self.assertIn("runtime_policy_thresholds_changed_since_training", result["reasons"])

    def test_combined_promotion_ready_cannot_bypass_artifact_blocker(self) -> None:
        predictions = pd.DataFrame(
            [
                {
                    "event_id": 1,
                    "market": "KR",
                    "session_date": "2026-07-13",
                    "ticker": "000100",
                    "prediction_ts": pd.Timestamp("2026-07-13T00:10:00Z"),
                    "path_name": "immediate",
                    "model_version": "v1",
                    "probability": 0.9,
                    "evaluable": True,
                    "abstain_reason": "",
                    "strategy": "path_a",
                },
                {
                    "event_id": 2,
                    "market": "KR",
                    "session_date": "2026-07-14",
                    "ticker": "000200",
                    "prediction_ts": pd.Timestamp("2026-07-14T00:10:00Z"),
                    "path_name": "immediate",
                    "model_version": "v1",
                    "probability": 0.1,
                    "evaluable": True,
                    "abstain_reason": "",
                    "strategy": "path_a",
                },
            ]
        )
        outcomes = pd.DataFrame(
            [
                {
                    "path_id": 1, "market": "KR", "session_date": "2026-07-13", "ticker_key": "000100",
                    "path_name": "immediate", "outcome_ts": pd.Timestamp("2026-07-13T00:10:00Z"),
                    "outcome_60m_pct": 1.0, "max_drawdown_60m_pct": -0.1,
                },
                {
                    "path_id": 2, "market": "KR", "session_date": "2026-07-14", "ticker_key": "000200",
                    "path_name": "immediate", "outcome_ts": pd.Timestamp("2026-07-14T00:10:00Z"),
                    "outcome_60m_pct": 0.31, "max_drawdown_60m_pct": -0.1,
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            report = monitor.build_report(
                predictions,
                outcomes,
                market="KR",
                min_matched=2,
                min_sessions=2,
                now=pd.Timestamp("2026-07-15T05:00:00Z"),
                models_dir=Path(tmp),
            )
        self.assertTrue(report["promotion_eligible_forward"])
        self.assertTrue(report["promotion_blockers"]["promotion_blocked"])
        self.assertFalse(report["promotion_ready"])


if __name__ == "__main__":
    unittest.main()
