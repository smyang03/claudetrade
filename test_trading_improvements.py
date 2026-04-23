import json
import sqlite3
import tempfile
import unittest
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

import ticker_selection_db as tsdb
import kis_api as kis_api_module
from phase1_trainer import digest_builder as digest_builder_module
from bot import candidate_policy
from bot.log_sanitizer import mask_secrets
from dashboard import dashboard_server as dashboard_server_module
from minority_report import consensus as consensus_module
from minority_report import tuner as tuner_module
import risk_manager
import trading_bot as trading_bot_module
import universe_manager as universe_manager_module
from risk_manager import RiskManager
from strategy import adaptive_params, continuation, gap_pullback, param_tuner


def _make_signal_df(last_row):
    rows = []
    for _ in range(5):
        rows.append(
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 100.0,
                "vol_avg20": 100.0,
                "gap_pct": 0.0,
            }
        )
    rows.append(last_row)
    return pd.DataFrame(rows)


def _make_ohlcv_df(n_rows: int, start: str = "2025-01-01") -> pd.DataFrame:
    dates = pd.date_range(start=start, periods=n_rows, freq="D")
    return pd.DataFrame(
        {
            "date": dates,
            "open": [100.0 + i for i in range(n_rows)],
            "high": [101.0 + i for i in range(n_rows)],
            "low": [99.0 + i for i in range(n_rows)],
            "close": [100.5 + i for i in range(n_rows)],
            "volume": [1000 + i for i in range(n_rows)],
        }
    )


class CandidatePolicyTests(unittest.TestCase):
    def test_filter_tradable_candidates_blocks_kr_derivative_products(self):
        candidates = [
            {"ticker": "005930", "name": "삼성전자"},
            {"ticker": "999999", "name": "KODEX 인버스"},
            {"ticker": "114800", "name": "KODEX Inverse"},
        ]

        filtered, removed = candidate_policy.filter_tradable_candidates(candidates, "KR")

        self.assertEqual([item["ticker"] for item in filtered], ["005930"])
        removed_by_ticker = {item["ticker"]: item["blocked_reason"] for item in removed}
        self.assertEqual(removed_by_ticker["999999"], "kr_derivative_etf")
        self.assertEqual(removed_by_ticker["114800"], "kr_untradable_product")

    def test_normalize_selection_result_keeps_explicit_empty_trade_ready(self):
        candidates = [
            {"ticker": "005930", "name": "삼성전자"},
            {"ticker": "000660", "name": "SK하이닉스"},
            {"ticker": "035420", "name": "NAVER"},
        ]
        parsed = {
            "watchlist": ["035420", "999999", "005930", "035420"],
            "trade_ready": [],
            "reasons": {"035420": "관심"},
            "veto": {"005930": "과열"},
            "risk_tags": {"005930": ["변동성"]},
            "recommended_strategy": {"035420": "momentum"},
            "max_position_pct": {"035420": 15},
        }

        normalized = candidate_policy.normalize_selection_result(parsed, candidates, "KR")

        self.assertEqual(normalized["watchlist"], ["035420", "005930"])
        self.assertEqual(normalized["trade_ready"], [])
        self.assertEqual(normalized["reasons"]["035420"], "관심")
        self.assertEqual(normalized["veto"]["005930"], "과열")
        self.assertEqual(normalized["risk_tags"]["005930"], ["변동성"])
        self.assertEqual(normalized["recommended_strategy"]["035420"], "momentum")
        self.assertEqual(normalized["max_position_pct"]["035420"], 15)

    def test_filter_tradable_candidates_blocks_us_structured_products(self):
        candidates = [
            {"ticker": "AAPL", "name": "Apple Inc."},
            {"ticker": "SOXL", "name": "Direxion Daily Semiconductor Bull 3X Shares"},
            {"ticker": "SPY", "name": "SPDR S&P 500 ETF Trust"},
        ]

        filtered, removed = candidate_policy.filter_tradable_candidates(candidates, "US")

        self.assertEqual([item["ticker"] for item in filtered], ["AAPL"])
        removed_by_ticker = {item["ticker"]: item["blocked_reason"] for item in removed}
        self.assertEqual(removed_by_ticker["SOXL"], "us_untradable_product")
        self.assertEqual(removed_by_ticker["SPY"], "us_structured_product")

    def test_normalize_selection_result_recovery_clears_trade_ready(self):
        candidates = [
            {"ticker": "NVDA", "name": "NVIDIA"},
            {"ticker": "AAPL", "name": "Apple"},
            {"ticker": "MSFT", "name": "Microsoft"},
        ]
        parsed = {
            "tickers": ["NVDA", "AAPL", "NVDA"],
            "reasons": {"NVDA": "복구 watch"},
            "_parse_recovered": True,
            "_fallback_mode": "ticker_regex",
        }

        normalized = candidate_policy.normalize_selection_result(parsed, candidates, "US")

        self.assertEqual(normalized["watchlist"], ["NVDA", "AAPL"])
        self.assertEqual(normalized["trade_ready"], [])
        self.assertEqual(normalized["reasons"]["NVDA"], "복구 watch")

    def test_normalize_selection_result_partial_recovery_keeps_watchlist_only(self):
        candidates = [
            {"ticker": "NVDA", "name": "NVIDIA"},
            {"ticker": "AAPL", "name": "Apple"},
            {"ticker": "MSFT", "name": "Microsoft"},
        ]
        parsed = {
            "watchlist": ["NVDA", "AAPL"],
            "trade_ready": ["NVDA"],
            "reasons": {"NVDA": "강함"},
            "_parse_recovered": True,
            "_fallback_mode": "selection_partial",
        }

        normalized = candidate_policy.normalize_selection_result(parsed, candidates, "US")

        self.assertEqual(normalized["watchlist"], ["NVDA", "AAPL"])
        self.assertEqual(normalized["trade_ready"], [])
        self.assertEqual(normalized["reasons"]["NVDA"], "강함")
        self.assertTrue(normalized["_parse_recovered"])
        self.assertEqual(normalized["_fallback_mode"], "selection_partial")


class LogSanitizerTests(unittest.TestCase):
    def test_mask_secrets_masks_token_in_path_and_query(self):
        text = (
            "GET https://api.telegram.org/bot12345:ABC_def/sendMessage"
            "?token=raw-token&crtfc_key=secret-key"
            " token=raw-token and 12345:ABC_def"
        )

        with patch.dict("os.environ", {"TELEGRAM_TOKEN": "12345:ABC_def"}):
            masked = mask_secrets(text)

        self.assertNotIn("12345:ABC_def", masked)
        self.assertNotIn("raw-token", masked)
        self.assertNotIn("secret-key", masked)
        self.assertIn("/bot***TELEGRAM_TOKEN***", masked)
        self.assertIn("token=***", masked)
        self.assertIn("crtfc_key=***", masked)


class ScreenerPolicyTests(unittest.TestCase):
    def test_kr_market_bucket_merge_preserves_kosdaq_quota(self):
        kospi = [
            {"ticker": f"P{i}", "price": 1000 + i, "volume": 1_000_000 + i, "change_rate": 12 - i, "vol_ratio": 8 - i * 0.1, "market_type": "KOSPI"}
            for i in range(8)
        ]
        kosdaq = [
            {"ticker": f"Q{i}", "price": 500 + i, "volume": 100_000 + i, "change_rate": 2 + i, "vol_ratio": 1.1, "market_type": "KOSDAQ"}
            for i in range(5)
        ]

        with patch.dict("os.environ", {"KR_SCREEN_KOSDAQ_MIN_RATIO": "0.5"}):
            merged = kis_api_module._merge_kr_market_buckets(kospi, kosdaq, top_n=6)

        self.assertEqual(len(merged), 6)
        self.assertGreaterEqual(
            len([row for row in merged if row.get("market_type") == "KOSDAQ"]),
            3,
        )

    def test_selection_candidate_cap_starts_kr_at_28_without_changing_us_default(self):
        try:
            from minority_report import analysts as analysts_module
        except Exception as exc:
            self.skipTest(f"analysts import unavailable: {exc}")

        with patch.dict("os.environ", {"KR_SELECTION_PROMPT_CAP": "28", "US_SELECTION_PROMPT_CAP": "24"}, clear=False):
            self.assertEqual(analysts_module._selection_candidate_cap("KR", 20, 10), 28)
            self.assertEqual(analysts_module._selection_candidate_cap("US", 30, 12), 24)


class DigestBuilderPeadTests(unittest.TestCase):
    def test_classify_earnings_window_handles_pre_and_post_ranges(self):
        self.assertEqual(
            digest_builder_module._classify_earnings_window("2026-04-24", "2026-04-27"),
            "pre",
        )
        self.assertEqual(
            digest_builder_module._classify_earnings_window("2026-04-24", "2026-04-23"),
            "post_1",
        )
        self.assertEqual(
            digest_builder_module._classify_earnings_window("2026-04-24", "2026-04-21"),
            "post_3",
        )

    def test_build_earnings_event_payload_derives_surprise_and_bias(self):
        with patch.object(digest_builder_module, "_ticker_symbol_candidates", return_value=["AAPL"]), \
             patch.object(
                 digest_builder_module,
                 "_load_yf_earnings_events",
                 return_value=[{"event_date": "2026-04-23", "reported_eps": 1.10, "eps_estimate": 1.00}],
             ), \
             patch.object(digest_builder_module, "_yf_earnings_date", return_value="2026-07-23"):
            payload = digest_builder_module._build_earnings_event_payload(
                "US",
                "AAPL",
                "2026-04-24",
                include_surprise=True,
            )

        self.assertEqual(payload["earnings_date"], "2026-04-23")
        self.assertEqual(payload["earnings_window"], "post_1")
        self.assertEqual(payload["surprise_sign"], "beat")
        self.assertEqual(payload["surprise_strength"], "medium")
        self.assertEqual(payload["confidence_tier"], "high")
        self.assertEqual(payload["pead_bias"], "positive_drift")

    def test_digest_to_prompt_shows_window_without_shadow_surprise(self):
        digest = {
            "market": "US",
            "date": "2026-04-24",
            "context": {},
            "technicals": {
                "AAPL": {
                    "name": "Apple",
                    "close": 190.0,
                    "change_pct": 1.2,
                    "rsi": 58.0,
                    "macd": "골든크로스",
                    "bb_pct": 64.0,
                    "vol_ratio": 1.5,
                    "pos_52w": 82.0,
                    "atr_pct": 4.1,
                    "trend_5d": 0.8,
                    "premarket_pct": 0.6,
                    "earnings_date": "2026-04-23",
                    "earnings_window": "post_1",
                    "surprise_sign": "beat",
                    "surprise_strength": "medium",
                    "prompt_applied": False,
                }
            },
            "top_news": [],
            "prev_result": {},
        }

        prompt = digest_builder_module.digest_to_prompt(digest)

        self.assertIn("실적 2026-04-23", prompt)
        self.assertIn("실적창 post_1", prompt)
        self.assertNotIn("surprise beat/medium", prompt)


class TradingBotPeadEnrichmentTests(unittest.TestCase):
    def test_annotate_selection_execution_features_inherits_digest_earnings_fields(self):
        bot = trading_bot_module.TradingBot.__new__(trading_bot_module.TradingBot)
        bot.today_judgment = {
            "digest_raw": {
                "context": {},
                "technicals": {
                    "NVDA": {
                        "earnings_date": "2026-04-23",
                        "earnings_window": "post_1",
                        "confidence_tier": "medium",
                        "pead_bias": "positive_drift",
                        "prompt_applied": False,
                        "surprise_sign": "beat",
                        "surprise_strength": "medium",
                    }
                },
            },
            "consensus": {"confidence": 0.6},
        }
        bot._ca_context_last = None
        bot._selection_active_strategies = lambda market, mode="": []
        bot._selection_session_phase = lambda market: {
            "phase": "mid",
            "minutes_to_close": 120.0,
            "entry_blackout": False,
            "elapsed_min": 90.0,
        }
        bot._or_formed = {}
        bot._get_ohlcv_cached = lambda ticker, market: pd.DataFrame()
        bot.enable_kr_momentum_shrink = True

        rows = bot._annotate_selection_execution_features("US", [{"ticker": "NVDA", "price": 100.0}], "NEUTRAL")

        self.assertEqual(rows[0]["earnings_window"], "post_1")
        self.assertEqual(rows[0]["earnings_date"], "2026-04-23")
        self.assertEqual(rows[0]["pead_bias"], "positive_drift")
        self.assertEqual(rows[0]["surprise_sign"], "beat")


class GapPullbackTests(unittest.TestCase):
    def test_opening_signal_requires_real_pullback_and_recovery(self):
        df = _make_signal_df(
            {
                "open": 100.0,
                "high": 103.0,
                "low": 99.5,
                "close": 100.2,
                "volume": 500.0,
                "vol_avg20": 100.0,
                "gap_pct": 4.0,
            }
        )
        params = gap_pullback.params("NEUTRAL", market="KR")
        params["session_elapsed_min"] = 5

        self.assertTrue(gap_pullback.signal(df, len(df) - 1, params))

    def test_opening_flat_bar_is_rejected(self):
        df = _make_signal_df(
            {
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 500.0,
                "vol_avg20": 100.0,
                "gap_pct": 4.0,
            }
        )
        params = gap_pullback.params("NEUTRAL", market="KR")
        params["session_elapsed_min"] = 5

        self.assertFalse(gap_pullback.signal(df, len(df) - 1, params))


class ContinuationTests(unittest.TestCase):
    def test_cautious_mode_is_disabled(self):
        params = continuation.params("CAUTIOUS", market="KR")
        self.assertTrue(params["disabled"])

    def test_signal_accepts_valid_continuation_setup(self):
        df = _make_signal_df(
            {
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 100.8,
                "volume": 250.0,
                "vol_avg20": 100.0,
                "gap_pct": 5.0,
            }
        )
        params = continuation.params("MILD_BULL", market="KR")
        params["session_elapsed_min"] = 15

        self.assertTrue(continuation.signal(df, len(df) - 1, params))


class RiskManagerTests(unittest.TestCase):
    def test_calc_order_budget_applies_atr_volatility_scaling(self):
        rm = RiskManager(init_cash=1_000_000, max_order_krw=200_000, market="KR")

        budget = rm.calc_order_budget(50, atr_pct=0.075, atr_target_pct=0.015)

        self.assertEqual(budget, 20_000)

    def test_auto_trailing_uses_breakeven_floor_and_usd_stop(self):
        rm = RiskManager(init_cash=1_000_000, max_order_krw=500_000, market="US")
        opened = rm.open_position("AAPL", price=100_000.0, qty=1, strategy="test", tp_pct=0.06, sl_pct=0.03)
        self.assertTrue(opened)

        pos = rm.positions[0]
        pos["display_currency"] = "USD"
        pos["display_avg_price"] = 100.0

        rm.update_prices({"AAPL": 103_000.0}, raw_prices={"AAPL": 103.0})

        self.assertTrue(pos["trailing"])
        self.assertTrue(pos["tp_triggered"])
        self.assertAlmostEqual(
            pos["trail_sl"],
            100_000.0 * (1 + risk_manager.AUTO_BREAKEVEN_BUFFER_PCT),
            places=6,
        )
        self.assertAlmostEqual(
            pos["trail_sl_usd"],
            100.0 * (1 + risk_manager.AUTO_BREAKEVEN_BUFFER_PCT),
            places=6,
        )

        rm.update_prices({"AAPL": 110_000.0}, raw_prices={"AAPL": 110.0})
        self.assertAlmostEqual(pos["trail_sl"], 110_000.0 * (1 - risk_manager.AUTO_TRAIL_PCT), places=6)
        self.assertAlmostEqual(pos["trail_sl_usd"], 110.0 * (1 - risk_manager.AUTO_TRAIL_PCT), places=6)

        rm.update_prices({"AAPL": 105_000.0}, raw_prices={"AAPL": 105.0})
        exits = rm.get_exit_candidates()
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0]["reason"], "trail_stop")

    def test_auto_trailing_uses_native_usd_trigger_not_krw_drift(self):
        rm = RiskManager(init_cash=1_000_000, max_order_krw=500_000, market="US")
        rm.positions.append(
            {
                "ticker": "NVTS",
                "entry": 100_000.0,
                "qty": 1,
                "current_price": 100_000.0,
                "display_avg_price": 100.0,
                "display_current_price": 100.0,
                "display_currency": "USD",
                "tp": 106_000.0,
                "sl": 97_000.0,
                "tp_pct": 0.06,
                "sl_pct": 0.03,
                "max_hold": 1,
                "held_days": 0,
                "trailing": False,
                "trail_sl": 0.0,
                "trail_sl_usd": 0.0,
                "trail_pct": 0.03,
                "tp_triggered": False,
                "hold_advice": None,
                "tp_price": 0.0,
                "management_protected": False,
            }
        )

        rm.update_prices({"NVTS": 108_000.0}, raw_prices={"NVTS": 98.0})

        self.assertFalse(rm.positions[0]["trailing"])

    def test_management_protected_position_skips_strategy_exit_but_keeps_stop_loss(self):
        rm = RiskManager(init_cash=1_000_000, max_order_krw=500_000, market="US")
        rm.positions.append(
            {
                "ticker": "NVTS",
                "entry": 100_000.0,
                "qty": 1,
                "current_price": 100_000.0,
                "display_avg_price": 100.0,
                "display_current_price": 100.0,
                "display_currency": "USD",
                "tp": 106_000.0,
                "sl": 97_000.0,
                "tp_pct": 0.06,
                "sl_pct": 0.03,
                "max_hold": 1,
                "held_days": 5,
                "trailing": False,
                "trail_sl": 0.0,
                "trail_sl_usd": 0.0,
                "trail_pct": 0.03,
                "tp_triggered": False,
                "hold_advice": None,
                "tp_price": 0.0,
                "management_protected": True,
            }
        )

        rm.update_prices({"NVTS": 105_000.0}, raw_prices={"NVTS": 105.0})
        self.assertFalse(rm.positions[0]["trailing"])
        self.assertEqual(rm.get_exit_candidates(), [])

        rm.update_prices({"NVTS": 96_000.0}, raw_prices={"NVTS": 96.0})
        exits = rm.get_exit_candidates()
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0]["reason"], "stop_loss")


class ConsensusGuardTests(unittest.TestCase):
    def test_apply_unanimous_override_blocks_opposite_consensus(self):
        judgments = {
            "bull": {"stance": "CAUTIOUS_BEAR", "confidence": 0.7},
            "bear": {"stance": "CAUTIOUS_BEAR", "confidence": 0.7},
            "neutral": {"stance": "CAUTIOUS_BEAR", "confidence": 0.7},
        }
        consensus = {"mode": "MILD_BULL", "size": 23, "tp_mult": 1.0}

        guarded = consensus_module.apply_unanimous_override(judgments, consensus)

        self.assertEqual(guarded["mode"], "CAUTIOUS_BEAR")
        self.assertEqual(guarded["size"], consensus_module._e("SIZE_CAUTIOUS_BEAR", 20))
        self.assertTrue(guarded["unanimous_override_applied"])
        self.assertEqual(guarded["unanimous_direction"], "bear")

    def test_build_judgment_eval_persists_directional_atomic_fields(self):
        judgments = {
            "bull": {"stance": "MILD_BULL"},
            "bear": {"stance": "MILD_BEAR"},
            "neutral": {"stance": "NEUTRAL"},
        }
        consensus = {"mode": "NEUTRAL"}

        result = consensus_module.build_judgment_eval(judgments, consensus, 1.2)

        self.assertEqual(result["actual_dir"], "UP")
        self.assertEqual(result["consensus_dir"], "FLAT")
        self.assertFalse(result["consensus_hit"])
        self.assertTrue(result["analyst_hits"]["bull"])
        self.assertFalse(result["analyst_hits"]["bear"])
        self.assertTrue(result["best_analyst_outperformed_consensus"])
        self.assertIsNone(result["unanimous_direction"])
        self.assertFalse(result["unanimous_consensus_mismatch"])


class TradingBotGateTests(unittest.TestCase):
    def _make_bot(self):
        bot = trading_bot_module.TradingBot.__new__(trading_bot_module.TradingBot)
        bot.is_paper = True
        bot.trade_ready_tickers = {"KR": [], "US": []}
        bot.selection_meta = {"KR": {}, "US": {}}
        bot.selection_stages = {"KR": {}, "US": {}}
        bot.today_ticker_reasons = {"KR": {}, "US": {}}
        bot.today_judgment = {}
        bot._active_session_date = {"KR": None, "US": None}
        bot.entry_priority_cutoff_enabled = True
        bot.entry_priority_cutoff = 0.20
        bot.enable_continuation_live = False
        bot.enable_soft_watch_promotion = False
        bot.enable_kr_momentum_shrink = True
        bot._claude_runtime_overrides = {"KR": {}, "US": {}}
        bot._ticker_runtime_blocked_reasons = {"KR": {}, "US": {}}
        bot._ticker_runtime_rejection_reasons = {"KR": {}, "US": {}}
        bot._ticker_no_signal_cycles = {}
        bot._invalid_price_count = {}
        bot.positions = {"KR": {}, "US": {}}
        bot._or_formed = {}
        bot._or_high = {}
        bot._or_low = {}
        bot.risk = SimpleNamespace(positions=[])
        bot.token = "test-token"
        bot._persist_live_judgment = lambda market: None
        return bot

    def test_trade_ready_gate_requires_membership_for_kr(self):
        bot = self._make_bot()
        bot.trade_ready_tickers["KR"] = ["005930", "000660"]

        self.assertTrue(bot._is_trade_ready_ticker("KR", "005930"))
        self.assertFalse(bot._is_trade_ready_ticker("KR", "035420"))

    def test_trade_ready_gate_normalizes_us_tickers(self):
        bot = self._make_bot()
        bot.trade_ready_tickers["US"] = ["aapl", "NvDa"]

        self.assertEqual(bot._trade_ready_set("US"), {"AAPL", "NVDA"})
        self.assertTrue(bot._is_trade_ready_ticker("US", "AAPL"))
        self.assertTrue(bot._is_trade_ready_ticker("US", "nvda"))
        self.assertFalse(bot._is_trade_ready_ticker("US", "MSFT"))

    def test_entry_priority_cutoff_blocks_only_below_cutoff(self):
        bot = self._make_bot()

        self.assertTrue(bot._is_entry_priority_blocked(0.19))
        self.assertFalse(bot._is_entry_priority_blocked(0.20))
        self.assertFalse(bot._is_entry_priority_blocked(0.35))

    def test_entry_priority_cutoff_can_be_disabled(self):
        bot = self._make_bot()
        bot.entry_priority_cutoff_enabled = False

        self.assertFalse(bot._is_entry_priority_blocked(0.0))

    def test_entry_priority_cutoff_uses_runtime_override_by_market(self):
        bot = self._make_bot()
        bot._claude_runtime_overrides["KR"] = {"entry_priority_cutoff_adjust": -0.05}

        self.assertAlmostEqual(bot._effective_entry_priority_cutoff("KR"), 0.15)
        self.assertTrue(bot._is_entry_priority_blocked(0.14, "KR"))
        self.assertFalse(bot._is_entry_priority_blocked(0.16, "KR"))

    def test_effective_momentum_wait_window_clamps_runtime_adjustment(self):
        bot = self._make_bot()
        bot._claude_runtime_overrides["US"] = {"momentum_wait_adjust_min": -20}

        self.assertEqual(bot._effective_momentum_wait_window("US", 15), 5.0)
        self.assertEqual(bot._effective_momentum_wait_window("US", 45), 30.0)

    def test_effective_kr_momentum_atr_caps_use_runtime_override(self):
        bot = self._make_bot()
        bot._claude_runtime_overrides["KR"] = {
            "kr_momentum_atr_cap_adjust": 0.015,
            "kr_momentum_atr_cap_high_adjust": 0.02,
        }

        self.assertEqual(bot._effective_kr_momentum_atr_caps(), (0.075, 0.12))

    def test_effective_kr_momentum_atr_stage_uses_stepwise_size_caps(self):
        bot = self._make_bot()

        self.assertEqual(bot._effective_kr_momentum_atr_stage(0.055)["size_cap"], None)
        self.assertEqual(bot._effective_kr_momentum_atr_stage(0.065)["size_cap"], 70)
        self.assertEqual(bot._effective_kr_momentum_atr_stage(0.075)["size_cap"], 50)
        self.assertEqual(bot._effective_kr_momentum_atr_stage(0.085)["size_cap"], 35)
        self.assertTrue(bot._effective_kr_momentum_atr_stage(0.105)["blocked"])

    def test_order_size_too_small_blocks_sub_minimum_kr_order(self):
        bot = self._make_bot()

        blocked, minimum = bot._is_order_size_too_small(
            "KR",
            qty=1,
            order_cost_krw=7_980,
            order_budget_krw=13_500,
        )

        self.assertTrue(blocked)
        self.assertEqual(minimum, 30_000)

    def test_order_size_too_small_uses_usd_minimum_for_us_orders(self):
        bot = self._make_bot()
        bot.usd_krw_rate = 1_500

        blocked, minimum = bot._is_order_size_too_small(
            "US",
            qty=1,
            order_cost_krw=40_000,
            order_budget_krw=40_000,
        )

        self.assertTrue(blocked)
        self.assertEqual(minimum, 45_000)

    def test_risk_off_mr_exception_allows_single_low_volume_setup(self):
        bot = self._make_bot()
        bot.today_judgment = {
            "digest_raw": {
                "context": {
                    "sp500": {"change_pct": -0.8},
                    "nasdaq": {"change_pct": -1.2},
                }
            }
        }

        result = bot._risk_off_mr_exception(
            "US",
            "CAUTIOUS_BEAR",
            "mean_reversion",
            {"vol_ratio": 1.4},
        )

        self.assertTrue(result["allowed"])
        self.assertEqual(result["size_cap"], 40)

    def test_risk_off_mr_exception_blocks_panic_and_position_overlap(self):
        bot = self._make_bot()
        bot.today_judgment = {
            "digest_raw": {
                "context": {
                    "sp500": {"change_pct": -3.1},
                    "nasdaq": {"change_pct": -4.0},
                }
            }
        }
        blocked = bot._risk_off_mr_exception(
            "US",
            "CAUTIOUS_BEAR",
            "mean_reversion",
            {"vol_ratio": 1.1},
        )
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["reason"], "panic_primary")

        bot.today_judgment["digest_raw"]["context"]["sp500"]["change_pct"] = -0.5
        bot.today_judgment["digest_raw"]["context"]["nasdaq"]["change_pct"] = -0.8
        bot.risk = SimpleNamespace(positions=[{"ticker": "AAPL"}])
        blocked = bot._risk_off_mr_exception(
            "US",
            "CAUTIOUS_BEAR",
            "mean_reversion",
            {"vol_ratio": 1.1},
        )
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["reason"], "risk_off_position_limit")

    def test_build_intraday_context_includes_execution_profile_and_ops_review(self):
        bot = self._make_bot()
        bot.today_judgment = {
            "consensus": {"mode": "MILD_BULL"},
            "ops_review_snapshot": {
                "market": "KR",
                "metrics": {
                    "consensus_directional_hit_rate": {"value": 42.0, "sample": 10, "breached": True},
                    "trade_ready_signal_conversion": {"value": 12.0, "sample": 24, "breached": True},
                },
            },
        }

        with patch.object(bot, "_market_elapsed_min", return_value=32.0), \
             patch.object(bot, "_minutes_to_close", return_value=180.0), \
             patch.object(bot, "_in_entry_blackout", return_value=False), \
             patch("kis_api.get_index_change", return_value=0.55):
            text = bot._build_intraday_context("KR")

        self.assertIn("session_phase=early", text)
        self.assertIn("active_strategies=opening_range_pullback,gap_pullback,momentum,mean_reversion", text)
        self.assertIn("gates=wait=", text)
        self.assertIn("ops review: consensus_hit=42.0% n=10 breach", text)
        self.assertIn("tr_ready_to_signal=12.0% n=24 breach", text)

    def test_annotate_selection_execution_features_adds_exec_fields(self):
        bot = self._make_bot()
        bot.today_judgment = {
            "consensus": {"mode": "MILD_BULL", "confidence": 0.7},
            "digest_raw": {"context": {}},
        }

        ohlcv = _make_ohlcv_df(3)
        signal_df = pd.DataFrame([{"close": 100.0, "atr": 7.0}])

        def _fake_entry_priority_score(**kwargs):
            if kwargs.get("strategy_name") == "momentum":
                return 1.6, {}
            return 0.5, {}

        with patch.object(bot, "_market_elapsed_min", return_value=35.0), \
             patch.object(bot, "_minutes_to_close", return_value=205.0), \
             patch.object(bot, "_in_entry_blackout", return_value=False), \
             patch.object(bot, "_get_ohlcv_cached", return_value=ohlcv), \
             patch.object(trading_bot_module, "calc_all", return_value=signal_df), \
             patch.object(trading_bot_module, "_adaptive_params", return_value={}), \
             patch.object(trading_bot_module, "entry_priority_score", side_effect=_fake_entry_priority_score):
            rows = bot._annotate_selection_execution_features(
                "KR",
                [{"ticker": "005930", "price": 100.0}],
                mode="MILD_BULL",
            )

        row = rows[0]
        self.assertEqual(row["or_state"], "missing")
        self.assertEqual(row["entry_priority_bucket"], "high")
        self.assertEqual(row["execution_fit_strategy"], "momentum")
        self.assertEqual(row["minutes_to_close"], 205.0)
        self.assertAlmostEqual(row["atr_pct"], 7.0)
        self.assertTrue(str(row["atr_stage"]).startswith("atr_"))

    def test_annotate_selection_execution_features_biases_risky_kr_momentum_to_watch_only(self):
        bot = self._make_bot()
        bot.today_judgment = {
            "consensus": {"mode": "MILD_BULL", "confidence": 0.7},
            "digest_raw": {"context": {}},
        }

        ohlcv = _make_ohlcv_df(3)
        signal_df = pd.DataFrame([{"close": 100.0, "atr": 8.0}])

        def _fake_entry_priority_score(**kwargs):
            if kwargs.get("strategy_name") == "momentum":
                return 1.7, {}
            return 0.4, {}

        with patch.object(bot, "_market_elapsed_min", return_value=35.0), \
             patch.object(bot, "_minutes_to_close", return_value=205.0), \
             patch.object(bot, "_in_entry_blackout", return_value=False), \
             patch.object(bot, "_get_ohlcv_cached", return_value=ohlcv), \
             patch.object(trading_bot_module, "calc_all", return_value=signal_df), \
             patch.object(trading_bot_module, "_adaptive_params", return_value={}), \
             patch.object(trading_bot_module, "entry_priority_score", side_effect=_fake_entry_priority_score):
            rows = bot._annotate_selection_execution_features(
                "KR",
                [{
                    "ticker": "005930",
                    "price": 100.0,
                    "change_rate": 9.2,
                    "liquidity_bucket": "mid",
                    "from_high_bucket": "near_high",
                    "from_high_pct": -0.8,
                }],
                mode="MILD_BULL",
            )

        row = rows[0]
        self.assertEqual(row["raw_execution_fit_strategy"], "momentum")
        self.assertEqual(row["execution_fit_strategy"], "observe")
        self.assertEqual(row["selection_bias"], "watch_only")
        self.assertEqual(row["entry_priority_bucket"], "low")
        self.assertIn("near_high", row["selection_bias_reason"])

    def test_partial_replace_score_penalizes_repeated_blocked_reasons(self):
        bot = self._make_bot()
        bot._ticker_no_signal_cycles["005930"] = 2
        bot._invalid_price_count["005930"] = 1
        bot._ticker_runtime_blocked_reasons["KR"] = {
            "005930": {"momentum_atr_too_high": 2, "entry_blackout": 1}
        }
        bot._ticker_runtime_rejection_reasons["KR"] = {
            "005930": {"momentum_wait": 1, "pullback_missing": 1}
        }

        score = bot._partial_replace_score("KR", "005930")

        self.assertGreaterEqual(score, 8.0)

    def test_apply_consensus_guards_normalizes_reused_opposite_consensus(self):
        bot = self._make_bot()
        judgments = {
            "bull": {"stance": "CAUTIOUS_BEAR", "confidence": 0.7},
            "bear": {"stance": "CAUTIOUS_BEAR", "confidence": 0.7},
            "neutral": {"stance": "CAUTIOUS_BEAR", "confidence": 0.7},
        }

        guarded = bot._apply_consensus_guards(
            "US",
            judgments,
            {"mode": "MILD_BULL", "size": 23, "tp_mult": 1.0},
            source="test",
        )

        self.assertEqual(guarded["mode"], "CAUTIOUS_BEAR")
        self.assertTrue(guarded["unanimous_override_applied"])

    def test_apply_runtime_tuning_adjustments_updates_judgment_state(self):
        bot = self._make_bot()
        bot.today_judgment = {"market": "KR"}

        changed = bot._apply_runtime_tuning_adjustments(
            "KR",
            {
                "momentum_wait_adjust_min": -12,
                "entry_priority_cutoff_adjust": -0.03,
                "kr_momentum_atr_cap_adjust": 0.01,
                "kr_momentum_atr_cap_high_adjust": 0.02,
            },
        )

        self.assertEqual(changed["momentum_wait_adjust_min"], -12)
        self.assertAlmostEqual(changed["entry_priority_cutoff_adjust"], -0.03)
        self.assertEqual(bot._claude_runtime_overrides["KR"]["momentum_wait_adjust_min"], -12)
        self.assertEqual(
            bot.today_judgment["claude_runtime_overrides"]["KR"]["kr_momentum_atr_cap_high_adjust"],
            0.02,
        )

    def test_restore_runtime_overrides_from_payload_restores_saved_market_values(self):
        bot = self._make_bot()
        bot.today_judgment = {"market": "KR"}

        restored = bot._restore_runtime_overrides_from_payload(
            "KR",
            {
                "claude_runtime_overrides": {
                    "KR": {
                        "momentum_wait_adjust_min": -5,
                        "entry_priority_cutoff_adjust": 0.02,
                        "kr_momentum_atr_cap_adjust": 0.01,
                        "kr_momentum_atr_cap_high_adjust": 0.01,
                    }
                }
            },
        )

        self.assertEqual(restored["momentum_wait_adjust_min"], -5)
        self.assertAlmostEqual(restored["entry_priority_cutoff_adjust"], 0.02)
        self.assertAlmostEqual(bot._claude_runtime_overrides["KR"]["kr_momentum_atr_cap_adjust"], 0.01)
        self.assertEqual(
            bot.today_judgment["claude_runtime_overrides"]["KR"]["kr_momentum_atr_cap_high_adjust"],
            0.01,
        )

    def test_persist_live_judgment_writes_runtime_overrides_from_memory(self):
        bot = self._make_bot()
        bot.is_paper = False
        bot._active_session_date["KR"] = date(2026, 4, 22)
        bot._claude_runtime_overrides["KR"] = {
            "momentum_wait_adjust_min": -5,
            "entry_priority_cutoff_adjust": 0.02,
            "kr_momentum_atr_cap_adjust": 0.01,
            "kr_momentum_atr_cap_high_adjust": 0.0,
        }
        bot.today_judgment = {
            "market": "KR",
            "consensus": {"mode": "MILD_BULL"},
            "judgment_eval": {"consensus_hit": True, "unanimous_consensus_mismatch": False},
            "ops_review_snapshot": {"market": "KR", "metrics": {"consensus_directional_hit_rate": {"value": 55.0}}},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            with patch.object(trading_bot_module, "JUDGMENT_DIR", base_dir):
                bot._persist_live_judgment = trading_bot_module.TradingBot._persist_live_judgment.__get__(
                    bot, trading_bot_module.TradingBot
                )
                bot._persist_live_judgment("KR")

            saved = json.loads((base_dir / "live_20260422_KR.json").read_text(encoding="utf-8"))

        self.assertEqual(saved["claude_runtime_overrides"]["KR"]["momentum_wait_adjust_min"], -5)
        self.assertAlmostEqual(saved["claude_runtime_overrides"]["KR"]["entry_priority_cutoff_adjust"], 0.02)
        self.assertTrue(saved["judgment_eval"]["consensus_hit"])
        self.assertEqual(saved["ops_review_snapshot"]["metrics"]["consensus_directional_hit_rate"]["value"], 55.0)

    def test_runtime_gate_state_text_reports_effective_values(self):
        bot = self._make_bot()
        bot._claude_runtime_overrides["KR"] = {
            "momentum_wait_adjust_min": -5,
            "entry_priority_cutoff_adjust": 0.02,
            "kr_momentum_atr_cap_adjust": 0.01,
            "kr_momentum_atr_cap_high_adjust": 0.0,
        }

        text = bot._runtime_gate_state_text("KR")

        self.assertIn("wait=45->40m", text)
        self.assertIn("cutoff=0.22", text)
        self.assertIn("kr_atr_cap=0.07/0.10", text)
        self.assertIn("adjust(wait=-5m, cutoff=+0.02, atr=+0.01/+0.00)", text)

    def test_update_session_date_diagnostics_records_mismatch(self):
        bot = self._make_bot()
        bot.today_judgment = {}
        bot._active_session_date["US"] = date(2026, 4, 23)
        bot._last_session_date_diag = {"KR": None, "US": None}

        with patch.object(trading_bot_module, "_market_session_date", return_value=date(2026, 4, 24)):
            diag = bot._update_session_date_diagnostics("US", "session_close")

        self.assertTrue(diag["mismatch"])
        self.assertEqual(diag["active_session_date"], "2026-04-23")
        self.assertEqual(diag["calendar_session_date"], "2026-04-24")
        self.assertEqual(bot.today_judgment["session_date_diagnostics"]["US"]["trigger"], "session_close")

    def test_build_portfolio_info_reflects_cash_and_position_limits(self):
        bot = self._make_bot()
        bot.risk = SimpleNamespace(cash=123456.7, max_order_krw=40000.4, positions=[{"ticker": "A"}, {"ticker": "B"}])
        bot._kis_total_equity_krw = lambda: 987654.3

        info = bot._build_portfolio_info()

        self.assertEqual(info["cash"], 123457)
        self.assertEqual(info["total_equity"], 987654)
        self.assertEqual(info["max_order_krw"], 40000)
        self.assertEqual(info["n_positions"], 2)
        self.assertEqual(info["max_positions"], trading_bot_module.HARD_RULES["max_positions"])

    def test_enrich_candidate_with_history_adds_gap_pullback_and_ma60_features(self):
        bot = self._make_bot()
        candles = pd.DataFrame(
            [
                {"close": 100.0, "high": 101.0},
                {"close": 102.0, "high": 105.0},
            ]
        )
        sig_df = pd.DataFrame(
            [
                {"gap_pct": 1.2, "ma60": 99.0},
                {"gap_pct": 2.5, "ma60": 100.0},
            ]
        )

        enriched = bot._enrich_candidate_with_history(
            {"ticker": "005930", "price": 102.0},
            candles,
            sig_df,
        )

        self.assertEqual(enriched["gap_pct"], 2.5)
        self.assertEqual(enriched["from_high_pct"], -2.8571)
        self.assertTrue(enriched["above_ma60"])

    def test_recommended_strategy_reorders_strategy_priority(self):
        bot = self._make_bot()
        bot.selection_meta["KR"] = {
            "recommended_strategy": {"005930": "갭+눌림"},
        }

        ordered = bot._prioritize_strategy_order(
            "KR",
            "005930",
            ["opening_range_pullback", "momentum", "gap_pullback", "mean_reversion"],
        )

        self.assertEqual(
            ordered,
            ["gap_pullback", "opening_range_pullback", "momentum", "mean_reversion"],
        )

    def test_selection_size_cap_combines_max_position_and_risk_tags(self):
        bot = self._make_bot()
        bot.selection_meta["US"] = {
            "max_position_pct": {"aapl": 18},
            "risk_tags": {"AAPL": ["변동성 높음", "뉴스 이벤트"]},
        }

        cap, reasons = bot._selection_size_cap_pct("US", "aapl")

        self.assertEqual(cap, 18)
        self.assertTrue(any("max_position_pct=18%" in reason for reason in reasons))
        self.assertTrue(any("risk_tags=" in reason for reason in reasons))

    def test_risk_tags_only_can_cap_size(self):
        bot = self._make_bot()
        bot.selection_meta["KR"] = {
            "risk_tags": {"005930": ["저유동성 주의"]},
        }

        cap, reasons = bot._selection_size_cap_pct("KR", "005930")

        self.assertEqual(cap, 35)
        self.assertEqual(reasons, ["risk_tags=저유동성 주의"])

    def test_watch_only_reason_text_uses_selection_reason_and_meta(self):
        bot = self._make_bot()
        bot.today_ticker_reasons["US"] = {"AAPL": "RS 약세. watch 유지"}
        bot.selection_meta["US"] = {
            "veto": {"AAPL": "저유동성 제한"},
            "risk_tags": {"AAPL": ["변동성 높음"]},
            "recommended_strategy": {"AAPL": "gap_pullback"},
            "max_position_pct": {"AAPL": 18},
        }

        reason = bot._watch_only_reason_text("US", "aapl")

        self.assertIn("RS 약세. watch 유지", reason)
        self.assertIn("제외사유 저유동성 제한", reason)
        self.assertIn("리스크 변동성 높음", reason)
        self.assertIn("권장전략 gap_pullback", reason)
        self.assertIn("비중상한 18%", reason)

    def test_watch_only_reason_text_falls_back_when_trade_ready_empty(self):
        bot = self._make_bot()

        reason = bot._watch_only_reason_text("US", "NVDA")

        self.assertEqual(reason, "selection 복구 상태 - trade_ready 확정 전")

    def test_watch_only_bucket_classifies_recovery_from_partial_meta(self):
        bot = self._make_bot()
        bot.selection_meta["US"] = {
            "_parse_recovered": True,
            "_fallback_mode": "selection_partial",
            "trade_ready": [],
        }

        self.assertEqual(bot._watch_only_bucket("US", "NVDA"), "RECOVERY")
        self.assertFalse(bot._can_recheck_soft_watch_only("US", "NVDA", "CAUTIOUS_BEAR"))

    def test_watch_only_bucket_classifies_hard_veto(self):
        bot = self._make_bot()
        bot.selection_meta["US"] = {
            "veto": {"AAPL": "저유동성 제한"},
            "trade_ready": ["NVDA"],
        }

        self.assertEqual(bot._watch_only_bucket("US", "AAPL"), "HARD")

    def test_watch_only_bucket_classifies_soft_reason(self):
        bot = self._make_bot()
        bot.enable_soft_watch_promotion = True
        bot.today_ticker_reasons["US"] = {"AAPL": "RS 약세. watch 유지"}
        bot.selection_meta["US"] = {"trade_ready": ["NVDA"]}

        self.assertEqual(bot._watch_only_bucket("US", "AAPL"), "SOFT")
        self.assertTrue(bot._can_recheck_soft_watch_only("US", "AAPL", "CAUTIOUS_BEAR"))
        self.assertFalse(bot._can_recheck_soft_watch_only("US", "AAPL", "DEFENSIVE"))

    def test_soft_watch_promotion_disabled_by_default(self):
        bot = self._make_bot()
        bot.today_ticker_reasons["US"] = {"AAPL": "RS 약세. watch 유지"}
        bot.selection_meta["US"] = {"trade_ready": ["NVDA"]}

        self.assertFalse(bot._can_recheck_soft_watch_only("US", "AAPL", "CAUTIOUS_BEAR"))

    def test_promote_trade_ready_ticker_updates_state(self):
        bot = self._make_bot()
        bot.selection_meta["US"] = {
            "watchlist": ["AAPL"],
            "trade_ready": [],
            "recommended_strategy": {"AAPL": "gap_pullback"},
        }
        bot.today_judgment = {"market": "US", "consensus": {"mode": "NEUTRAL"}}

        changed = bot._promote_trade_ready_ticker("US", "aapl", strategy_name="gap_pullback")

        self.assertTrue(changed)
        self.assertIn("AAPL", bot.trade_ready_tickers["US"])
        self.assertIn("AAPL", bot.selection_meta["US"]["trade_ready"])
        self.assertIn("AAPL", bot.today_judgment["trade_ready_tickers"])

    def test_selection_replace_candidates_preserves_explicit_empty_trade_ready(self):
        bot = self._make_bot()

        selected = bot._selection_replace_candidates({"trade_ready": []}, ["AAPL", "NVDA"])
        legacy = bot._selection_replace_candidates({}, ["AAPL", "NVDA"])

        self.assertEqual(selected, [])
        self.assertEqual(legacy, ["AAPL", "NVDA"])

    def test_normalize_selection_meta_runtime_caps_trade_ready_by_slot(self):
        bot = self._make_bot()
        meta = {
            "watchlist": ["A", "B", "C", "D", "E", "F", "G"],
            "trade_ready": ["A", "B", "C", "D", "E", "F", "G"],
            "recommended_strategy": {
                "A": "momentum",
                "B": "momentum",
                "C": "momentum",
                "D": "gap_pullback",
                "E": "gap_pullback",
                "F": "opening_range_pullback",
                "G": "mean_reversion",
            },
        }

        normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="MODERATE_BULL")

        self.assertEqual(normalized["trade_ready"], ["A", "B", "D", "E", "F", "G"])

    def test_normalize_selection_meta_runtime_filters_continuation_when_live_disabled(self):
        bot = self._make_bot()
        meta = {
            "watchlist": ["A", "B"],
            "trade_ready": ["A", "B"],
            "recommended_strategy": {
                "A": "continuation",
                "B": "gap_pullback",
            },
        }

        normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="MODERATE_BULL")

        self.assertEqual(normalized["trade_ready"], ["B"])
        self.assertEqual(
            normalized["_runtime_filtered_trade_ready"],
            {"A": "continuation_shadow_only"},
        )

    def test_selection_active_strategies_excludes_continuation_when_live_disabled(self):
        bot = self._make_bot()

        active = bot._selection_active_strategies("US", "MILD_BULL")

        self.assertNotIn("continuation", active)

    def test_trade_ready_slot_config_shrinks_kr_momentum_slots(self):
        bot = self._make_bot()

        risk_on = bot._trade_ready_slot_config("MILD_BULL", "KR")
        balanced = bot._trade_ready_slot_config("CAUTIOUS", "KR")

        self.assertEqual(risk_on["momentum"], 1)
        self.assertEqual(balanced["momentum"], 0)

    def test_pick_partial_replace_in_prefers_matching_slot_then_diverse(self):
        bot = self._make_bot()
        bot.selection_meta["KR"] = {
            "recommended_strategy": {
                "005930": "momentum",
                "000660": "gap_pullback",
            }
        }
        candidate_meta = {
            "recommended_strategy": {
                "111111": "momentum",
                "222222": "gap_pullback",
                "333333": "gap_pullback",
            }
        }
        candidate_map = {
            "111111": {"ticker": "111111", "category": "leader", "sector": "semis", "from_high_bucket": "near_high", "liquidity_bucket": "high"},
            "222222": {"ticker": "222222", "category": "earnings", "sector": "autos", "from_high_bucket": "pullback", "liquidity_bucket": "high"},
            "333333": {"ticker": "333333", "category": "earnings", "sector": "autos", "from_high_bucket": "pullback", "liquidity_bucket": "low"},
        }

        picked = bot._pick_partial_replace_in(
            "KR",
            ["005930", "000660"],
            ["111111", "222222", "333333"],
            candidate_meta,
            candidate_map,
            2,
        )

        self.assertEqual(picked, ["111111", "222222"])

    def test_fetch_signal_ready_ohlcv_tops_up_us_history(self):
        bot = self._make_bot()
        primary = _make_ohlcv_df(100, start="2025-01-01")
        fallback = _make_ohlcv_df(180, start="2024-09-01")

        def _fake_calc_all(df):
            usable = max(len(df) - 59, 0)
            return pd.DataFrame({"ma60": [1.0] * usable})

        with patch.object(trading_bot_module, "get_daily_ohlcv", return_value=primary), \
             patch.object(trading_bot_module, "_daily_ohlcv_us_yf", return_value=fallback), \
             patch.object(trading_bot_module, "_daily_ohlcv_us_alpha", return_value=pd.DataFrame()), \
             patch.object(trading_bot_module, "calc_all", side_effect=_fake_calc_all):
            df, usable, source = bot._fetch_signal_ready_ohlcv("AAPL", "US", lookback_days=260)

        self.assertGreaterEqual(len(df), 180)
        self.assertGreaterEqual(usable, bot._MIN_SIGNAL_ROWS)
        self.assertEqual(source, "yfinance")


class OpsReviewSnapshotTests(unittest.TestCase):
    def test_build_ops_review_snapshot_aggregates_review_metrics(self):
        bot = trading_bot_module.TradingBot.__new__(trading_bot_module.TradingBot)
        bot.is_paper = False
        bot._active_session_date = {"KR": date(2026, 4, 22), "US": None}
        base_dir = Path(tempfile.mkdtemp())
        judgment_dir = base_dir / "judgment"
        judgment_dir.mkdir(parents=True, exist_ok=True)
        selection_db = base_dir / "ticker_selection_log.db"
        decisions_db = base_dir / "decisions.db"

        (judgment_dir / "live_20260421_KR.json").write_text(
            json.dumps(
                {
                    "date": "2026-04-21",
                    "market": "KR",
                    "actual_result": {"market_change": -0.6},
                    "judgment_eval": {
                        "consensus_hit": False,
                        "analyst_hits": {"bull": True, "bear": False, "neutral": False},
                        "unanimous_consensus_mismatch": True,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        orig_tsdb = tsdb.DB_PATH
        try:
            tsdb.DB_PATH = str(selection_db)
            tsdb.init()
            with sqlite3.connect(selection_db) as conn:
                conn.executemany(
                    """
                    INSERT INTO ticker_selection_log
                        (bot_mode, date, market, ticker, trade_ready, signal_fired, blocked_reason, forward_3d, max_runup_3d)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("live", "2026-04-21", "KR", "A", 1, 1, None, 1.2, 2.0),
                        ("live", "2026-04-21", "KR", "B", 1, 0, None, -0.4, 1.0),
                        ("live", "2026-04-22", "KR", "C", 1, 1, "momentum_atr_too_high", 0.6, 5.5),
                        ("live", "2026-04-22", "KR", "D", 0, 0, None, 1.0, 6.2),
                        ("live", "2026-04-22", "KR", "E", 0, 0, None, -0.2, 1.1),
                    ],
                )
            with sqlite3.connect(decisions_db) as conn:
                conn.execute(
                    """
                    CREATE TABLE decisions (
                        market TEXT,
                        decision TEXT,
                        block_reason TEXT,
                        strategy_used TEXT,
                        pnl_pct REAL,
                        data_source TEXT,
                        session_date TEXT
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO decisions (market, decision, block_reason, strategy_used, pnl_pct, data_source, session_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("KR", "BLOCKED", "entry_blackout", None, None, "live", "2026-04-21"),
                        ("KR", "BLOCKED", "watch_only", None, None, "live", "2026-04-22"),
                        ("KR", "BUY_SIGNAL", None, "continuation", -4.2, "live", "2026-04-22"),
                    ],
                )

            current_record = {
                "date": "2026-04-22",
                "market": "KR",
                "actual_result": {"market_change": 0.8},
                "judgment_eval": {
                    "consensus_hit": True,
                    "analyst_hits": {"bull": True, "bear": True, "neutral": False},
                    "unanimous_consensus_mismatch": False,
                },
            }

            with patch.object(trading_bot_module, "JUDGMENT_DIR", judgment_dir), \
                 patch.object(trading_bot_module, "_DECISIONS_DB_PATH", decisions_db):
                snapshot = bot._build_ops_review_snapshot("KR", current_record=current_record)
        finally:
            tsdb.DB_PATH = orig_tsdb

        metrics = snapshot["metrics"]
        self.assertEqual(snapshot["judgment_sessions"], 2)
        self.assertEqual(snapshot["best_analyst"], "bull")
        self.assertEqual(metrics["consensus_directional_hit_rate"]["value"], 50.0)
        self.assertEqual(metrics["best_analyst_minus_consensus_hit_gap"]["value"], 50.0)
        self.assertEqual(metrics["unanimous_mismatch_count"]["value"], 1)
        self.assertEqual(metrics["trade_ready_signal_conversion"]["value"], 66.7)
        self.assertEqual(metrics["watch_only_missed_runup_ratio"]["value"], 50.0)
        self.assertAlmostEqual(metrics["trade_ready_forward_3d_average"]["value"], 0.467, places=3)
        self.assertEqual(metrics["atr_blocked_missed_runup"]["value"], 5.5)
        self.assertEqual(metrics["entry_blackout_ratio"]["value"], 50.0)
        self.assertEqual(metrics["watch_only_blocked_ratio"]["value"], 50.0)
        self.assertEqual(metrics["continuation_average_pnl"]["value"], -4.2)
        self.assertTrue(snapshot["triggers"]["large_analyst_gap"])
        self.assertTrue(snapshot["triggers"]["unanimous_mismatch"])
        self.assertTrue(snapshot["triggers"]["high_entry_blackout_ratio"])
        self.assertTrue(snapshot["triggers"]["high_watch_only_blocked_ratio"])
        self.assertFalse(snapshot["triggers"]["low_trade_ready_conversion"])


class TradingBotSessionDateTests(unittest.TestCase):
    def _make_bot(self):
        bot = trading_bot_module.TradingBot.__new__(trading_bot_module.TradingBot)
        bot.is_paper = True
        bot._active_session_date = {"KR": None, "US": None}
        bot.risk = SimpleNamespace(daily_pnl=0.0, all_trade_log=[])
        bot.today_judgment = {}
        bot.today_ticker_reasons = {"KR": {}, "US": {}}
        bot.trade_ready_tickers = {"KR": [], "US": []}
        bot.selection_meta = {"KR": {}, "US": {}}
        return bot

    def test_has_same_day_trade_uses_session_date_across_us_midnight(self):
        bot = self._make_bot()
        bot._active_session_date["US"] = date(2026, 4, 21)
        bot.risk.all_trade_log = [
            {
                "ticker": "NVDA",
                "side": "buy",
                "date": "2026-04-22",
                "session_date": "2026-04-21",
            }
        ]

        self.assertTrue(bot._has_same_day_trade("nvda", "US"))

    def test_restore_daily_pnl_from_decisions_uses_session_date_across_us_midnight(self):
        bot = self._make_bot()
        bot._active_session_date["US"] = date(2026, 4, 21)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decisions.jsonl"
            db_path.write_text(
                json.dumps(
                    {
                        "type": "closed",
                        "market": "US",
                        "ticker": "NVDA",
                        "timestamp": "2026-04-22T00:30:00+09:00",
                        "session_date": "2026-04-21",
                        "pnl_krw": 1234.0,
                        "order_no": "A1",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.object(trading_bot_module, "DECISIONS_FILE", db_path):
                bot._restore_daily_pnl_from_decisions("US")

        self.assertEqual(bot.risk.daily_pnl, 1234.0)

    def test_backfill_missed_postmortem_skips_current_active_us_session(self):
        bot = self._make_bot()
        bot._active_session_date["US"] = date(2026, 4, 21)

        class _FakeDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 4, 22)

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            record_path = base_dir / "paper_20260421_US.json"
            record_path.write_text(
                json.dumps({"judgments": {"bull": {"stance": "BULL"}}}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.object(trading_bot_module, "JUDGMENT_DIR", base_dir), \
                 patch.object(trading_bot_module, "date", _FakeDate), \
                 patch.object(trading_bot_module, "run_postmortem") as mocked_postmortem:
                bot._backfill_missed_postmortem("US")

        mocked_postmortem.assert_not_called()

    def test_persist_live_judgment_uses_active_session_date_filename(self):
        bot = self._make_bot()
        bot.is_paper = False
        bot._active_session_date["US"] = date(2026, 4, 21)
        bot.today_judgment = {"market": "US", "consensus": {"mode": "NEUTRAL"}}

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            with patch.object(trading_bot_module, "JUDGMENT_DIR", base_dir):
                bot._persist_live_judgment = trading_bot_module.TradingBot._persist_live_judgment.__get__(
                    bot, trading_bot_module.TradingBot
                )
                bot._persist_live_judgment("US")

            self.assertTrue((base_dir / "live_20260421_US.json").exists())


class TradingBotRecoveryTests(unittest.TestCase):
    def _make_bot(self):
        bot = trading_bot_module.TradingBot.__new__(trading_bot_module.TradingBot)
        bot.is_paper = True
        bot._active_session_date = {"KR": date(2026, 4, 23), "US": date(2026, 4, 23)}
        bot.usd_krw_rate = 1500.0
        bot.risk = SimpleNamespace(positions=[], trade_log=[], all_trade_log=[], cash=0.0)
        bot.pending_orders = []
        bot._funnel = {"KR": {"filled": 0}, "US": {"filled": 0}}
        bot._sell_fail_at = {}
        bot._exit_process_lock = __import__("threading").Lock()
        bot.current_market = None
        bot.enable_trailing_stop = True
        bot.token = "test-token"
        bot.price_cache = {}
        bot.price_cache_raw = {}
        bot._save_positions = Mock()
        bot._save_pending_orders = Mock()
        bot._block_entry = Mock()
        bot._execute_sell = Mock()
        bot._handle_max_hold_claude = Mock()
        return bot

    def test_refresh_position_holding_days_keeps_same_session_entry_at_zero(self):
        bot = self._make_bot()
        bot.risk.positions = [
            {
                "ticker": "OKLO",
                "entry_session_date": "2026-04-23",
                "session_date": "2026-04-23",
                "held_days": 99,
            },
            {
                "ticker": "AAPL",
                "entry_session_date": "2026-04-22",
                "session_date": "2026-04-22",
                "held_days": 0,
            },
        ]

        bot._refresh_position_holding_days("US")

        self.assertEqual(bot.risk.positions[0]["held_days"], 0)
        self.assertEqual(bot.risk.positions[1]["held_days"], 1)

    def test_make_runtime_position_from_broker_marks_untemplated_injection_protected(self):
        bot = self._make_bot()

        pos = bot._make_runtime_position_from_broker(
            "NVTS",
            "US",
            {"ticker": "NVTS", "qty": 2, "avg_price": 18.32, "eval_price": 18.32},
        )

        self.assertEqual(pos["position_origin"], "broker_injected")
        self.assertEqual(pos["position_integrity"], "protected")
        self.assertTrue(pos["management_protected"])

    def test_reconcile_pending_orders_persists_positions_when_fill_detected(self):
        bot = self._make_bot()
        bot.pending_orders = [
            {
                "market": "US",
                "ticker": "NVTS",
                "qty": 2,
                "raw_price": 18.47,
                "filled_price_native": 18.47,
                "order_no": "0031500432",
                "strategy": "continuation",
                "tp_pct": 0.02,
                "sl_pct": 0.01,
                "max_hold": 1,
                "session_date": "2026-04-23",
            }
        ]

        with patch.object(trading_bot_module, "fill_confirm_alert"):
            bot._reconcile_pending_orders(
                broker_kr={},
                broker_us={
                    "NVTS": {
                        "ticker": "NVTS",
                        "qty": 2,
                        "avg_price": 18.47,
                        "eval_price": 18.47,
                        "name": "NVTS",
                    }
                },
            )

        self.assertEqual(len(bot.risk.positions), 1)
        self.assertEqual(bot.pending_orders, [])
        bot._save_positions.assert_called_once()
        bot._save_pending_orders.assert_called_once()

    def test_process_exit_candidates_deduplicates_same_ticker(self):
        bot = self._make_bot()
        bot.risk = SimpleNamespace(
            get_exit_candidates=lambda: [
                {"ticker": "NVTS", "reason": "max_hold", "exit_price": 10.0},
                {"ticker": "NVTS", "reason": "trail_stop", "exit_price": 10.0},
            ]
        )

        with patch.object(trading_bot_module, "is_trading_halted", return_value=False):
            bot._process_exit_candidates()

        bot._execute_sell.assert_called_once()
        _, kwargs = bot._execute_sell.call_args
        self.assertEqual(kwargs["reason"], "trail_stop")
        bot._handle_max_hold_claude.assert_not_called()


class LessonCandidateTests(unittest.TestCase):
    def _make_bot(self):
        bot = trading_bot_module.TradingBot.__new__(trading_bot_module.TradingBot)
        bot.today_judgment = {}
        bot.decision_event_log = []
        return bot

    def test_persist_lesson_candidates_includes_affordability_cluster(self):
        bot = self._make_bot()
        bot.decision_event_log = [
            {
                "market": "US",
                "ticker": "GEV",
                "reason_family": "affordability",
                "reason": "order_size_too_small",
                "detail": "cost=28,000 budget=50,000 min=45,000",
            },
            {
                "market": "US",
                "ticker": "AVGO",
                "reason_family": "affordability",
                "reason": "insufficient_cash",
                "detail": "cost=510,000 cash=400,000",
            },
        ]
        snapshot = {
            "window_days": 14,
            "metrics": {
                "trade_ready_signal_conversion": {"value": 9.5, "sample": 24, "breached": True},
                "watch_only_missed_runup_ratio": {"value": 33.0, "sample": 22, "breached": True},
                "continuation_average_pnl": {"value": -4.2, "sample": 6, "breached": True},
                "unanimous_mismatch_count": {"value": 1, "sample": 5, "breached": True},
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "lesson_candidates.json"
            with patch.object(trading_bot_module, "_LESSON_CANDIDATES_PATH", path):
                candidates = bot._persist_lesson_candidates("US", snapshot)
                saved = json.loads(path.read_text(encoding="utf-8"))

        ids = {item["id"] for item in candidates}
        self.assertIn("affordability_fail_cluster", ids)
        self.assertIn("trade_ready_conversion_review", ids)
        self.assertEqual(saved["markets"]["US"], candidates)


class TunerRuntimeAdjustmentTests(unittest.TestCase):
    def test_coerce_runtime_adjustments_clamps_values(self):
        result = tuner_module._coerce_runtime_adjustments(
            {
                "momentum_wait_adjust_min": -30,
                "entry_priority_cutoff_adjust": "0.20",
                "kr_momentum_atr_cap_adjust": -0.05,
                "kr_momentum_atr_cap_high_adjust": 0.08,
            }
        )

        self.assertEqual(result["momentum_wait_adjust_min"], -15)
        self.assertEqual(result["entry_priority_cutoff_adjust"], 0.08)
        self.assertEqual(result["kr_momentum_atr_cap_adjust"], -0.02)
        self.assertEqual(result["kr_momentum_atr_cap_high_adjust"], 0.03)

    def test_runtime_adjustment_summary_formats_all_override_fields(self):
        summary = tuner_module._runtime_adjustment_summary(
            {
                "momentum_wait_adjust_min": -5,
                "entry_priority_cutoff_adjust": 0.02,
                "kr_momentum_atr_cap_adjust": 0.01,
                "kr_momentum_atr_cap_high_adjust": 0.0,
            }
        )

        self.assertEqual(summary, "wait=-5m cutoff=+0.02 kr_atr=+0.01/+0.00")


class DashboardSelectionReasonTests(unittest.TestCase):
    def test_selection_status_for_ticker_uses_trade_ready_membership(self):
        rec = {"selection_meta": {"trade_ready": ["NVDA", "AAPL"]}}

        self.assertEqual(
            dashboard_server_module._selection_status_for_ticker(rec, "nvda", "US"),
            "TRADE_READY",
        )
        self.assertEqual(
            dashboard_server_module._selection_status_for_ticker(rec, "MSFT", "US"),
            "WATCH_ONLY",
        )

    def test_resolve_ticker_select_reason_prefers_watch_only_detail(self):
        rec = {"selection_meta": {"trade_ready": []}}
        item = {"selection_status": "WATCH_ONLY"}

        reason = dashboard_server_module._resolve_ticker_select_reason(
            "NVDA",
            "US",
            "CAUTIOUS_BEAR",
            item,
            rec,
            base_reason="",
            watch_only_detail="RS 약세. watch 유지",
        )

        self.assertEqual(reason, "RS 약세. watch 유지")

    def test_resolve_ticker_select_reason_uses_veto_when_reason_missing(self):
        rec = {"selection_meta": {"trade_ready": ["NVDA"], "veto": {"AAPL": "저유동성 제한"}}}
        item = {"selection_status": "WATCH_ONLY"}

        reason = dashboard_server_module._resolve_ticker_select_reason(
            "AAPL",
            "US",
            "CAUTIOUS_BEAR",
            item,
            rec,
            base_reason="",
            watch_only_detail="",
        )

        self.assertEqual(reason, "TRADE_READY 제외 · 저유동성 제한")


class DashboardAnalysisFeedTests(unittest.TestCase):
    def test_load_analysis_records_for_session_reads_live_files_across_midnight(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            analysis_dir = base_dir / "logs" / "analysis"
            analysis_dir.mkdir(parents=True, exist_ok=True)
            session_rec = {
                "timestamp": "2026-04-22T00:23:22.271633",
                "extra": {"event": "entry_skip", "market": "US", "ticker": "HIMS", "reason": "watch_only"},
            }
            sysdate_rec = {
                "timestamp": "2026-04-22T01:31:10.000000",
                "extra": {"event": "signal_check", "market": "US", "ticker": "NVDA", "signal": "none"},
            }
            (analysis_dir / "live_analysis_20260421.jsonl").write_text(
                json.dumps(session_rec, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (analysis_dir / "live_analysis_20260422.jsonl").write_text(
                json.dumps(sysdate_rec, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            class _FakeDateTime:
                @classmethod
                def now(cls, tz=None):
                    return __import__("datetime").datetime(2026, 4, 22, 1, 40, 0, tzinfo=dashboard_server_module.KST)

            with patch.object(dashboard_server_module, "BASE_DIR", base_dir), \
                 patch.object(dashboard_server_module, "datetime", _FakeDateTime):
                records = dashboard_server_module._load_analysis_records_for_session("US", "live")

            timestamps = [rec.get("timestamp") for rec in records]
            self.assertIn("2026-04-22T00:23:22.271633", timestamps)
            self.assertIn("2026-04-22T01:31:10.000000", timestamps)


class DashboardBrainLoadTests(unittest.TestCase):
    def test_load_brain_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            brain_path = Path(tmpdir) / "brain.json"
            brain_path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"ok": True}).encode("utf-8"))

            with patch.object(dashboard_server_module, "BRAIN_PATH", brain_path):
                brain = dashboard_server_module.load_brain()

        self.assertEqual(brain, {"ok": True})

    def test_load_brain_returns_empty_dict_on_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            brain_path = Path(tmpdir) / "brain.json"
            brain_path.write_text('{"broken": "value"', encoding="utf-8")

            with patch.object(dashboard_server_module, "BRAIN_PATH", brain_path):
                brain = dashboard_server_module.load_brain()

        self.assertEqual(brain, {})


class AdaptiveParamsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "decisions.db"
        self._orig_db = adaptive_params._DB
        adaptive_params._DB = self._db_path
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE decisions (
                    market TEXT,
                    strategy_used TEXT,
                    decision TEXT,
                    forward_1d REAL,
                    pnl_pct REAL,
                    data_source TEXT,
                    session_date TEXT
                )
                """
            )

    def tearDown(self):
        adaptive_params._DB = self._orig_db
        self._tmp.cleanup()

    def test_get_perf_stats_uses_realized_pnl_when_forward_return_missing(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.executemany(
                """
                INSERT INTO decisions (market, strategy_used, decision, forward_1d, pnl_pct, data_source, session_date)
                VALUES (?, ?, 'BUY_SIGNAL', ?, ?, 'live', '2026-04-21')
                """,
                [
                    ("KR", "gap_pullback", None, 2.1),
                    ("KR", "gap_pullback", None, -1.2),
                    ("KR", "gap_pullback", None, 0.7),
                    ("KR", "gap_pullback", None, 1.4),
                    ("KR", "gap_pullback", None, -0.5),
                    ("KR", "gap_pullback", None, 0.3),
                ],
            )

        stats = adaptive_params.get_perf_stats("gap_pullback", "KR", days=30)

        self.assertEqual(stats["source"], "live_small")
        self.assertEqual(stats["n"], 6)
        self.assertEqual(stats["win_rate"], 66.7)


class ParamTunerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)
        self._orig_db_path = param_tuner._DB_PATH
        self._orig_state_path = param_tuner._SESSION_STATE_PATH
        self._orig_registry = {k: list(v) for k, v in param_tuner._session_registry.items()}
        self._orig_cache = dict(param_tuner._cache)
        self._orig_cache_mode = dict(param_tuner._cache_mode)

        param_tuner._DB_PATH = self._tmp_path / "decisions.db"
        param_tuner._SESSION_STATE_PATH = self._tmp_path / "param_tuner_sessions.json"
        param_tuner._session_registry.clear()
        param_tuner.clear_cache()
        param_tuner.ensure_table()

    def tearDown(self):
        param_tuner._DB_PATH = self._orig_db_path
        param_tuner._SESSION_STATE_PATH = self._orig_state_path
        param_tuner._session_registry.clear()
        param_tuner._session_registry.update(self._orig_registry)
        param_tuner._cache.clear()
        param_tuner._cache.update(self._orig_cache)
        param_tuner._cache_mode.clear()
        param_tuner._cache_mode.update(self._orig_cache_mode)
        self._tmp.cleanup()

    def test_update_outcomes_writes_strategy_specific_rows(self):
        sid1 = param_tuner._save_session(
            market="KR",
            mode="NEUTRAL",
            trigger="session_open",
            strategy="momentum",
            base_params={"tp_pct": 0.02},
            claude_params={"tp_pct": 0.02},
            reason="same",
            context={"vix": 18.0, "usd_krw": 1390.0, "analyst_conf": 0.61},
        )
        sid2 = param_tuner._save_session(
            market="KR",
            mode="NEUTRAL",
            trigger="session_open",
            strategy="gap_pullback",
            base_params={"tp_pct": 0.025},
            claude_params={"tp_pct": 0.025},
            reason="same",
            context={"vix": 18.0, "usd_krw": 1390.0, "analyst_conf": 0.61},
        )

        param_tuner.update_outcomes(
            [sid1, sid2],
            signals=0,
            entries=0,
            wins=0,
            losses=0,
            avg_pnl_pct=0.0,
            total_pnl_krw=0.0,
            strategy_outcomes={
                "momentum": {
                    "signals": 3,
                    "entries": 2,
                    "wins": 1,
                    "losses": 1,
                    "avg_pnl_pct": 1.2,
                    "total_pnl_krw": 12500.0,
                },
                "gap_pullback": {
                    "signals": 0,
                    "entries": 0,
                    "wins": 0,
                    "losses": 0,
                    "avg_pnl_pct": 0.0,
                    "total_pnl_krw": 0.0,
                },
            },
        )

        with param_tuner._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT strategy, signals_count, entries_count, wins, losses, avg_pnl_pct, total_pnl_krw
                FROM param_sessions
                ORDER BY id
                """
            ).fetchall()

        self.assertEqual(
            rows,
            [
                ("momentum", 3, 2, 1, 1, 1.2, 12500.0),
                ("gap_pullback", 0, 0, 0, 0, 0.0, 0.0),
            ],
        )
        history = param_tuner.get_recent_history("KR", days=5)
        self.assertEqual({item["strategy"] for item in history}, {"momentum", "gap_pullback"})


class TickerSelectionDBTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "ticker_selection_log.db"
        self._price_dir = Path(self._tmp.name) / "price"
        self._orig_db_path = tsdb.DB_PATH
        self._orig_price_dir = tsdb.PRICE_DIR
        tsdb.DB_PATH = str(self._db_path)
        tsdb.PRICE_DIR = str(self._price_dir)
        tsdb._price_cache.clear()
        tsdb.init()

    def tearDown(self):
        tsdb.DB_PATH = self._orig_db_path
        tsdb.PRICE_DIR = self._orig_price_dir
        tsdb._price_cache.clear()
        self._tmp.cleanup()

    def _write_price_csv(self, market, ticker, rows):
        market_dir = self._price_dir / market.lower()
        market_dir.mkdir(parents=True, exist_ok=True)
        path = market_dir / f"{market.lower()}_{ticker}.csv"
        lines = ["date,open,high,low,close,volume"]
        for row in rows:
            lines.append(
                f"{row['date']},{row['open']},{row['high']},{row['low']},{row['close']},{row['volume']}"
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    def test_init_adds_selection_meta_columns(self):
        with sqlite3.connect(tsdb.DB_PATH) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(ticker_selection_log)")}

        for column in (
            "watchlist_rank",
            "trade_ready",
            "veto_reason",
            "risk_tags",
            "recommended_strategy",
            "max_position_pct",
            "market_type",
            "category",
            "liquidity_bucket",
            "from_high_bucket",
            "forward_1d",
            "forward_3d",
            "forward_5d",
            "max_runup_3d",
            "max_drawdown_3d",
            "max_runup_5d",
            "max_drawdown_5d",
        ):
            self.assertIn(column, columns)

    def test_insert_batch_persists_trade_ready_and_selection_meta(self):
        row_ids = tsdb.insert_batch(
            date="2026-04-21",
            market="KR",
            source_type="initial",
            selected=["005930", "000660"],
            candidates=[
                {
                    "ticker": "005930",
                    "change_rate": 1.2,
                    "vol_ratio": 2.1,
                    "gap_pct": 1.0,
                    "from_high_pct": -0.3,
                    "market_type": "KOSPI",
                    "category": "momentum",
                    "sector": "Semiconductors",
                    "liquidity_bucket": "high",
                    "from_high_bucket": "at_high",
                },
                {
                    "ticker": "000660",
                    "change_rate": 0.8,
                    "vol_ratio": 1.5,
                    "gap_pct": 0.6,
                    "from_high_pct": -0.8,
                    "market_type": "KOSDAQ",
                    "category": "earnings_momentum",
                    "sector": "AI",
                    "liquidity_bucket": "mid",
                    "from_high_bucket": "near_high",
                },
            ],
            sel_reasons={"005930": "대표주", "000660": "관망"},
            consensus_mode="NEUTRAL",
            selection_meta={
                "trade_ready": ["005930"],
                "veto": {"000660": "손절폭 과대"},
                "risk_tags": {"005930": ["변동성"], "000660": ["관망"]},
                "recommended_strategy": {"005930": "momentum", "000660": "observe"},
                "max_position_pct": {"005930": 20, "000660": 10},
            },
        )

        self.assertEqual(set(row_ids), {"005930", "000660"})

        with sqlite3.connect(tsdb.DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT ticker, watchlist_rank, trade_ready, veto_reason, risk_tags,
                       recommended_strategy, max_position_pct, market_type, category, sector,
                       liquidity_bucket, from_high_bucket
                FROM ticker_selection_log
                ORDER BY watchlist_rank
                """
            ).fetchall()

        self.assertEqual(rows[0][0], "005930")
        self.assertEqual(rows[0][1], 1)
        self.assertEqual(rows[0][2], 1)
        self.assertIsNone(rows[0][3])
        self.assertEqual(json.loads(rows[0][4]), ["변동성"])
        self.assertEqual(rows[0][5], "momentum")
        self.assertEqual(rows[0][6], 20.0)
        self.assertEqual(rows[0][7], "KOSPI")
        self.assertEqual(rows[0][8], "momentum")
        self.assertEqual(rows[0][9], "Semiconductors")
        self.assertEqual(rows[0][10], "high")
        self.assertEqual(rows[0][11], "at_high")

        self.assertEqual(rows[1][0], "000660")
        self.assertEqual(rows[1][1], 2)
        self.assertEqual(rows[1][2], 0)
        self.assertEqual(rows[1][3], "손절폭 과대")
        self.assertEqual(json.loads(rows[1][4]), ["관망"])
        self.assertEqual(rows[1][5], "observe")
        self.assertEqual(rows[1][6], 10.0)
        self.assertEqual(rows[1][7], "KOSDAQ")
        self.assertEqual(rows[1][8], "earnings_momentum")
        self.assertEqual(rows[1][9], "AI")
        self.assertEqual(rows[1][10], "mid")
        self.assertEqual(rows[1][11], "near_high")


    def test_update_forward_returns_fills_watchlist_outcomes(self):
        self._write_price_csv(
            "KR",
            "005930",
            [
                {"date": "2026-04-21", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
                {"date": "2026-04-22", "open": 102, "high": 111, "low": 101, "close": 110, "volume": 1000},
                {"date": "2026-04-23", "open": 109, "high": 117, "low": 108, "close": 115, "volume": 1000},
                {"date": "2026-04-24", "open": 116, "high": 121, "low": 114, "close": 120, "volume": 1000},
            ],
        )
        self._write_price_csv(
            "KR",
            "000660",
            [
                {"date": "2026-04-21", "open": 200, "high": 201, "low": 198, "close": 200, "volume": 1000},
                {"date": "2026-04-22", "open": 198, "high": 199, "low": 195, "close": 196, "volume": 1000},
                {"date": "2026-04-23", "open": 202, "high": 207, "low": 201, "close": 205, "volume": 1000},
                {"date": "2026-04-24", "open": 206, "high": 211, "low": 204, "close": 210, "volume": 1000},
            ],
        )

        tsdb.insert_batch(
            date="2026-04-21",
            market="KR",
            source_type="initial",
            selected=["005930", "000660"],
            candidates=[
                {"ticker": "005930", "change_rate": 1.2, "vol_ratio": 2.1},
                {"ticker": "000660", "change_rate": 0.8, "vol_ratio": 1.5},
            ],
            sel_reasons={"005930": "leader", "000660": "watch"},
            consensus_mode="NEUTRAL",
            selection_meta={"trade_ready": ["005930"]},
        )

        stats = tsdb.update_forward_returns(market="KR")

        self.assertEqual(stats["updated"], 2)
        self.assertEqual(stats["missing_csv"], 0)

        with sqlite3.connect(tsdb.DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT
                    ticker, trade_ready,
                    forward_1d, forward_3d, forward_5d,
                    max_runup_3d, max_drawdown_3d,
                    max_runup_5d, max_drawdown_5d
                FROM ticker_selection_log
                ORDER BY watchlist_rank
                """
            ).fetchall()

        self.assertEqual(rows[0], ("005930", 1, 10.0, 20.0, None, 21.0, 1.0, None, None))
        self.assertEqual(rows[1], ("000660", 0, -2.0, 5.0, None, 5.5, -2.5, None, None))

    def test_update_forward_returns_is_idempotent_when_no_new_future_data(self):
        self._write_price_csv(
            "US",
            "AAPL",
            [
                {"date": "2026-04-21", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
                {"date": "2026-04-22", "open": 101, "high": 106, "low": 100, "close": 105, "volume": 1000},
            ],
        )

        tsdb.insert_batch(
            date="2026-04-21",
            market="US",
            source_type="initial",
            selected=["AAPL"],
            candidates=[{"ticker": "AAPL", "change_rate": 1.1, "vol_ratio": 2.3}],
            sel_reasons={"AAPL": "breakout"},
            consensus_mode="MILD_BULL",
            selection_meta={"trade_ready": ["AAPL"]},
        )

        first = tsdb.update_forward_returns(market="US")
        second = tsdb.update_forward_returns(market="US")

        self.assertEqual(first["updated"], 1)
        self.assertEqual(second["updated"], 0)

        with sqlite3.connect(tsdb.DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT forward_1d, forward_3d, forward_5d
                FROM ticker_selection_log
                WHERE market='US' AND ticker='AAPL'
                """
            ).fetchone()

        self.assertEqual(row, (5.0, None, None))

    def test_update_forward_returns_populates_5d_runup_and_drawdown(self):
        self._write_price_csv(
            "US",
            "MSFT",
            [
                {"date": "2026-04-21", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
                {"date": "2026-04-22", "open": 101, "high": 104, "low": 99, "close": 103, "volume": 1000},
                {"date": "2026-04-23", "open": 103, "high": 106, "low": 98, "close": 105, "volume": 1000},
                {"date": "2026-04-24", "open": 105, "high": 108, "low": 101, "close": 107, "volume": 1000},
                {"date": "2026-04-25", "open": 106, "high": 107, "low": 97, "close": 104, "volume": 1000},
                {"date": "2026-04-26", "open": 104, "high": 110, "low": 103, "close": 109, "volume": 1000},
            ],
        )

        tsdb.insert_batch(
            date="2026-04-21",
            market="US",
            source_type="initial",
            selected=["MSFT"],
            candidates=[{"ticker": "MSFT", "change_rate": 1.1, "vol_ratio": 2.3}],
            sel_reasons={"MSFT": "leader"},
            consensus_mode="MILD_BULL",
            selection_meta={"trade_ready": ["MSFT"]},
        )

        stats = tsdb.update_forward_returns(market="US")

        self.assertEqual(stats["updated"], 1)

        with sqlite3.connect(tsdb.DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT
                    forward_1d, forward_3d, forward_5d,
                    max_runup_3d, max_drawdown_3d,
                    max_runup_5d, max_drawdown_5d
                FROM ticker_selection_log
                WHERE market='US' AND ticker='MSFT'
                """
            ).fetchone()

        self.assertEqual(row, (3.0, 7.0, 9.0, 8.0, -2.0, 10.0, -3.0))

    def test_get_recent_selection_feedback_summarizes_trade_ready_and_watch_only(self):
        tsdb.insert_batch(
            date="2026-04-21",
            market="KR",
            source_type="initial",
            selected=["005930", "000660", "035420", "051910"],
            candidates=[
                {"ticker": "005930", "market_type": "KOSPI", "category": "leader", "liquidity_bucket": "high", "from_high_bucket": "at_high"},
                {"ticker": "000660", "market_type": "KOSPI", "category": "leader", "liquidity_bucket": "mid", "from_high_bucket": "pullback"},
                {"ticker": "035420", "market_type": "KOSDAQ", "category": "earnings_momentum", "liquidity_bucket": "low", "from_high_bucket": "near_high"},
                {"ticker": "051910", "market_type": "KOSDAQ", "category": "earnings_momentum", "liquidity_bucket": "low", "from_high_bucket": "near_high"},
            ],
            sel_reasons={},
            consensus_mode="NEUTRAL",
            selection_meta={
                "trade_ready": ["005930", "000660"],
                "recommended_strategy": {
                    "005930": "momentum",
                    "000660": "momentum",
                    "035420": "observe",
                    "051910": "observe",
                },
            },
        )

        with sqlite3.connect(tsdb.DB_PATH) as conn:
            conn.execute(
                """
                UPDATE ticker_selection_log
                SET traded=CASE ticker WHEN '005930' THEN 1 ELSE traded END,
                    forward_3d=CASE ticker
                        WHEN '005930' THEN 4.0
                        WHEN '000660' THEN -1.0
                        WHEN '035420' THEN 2.0
                        WHEN '051910' THEN -3.0
                    END,
                    max_runup_3d=CASE ticker
                        WHEN '005930' THEN 8.0
                        WHEN '000660' THEN 3.0
                        WHEN '035420' THEN 7.0
                        WHEN '051910' THEN 1.0
                    END,
                    max_drawdown_3d=CASE ticker
                        WHEN '005930' THEN -2.0
                        WHEN '000660' THEN -4.0
                        WHEN '035420' THEN -1.0
                        WHEN '051910' THEN -5.0
                    END
                """
            )

        summary = tsdb.get_recent_selection_feedback("KR", days=20, as_of="2026-04-30")
        text = tsdb.format_recent_selection_feedback("KR", days=20, as_of="2026-04-30")

        self.assertEqual(summary["total_rows"], 4)
        self.assertEqual(summary["trade_ready_rows"], 2)
        self.assertEqual(summary["watch_only_rows"], 2)
        self.assertEqual(summary["traded_rows"], 1)
        self.assertEqual(summary["trade_ready_hit_rate_3d"], 50.0)
        self.assertEqual(summary["missed_watch_only_count"], 1)
        self.assertEqual(summary["missed_watch_only_rate_3d"], 50.0)
        self.assertEqual(summary["weak_trade_ready_count"], 1)
        self.assertEqual(summary["weak_trade_ready_rate_3d"], 50.0)
        self.assertIn("trade_ready 3d: hit_rate=50.0%", text)
        self.assertIn("missed watch_only: runup>=5.0% 1건 (50.0%)", text)
        self.assertIn("by board: KOSDAQ", text)
        self.assertIn("by category: earnings_momentum", text)
        self.assertIn("by liquidity: low", text)
        self.assertIn("by pullback: near_high", text)
        self.assertIn("by strategy: observe", text)

    def test_selection_feedback_breakdown_ranks_group_with_missed_watch_only(self):
        tsdb.insert_batch(
            date="2026-04-21",
            market="US",
            source_type="initial",
            selected=["AAPL", "MSFT", "NVDA", "AMD"],
            candidates=[
                {"ticker": "AAPL", "market_type": "NASDAQ", "category": "mega_cap", "liquidity_bucket": "high", "from_high_bucket": "at_high"},
                {"ticker": "MSFT", "market_type": "NASDAQ", "category": "mega_cap", "liquidity_bucket": "high", "from_high_bucket": "pullback"},
                {"ticker": "NVDA", "market_type": "NASDAQ", "category": "ai_momentum", "liquidity_bucket": "mid", "from_high_bucket": "near_high"},
                {"ticker": "AMD", "market_type": "NASDAQ", "category": "ai_momentum", "liquidity_bucket": "mid", "from_high_bucket": "near_high"},
            ],
            sel_reasons={},
            consensus_mode="MILD_BULL",
            selection_meta={"trade_ready": ["AAPL", "MSFT"]},
        )

        with sqlite3.connect(tsdb.DB_PATH) as conn:
            conn.execute(
                """
                UPDATE ticker_selection_log
                SET forward_3d=CASE ticker
                        WHEN 'AAPL' THEN 1.0
                        WHEN 'MSFT' THEN -2.0
                        WHEN 'NVDA' THEN 3.0
                        WHEN 'AMD' THEN 1.5
                    END,
                    max_runup_3d=CASE ticker
                        WHEN 'AAPL' THEN 2.0
                        WHEN 'MSFT' THEN 4.0
                        WHEN 'NVDA' THEN 9.0
                        WHEN 'AMD' THEN 8.0
                    END,
                    max_drawdown_3d=CASE ticker
                        WHEN 'AAPL' THEN -1.0
                        WHEN 'MSFT' THEN -3.0
                        WHEN 'NVDA' THEN -2.0
                        WHEN 'AMD' THEN -2.5
                    END
                """
            )

        rows = tsdb.get_recent_selection_feedback_breakdown("US", "category", days=20, as_of="2026-04-30")
        text = tsdb.format_recent_selection_feedback_breakdown("US", "category", days=20, as_of="2026-04-30")

        self.assertEqual(rows[0]["group_value"], "ai_momentum")
        self.assertEqual(rows[0]["missed_watch_only_count"], 2)
        self.assertEqual(rows[0]["missed_watch_only_rate_3d"], 100.0)
        self.assertIn("by category: ai_momentum", text)
        self.assertIn("miss_watch=100.0%(n=2)", text)

    def test_selection_feedback_breakdown_supports_liquidity_and_pullback_groups(self):
        tsdb.insert_batch(
            date="2026-04-21",
            market="US",
            source_type="initial",
            selected=["QQQ", "SMH", "IWM", "SOXL"],
            candidates=[
                {"ticker": "QQQ", "liquidity_bucket": "high", "from_high_bucket": "at_high"},
                {"ticker": "SMH", "liquidity_bucket": "mid", "from_high_bucket": "near_high"},
                {"ticker": "IWM", "liquidity_bucket": "low", "from_high_bucket": "deep"},
                {"ticker": "SOXL", "liquidity_bucket": "low", "from_high_bucket": "deep"},
            ],
            sel_reasons={},
            consensus_mode="MILD_BULL",
            selection_meta={"trade_ready": ["QQQ", "SMH"]},
        )

        with sqlite3.connect(tsdb.DB_PATH) as conn:
            conn.execute(
                """
                UPDATE ticker_selection_log
                SET forward_3d=CASE ticker
                        WHEN 'QQQ' THEN 2.0
                        WHEN 'SMH' THEN -1.0
                        WHEN 'IWM' THEN 1.0
                        WHEN 'SOXL' THEN 2.0
                    END,
                    max_runup_3d=CASE ticker
                        WHEN 'QQQ' THEN 3.0
                        WHEN 'SMH' THEN 4.0
                        WHEN 'IWM' THEN 8.0
                        WHEN 'SOXL' THEN 9.0
                    END
                """
            )

        liq_rows = tsdb.get_recent_selection_feedback_breakdown("US", "liquidity_bucket", days=20, as_of="2026-04-30")
        pullback_rows = tsdb.get_recent_selection_feedback_breakdown("US", "from_high_bucket", days=20, as_of="2026-04-30")
        liq_text = tsdb.format_recent_selection_feedback_breakdown("US", "liquidity_bucket", days=20, as_of="2026-04-30")
        pullback_text = tsdb.format_recent_selection_feedback_breakdown("US", "from_high_bucket", days=20, as_of="2026-04-30")

        self.assertEqual(liq_rows[0]["group_value"], "low")
        self.assertEqual(liq_rows[0]["missed_watch_only_count"], 2)
        self.assertEqual(pullback_rows[0]["group_value"], "deep")
        self.assertEqual(pullback_rows[0]["missed_watch_only_count"], 2)
        self.assertIn("by liquidity: low", liq_text)
        self.assertIn("by pullback: deep", pullback_text)


class AnalystSelectionPromptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "ticker_selection_log.db"
        self._orig_db_path = tsdb.DB_PATH
        tsdb.DB_PATH = str(self._db_path)
        tsdb._price_cache.clear()
        tsdb.init()

    def tearDown(self):
        tsdb.DB_PATH = self._orig_db_path
        tsdb._price_cache.clear()
        try:
            self._tmp.cleanup()
        except Exception:
            pass

    def test_select_tickers_prompt_includes_recent_selection_feedback(self):
        try:
            from minority_report import analysts as analysts_module
        except Exception as exc:
            self.skipTest(f"analysts import unavailable: {exc}")

        tsdb.insert_batch(
            date="2026-04-21",
            market="KR",
            source_type="initial",
            selected=["005930", "000660"],
            candidates=[{"ticker": "005930"}, {"ticker": "000660"}],
            sel_reasons={},
            consensus_mode="NEUTRAL",
            selection_meta={"trade_ready": ["005930"]},
        )
        with sqlite3.connect(tsdb.DB_PATH) as conn:
            conn.execute(
                """
                UPDATE ticker_selection_log
                SET forward_3d=CASE ticker WHEN '005930' THEN 3.0 ELSE -1.0 END,
                    max_runup_3d=CASE ticker WHEN '005930' THEN 6.0 ELSE 5.5 END,
                    max_drawdown_3d=CASE ticker WHEN '005930' THEN -2.0 ELSE -4.0 END
                """
            )

        captured = {}

        def _fake_create(*, model, max_tokens, messages):
            captured["prompt"] = messages[0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"watchlist":["005930"],"trade_ready":["005930"],"reasons":{"005930":"ok"}}')],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        candidates = [
            {
                "ticker": "005930",
                "name": "Samsung",
                "change_rate": 1.2,
                "price": 70000,
                "volume": 100000,
                "market_type": "KOSPI",
                "from_high_pct": -0.8,
                "above_ma60": True,
                "or_state": "formed",
                "atr_pct": 5.8,
                "atr_stage": "normal",
                "entry_priority_bucket": "high",
                "execution_fit_strategy": "gap_pullback",
                "minutes_to_close": 240,
            },
            {
                "ticker": "000660",
                "name": "SK",
                "change_rate": 0.8,
                "price": 180000,
                "volume": 50000,
                "market_type": "KOSDAQ",
                "category": "earnings_momentum",
                "from_high_pct": -5.4,
                "above_ma60": False,
                "or_state": "missing",
                "atr_pct": 8.4,
                "atr_stage": "atr_35",
                "entry_priority_bucket": "low",
                "execution_fit_strategy": "observe",
                "minutes_to_close": 18,
                "entry_blackout_now": True,
            },
        ]
        with patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda *args, **kwargs: None):
            tickers, reasons = analysts_module.select_tickers(
                market="KR",
                digest_prompt="market digest",
                consensus_mode="NEUTRAL",
                candidates=candidates,
                intraday_context="session_phase=early elapsed=18m to_close=240m blackout=no | active_strategies=opening_range_pullback,gap_pullback,momentum | gates=wait=45->20m cutoff=0.18",
                market_change_pct=0.0,
                secondary_change_pct=0.0,
            )

        self.assertEqual(tickers, ["005930"])
        self.assertEqual(reasons["005930"], "ok")
        self.assertIn("recent selection feedback:", captured["prompt"])
        self.assertIn("Use recent selection feedback to calibrate trade_ready aggressiveness.", captured["prompt"])
        self.assertIn("slot guide:", captured["prompt"])
        self.assertIn("session_phase=early", captured["prompt"])
        self.assertIn("active_strategies=opening_range_pullback,gap_pullback,momentum", captured["prompt"])
        self.assertIn("exec=or=formed,atr=5.8%(normal),ep=high,fit=gap_pullback,tclose=240m", captured["prompt"])
        self.assertIn("exec=or=missing,atr=8.4%(atr_35),ep=low,fit=observe,tclose=18m,blackout=now", captured["prompt"])
        self.assertIn("Treat exec= hints (or/atr/ep/fit/tclose/blackout) as real execution constraints.", captured["prompt"])
        self.assertIn("recommended_strategy and max_position_pct must reflect conviction and risk", captured["prompt"])
        self.assertIn("trade_ready 3d:", captured["prompt"])
        self.assertIn("board=KOSPI", captured["prompt"])
        self.assertIn("board=KOSDAQ", captured["prompt"])
        self.assertIn("category=earnings_momentum", captured["prompt"])
        self.assertIn("liq=high", captured["prompt"])
        self.assertIn("liq=mid", captured["prompt"])
        self.assertIn("from_high=-0.8%(near_high)", captured["prompt"])
        self.assertIn("from_high=-5.4%(deep)", captured["prompt"])
        self.assertIn("ma60=above", captured["prompt"])
        self.assertIn("ma60=below", captured["prompt"])
        self.assertEqual(candidates[0]["liquidity_bucket"], "mid")
        self.assertEqual(candidates[0]["from_high_bucket"], "near_high")
        self.assertEqual(candidates[1]["liquidity_bucket"], "high")
        self.assertEqual(candidates[1]["from_high_bucket"], "deep")


class UniverseManagerTests(unittest.TestCase):
    def test_build_universe_from_candidates_applies_diversity_caps(self):
        candidates = [
            {"ticker": "A1", "name": "A1", "price": 100, "volume": 500000, "vol_ratio": 3.0, "change_rate": 10.0, "sector": "semis", "category": "momentum", "market_type": "KOSDAQ", "from_high_pct": -0.1},
            {"ticker": "A2", "name": "A2", "price": 90, "volume": 450000, "vol_ratio": 2.9, "change_rate": 9.0, "sector": "semis", "category": "momentum", "market_type": "KOSDAQ", "from_high_pct": -0.2},
            {"ticker": "A3", "name": "A3", "price": 80, "volume": 440000, "vol_ratio": 2.8, "change_rate": 8.0, "sector": "semis", "category": "momentum", "market_type": "KOSDAQ", "from_high_pct": -0.3},
            {"ticker": "B1", "name": "B1", "price": 70, "volume": 25000000, "vol_ratio": 2.7, "change_rate": 7.0, "sector": "bio", "category": "reversal", "market_type": "KOSDAQ", "from_high_pct": -3.0},
            {"ticker": "C1", "name": "C1", "price": 60, "volume": 20000000, "vol_ratio": 2.6, "change_rate": 6.0, "sector": "autos", "category": "earnings", "market_type": "KOSPI", "from_high_pct": -6.0},
        ]

        snapshot = universe_manager_module.build_universe_from_candidates(
            market="KR",
            target_date="2026-04-22",
            candidates=candidates,
            config=universe_manager_module.UniverseConfig(
                top_n=4,
                category_cap=2,
                sector_cap=2,
                overextended_cap=2,
                low_liquidity_cap=1,
                kosdaq_cap=4,
            ),
            source="test",
            core_tickers=[],
        )

        tickers = snapshot["tickers"]
        self.assertEqual(len(tickers), 4)
        self.assertIn("B1", tickers)
        self.assertIn("C1", tickers)
        self.assertLessEqual(len([ticker for ticker in tickers if ticker in {"A1", "A2", "A3"}]), 2)

    def test_build_universe_preserves_metadata_for_downstream_selection(self):
        snapshot = universe_manager_module.build_universe_from_candidates(
            market="US",
            target_date="2026-04-22",
            candidates=[
                {
                    "ticker": "NVDA",
                    "name": "NVIDIA",
                    "price": 100.0,
                    "volume": 120000000,
                    "vol_ratio": 2.0,
                    "change_rate": 4.0,
                    "sector": "semis",
                    "category": "momentum",
                    "from_high_pct": -1.2,
                    "market_type": "NASDAQ",
                    "above_ma60": True,
                }
            ],
            config=universe_manager_module.UniverseConfig(top_n=1),
            source="test",
            core_tickers=[],
        )

        row = snapshot["candidates"][0]
        self.assertEqual(row["sector"], "semis")
        self.assertEqual(row["category"], "momentum")
        self.assertEqual(row["from_high_bucket"], "near_high")
        self.assertEqual(row["liquidity_bucket"], "high")

    def test_select_tickers_recovery_clears_trade_ready(self):
        try:
            from minority_report import analysts as analysts_module
        except Exception as exc:
            self.skipTest(f"analysts import unavailable: {exc}")

        def _fake_create(*, model, max_tokens, messages):
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"watchlist":["NVDA","AAPL"],"trade_ready":["NVDA"],"reasons":{"NVDA":"ok"}')],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        candidates = [
            {"ticker": "NVDA", "name": "NVIDIA", "price": 100.0, "volume": 1000, "change_rate": 1.0},
            {"ticker": "AAPL", "name": "Apple", "price": 90.0, "volume": 900, "change_rate": 0.8},
            {"ticker": "MSFT", "name": "Microsoft", "price": 80.0, "volume": 800, "change_rate": 0.7},
        ]
        with patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda *args, **kwargs: None):
            tickers, _ = analysts_module.select_tickers(
                market="US",
                digest_prompt="market digest",
                consensus_mode="NEUTRAL",
                candidates=candidates,
                market_change_pct=0.0,
                secondary_change_pct=0.0,
            )

        self.assertEqual(tickers, ["NVDA", "AAPL"])
        self.assertEqual(analysts_module.get_last_selection_meta()["trade_ready"], [])

    def test_select_tickers_includes_lesson_context_in_prompt(self):
        try:
            from minority_report import analysts as analysts_module
        except Exception as exc:
            self.skipTest(f"analysts import unavailable: {exc}")

        captured = {}

        def _fake_create(*, model, max_tokens, messages):
            captured["prompt"] = messages[0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"watchlist":["NVDA"],"trade_ready":[],"reasons":{"NVDA":"ok"}}')],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        candidates = [
            {"ticker": "NVDA", "name": "NVIDIA", "price": 100.0, "volume": 1000, "change_rate": 1.0},
        ]
        with patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda *args, **kwargs: None):
            analysts_module.select_tickers(
                market="US",
                digest_prompt="market digest",
                consensus_mode="NEUTRAL",
                candidates=candidates,
                lesson_context="recent lesson candidates:\n- continuation weak (n=5, severity=high)",
                market_change_pct=0.0,
                secondary_change_pct=0.0,
            )

        self.assertIn("recent lesson candidates:", captured["prompt"])
        self.assertIn("continuation weak", captured["prompt"])

    def test_select_tickers_includes_earnings_window_hint_in_prompt(self):
        try:
            from minority_report import analysts as analysts_module
        except Exception as exc:
            self.skipTest(f"analysts import unavailable: {exc}")

        captured = {}

        def _fake_create(*, model, max_tokens, messages):
            captured["prompt"] = messages[0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"watchlist":["NVDA"],"trade_ready":[],"reasons":{"NVDA":"watch earnings drift"}}')],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        candidates = [
            {
                "ticker": "NVDA",
                "name": "NVIDIA",
                "price": 100.0,
                "volume": 1_000_000,
                "change_rate": 2.0,
                "earnings_window": "post_1",
                "prompt_applied": False,
            }
        ]

        with patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda *args, **kwargs: None):
            analysts_module.select_tickers(
                market="US",
                digest_prompt="market digest",
                consensus_mode="NEUTRAL",
                candidates=candidates,
                market_change_pct=0.0,
                secondary_change_pct=0.0,
            )

        self.assertIn("earn=post_1", captured["prompt"])

    def test_call_analyst_includes_lesson_context_in_prompt(self):
        try:
            from minority_report import analysts as analysts_module
        except Exception as exc:
            self.skipTest(f"analysts import unavailable: {exc}")

        captured = {}

        def _fake_create(*, model, max_tokens, messages):
            captured["prompt"] = messages[0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"stance":"NEUTRAL","confidence":0.55,"key_reason":"ok","full_reasoning":"ok","top_risks":["a"],"suggested_strategy":"관망","suggested_size_pct":30}')],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        with patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda *args, **kwargs: None):
            analysts_module.call_analyst(
                "bull",
                "digest prompt",
                "brain summary",
                "{}",
                lesson_context="recent lesson candidates:\n- trade_ready conversion weak",
                market="US",
            )

        self.assertIn("recent lesson candidates", captured["prompt"])
        self.assertIn("trade_ready conversion weak", captured["prompt"])

    def test_select_tickers_partial_recovery_reasks_with_light_prompt(self):
        try:
            from minority_report import analysts as analysts_module
        except Exception as exc:
            self.skipTest(f"analysts import unavailable: {exc}")

        prompts = []
        responses = [
            '{"watchlist":["NVDA","AAPL","MSFT"],"trade_ready":["NVDA","AAPL"],"reasons":{"NVDA":"strong","AAPL":"watch"}',
            '{"watchlist":["NVDA","AAPL"],"trade_ready":["NVDA"],"reasons":{"NVDA":"strong","AAPL":"watch"}}',
        ]

        def _fake_create(*, model, max_tokens, messages):
            prompts.append(messages[0]["content"])
            text = responses[len(prompts) - 1]
            return SimpleNamespace(
                content=[SimpleNamespace(text=text)],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        candidates = [
            {"ticker": "NVDA", "name": "NVIDIA", "price": 100.0, "volume": 1000, "change_rate": 1.0},
            {"ticker": "AAPL", "name": "Apple", "price": 90.0, "volume": 900, "change_rate": 0.8},
            {"ticker": "MSFT", "name": "Microsoft", "price": 80.0, "volume": 800, "change_rate": 0.7},
            {"ticker": "TSLA", "name": "Tesla", "price": 70.0, "volume": 700, "change_rate": 0.6},
        ]
        with patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda *args, **kwargs: None):
            tickers, reasons = analysts_module.select_tickers(
                market="US",
                digest_prompt="market digest",
                consensus_mode="NEUTRAL",
                candidates=candidates,
                market_change_pct=0.0,
                secondary_change_pct=0.0,
            )

        self.assertEqual(len(prompts), 2)
        self.assertIn("다시 묻습니다", prompts[1])
        self.assertEqual(tickers, ["NVDA", "AAPL"])
        self.assertEqual(reasons["NVDA"], "strong")
        self.assertEqual(analysts_module.get_last_selection_meta()["trade_ready"], ["NVDA"])

    def test_select_tickers_total_failure_uses_safe_watch_fallback(self):
        try:
            from minority_report import analysts as analysts_module
        except Exception as exc:
            self.skipTest(f"analysts import unavailable: {exc}")

        def _fake_create(*, model, max_tokens, messages):
            return SimpleNamespace(
                content=[SimpleNamespace(text="selection unavailable")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        candidates = [
            {"ticker": f"T{i:02d}", "name": f"Ticker{i}", "price": 10.0 + i, "volume": 1000 + i, "change_rate": 1.0}
            for i in range(15)
        ]
        with patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda *args, **kwargs: None):
            tickers, _ = analysts_module.select_tickers(
                market="US",
                digest_prompt="market digest",
                consensus_mode="NEUTRAL",
                candidates=candidates,
                market_change_pct=0.0,
                secondary_change_pct=0.0,
            )

        self.assertEqual(tickers, [f"T{i:02d}" for i in range(12)])
        self.assertEqual(analysts_module.get_last_selection_meta()["trade_ready"], [])

    def test_curate_selection_candidates_spreads_overextended_names(self):
        try:
            from minority_report import analysts as analysts_module
        except Exception as exc:
            self.skipTest(f"analysts import unavailable: {exc}")

        candidates = [
            {"ticker": "A", "category": "leader", "sector": "semis", "liquidity_bucket": "high", "from_high_bucket": "at_high", "market_type": "KOSDAQ"},
            {"ticker": "B", "category": "leader", "sector": "semis", "liquidity_bucket": "high", "from_high_bucket": "near_high", "market_type": "KOSDAQ"},
            {"ticker": "C", "category": "leader", "sector": "semis", "liquidity_bucket": "high", "from_high_bucket": "at_high", "market_type": "KOSDAQ"},
            {"ticker": "D", "category": "earnings", "sector": "autos", "liquidity_bucket": "mid", "from_high_bucket": "pullback", "market_type": "KOSPI"},
            {"ticker": "E", "category": "turnaround", "sector": "bio", "liquidity_bucket": "low", "from_high_bucket": "deep", "market_type": "KOSPI"},
        ]

        curated = analysts_module._curate_selection_candidates(candidates, "KR", 4)

        self.assertEqual([item["ticker"] for item in curated], ["A", "B", "D", "E"])


class BrainSummarySelectionFeedbackTests(unittest.TestCase):
    def test_generate_prompt_summary_includes_recent_selection_feedback(self):
        try:
            from claude_memory import brain as brain_module
        except Exception as exc:
            self.skipTest(f"brain import unavailable: {exc}")

        fake_brain = {
            "meta": {},
            "markets": {
                "KR": {
                    "trained_days": 5,
                    "analyst_performance": {
                        "bull": {"rate": 0.6, "recent_7d": {"rate": 0.5}, "trend": "stable"},
                        "bear": {"rate": 0.4, "recent_7d": {"rate": 0.5}, "trend": "stable"},
                        "neutral": {"rate": 0.5, "recent_7d": {"rate": 0.5}, "trend": "stable"},
                    },
                    "mode_performance": {
                        "NEUTRAL": {"count": 2, "avg_pnl": 0.2, "win_rate": 0.5},
                        "MILD_BULL": {"count": 1, "avg_pnl": 0.4, "win_rate": 1.0},
                    },
                    "current_beliefs": {
                        "market_regime": "range",
                        "bull_reliability": "mid",
                        "bear_reliability": "mid",
                        "best_strategy": "momentum",
                        "avoid": [],
                        "emphasize": [],
                        "learned_lessons": ["wait for confirmation"],
                    },
                    "issue_patterns": [
                        {
                            "id": "P1",
                            "type": "selection",
                            "count": 2,
                            "bull_accuracy": 0.5,
                            "avg_pnl_when_followed": 1.2,
                            "insight": "watch weak breakouts",
                            "description": "sample",
                        }
                    ],
                    "recent_days": [
                        {"date": "2026-04-20", "mode": "NEUTRAL", "pnl_pct": 0.8, "win": True}
                    ],
                    "tuning_patterns": {
                        "open_drive": {
                            "count": 1,
                            "rate": 1.0,
                            "correct": 1,
                            "insight": "works in calm tape",
                            "last_seen": "2026-04-20",
                        }
                    },
                    "execution_patterns": {},
                    "execution_lessons": [],
                }
            },
        }

        with patch.object(brain_module, "load", return_value=fake_brain), \
             patch.object(brain_module, "get_recent_selection_feedback_text", return_value="- selected=12 trade_ready=4"):
            summary = brain_module.generate_prompt_summary("KR")

        self.assertIn("Recent Selection Feedback", summary)
        self.assertIn("- selected=12 trade_ready=4", summary)


class PostmortemSelectionFeedbackTests(unittest.TestCase):
    def test_postmortem_prompt_and_daily_record_include_selection_feedback(self):
        try:
            from minority_report import postmortem as postmortem_module
        except Exception as exc:
            self.skipTest(f"postmortem import unavailable: {exc}")

        captured = {}

        def _fake_create(*, model, max_tokens, messages):
            captured["prompt"] = messages[0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps({
                    "bull_result": "HIT",
                    "bear_result": "MISS",
                    "neutral_result": "PARTIAL",
                    "bull_why": "ok",
                    "bear_why": "ok",
                    "neutral_why": "ok",
                    "best_trade": None,
                    "worst_trade": None,
                    "worst_trade_reason": "",
                    "key_lesson": "selection discipline",
                    "issue_type": "selection",
                    "issue_desc": "watch_only miss",
                    "pattern_id": None,
                    "brain_updates": {
                        "bull_reliability_change": "stable",
                        "bear_reliability_change": "stable",
                        "new_lesson": None,
                        "market_regime": "unknown",
                    },
                    "correction_guide": {
                        "bull_adjustments": [],
                        "bear_adjustments": [],
                        "tuning_rules": [],
                        "today_notes": "note",
                    },
                }))],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        def _capture_daily_record(market, record):
            captured["daily_record"] = (market, record)

        judgment = {
            "judgments": {
                "bull": {"stance": "MILD_BULL", "key_reason": "bull"},
                "bear": {"stance": "NEUTRAL", "key_reason": "bear"},
                "neutral": {"stance": "NEUTRAL", "key_reason": "neutral"},
            },
            "consensus": {"mode": "NEUTRAL", "size": 10},
        }
        actual_result = {"market_change": 0.8, "pnl_pct": 0.0, "win": False}

        with patch.object(postmortem_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(postmortem_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(postmortem_module, "save_raw_call", lambda *args, **kwargs: None), \
             patch.object(postmortem_module.BrainDB, "generate_prompt_summary", return_value="brain summary"), \
             patch.object(postmortem_module.BrainDB, "get_recent_selection_feedback_text", return_value="- selected=10 watch_only=6"), \
             patch.object(postmortem_module.BrainDB, "load", return_value={"markets": {"KR": {"recent_days": []}}}), \
             patch.object(postmortem_module.BrainDB, "update_analyst", lambda *args, **kwargs: None), \
             patch.object(postmortem_module.BrainDB, "update_mode_performance", lambda *args, **kwargs: None), \
             patch.object(postmortem_module.BrainDB, "update_beliefs", lambda *args, **kwargs: None), \
             patch.object(postmortem_module.BrainDB, "update_issue_pattern", lambda *args, **kwargs: None), \
             patch.object(postmortem_module.BrainDB, "add_daily_record", side_effect=_capture_daily_record), \
             patch.object(postmortem_module.BrainDB, "update_strategy_performance", lambda *args, **kwargs: None), \
             patch.object(postmortem_module.BrainDB, "update_debate_outcome", lambda *args, **kwargs: None), \
             patch.object(postmortem_module.BrainDB, "update_correction_guide", lambda *args, **kwargs: None):
            pm = postmortem_module.run(
                market="KR",
                date="2026-04-21",
                today_judgment=judgment,
                actual_result=actual_result,
                digest_prompt="market digest",
                trade_log=[],
                decision_event_log=[],
            )

        self.assertEqual(pm["bull_result"], "HIT")
        self.assertIn("[Recent selection feedback]", captured["prompt"])
        self.assertIn("- selected=10 watch_only=6", captured["prompt"])
        self.assertIn("brain summary", captured["prompt"])
        self.assertEqual(captured["daily_record"][0], "KR")
        self.assertEqual(captured["daily_record"][1]["selection_feedback"], "- selected=10 watch_only=6")


if __name__ == "__main__":
    unittest.main()
