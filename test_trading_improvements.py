import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import ticker_selection_db as tsdb
from bot import candidate_policy
from bot.log_sanitizer import mask_secrets
from dashboard import dashboard_server as dashboard_server_module
import risk_manager
import trading_bot as trading_bot_module
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
    def test_cautious_bull_mode_is_disabled(self):
        params = continuation.params("CAUTIOUS_BULL", market="KR")
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


class TradingBotGateTests(unittest.TestCase):
    def _make_bot(self):
        bot = trading_bot_module.TradingBot.__new__(trading_bot_module.TradingBot)
        bot.is_paper = True
        bot.trade_ready_tickers = {"KR": [], "US": []}
        bot.selection_meta = {"KR": {}, "US": {}}
        bot.today_ticker_reasons = {"KR": {}, "US": {}}
        bot.today_judgment = {}
        bot._active_session_date = {"KR": None, "US": None}
        bot.entry_priority_cutoff_enabled = True
        bot.entry_priority_cutoff = 0.20
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
        bot.today_ticker_reasons["US"] = {"AAPL": "RS 약세. watch 유지"}
        bot.selection_meta["US"] = {"trade_ready": ["NVDA"]}

        self.assertEqual(bot._watch_only_bucket("US", "AAPL"), "SOFT")
        self.assertTrue(bot._can_recheck_soft_watch_only("US", "AAPL", "CAUTIOUS_BEAR"))
        self.assertFalse(bot._can_recheck_soft_watch_only("US", "AAPL", "DEFENSIVE"))

    def test_promote_trade_ready_ticker_updates_state(self):
        bot = self._make_bot()
        bot.selection_meta["US"] = {"watchlist": ["AAPL"], "trade_ready": []}
        bot.today_judgment = {"market": "US"}

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
        self._tmp.cleanup()

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
                market_change_pct=0.0,
                secondary_change_pct=0.0,
            )

        self.assertEqual(tickers, ["005930"])
        self.assertEqual(reasons["005930"], "ok")
        self.assertIn("recent selection feedback:", captured["prompt"])
        self.assertIn("Use recent selection feedback to calibrate trade_ready aggressiveness.", captured["prompt"])
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
