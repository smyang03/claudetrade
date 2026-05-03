from __future__ import annotations

import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import kis_api
import trading_bot


def _payload(cols: list[str], values: dict[str, str]) -> str:
    row = {col: "" for col in cols}
    row.update(values)
    return "^".join(row[col] for col in cols)


def _bot_with_pending(order: dict) -> SimpleNamespace:
    bot = SimpleNamespace()
    bot.pending_orders = [order]
    bot.risk = SimpleNamespace(positions=[])
    bot.usd_krw_rate = 1300.0
    bot.v2_partial_fill_policy = None
    bot.pathb = SimpleNamespace(on_buy_fill=Mock())
    bot._funnel = {"KR": {"filled": 0}, "US": {"filled": 0}}
    bot._make_position_from_broker = lambda fill_order, broker_pos: {
        "market": fill_order.get("market", "KR"),
        "ticker": fill_order.get("ticker", ""),
        "qty": int(fill_order.get("qty", 0) or 0),
        "entry": float(broker_pos.get("avg_price", 0) or 0),
        "position_id": "pos1",
    }
    bot._entry_timing_filled = Mock()
    bot._v2_record_lifecycle_event = Mock()
    bot._save_pending_orders = Mock()
    bot._save_positions = Mock()
    return bot


class KisWebSocketNoticeParserTests(unittest.TestCase):
    def _notice_subscription_tr_ids(self, market: str) -> list[str]:
        sent: list[str] = []

        class FakeWebSocketApp:
            instances = []

            def __init__(self, url, on_open=None, on_message=None, on_error=None, on_close=None):
                self.url = url
                self.on_open = on_open
                self.on_message = on_message
                self.on_error = on_error
                self.on_close = on_close
                self.closed = False
                FakeWebSocketApp.instances.append(self)

            def send(self, msg):
                sent.append(msg)

            def run_forever(self):
                return None

            def close(self):
                self.closed = True
                return None

        class FakeThread:
            def __init__(self, target=None, daemon=None):
                self.target = target
                self.daemon = daemon

            def start(self):
                return None

        fake_websocket = SimpleNamespace(WebSocketApp=FakeWebSocketApp)
        with (
            patch.dict(sys.modules, {"websocket": fake_websocket}),
            patch.object(kis_api.KISWebSocket, "_get_ws_key", return_value="approval"),
            patch.object(kis_api.threading, "Thread", FakeThread),
        ):
            ws = kis_api.KISWebSocket("token", [], on_notice=Mock(), market=market)
            ws._hts_id = "HTS"
            ws.start()
            FakeWebSocketApp.instances[0].on_open(FakeWebSocketApp.instances[0])

        return [json.loads(msg)["body"]["input"]["tr_id"] for msg in sent]

    def test_kr_websocket_subscribes_only_kr_notice(self) -> None:
        tr_ids = self._notice_subscription_tr_ids("KR")

        self.assertTrue(any(tr_id in {"H0STCNI9", "H0STCNI0"} for tr_id in tr_ids))
        self.assertFalse(any(tr_id in {"H0GSCNI9", "H0GSCNI0"} for tr_id in tr_ids))

    def test_us_websocket_subscribes_only_us_notice(self) -> None:
        tr_ids = self._notice_subscription_tr_ids("US")

        self.assertTrue(any(tr_id in {"H0GSCNI9", "H0GSCNI0"} for tr_id in tr_ids))
        self.assertFalse(any(tr_id in {"H0STCNI9", "H0STCNI0"} for tr_id in tr_ids))

    def test_websocket_status_tracks_start_error_close_and_stop(self) -> None:
        class FakeWebSocketApp:
            instances = []

            def __init__(self, url, on_open=None, on_message=None, on_error=None, on_close=None):
                self.url = url
                self.on_open = on_open
                self.on_message = on_message
                self.on_error = on_error
                self.on_close = on_close
                self.closed = False
                FakeWebSocketApp.instances.append(self)

            def run_forever(self):
                return None

            def close(self):
                self.closed = True
                if self.on_close:
                    self.on_close(self)

        class FakeThread:
            def __init__(self, target=None, daemon=None):
                self.target = target
                self.daemon = daemon

            def start(self):
                return None

        fake_websocket = SimpleNamespace(WebSocketApp=FakeWebSocketApp)
        with (
            patch.dict(sys.modules, {"websocket": fake_websocket}),
            patch.object(kis_api.KISWebSocket, "_get_ws_key", return_value="approval"),
            patch.object(kis_api.threading, "Thread", FakeThread),
        ):
            ws = kis_api.KISWebSocket("token", [], market="KR")
            ws.start()
            self.assertTrue(ws.running)
            self.assertTrue(ws.started_at)
            FakeWebSocketApp.instances[0].on_error(FakeWebSocketApp.instances[0], RuntimeError("boom"))
            self.assertFalse(ws.running)
            self.assertIn("boom", ws.last_error)
            ws.stop()
            self.assertFalse(ws.running)
            self.assertTrue(FakeWebSocketApp.instances[0].closed)

    def test_parse_kr_fill_notice(self) -> None:
        ws = kis_api.KISWebSocket("token", [], market="KR")
        raw = _payload(
            kis_api._NOTICE_COLS_KR,
            {
                "ODER_NO": "ord-kr-1",
                "SELN_BYOV_CLS": "2",
                "STCK_SHRN_ISCD": "005930",
                "CNTG_QTY": "3",
                "CNTG_UNPR": "70100",
                "STCK_CNTG_HOUR": "093000",
                "CNTG_YN": "2",
            },
        )

        event = ws._parse_notice(raw, market="KR")

        self.assertEqual(event["order_no"], "ord-kr-1")
        self.assertEqual(event["ticker"], "005930")
        self.assertEqual(event["filled_qty"], 3)
        self.assertEqual(event["filled_price"], 70100.0)
        self.assertEqual(event["filled_time"], "093000")
        self.assertEqual(event["side"], "buy")
        self.assertEqual(event["market"], "KR")
        self.assertTrue(event["raw_hash"])

    def test_parse_us_fill_notice_uses_decimal_price(self) -> None:
        ws = kis_api.KISWebSocket("token", [], market="US")
        raw = _payload(
            kis_api._NOTICE_COLS_US,
            {
                "ODER_NO": "ord-us-1",
                "SELN_BYOV_CLS": "2",
                "STCK_SHRN_ISCD": "AAPL",
                "CNTG_QTY": "2",
                "CNTG_UNPR": "180",
                "CNTG_UNPR12": "180.25",
                "STCK_CNTG_HOUR": "093001",
                "CNTG_YN": "2",
            },
        )

        event = ws._parse_notice(raw, market="US")

        self.assertEqual(event["order_no"], "ord-us-1")
        self.assertEqual(event["ticker"], "AAPL")
        self.assertEqual(event["filled_qty"], 2)
        self.assertEqual(event["filled_price"], 180.25)
        self.assertEqual(event["side"], "buy")
        self.assertEqual(event["market"], "US")

    def test_notice_parser_ignores_non_fill_duplicate_and_malformed_rows(self) -> None:
        ws = kis_api.KISWebSocket("token", [], market="KR")
        accepted = _payload(
            kis_api._NOTICE_COLS_KR,
            {
                "ODER_NO": "ord-kr-2",
                "SELN_BYOV_CLS": "2",
                "STCK_SHRN_ISCD": "005930",
                "CNTG_QTY": "1",
                "CNTG_UNPR": "70200",
                "STCK_CNTG_HOUR": "093010",
                "CNTG_YN": "2",
            },
        )
        non_fill = _payload(
            kis_api._NOTICE_COLS_KR,
            {
                "ODER_NO": "ord-kr-3",
                "CNTG_QTY": "1",
                "CNTG_UNPR": "70200",
                "STCK_CNTG_HOUR": "093011",
                "CNTG_YN": "1",
            },
        )

        self.assertIsNotNone(ws._parse_notice(accepted, market="KR"))
        self.assertIsNone(ws._parse_notice(accepted, market="KR"))
        self.assertIsNone(ws._parse_notice(non_fill, market="KR"))
        self.assertIsNone(ws._parse_notice("too^short", market="KR"))


class TradingBotFillNoticeTests(unittest.TestCase):
    def test_full_buy_fill_removes_pending_and_records_position(self) -> None:
        bot = _bot_with_pending(
            {
                "market": "KR",
                "ticker": "005930",
                "qty": 5,
                "order_no": "ord-full",
                "raw_price": 70000,
            }
        )

        with patch.object(trading_bot, "fill_confirm_alert") as alert:
            trading_bot.TradingBot._on_fill_notice(
                bot,
                {
                    "order_no": "ord-full",
                    "ticker": "005930",
                    "filled_qty": 5,
                    "filled_price": 70100,
                    "filled_time": "093000",
                    "side": "buy",
                },
            )

        self.assertEqual(bot.pending_orders, [])
        self.assertEqual(bot.risk.positions[0]["qty"], 5)
        self.assertEqual(bot.risk.positions[0]["entry"], 70100.0)
        trading_bot.TradingBot._reconcile_pending_orders(bot, broker_kr={}, broker_us={})
        self.assertEqual(len(bot.risk.positions), 1)
        self.assertEqual(bot._funnel["KR"]["filled"], 1)
        bot._entry_timing_filled.assert_called_once()
        bot._v2_record_lifecycle_event.assert_called_once()
        bot._save_pending_orders.assert_called_once()
        bot._save_positions.assert_called_once()
        alert.assert_called_once()

    def test_partial_pathb_buy_fill_keeps_remainder_and_calls_pathb(self) -> None:
        order = {
            "market": "KR",
            "ticker": "005930",
            "qty": 10,
            "order_no": "ord-partial",
            "raw_price": 70000,
            "pathb_path_run_id": "pathb-1",
        }
        bot = _bot_with_pending(order)

        with patch.object(trading_bot, "fill_confirm_alert"):
            trading_bot.TradingBot._on_fill_notice(
                bot,
                {
                    "order_no": "ord-partial",
                    "ticker": "005930",
                    "filled_qty": 3,
                    "filled_price": 70100,
                    "filled_time": "093001",
                    "side": "buy",
                },
            )

        self.assertEqual(bot.pending_orders[0]["qty"], 7)
        self.assertEqual(bot.pending_orders[0]["filled_qty_accum"], 3)
        self.assertEqual(bot.pending_orders[0]["filled_price_native"], 70100.0)
        self.assertEqual(bot.pending_orders[0]["fill_time"], "093001")
        bot._entry_timing_filled.assert_not_called()
        bot.pathb.on_buy_fill.assert_called_once()
        pathb_order = bot.pathb.on_buy_fill.call_args.args[0]
        self.assertEqual(pathb_order["qty"], 3)
        self.assertEqual(pathb_order["filled_price_native"], 70100.0)
        self.assertTrue(bot.pathb.on_buy_fill.call_args.kwargs["partial"])

    def test_duplicate_ws_raw_hash_is_not_applied_twice(self) -> None:
        order = {
            "market": "KR",
            "ticker": "005930",
            "qty": 10,
            "order_no": "ord-dup",
            "raw_price": 70000,
            "pathb_path_run_id": "pathb-dup",
        }
        bot = _bot_with_pending(order)
        event = {
            "order_no": "ord-dup",
            "ticker": "005930",
            "filled_qty": 3,
            "filled_price": 70100,
            "filled_time": "093001",
            "side": "buy",
            "raw_hash": "same-raw-payload",
        }

        with patch.object(trading_bot, "fill_confirm_alert"):
            trading_bot.TradingBot._on_fill_notice(bot, dict(event))
            trading_bot.TradingBot._on_fill_notice(bot, dict(event))

        self.assertEqual(bot.pending_orders[0]["qty"], 7)
        self.assertEqual(bot.pending_orders[0]["filled_qty_accum"], 3)
        self.assertEqual(len(bot.risk.positions), 1)
        bot.pathb.on_buy_fill.assert_called_once()

    def test_fill_ledger_evicts_oldest_keys_after_max_size(self) -> None:
        bot = SimpleNamespace()

        with patch.dict("os.environ", {"FILL_LEDGER_MAX_KEYS": "100"}):
            for idx in range(101):
                self.assertFalse(trading_bot._fill_ledger_seen_or_mark(bot, f"fill-{idx}"))

        self.assertEqual(len(bot._applied_fill_keys), 100)
        self.assertNotIn("fill-0", bot._applied_fill_keys)
        self.assertIn("fill-100", bot._applied_fill_keys)
        self.assertEqual(len(bot._applied_fill_key_order), 100)
        self.assertEqual(set(bot._applied_fill_key_order), bot._applied_fill_keys)

    def test_fill_ledger_duplicate_key_still_skips_without_growing_order(self) -> None:
        bot = SimpleNamespace()

        self.assertFalse(trading_bot._fill_ledger_seen_or_mark(bot, "same-fill"))
        self.assertTrue(trading_bot._fill_ledger_seen_or_mark(bot, "same-fill"))

        self.assertEqual(bot._applied_fill_keys, {"same-fill"})
        self.assertEqual(list(bot._applied_fill_key_order), ["same-fill"])

    def test_fill_ledger_initializes_order_deque_when_missing(self) -> None:
        bot = SimpleNamespace(_applied_fill_keys={"old-fill"})

        self.assertFalse(trading_bot._fill_ledger_seen_or_mark(bot, "new-fill"))

        self.assertIn("old-fill", bot._applied_fill_keys)
        self.assertIn("new-fill", bot._applied_fill_keys)
        self.assertTrue(hasattr(bot, "_applied_fill_key_order"))
        self.assertEqual(set(bot._applied_fill_key_order), bot._applied_fill_keys)

    def test_sell_or_invalid_notice_is_ignored(self) -> None:
        bot = _bot_with_pending({"market": "KR", "ticker": "005930", "qty": 5, "order_no": "ord-ignore"})

        trading_bot.TradingBot._on_fill_notice(
            bot,
            {
                "order_no": "ord-ignore",
                "ticker": "005930",
                "filled_qty": 5,
                "filled_price": 70100,
                "side": "sell",
            },
        )
        trading_bot.TradingBot._on_fill_notice(
            bot,
            {
                "order_no": "",
                "ticker": "005930",
                "filled_qty": 5,
                "filled_price": 70100,
                "side": "buy",
            },
        )

        self.assertEqual(len(bot.pending_orders), 1)
        self.assertEqual(bot.risk.positions, [])
        bot._save_pending_orders.assert_not_called()
        bot._save_positions.assert_not_called()


if __name__ == "__main__":
    unittest.main()
