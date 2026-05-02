from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import trading_bot


class _PathB:
    def __init__(self) -> None:
        self.calls = []

    def on_buy_fill(self, order, *, position, partial):
        self.calls.append((dict(order), dict(position), bool(partial)))


def _bot_with_pending(order: dict) -> SimpleNamespace:
    bot = SimpleNamespace()
    bot.pending_orders = [order]
    bot.risk = SimpleNamespace(positions=[], trade_log=[], all_trade_log=[])
    bot.token = "token"
    bot.price_cache_raw = {}
    bot.price_cache = {}
    bot.usd_krw_rate = 1300.0
    bot.v2_partial_fill_policy = None
    bot.pathb = _PathB()
    bot._funnel = {"KR": {"filled": 0}, "US": {"filled": 0}}
    bot._ticker_market = lambda ticker: "US" if str(ticker).replace(".", "").isalpha() else "KR"
    bot._local_filled_qty_for_order = (
        lambda market, ticker, order_no: trading_bot.TradingBot._local_filled_qty_for_order(
            bot,
            market,
            ticker,
            order_no,
        )
    )
    bot._make_position_from_broker = lambda fill_order, broker_pos: {
        "market": fill_order.get("market", "KR"),
        "ticker": fill_order.get("ticker", ""),
        "qty": int(fill_order.get("qty", 0) or 0),
        "entry": float(broker_pos.get("avg_price", 0) or 0),
        "order_no": fill_order.get("order_no", ""),
        "v2_execution_id": fill_order.get("v2_execution_id", fill_order.get("order_no", "")),
        "position_id": "pos1",
    }
    bot._entry_timing_filled = Mock()
    bot._v2_record_lifecycle_event = Mock()
    bot._v2_record_order_unknown = Mock()
    bot._save_positions = Mock()
    bot._save_pending_orders = Mock()
    bot._current_session_date_str = lambda market: "2026-04-29"
    return bot


class LiveOrderReconciliationTests(unittest.TestCase):
    def test_partial_fill_persists_pending_and_passes_filled_qty_to_pathb(self) -> None:
        bot = _bot_with_pending(
            {
                "market": "KR",
                "ticker": "005930",
                "qty": 10,
                "order_no": "order1",
                "raw_price": 70000,
                "pathb_path_run_id": "pathb1",
            }
        )

        with patch(
            "trading_bot.get_order_fill_kr",
            return_value={"filled_qty": 3, "fill_price": 70100, "order_time": "093000"},
        ):
            trading_bot.TradingBot._reconcile_pending_orders(bot, broker_kr={}, broker_us={})

        self.assertEqual(bot.risk.positions[0]["qty"], 3)
        self.assertEqual(bot.pending_orders[0]["qty"], 7)
        self.assertEqual(bot.pending_orders[0]["filled_qty_accum"], 3)
        bot._save_positions.assert_called_once()
        bot._save_pending_orders.assert_called_once()
        self.assertEqual(bot.pathb.calls[0][0]["qty"], 3)
        self.assertTrue(bot.pathb.calls[0][2])

    def test_rest_cumulative_partial_does_not_duplicate_already_filled_qty(self) -> None:
        bot = _bot_with_pending(
            {
                "market": "KR",
                "ticker": "005930",
                "qty": 7,
                "order_no": "order1",
                "raw_price": 70000,
                "filled_qty_accum": 3,
                "filled_price_native": 70100,
                "partial_fill_at": "2026-04-29T09:30:00+09:00",
            }
        )
        bot.risk.positions.append(
            {
                "market": "KR",
                "ticker": "005930",
                "qty": 3,
                "entry": 70100,
                "order_no": "order1",
                "v2_execution_id": "order1",
            }
        )

        with patch(
            "trading_bot.get_order_fill_kr",
            return_value={"filled_qty": 3, "fill_price": 70100, "order_time": "093000"},
        ):
            trading_bot.TradingBot._reconcile_pending_orders(bot, broker_kr={}, broker_us={})

        self.assertEqual(len(bot.risk.positions), 1)
        self.assertEqual(bot.pending_orders[0]["qty"], 7)
        bot._save_positions.assert_not_called()

    def test_rest_cumulative_full_after_partial_applies_only_remaining_qty(self) -> None:
        bot = _bot_with_pending(
            {
                "market": "KR",
                "ticker": "005930",
                "qty": 7,
                "order_no": "order1",
                "raw_price": 70000,
                "filled_qty_accum": 3,
                "filled_price_native": 70100,
                "partial_fill_at": "2026-04-29T09:30:00+09:00",
            }
        )
        bot.risk.positions.append(
            {
                "market": "KR",
                "ticker": "005930",
                "qty": 3,
                "entry": 70100,
                "order_no": "order1",
                "v2_execution_id": "order1",
            }
        )

        with patch(
            "trading_bot.get_order_fill_kr",
            return_value={"filled_qty": 10, "fill_price": 70100, "order_time": "093500"},
        ), patch.object(trading_bot, "fill_confirm_alert"):
            trading_bot.TradingBot._reconcile_pending_orders(bot, broker_kr={}, broker_us={})

        self.assertEqual([pos["qty"] for pos in bot.risk.positions], [3, 7])
        self.assertEqual(bot.pending_orders, [])
        bot._save_positions.assert_called_once()
        bot._save_pending_orders.assert_called_once()

    def test_partial_ttl_unknown_state_is_persisted(self) -> None:
        bot = _bot_with_pending(
            {
                "market": "KR",
                "ticker": "005930",
                "qty": 7,
                "order_no": "order1",
                "raw_price": 70000,
                "partial_fill_at": "2000-01-01T00:00:00+09:00",
                "partial_fill_ttl_sec": 1,
            }
        )

        with patch("trading_bot.get_order_fill_kr", return_value=None):
            trading_bot.TradingBot._reconcile_pending_orders(bot, broker_kr={}, broker_us={})

        self.assertTrue(bot.pending_orders[0]["v2_unknown_recorded"])
        bot._v2_record_order_unknown.assert_called_once()
        bot._save_pending_orders.assert_called_once()
        bot._save_positions.assert_not_called()


if __name__ == "__main__":
    unittest.main()
