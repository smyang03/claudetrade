from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import trading_bot
from minority_report import analysts
from bot.session_date import KST
from preopen.storage import (
    load_preopen_pin_candidates,
    load_preopen_state,
    save_preopen_state,
)
from universe_manager import UniverseConfig, build_universe_from_candidates


def _runtime_path(root: Path):
    def _inner(*parts, make_parents=True):
        path = root.joinpath(*parts)
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return _inner


class PreopenPinUniverseTests(unittest.TestCase):
    def test_pin_loader_uses_explicit_age_and_strips_lookahead_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            captured_at = (datetime.now(KST) - timedelta(minutes=90)).isoformat(timespec="seconds")
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                save_preopen_state(
                    "US",
                    {
                        "market": "US",
                        "session_date": "2026-05-05",
                        "captured_at": captured_at,
                        "candidates": [
                            {
                                "ticker": "LEGN",
                                "name": "Legend Biotech",
                                "captured_at": captured_at,
                                "preopen_score": 0.55,
                                "shadow_preopen_rank": 3,
                                "price": 26.5,
                                "volume": 3_882_074,
                                "change_rate": 12.33,
                                "anchor_price": 26.5,
                                "outcome_samples": [{"offset_min": 30}],
                                "post_open_30m_return_pct": 10.36,
                                "actual_ordered": True,
                            }
                        ],
                    },
                    session_date="2026-05-05",
                )

                self.assertEqual(load_preopen_state("US", session_date="2026-05-05", max_age_min=60), {})
                pins = load_preopen_pin_candidates("US", session_date="2026-05-05", max_age_min=120)

        self.assertEqual([row["ticker"] for row in pins], ["LEGN"])
        self.assertTrue(pins[0]["preopen_pinned"])
        self.assertEqual(pins[0]["preopen_anchor_price"], 26.5)
        self.assertNotIn("outcome_samples", pins[0])
        self.assertNotIn("post_open_30m_return_pct", pins[0])
        self.assertNotIn("actual_ordered", pins[0])
        self.assertEqual(pins[0]["preopen_pin_tier"], "HARD")
        self.assertTrue(pins[0]["preopen_pin_require_confirmation"])

    def test_pin_loader_splits_hard_and_soft_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            captured_at = datetime.now(KST).isoformat(timespec="seconds")
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                save_preopen_state(
                    "US",
                    {
                        "market": "US",
                        "session_date": "2026-05-05",
                        "captured_at": captured_at,
                        "provider": "us_screen_market",
                        "data_quality": "us_screen_market",
                        "candidates": [
                            {
                                "ticker": "LEGN",
                                "preopen_score": 0.55,
                                "shadow_preopen_rank": 3,
                                "price": 26.5,
                                "volume": 3_882_074,
                                "anchor_price": 26.5,
                            },
                            {
                                "ticker": "AXTI",
                                "preopen_score": 0.55,
                                "shadow_preopen_rank": 4,
                                "price": 106.0,
                                "volume": 11_409_097,
                            },
                            {
                                "ticker": "AGRO",
                                "preopen_score": 0.55,
                                "shadow_preopen_rank": 5,
                                "price": 15.18,
                                "volume": 2_449_879,
                            },
                        ],
                    },
                    session_date="2026-05-05",
                )
                hard = load_preopen_pin_candidates("US", session_date="2026-05-05", max_age_min=120)
                all_rows = load_preopen_pin_candidates("US", session_date="2026-05-05", max_age_min=120, include_soft=True)

        self.assertEqual([row["ticker"] for row in hard], ["LEGN"])
        tiers = {row["ticker"]: row["preopen_pin_tier"] for row in all_rows}
        self.assertEqual(tiers["LEGN"], "HARD")
        self.assertEqual(tiers["AXTI"], "SOFT")
        self.assertEqual(tiers["AGRO"], "SOFT")
        self.assertIn("rank>3", next(row for row in all_rows if row["ticker"] == "AXTI")["preopen_pin_rejected_reason"])

    def test_seed_only_does_not_become_hard_pin_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            captured_at = datetime.now(KST).isoformat(timespec="seconds")
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                save_preopen_state(
                    "KR",
                    {
                        "market": "KR",
                        "session_date": "2026-05-05",
                        "captured_at": captured_at,
                        "provider": "seed_watchlist",
                        "data_quality": "seed_only",
                        "candidates": [
                            {
                                "ticker": "000660",
                                "preopen_score": 0.55,
                                "shadow_preopen_rank": 1,
                                "price": 200000,
                                "volume": 100000,
                            }
                        ],
                    },
                    session_date="2026-05-05",
                )
                hard = load_preopen_pin_candidates("KR", session_date="2026-05-05", max_age_min=120)
                all_rows = load_preopen_pin_candidates("KR", session_date="2026-05-05", max_age_min=120, include_soft=True)

        self.assertEqual(hard, [])
        self.assertEqual(all_rows[0]["preopen_pin_tier"], "SOFT")
        self.assertIn("seed_only", all_rows[0]["preopen_pin_rejected_reason"])

    def test_news_edge_candidate_bypasses_rank_score_pin_cutoff_with_safety_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            captured_at = datetime.now(KST).isoformat(timespec="seconds")
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                save_preopen_state(
                    "US",
                    {
                        "market": "US",
                        "session_date": "2026-05-05",
                        "captured_at": captured_at,
                        "provider": "us_screen_market",
                        "data_quality": "us_screen_market",
                        "candidates": [
                            {
                                "ticker": "NEWS",
                                "preopen_score": 0.10,
                                "shadow_preopen_rank": 40,
                                "price": 10.0,
                                "volume": 10_000_000,
                                "preopen_news_edge": True,
                                "preopen_news_policy": "strict_loss_filter_v1",
                                "preopen_news_edge_reason": "news_strict_catalyst",
                                "preopen_pinned": True,
                                "preopen_pin_tier": "HARD",
                                "preopen_pin_source": "news_strict_catalyst",
                                "news_prompt_eligible": True,
                                "news_signal_type": "direct_catalyst",
                                "news_prompt_summary": "direct_catalyst:contract",
                            },
                            {
                                "ticker": "RISK",
                                "preopen_score": 0.10,
                                "shadow_preopen_rank": 41,
                                "price": 10.0,
                                "volume": 10_000_000,
                                "preopen_news_edge": True,
                                "preopen_news_policy": "strict_loss_filter_v1",
                                "preopen_news_edge_reason": "news_strict_catalyst",
                                "preopen_pin_source": "news_strict_catalyst",
                                "news_prompt_eligible": True,
                                "news_signal_type": "direct_catalyst",
                                "risk_news_summary": "regulatory uncertainty",
                            },
                        ],
                    },
                    session_date="2026-05-05",
                )
                pins = load_preopen_pin_candidates("US", session_date="2026-05-05", max_age_min=120)

        self.assertEqual([row["ticker"] for row in pins], ["NEWS"])
        self.assertTrue(pins[0]["preopen_pinned"])
        self.assertEqual(pins[0]["preopen_pin_tier"], "HARD")
        self.assertTrue(pins[0]["preopen_pin_require_confirmation"])
        self.assertEqual(pins[0]["preopen_pin_source"], "news_strict_catalyst")
        self.assertIn("news_strict_catalyst", pins[0]["preopen_pin_reason"])
        self.assertTrue(pins[0]["preopen_news_edge"])
        self.assertEqual(pins[0]["preopen_news_policy"], "strict_loss_filter_v1")

    def test_universe_places_pins_after_core_and_keeps_top_n(self) -> None:
        snapshot = build_universe_from_candidates(
            market="US",
            target_date="2026-05-05",
            candidates=[
                {"ticker": "CORE", "price": 100, "volume": 1_000, "change_rate": 0},
                {"ticker": "AAA", "price": 10, "volume": 5_000_000, "change_rate": 8},
                {"ticker": "BBB", "price": 10, "volume": 4_000_000, "change_rate": 7},
                {"ticker": "CCC", "price": 10, "volume": 3_000_000, "change_rate": 6},
            ],
            pinned_candidates=[
                {
                    "ticker": "PIN",
                    "price": 20,
                    "volume": 100_000,
                    "change_rate": 12,
                    "preopen_pinned": True,
                    "preopen_pin_reason": "rank<=5",
                }
            ],
            core_tickers=["CORE"],
            config=UniverseConfig(top_n=3),
        )

        self.assertEqual(snapshot["tickers"][0], "CORE")
        self.assertEqual(snapshot["tickers"][1], "PIN")
        self.assertEqual(snapshot["pin_count"], 1)
        self.assertEqual(snapshot["pinned_tickers"], ["PIN"])
        self.assertEqual(snapshot["count"], 3)
        self.assertEqual(len(snapshot["tickers"]), 3)

    def test_universe_ignores_soft_pin_candidates(self) -> None:
        snapshot = build_universe_from_candidates(
            market="US",
            target_date="2026-05-05",
            candidates=[
                {"ticker": "CORE", "price": 100, "volume": 1_000, "change_rate": 0},
                {"ticker": "AAA", "price": 10, "volume": 5_000_000, "change_rate": 8},
                {"ticker": "BBB", "price": 10, "volume": 4_000_000, "change_rate": 7},
            ],
            pinned_candidates=[
                {
                    "ticker": "SOFT",
                    "price": 20,
                    "volume": 100_000,
                    "change_rate": 12,
                    "preopen_pinned": False,
                    "preopen_pin_tier": "SOFT",
                }
            ],
            core_tickers=["CORE"],
            config=UniverseConfig(top_n=3),
        )

        self.assertNotIn("SOFT", snapshot["tickers"])
        self.assertEqual(snapshot["pin_count"], 0)

    def test_merge_preopen_pin_candidates_preserves_fresh_price(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)

        merged = trading_bot.TradingBot._merge_preopen_pin_candidates(
            bot,
            "US",
            [{"ticker": "LEGN", "price": 29.0, "volume": 1_000_000}],
            "unit_test",
            pin_candidates=[
                {
                    "ticker": "LEGN",
                    "price": 26.5,
                    "preopen_pinned": True,
                    "preopen_anchor_price": 26.5,
                    "preopen_pin_reason": "rank<=5",
                },
                {
                    "ticker": "XYZ",
                    "price": 10.0,
                    "preopen_pinned": True,
                    "preopen_anchor_price": 10.0,
                    "preopen_pin_reason": "score>=0.50",
                },
            ],
        )

        by_ticker = {row["ticker"]: row for row in merged}
        self.assertEqual(by_ticker["LEGN"]["price"], 29.0)
        self.assertEqual(by_ticker["LEGN"]["preopen_anchor_price"], 26.5)
        self.assertTrue(by_ticker["LEGN"]["preopen_pinned"])
        self.assertIn("XYZ", by_ticker)

    def test_merge_preopen_pin_candidates_accepts_empty_fresh_list(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)

        merged = trading_bot.TradingBot._merge_preopen_pin_candidates(
            bot,
            "US",
            [],
            "unit_test",
            pin_candidates=[
                {
                    "ticker": "LEGN",
                    "price": 26.5,
                    "preopen_pinned": True,
                    "preopen_anchor_price": 26.5,
                    "preopen_pin_reason": "rank<=5",
                }
            ],
        )

        self.assertEqual([row["ticker"] for row in merged], ["LEGN"])
        self.assertTrue(merged[0]["preopen_pinned"])

    def test_merge_preopen_pin_candidates_ignores_soft_rows(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)

        merged = trading_bot.TradingBot._merge_preopen_pin_candidates(
            bot,
            "US",
            [],
            "unit_test",
            pin_candidates=[
                {
                    "ticker": "AGRO",
                    "price": 15.18,
                    "preopen_pinned": False,
                    "preopen_pin_tier": "SOFT",
                    "preopen_pin_rejected_reason": "rank>3",
                }
            ],
        )

        self.assertEqual(merged, [])

    def test_selection_candidate_formatter_includes_preopen_confirmation_hint(self) -> None:
        hint = analysts._candidate_preopen_pin_hint(
            {
                "ticker": "LEGN",
                "preopen_pinned": True,
                "preopen_pin_tier": "HARD",
                "preopen_pin_require_confirmation": True,
                "preopen_anchor_price": 26.5,
                "preopen_score": 0.55,
                "shadow_preopen_rank": 3,
                "preopen_pin_turnover": 102_874_961,
                "preopen_pin_reason": "rank<=3,score>=0.50",
            }
        )

        self.assertIn("preopen_pin=HARD", hint)
        self.assertIn("rank=3", hint)
        self.assertIn("score=0.55", hint)
        self.assertIn("anchor=26.5", hint)
        self.assertIn("confirm=required_before_trade_ready", hint)

    def test_dynamic_universe_top_n_uses_global_fallback(self) -> None:
        old_us = os.environ.pop("US_DYNAMIC_UNIVERSE_TOP_N", None)
        try:
            with patch.dict(os.environ, {"DYNAMIC_UNIVERSE_TOP_N": "30"}, clear=False):
                self.assertEqual(trading_bot._market_dynamic_universe_top_n_env("US"), 30)
            with patch.dict(os.environ, {"DYNAMIC_UNIVERSE_TOP_N": "30", "US_DYNAMIC_UNIVERSE_TOP_N": "25"}, clear=False):
                self.assertEqual(trading_bot._market_dynamic_universe_top_n_env("US"), 25)
        finally:
            if old_us is not None:
                os.environ["US_DYNAMIC_UNIVERSE_TOP_N"] = old_us


if __name__ == "__main__":
    unittest.main()
