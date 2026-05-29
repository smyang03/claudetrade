from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from minority_report.analysts import _candidate_quality_hint


class KrCandidateQualityPromptTests(unittest.TestCase):
    def test_quality_hint_is_env_gated(self) -> None:
        candidate = {"candidate_quality_grade": "A", "candidate_quality_score": 82}

        with patch.dict(os.environ, {"ENABLE_KR_CANDIDATE_QUALITY_PROMPT": "false"}, clear=False):
            self.assertEqual(_candidate_quality_hint(candidate), "")

        with patch.dict(os.environ, {"ENABLE_KR_CANDIDATE_QUALITY_PROMPT": "true"}, clear=False):
            self.assertIn("q=A82", _candidate_quality_hint(candidate))

    def test_quality_hint_compacts_rs_turnover_and_flow(self) -> None:
        candidate = {
            "candidate_quality_grade": "B",
            "candidate_quality_score": 66.4,
            "rs_20d_vs_board": 4.2,
            "turnover_vs_20d": 2.5,
            "foreign_net_qty_1d": 100,
            "institution_net_qty_1d": -50,
            "quality_data_gaps": ["index_history_missing"],
            "trainer_cohort_reliability": 0.31,
            "cohort_sample_n": 7,
            "trainer_tier": "C",
        }

        with patch.dict(os.environ, {"ENABLE_KR_CANDIDATE_QUALITY_PROMPT": "true"}, clear=False):
            hint = _candidate_quality_hint(candidate)

        self.assertIn("q=B66", hint)
        self.assertIn("rs20=+4.2", hint)
        self.assertIn("turn20=2.5x", hint)
        self.assertIn("flow=F1+I1-", hint)
        self.assertIn("qgap=1", hint)
        self.assertIn("cohort=low n=7", hint)
        self.assertIn("tier=C", hint)

    def test_quality_hint_marks_all_zero_flow_unavailable(self) -> None:
        candidate = {
            "candidate_quality_grade": "C",
            "candidate_quality_score": 51,
            "foreign_net_qty_1d": 0,
            "institution_net_qty_1d": 0,
            "flow_data_quality": "bad_zero_flow_cluster",
            "flow_quality_flags": ["kr_investor_flow_all_zero_cluster"],
        }

        with patch.dict(os.environ, {"ENABLE_KR_CANDIDATE_QUALITY_PROMPT": "true"}, clear=False):
            hint = _candidate_quality_hint(candidate)

        self.assertIn("flow=unavailable:all_zero_cluster", hint)
        self.assertNotIn("flow=F", hint)

    def test_trainer_hint_regression_keeps_existing_score_fields(self) -> None:
        from minority_report.analysts import _candidate_trainer_hint

        candidate = {
            "trainer_candidate_state": "PLAN_A",
            "trainer_prompt_score": 77,
            "trainer_plan_a_score": 70,
            "trainer_pathb_wait_score": 80,
            "trainer_risk_score": 20,
        }

        hint = _candidate_trainer_hint(candidate)

        self.assertIn("PLAN_A", hint)
        self.assertIn("q=77", hint)
        self.assertIn("pa=70", hint)
        self.assertIn("pb=80", hint)
        self.assertIn("risk=20", hint)


if __name__ == "__main__":
    unittest.main()
