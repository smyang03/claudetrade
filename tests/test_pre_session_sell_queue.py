from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from trading_bot import TradingBot


class _Risk:
    def __init__(self) -> None:
        self.positions = [
            {
                "ticker": "QCOM",
                "pending_next_open_sell": True,
                "pending_next_open_sell_attempt_error": "old",
            }
        ]


class PreSessionSellQueueTests(unittest.TestCase):
    def _bot(self) -> TradingBot:
        bot = TradingBot.__new__(TradingBot)
        bot.risk = _Risk()
        bot.price_cache = {}
        bot.price_cache_raw = {}
        bot._sell_fail_meta = {}
        return bot

    def test_failed_pre_session_sell_keeps_retry_flag(self) -> None:
        bot = self._bot()

        bot._record_pre_session_sell_result("QCOM", ok=False)

        pos = bot.risk.positions[0]
        self.assertTrue(pos["pending_next_open_sell"])
        self.assertTrue(pos["pending_next_open_sell_retry_needed"])
        self.assertEqual(pos["pending_next_open_sell_attempt_status"], "failed")

    def test_exception_pre_session_sell_keeps_retry_flag_and_error(self) -> None:
        bot = self._bot()

        bot._record_pre_session_sell_result(
            "QCOM",
            ok=False,
            error="network timeout",
            cause="exception",
            detail="network timeout",
            price_source="entry_fallback",
            attempted_price=100.0,
        )

        pos = bot.risk.positions[0]
        self.assertTrue(pos["pending_next_open_sell"])
        self.assertTrue(pos["pending_next_open_sell_retry_needed"])
        self.assertEqual(pos["pending_next_open_sell_attempt_status"], "failed_exception")
        self.assertEqual(pos["pending_next_open_sell_attempt_error"], "network timeout")
        self.assertEqual(pos["pending_next_open_sell_attempt_cause"], "exception")
        self.assertEqual(pos["pending_next_open_sell_attempt_detail"], "network timeout")
        self.assertEqual(pos["pending_next_open_sell_price_source"], "entry_fallback")
        self.assertEqual(pos["pending_next_open_sell_attempt_price"], 100.0)

    def test_successful_pre_session_sell_clears_retry_flag(self) -> None:
        bot = self._bot()

        bot._record_pre_session_sell_result("QCOM", ok=True)

        pos = bot.risk.positions[0]
        self.assertFalse(pos["pending_next_open_sell"])
        self.assertEqual(pos["pending_next_open_sell_attempt_status"], "sent")
        self.assertNotIn("pending_next_open_sell_retry_needed", pos)
        self.assertNotIn("pending_next_open_sell_attempt_error", pos)

    def test_pre_session_sell_price_context_preserves_existing_priority(self) -> None:
        bot = self._bot()
        bot.price_cache["QCOM"] = 101.0
        bot.price_cache_raw["QCOM"] = 99.0
        pos = {"ticker": "QCOM", "current_price": 100.0, "entry": 90.0}

        ctx = bot._pre_session_sell_price_context("QCOM", pos, "US")

        self.assertEqual(ctx["price"], 101.0)
        self.assertEqual(ctx["source"], "price_cache")
        self.assertEqual(ctx["raw_price"], 99.0)

    def test_pre_session_sell_classifies_review_hold_and_broker_failure(self) -> None:
        bot = self._bot()

        cause, detail = bot._classify_pre_session_sell_result(
            "QCOM",
            ok=False,
            cand={"exit_price": 100.0, "auto_sell_review_action": "HOLD", "auto_sell_review_detail": "stale"},
            price_context={"source": "price_cache", "price": 100.0},
        )
        self.assertEqual(cause, "auto_sell_review_hold")
        self.assertEqual(detail, "stale")

        bot._sell_fail_meta["QCOM"] = {"sig": "pre_session_sell|precheck_failed"}
        cause, detail = bot._classify_pre_session_sell_result(
            "QCOM",
            ok=False,
            cand={"exit_price": 100.0},
            price_context={"source": "price_cache", "price": 100.0},
        )
        self.assertEqual(cause, "broker_or_order_failure")
        self.assertEqual(detail, "pre_session_sell|precheck_failed")

    def test_preopen_position_review_does_not_create_sell_queue_or_ask_advisor(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.risk = type(
            "Risk",
            (),
            {
                "positions": [
                    {"ticker": "QCOM", "pending_next_open_sell": True},
                    {"ticker": "TEVA", "entry": 35.0},
                ]
            },
        )()
        bot._pre_session_sell_queue = {"US": [{"ticker": "QCOM"}]}
        bot._current_judgment_phase = lambda market: "preopen_watch"  # type: ignore[method-assign]
        bot._ticker_market = lambda ticker: "US"  # type: ignore[method-assign]
        bot._current_session_date_str = lambda market: "2026-05-04"  # type: ignore[method-assign]
        bot._save_positions = Mock()  # type: ignore[method-assign]

        with patch("minority_report.hold_advisor.ask") as advisor:
            bot._pre_session_position_review("US")

        advisor.assert_not_called()
        self.assertEqual(bot._pre_session_sell_queue["US"], [])
        self.assertEqual(
            bot.risk.positions[0]["pending_next_open_sell_recheck_status"],
            "waiting_open",
        )
        bot._save_positions.assert_called_once()

    def test_legacy_pre_session_sell_queue_is_deferred_not_executed(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.risk = type("Risk", (), {"positions": [{"ticker": "QCOM"}]})()
        bot._pre_session_sell_queue = {
            "US": [
                {
                    "ticker": "QCOM",
                    "pending_next_open_reason": "prior close sell",
                    "hold_advice": {"action": "SELL"},
                }
            ]
        }
        bot._current_session_date_str = lambda market: "2026-05-04"  # type: ignore[method-assign]
        bot._save_positions = Mock()  # type: ignore[method-assign]

        count = bot._defer_pre_session_sell_queue_for_opening_recheck("US", cause="test")

        pos = bot.risk.positions[0]
        self.assertEqual(count, 1)
        self.assertEqual(bot._pre_session_sell_queue["US"], [])
        self.assertTrue(pos["pending_next_open_sell"])
        self.assertEqual(pos["pending_next_open_sell_recheck_status"], "needs_opening_recheck")
        self.assertEqual(pos["pending_next_open_sell_recheck_cause"], "test")
        self.assertEqual(pos["pending_next_open_reason"], "prior close sell")
        self.assertEqual(pos["hold_advice"], {"action": "SELL"})
        bot._save_positions.assert_called_once()

    def test_pending_next_open_sell_recheck_waits_for_opening_refresh(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.session_active = True
        bot.current_market = "US"
        bot.risk = type("Risk", (), {"positions": [{"ticker": "QCOM", "pending_next_open_sell": True}]})()
        bot._current_judgment_phase = lambda market: "opening_confirm"  # type: ignore[method-assign]
        bot._is_executable_judgment_phase = lambda phase: True  # type: ignore[method-assign]
        bot._market_after_open_refresh_time = lambda market: False  # type: ignore[method-assign]
        bot._ticker_market = lambda ticker: "US"  # type: ignore[method-assign]
        bot._intraday_position_review = Mock()  # type: ignore[method-assign]

        bot._maybe_recheck_pending_next_open_sells("US")

        bot._intraday_position_review.assert_not_called()

    def test_pending_next_open_sell_recheck_uses_intraday_review_after_open(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.session_active = True
        bot.current_market = "US"
        bot.risk = type("Risk", (), {"positions": [{"ticker": "QCOM", "pending_next_open_sell": True}]})()
        bot._current_judgment_phase = lambda market: "opening_confirm"  # type: ignore[method-assign]
        bot._is_executable_judgment_phase = lambda phase: True  # type: ignore[method-assign]
        bot._market_after_open_refresh_time = lambda market: True  # type: ignore[method-assign]
        bot._current_session_date_str = lambda market: "2026-05-04"  # type: ignore[method-assign]
        bot._ticker_market = lambda ticker: "US"  # type: ignore[method-assign]
        bot._intraday_position_review = Mock()  # type: ignore[method-assign]
        bot._save_positions = Mock()  # type: ignore[method-assign]

        bot._maybe_recheck_pending_next_open_sells("US")

        bot._intraday_position_review.assert_called_once_with("US", force=True, ticker_filter="QCOM")
        self.assertEqual(bot.risk.positions[0]["pending_next_open_sell_recheck_status"], "reviewing")
        bot._save_positions.assert_called_once()

    def test_max_hold_final_flag_still_requires_claude_sell_decision(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        pos = {"ticker": "QCOM", "entry": 100.0, "qty": 1, "max_hold": 2}
        bot.risk = type("Risk", (), {"positions": [pos]})()
        bot.today_judgment = {"digest_prompt": ""}
        bot._build_intraday_context = lambda market: ""  # type: ignore[method-assign]
        bot._advisor_pos = lambda cand, market: cand  # type: ignore[method-assign]
        bot._save_positions = Mock()  # type: ignore[method-assign]
        bot._execute_sell = Mock()  # type: ignore[method-assign]
        cand = {
            **pos,
            "held_days": 2,
            "pnl_pct": 1.0,
            "exit_price": 101.0,
            "max_hold_extended": True,
            "max_hold_final": True,
        }

        with patch("minority_report.hold_advisor.ask", return_value={"action": "HOLD", "votes": {}}), patch(
            "telegram_reporter.send"
        ):
            bot._handle_max_hold_claude(cand, "US")

        bot._execute_sell.assert_not_called()
        self.assertEqual(pos["max_hold"], 2)
        self.assertFalse(pos["max_hold_final"])

    def test_max_hold_advisor_failure_holds_instead_of_selling(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        pos = {"ticker": "QCOM", "entry": 100.0, "qty": 1, "max_hold": 1}
        bot.risk = type("Risk", (), {"positions": [pos]})()
        bot.today_judgment = {"digest_prompt": ""}
        bot._build_intraday_context = lambda market: ""  # type: ignore[method-assign]
        bot._advisor_pos = lambda cand, market: cand  # type: ignore[method-assign]
        bot._save_positions = Mock()  # type: ignore[method-assign]
        bot._execute_sell = Mock()  # type: ignore[method-assign]
        cand = {**pos, "held_days": 1, "pnl_pct": 1.0, "exit_price": 101.0}

        with patch("minority_report.hold_advisor.ask", side_effect=RuntimeError("timeout")), patch(
            "telegram_reporter.send"
        ):
            bot._handle_max_hold_claude(cand, "US")

        bot._execute_sell.assert_not_called()
        self.assertEqual(pos["max_hold"], 1)

    def test_max_hold_sell_decision_does_not_sell_when_max_hold_disabled(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        pos = {"ticker": "QCOM", "entry": 100.0, "qty": 1, "max_hold": 1}
        bot.risk = type("Risk", (), {"positions": [pos]})()
        bot.today_judgment = {"digest_prompt": ""}
        bot._build_intraday_context = lambda market: ""  # type: ignore[method-assign]
        bot._advisor_pos = lambda cand, market: cand  # type: ignore[method-assign]
        bot._save_positions = Mock()  # type: ignore[method-assign]
        bot._execute_sell = Mock()  # type: ignore[method-assign]
        cand = {**pos, "held_days": 30, "pnl_pct": 1.0, "exit_price": 101.0}

        with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "votes": {}}), patch(
            "telegram_reporter.send"
        ):
            bot._handle_max_hold_claude(cand, "US")

        bot._execute_sell.assert_not_called()
        self.assertTrue(pos["max_hold_sell_ignored"])


if __name__ == "__main__":
    unittest.main()
