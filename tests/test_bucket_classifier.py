from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from bot.bucket_classifier import (
    annotate_candidates_with_bucket_metadata,
    classify_candidate_bucket,
)


class BucketClassifierTests(unittest.TestCase):
    def test_primary_priority_uses_pre_move_before_liquidity(self) -> None:
        result = classify_candidate_bucket(
            {
                "ticker": "001510",
                "price": 5000,
                "volume": 3_000_000,
                "change_rate": 1.2,
                "vol_ratio": 2.0,
                "recent_strength_pct": 8.0,
                "above_ma60": True,
            },
            "KR",
        )

        self.assertEqual(result["primary_bucket"], "pre_move_setup")
        self.assertIn("liquidity_leader", result["secondary_buckets"])
        self.assertIn("score_vol_ratio_capped", result)

    def test_repeated_detection_keeps_first_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "bucket_state.json"
            first = datetime(2026, 4, 28, 9, 5, 0)
            second = datetime(2026, 4, 28, 9, 15, 0)
            candidate = {
                "ticker": "001510",
                "price": 5000,
                "volume": 3_000_000,
                "change_rate": 8.0,
                "vol_ratio": 5.0,
            }

            rows_1 = annotate_candidates_with_bucket_metadata(
                [candidate],
                market="KR",
                session_date="2026-04-28",
                detected_at=first,
                state_path=state_path,
            )
            rows_2 = annotate_candidates_with_bucket_metadata(
                [candidate],
                market="KR",
                session_date="2026-04-28",
                detected_at=second,
                state_path=state_path,
            )

            self.assertEqual(rows_1[0]["first_bucket_detected_at"], "2026-04-28T09:05:00")
            self.assertEqual(rows_2[0]["first_bucket_detected_at"], "2026-04-28T09:05:00")
            self.assertEqual(rows_2[0]["last_bucket_detected_at"], "2026-04-28T09:15:00")
            self.assertEqual(rows_2[0]["bucket_seen_count"], 2)

    def test_primary_bucket_change_creates_separate_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "bucket_state.json"
            first = datetime(2026, 4, 28, 9, 5, 0)
            second = datetime(2026, 4, 28, 10, 0, 0)

            annotate_candidates_with_bucket_metadata(
                [
                    {
                        "ticker": "001510",
                        "price": 5000,
                        "volume": 3_000_000,
                        "change_rate": 1.0,
                        "vol_ratio": 2.0,
                        "recent_strength_pct": 7.0,
                        "above_ma60": True,
                    }
                ],
                market="KR",
                session_date="2026-04-28",
                detected_at=first,
                state_path=state_path,
            )
            rows = annotate_candidates_with_bucket_metadata(
                [
                    {
                        "ticker": "001510",
                        "price": 5000,
                        "volume": 3_000_000,
                        "change_rate": 12.0,
                        "vol_ratio": 6.0,
                    }
                ],
                market="KR",
                session_date="2026-04-28",
                detected_at=second,
                state_path=state_path,
            )
            data = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual(rows[0]["primary_bucket"], "volume_surge")
            self.assertEqual(rows[0]["earliest_bucket_detected_at"], "2026-04-28T09:05:00")
            self.assertEqual(len(data["records"]), 2)


if __name__ == "__main__":
    unittest.main()
