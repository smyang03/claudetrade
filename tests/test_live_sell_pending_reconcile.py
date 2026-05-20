from __future__ import annotations

from unittest.mock import Mock, patch

from runtime.market_resolver import infer_ticker_market
from trading_bot import TradingBot


class _Risk:
    def __init__(self, positions: list[dict]) -> None:
        self.positions = positions
        self.cash = 0.0
        self.total_fee = 0.0
        self.daily_pnl = 0.0
        self.trade_log: list[dict] = []
        self.all_trade_log: list[dict] = []
        self.partial_close_calls = 0

    def _fee(self, side: str, amount: float) -> float:
        return 0.0

    def close_position_qty(
        self,
        ticker: str,
        exit_price: float,
        qty: int,
        reason: str,
        session_date: str | None = None,
        exit_meta: dict | None = None,
    ) -> dict | None:
        self.partial_close_calls += 1
        for pos in list(self.positions):
            if str(pos.get("ticker", "")).upper() != ticker.upper():
                continue
            close_qty = min(int(qty or 0), int(pos.get("qty", 0) or 0))
            if close_qty <= 0:
                return None
            entry = float(pos.get("entry", 0) or 0)
            pnl = (float(exit_price or 0) - entry) * close_qty
            pnl_pct = ((float(exit_price or 0) / entry) - 1.0) * 100.0 if entry > 0 else 0.0
            pos["qty"] = int(pos.get("qty", 0) or 0) - close_qty
            result = {
                **pos,
                **dict(exit_meta or {}),
                "ticker": ticker,
                "qty": close_qty,
                "remaining_qty": pos["qty"],
                "entry": entry,
                "exit_price": float(exit_price or 0),
                "reason": reason,
                "pnl": pnl,
                "pnl_krw": pnl,
                "pnl_pct": pnl_pct,
                "partial_close": pos["qty"] > 0,
            }
            event = {
                "side": "sell",
                "market": infer_ticker_market(ticker, unknown="KR"),
                "ticker": ticker,
                "qty": close_qty,
                "price": float(exit_price or 0),
                "reason": reason,
                "session_date": session_date or "",
                "pnl": pnl,
                "pnl_krw": pnl,
                **dict(exit_meta or {}),
            }
            self.trade_log.append(event)
            self.all_trade_log.append(event)
            self.daily_pnl += pnl
            return result
        return None

    def close_position(
        self,
        ticker: str,
        exit_price: float,
        reason: str,
        session_date: str | None = None,
        exit_meta: dict | None = None,
    ) -> dict | None:
        for idx, pos in enumerate(list(self.positions)):
            if str(pos.get("ticker", "")).upper() != ticker.upper():
                continue
            self.positions.pop(idx)
            qty = int(pos.get("qty", 0) or 0)
            entry = float(pos.get("entry", 0) or 0)
            pnl = (float(exit_price or 0) - entry) * qty
            pnl_pct = ((float(exit_price or 0) / entry) - 1.0) * 100.0 if entry > 0 else 0.0
            result = {
                **pos,
                **dict(exit_meta or {}),
                "ticker": ticker,
                "qty": qty,
                "entry": entry,
                "exit_price": float(exit_price or 0),
                "reason": reason,
                "pnl": pnl,
                "pnl_krw": pnl,
                "pnl_pct": pnl_pct,
            }
            event = {
                "side": "sell",
                "market": infer_ticker_market(ticker, unknown="KR"),
                "ticker": ticker,
                "qty": qty,
                "price": float(exit_price or 0),
                "reason": reason,
                "session_date": session_date or "",
                "pnl": pnl,
                "pnl_krw": pnl,
                **dict(exit_meta or {}),
            }
            self.trade_log.append(event)
            self.all_trade_log.append(event)
            self.daily_pnl += pnl
            return result
        return None


def _pending_position() -> dict:
    return {
        "ticker": "IREN",
        "qty": 2,
        "entry": 50_000.0,
        "strategy": "swing",
        "source_strategy": "swing",
        "sell_confirmation_pending": True,
        "pending_sell_order_no": "0031706077",
        "pending_sell_qty": 2,
        "pending_sell_reason": "loss_cap",
        "pending_sell_price": 59.25,
        "pending_sell_created_at": "2026-05-08T04:00:00+09:00",
    }


def _bot_with_snapshot(snapshot: dict, *, lookup_fill: dict | None = None) -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.usd_krw_rate = 1000.0
    bot.risk = _Risk([_pending_position()])
    bot._session_closed_tickers = {"KR": set(), "US": set()}
    bot.decision_events: list[dict] = []
    bot.saved_positions = 0
    bot.live_status_writes = 0

    bot._current_session_date_str = lambda market: "2026-05-08"  # type: ignore[method-assign]
    bot._broker_truth_market_snapshot = lambda market, force=True, ttl_sec=15: snapshot  # type: ignore[method-assign]
    bot._lookup_order_fill = lambda market, order_no, ticker, created_at="": lookup_fill  # type: ignore[method-assign]
    bot._record_decision_event = lambda market, action, ticker, **kw: bot.decision_events.append(  # type: ignore[method-assign]
        {"market": market, "action": action, "ticker": ticker, **kw}
    )
    bot._save_positions = lambda: setattr(bot, "saved_positions", bot.saved_positions + 1)  # type: ignore[method-assign]
    bot._write_live_status = lambda market, force=False: setattr(bot, "live_status_writes", bot.live_status_writes + 1)  # type: ignore[method-assign]
    bot._note_recent_sell_proceeds = lambda *args, **kwargs: None  # type: ignore[method-assign]
    bot._note_stop_loss_event = lambda *args, **kwargs: None  # type: ignore[method-assign]
    return bot


def _bot_for_broker_sync(snapshot: dict) -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.usd_krw_rate = 1000.0
    bot.is_paper = False
    bot.pending_orders = []
    bot._broker_state = {}
    bot._session_closed_tickers = {"KR": set(), "US": set()}
    bot.risk = _Risk([_pending_position()])
    bot.risk.cash = 1_000_000.0
    bot.decision_events: list[dict] = []
    bot.saved_positions = 0
    bot.live_status_writes = 0
    bot.proceeds_records: list[dict] = []

    bot._market_enabled = lambda market: str(market).upper() == "US"  # type: ignore[method-assign]
    bot._token_for_market = lambda market, force_refresh=False: "token"  # type: ignore[method-assign]
    bot._current_session_date_str = lambda market: "2026-05-08"  # type: ignore[method-assign]
    bot._reconcile_pending_orders = lambda broker_kr, broker_us: None  # type: ignore[method-assign]
    bot._broker_truth_market_snapshot = lambda market, force=True, ttl_sec=15: snapshot  # type: ignore[method-assign]
    bot._lookup_order_fill = lambda market, order_no, ticker, created_at="": None  # type: ignore[method-assign]
    bot._record_decision_event = lambda market, action, ticker, **kw: bot.decision_events.append(  # type: ignore[method-assign]
        {"market": market, "action": action, "ticker": ticker, **kw}
    )
    bot._save_positions = lambda: setattr(bot, "saved_positions", bot.saved_positions + 1)  # type: ignore[method-assign]
    bot._write_live_status = lambda market, force=False: setattr(bot, "live_status_writes", bot.live_status_writes + 1)  # type: ignore[method-assign]
    bot._note_recent_sell_proceeds = lambda market, ticker, **kw: bot.proceeds_records.append(  # type: ignore[method-assign]
        {"market": market, "ticker": ticker, **kw}
    )
    bot._note_stop_loss_event = lambda *args, **kwargs: None  # type: ignore[method-assign]
    bot._flag_execution_issue = Mock()  # type: ignore[method-assign]
    return bot


def _broker_balance_without_us_positions() -> dict:
    return {
        "cash": 1000.0,
        "total_eval": 0.0,
        "stocks": [],
    }


def test_pending_sell_fill_closes_local_position_and_records_closed_event() -> None:
    bot = _bot_with_snapshot(
        {
            "missing": False,
            "stale": False,
            "error": "",
            "positions": [{"ticker": "IREN", "qty": 2}],
            "open_orders": [],
            "today_fills": [
                {
                    "ticker": "IREN",
                    "side": "sell",
                    "order_no": "0031706077",
                    "filled_qty": 2,
                    "fill_price": 59.5,
                }
            ],
        }
    )

    summary = TradingBot._reconcile_pending_sell_confirmations(bot, "US", force=True)

    assert summary["checked"] == 1
    assert summary["closed"] == 1
    assert bot.risk.positions == []
    event = bot.decision_events[-1]
    assert event["action"] == "sell_filled"
    assert event["order_no"] == "0031706077"
    assert event["broker_fill_source"] == "broker_fill_query_reconcile"
    assert event["detail"] == "pending_sell_reconcile:BROKER_SELL_FILL_CONFIRMED"


def test_pending_sell_partial_fill_uses_risk_partial_close() -> None:
    bot = _bot_with_snapshot(
        {
            "missing": False,
            "stale": False,
            "error": "",
            "positions": [{"ticker": "IREN", "qty": 3}],
            "open_orders": [],
            "today_fills": [
                {
                    "ticker": "IREN",
                    "side": "sell",
                    "order_no": "0031706077",
                    "filled_qty": 1,
                    "fill_price": 59.5,
                }
            ],
        }
    )
    bot.risk.positions[0]["qty"] = 3
    bot.risk.positions[0]["pending_sell_qty"] = 3

    summary = TradingBot._reconcile_pending_sell_confirmations(bot, "US", force=True)

    assert summary["checked"] == 1
    assert summary["partial"] == 1
    assert bot.risk.partial_close_calls == 1
    assert bot.risk.positions[0]["qty"] == 2
    assert bot.risk.positions[0]["sell_confirmation_pending"] is False
    event = bot.decision_events[-1]
    assert event["qty"] == 1
    assert event["detail"] == "pending_sell_reconcile:BROKER_SELL_PARTIAL_FILL_CONFIRMED"


def test_pending_sell_partial_fill_with_open_order_keeps_remaining_pending() -> None:
    bot = _bot_with_snapshot(
        {
            "missing": False,
            "stale": False,
            "error": "",
            "positions": [{"ticker": "IREN", "qty": 2}],
            "open_orders": [
                {
                    "ticker": "IREN",
                    "side": "sell",
                    "order_no": "0031706077",
                    "remaining_qty": 2,
                }
            ],
            "today_fills": [
                {
                    "ticker": "IREN",
                    "side": "sell",
                    "order_no": "0031706077",
                    "filled_qty": 1,
                    "fill_price": 59.5,
                }
            ],
        }
    )
    bot.risk.positions[0]["qty"] = 3
    bot.risk.positions[0]["pending_sell_qty"] = 3

    summary = TradingBot._reconcile_pending_sell_confirmations(bot, "US", force=True)

    assert summary["checked"] == 1
    assert summary["partial"] == 1
    assert summary["kept_pending"] == 1
    assert bot.risk.partial_close_calls == 1
    pos = bot.risk.positions[0]
    assert pos["qty"] == 2
    assert pos["sell_confirmation_pending"] is True
    assert pos["pending_sell_order_no"] == "0031706077"
    assert pos["pending_sell_qty"] == 2
    assert pos["pending_sell_status"] == "partial_fill_open_order"
    assert pos["pending_sell_broker_status"] == "BROKER_SELL_PARTIAL_FILL_OPEN_ORDER_KEEP_PENDING"
    assert pos["pending_sell_resolution"] == "BROKER_SELL_PARTIAL_FILL_OPEN_ORDER_KEEP_PENDING"
    event = bot.decision_events[-1]
    assert event["qty"] == 1
    assert event["detail"] == "pending_sell_reconcile:BROKER_SELL_PARTIAL_FILL_CONFIRMED"


def test_pending_sell_position_absent_assumes_sold_and_closes_local_position() -> None:
    bot = _bot_with_snapshot(
        {
            "missing": False,
            "stale": False,
            "error": "",
            "positions": [],
            "open_orders": [],
            "today_fills": [],
        }
    )

    summary = TradingBot._reconcile_pending_sell_confirmations(bot, "US", force=True)

    assert summary["checked"] == 1
    assert summary["closed"] == 1
    assert bot.risk.positions == []
    event = bot.decision_events[-1]
    assert event["broker_fill_source"] == "broker_position_absent_inferred"
    assert event["detail"] == "pending_sell_reconcile:BROKER_POSITION_GONE_ASSUME_SOLD"


def test_pending_sell_order_no_only_is_reconciled_as_active() -> None:
    bot = _bot_with_snapshot(
        {
            "missing": False,
            "stale": False,
            "error": "",
            "positions": [],
            "open_orders": [],
            "today_fills": [],
        }
    )
    pos = bot.risk.positions[0]
    pos["sell_confirmation_pending"] = False
    pos["pending_sell_status"] = "submitted"

    summary = TradingBot._reconcile_pending_sell_confirmations(bot, "US", force=True)

    assert summary["checked"] == 1
    assert summary["closed"] == 1
    assert bot.risk.positions == []
    assert bot.decision_events[-1]["detail"] == "pending_sell_reconcile:BROKER_POSITION_GONE_ASSUME_SOLD"


def test_resolved_pending_sell_order_no_metadata_is_not_reconciled() -> None:
    bot = _bot_with_snapshot(
        {
            "missing": False,
            "stale": False,
            "error": "",
            "positions": [],
            "open_orders": [],
            "today_fills": [],
        }
    )
    pos = bot.risk.positions[0]
    pos["sell_confirmation_pending"] = False
    pos["pending_sell_status"] = "resolved"
    pos["pending_sell_resolution"] = "BROKER_STILL_HELD_NO_OPEN_ORDER_CLEAR_STALE_PENDING"

    summary = TradingBot._reconcile_pending_sell_confirmations(bot, "US", force=True)

    assert summary["checked"] == 0
    assert len(bot.risk.positions) == 1
    assert bot.decision_events == []


def test_broker_sync_keeps_us_position_when_success_empty_stocks_is_unreliable() -> None:
    bot = _bot_for_broker_sync(
        {
            "missing": False,
            "stale": False,
            "error": "",
            "positions": [],
            "open_orders": [],
            "today_fills": [],
        }
    )

    with patch("trading_bot.get_balance", return_value=_broker_balance_without_us_positions()):
        TradingBot._sync_runtime_with_broker(bot)

    assert len(bot.risk.positions) == 1
    assert bot.risk.positions[0]["ticker"] == "IREN"
    assert bot.risk.positions[0]["position_integrity"] == "protected"
    assert bot._broker_state["US"]["trust_level"] == "degraded"
    assert "broker_empty_with_internal_positions:runtime_sync" in bot._broker_state["US"]["last_error"]
    assert bot.risk.cash == 1_000_000.0
    bot._flag_execution_issue.assert_not_called()
    assert bot.decision_events == []
    assert bot.proceeds_records == []


def test_broker_sync_keeps_pending_sell_when_broker_truth_unavailable() -> None:
    bot = _bot_for_broker_sync(
        {
            "missing": False,
            "stale": True,
            "error": "snapshot_stale",
            "positions": [],
            "open_orders": [],
            "today_fills": [],
        }
    )

    with patch("trading_bot.get_balance", return_value=_broker_balance_without_us_positions()):
        TradingBot._sync_runtime_with_broker(bot)

    assert len(bot.risk.positions) == 1
    pos = bot.risk.positions[0]
    assert pos["position_integrity"] == "protected"
    assert bot._broker_state["US"]["trust_level"] == "degraded"
    bot._flag_execution_issue.assert_not_called()
    assert bot.proceeds_records == []


def test_broker_sync_keeps_pending_sell_when_broker_open_order_exists() -> None:
    bot = _bot_for_broker_sync(
        {
            "missing": False,
            "stale": False,
            "error": "",
            "positions": [],
            "open_orders": [
                {
                    "ticker": "IREN",
                    "side": "sell",
                    "order_no": "0031706077",
                    "remaining_qty": 2,
                }
            ],
            "today_fills": [],
        }
    )

    with patch("trading_bot.get_balance", return_value=_broker_balance_without_us_positions()):
        TradingBot._sync_runtime_with_broker(bot)

    assert len(bot.risk.positions) == 1
    pos = bot.risk.positions[0]
    assert pos["sell_confirmation_pending"] is True
    assert pos["position_integrity"] == "protected"
    assert bot._broker_state["US"]["trust_level"] == "degraded"
    bot._flag_execution_issue.assert_not_called()
    assert bot.proceeds_records == []


def test_verify_live_positions_keeps_saved_us_position_when_broker_empty_unreliable() -> None:
    bot = _bot_for_broker_sync({})
    bot.pending_orders = []
    bot.risk.positions = []
    saved = [{"ticker": "AAPL", "qty": 3, "display_currency": "USD"}]

    with patch("trading_bot.get_balance", return_value=_broker_balance_without_us_positions()):
        verified = TradingBot._verify_live_positions(bot, saved)

    assert len(verified) == 1
    assert verified[0]["ticker"] == "AAPL"
    assert verified[0]["market"] == "US"
    assert verified[0]["position_integrity"] == "protected"
    assert bot._broker_state["US"]["trust_level"] == "degraded"
    assert "broker_empty_with_internal_positions:verify_live_positions" in bot._broker_state["US"]["last_error"]


def test_pending_sell_still_held_without_open_order_clears_stale_pending() -> None:
    bot = _bot_with_snapshot(
        {
            "missing": False,
            "stale": False,
            "error": "",
            "positions": [{"ticker": "IREN", "qty": 2}],
            "open_orders": [],
            "today_fills": [],
        }
    )

    summary = TradingBot._reconcile_pending_sell_confirmations(bot, "US", force=True)

    assert summary["checked"] == 1
    assert summary["cleared_stale"] == 1
    assert len(bot.risk.positions) == 1
    pos = bot.risk.positions[0]
    assert pos["sell_confirmation_pending"] is False
    assert pos["pending_sell_resolution"] == "BROKER_STILL_HELD_NO_OPEN_ORDER_CLEAR_STALE_PENDING"


def test_pending_sell_open_order_keeps_pending() -> None:
    bot = _bot_with_snapshot(
        {
            "missing": False,
            "stale": False,
            "error": "",
            "positions": [{"ticker": "IREN", "qty": 2}],
            "open_orders": [
                {
                    "ticker": "IREN",
                    "side": "sell",
                    "order_no": "0031706077",
                    "remaining_qty": 2,
                }
            ],
            "today_fills": [],
        }
    )

    summary = TradingBot._reconcile_pending_sell_confirmations(bot, "US", force=True)

    assert summary["checked"] == 1
    assert summary["kept_pending"] == 1
    pos = bot.risk.positions[0]
    assert pos["sell_confirmation_pending"] is True
    assert pos["pending_sell_resolution"] == "BROKER_OPEN_ORDER_FOUND_KEEP_PENDING"


def test_pending_sell_broker_truth_unavailable_keeps_pending() -> None:
    bot = _bot_with_snapshot(
        {
            "missing": False,
            "stale": True,
            "error": "snapshot_stale",
            "positions": [],
            "open_orders": [],
            "today_fills": [],
        }
    )

    summary = TradingBot._reconcile_pending_sell_confirmations(bot, "US", force=False)

    assert summary["checked"] == 1
    assert summary["kept_pending"] == 1
    assert summary["broker_truth_unavailable"] is True
    pos = bot.risk.positions[0]
    assert pos["sell_confirmation_pending"] is True
    assert pos["pending_sell_resolution"] == "BROKER_TRUTH_UNAVAILABLE_KEEP_PENDING"
