from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from bot.session_date import KST
from runtime.profit_strategy_order_bridge import run_profit_strategy_handoff


class FakeBot:
    is_paper = False
    usd_krw_rate = 1.0
    today_judgment = {"consensus": {"mode": "CAUTIOUS"}}
    price_cache_raw: dict[str, float] = {}
    pending_orders: list[dict] = []

    def __init__(self, *, cash: float) -> None:
        self.cash = cash
        self.risk = SimpleNamespace(positions=[])
        self._last_micro_probe_submit_result: dict = {}
        self.submitted: list[dict] = []
        self.order_unknowns: list[dict] = []
        self.config = {
            "PROFIT_STRATEGY_ORDER_HANDOFF_ENABLED": True,
            "PROFIT_STRATEGY_KILL_SWITCH": False,
            "PROFIT_STRATEGY_AUTHORITY_MODE": "micro",
            "PROFIT_STRATEGY_ORDER_SUBMIT_ENABLED": True,
            "PROFIT_STRATEGY_ORDER_LIVE_ACK": "I_ACCEPT_LIVE_PROFIT_STRATEGIES",
            "PROFIT_STRATEGY_ENABLED_IDS": "US_CONSENSUS_3D_V1",
            "PROFIT_STRATEGY_ORDER_MIN_OPEN_MIN": 5,
            "PROFIT_STRATEGY_ORDER_MAX_OPEN_MIN": 45,
            "PROFIT_STRATEGY_MAX_NEW_PER_DAY_US": 1,
            "PROFIT_STRATEGY_MAX_OPEN_SLOTS": 4,
            "PROFIT_STRATEGY_MAX_CHASE_PCT": 0.75,
            "PROFIT_STRATEGY_MAX_ORDER_KRW_US": 100000,
        }

    def _runtime_bool(self, key: str, default: bool = False) -> bool:
        return bool(self.config.get(key, default))

    def _runtime_value(self, key: str, default=None):
        return self.config.get(key, default)

    def _runtime_int(self, key: str, default: int = 0) -> int:
        return int(self.config.get(key, default))

    def _runtime_float(self, key: str, default: float = 0.0) -> float:
        return float(self.config.get(key, default))

    def _current_session_date_str(self, _: str) -> str:
        return "2026-07-15"

    def _sync_runtime_with_broker(self) -> None:
        return None

    def _ticker_market(self, _: str) -> str:
        return "US"

    def _has_open_position(self, *_):
        return False

    def _has_pending_order(self, *_):
        return False

    def _token_for_market(self, _: str) -> str:
        return "token"

    def _market_budget_available(self, _: str) -> float:
        return 100000.0

    def _broker_orderable_cash_krw(self, _: str) -> float:
        return self.cash

    def _submit_micro_probe_buy_order(self, **kwargs) -> bool:
        self.submitted.append(kwargs)
        self._last_micro_probe_submit_result = {"status": "SUBMITTED", "order_no": "123"}
        return True

    def _v2_record_order_unknown(self, market: str, ticker: str, order: dict, detail: str) -> None:
        self.order_unknowns.append({"market": market, "ticker": ticker, "order": order, "detail": detail})


def _signal() -> list[dict]:
    return [{
        "strategy_id": "US_CONSENSUS_3D_V1",
        "source_strategy": "us_consensus_3d",
        "market": "US",
        "ticker": "AAPL",
        "entry_session_date": "2026-07-15",
        "known_at": "2026-07-14T23:59:59Z",
        "rank": 1,
        "priority": 1.0,
        "hold_sessions": 3,
        "weight": 1.0,
    }]


def test_zero_broker_cash_fails_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.regular_open_dt", lambda *_: datetime.now(KST) - timedelta(minutes=10))
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.load_signals", lambda *_, **__: _signal())
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.get_price", lambda *_, **__: {"price": 10000, "open": 10000})
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.get_runtime_path", lambda *parts, **__: tmp_path.joinpath(*parts))
    bot = FakeBot(cash=0.0)
    result = run_profit_strategy_handoff(bot, "US")
    assert bot.submitted == []
    assert result["results"][0]["reason"] == "micro_budget_unavailable"


def test_micro_contract_submits_bounded_order(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.regular_open_dt", lambda *_: datetime.now(KST) - timedelta(minutes=10))
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.load_signals", lambda *_, **__: _signal())
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.get_price", lambda *_, **__: {"price": 10000, "open": 10000})
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.get_runtime_path", lambda *parts, **__: tmp_path.joinpath(*parts))
    bot = FakeBot(cash=50000.0)
    result = run_profit_strategy_handoff(bot, "US")
    assert result["results"][0]["status"] == "SUBMITTED"
    assert bot.submitted[0]["qty"] == 5
    assert bot.submitted[0]["source_strategy"] == "us_consensus_3d"
    assert bot.submitted[0]["max_hold"] == 3


def test_missing_exact_live_ack_blocks_before_signal_access(tmp_path, monkeypatch) -> None:
    bot = FakeBot(cash=50000.0)
    bot.config["PROFIT_STRATEGY_ORDER_LIVE_ACK"] = "wrong"
    result = run_profit_strategy_handoff(bot, "US")
    assert result["status"] == "BLOCKED"
    assert bot.submitted == []


def test_order_unknown_registers_global_guard_and_blocks_later_scans(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.regular_open_dt", lambda *_: datetime.now(KST) - timedelta(minutes=10))
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.load_signals", lambda *_, **__: _signal())
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.get_price", lambda *_, **__: {"price": 10000, "open": 10000})
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.get_runtime_path", lambda *parts, **__: tmp_path.joinpath(*parts))
    bot = FakeBot(cash=50000.0)

    def unknown_submit(**kwargs) -> bool:
        bot.submitted.append(kwargs)
        bot._last_micro_probe_submit_result = {
            "status": "UNKNOWN",
            "order_no": "",
            "reason": "order_exception",
        }
        return False

    bot._submit_micro_probe_buy_order = unknown_submit
    first = run_profit_strategy_handoff(bot, "US")
    second = run_profit_strategy_handoff(bot, "US")

    assert first["results"][0]["status"] == "ORDER_UNKNOWN"
    assert len(bot.order_unknowns) == 1
    assert bot.order_unknowns[0]["order"]["source_strategy"] == "us_consensus_3d"
    assert second["status"] == "BLOCKED"
    assert second["reason"] == "unresolved_strategy_order_unknown"
    assert len(bot.submitted) == 1


def test_broker_rejection_is_not_retried_same_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.regular_open_dt", lambda *_: datetime.now(KST) - timedelta(minutes=10))
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.load_signals", lambda *_, **__: _signal())
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.get_price", lambda *_, **__: {"price": 10000, "open": 10000})
    monkeypatch.setattr("runtime.profit_strategy_order_bridge.get_runtime_path", lambda *parts, **__: tmp_path.joinpath(*parts))
    bot = FakeBot(cash=50000.0)

    def rejected_submit(**kwargs) -> bool:
        bot.submitted.append(kwargs)
        bot._last_micro_probe_submit_result = {
            "status": "REJECTED",
            "reason": "broker_reject",
            "detail": "account capability missing",
            "order_no": "",
        }
        return False

    bot._submit_micro_probe_buy_order = rejected_submit
    first = run_profit_strategy_handoff(bot, "US")
    second = run_profit_strategy_handoff(bot, "US")

    assert first["results"][0]["status"] == "SUBMIT_BLOCKED"
    assert first["results"][0]["broker_outcome_status"] == "REJECTED"
    assert first["results"][0]["broker_detail"] == "account capability missing"
    assert second["status"] == "BLOCKED"
    assert second["reason"] == "daily_strategy_order_cap"
    assert second["attempted"] == 1
    assert len(bot.submitted) == 1
