from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile
import unittest

from bot import market_utils
from execution.order_state import OrderUnknownEscalator
import trading_bot


class _FrozenDateTime(datetime):
    fixed: datetime | None = None

    @classmethod
    def now(cls, tz=None):
        value = cls.fixed or datetime(2026, 4, 29, 10, 0, tzinfo=market_utils.KST)
        if tz is not None and value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value


def _bare_bot() -> trading_bot.TradingBot:
    bot = object.__new__(trading_bot.TradingBot)
    bot.pending_orders = []
    bot._save_pending_orders = Mock()
    bot._reconcile_broker_open_orders = Mock(return_value={})
    bot._flag_execution_issue = Mock()
    return bot


class PendingOrderUpsertTests(unittest.TestCase):
    def test_add_pending_order_preserves_distinct_order_numbers_for_same_ticker(self) -> None:
        bot = _bare_bot()
        bot.pending_orders = [
            {"market": "KR", "ticker": "006340", "order_no": "0007603600", "qty": 10}
        ]

        trading_bot.TradingBot._add_pending_order(
            bot,
            {"market": "KR", "ticker": "006340", "order_no": "0008146000", "qty": 10},
        )

        order_nos = sorted(o["order_no"] for o in bot.pending_orders)
        self.assertEqual(order_nos, ["0007603600", "0008146000"])
        self.assertTrue(all(o.get("duplicate_ticker_pending") for o in bot.pending_orders))
        bot._reconcile_broker_open_orders.assert_called_once()
        bot._save_pending_orders.assert_called_once()

    def test_add_pending_order_without_order_no_only_replaces_temporary_row(self) -> None:
        bot = _bare_bot()
        bot.pending_orders = [
            {"market": "KR", "ticker": "047040", "order_no": "0008147800", "qty": 3},
            {"market": "KR", "ticker": "047040", "order_no": "", "qty": 1},
        ]

        trading_bot.TradingBot._add_pending_order(
            bot,
            {"market": "KR", "ticker": "047040", "qty": 2},
        )

        self.assertEqual(len(bot.pending_orders), 2)
        self.assertIn("0008147800", {o.get("order_no", "") for o in bot.pending_orders})
        temp_rows = [o for o in bot.pending_orders if not o.get("order_no")]
        self.assertEqual(len(temp_rows), 1)
        self.assertEqual(temp_rows[0]["qty"], 2)


class OrderUnknownRegistryTests(unittest.TestCase):
    def test_broker_only_open_order_blocks_market_and_tracks_order_no(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = OrderUnknownEscalator(Path(tmp) / "unknown.json")

            state = registry.record_broker_open_order(
                market="KR",
                ticker="006340",
                order_no="0007603600",
                side="buy",
                qty=10,
                remaining_qty=10,
                reason="test",
            )

            self.assertTrue(state["blocked"])
            self.assertTrue(registry.should_block_market("KR"))
            order_state = registry.state["orders"]["KR:0007603600"]
            self.assertTrue(order_state["broker_open"])
            self.assertEqual(order_state["resolution"], "BROKER_ONLY_OPEN_ORDER")

    def test_cancel_requested_and_resolved_are_tracked_by_order_no(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = OrderUnknownEscalator(Path(tmp) / "unknown.json")

            registry.record_cancel_requested(
                market="KR",
                ticker="047040",
                order_no="0008147800",
                qty=3,
                reason="cancel_above_after_ack",
            )
            order_state = registry.state["orders"]["KR:0008147800"]
            self.assertEqual(order_state["resolution"], "CANCEL_REQUESTED")
            self.assertEqual(order_state["cancel_attempts"], 1)
            self.assertTrue(order_state["cancel_requested_at"])

            registry.record_cancel_resolved(
                market="KR",
                ticker="047040",
                order_no="0008147800",
                resolution="CANCEL_CONFIRMED",
            )
            order_state = registry.state["orders"]["KR:0008147800"]
            self.assertEqual(order_state["resolution"], "CANCEL_CONFIRMED")
            self.assertTrue(order_state["resolved_at"])


class EquityReferenceTests(unittest.TestCase):
    def _bot_for_equity(self, market: str, base: float, broker_total: float, *, daily_pnl: float = 0.0):
        bot = object.__new__(trading_bot.TradingBot)
        bot._daily_baseline_by_market = {market: {"session_date": "2026-04-29", "base": base}}
        bot._broker_state = {
            market: {
                "trust_level": "trusted",
                "last_snapshot": {"total_krw": broker_total, "cash_krw": broker_total, "eval_krw": 0},
                "last_trusted_snapshot": {"total_krw": broker_total, "cash_krw": broker_total},
            }
        }
        bot.risk = SimpleNamespace(daily_pnl=daily_pnl, session_start_equity=base, positions=[])
        bot.current_market = market
        bot._recent_sell_proceeds_by_market = {"KR": [], "US": []}
        bot._current_session_date_str = lambda _market: "2026-04-29"
        bot._ticker_market = lambda ticker: "US" if str(ticker).replace(".", "").isalpha() else "KR"
        return bot

    def test_kr_uses_internal_session_equity_when_broker_cash_is_stale(self) -> None:
        bot = self._bot_for_equity("KR", 1_000_000, 1_100_000, daily_pnl=-1_000)
        bot.risk.all_trade_log = [
            {
                "side": "sell",
                "ticker": "006340",
                "qty": 10,
                "pnl": -1_000,
                "reason": "stop_loss",
                "session_date": "2026-04-29",
                "market": "KR",
            }
        ]
        bot.risk.positions = [
            {
                "ticker": "006340",
                "qty": 10,
                "entry": 10_000,
                "current_price": 10_100,
                "entry_session_date": "2026-04-29",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(trading_bot, "DECISIONS_FILE", Path(tmp) / "decisions.jsonl"):
                ctx = trading_bot.TradingBot._market_equity_reference_context(bot, "KR")

        self.assertEqual(ctx["source"], "internal_session_equity")
        self.assertEqual(ctx["total_krw"], 1_000_000)
        self.assertTrue(ctx["lag_suspected"])

    def test_realized_pnl_ignores_other_market_even_when_daily_pnl_is_combined(self) -> None:
        bot = self._bot_for_equity("KR", 1_000_000, 1_000_000, daily_pnl=-99_999)
        bot.risk.all_trade_log = [
            {
                "side": "sell",
                "ticker": "006340",
                "qty": 10,
                "pnl": -1_000,
                "reason": "stop_loss",
                "session_date": "2026-04-29",
                "market": "KR",
            },
            {
                "side": "sell",
                "ticker": "SNAP",
                "qty": 12,
                "pnl": -8_000,
                "reason": "pre_session_sell",
                "session_date": "2026-04-29",
                "market": "US",
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(trading_bot, "DECISIONS_FILE", Path(tmp) / "decisions.jsonl"):
                realized = trading_bot.TradingBot._market_realized_pnl_krw(bot, "KR")

        self.assertEqual(realized, -1_000)

    def test_us_recent_sell_proceeds_adjusts_broker_lag_without_double_count(self) -> None:
        bot = self._bot_for_equity("US", 1_272_940, 1_167_233, daily_pnl=-1_676)
        bot.risk.all_trade_log = []
        trading_bot.TradingBot._note_recent_sell_proceeds(
            bot,
            "US",
            "SNAP",
            order_no="0030639869",
            proceeds_krw=105_530,
            reason="pre_session_sell",
        )

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(trading_bot, "DECISIONS_FILE", Path(tmp) / "decisions.jsonl"):
                ctx = trading_bot.TradingBot._market_equity_reference_context(bot, "US")
                ret = trading_bot.TradingBot._market_daily_return_pct(bot, "US")

        self.assertEqual(ctx["source"], "broker_current_sell_lag_adjusted")
        self.assertEqual(ctx["adjustment_krw"], 105_530)
        self.assertGreater(ret, -1.0)


class NewBuyGateTests(unittest.TestCase):
    def _bot(self) -> trading_bot.TradingBot:
        bot = object.__new__(trading_bot.TradingBot)
        bot.v2 = None
        bot.v2_order_unknown = None
        return bot

    def _with_now(self, hour: int, minute: int):
        _FrozenDateTime.fixed = datetime(2026, 4, 29, hour, minute, tzinfo=market_utils.KST)
        return patch.multiple(
            market_utils,
            datetime=_FrozenDateTime,
        )

    def test_kr_new_buy_blocks_after_blackout_start_before_close(self) -> None:
        bot = self._bot()
        with self._with_now(15, 21), patch.object(trading_bot, "datetime", _FrozenDateTime), patch.dict(
            market_utils.HARD_RULES, {"no_new_entry_min": 10, "close_before_min": 10}
        ):
            state = trading_bot.TradingBot._new_buy_block_state(bot, "KR")

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "ENTRY_BLACKOUT")

    def test_kr_new_buy_blocks_after_orderable_close(self) -> None:
        bot = self._bot()
        with self._with_now(15, 31), patch.object(trading_bot, "datetime", _FrozenDateTime), patch.dict(
            market_utils.HARD_RULES, {"no_new_entry_min": 10, "close_before_min": 10}
        ):
            state = trading_bot.TradingBot._new_buy_block_state(bot, "KR")

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "MARKET_CLOSED")

    def test_order_unknown_market_block_stops_new_buy(self) -> None:
        bot = self._bot()
        bot.v2_order_unknown = SimpleNamespace(
            should_block_global=Mock(return_value=False),
            should_block_market=Mock(return_value=True),
        )
        with self._with_now(10, 0), patch.object(trading_bot, "datetime", _FrozenDateTime), patch.dict(
            market_utils.HARD_RULES, {"no_new_entry_min": 10, "close_before_min": 10}
        ):
            state = trading_bot.TradingBot._new_buy_block_state(bot, "KR")

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "ORDER_UNKNOWN_UNRESOLVED")
        self.assertEqual(state["scope"], "market")


if __name__ == "__main__":
    unittest.main()
