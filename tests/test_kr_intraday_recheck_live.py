from __future__ import annotations

from datetime import datetime, timedelta
import time
import unittest
from unittest.mock import Mock, patch

from trading_bot import KST, TradingBot


class _RuntimeConfig:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.values.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self.values.get(key, default))


class _Risk:
    def __init__(self, positions: list[dict]) -> None:
        self.positions = positions


def _bot(position: dict | None = None) -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.runtime_config = _RuntimeConfig(
        {
            "INTRADAY_REVIEW_CONTEXT_V2_ENABLED": True,
            "KR_INTRADAY_SMALL_LOSS_RECHECK_ENABLED": True,
            "KR_INTRADAY_RECHECK_MIN_PNL": -2.5,
            "KR_INTRADAY_RECHECK_MAX_PNL": 0.0,
            "KR_INTRADAY_RECHECK_MIN_STOP_DISTANCE_PCT": 1.0,
            "KR_INTRADAY_RECHECK_MINUTES_TO_CLOSE": 60.0,
            "KR_INTRADAY_RECHECK_STALE_ALERT_MIN": 90.0,
            "EXECUTION_LEAK_LOG_ENABLED": True,
        }
    )
    bot.risk = _Risk([position] if position is not None else [])
    bot.today_judgment = {"consensus": {"mode": "NEUTRAL"}, "digest_prompt": ""}
    bot.today_ticker_reasons = {"KR": {"005930": "opening range momentum"}}
    bot.selection_meta = {"KR": {}, "US": {}}
    bot.pending_orders = []
    bot.price_cache = {"005930": float((position or {}).get("current_price", 990))}
    bot.price_cache_raw = {"005930": float((position or {}).get("current_price", 990))}
    bot.usd_krw_rate = 1350.0
    bot._or_high = {"005930": 1000.0}
    bot._or_low = {"005930": 960.0}
    bot._or_formed = {"005930": True}
    bot._broker_state = {"KR": {"trust_level": "trusted"}, "US": {"trust_level": "trusted"}}
    bot._intraday_recheck_stale_alert_keys = set()
    bot.session_active = True
    bot.current_market = "KR"
    bot._ticker_market = lambda ticker: "US" if str(ticker).isalpha() else "KR"  # type: ignore[method-assign]
    bot._selection_ticker_key = lambda market, ticker: str(ticker).upper() if market == "US" else str(ticker)  # type: ignore[method-assign]
    bot._current_session_date_str = lambda market: "2026-05-20"  # type: ignore[method-assign]
    bot._minutes_to_close = lambda market: 180.0  # type: ignore[method-assign]
    bot._market_elapsed_min = lambda market: 90.0  # type: ignore[method-assign]
    bot._build_intraday_context = lambda market: ""  # type: ignore[method-assign]
    bot._lookup_ticker_name = lambda ticker, market: ""  # type: ignore[method-assign]
    bot._record_exit_lifecycle_decision = Mock(return_value={})  # type: ignore[method-assign]
    bot._apply_pathb_hold_advice_bridge = Mock()  # type: ignore[method-assign]
    bot._write_live_status = Mock()  # type: ignore[method-assign]
    bot._save_positions = Mock()  # type: ignore[method-assign]
    bot._write_execution_leak_event = Mock()  # type: ignore[method-assign]
    bot._rolling_kr_forward_3d_recent = Mock(
        return_value={"allowed": True, "reason": "ok", "n": 12, "avg_forward_3d": 1.2}
    )  # type: ignore[method-assign]
    bot._execute_sell = Mock(return_value=True)  # type: ignore[method-assign]
    return bot


class KrIntradayRecheckLiveTests(unittest.TestCase):
    def test_advisor_context_v2_adds_or_stop_and_thesis_context(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 995.0,
            "current_price": 990.0,
            "sl": 960.0,
            "selected_reason": "volume breakout",
            "source_type": "signal_entry",
            "entry_route": "plan_a",
            "_fill_ts": time.time() - 3900,
        }
        bot = _bot(pos)

        enriched = TradingBot._advisor_pos(bot, pos, "KR")
        ctx = enriched["advisor_context_v2"]

        self.assertEqual(ctx["selected_reason"], "volume breakout")
        self.assertTrue(ctx["or_formed"])
        self.assertEqual(ctx["or_high"], 1000.0)
        self.assertAlmostEqual(ctx["entry_vs_or_high_pct"], -0.5)
        self.assertAlmostEqual(ctx["hard_stop_distance_pct"], 3.125)
        self.assertIn("hard stop", ctx["invalid_if"])

    def test_recheck_gate_allows_single_kr_small_loss_when_guards_pass(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "current_price": 990.0,
            "sl": 960.0,
            "source_type": "signal_entry",
        }
        bot = _bot(pos)

        gate = TradingBot._kr_intraday_small_loss_recheck_gate(bot, pos, "KR", -1.0, 990.0)

        self.assertTrue(gate["allowed"])
        self.assertEqual(gate["reason"], "small_loss_recheck")
        self.assertGreater(gate["hard_stop_distance_pct"], 1.0)

    def test_recheck_gate_blocks_preopen_watch_source(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "current_price": 990.0,
            "sl": 960.0,
            "source_type": "preopen_watch",
        }
        bot = _bot(pos)

        gate = TradingBot._kr_intraday_small_loss_recheck_gate(bot, pos, "KR", -1.0, 990.0)

        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["reason"], "preopen_watch")

    def test_recheck_gate_blocks_recovery_micro_positions(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "current_price": 990.0,
            "sl": 960.0,
            "strategy": "RECOVERY_MICRO",
        }
        bot = _bot(pos)

        gate = TradingBot._kr_intraday_small_loss_recheck_gate(bot, pos, "KR", -1.0, 990.0)

        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["reason"], "recovery_micro_strategy_no_carry_default")

    def test_stale_recheck_is_expired_on_next_session(self) -> None:
        pos = {
            "ticker": "005930",
            "pending_intraday_recheck": True,
            "pending_intraday_recheck_session": "2026-05-19",
        }
        bot = _bot(pos)

        TradingBot._clear_stale_intraday_recheck_flags(bot, "KR")

        self.assertFalse(pos["pending_intraday_recheck"])
        self.assertEqual(pos["pending_intraday_recheck_status"], "expired_next_session")
        bot._save_positions.assert_called_once()

    def test_intraday_review_defers_first_small_loss_sell_instead_of_executing(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "display_avg_price": 1000.0,
            "current_price": 990.0,
            "display_current_price": 990.0,
            "sl": 960.0,
            "qty": 1,
            "source_type": "signal_entry",
            "_fill_ts": time.time() - 3900,
        }
        bot = _bot(pos)

        with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.8}), patch(
            "trading_bot.block_alert"
        ):
            TradingBot._intraday_position_review(bot, "KR")

        self.assertTrue(pos["pending_intraday_recheck"])
        self.assertTrue(pos["pending_intraday_recheck_used"])
        bot._execute_sell.assert_not_called()
        events = [call.args[2] for call in bot._write_execution_leak_event.call_args_list]
        self.assertIn("recheck_deferred", events)

    def test_intraday_review_pending_sell_after_due_records_recheck_result(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "display_avg_price": 1000.0,
            "current_price": 990.0,
            "display_current_price": 990.0,
            "sl": 960.0,
            "qty": 1,
            "source_type": "signal_entry",
            "pending_intraday_recheck": True,
            "pending_intraday_recheck_used": True,
            "pending_intraday_recheck_pnl_at_review": -1.2,
            "pending_intraday_recheck_due_at": (datetime.now(KST) - timedelta(minutes=1)).isoformat(
                timespec="seconds"
            ),
            "_fill_ts": time.time() - 3900,
        }
        bot = _bot(pos)

        with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.8}), patch(
            "trading_bot.block_alert"
        ):
            TradingBot._intraday_position_review(bot, "KR")

        bot._execute_sell.assert_called_once()
        self.assertFalse(pos["pending_intraday_recheck"])
        self.assertEqual(pos["pending_intraday_recheck_status"], "sell_after_recheck")
        events = [call.args[2] for call in bot._write_execution_leak_event.call_args_list]
        self.assertIn("recheck_result", events)

    def test_intraday_review_second_sell_after_due_executes(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "display_avg_price": 1000.0,
            "current_price": 990.0,
            "display_current_price": 990.0,
            "sl": 960.0,
            "qty": 1,
            "source_type": "signal_entry",
            "pending_intraday_recheck": True,
            "pending_intraday_recheck_used": True,
            "pending_intraday_recheck_pnl_at_review": -1.2,
            "pending_intraday_recheck_due_at": (datetime.now(KST) - timedelta(minutes=1)).isoformat(
                timespec="seconds"
            ),
            "_fill_ts": time.time() - 3900,
        }
        bot = _bot(pos)

        with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.8}), patch(
            "trading_bot.block_alert"
        ):
            TradingBot._intraday_position_review(bot, "KR")

        bot._execute_sell.assert_called_once()
        self.assertFalse(pos["pending_intraday_recheck"])
        self.assertEqual(pos["pending_intraday_recheck_status"], "sell_after_recheck")

    def test_intraday_review_pending_hold_after_due_clears_pending(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "display_avg_price": 1000.0,
            "current_price": 990.0,
            "display_current_price": 990.0,
            "sl": 960.0,
            "qty": 1,
            "source_type": "signal_entry",
            "pending_intraday_recheck": True,
            "pending_intraday_recheck_used": True,
            "pending_intraday_recheck_pnl_at_review": -1.2,
            "pending_intraday_recheck_due_at": (datetime.now(KST) - timedelta(minutes=1)).isoformat(
                timespec="seconds"
            ),
            "_fill_ts": time.time() - 3900,
        }
        bot = _bot(pos)

        with patch("minority_report.hold_advisor.ask", return_value={"action": "HOLD", "confidence": 0.8}), patch(
            "trading_bot.block_alert"
        ):
            TradingBot._intraday_position_review(bot, "KR")

        bot._execute_sell.assert_not_called()
        self.assertFalse(pos["pending_intraday_recheck"])
        self.assertEqual(pos["pending_intraday_recheck_status"], "hold_after_recheck")
        events = [call.args[2] for call in bot._write_execution_leak_event.call_args_list]
        self.assertIn("recheck_result", events)

    def test_intraday_review_pending_sell_before_due_does_not_execute(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "display_avg_price": 1000.0,
            "current_price": 990.0,
            "display_current_price": 990.0,
            "sl": 960.0,
            "qty": 1,
            "source_type": "signal_entry",
            "pending_intraday_recheck": True,
            "pending_intraday_recheck_used": True,
            "pending_intraday_recheck_pnl_at_review": -1.2,
            "pending_intraday_recheck_due_at": (datetime.now(KST) + timedelta(minutes=30)).isoformat(
                timespec="seconds"
            ),
            "_fill_ts": time.time() - 3900,
        }
        bot = _bot(pos)

        with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.8}), patch(
            "trading_bot.block_alert"
        ):
            TradingBot._intraday_position_review(bot, "KR")

        bot._execute_sell.assert_not_called()
        self.assertTrue(pos["pending_intraday_recheck"])
        self.assertNotEqual(pos.get("pending_intraday_recheck_status"), "sell_after_recheck")
        events = [call.args[2] for call in bot._write_execution_leak_event.call_args_list]
        self.assertNotIn("recheck_result", events)

    def test_intraday_review_pending_hold_before_due_does_not_clear_pending(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "display_avg_price": 1000.0,
            "current_price": 990.0,
            "display_current_price": 990.0,
            "sl": 960.0,
            "qty": 1,
            "source_type": "signal_entry",
            "pending_intraday_recheck": True,
            "pending_intraday_recheck_used": True,
            "pending_intraday_recheck_pnl_at_review": -1.2,
            "pending_intraday_recheck_due_at": (datetime.now(KST) + timedelta(minutes=30)).isoformat(
                timespec="seconds"
            ),
            "_fill_ts": time.time() - 3900,
        }
        bot = _bot(pos)

        with patch("minority_report.hold_advisor.ask", return_value={"action": "HOLD", "confidence": 0.8}), patch(
            "trading_bot.block_alert"
        ):
            TradingBot._intraday_position_review(bot, "KR")

        bot._execute_sell.assert_not_called()
        self.assertTrue(pos["pending_intraday_recheck"])
        self.assertNotEqual(pos.get("pending_intraday_recheck_status"), "hold_after_recheck")
        events = [call.args[2] for call in bot._write_execution_leak_event.call_args_list]
        self.assertNotIn("recheck_result", events)

    def test_intraday_review_trail_action_does_not_execute_sell(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "display_avg_price": 1000.0,
            "current_price": 990.0,
            "display_current_price": 990.0,
            "sl": 960.0,
            "qty": 1,
            "source_type": "signal_entry",
            "pending_intraday_recheck": True,
            "pending_intraday_recheck_used": True,
            "pending_intraday_recheck_pnl_at_review": -1.2,
            "pending_intraday_recheck_due_at": (datetime.now(KST) - timedelta(minutes=1)).isoformat(
                timespec="seconds"
            ),
            "_fill_ts": time.time() - 3900,
        }
        bot = _bot(pos)

        with patch("minority_report.hold_advisor.ask", return_value={"action": "TRAIL", "confidence": 0.8}), patch(
            "trading_bot.block_alert"
        ):
            TradingBot._intraday_position_review(bot, "KR")

        bot._execute_sell.assert_not_called()
        self.assertFalse(pos["pending_intraday_recheck"])
        self.assertEqual(pos["pending_intraday_recheck_status"], "hold_after_recheck")
        events = [call.args[2] for call in bot._write_execution_leak_event.call_args_list]
        self.assertIn("recheck_result", events)

    def test_intraday_review_pending_sell_without_due_at_fail_closed_blocks_sell(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "display_avg_price": 1000.0,
            "current_price": 990.0,
            "display_current_price": 990.0,
            "sl": 960.0,
            "qty": 1,
            "source_type": "signal_entry",
            "pending_intraday_recheck": True,
            "pending_intraday_recheck_used": True,
            "pending_intraday_recheck_pnl_at_review": -1.2,
            "_fill_ts": time.time() - 3900,
        }
        bot = _bot(pos)

        with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.8}), patch(
            "trading_bot.block_alert"
        ):
            TradingBot._intraday_position_review(bot, "KR")

        bot._execute_sell.assert_not_called()
        self.assertTrue(pos["pending_intraday_recheck"])
        self.assertNotEqual(pos.get("pending_intraday_recheck_status"), "sell_after_recheck")
        events = [call.args[2] for call in bot._write_execution_leak_event.call_args_list]
        self.assertNotIn("recheck_result", events)

    def test_intraday_recheck_due_state_invalid_due_at_is_fail_closed(self) -> None:
        pos = {
            "ticker": "005930",
            "pending_intraday_recheck": True,
            "pending_intraday_recheck_due_at": "not-a-date",
        }
        bot = _bot(pos)

        state = TradingBot._intraday_recheck_due_state(bot, pos)

        self.assertFalse(state["due"])
        self.assertEqual(state["reason"], "invalid_due_at")
        self.assertEqual(state["due_at"], "not-a-date")

    def test_intraday_recheck_due_state_missing_due_at_is_fail_closed(self) -> None:
        pos = {
            "ticker": "005930",
            "pending_intraday_recheck": True,
        }
        bot = _bot(pos)

        with patch("trading_bot.log.warning") as warning:
            state = TradingBot._intraday_recheck_due_state(bot, pos)

        self.assertFalse(state["due"])
        self.assertEqual(state["reason"], "missing_due_at")
        self.assertEqual(state["due_at"], "")
        warning.assert_called_once()

    def test_intraday_review_skips_recent_position_inside_cooldown(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "display_avg_price": 1000.0,
            "current_price": 994.0,
            "display_current_price": 994.0,
            "sl": 900.0,
            "qty": 1,
            "source_type": "signal_entry",
            "intraday_review_session": "2026-05-20",
            "intraday_review_count": 1,
            "intraday_review_last_at": (datetime.now(KST) - timedelta(minutes=30)).isoformat(timespec="seconds"),
            "intraday_review_last_pnl_pct": -0.5,
            "_fill_ts": time.time() - 7200,
        }
        bot = _bot(pos)

        gate = TradingBot._intraday_review_gate(bot, pos, "KR", force=False, pnl_pct=-0.6, current_native=994.0)
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["reason"], "skipped_cooldown_regular")

        with patch("minority_report.hold_advisor.ask") as advisor_ask, patch("trading_bot.block_alert"):
            TradingBot._intraday_position_review(bot, "KR")

        advisor_ask.assert_not_called()
        bot._execute_sell.assert_not_called()
        self.assertEqual(pos["intraday_review_count"], 1)

    def test_intraday_review_daily_max_blocks_regular_reask(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "display_avg_price": 1000.0,
            "current_price": 994.0,
            "display_current_price": 994.0,
            "sl": 900.0,
            "qty": 1,
            "source_type": "signal_entry",
            "intraday_review_session": "2026-05-20",
            "intraday_review_count": 3,
            "intraday_review_last_at": (datetime.now(KST) - timedelta(hours=3)).isoformat(timespec="seconds"),
            "intraday_review_last_pnl_pct": -0.5,
            "_fill_ts": time.time() - 7200,
        }
        bot = _bot(pos)

        gate = TradingBot._intraday_review_gate(bot, pos, "KR", force=False, pnl_pct=-0.6, current_native=994.0)
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["reason"], "skipped_daily_max_regular")

        with patch("minority_report.hold_advisor.ask") as advisor_ask, patch("trading_bot.block_alert"):
            TradingBot._intraday_position_review(bot, "KR")

        advisor_ask.assert_not_called()
        bot._execute_sell.assert_not_called()
        self.assertEqual(pos["intraday_review_count"], 3)

    def test_intraday_review_material_pnl_change_bypasses_cooldown_before_daily_max(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "display_avg_price": 1000.0,
            "current_price": 985.0,
            "display_current_price": 985.0,
            "sl": 900.0,
            "qty": 1,
            "source_type": "signal_entry",
            "intraday_review_session": "2026-05-20",
            "intraday_review_count": 1,
            "intraday_review_last_at": (datetime.now(KST) - timedelta(minutes=30)).isoformat(timespec="seconds"),
            "intraday_review_last_pnl_pct": -0.1,
            "_fill_ts": time.time() - 7200,
        }
        bot = _bot(pos)

        with patch("minority_report.hold_advisor.ask", return_value={"action": "HOLD", "confidence": 0.8}) as advisor_ask, patch(
            "trading_bot.block_alert"
        ):
            TradingBot._intraday_position_review(bot, "KR")

        advisor_ask.assert_called_once()
        bot._execute_sell.assert_not_called()
        self.assertEqual(pos["intraday_review_count"], 2)

    def test_intraday_review_material_pnl_change_bypasses_daily_max(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "display_avg_price": 1000.0,
            "current_price": 985.0,
            "display_current_price": 985.0,
            "sl": 900.0,
            "qty": 1,
            "source_type": "signal_entry",
            "intraday_review_session": "2026-05-20",
            "intraday_review_count": 3,
            "intraday_review_last_at": (datetime.now(KST) - timedelta(minutes=30)).isoformat(timespec="seconds"),
            "intraday_review_last_pnl_pct": -0.1,
            "_fill_ts": time.time() - 7200,
        }
        bot = _bot(pos)

        gate = TradingBot._intraday_review_gate(bot, pos, "KR", force=False, pnl_pct=-1.5, current_native=985.0)
        self.assertTrue(gate["allowed"])
        self.assertEqual(gate["reason"], "bypassed_daily_max_material_pnl_change")

        with patch("minority_report.hold_advisor.ask", return_value={"action": "HOLD", "confidence": 0.8}) as advisor_ask, patch(
            "trading_bot.block_alert"
        ):
            TradingBot._intraday_position_review(bot, "KR")

        advisor_ask.assert_called_once()
        bot._execute_sell.assert_not_called()
        self.assertEqual(pos["intraday_review_count"], 4)

    def test_intraday_review_near_hard_stop_bypasses_daily_max(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "display_avg_price": 1000.0,
            "current_price": 961.0,
            "display_current_price": 961.0,
            "sl": 960.0,
            "qty": 1,
            "source_type": "signal_entry",
            "intraday_review_session": "2026-05-20",
            "intraday_review_count": 3,
            "intraday_review_last_at": (datetime.now(KST) - timedelta(minutes=30)).isoformat(timespec="seconds"),
            "intraday_review_last_pnl_pct": -3.9,
            "_fill_ts": time.time() - 7200,
        }
        bot = _bot(pos)

        gate = TradingBot._intraday_review_gate(bot, pos, "KR", force=False, pnl_pct=-3.9, current_native=961.0)
        self.assertTrue(gate["allowed"])
        self.assertEqual(gate["reason"], "bypassed_daily_max_near_stop")

        with patch("minority_report.hold_advisor.ask", return_value={"action": "HOLD", "confidence": 0.8}) as advisor_ask, patch(
            "trading_bot.block_alert"
        ):
            TradingBot._intraday_position_review(bot, "KR")

        advisor_ask.assert_called_once()
        bot._execute_sell.assert_not_called()
        self.assertEqual(pos["intraday_review_count"], 4)

    def test_intraday_review_invalid_count_is_reset_without_blocking_review(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "display_avg_price": 1000.0,
            "current_price": 994.0,
            "display_current_price": 994.0,
            "sl": 900.0,
            "qty": 1,
            "source_type": "signal_entry",
            "intraday_review_session": "2026-05-20",
            "intraday_review_count": "not-a-number",
            "intraday_review_last_at": (datetime.now(KST) - timedelta(minutes=30)).isoformat(timespec="seconds"),
            "intraday_review_last_pnl_pct": -0.5,
            "_fill_ts": time.time() - 7200,
        }
        bot = _bot(pos)

        with patch("minority_report.hold_advisor.ask", return_value={"action": "HOLD", "confidence": 0.8}) as advisor_ask, patch(
            "trading_bot.block_alert"
        ):
            TradingBot._intraday_position_review(bot, "KR")

        advisor_ask.assert_called_once()
        bot._execute_sell.assert_not_called()
        self.assertEqual(pos["intraday_review_count"], 1)

    def test_intraday_review_pending_recheck_due_bypasses_daily_max(self) -> None:
        pos = {
            "ticker": "005930",
            "entry": 1000.0,
            "display_avg_price": 1000.0,
            "current_price": 990.0,
            "display_current_price": 990.0,
            "sl": 960.0,
            "qty": 1,
            "source_type": "signal_entry",
            "pending_intraday_recheck": True,
            "pending_intraday_recheck_used": True,
            "pending_intraday_recheck_pnl_at_review": -1.2,
            "pending_intraday_recheck_due_at": (datetime.now(KST) - timedelta(minutes=1)).isoformat(
                timespec="seconds"
            ),
            "intraday_review_session": "2026-05-20",
            "intraday_review_count": 3,
            "intraday_review_last_at": (datetime.now(KST) - timedelta(hours=3)).isoformat(timespec="seconds"),
            "intraday_review_last_pnl_pct": -1.2,
            "_fill_ts": time.time() - 7200,
        }
        bot = _bot(pos)

        gate = TradingBot._intraday_review_gate(bot, pos, "KR", force=False, pnl_pct=-1.0, current_native=990.0)
        self.assertTrue(gate["allowed"])
        self.assertEqual(gate["reason"], "bypassed_daily_max_pending_due")

        with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.8}) as advisor_ask, patch(
            "trading_bot.block_alert"
        ):
            TradingBot._intraday_position_review(bot, "KR")

        advisor_ask.assert_called_once()
        bot._execute_sell.assert_called_once()
        self.assertFalse(pos["pending_intraday_recheck"])
        self.assertEqual(pos["pending_intraday_recheck_status"], "sell_after_recheck")


if __name__ == "__main__":
    unittest.main()
