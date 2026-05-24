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

    def _kr_candidates(self, start: int, count: int, market_type: str) -> list[dict]:
        return [
            {
                "ticker": f"{start + idx:06d}",
                "name": f"KR Test {idx}",
                "price": 5_000 + idx * 100,
                "change_rate": 5.0 + idx,
                "volume": 500_000 + idx * 10_000,
                "vol_ratio": 2.0 + idx / 10.0,
                "market_type": market_type,
            }
            for idx in range(count)
        ]

    def test_kr_screener_writes_price_collection_priority(self) -> None:
        import kis_api

        kospi = self._kr_candidates(1000, 6, "KOSPI")
        kosdaq = self._kr_candidates(2000, 6, "KOSDAQ")

        def fake_rank(_token: str, *, input_iscd: str, **_kwargs):
            return kosdaq if input_iscd == "1001" else kospi

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"KR_SCREEN_RESERVE_LIMIT": "10", "PRICE_COLLECTION_PRIORITY_WRITE": "true"},
        ), patch.object(kis_api, "_US_SCREEN_CACHE_PATH", Path(tmp) / "us_screen_cache.json"), patch.object(
            kis_api, "_KR_SCREEN_CACHE_PATH", Path(tmp) / "kr_screen_cache.json"
        ), patch.object(
            kis_api, "_is_kr_premarket_window", return_value=False
        ), patch.object(
            kis_api, "_kis_volume_rank", side_effect=fake_rank
        ), patch.object(
            kis_api, "_save_kr_screen_audit"
        ), patch(
            "bot.candidate_policy.filter_tradable_candidates", side_effect=lambda cands, _market: (cands, [])
        ):
            rows = kis_api.screen_market_kr("token", top_n=10, mode="NEUTRAL")

            priority_files = list(Path(tmp).glob("price_collection_priority_KR_*.json"))
            self.assertEqual(len(priority_files), 1)
            priority = json.loads(priority_files[0].read_text(encoding="utf-8"))
            priority_tickers = {item["ticker"] for item in priority["items"]}
            self.assertEqual(priority["market"], "KR")
            self.assertEqual(priority["source"], "screen")
            self.assertEqual(priority["mode"], "NEUTRAL")
            self.assertEqual(len(priority["items"]), len(rows))
            self.assertTrue({row["ticker"] for row in rows}.issubset(priority_tickers))

    def test_kr_screen_score_uses_signed_change_for_momentum_rank(self) -> None:
        import kis_api

        up = {"price": 10_000, "volume": 100_000, "change_rate": 8.0, "vol_ratio": 2.0}
        down = {"price": 10_000, "volume": 100_000, "change_rate": -8.0, "vol_ratio": 2.0}

        self.assertGreater(kis_api._kr_screen_score(up), kis_api._kr_screen_score(down))
        self.assertEqual(kis_api._kr_screen_move_metadata(up)["screen_move_bucket"], "momentum_up")
        self.assertEqual(kis_api._kr_screen_move_metadata(down)["screen_move_bucket"], "reversal_watch")

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
            priority_files = list(Path(tmp).glob("price_collection_priority_US_*.json"))
            self.assertEqual(len(priority_files), 1)
            priority = json.loads(priority_files[0].read_text(encoding="utf-8"))
            self.assertEqual(priority["items"][0]["ticker"], rows[0]["ticker"])

    def test_us_dynamic_losers_quota_is_env_gated_and_override_wins(self) -> None:
        import kis_api

        with patch.dict(os.environ, {"US_DYNAMIC_LOSERS_QUOTA_ENABLED": "true"}, clear=False):
            aggressive = kis_api.get_screening_preset("US", "AGGRESSIVE")
            defensive = kis_api.get_screening_preset("US", "DEFENSIVE")
        self.assertLessEqual(aggressive["quota_losers"], 2)
        self.assertGreaterEqual(defensive["quota_losers"], 5)

        with patch.dict(
            os.environ,
            {"US_DYNAMIC_LOSERS_QUOTA_ENABLED": "true", "US_QUOTA_LOSERS": "9"},
            clear=False,
        ):
            overridden = kis_api.get_screening_preset("US", "AGGRESSIVE")
        self.assertEqual(overridden["quota_losers"], 9)

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

    def test_us_screener_degraded_state_is_not_cache_eligible_even_with_count(self) -> None:
        import kis_api

        self.assertFalse(
            kis_api._us_screen_cache_eligible(
                {
                    "screener_quality_state": "DEGRADED_CATEGORY",
                    "screener_degraded": True,
                    "fresh_count": 40,
                    "min_cache_count": 30,
                }
            )
        )
        self.assertTrue(
            kis_api._us_screen_cache_eligible(
                {
                    "screener_quality_state": "OK",
                    "screener_degraded": False,
                    "fresh_count": 40,
                    "min_cache_count": 30,
                }
            )
        )

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

    def test_us_projected_dollar_volume_shadow_does_not_change_filter(self) -> None:
        import kis_api

        candidate = {
            "ticker": "ABCD",
            "name": "Projected Volume",
            "price": 10.0,
            "change_rate": 1.0,
            "volume": 100_000,
            "vol_ratio": 1.0,
            "category": "most_actives",
        }

        with patch.dict(
            os.environ,
            {
                "US_SCREEN_PROJECTED_DOLLAR_VOL_SHADOW_ENABLED": "true",
                "US_SCREEN_PROJECTED_DOLLAR_VOL_FRACTION_FLOOR": "0.05",
            },
            clear=False,
        ), patch.object(kis_api, "_us_screen_market_elapsed_min", return_value=10.0):
            filtered, stats = kis_api._us_post_filter_with_stats(
                [candidate],
                "most_actives",
                5.0,
                25.0,
                15_000_000,
                20.0,
            )

        self.assertEqual(filtered, [])
        self.assertEqual(stats["dollar_volume_reject_count"], 1)
        self.assertEqual(stats["projected_dollar_volume_shadow_count"], 1)
        self.assertEqual(stats["projected_dollar_volume_would_pass_count"], 1)
        shadow = stats["projected_dollar_volume_shadow"][0]
        self.assertEqual(shadow["current_filter_rejected_reason"], "dollar_volume_below_min")
        self.assertEqual(shadow["current_dollar_vol"], 1_000_000.0)
        self.assertEqual(shadow["projected_dollar_vol"], 20_000_000.0)
        self.assertTrue(shadow["would_pass_projected_dollar_vol"])

    def test_us_kis_ranking_shadow_records_overlap_without_changing_yahoo_return(self) -> None:
        import kis_api

        captured_payloads: list[dict] = []

        def fake_kis_get(_token: str, *, path: str, tr_id: str, params: dict, label: str) -> list[dict]:
            if "trade-vol" in path:
                return [{"SYMB": "AAA", "LAST": "12.5", "PRDY_CTRT": "1.2", "ACML_VOL": "2000000"}]
            if str(params.get("GUBN")) == "1":
                return [{"SYMB": "GGG", "LAST": "15.0", "PRDY_CTRT": "5.0", "ACML_VOL": "3000000"}]
            return [{"SYMB": "LLL", "LAST": "9.0", "PRDY_CTRT": "-4.0", "ACML_VOL": "2500000"}]

        def fake_write(payload: dict) -> str:
            captured_payloads.append(payload)
            return "shadow.jsonl"

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "US_KIS_RANKING_SHADOW_ENABLED": "true",
                "US_KIS_RANKING_SHADOW_EXCHANGES": "NAS",
                "US_SCREEN_MIN_CACHE_CANDIDATES": "1",
                "US_SCREEN_MIN_CACHE_RATIO": "0.0",
            },
            clear=False,
        ), patch.object(kis_api, "_US_SCREEN_CACHE_PATH", Path(tmp) / "us_screen_cache.json"), patch.object(
            kis_api, "_yf_screen_candidates", return_value=self._us_raw_by_cat(actives=1, gainers=0, losers=0)
        ), patch.object(
            kis_api, "_kis_us_ranking_get", side_effect=fake_kis_get
        ), patch.object(
            kis_api, "_write_us_kis_ranking_shadow", side_effect=fake_write
        ):
            rows = kis_api.screen_market_us(top_n=1, mode="NEUTRAL", token="token")

        self.assertEqual([row["ticker"] for row in rows], ["AAA"])
        self.assertEqual(len(captured_payloads), 1)
        self.assertFalse(captured_payloads[0]["selection_behavior_changed"])
        self.assertEqual(captured_payloads[0]["categories"]["most_actives"]["overlap_count"], 1)
        self.assertEqual(rows[0]["us_kis_ranking_shadow_path"], "shadow.jsonl")
        self.assertEqual(rows[0]["us_kis_ranking_shadow_overlap_by_category"]["most_actives"], 1)


if __name__ == "__main__":
    unittest.main()
