from __future__ import annotations

import json
import time
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import trading_bot
from bot.session_date import KST


class PreopenOpeningRoleSeparationTests(unittest.TestCase):
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
        self.assertEqual(result["max_gross_exposure_pct"], 25)
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
