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
        }

        with patch.dict(os.environ, {"ENABLE_KR_CANDIDATE_QUALITY_PROMPT": "true"}, clear=False):
            hint = _candidate_quality_hint(candidate)

        self.assertIn("q=B66", hint)
        self.assertIn("rs20=+4.2", hint)
        self.assertIn("turn20=2.5x", hint)
        self.assertIn("flow=F1+I1-", hint)
        self.assertIn("qgap=1", hint)


if __name__ == "__main__":
    unittest.main()
