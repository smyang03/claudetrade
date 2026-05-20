import json
import os
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
from claude_memory import brain as brain_module
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

    def test_screen_market_kr_refills_slots_after_product_filter(self):
        kospi_rows = [
            {"ticker": "114800", "name": "KODEX 인버스", "price": 1000, "volume": 9_000_000, "change_rate": 9, "vol_ratio": 8, "market_type": "KOSPI"},
            {"ticker": "252670", "name": "KODEX 200선물인버스2X", "price": 1000, "volume": 8_000_000, "change_rate": 8, "vol_ratio": 7, "market_type": "KOSPI"},
            {"ticker": "005930", "name": "삼성전자", "price": 70000, "volume": 7_000_000, "change_rate": 2, "vol_ratio": 3, "market_type": "KOSPI"},
            {"ticker": "000660", "name": "SK하이닉스", "price": 180000, "volume": 6_000_000, "change_rate": 3, "vol_ratio": 4, "market_type": "KOSPI"},
            {"ticker": "035420", "name": "NAVER", "price": 200000, "volume": 5_000_000, "change_rate": 2, "vol_ratio": 3, "market_type": "KOSPI"},
            {"ticker": "005380", "name": "현대차", "price": 250000, "volume": 4_000_000, "change_rate": 2, "vol_ratio": 2, "market_type": "KOSPI"},
        ]
        kosdaq_rows = [
            {"ticker": "091990", "name": "셀트리온헬스케어", "price": 65000, "volume": 3_000_000, "change_rate": 4, "vol_ratio": 3, "market_type": "KOSDAQ"},
            {"ticker": "247540", "name": "에코프로비엠", "price": 120000, "volume": 2_000_000, "change_rate": 5, "vol_ratio": 4, "market_type": "KOSDAQ"},
        ]
        requested_limits = []

        def fake_volume_rank(token, vol_cnt, top_n, market_div="J", input_iscd="0000"):
            requested_limits.append(top_n)
            rows = kosdaq_rows if input_iscd == "1001" else kospi_rows
            return rows[:top_n]

        with patch.object(kis_api_module, "_is_kr_premarket_window", return_value=False), \
             patch.object(kis_api_module, "_kis_volume_rank", side_effect=fake_volume_rank), \
             patch.object(kis_api_module, "save_kr_screen_cache"), \
             patch.object(kis_api_module, "_save_kr_screen_audit"):
            result = kis_api_module.screen_market_kr("token", top_n=4, mode="NEUTRAL")

        tickers = [row["ticker"] for row in result]
        self.assertEqual(len(result), 4)
        self.assertNotIn("114800", tickers)
        self.assertNotIn("252670", tickers)
        self.assertTrue(all(limit > 4 for limit in requested_limits))

    def test_screen_market_kr_warns_when_kosdaq_raw_is_zero(self):
        kospi_rows = [
            {"ticker": "005930", "name": "삼성전자", "price": 70000, "volume": 7_000_000, "change_rate": 2, "vol_ratio": 3, "market_type": "KOSPI"},
            {"ticker": "000660", "name": "SK하이닉스", "price": 180000, "volume": 6_000_000, "change_rate": 3, "vol_ratio": 4, "market_type": "KOSPI"},
            {"ticker": "035420", "name": "NAVER", "price": 200000, "volume": 5_000_000, "change_rate": 2, "vol_ratio": 3, "market_type": "KOSPI"},
            {"ticker": "005380", "name": "현대차", "price": 250000, "volume": 4_000_000, "change_rate": 2, "vol_ratio": 2, "market_type": "KOSPI"},
        ]

        def fake_volume_rank(token, vol_cnt, top_n, market_div="J", input_iscd="0000"):
            return [] if input_iscd == "1001" else kospi_rows[:top_n]

        with patch.object(kis_api_module, "_is_kr_premarket_window", return_value=False), \
             patch.object(kis_api_module, "_kis_volume_rank", side_effect=fake_volume_rank), \
             patch.object(kis_api_module, "save_kr_screen_cache"), \
             patch.object(kis_api_module, "_save_kr_screen_audit"), \
             self.assertLogs("trading_system", level="WARNING") as logs:
            kis_api_module.screen_market_kr("token", top_n=4, mode="NEUTRAL")

        self.assertTrue(any("KOSDAQ raw=0" in line for line in logs.output))
        self.assertTrue(any("input_iscd=1001" in line for line in logs.output))

    def test_kis_volume_rank_uses_input_iscd_for_kosdaq_labeling(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "output": [
                        {
                            "mksc_shrn_iscd": "091990",
                            "hts_kor_isnm": "셀트리온헬스케어",
                            "stck_prpr": "65000",
                            "prdy_ctrt": "4.5",
                            "acml_vol": "3000000",
                            "vol_tnrt": "3.2",
                        }
                    ]
                }

        with patch.object(kis_api_module, "_headers", return_value={}), \
             patch.object(kis_api_module, "_kis_get", return_value=FakeResponse()) as get_mock:
            rows = kis_api_module._kis_volume_rank(
                "token", vol_cnt="100000", top_n=5, market_div="J", input_iscd="1001"
            )

        params = get_mock.call_args.kwargs["params"]
        self.assertEqual(params["FID_COND_MRKT_DIV_CODE"], "J")
        self.assertEqual(params["FID_INPUT_ISCD"], "1001")
        self.assertEqual(rows[0]["market_type"], "KOSDAQ")

    def test_screen_market_kr_calls_kospi_and_kosdaq_with_input_iscd_split(self):
        calls = []

        def fake_volume_rank(token, vol_cnt, top_n, market_div="J", input_iscd="0000"):
            calls.append((market_div, input_iscd))
            market_type = "KOSDAQ" if input_iscd == "1001" else "KOSPI"
            return [
                {
                    "ticker": "091990" if input_iscd == "1001" else "005930",
                    "name": "셀트리온헬스케어" if input_iscd == "1001" else "삼성전자",
                    "price": 65000,
                    "volume": 3_000_000,
                    "change_rate": 4,
                    "vol_ratio": 3,
                    "market_type": market_type,
                }
            ]

        with patch.object(kis_api_module, "_is_kr_premarket_window", return_value=False), \
             patch.object(kis_api_module, "_kis_volume_rank", side_effect=fake_volume_rank), \
             patch.object(kis_api_module, "save_kr_screen_cache"), \
             patch.object(kis_api_module, "_save_kr_screen_audit"):
            result = kis_api_module.screen_market_kr("token", top_n=2, mode="NEUTRAL")

        self.assertIn(("J", "0001"), calls)
        self.assertIn(("J", "1001"), calls)
        self.assertNotIn(("Q", "0000"), calls)
        self.assertTrue(any(row.get("market_type") == "KOSDAQ" for row in result))

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

    def test_pead_prompt_gate_requires_shadow_days_and_manual_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def runtime_path(*parts, make_parents=True):
                path = root.joinpath(*parts)
                if make_parents:
                    path.parent.mkdir(parents=True, exist_ok=True)
                return path

            state = {
                "markets": {
                    "US": {
                        "market": "US",
                        "shadow_start_date": "2026-04-20",
                        "trading_days_observed": 5,
                        "required_trading_days": 5,
                        "prompt_surprise_enabled": True,
                        "manual_review": {
                            "tier_null_rate_checked": True,
                            "surprise_sample_10_checked": True,
                            "prompt_leak_zero_checked": False,
                        },
                    }
                }
            }

            with patch.object(digest_builder_module, "get_runtime_path", side_effect=runtime_path), \
                 patch.object(digest_builder_module, "_PEAD_PROMPT_INCLUDE_SURPRISE", True):
                digest_builder_module._save_pead_shadow_state(state)
                self.assertFalse(digest_builder_module._pead_surprise_prompt_allowed("US", "2026-04-24"))

                state["markets"]["US"]["manual_review"]["prompt_leak_zero_checked"] = True
                digest_builder_module._save_pead_shadow_state(state)
                self.assertTrue(digest_builder_module._pead_surprise_prompt_allowed("US", "2026-04-24"))

                state["markets"]["US"]["trading_days_observed"] = 4
                digest_builder_module._save_pead_shadow_state(state)
                self.assertFalse(digest_builder_module._pead_surprise_prompt_allowed("US", "2026-04-23"))

    def test_persist_pead_shadow_rows_writes_state_summary(self):
        rows = [
            {
                "ticker": "NVDA",
                "reported_eps": 1.2,
                "eps_estimate": 1.0,
                "surprise_sign": "beat",
                "confidence_tier": "high",
                "prompt_applied": False,
            },
            {
                "ticker": "GS",
                "reported_eps": None,
                "eps_estimate": None,
                "surprise_sign": "unknown",
                "confidence_tier": "medium",
                "prompt_applied": False,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def runtime_path(*parts, make_parents=True):
                path = root.joinpath(*parts)
                if make_parents:
                    path.parent.mkdir(parents=True, exist_ok=True)
                return path

            with patch.object(digest_builder_module, "get_runtime_path", side_effect=runtime_path):
                digest_builder_module._persist_pead_shadow_rows("US", "2026-04-24", rows)

            state = json.loads((root / "state" / "pead_shadow_state.json").read_text(encoding="utf-8"))
            market_state = state["markets"]["US"]
            self.assertEqual(market_state["trading_days_observed"], 1)
            self.assertFalse(market_state["manual_review_passed"])
            self.assertEqual(market_state["last_shadow_summary"]["by_tier"]["high"]["surprise_known"], 1)
            self.assertEqual(market_state["last_shadow_summary"]["by_tier"]["medium"]["reported_eps_null_rate"], 1.0)


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
        # -2% hard cap fires before the -3% stop_loss when price drops to -4%;
        # loss_cap is the expected exit reason with the cap in effect.
        self.assertIn(exits[0]["reason"], ("stop_loss", "loss_cap"))


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

    def test_cautious_judgment_eval_uses_bull_direction_consistently(self):
        judgments = {
            "bull": {"stance": "CAUTIOUS"},
            "bear": {"stance": "CAUTIOUS"},
            "neutral": {"stance": "CAUTIOUS"},
        }
        consensus = {"mode": "CAUTIOUS"}

        result = consensus_module.build_judgment_eval(judgments, consensus, 0.2)

        self.assertEqual(result["consensus_dir"], "UP")
        self.assertEqual(result["analyst_dirs"], {"bull": "UP", "bear": "UP", "neutral": "UP"})
        self.assertEqual(result["unanimous_direction"], "bull")
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
        bot._unanimous_override_events = []
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
        bot.tokens = {"KR": "test-token", "US": "test-token"}
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

    def test_micro_probe_adjusts_sub_minimum_order_when_safety_checks_pass(self):
        bot = self._make_bot()
        bot.enable_micro_probe = True
        bot.micro_probe_paper_only = True
        bot.micro_probe_allowed_markets = {"KR", "US"}
        bot.micro_probe_allowed_modes = {"MILD_BULL"}
        bot.micro_probe_min_entry_priority = 0.45
        bot.micro_probe_max_oversize_ratio = 2.0
        bot.micro_probe_max_order_krw = 50_000
        bot.micro_probe_max_daily_trades = 2
        bot.micro_probe_max_open_positions = 2
        bot.pending_orders = []
        bot.decision_event_log = []

        result = bot._micro_probe_adjustment(
            market="KR",
            ticker="123456",
            mode="MILD_BULL",
            source_strategy="momentum",
            entry_priority_score=0.62,
            qty=1,
            risk_price_krw=8_000,
            original_order_cost_krw=8_000,
            order_budget_krw=20_000,
            min_effective_order_krw=30_000,
            available_budget_krw=100_000,
            cash_krw=100_000,
        )

        self.assertTrue(result["allowed"])
        self.assertEqual(result["adjusted_qty"], 4)
        self.assertEqual(result["adjusted_order_cost_krw"], 32_000)
        self.assertAlmostEqual(result["oversize_ratio"], 1.6)
        self.assertEqual(result["source_strategy"], "momentum")

    def test_micro_probe_blocks_live_by_default(self):
        bot = self._make_bot()
        bot.is_paper = False
        bot.enable_micro_probe = True
        bot.micro_probe_paper_only = True
        bot.micro_probe_allowed_markets = {"KR"}
        bot.micro_probe_allowed_modes = {"MILD_BULL"}
        bot.micro_probe_min_entry_priority = 0.45
        bot.micro_probe_max_oversize_ratio = 2.0
        bot.micro_probe_max_order_krw = 50_000
        bot.micro_probe_max_daily_trades = 2
        bot.micro_probe_max_open_positions = 2
        bot.pending_orders = []
        bot.decision_event_log = []

        result = bot._micro_probe_adjustment(
            market="KR",
            ticker="123456",
            mode="MILD_BULL",
            source_strategy="momentum",
            entry_priority_score=0.80,
            qty=1,
            risk_price_krw=8_000,
            original_order_cost_krw=8_000,
            order_budget_krw=20_000,
            min_effective_order_krw=30_000,
            available_budget_krw=100_000,
            cash_krw=100_000,
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "live_disabled")

    def test_micro_probe_blocks_excessive_adjusted_order(self):
        bot = self._make_bot()
        bot.enable_micro_probe = True
        bot.micro_probe_paper_only = True
        bot.micro_probe_allowed_markets = {"KR"}
        bot.micro_probe_allowed_modes = {"MILD_BULL"}
        bot.micro_probe_min_entry_priority = 0.45
        bot.micro_probe_max_oversize_ratio = 2.0
        bot.micro_probe_max_order_krw = 50_000
        bot.micro_probe_max_daily_trades = 2
        bot.micro_probe_max_open_positions = 2
        bot.pending_orders = []
        bot.decision_event_log = []

        result = bot._micro_probe_adjustment(
            market="KR",
            ticker="123456",
            mode="MILD_BULL",
            source_strategy="momentum",
            entry_priority_score=0.80,
            qty=0,
            risk_price_krw=120_000,
            original_order_cost_krw=0,
            order_budget_krw=20_000,
            min_effective_order_krw=30_000,
            available_budget_krw=200_000,
            cash_krw=200_000,
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "probe_order_too_large")

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

        # _get_market_change_pct/secondary는 실시간 kis_api 호출을 우선하므로
        # 테스트 시나리오값을 직접 주입한다.
        with patch.object(bot, "_get_market_change_pct", return_value=-3.1), \
             patch.object(bot, "_get_secondary_change_pct", return_value=-4.0):
            blocked = bot._risk_off_mr_exception(
                "US",
                "CAUTIOUS_BEAR",
                "mean_reversion",
                {"vol_ratio": 1.1},
            )
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["reason"], "panic_primary")

        bot.risk = SimpleNamespace(positions=[{"ticker": "AAPL"}])
        with patch.object(bot, "_get_market_change_pct", return_value=-0.5), \
             patch.object(bot, "_get_secondary_change_pct", return_value=-0.8):
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
        self.assertEqual(bot.today_judgment["unanimous_override_count"], 1)
        self.assertEqual(bot.today_judgment["unanimous_override_events"][0]["source"], "test")
        self.assertEqual(bot.today_judgment["unanimous_override_events"][0]["post_mode"], "CAUTIOUS_BEAR")

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
        self.assertEqual(
            normalized["_runtime_filtered_trade_ready"],
            {"C": "slot_cap:momentum"},
        )

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

        with patch.dict("os.environ", {"KR_TRAINER_REPLACEMENT_DELTA_GATE_ENABLED": "false"}):
            picked = bot._pick_partial_replace_in(
                "KR",
                ["005930", "000660"],
                ["111111", "222222", "333333"],
                candidate_meta,
                candidate_map,
                2,
            )

        self.assertEqual(picked["accepted"], ["111111", "222222"])

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
                        ("live", "2026-04-21", "KR", "A", 1, 1, None, None, None),
                        ("live", "2026-04-21", "KR", "A", 1, 1, None, 1.2, 2.0),
                        ("live", "2026-04-21", "KR", "A", 1, 1, None, 1.2, 2.0),
                        ("live", "2026-04-21", "KR", "B", 1, 0, None, -0.4, 1.0),
                        ("live", "2026-04-22", "KR", "C", 1, 1, "momentum_atr_too_high", 0.6, 5.5),
                        ("live", "2026-04-22", "KR", "C", 1, 1, "momentum_atr_too_high", None, 5.5),
                        ("live", "2026-04-22", "KR", "D", 0, 0, None, 1.0, 6.2),
                        ("live", "2026-04-22", "KR", "D", 0, 0, None, 1.0, 6.2),
                        ("live", "2026-04-22", "KR", "E", 0, 0, None, -0.2, 1.1),
                        ("live", "2026-04-22", "KR", "F", 0, 0, None, None, 9.0),
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
                "unanimous_override_events": [
                    {
                        "source": "test",
                        "unanimous_direction": "bear",
                        "pre_unanimous_override_mode": "MILD_BULL",
                        "post_mode": "CAUTIOUS_BEAR",
                    }
                ],
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
        self.assertEqual(metrics["unanimous_override_count"]["value"], 1)
        self.assertEqual(metrics["trade_ready_signal_conversion"]["value"], 66.7)
        self.assertEqual(metrics["watch_only_missed_runup_ratio"]["value"], 50.0)
        self.assertEqual(snapshot["samples"]["trade_ready_rows"], 3)
        self.assertEqual(snapshot["samples"]["watch_only_forward_n"], 2)
        self.assertEqual(snapshot["samples"]["atr_blocked_rows"], 1)
        self.assertAlmostEqual(metrics["trade_ready_forward_3d_average"]["value"], 0.467, places=3)
        self.assertEqual(metrics["atr_blocked_missed_runup"]["value"], 5.5)
        self.assertEqual(metrics["entry_blackout_ratio"]["value"], 50.0)
        self.assertEqual(metrics["watch_only_blocked_ratio"]["value"], 50.0)
        self.assertEqual(metrics["continuation_average_pnl"]["value"], -4.2)
        self.assertTrue(snapshot["triggers"]["large_analyst_gap"])
        self.assertTrue(snapshot["triggers"]["unanimous_mismatch"])
        self.assertTrue(snapshot["triggers"]["unanimous_override_seen"])
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
        bot.is_paper = False
        bot._active_session_date = {"KR": date(2026, 4, 23), "US": date(2026, 4, 23)}
        bot.usd_krw_rate = 1500.0
        bot.risk = SimpleNamespace(positions=[], trade_log=[], all_trade_log=[], cash=0.0)
        bot.pending_orders = []
        bot._funnel = {"KR": {"filled": 0}, "US": {"filled": 0}}
        bot._execution_flags = {"KR": set(), "US": set()}
        bot._broker_state = {"KR": {}, "US": {}}
        bot._session_closed_tickers = {"KR": set(), "US": set()}
        bot._sell_fail_at = {}
        bot._exit_process_lock = __import__("threading").Lock()
        bot.current_market = None
        bot.enable_trailing_stop = True
        bot.token = "test-token"
        bot.tokens = {"KR": "test-token", "US": "test-token"}
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

    def test_make_position_from_broker_preserves_micro_probe_metadata(self):
        bot = self._make_bot()

        pos = bot._make_position_from_broker(
            {
                "market": "KR",
                "ticker": "123456",
                "qty": 4,
                "raw_price": 8_000,
                "order_no": "P1",
                "strategy": "MICRO_PROBE",
                "source_strategy": "momentum",
                "micro_probe": True,
                "micro_probe_reason": "order_size_too_small_probe",
                "original_order_cost_krw": 8_000,
                "adjusted_order_cost_krw": 32_000,
                "oversize_ratio": 1.6,
                "tp_pct": 0.03,
                "sl_pct": 0.02,
            },
            {"ticker": "123456", "qty": 4, "avg_price": 8_000, "eval_price": 8_100},
        )

        self.assertEqual(pos["strategy"], "MICRO_PROBE")
        self.assertEqual(pos["source_strategy"], "momentum")
        self.assertTrue(pos["micro_probe"])
        self.assertEqual(pos["micro_probe_reason"], "order_size_too_small_probe")
        self.assertEqual(pos["original_order_cost_krw"], 8_000)
        self.assertEqual(pos["adjusted_order_cost_krw"], 32_000)
        self.assertEqual(pos["oversize_ratio"], 1.6)

    def test_verify_live_positions_protects_position_when_broker_returns_empty(self):
        # broker가 빈 stocks를 반환해도 내부 포지션이 있으면 broker truth degraded로 간주,
        # 포지션을 제거하지 않고 보호 상태로 유지한다.
        bot = self._make_bot()
        saved = [{"ticker": "OKLO", "qty": 2, "entry": 100.0, "price_source": "order_fill"}]

        with patch.object(
            trading_bot_module,
            "get_balance",
            side_effect=[
                {"cash": 1000000.0, "total_eval": 0.0, "stocks": []},
                {"cash": 100.0, "total_eval": 0.0, "stocks": []},
            ],
        ):
            verified = bot._verify_live_positions(saved)

        self.assertEqual(len(verified), 1)
        self.assertTrue(verified[0].get("management_protected"))

    def test_verify_live_positions_keeps_legacy_when_pending_exists(self):
        bot = self._make_bot()
        bot.pending_orders = [{"market": "US", "ticker": "OKLO", "qty": 2, "order_no": "P1"}]
        saved = [{"ticker": "OKLO", "qty": 2, "entry": 100.0, "price_source": "order_fill"}]

        with patch.object(
            trading_bot_module,
            "get_balance",
            side_effect=[
                {"cash": 1000000.0, "total_eval": 0.0, "stocks": []},
                {"cash": 100.0, "total_eval": 0.0, "stocks": []},
            ],
        ):
            verified = bot._verify_live_positions(saved)

        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]["position_integrity"], "protected")
        self.assertTrue(verified[0]["management_protected"])

    def test_sync_runtime_with_broker_protects_position_when_broker_returns_empty(self):
        # broker가 빈 stocks를 반환해도 내부 포지션이 있으면 broker truth degraded로 간주,
        # 포지션을 제거하지 않고 보호 상태로 유지한다.
        bot = self._make_bot()
        bot.risk.positions = [
            {
                "ticker": "OKLO",
                "qty": 2,
                "entry": 100.0,
                "current_price": 95.0,
                "display_avg_price": 10.0,
                "display_current_price": 9.5,
                "display_currency": "USD",
                "price_source": "order_fill",
                "position_origin": "saved_restore",
                "position_integrity": "trusted",
                "management_protected": False,
            }
        ]
        bot.risk.cash = 0.0
        bot._save_positions = Mock()

        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            with patch.object(
                trading_bot_module,
                "get_balance",
                side_effect=[
                    {"cash": 500000.0, "total_eval": 0.0, "stocks": []},
                    {"cash": 100.0, "total_eval": 0.0, "stocks": []},
                ],
            ), patch.object(
                trading_bot_module,
                "get_runtime_path",
                side_effect=lambda *parts: Path(tmpdir).joinpath(*parts),
            ):
                bot._sync_runtime_with_broker()

        # degraded 상태에서 포지션이 제거되지 않고 보존되는지 확인
        self.assertEqual(len(bot.risk.positions), 1)
        self.assertEqual(bot.risk.positions[0]["ticker"], "OKLO")

    def test_process_exit_candidates_deduplicates_same_ticker(self):
        bot = self._make_bot()
        bot.risk = SimpleNamespace(
            get_exit_candidates=lambda: [
                {"ticker": "NVTS", "reason": "max_hold", "exit_price": 10.0},
                {"ticker": "NVTS", "reason": "trail_stop", "exit_price": 10.0},
            ]
        )

        with patch.object(trading_bot_module, "is_trading_halted", return_value=False), \
             patch.object(bot, "_is_order_allowed_now", return_value=True):
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
        affordability = next(item for item in candidates if item["id"] == "affordability_fail_cluster")
        self.assertTrue(affordability["ops_flag"])
        self.assertFalse(affordability["claude_actionable"])
        self.assertEqual(affordability["action_hint"], "")
        self.assertEqual(affordability["min_sample"], 2)
        watch = next(item for item in candidates if item["id"] == "watch_only_missed_runup_review")
        trade_ready = next(item for item in candidates if item["id"] == "trade_ready_conversion_review")
        self.assertTrue(trade_ready["claude_actionable"])
        self.assertTrue(watch["quality_conflict_suppressed"])
        self.assertFalse(watch["claude_actionable"])

    def test_watch_only_lesson_candidate_gets_action_hint_without_conflict(self):
        bot = self._make_bot()
        snapshot = {
            "window_days": 14,
            "metrics": {
                "trade_ready_signal_conversion": {"value": 25.0, "sample": 24, "breached": False},
                "watch_only_missed_runup_ratio": {"value": 66.5, "sample": 224, "breached": True},
            },
        }

        candidates = bot._build_lesson_candidates("KR", snapshot)
        watch = next(item for item in candidates if item["id"] == "watch_only_missed_runup_review")

        self.assertTrue(watch["claude_actionable"])
        self.assertFalse(watch["ops_flag"])
        self.assertIn("66.5%", watch["action_hint"])
        self.assertIn("+5% 이상 runup", watch["action_hint"])
        self.assertEqual(watch["quality_version"], "active_lesson_quality.v1")


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

        self.assertEqual(result["momentum_wait_adjust_min"], -10)
        self.assertEqual(result["entry_priority_cutoff_adjust"], 0.05)
        self.assertEqual(result["kr_momentum_atr_cap_adjust"], -0.01)
        self.assertEqual(result["kr_momentum_atr_cap_high_adjust"], 0.02)

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

    def test_resolve_ticker_select_reason_uses_runtime_filtered_slot_reason(self):
        rec = {
            "selection_meta": {
                "trade_ready": ["209640", "332570"],
                "_runtime_filtered_trade_ready": {"032820": "slot_cap:momentum"},
            }
        }
        item = {"selection_status": "WATCH_ONLY"}

        reason = dashboard_server_module._resolve_ticker_select_reason(
            "032820",
            "KR",
            "MILD_BULL",
            item,
            rec,
            base_reason="",
            watch_only_detail="",
        )

        self.assertEqual(reason, "TRADE_READY 제외 · 모멘텀 슬롯 한도 도달")


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
                fromisoformat = staticmethod(__import__("datetime").datetime.fromisoformat)
                combine = staticmethod(__import__("datetime").datetime.combine)

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
            brain_path.write_bytes(bytes.fromhex("efbbbf") + json.dumps({"ok": True}).encode("utf-8"))

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


class BrainIntegrityTests(unittest.TestCase):
    def test_normalize_brain_dedupes_recent_days_debate_history_and_rules(self):
        brain = {
            "meta": {"version": 1, "trained_days_kr": 0, "trained_days_us": 0},
            "markets": {
                "KR": {
                    "recent_days": [
                        {"date": "2026-04-22", "mode": "MILD_BULL"},
                        {"date": "2026-04-22", "mode": "CAUTIOUS"},
                    ],
                    "debate_history": [
                        {"date": "2026-04-22", "r1": {"bull": {"stance": "A"}}, "r2": {"bull": {"stance": "A"}}, "changes": [], "consensus_shifted": False, "outcome": None},
                        {"date": "2026-04-22", "r1": {"bull": {"stance": "B"}}, "r2": {"bull": {"stance": "B"}}, "changes": [{"analyst": "bull"}], "consensus_shifted": True, "outcome": "correct"},
                    ],
                    "trained_days": 0,
                },
                "US": {
                    "recent_days": [],
                    "debate_history": [],
                    "trained_days": 0,
                },
            },
            "correction_guide": {
                "KR": {"tuning_rules": ["규칙A", "규칙A", ""]},
                "US": {"tuning_rules": []},
            },
            "cross_market": {},
            "hold_advisor_performance": {},
        }

        normalized = brain_module._normalize_brain(brain)

        self.assertEqual(len(normalized["markets"]["KR"]["recent_days"]), 1)
        self.assertEqual(normalized["markets"]["KR"]["recent_days"][0]["mode"], "CAUTIOUS")
        self.assertEqual(len(normalized["markets"]["KR"]["debate_history"]), 1)
        self.assertEqual(normalized["markets"]["KR"]["debate_history"][0]["outcome"], "correct")
        self.assertEqual(normalized["correction_guide"]["KR"]["tuning_rules"], ["규칙A"])
        self.assertEqual(normalized["markets"]["KR"]["trained_days"], 1)

    def test_next_issue_pattern_id_uses_max_valid_id(self):
        patterns = [
            {"id": "P001"},
            {"id": "P003"},
            {"id": "PX99"},
            {"id": None},
            {"type": "missing_id"},
            "not_a_pattern",
        ]

        self.assertEqual(brain_module._next_issue_pattern_id(patterns), "P004")

    def test_next_issue_pattern_id_starts_at_p001_without_valid_ids(self):
        patterns = [{"id": "legacy"}, {"type": "missing_id"}]

        self.assertEqual(brain_module._next_issue_pattern_id(patterns), "P001")
        self.assertEqual(brain_module._next_issue_pattern_id([]), "P001")

    def test_update_issue_pattern_allocates_after_max_id_without_collision(self):
        patterns = [
            {"id": "P001"},
            {"id": "P002"},
            {"id": "P004"},
        ]
        fake_brain = {"markets": {"KR": {"issue_patterns": patterns}}}
        saved = []

        with patch.object(brain_module, "load", return_value=fake_brain), \
             patch.object(brain_module, "save", side_effect=saved.append):
            brain_module.update_issue_pattern("KR", {
                "type": "new_gap_case",
                "description": "gap allocation must not duplicate P004",
                "bull_hit": True,
                "pnl_pct": 1.25,
            })

        ids = [p["id"] for p in patterns]
        self.assertEqual(patterns[-1]["id"], "P005")
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(saved, [fake_brain])

    def test_update_correction_guide_preserves_non_empty_guide_on_empty_input(self):
        brain = {
            "correction_guide": {
                "KR": {
                    "bull_adjustments": ["MACD 골든크로스 우선"],
                    "bear_adjustments": [],
                    "tuning_rules": ["거래량 증가 확인"],
                    "today_notes": "기존 메모",
                    "generated_date": "2026-04-20",
                }
            }
        }
        saved = []

        with patch.object(brain_module, "load", return_value=brain), \
             patch.object(brain_module, "save", side_effect=lambda payload: saved.append(payload)):
            brain_module.update_correction_guide(
                "KR",
                {
                    "bull_adjustments": [],
                    "bear_adjustments": [],
                    "tuning_rules": [],
                    "today_notes": "",
                },
            )

        self.assertEqual(saved, [])
        self.assertEqual(brain["correction_guide"]["KR"]["bull_adjustments"], ["MACD 골든크로스 우선"])
        self.assertEqual(brain["correction_guide"]["KR"]["tuning_rules"], ["거래량 증가 확인"])

    def test_update_correction_guide_accepts_non_empty_input(self):
        brain = {"correction_guide": {"KR": {"bull_adjustments": ["old"]}}}
        saved = []

        with patch.object(brain_module, "load", return_value=brain), \
             patch.object(brain_module, "save", side_effect=lambda payload: saved.append(payload)):
            brain_module.update_correction_guide(
                "KR",
                {
                    "bull_adjustments": ["new"],
                    "bear_adjustments": [],
                    "tuning_rules": ["rule"],
                    "today_notes": "",
                },
            )

        self.assertEqual(len(saved), 1)
        self.assertEqual(brain["correction_guide"]["KR"]["bull_adjustments"], ["new"])
        self.assertEqual(brain["correction_guide"]["KR"]["tuning_rules"], ["rule"])
        self.assertIn("generated_date", brain["correction_guide"]["KR"])

    def test_update_execution_pattern_writes_readable_execution_lessons(self):
        brain = {
            "markets": {
                "KR": {
                    "execution_patterns": {},
                    "execution_lessons": [],
                    "execution_stats": {
                        "buy_order": 0,
                        "buy_failed": 0,
                        "sell_filled": 0,
                        "sell_failed": 0,
                    },
                }
            }
        }
        events = [
            {"action": "buy_failed", "strategy": "momentum", "reason": "order_reject"},
            {"action": "sell_failed", "strategy": "momentum", "reason": "pre_session_sell"},
            {"action": "sell_filled", "strategy": "momentum", "reason": "pre_close", "pnl_pct": -1.2},
            {"action": "sell_filled", "strategy": "momentum", "reason": "tp_analyst_sell", "pnl_pct": 1.5},
        ]

        with patch.object(brain_module, "load", return_value=brain), \
             patch.object(brain_module, "save", lambda payload: None):
            for event in events:
                brain_module.update_execution_pattern("KR", event)

        self.assertEqual(
            brain["markets"]["KR"]["execution_lessons"],
            [
                "momentum 매수 실패 주요 사유: order_reject",
                "청산 실패 주요 사유: pre_session_sell",
                "손실 매도 주요 사유: pre_close",
                "수익 청산 유효 패턴: tp_analyst_sell",
            ],
        )


class DashboardLiveBrokerTruthTests(unittest.TestCase):
    def setUp(self):
        self.client = dashboard_server_module.app.test_client()

    def test_api_summary_live_uses_broker_positions_not_legacy_saved_positions(self):
        with patch.object(dashboard_server_module, "load_records", return_value=[{"date": "2026-04-24"}]), \
             patch.object(dashboard_server_module, "load_today", return_value={"date": "2026-04-24", "actual_result": {"cumulative": 1_000_000}, "consensus": {"mode": "MILD_BULL", "size": 55}}), \
             patch.object(dashboard_server_module, "_load_live_status", return_value={"pending_orders": [], "mode": "MILD_BULL", "max_order_krw": 500_000}), \
             patch.object(dashboard_server_module, "_is_fresh_live_status", return_value=False), \
             patch.object(dashboard_server_module, "_record_metrics", return_value={"pnl_krw": 0.0, "pnl_pct": 0.0, "win": False, "trades": 0}), \
             patch.object(dashboard_server_module, "_load_broker_positions", return_value=[{"ticker": "OKLO", "name": "Oklo", "qty": 1, "avg_price": 75.0, "current_price": 76.0, "pnl_pct": 1.33, "strategy": "broker_balance", "price_source": "broker_balance", "currency": "USD"}]), \
             patch.object(dashboard_server_module, "_load_broker_positions_fast", return_value=[{"ticker": "OKLO", "name": "Oklo", "qty": 1, "avg_price": 75.0, "current_price": 76.0, "pnl_pct": 1.33, "strategy": "broker_balance", "price_source": "broker_balance", "currency": "USD"}]), \
             patch.object(dashboard_server_module, "_live_position_context_for_market", return_value=[{"ticker": "OKLO", "strategy": "gap_pullback"}]), \
             patch.object(dashboard_server_module, "_saved_positions_for_market", return_value=[{"ticker": "STALE", "strategy": "momentum"}]), \
             patch.object(dashboard_server_module, "_broker_snapshot", return_value={"source": "broker", "usd_krw": 1400.0, "kr_cash_effective": 1000000.0, "kr_cash": 1000000.0, "kr_eval": 0.0, "us_cash_krw": 0.0, "us_eval_krw": 106400.0, "us_cash_usd": 0.0, "us_eval_usd": 76.0, "unrealized_krw": {"US": 1400.0, "KR": 0.0}, "cumulative": 1_106_400.0}), \
             patch.object(dashboard_server_module, "_broker_snapshot_fast", return_value={"source": "broker", "usd_krw": 1400.0, "kr_cash_effective": 1000000.0, "kr_cash": 1000000.0, "kr_eval": 0.0, "us_cash_krw": 0.0, "us_eval_krw": 106400.0, "us_cash_usd": 0.0, "us_eval_usd": 76.0, "unrealized_krw": {"US": 1400.0, "KR": 0.0}, "cumulative": 1_106_400.0}), \
             patch.object(dashboard_server_module, "_persist_broker_equity_snapshot", return_value=None), \
             patch.object(dashboard_server_module, "_broker_realized_pnl_krw", return_value=0.0), \
             patch.object(dashboard_server_module, "_ticker_name_map", return_value={}), \
             patch.object(dashboard_server_module, "_today_signal_digest", return_value={}), \
             patch.object(dashboard_server_module, "_ml_db_digest", return_value={}), \
             patch.object(dashboard_server_module, "_adaptive_param_digest", return_value={}), \
             patch.object(dashboard_server_module, "_current_risk_snapshot", return_value={}):
            response = self.client.get("/api/summary?market=US&mode=live")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        tickers = [item["ticker"] for item in payload["today"]["positions"]]
        self.assertEqual(tickers, ["OKLO"])
        self.assertEqual(payload["today"]["positions"][0]["strategy"], "gap_pullback")

    def test_api_trades_list_live_returns_broker_rows_only(self):
        broker_rows = [
            {
                "date": "2026-04-24",
                "time": "02:30",
                "side": "sell",
                "ticker": "OKLO",
                "strategy": "broker_sync",
                "price": 76.0,
                "display_price": 76.0,
                "qty": 1,
                "pnl": 1400.0,
                "pnl_pct": 1.33,
                "reason": "broker_fill",
                "order_no": "123",
                "price_source": "order_fill",
                "currency": "USD",
                "source_kind": "broker_fill",
                "pnl_known": True,
            }
        ]
        with patch.object(dashboard_server_module, "load_records_filtered", return_value=[{"date": "2026-04-24"}]), \
             patch.object(dashboard_server_module, "_trades_for_record", return_value=[{"date": "2026-04-24", "time": "02:31", "side": "sell", "ticker": "GHOST", "strategy": "momentum", "price": 10.0, "display_price": 10.0, "qty": 1, "pnl": 500.0, "pnl_pct": 5.0, "reason": "tp_analyst_sell", "order_no": "x"}]), \
             patch.object(dashboard_server_module, "_broker_trade_rows_with_pnl", return_value=broker_rows), \
             patch.object(dashboard_server_module, "_ticker_name_map", return_value={}), \
             patch.object(dashboard_server_module, "_live_trades", return_value=[]):
            response = self.client.get("/api/trades/list?market=US&mode=live&include_live=false")

        self.assertEqual(response.status_code, 200)
        rows = response.get_json()
        self.assertEqual([row["ticker"] for row in rows], ["OKLO"])

    def test_api_stats_period_live_uses_broker_trade_rows(self):
        broker_rows = [
            {"side": "sell", "date": "2026-04-24", "ticker": "OKLO", "pnl": 1400.0, "pnl_pct": 1.33, "pnl_known": True},
            {"side": "sell", "date": "2026-04-24", "ticker": "NVTS", "pnl": -700.0, "pnl_pct": -0.66, "pnl_known": True},
        ]
        with patch.object(dashboard_server_module, "load_records_filtered", return_value=[{"date": "2026-04-24"}]), \
             patch.object(dashboard_server_module, "_broker_trade_rows_with_pnl", return_value=broker_rows):
            response = self.client.get("/api/stats/period?market=US&mode=live&period=month")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["basis"], "broker_closed_trades")
        self.assertEqual(payload["trades"], 2)
        self.assertAlmostEqual(payload["total_pnl"], 0.67, places=2)

    def test_api_history_equity_live_uses_broker_asset_reconstruction(self):
        broker_rows = [
            {"side": "sell", "date": "2026-04-23", "ticker": "OKLO", "pnl": 1000.0, "pnl_pct": 1.0, "pnl_known": True},
            {"side": "sell", "date": "2026-04-24", "ticker": "NVTS", "pnl": -500.0, "pnl_pct": -0.5, "pnl_known": True},
        ]
        broker_snapshot = {
            "source": "broker",
            "kr_cash_effective": 1_000_000.0,
            "kr_cash": 1_000_000.0,
            "kr_eval": 0.0,
            "us_cash_krw": 0.0,
            "us_eval_krw": 105_000.0,
            "unrealized_krw": {"US": 500.0, "KR": 0.0},
            "cumulative": 1_105_000.0,
        }
        with patch.object(dashboard_server_module, "_broker_snapshot", return_value=broker_snapshot), \
             patch.object(dashboard_server_module, "_persist_broker_equity_snapshot", return_value=None), \
             patch.object(dashboard_server_module, "_load_broker_equity_snapshots", return_value=[]), \
             patch.object(dashboard_server_module, "_broker_trade_rows_with_pnl", return_value=broker_rows):
            response = self.client.get("/api/history/equity?market=US&mode=live&period=month&refresh=true")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["basis"], "broker_asset_reconstructed")
        # Last label should be a recent date (within 2 days of today to handle midnight boundary).
        from datetime import timedelta
        last_label = date.fromisoformat(payload["labels"][-1])
        self.assertLessEqual(last_label, date.today())
        self.assertGreaterEqual(last_label, date.today() - timedelta(days=2))
        self.assertEqual(payload["equity"][-1], 105000.0)


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

    def test_micro_probe_log_records_entry_and_outcome_report(self):
        tsdb.log_micro_probe_entry(
            session_date="2026-04-24",
            market="KR",
            ticker="123456",
            order_no="P1",
            source_strategy="momentum",
            reason="order_size_too_small_probe",
            entry_priority_score=0.62,
            original_qty=1,
            adjusted_qty=4,
            original_order_cost_krw=8_000,
            adjusted_order_cost_krw=32_000,
            order_budget_krw=20_000,
            min_effective_order_krw=30_000,
            oversize_ratio=1.6,
            entered_at="2026-04-24T09:10:00+09:00",
        )
        tsdb.update_micro_probe_outcome(
            market="KR",
            ticker="123456",
            order_no="P1",
            pnl_pct=1.25,
            pnl_krw=400,
            exit_reason="take_profit",
            exited_at="2026-04-24T10:10:00+09:00",
        )

        report = tsdb.micro_probe_performance_report("KR")

        self.assertEqual(report["trades"], 1)
        self.assertEqual(report["wins"], 1)
        self.assertEqual(report["win_rate_pct"], 100.0)
        self.assertEqual(report["avg_pnl_pct"], 1.25)
        self.assertEqual(report["total_pnl_krw"], 400)
        with sqlite3.connect(tsdb.DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT source_strategy, original_order_cost_krw, adjusted_order_cost_krw,
                       oversize_ratio, status, exit_reason
                FROM micro_probe_log
                WHERE ticker='123456'
                """
            ).fetchone()
        self.assertEqual(row[0], "momentum")
        self.assertEqual(row[1], 8_000)
        self.assertEqual(row[2], 32_000)
        self.assertEqual(row[3], 1.6)
        self.assertEqual(row[4], "CLOSED")
        self.assertEqual(row[5], "take_profit")

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
            date=date.today().isoformat(),
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
        with patch.dict(os.environ, {"CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false"}, clear=False), \
             patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
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
        self.assertIn("recent selection feedback", captured["prompt"])
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
        with patch.dict(os.environ, {"CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false"}, clear=False), \
             patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
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
        _empty_lessons = {"section": "", "metadata": {"count": 0, "injected": False, "shadow": True, "chars": 0}}
        with patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "build_active_lesson_context", return_value=_empty_lessons):
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
        with patch.dict(os.environ, {"CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false"}, clear=False), \
             patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
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
        self.assertIn("price_targets", prompts[1])
        self.assertEqual(tickers, ["NVDA", "AAPL"])
        self.assertEqual(reasons["NVDA"], "strong")
        meta = analysts_module.get_last_selection_meta()
        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["_selection_retry_trade_ready_ignored"], ["NVDA"])

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

    def test_postmortem_exception_does_not_store_system_error_as_policy_pattern(self):
        try:
            from minority_report import postmortem as postmortem_module
        except Exception as exc:
            self.skipTest(f"postmortem import unavailable: {exc}")

        captured = {"issue_pattern_calls": 0, "correction_guide_calls": 0}

        def _raise_create(*, model, max_tokens, messages):
            captured["prompt"] = messages[0]["content"]
            raise ValueError("Expecting ',' delimiter: line 1 column 2")

        def _capture_issue_pattern(*args, **kwargs):
            captured["issue_pattern_calls"] += 1

        def _capture_daily_record(market, record):
            captured["daily_record"] = (market, record)

        def _capture_correction_guide(*args, **kwargs):
            captured["correction_guide_calls"] += 1

        judgment = {
            "judgments": {
                "bull": {"stance": "MILD_BULL", "key_reason": "bull"},
                "bear": {"stance": "NEUTRAL", "key_reason": "bear"},
                "neutral": {"stance": "NEUTRAL", "key_reason": "neutral"},
            },
            "consensus": {"mode": "NEUTRAL", "size": 10},
        }
        actual_result = {"market_change": 0.8, "pnl_pct": -0.3, "win": False}

        with patch.object(postmortem_module.client.messages, "create", side_effect=_raise_create), \
             patch.object(postmortem_module.BrainDB, "generate_prompt_summary", return_value="brain summary"), \
             patch.object(postmortem_module.BrainDB, "get_recent_selection_feedback_text", return_value=""), \
             patch.object(postmortem_module.BrainDB, "load", return_value={"markets": {"KR": {"recent_days": []}}}), \
             patch.object(postmortem_module.BrainDB, "update_analyst", lambda *args, **kwargs: None), \
             patch.object(postmortem_module.BrainDB, "update_mode_performance", lambda *args, **kwargs: None), \
             patch.object(postmortem_module.BrainDB, "update_beliefs", lambda *args, **kwargs: None), \
             patch.object(postmortem_module.BrainDB, "update_issue_pattern", side_effect=_capture_issue_pattern), \
             patch.object(postmortem_module.BrainDB, "add_daily_record", side_effect=_capture_daily_record), \
             patch.object(postmortem_module.BrainDB, "update_strategy_performance", lambda *args, **kwargs: None), \
             patch.object(postmortem_module.BrainDB, "update_debate_outcome", lambda *args, **kwargs: None), \
             patch.object(postmortem_module.BrainDB, "update_correction_guide", side_effect=_capture_correction_guide):
            pm = postmortem_module.run(
                market="KR",
                date="2026-04-22",
                today_judgment=judgment,
                actual_result=actual_result,
                digest_prompt="market digest",
                trade_log=[],
                decision_event_log=[],
            )

        self.assertTrue(pm["_system_error"])
        self.assertTrue(pm["_skip_issue_pattern"])
        self.assertEqual(captured["issue_pattern_calls"], 0)
        self.assertEqual(captured["correction_guide_calls"], 0)
        self.assertIn("Postmortem JSON parse failed", captured["daily_record"][1]["key_lesson"])
        self.assertEqual(captured["daily_record"][1]["issue_type"], "postmortem_parse_error")


if __name__ == "__main__":
    unittest.main()
