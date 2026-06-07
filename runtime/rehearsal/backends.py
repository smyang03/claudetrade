from __future__ import annotations

import contextlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from runtime.rehearsal.context import RehearsalContext, RehearsalGuardError
from runtime.rehearsal.fixtures import RehearsalMarketFixture, RehearsalScenarioFixture


def _market_key(market: str | None) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


class FixtureBrokerBackend:
    def __init__(self, context: RehearsalContext, fixture: RehearsalScenarioFixture):
        self.context = context
        self.fixture = fixture
        self.intent_seq = 0

    def _market(self, market: str | None) -> RehearsalMarketFixture:
        return self.fixture.market(_market_key(market))

    def get_access_token(self, *, market: str = "KR", force_refresh: bool = False) -> str:
        return f"rehearsal_token_{_market_key(market).lower()}"

    def get_balance(self, token: str = "", *, market: str = "KR", force_refresh: bool = False) -> dict[str, Any]:
        data = self._market(market)
        if data.market == "US":
            cash_usd = float(data.cash_krw or 0.0) / float(data.usd_krw or 1350.0)
            return {
                "cash": cash_usd,
                "orderable_cash": cash_usd,
                "asset_cash": cash_usd,
                "asset_cash_krw": float(data.cash_krw or 0.0),
                "total_eval": 0.0,
                "kis_exchange_rate": float(data.usd_krw or 1350.0),
                "stocks": list(data.positions),
                "open_orders": list(data.open_orders),
                "today_fills": list(data.today_fills),
            }
        return {
            "cash": int(data.cash_krw or 0),
            "total_eval": 0,
            "stocks": list(data.positions),
            "open_orders": list(data.open_orders),
            "today_fills": list(data.today_fills),
        }

    def get_price(self, ticker: str, token: str = "", *, market: str = "KR", **kwargs: Any) -> dict[str, Any]:
        data = self._market(market)
        key = str(ticker or "").strip().upper() if data.market == "US" else str(ticker or "").strip()
        price = float(data.prices.get(key, next(iter(data.prices.values()), 100.0)) or 100.0)
        return {
            "ticker": key,
            "market": data.market,
            "price": price,
            "current_price": price,
            "last": price,
            "source": "fixture",
        }

    def get_daily_ohlcv(self, ticker: str, token: str = "", *, market: str = "KR", **kwargs: Any):
        try:
            import pandas as pd
        except Exception as exc:  # pragma: no cover
            raise RehearsalGuardError("pandas unavailable for fixture OHLCV") from exc
        price = float(self.get_price(ticker, token, market=market)["price"])
        rows = []
        for idx in range(30):
            base = price * (0.98 + idx * 0.001)
            rows.append({"open": base, "high": base * 1.01, "low": base * 0.99, "close": base, "volume": 1_000_000 + idx})
        return pd.DataFrame(rows)

    def precheck_order(
        self,
        ticker: str,
        qty: int,
        price: float,
        side: str,
        token: str = "",
        *,
        market: str = "KR",
        **kwargs: Any,
    ) -> dict[str, Any]:
        qty_i = int(qty or 0)
        if qty_i <= 0:
            return {"ok": False, "allowed": False, "reason": "INVALID_QTY", "source": "fixture"}
        if float(price or 0) <= 0:
            return {"ok": False, "allowed": False, "reason": "INVALID_PRICE", "source": "fixture"}
        data = self._market(market)
        if data.broker_truth_status != "ok":
            return {"ok": False, "allowed": False, "reason": "BLOCKED_BROKER_TRUTH", "source": "fixture"}
        return {"ok": True, "allowed": True, "reason": "OK", "source": "fixture"}

    def place_order(
        self,
        ticker: str,
        qty: int,
        price: float,
        side: str,
        token: str = "",
        *,
        market: str = "KR",
        **kwargs: Any,
    ) -> dict[str, Any]:
        data = self._market(market)
        if data.broker_truth_status != "ok":
            return {"ok": False, "success": False, "reason": "BLOCKED_BROKER_TRUTH", "source": "fixture"}
        self.intent_seq += 1
        side_key = str(side or "buy").lower()
        ticker_key = str(ticker or "").strip().upper() if data.market == "US" else str(ticker or "").strip()
        order_no = f"rehearsal_{data.market}_{ticker_key}_{side_key}_{self.intent_seq:04d}"
        intent = {
            "rehearsal": True,
            "runtime_context": self.context.runtime_context,
            "runtime_mode": "live",
            "learning_excluded": True,
            "do_not_learn": True,
            "market": data.market,
            "ticker": ticker_key,
            "side": side_key,
            "qty": int(qty or 0),
            "price": float(price or 0.0),
            "order_no": order_no,
            "order_send": False,
            "broker_call": False,
            "claude_call": False,
            "scenario": self.fixture.name,
            "path_type": kwargs.get("path_type", ""),
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        self.context.order_intents_path.parent.mkdir(parents=True, exist_ok=True)
        with self.context.order_intents_path.open("a", encoding="utf-8", newline="\n") as fp:
            fp.write(json.dumps(intent, ensure_ascii=False, sort_keys=True) + "\n")
        data.open_orders.append({**intent, "remaining_qty": int(qty or 0), "status": "OPEN"})
        return {"ok": True, "success": True, "order_no": order_no, "source": "fixture", "intent": intent}

    def cancel_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "success": True, "source": "fixture", "cancelled": True}

    def broker_truth_snapshot(self, market: str) -> dict[str, Any]:
        data = self._market(market)
        if data.broker_truth_status != "ok":
            return {"ok": False, "status": data.broker_truth_status, "positions": [], "open_orders": []}
        return {
            "ok": True,
            "status": "ok",
            "market": data.market,
            "positions": list(data.positions),
            "open_orders": list(data.open_orders),
            "today_fills": list(data.today_fills),
            "source": "fixture",
        }


class FixtureClaudeBackend:
    def __init__(self, fixture: RehearsalScenarioFixture):
        self.fixture = fixture

    def get_selection_meta(self, market: str) -> dict[str, Any]:
        return dict(self.fixture.market(market).selection_meta)

    def get_judgment(self, market: str) -> dict[str, Any]:
        return dict(self.fixture.market(market).judgment)

    def get_hold_advice(self, market: str, ticker: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"action": "HOLD", "reason": "fixture_hold", "confidence": 0.8}


class FixtureKISWebSocket:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False


class FixtureBrokerTruthSnapshot:
    def __init__(self, *args: Any, backend: FixtureBrokerBackend | None = None, **kwargs: Any) -> None:
        self.backend = backend

    def refresh(self, market: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if self.backend is None:
            return {"ok": False, "status": "fixture_backend_missing"}
        return self.backend.broker_truth_snapshot(market)

    def get(self, market: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.refresh(market, *args, **kwargs)


def _noop_alert(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {"ok": True, "sent": False, "source": "fixture"}


def _patch(module_name: str, attr: str, value: Any, restore: list[tuple[Any, str, Any]]) -> None:
    module = sys.modules.get(module_name)
    if module is None or not hasattr(module, attr):
        return
    restore.append((module, attr, getattr(module, attr)))
    setattr(module, attr, value)


@contextlib.contextmanager
def install_rehearsal_backends(
    context: RehearsalContext,
    fixture: RehearsalScenarioFixture,
) -> Iterator[dict[str, Any]]:
    broker = FixtureBrokerBackend(context, fixture)
    claude = FixtureClaudeBackend(fixture)
    restore: list[tuple[Any, str, Any]] = []

    def price(ticker: str, token: str = "", *args: Any, market: str = "KR", **kwargs: Any) -> dict[str, Any]:
        return broker.get_price(ticker, token, market=market, **kwargs)

    def balance(token: str = "", *args: Any, market: str = "KR", force_refresh: bool = False, **kwargs: Any) -> dict[str, Any]:
        return broker.get_balance(token, market=market, force_refresh=force_refresh)

    def token(*args: Any, market: str = "KR", force_refresh: bool = False, **kwargs: Any) -> str:
        return broker.get_access_token(market=market, force_refresh=force_refresh)

    def precheck(ticker: str, qty: int, price_value: float, side: str, token_value: str = "", *args: Any, market: str = "KR", **kwargs: Any) -> dict[str, Any]:
        return broker.precheck_order(ticker, qty, price_value, side, token_value, market=market, **kwargs)

    def order(ticker: str, qty: int, price_value: float, side: str, token_value: str = "", *args: Any, market: str = "KR", **kwargs: Any) -> dict[str, Any]:
        return broker.place_order(ticker, qty, price_value, side, token_value, market=market, **kwargs)

    def daily(ticker: str, token_value: str = "", *args: Any, market: str = "KR", **kwargs: Any):
        return broker.get_daily_ohlcv(ticker, token_value, market=market, **kwargs)

    def select_tickers(market: str, *args: Any, **kwargs: Any):
        data = fixture.market(market)
        return list(data.selected), {ticker: "fixture_selected" for ticker in data.selected}

    def judgments(*args: Any, market: str = "KR", **kwargs: Any):
        return claude.get_judgment(market)

    def last_selection_meta(*args: Any, market: str = "KR", **kwargs: Any):
        return claude.get_selection_meta(market)

    def startup_health_check(self: Any) -> None:
        return None

    for module_name in ("kis_api", "trading_bot"):
        _patch(module_name, "get_access_token", token, restore)
        _patch(module_name, "get_balance", balance, restore)
        _patch(module_name, "get_price", price, restore)
        _patch(module_name, "precheck_order", precheck, restore)
        _patch(module_name, "place_order", order, restore)
        _patch(module_name, "cancel_order", broker.cancel_order, restore)
        _patch(module_name, "get_daily_ohlcv", daily, restore)
        _patch(module_name, "get_usd_krw", lambda *a, **k: fixture.market("US").usd_krw, restore)
        _patch(module_name, "get_index_change", lambda *a, **k: {"change_pct": 0.0, "source": "fixture"}, restore)
        _patch(module_name, "get_index_snapshot", lambda *a, **k: {"price": 100.0, "change_pct": 0.0, "source": "fixture"}, restore)
        _patch(module_name, "get_intraday_candles", lambda *a, **k: [], restore)
        _patch(module_name, "get_kr_orderbook_snapshot", lambda *a, **k: {"source": "fixture"}, restore)
        _patch(module_name, "get_kr_vi_state", lambda *a, **k: {"source": "fixture", "vi": False}, restore)
        _patch(module_name, "screen_market_kr", lambda *a, **k: list(fixture.market("KR").selected), restore)
        _patch(module_name, "screen_market_us", lambda *a, **k: list(fixture.market("US").selected), restore)
        _patch(module_name, "save_kr_screen_cache", lambda *a, **k: None, restore)
        _patch(module_name, "is_trading_halted", lambda *a, **k: False, restore)
        _patch(module_name, "get_kis_market_profile", lambda *a, **k: {"source": "fixture"}, restore)
        _patch(module_name, "inquire_ccnl_us", lambda *a, **k: list(fixture.market("US").today_fills), restore)
        _patch(module_name, "inquire_daily_ccld_kr", lambda *a, **k: list(fixture.market("KR").today_fills), restore)
        _patch(module_name, "KISWebSocket", FixtureKISWebSocket, restore)

    _patch("runtime.pathb_runtime", "get_balance", balance, restore)
    _patch("runtime.pathb_runtime", "get_price", price, restore)
    _patch("runtime.pathb_runtime", "precheck_order", precheck, restore)
    _patch("runtime.pathb_runtime", "place_order", order, restore)
    _patch("runtime.pathb_runtime", "cancel_order", broker.cancel_order, restore)
    _patch("runtime.pathb_runtime", "buy_order_alert", _noop_alert, restore)
    _patch("runtime.pathb_runtime", "tg_send", _noop_alert, restore)
    _patch("runtime.intraday_minute_cache", "get_intraday_candles", lambda *a, **k: [], restore)
    _patch("runtime.broker_truth_snapshot", "BrokerTruthSnapshot", lambda *a, **k: FixtureBrokerTruthSnapshot(*a, backend=broker, **k), restore)

    _patch("minority_report.analysts", "select_tickers", select_tickers, restore)
    _patch("minority_report.analysts", "get_three_judgments", judgments, restore)
    _patch("minority_report.analysts", "get_last_selection_meta", last_selection_meta, restore)
    _patch("minority_report.hold_advisor", "ask", claude.get_hold_advice, restore)
    _patch("minority_report.quick_exit_check", "quick_exit_check", lambda *a, **k: {"action": "HOLD", "source": "fixture"}, restore)
    _patch("minority_report.tuner", "tune", lambda *a, **k: {}, restore)
    _patch("minority_report.postmortem", "run", lambda *a, **k: {}, restore)

    for module_name in ("telegram_reporter", "trading_bot"):
        for attr in ("send", "buy_order_alert", "fill_confirm_alert", "system_alert", "status_report", "dashboard_push"):
            _patch(module_name, attr, _noop_alert, restore)

    tb = sys.modules.get("trading_bot")
    if tb is not None and hasattr(tb, "TradingBot"):
        _patch("trading_bot", "select_tickers", select_tickers, restore)
        _patch("trading_bot", "get_three_judgments", judgments, restore)
        _patch("trading_bot", "get_last_selection_meta", last_selection_meta, restore)
        _patch("trading_bot", "tune", lambda *a, **k: {}, restore)
        _patch("trading_bot", "run_postmortem", lambda *a, **k: {}, restore)
        restore.append((tb.TradingBot, "_startup_health_check", getattr(tb.TradingBot, "_startup_health_check")))
        setattr(tb.TradingBot, "_startup_health_check", startup_health_check)

    try:
        yield {"broker": broker, "claude": claude}
    finally:
        for obj, attr, value in reversed(restore):
            try:
                setattr(obj, attr, value)
            except Exception:
                pass


def read_intents(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
