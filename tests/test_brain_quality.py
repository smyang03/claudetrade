from __future__ import annotations

import unittest
from datetime import date

from claude_memory import brain


class BrainQualityHelperTests(unittest.TestCase):
    def test_clean_prompt_text_list_drops_placeholders_and_mojibake(self) -> None:
        cleaned = brain._clean_prompt_text_list([
            "정상 교훈",
            "오류로 자동 판정",
            "?\uc10f\uc521 \uf9e3?\uad9b ?\uc88f\uc29a ?\u2466\uaf69: profit_floor",
            "정상 교훈",
        ])

        self.assertEqual(cleaned, ["정상 교훈"])

    def test_issue_pattern_requires_readable_description(self) -> None:
        self.assertFalse(brain._is_valid_issue_pattern({"description": "", "insight": "오류로 자동 판정"}))
        self.assertTrue(
            brain._is_valid_issue_pattern({
                "description": "watch_only missed runup high",
                "insight": "trade_ready 기준 재검토",
            })
        )

    def test_tuning_summary_uses_adjustment_semantics(self) -> None:
        summary = brain._format_tuning_pattern_summary(
            {
                "30min_tune": {
                    "count": 4,
                    "correct": 3,
                    "rate": 0.75,
                    "adjusted": 3,
                    "adjusted_rate": 0.75,
                    "metric_semantics": "adjusted_not_accuracy",
                    "last_seen": "2026-05-01",
                    "insight": "volatility tightened",
                }
            },
            date(2026, 5, 1),
        )

        self.assertIn("4건 중 3건 조정", summary)
        self.assertNotIn("적중", summary)


if __name__ == "__main__":
    unittest.main()
