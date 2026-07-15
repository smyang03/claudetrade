from __future__ import annotations

import unittest

from tools.audit_early_judge_recheck_pipeline import build_report


class EarlyJudgeRecheckPipelineAuditTests(unittest.TestCase):
    def test_kr_and_us_pipeline_matrix_has_no_missing_or_orphaned_flow(self) -> None:
        report = build_report()

        self.assertEqual(report["record_count"], 8)
        self.assertEqual(report["fail_count"], 0)
        self.assertEqual(report["pass_count"], 8)
        self.assertEqual({row["market"] for row in report["records"]}, {"KR", "US"})
        self.assertEqual(
            {row["pipeline"] for row in report["records"]},
            {
                "entry_blackout_replay",
                "wait_recheck_consumer",
                "watch_signal_to_claude",
                "adaptive_reask_to_claude",
            },
        )
        for row in report["records"]:
            self.assertEqual(row["status"], "PASS", row)
            self.assertTrue(all(row["checks"].values()), row)


if __name__ == "__main__":
    unittest.main()
