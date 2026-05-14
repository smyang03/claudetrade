from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
                    {
                        "ticker": "001510",
                        "candidate_quality_score": 72.5,
                        "candidate_quality_grade": "B",
                        "rs_20d_vs_board": 4.2,
                        "quality_data_gaps": ["flow_missing"],
                        "flow_window_5d_count": "3.0",
                    },
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
            self.assertEqual(by_ticker["001510"]["candidate_quality_grade"], "B")
            self.assertEqual(by_ticker["001510"]["candidate_quality_score"], 72.5)
            self.assertEqual(by_ticker["001510"]["rs_20d_vs_board"], 4.2)
            self.assertIn("flow_missing", by_ticker["001510"]["quality_data_gaps"])
            self.assertEqual(by_ticker["001510"]["flow_window_5d_count"], 3)

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

    def _us_candidates(self, count: int, category: str = "most_actives") -> list[dict]:
        prefix = {"most_actives": "A", "day_gainers": "G", "day_losers": "L"}.get(category, "T")
        return [
            {
                "ticker": f"{prefix}{chr(65 + (idx // 26) % 26)}{chr(65 + idx % 26)}",
                "name": f"Test {idx}",
                "price": 10.0 + idx,
                "change_rate": 1.0,
                "volume": 5_000_000,
                "vol_ratio": 1.0,
                "category": category,
                "exchange": "NMS",
                "fullExchangeName": "NasdaqGS",
            }
            for idx in range(count)
        ]

    def _us_raw_by_cat(self, *, actives: int = 15, gainers: int = 10, losers: int = 5) -> dict:
        return {
            "most_actives": self._us_candidates(actives, "most_actives"),
            "day_gainers": self._us_candidates(gainers, "day_gainers"),
            "day_losers": self._us_candidates(losers, "day_losers"),
        }

    def test_us_screener_degraded_fresh_result_is_not_cached(self) -> None:
        import kis_api

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"US_SCREEN_MIN_CACHE_CANDIDATES": "30", "US_SCREEN_MIN_CACHE_RATIO": "0.60"},
        ), patch.object(kis_api, "_US_SCREEN_CACHE_PATH", Path(tmp) / "us_screen_cache.json"), patch.object(
            kis_api,
            "_yf_screen_candidates",
            return_value=self._us_raw_by_cat(actives=2, gainers=0, losers=0),
        ):
            rows = kis_api.screen_market_us(top_n=30, mode="NEUTRAL")

            self.assertEqual(len(rows), 2)
            self.assertFalse(kis_api._US_SCREEN_CACHE_PATH.exists())
            self.assertEqual(rows[0]["screener_quality_state"], "DEGRADED_COUNT")
            self.assertEqual(rows[0]["screener_cache_skipped_reason"], "fresh_count_below_min_cache_count")

    def test_us_screener_low_quality_cache_is_not_reused(self) -> None:
        import kis_api

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"US_SCREEN_MIN_CACHE_CANDIDATES": "30", "US_SCREEN_MIN_CACHE_RATIO": "0.60"},
        ), patch.object(kis_api, "_US_SCREEN_CACHE_PATH", Path(tmp) / "us_screen_cache.json"), patch.object(
            kis_api,
            "_yf_screen_candidates",
            return_value=self._us_raw_by_cat(),
        ):
            kis_api._US_SCREEN_CACHE_PATH.write_text(
                json.dumps(
                    {
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "candidates": self._us_candidates(2),
                        "source": "yf",
                        "cached_at": __import__("time").time(),
                        "mode": "NEUTRAL",
                        "preset": {
                            "min_price": 5.0,
                            "max_chg": 25.0,
                            "min_dollar_vol": 15_000_000,
                            "loser_max_chg": 20.0,
                            "quota_actives": 15,
                            "quota_gainers": 10,
                            "quota_losers": 5,
                            "fmp_max": 5,
                            "top_n": 30,
                            "schema": kis_api._US_SCREEN_CACHE_SCHEMA,
                        },
                        "schema": kis_api._US_SCREEN_CACHE_SCHEMA,
                        "quality": {"fresh_count": 2, "min_cache_count": 30},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            rows = kis_api.screen_market_us(top_n=30, mode="NEUTRAL")

            self.assertEqual(len(rows), 30)
            self.assertFalse(rows[0]["screener_cache_used"])
            self.assertTrue(rows[0]["screener_cache_saved"])

    def test_us_screener_sufficient_fresh_result_is_cached_and_reused(self) -> None:
        import kis_api

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"US_SCREEN_MIN_CACHE_CANDIDATES": "30", "US_SCREEN_MIN_CACHE_RATIO": "0.60"},
        ), patch.object(kis_api, "_US_SCREEN_CACHE_PATH", Path(tmp) / "us_screen_cache.json"):
            with patch.object(kis_api, "_yf_screen_candidates", return_value=self._us_raw_by_cat()):
                first = kis_api.screen_market_us(top_n=30, mode="NEUTRAL")
                self.assertTrue(kis_api._US_SCREEN_CACHE_PATH.exists())
                self.assertTrue(first[0]["screener_cache_saved"])
            with patch.object(
                kis_api,
                "_yf_screen_candidates",
                side_effect=AssertionError("fresh fetch should not be called when cache is reusable"),
            ):
                second = kis_api.screen_market_us(top_n=30, mode="NEUTRAL")

        self.assertEqual(len(second), 30)
        self.assertTrue(second[0]["screener_cache_used"])


if __name__ == "__main__":
    unittest.main()
