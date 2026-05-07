from __future__ import annotations

import unittest

from runtime.future_blind_replay import build_replay_baseline, filter_known_snapshots


class FutureBlindReplayTests(unittest.TestCase):
    def test_filter_known_snapshots_excludes_future_data(self) -> None:
        used, skipped = filter_known_snapshots(
            [
                {"ticker": "A", "known_at": "2026-05-06T09:05:00"},
                {"ticker": "B", "known_at": "2026-05-06T09:30:00"},
            ],
            decision_at="2026-05-06T09:05:00",
        )

        self.assertEqual([row["ticker"] for row in used], ["A"])
        self.assertEqual([row["ticker"] for row in skipped], ["B"])

    def test_baseline_marks_missing_funnel_and_future_skip(self) -> None:
        result = build_replay_baseline(
            scenario="baseline_actual",
            decision_at="2026-05-06T09:05:00",
            snapshots=[
                {"ticker": "A", "known_at": "2026-05-06T09:30:00"},
            ],
            missing_funnel_log=True,
        )

        self.assertEqual(result.used_snapshots, 0)
        self.assertEqual(result.skipped_future_snapshots, 1)
        self.assertIn("missing_funnel_log", result.notes)
        self.assertIn("future_snapshots_skipped", result.notes)


if __name__ == "__main__":
    unittest.main()
