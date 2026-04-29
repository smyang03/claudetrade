from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from bot.screener_quality import opening_fresh_quality_metrics, write_candidate_quality_log


class ScreenerQualityTests(unittest.TestCase):
    def test_write_candidate_quality_log_records_prompt_and_not_in_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quality.jsonl"
            summary = write_candidate_quality_log(
                market="KR",
                phase="opening_refresh",
                raw_candidates=[
                    {"ticker": "001510", "name": "SK증권", "price": 5370, "change_rate": 10.5, "volume": 1000, "market_type": "KOSPI", "vol_ratio": 5.0},
                    {"ticker": "138360", "name": "협진", "price": 3840, "change_rate": 8.1, "volume": 900, "market_type": "KOSDAQ"},
                ],
                prompt_candidates=[
                    {"ticker": "001510", "name": "SK증권", "price": 5370, "change_rate": 10.5, "volume": 1000, "market_type": "KOSPI"},
                ],
                selected=["001510"],
                selection_meta={"trade_ready": ["001510"], "watchlist": ["001510"]},
                reasons={"001510": "strong opening momentum"},
                now=datetime(2026, 4, 28, 9, 5, 0),
                path=path,
                bucket_state_path=Path(tmp) / "bucket_state.json",
            )

            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

            self.assertEqual(summary["rows"], 2)
            by_ticker = {row["ticker"]: row for row in rows}
            self.assertEqual(by_ticker["001510"]["status"], "TRADE_READY")
            self.assertTrue(by_ticker["001510"]["input_to_claude"])
            self.assertEqual(by_ticker["138360"]["status"], "NOT_IN_PROMPT")
            self.assertFalse(by_ticker["138360"]["input_to_claude"])
            self.assertEqual(by_ticker["138360"]["excluded_reason"], "not_in_prompt")
            self.assertEqual(by_ticker["001510"]["primary_bucket"], "volume_surge")
            self.assertIn("momentum_now", by_ticker["001510"]["secondary_buckets"])
            self.assertEqual(by_ticker["001510"]["first_bucket_detected_at"], "2026-04-28T09:05:00")
            self.assertIn("score_vol_ratio_capped", by_ticker["001510"])

    def test_opening_fresh_quality_metrics_triggers_when_top_gainers_missing(self) -> None:
        metrics = opening_fresh_quality_metrics(
            market="KR",
            prompt_tickers=["001510"],
            raw_candidates=[
                {"ticker": "001510", "price": 5000, "volume": 1000, "change_rate": 20.0},
                {"ticker": "138360", "price": 4000, "volume": 400000, "change_rate": 18.0},
                {"ticker": "002780", "price": 1500, "volume": 1000000, "change_rate": 15.0},
                {"ticker": "047040", "price": 3500, "volume": 900000, "change_rate": 12.0},
            ],
            top_n=4,
        )

        self.assertTrue(metrics["judge_triggered"])
        self.assertEqual(metrics["not_in_prompt"], 3)
        self.assertIn("new_top_gainer_not_in_prompt>=3", metrics["trigger_reason"])

    def test_opening_fresh_quality_metrics_triggers_when_trade_ready_weakens(self) -> None:
        metrics = opening_fresh_quality_metrics(
            market="KR",
            prompt_tickers=["001510", "002780"],
            current_trade_ready=["001510", "002780"],
            raw_candidates=[
                {"ticker": "001510", "price": 5000, "volume": 1000, "change_rate": -0.2},
                {"ticker": "002780", "price": 1500, "volume": 1000, "change_rate": 0.0},
            ],
            top_n=2,
        )

        self.assertTrue(metrics["judge_triggered"])
        self.assertEqual(metrics["existing_trade_ready_weakened"], 2)
        self.assertIn("existing_trade_ready_weakened>=2", metrics["trigger_reason"])


if __name__ == "__main__":
    unittest.main()
