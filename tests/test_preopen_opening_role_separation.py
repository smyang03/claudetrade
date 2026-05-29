from __future__ import annotations

import json
import inspect
import os
import time
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import trading_bot
from bot.session_date import KST


class PreopenOpeningRoleSeparationTests(unittest.TestCase):
    def _resume_bot(self):
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot.is_paper = False
        bot._current_session_date_str = lambda market: "2026-05-15"
        bot._is_market_session_now = lambda market: True
        bot._new_buy_block_state = lambda market, ticker, strategy: {"allowed": True}
        bot._token_for_market = lambda market: "token"
        bot._price_to_krw = lambda price, market: float(price)
        bot.price_cache_raw = {}
        bot.price_cache = {}
        return bot

    def _resume_saved(self, *, now=None, market: str = "US") -> dict:
        current = now or datetime.now(KST)
        ticker = "AAPL" if market == "US" else "005930"
        return {
            "date": "2026-05-15",
            "market": market,
            "mode": "live",
            "tickers": [ticker],
            "trade_ready_tickers": [ticker],
            "selection_meta": {
                "watchlist": [ticker],
                "trade_ready": [ticker],
                "selection_snapshot_ts": current.isoformat(timespec="seconds"),
                "price_targets": {ticker: {"reference_price": 100.0}},
            },
            "judgment_context_basis": {
                "phase": "intraday_live",
                "live_index_context_ok": True,
                "updated_at": current.isoformat(timespec="seconds"),
            },
        }

    def test_judgment_phase_contract_blocks_preopen_for_kr_and_us(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)

        for market in ("KR", "US"):
            bot.today_judgment = {
                "market": market,
                "consensus": {"mode": "MILD_BULL", "size": 50},
                "judgment_context_basis": {"phase": "preopen_watch"},
            }

            allowed, reason = trading_bot.TradingBot._new_entry_judgment_gate(bot, market)

            self.assertFalse(allowed)
            self.assertIn("non_executable_judgment_phase:preopen_watch", reason)

    def test_judgment_phase_contract_allows_opening_and_intraday(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)

        for phase in ("opening_confirm", "intraday_live"):
            bot.today_judgment = {
                "market": "KR",
                "consensus": {"mode": "MILD_BULL", "size": 50},
                "judgment_context_basis": {"phase": phase},
            }

            allowed, reason = trading_bot.TradingBot._new_entry_judgment_gate(bot, "KR")

            self.assertTrue(allowed)
            self.assertEqual(reason, "ok")

    def test_current_phase_common_for_preopen_opening_and_intraday(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        regular_open = datetime(2026, 5, 4, 9, 0, tzinfo=KST)
        bot._market_regular_open_dt = lambda market, **kwargs: regular_open

        self.assertEqual(
            trading_bot.TradingBot._current_judgment_phase(
                bot,
                "KR",
                now_dt=regular_open - timedelta(minutes=1),
            ),
            "preopen_watch",
        )
        self.assertEqual(
            trading_bot.TradingBot._current_judgment_phase(
                bot,
                "KR",
                now_dt=regular_open + timedelta(minutes=5),
            ),
            "opening_confirm",
        )
        self.assertEqual(
            trading_bot.TradingBot._current_judgment_phase(
                bot,
                "KR",
                now_dt=regular_open + timedelta(minutes=45),
            ),
            "intraday_live",
        )

    def test_force_preopen_watch_only_demotes_execution_fields(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot.selection_meta = {"KR": {}}
        bot.trade_ready_tickers = {"KR": ["005930"]}
        bot.selection_stages = {"KR": {}}
        bot.today_judgment = {"market": "KR"}

        meta = trading_bot.TradingBot._force_preopen_watch_only(
            bot,
            "KR",
            {
                "watchlist": ["005930", "000660"],
                "trade_ready": ["005930"],
                "recommended_strategy": {"005930": "momentum"},
                "max_position_pct": {"005930": 20},
                "max_order_cap_pct": {"005930": 20},
                "risk_budget_pct": {"005930": 0.3},
                "price_targets": {"005930": {"buy_zone_low": 70000}},
            },
        )

        self.assertEqual(meta["watchlist"], ["005930", "000660"])
        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["recommended_strategy"], {})
        self.assertEqual(meta["price_targets"], {})
        self.assertEqual(bot.trade_ready_tickers["KR"], [])
        self.assertEqual(bot.today_judgment["trade_ready_tickers"], [])

    def test_saved_preopen_judgment_requires_refresh_after_open(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot._market_after_open_refresh_time = lambda market: True
        bot._digest_payload_built_before_open = lambda market, payload=None: False

        self.assertTrue(
            trading_bot.TradingBot._saved_judgment_requires_intraday_refresh(
                bot,
                {"judgment_context_basis": {"phase": "preopen_watch"}},
                "US",
            )
        )

    def test_startup_mid_session_hydrates_empty_price_cache_before_guard(self) -> None:
        bot = self._resume_bot()
        now = datetime.now(KST)
        saved = self._resume_saved(now=now)

        with patch("trading_bot.get_price", return_value={"price": 100.5}) as get_price:
            precheck = trading_bot.TradingBot._trade_ready_resume_precheck(
                bot,
                saved,
                "US",
                "startup_mid_session",
                now_dt=now,
            )
            hydration = trading_bot.TradingBot._hydrate_trade_ready_resume_prices(
                bot,
                "US",
                precheck["trade_ready"],
                timeout_sec=2,
            )
            guard = trading_bot.TradingBot._trade_ready_resume_guard(
                bot,
                saved,
                "US",
                "startup_mid_session",
                now_dt=now,
                precheck=precheck,
                price_hydration=hydration,
            )

        self.assertTrue(precheck["ready_for_price_check"])
        self.assertEqual(get_price.call_count, 1)
        self.assertEqual(bot.price_cache_raw["AAPL"], 100.5)
        self.assertTrue(guard["preserve"])
        self.assertEqual(guard["reason"], "preserve_recent_trade_ready")

    def test_startup_mid_session_hydration_failure_rescreens(self) -> None:
        bot = self._resume_bot()
        now = datetime.now(KST)
        saved = self._resume_saved(now=now)

        with patch("trading_bot.get_price", side_effect=RuntimeError("provider down")):
            precheck = trading_bot.TradingBot._trade_ready_resume_precheck(
                bot,
                saved,
                "US",
                "startup_mid_session",
                now_dt=now,
            )
            hydration = trading_bot.TradingBot._hydrate_trade_ready_resume_prices(
                bot,
                "US",
                precheck["trade_ready"],
                timeout_sec=2,
            )
            guard = trading_bot.TradingBot._trade_ready_resume_guard(
                bot,
                saved,
                "US",
                "startup_mid_session",
                now_dt=now,
                precheck=precheck,
                price_hydration=hydration,
            )

        self.assertFalse(guard["preserve"])
        self.assertEqual(guard["reason"], "resume_price_hydration_failed")
        self.assertIn("AAPL", guard["skipped"])

    def test_startup_mid_session_rejects_missing_market(self) -> None:
        bot = self._resume_bot()
        saved = self._resume_saved()
        saved.pop("market")

        guard = trading_bot.TradingBot._trade_ready_resume_guard(
            bot,
            saved,
            "US",
            "startup_mid_session",
        )

        self.assertFalse(guard["preserve"])
        self.assertEqual(guard["reason"], "incomplete_saved_judgment:missing_market")

    def test_startup_mid_session_rejects_missing_session_date(self) -> None:
        bot = self._resume_bot()
        saved = self._resume_saved()
        saved.pop("date")
        saved.pop("session_date", None)

        guard = trading_bot.TradingBot._trade_ready_resume_guard(
            bot,
            saved,
            "US",
            "startup_mid_session",
        )

        self.assertFalse(guard["preserve"])
        self.assertEqual(guard["reason"], "incomplete_saved_judgment:missing_session_date")

    def test_should_rescreen_reused_judgment_has_no_side_effect(self) -> None:
        bot = self._resume_bot()
        bot.price_cache_raw = {"AAPL": 100.5}
        bot._last_trade_ready_resume_guard = {"US": {"reason": "old"}}
        before = dict(bot._last_trade_ready_resume_guard)

        should_rescreen = trading_bot.TradingBot._should_rescreen_reused_judgment(
            bot,
            "startup_mid_session",
            self._resume_saved(),
            "US",
        )

        self.assertFalse(should_rescreen)
        self.assertEqual(bot._last_trade_ready_resume_guard, before)

    def test_session_open_stores_resume_guard_in_call_site(self) -> None:
        source = inspect.getsource(trading_bot.TradingBot.session_open)

        self.assertIn("_trade_ready_resume_precheck", source)
        self.assertIn("_hydrate_trade_ready_resume_prices", source)
        self.assertIn("_trade_ready_resume_guard", source)
        self.assertIn("_last_trade_ready_resume_guard[market] = dict(_resume_guard)", source)
        self.assertIn("_failclose_trade_ready_after_resume_rescreen_empty", source)

    def test_resume_rescreen_empty_failcloses_trade_ready_state(self) -> None:
        bot = self._resume_bot()
        meta = {
            "watchlist": ["AAPL"],
            "trade_ready": ["AAPL"],
            "selection_snapshot_ts": "2026-05-15T10:00:00+09:00",
            "price_targets": {"AAPL": {"reference_price": 100.0}},
        }
        bot.today_tickers = {"US": ["AAPL"], "KR": []}
        bot.trade_ready_tickers = {"US": ["AAPL"], "KR": []}
        bot.selection_meta = {"US": dict(meta), "KR": {}}
        bot.selection_stages = {
            "US": {
                "normalized": {"trade_ready": ["AAPL"], "runtime_filtered": {}},
                "applied": {"trade_ready": ["AAPL"]},
            }
        }
        bot.today_judgment = {
            "market": "US",
            "selection_meta": dict(meta),
            "trade_ready_tickers": ["AAPL"],
        }

        summary = trading_bot.TradingBot._failclose_trade_ready_after_resume_rescreen_empty(
            bot,
            "US",
            {"reason": "price_hydration_failed"},
        )

        self.assertEqual(summary["previous_trade_ready"], ["AAPL"])
        self.assertEqual(bot.today_tickers["US"], ["AAPL"])
        self.assertEqual(bot.trade_ready_tickers["US"], [])
        self.assertEqual(bot.selection_meta["US"]["trade_ready"], [])
        self.assertEqual(bot.today_judgment["trade_ready_tickers"], [])
        self.assertEqual(bot.today_judgment["selection_meta"]["trade_ready"], [])
        self.assertEqual(bot.selection_stages["US"]["normalized"]["trade_ready"], [])
        self.assertEqual(bot.selection_stages["US"]["applied"]["trade_ready"], [])
        self.assertEqual(
            bot.selection_meta["US"]["_runtime_filtered_trade_ready"]["AAPL"],
            "resume_guard_failclosed:price_hydration_failed",
        )

    def test_startup_mid_session_preserves_recent_trade_ready_judgment(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot.is_paper = False
        bot._current_session_date_str = lambda market: "2026-05-15"
        bot._is_market_session_now = lambda market: True
        bot._new_buy_block_state = lambda market, ticker, strategy: {"allowed": True}
        bot.price_cache_raw = {"AAPL": 100.5}
        bot.price_cache = {}
        now = datetime.now(KST)
        saved = {
            "date": "2026-05-15",
            "market": "US",
            "mode": "live",
            "tickers": ["AAPL", "MSFT"],
            "trade_ready_tickers": ["AAPL"],
            "selection_meta": {
                "watchlist": ["AAPL", "MSFT"],
                "trade_ready": ["AAPL"],
                "selection_snapshot_ts": now.isoformat(timespec="seconds"),
                "price_targets": {"AAPL": {"buy_zone": [100.0, 101.0]}},
            },
            "judgment_context_basis": {
                "phase": "intraday_live",
                "live_index_context_ok": True,
                "updated_at": now.isoformat(timespec="seconds"),
            },
        }

        self.assertFalse(
            trading_bot.TradingBot._should_rescreen_reused_judgment(
                bot,
                "startup_mid_session",
                saved,
                "US",
            )
        )
        self.assertTrue(
            trading_bot.TradingBot._should_rescreen_reused_judgment(
                bot,
                "schedule",
            )
        )
        self.assertTrue(
            trading_bot.TradingBot._should_rescreen_reused_judgment(
                bot,
                "",
            )
        )

    def test_startup_mid_session_rescreens_stale_or_unconfirmed_trade_ready(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot.is_paper = False
        bot._current_session_date_str = lambda market: "2026-05-15"
        bot._is_market_session_now = lambda market: True
        bot._new_buy_block_state = lambda market, ticker, strategy: {"allowed": True}
        bot.price_cache_raw = {"AAPL": 100.5}
        bot.price_cache = {}
        stale = datetime.now(KST) - timedelta(minutes=45)
        saved = {
            "date": "2026-05-15",
            "market": "US",
            "mode": "live",
            "tickers": ["AAPL"],
            "trade_ready_tickers": ["AAPL"],
            "selection_meta": {
                "watchlist": ["AAPL"],
                "trade_ready": ["AAPL"],
                "selection_snapshot_ts": stale.isoformat(timespec="seconds"),
                "price_targets": {"AAPL": {"buy_zone": [100.0, 101.0]}},
            },
            "judgment_context_basis": {
                "phase": "intraday_live",
                "live_index_context_ok": True,
                "updated_at": stale.isoformat(timespec="seconds"),
            },
        }

        with patch.dict(os.environ, {"TRADE_READY_RESTORE_MAX_AGE_MINUTES": "20"}, clear=False):
            self.assertTrue(
                trading_bot.TradingBot._should_rescreen_reused_judgment(
                    bot,
                    "startup_mid_session",
                    saved,
                    "US",
                )
            )

        saved["selection_meta"]["selection_snapshot_ts"] = datetime.now(KST).isoformat(timespec="seconds")
        saved["judgment_context_basis"]["live_index_context_ok"] = False
        self.assertTrue(
            trading_bot.TradingBot._should_rescreen_reused_judgment(
                bot,
                "startup_mid_session",
                saved,
                "US",
            )
        )

    def test_startup_mid_session_rescreens_price_drift_exceeded(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot.is_paper = False
        bot._current_session_date_str = lambda market: "2026-05-15"
        bot._is_market_session_now = lambda market: True
        bot._new_buy_block_state = lambda market, ticker, strategy: {"allowed": True}
        bot.price_cache_raw = {"AAPL": 105.0}
        bot.price_cache = {}
        now = datetime.now(KST)
        saved = {
            "date": "2026-05-15",
            "market": "US",
            "mode": "live",
            "tickers": ["AAPL"],
            "trade_ready_tickers": ["AAPL"],
            "selection_meta": {
                "watchlist": ["AAPL"],
                "trade_ready": ["AAPL"],
                "selection_snapshot_ts": now.isoformat(timespec="seconds"),
                "price_targets": {"AAPL": {"reference_price": 100.0}},
            },
            "judgment_context_basis": {
                "phase": "intraday_live",
                "live_index_context_ok": True,
                "updated_at": now.isoformat(timespec="seconds"),
            },
        }

        with patch.dict(os.environ, {"TRADE_READY_RESTORE_MAX_PRICE_DRIFT_PCT": "2.0"}, clear=False):
            guard = trading_bot.TradingBot._trade_ready_resume_guard(
                bot,
                saved,
                "US",
                "startup_mid_session",
                now_dt=now,
            )

        self.assertFalse(guard["preserve"])
        self.assertEqual(guard["reason"], "price_drift_exceeded")
        self.assertIn("AAPL", guard["skipped"])

    def test_startup_mid_session_rescreens_when_price_unchecked_by_default(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot.is_paper = False
        bot._current_session_date_str = lambda market: "2026-05-15"
        bot._is_market_session_now = lambda market: True
        bot._new_buy_block_state = lambda market, ticker, strategy: {"allowed": True}
        bot.price_cache_raw = {}
        bot.price_cache = {}
        now = datetime.now(KST)
        saved = {
            "date": "2026-05-15",
            "market": "US",
            "mode": "live",
            "tickers": ["AAPL"],
            "trade_ready_tickers": ["AAPL"],
            "selection_meta": {
                "watchlist": ["AAPL"],
                "trade_ready": ["AAPL"],
                "selection_snapshot_ts": now.isoformat(timespec="seconds"),
                "price_targets": {"AAPL": {"reference_price": 100.0}},
            },
            "judgment_context_basis": {
                "phase": "intraday_live",
                "live_index_context_ok": True,
                "updated_at": now.isoformat(timespec="seconds"),
            },
        }

        guard = trading_bot.TradingBot._trade_ready_resume_guard(
            bot,
            saved,
            "US",
            "startup_mid_session",
            now_dt=now,
        )

        self.assertFalse(guard["preserve"])
        self.assertEqual(guard["reason"], "resume_price_hydration_failed")
        self.assertEqual(guard["skipped"], {"AAPL": "resume_price_hydration_failed"})

    def test_startup_mid_session_records_trade_ready_restore_failure_metadata(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot.today_judgment = {"market": "US"}
        guard = {
            "preserve": False,
            "reason": "resume_price_hydration_failed",
            "skipped": {"AAPL": "resume_price_hydration_failed"},
            "price_hydration": {"failed": {"AAPL": "resume_price_hydration_failed"}},
            "max_age_minutes": 20,
            "age_minutes": 4.5,
            "trade_ready": ["AAPL"],
        }

        recorded = trading_bot.TradingBot._record_trade_ready_restore_guard(
            bot,
            "US",
            "startup_mid_session",
            guard,
        )

        self.assertTrue(recorded)
        meta = bot.today_judgment["trade_ready_restore"]
        self.assertTrue(meta["attempted"])
        self.assertEqual(meta["restored"], [])
        self.assertEqual(meta["skipped"], {"AAPL": "resume_price_hydration_failed"})
        self.assertEqual(meta["price_hydration"], {"failed": {"AAPL": "resume_price_hydration_failed"}})
        self.assertEqual(meta["reason"], "resume_price_hydration_failed")
        self.assertEqual(bot.today_judgment["trade_ready_resume_guard"], guard)

        before = dict(bot.today_judgment)
        self.assertFalse(
            trading_bot.TradingBot._record_trade_ready_restore_guard(
                bot,
                "US",
                "schedule",
                {"preserve": False, "reason": "selection_stale"},
            )
        )
        self.assertEqual(bot.today_judgment, before)

    def test_max_daily_entries_alert_renders_rate_details(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot._current_session_date_str = lambda market: "2026-05-15"
        bot.today_judgment = {"consensus": {"mode": "NEUTRAL", "size": 20}}

        with patch("trading_bot.block_alert") as alert:
            trading_bot.TradingBot._maybe_alert_new_buy_block(
                bot,
                "US",
                "MAX_DAILY_ENTRIES",
                "market",
                {
                    "ticker": "AAPL",
                    "strategy": "momentum",
                    "rate_key": "live:US:buy",
                    "daily_count": 20,
                    "max_daily_entries": 20,
                },
            )

        rendered = "\n".join(alert.call_args.args[1])
        self.assertIn("daily entries: 20/20", rendered)
        self.assertIn("rate key: live:US:buy", rendered)

    def test_consensus_new_buy_permission_block_sets_hard_size_zero(self) -> None:
        from minority_report import consensus as consensus_module

        judgments = {
            "bull": {
                "stance": "MILD_BULL",
                "confidence": 0.7,
                "new_buy_permission": "allow",
                "max_gross_exposure_pct": 80,
            },
            "bear": {
                "stance": "CAUTIOUS_BEAR",
                "confidence": 0.8,
                "new_buy_permission": "block",
                "max_gross_exposure_pct": 25,
            },
            "neutral": {
                "stance": "NEUTRAL",
                "confidence": 0.6,
                "new_buy_permission": "selective",
                "max_gross_exposure_pct": 50,
            },
        }

        with patch.object(consensus_module, "_get_weights", return_value={"bull": 1.0, "bear": 1.0, "neutral": 1.0}):
            result = consensus_module.build_consensus(judgments, market="US")

        self.assertEqual(result["new_buy_permission"], "block")
        self.assertEqual(
            result["new_buy_permission_votes_by_role"],
            {"bull": "allow", "bear": "block", "neutral": "selective"},
        )
        self.assertEqual(result["max_gross_exposure_pct"], 25)
        self.assertEqual(result["max_gross_exposure_pct_by_role"]["bear"], 25)
        self.assertEqual(result["size"], 0)
        self.assertGreater(result["size_before_new_buy_block"], 0)

    def test_new_buy_gate_blocks_analyst_permission_and_max_gross(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot._is_order_allowed_now = lambda market: True
        bot._in_entry_blackout = lambda market: False
        bot.v2_order_unknown = None
        bot.v2 = None

        bot.today_judgment = {"consensus": {"new_buy_permission": "block", "max_gross_exposure_pct": 0}}
        state = trading_bot.TradingBot._new_buy_block_state(bot, "KR", "005930", "momentum")
        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "ANALYST_NEW_BUY_BLOCK")

        bot.today_judgment = {"consensus": {"new_buy_permission": "selective", "max_gross_exposure_pct": 20}}
        bot._market_equity_reference_context = lambda market: {
            "total_krw": 100000.0,
            "position_krw": 25000.0,
            "source": "test",
        }
        state = trading_bot.TradingBot._new_buy_block_state(bot, "KR", "005930", "momentum")
        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "ANALYST_MAX_GROSS_EXPOSURE_REACHED")
        self.assertEqual(state["details"]["gross_exposure_pct"], 25.0)

    def test_new_buy_gate_can_use_manual_gross_exposure_cap(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot._is_order_allowed_now = lambda market: True
        bot._in_entry_blackout = lambda market: False
        bot.v2_order_unknown = None
        bot.v2 = None
        bot.today_judgment = {"consensus": {"new_buy_permission": "selective", "max_gross_exposure_pct": 40}}
        bot._market_equity_reference_context = lambda market: {
            "total_krw": 100000.0,
            "position_krw": 50000.0,
            "source": "test",
        }

        with patch.dict(
            os.environ,
            {
                "US_ANALYST_GROSS_EXPOSURE_CAP_MODE": "manual",
                "US_ANALYST_GROSS_EXPOSURE_CAP_PCT": "60",
            },
            clear=False,
        ):
            state = trading_bot.TradingBot._new_buy_block_state(bot, "US", "AAPL", "momentum")

        self.assertTrue(state["allowed"])
        self.assertEqual(state["details"]["max_gross_exposure_pct"], 60.0)
        self.assertEqual(state["details"]["analyst_max_gross_exposure_pct"], 40.0)
        self.assertEqual(state["details"]["gross_cap_mode"], "manual")
        self.assertEqual(state["details"]["gross_cap_source"], "manual_config")

    def test_manual_gross_exposure_cap_falls_back_to_analyst_when_missing(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot._is_order_allowed_now = lambda market: True
        bot._in_entry_blackout = lambda market: False
        bot.v2_order_unknown = None
        bot.v2 = None
        bot.today_judgment = {"consensus": {"new_buy_permission": "selective", "max_gross_exposure_pct": 40}}
        bot._market_equity_reference_context = lambda market: {
            "total_krw": 100000.0,
            "position_krw": 50000.0,
            "source": "test",
        }

        with patch.dict(
            os.environ,
            {
                "US_ANALYST_GROSS_EXPOSURE_CAP_MODE": "manual",
                "US_ANALYST_GROSS_EXPOSURE_CAP_PCT": "",
            },
            clear=False,
        ):
            state = trading_bot.TradingBot._new_buy_block_state(bot, "US", "AAPL", "momentum")

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "ANALYST_MAX_GROSS_EXPOSURE_REACHED")
        self.assertEqual(state["details"]["max_gross_exposure_pct"], 40.0)
        self.assertEqual(state["details"]["gross_cap_source"], "analyst_consensus")
        self.assertEqual(state["details"]["gross_cap_config_error"], "manual_cap_missing_or_invalid")

    def test_new_buy_block_alert_includes_votes_and_dedupes(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot._current_session_date_str = lambda market: "2026-05-15"
        bot.today_judgment = {"consensus": {"mode": "CAUTIOUS", "size": 0, "new_buy_permission": "block"}}
        details = {
            "ticker": "AAPL",
            "strategy": "momentum",
            "permission_votes_by_role": {"bull": "block", "bear": "selective", "neutral": "block"},
            "max_gross_exposure_pct_by_role": {"bull": 35, "bear": 40, "neutral": 30},
            "max_gross_exposure_pct": 30,
        }

        with patch.dict(os.environ, {"NEW_BUY_BLOCK_TG_ALERT_REASONS": ""}, clear=False):
            with patch("trading_bot.block_alert") as alert:
                trading_bot.TradingBot._maybe_alert_new_buy_block(
                    bot,
                    "US",
                    "ANALYST_NEW_BUY_BLOCK",
                    "market",
                    details,
                )
                trading_bot.TradingBot._maybe_alert_new_buy_block(
                    bot,
                    "US",
                    "ANALYST_NEW_BUY_BLOCK",
                    "market",
                    details,
                )

        self.assertEqual(alert.call_count, 1)
        lines = alert.call_args.args[1]
        rendered = "\n".join(lines)
        self.assertIn("bull=block", rendered)
        self.assertIn("mode: CAUTIOUS", rendered)
        self.assertIn("size: 0%", rendered)
        self.assertIn("/claude US", rendered)
        self.assertIn("/rescreen US", rendered)

    def test_reinvoke_rescreen_triggers_when_permission_relaxes_same_mode(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)

        should_refresh, reason = trading_bot.TradingBot._should_refresh_selection_after_reinvoke(
            bot,
            {"mode": "CAUTIOUS", "size": 0, "new_buy_permission": "block", "max_gross_exposure_pct": 30},
            {"mode": "CAUTIOUS", "size": 20, "new_buy_permission": "selective", "max_gross_exposure_pct": 30},
            {"phase": "intraday_live_unconfirmed", "live_index_context_ok": False},
            {"phase": "intraday_live", "live_index_context_ok": True},
        )

        self.assertTrue(should_refresh)
        self.assertIn("new_buy_permission_relaxed", reason)

    def test_kr_intraday_context_does_not_render_stale_index_as_zero(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot.today_judgment = {
            "consensus": {"mode": "NEUTRAL"},
            "digest_raw": {"context": {"kospi": {"change_pct": 2.25}}},
        }
        bot.risk = SimpleNamespace(positions=[])
        bot._build_execution_profile_text = lambda market, mode: "profile=ok"
        bot._format_ops_review_context = lambda market: ""

        with patch("kis_api.get_index_snapshot", side_effect=RuntimeError("index unavailable")):
            context = trading_bot.TradingBot._build_intraday_context(bot, "KR")

        self.assertIn("KIS live index unavailable", context)
        self.assertIn("live_index_context_ok=false", context)
        self.assertNotIn("코스피 현재 +0.00%", context)
        self.assertNotIn("장전 대비", context)

    def test_reinvoke_rescreen_triggers_when_cap_relaxes_same_mode(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)

        with patch.dict(os.environ, {"REINVOKE_RESCREEN_CAP_INCREASE_THRESHOLD_PCT": "10"}, clear=False):
            should_refresh, reason = trading_bot.TradingBot._should_refresh_selection_after_reinvoke(
                bot,
                {"mode": "NEUTRAL", "size": 20, "new_buy_permission": "selective", "max_gross_exposure_pct": 20},
                {"mode": "NEUTRAL", "size": 20, "new_buy_permission": "selective", "max_gross_exposure_pct": 40},
                {"phase": "intraday_live", "live_index_context_ok": True},
                {"phase": "intraday_live", "live_index_context_ok": True},
            )

        self.assertTrue(should_refresh)
        self.assertIn("max_gross_cap_relaxed", reason)

    def test_run_cycle_blocks_new_buy_scan_when_judgment_not_executable(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot.session_active = True
        bot.current_market = "KR"
        bot.today_judgment = {
            "market": "KR",
            "consensus": {"mode": "HALT", "size": 0},
            "judgments": {},
            "digest_raw": {"context": {}},
            "judgment_context_basis": {"phase": "preopen_watch"},
        }
        bot.today_tickers = {"KR": ["005930"]}
        bot.today_ticker_reasons = {"KR": {}}
        bot.selection_meta = {"KR": {"trade_ready": ["005930"]}}
        bot.trade_ready_tickers = {"KR": ["005930"]}
        bot.price_cache = {}
        bot.price_cache_raw = {}
        bot._session_open_at = {"KR": time.time() - 120}
        bot._session_startup_guard_sec = {"KR": 0}
        bot._pre_session_sell_queue = {"KR": []}
        bot._vix_refresh_at = 0
        bot.pathb = None
        bot.enable_slippage_guard = False
        bot._or_high = {}
        bot._or_low = {}
        bot._or_formed = {}
        bot.risk = SimpleNamespace(
            halt_reason="",
            daily_pnl=0.0,
            positions=[],
            cash=100000.0,
            update_prices=lambda *args, **kwargs: None,
        )
        bot._enter_market_task = lambda market, owner: True
        bot._leave_market_task = Mock()
        bot._refresh_operational_halt = lambda market: None
        bot._has_broker_sync_risk = lambda market: False
        bot._check_market_halt = lambda *args, **kwargs: False
        bot._refresh_claude_control = lambda: None
        bot._consume_pending_claude_trigger = lambda market: None
        bot._consume_pending_position_review = lambda market: None
        bot._consume_pending_sell = lambda market: None
        bot._maybe_refresh_opening_judgment = lambda market: None
        bot._maybe_run_opening_fresh_screener = lambda market: None
        bot._sync_runtime_with_broker = lambda: None
        bot._runtime_gate_state_text = lambda market: "ok"
        bot._us_order_block_reason = lambda ticker: ""
        bot._token_for_market = lambda market: "token"
        bot._price_to_krw = lambda price, market: price
        bot._process_exit_candidates = Mock()
        bot._trade_ready_set = Mock(side_effect=AssertionError("trade_ready gate should not run"))
        bot._write_live_status = Mock()
        bot._maybe_push_dashboard = Mock()

        with patch("trading_bot.get_price", return_value={"price": 70000.0}):
            trading_bot.TradingBot.run_cycle(bot, "KR")

        bot._process_exit_candidates.assert_called_once()
        bot._trade_ready_set.assert_not_called()
        bot._write_live_status.assert_called_once_with("KR")
        bot._leave_market_task.assert_called_with("KR", "run_cycle")

    def test_run_cycle_allows_opening_phase_to_reach_trade_ready_gate(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot.session_active = True
        bot.current_market = "US"
        bot.today_judgment = {
            "market": "US",
            "consensus": {"mode": "DEFENSIVE", "size": 20},
            "judgments": {},
            "digest_raw": {"context": {}},
            "judgment_context_basis": {"phase": "opening_confirm"},
        }
        bot.today_tickers = {"US": ["AAPL"]}
        bot.today_ticker_reasons = {"US": {}}
        bot.selection_meta = {"US": {"trade_ready": []}}
        bot.trade_ready_tickers = {"US": []}
        bot.price_cache = {}
        bot.price_cache_raw = {}
        bot._session_open_at = {"US": time.time() - 120}
        bot._session_startup_guard_sec = {"US": 0}
        bot._pre_session_sell_queue = {"US": []}
        bot._vix_refresh_at = 0
        bot.pathb = None
        bot.enable_slippage_guard = False
        bot._or_high = {}
        bot._or_low = {}
        bot._or_formed = {}
        bot.risk = SimpleNamespace(
            halt_reason="",
            daily_pnl=0.0,
            positions=[],
            cash=100000.0,
            update_prices=lambda *args, **kwargs: None,
        )
        bot._enter_market_task = lambda market, owner: True
        bot._leave_market_task = Mock()
        bot._refresh_operational_halt = lambda market: None
        bot._has_broker_sync_risk = lambda market: False
        bot._check_market_halt = lambda *args, **kwargs: False
        bot._refresh_claude_control = lambda: None
        bot._consume_pending_claude_trigger = lambda market: None
        bot._consume_pending_position_review = lambda market: None
        bot._consume_pending_sell = lambda market: None
        bot._maybe_refresh_opening_judgment = lambda market: None
        bot._maybe_run_opening_fresh_screener = lambda market: None
        bot._sync_runtime_with_broker = lambda: None
        bot._runtime_gate_state_text = lambda market: "ok"
        bot._us_order_block_reason = lambda ticker: ""
        bot._token_for_market = lambda market: "token"
        bot._price_to_krw = lambda price, market: price
        bot._market_elapsed_min = lambda market: 6
        bot._process_exit_candidates = Mock()
        bot._trade_ready_set = Mock(return_value=set())
        bot._is_trade_ready_ticker = Mock(return_value=False)
        bot._watch_only_bucket = lambda market, ticker: "WATCH_ONLY"
        bot._watch_only_reason_text = lambda market, ticker: "not trade ready"
        bot._can_recheck_soft_watch_only = lambda market, ticker, mode: False
        bot._write_live_status = Mock()
        bot._maybe_push_dashboard = Mock()

        with patch("trading_bot.get_price", return_value={"price": 180.0}):
            trading_bot.TradingBot.run_cycle(bot, "US")

        bot._process_exit_candidates.assert_called_once()
        bot._trade_ready_set.assert_called_once_with("US")
        bot._is_trade_ready_ticker.assert_called_once_with("US", "AAPL")
        bot._write_live_status.assert_called_once_with("US")
        bot._leave_market_task.assert_called_with("US", "run_cycle")

    def test_selection_preopen_phase_forces_watch_only_even_if_model_returns_trade_ready(self) -> None:
        from minority_report import analysts as analysts_module

        captured = {}
        response_payload = {
            "watchlist": ["AAPL", "MSFT"],
            "trade_ready": ["AAPL"],
            "reasons": {"AAPL": "strong premarket", "MSFT": "watch"},
            "recommended_strategy": {"AAPL": "momentum"},
            "max_position_pct": {"AAPL": 20},
            "max_order_cap_pct": {"AAPL": 20},
            "risk_budget_pct": {"AAPL": 0.35},
            "price_targets": {
                "AAPL": {
                    "reference_price": 180,
                    "buy_zone_low": 179,
                    "buy_zone_high": 181,
                    "sell_target": 188,
                    "stop_loss": 176,
                    "hold_days": 1,
                    "confidence": 0.7,
                }
            },
        }

        def _fake_create(*, model, max_tokens, messages):
            captured["prompt"] = messages[0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps(response_payload))],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        with patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda *args, **kwargs: None):
            tickers, reasons = analysts_module.select_tickers(
                market="US",
                digest_prompt="preopen digest",
                consensus_mode="PREOPEN_WATCH",
                candidates=[
                    {"ticker": "AAPL", "price": 180.0, "volume": 1000000, "change_rate": 4.0},
                    {"ticker": "MSFT", "price": 420.0, "volume": 900000, "change_rate": 2.0},
                ],
                execution_phase="preopen_watch",
            )

        meta = analysts_module.get_last_selection_meta()
        self.assertEqual(tickers, ["AAPL", "MSFT"])
        self.assertEqual(reasons["AAPL"], "strong premarket")
        self.assertIn("PREOPEN WATCH ONLY", captured["prompt"])
        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["recommended_strategy"], {})
        self.assertEqual(meta["price_targets"], {})


if __name__ == "__main__":
    unittest.main()
