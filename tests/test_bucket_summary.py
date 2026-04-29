from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from interface.bucket_summary import build_bucket_summary


class BucketSummaryTests(unittest.TestCase):
    def test_missing_file_returns_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = build_bucket_summary(market="KR", session_date="2026-04-28", log_dir=tmp, allow_judgment_fallback=False)

            self.assertTrue(summary["missing"])
            self.assertEqual(summary["row_count"], 0)
            self.assertIn("NO_BUCKET_DATA", summary["warnings"])

    def test_summary_aggregates_latest_bucket_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "20260428_KR_candidates.jsonl"
            rows = [
                {
                    "timestamp": "2026-04-28T09:05:00",
                    "market": "KR",
                    "ticker": "001510",
                    "name": "SK증권",
                    "market_type": "KOSPI",
                    "primary_bucket": "volume_surge",
                    "secondary_buckets": ["momentum_now"],
                    "status": "TRADE_READY",
                    "input_to_claude": True,
                    "forward_30m_from_bucket": 2.5,
                    "price": 5000,
                    "change_rate": 8.0,
                },
                {
                    "timestamp": "2026-04-28T09:06:00",
                    "market": "KR",
                    "ticker": "138360",
                    "name": "협진",
                    "market_type": "KOSDAQ",
                    "primary_bucket": "pre_move_setup",
                    "secondary_buckets": [],
                    "status": "NOT_IN_PROMPT",
                    "input_to_claude": False,
                    "max_runup_close_from_bucket": 6.0,
                    "price": 3000,
                    "change_rate": 1.0,
                },
            ]
            path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

            summary = build_bucket_summary(market="KR", session_date="2026-04-28", log_dir=tmp)
            by_bucket = {row["primary_bucket"]: row for row in summary["buckets"]}

            self.assertFalse(summary["missing"])
            self.assertEqual(summary["unique_candidate_count"], 2)
            self.assertEqual(by_bucket["volume_surge"]["trade_ready"], 1)
            self.assertEqual(by_bucket["volume_surge"]["winner_30m"], 1)
            self.assertEqual(by_bucket["pre_move_setup"]["not_in_prompt"], 1)
            self.assertEqual(by_bucket["pre_move_setup"]["missed_winner"], 1)
            self.assertIn("PRE_MOVE_NOT_IN_PROMPT", summary["warnings"])
            self.assertEqual(summary["candidates"][0]["display_ticker"], "협진 (138360)")

    def test_summary_falls_back_to_daily_judgment_when_quality_log_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            judgment_dir = Path(tmp) / "logs" / "daily_judgment"
            judgment_dir.mkdir(parents=True)
            path = judgment_dir / "live_20260428_KR.json"
            path.write_text(
                json.dumps(
                    {
                        "market": "KR",
                        "universe_tickers": ["001510"],
                        "tickers": ["001510"],
                        "trade_ready_tickers": ["001510"],
                        "selection_meta": {
                            "watchlist": ["001510"],
                            "trade_ready": ["001510"],
                            "reasons": {"001510": "강한 장초 모멘텀"},
                        },
                        "digest_raw": {
                            "built_at": "2026-04-28T09:05:00",
                            "technicals": {
                                "001510": {
                                    "name": "SK증권",
                                    "close": 5000,
                                    "change_pct": 8.0,
                                    "vol_ratio": 5.0,
                                }
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            import interface.bucket_summary as bucket_summary

            old_get_runtime_path = bucket_summary.get_runtime_path
            try:
                bucket_summary.get_runtime_path = lambda *parts, make_parents=True: Path(tmp).joinpath(*parts)
                summary = bucket_summary.build_bucket_summary(market="KR", session_date="2026-04-28", runtime_mode="live", log_dir=Path(tmp) / "no_quality")
            finally:
                bucket_summary.get_runtime_path = old_get_runtime_path

            self.assertFalse(summary["missing"])
            self.assertEqual(summary["source"], "daily_judgment_fallback")
            self.assertEqual(summary["unique_candidate_count"], 1)
            self.assertEqual(summary["candidates"][0]["display_ticker"], "SK증권 (001510)")
            self.assertEqual(summary["candidates"][0]["status"], "TRADE_READY")


if __name__ == "__main__":
    unittest.main()
