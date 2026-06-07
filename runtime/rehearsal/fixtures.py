from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class RehearsalMarketFixture:
    market: str
    session_date: str
    cash_krw: float
    usd_krw: float
    prices: dict[str, float] = field(default_factory=dict)
    positions: list[dict[str, Any]] = field(default_factory=list)
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    today_fills: list[dict[str, Any]] = field(default_factory=list)
    selection_meta: dict[str, Any] = field(default_factory=dict)
    selected: list[str] = field(default_factory=list)
    judgment: dict[str, Any] = field(default_factory=dict)
    broker_truth_status: str = "ok"


@dataclass
class RehearsalScenarioFixture:
    name: str
    markets: dict[str, RehearsalMarketFixture]
    expected: dict[str, Any] = field(default_factory=dict)

    def market(self, market: str) -> RehearsalMarketFixture:
        key = str(market or "KR").upper()
        if key not in self.markets:
            raise KeyError(f"fixture market not found: {key}")
        return self.markets[key]


def _today() -> str:
    return date.today().isoformat()


def _kr_fixture() -> RehearsalMarketFixture:
    return RehearsalMarketFixture(
        market="KR",
        session_date=_today(),
        cash_krw=5_000_000,
        usd_krw=1350.0,
        prices={"005930": 72000.0, "000660": 190000.0},
        selected=["005930"],
        judgment={"consensus": {"mode": "RISK_ON"}, "market": "KR"},
        selection_meta={
            "trade_ready": ["005930"],
            "watchlist": [],
            "selected": ["005930"],
            "price_targets": {
                "005930": {
                    "buy_zone_low": 70000,
                    "buy_zone_high": 73000,
                    "target_price": 76000,
                    "stop_price": 69000,
                    "confidence": 0.7,
                }
            },
        },
    )


def _us_fixture() -> RehearsalMarketFixture:
    return RehearsalMarketFixture(
        market="US",
        session_date=_today(),
        cash_krw=6_000_000,
        usd_krw=1350.0,
        prices={"NVDA": 123.45, "AAPL": 210.0},
        selected=["NVDA"],
        judgment={"consensus": {"mode": "RISK_ON"}, "market": "US"},
        selection_meta={
            "trade_ready": ["NVDA"],
            "watchlist": [],
            "selected": ["NVDA"],
            "price_targets": {
                "NVDA": {
                    "buy_zone_low": 120.0,
                    "buy_zone_high": 125.0,
                    "target_price": 130.0,
                    "stop_price": 118.0,
                    "confidence": 0.72,
                }
            },
        },
    )


def fixture_for_scenario(name: str) -> RehearsalScenarioFixture:
    scenario = str(name or "").strip() or "kr_patha_buy"
    kr = _kr_fixture()
    us = _us_fixture()
    expected: dict[str, Any] = {"min_order_intents": 1}
    if scenario == "us_pathb_sell_target":
        us.positions = [
            {
                "market": "US",
                "ticker": "NVDA",
                "qty": 1,
                "avg_price": 120.0,
                "current_price": 131.0,
                "path_type": "claude_price",
                "pathb_path_run_id": "rehearsal_pathb_NVDA_0001",
            }
        ]
    elif scenario == "broker_truth_fail_closed":
        us.broker_truth_status = "stale"
        expected["min_order_intents"] = 0
        expected["block_reason"] = "BLOCKED_BROKER_TRUTH"
    elif scenario == "order_unknown_reconcile":
        us.open_orders = [
            {
                "market": "US",
                "ticker": "NVDA",
                "side": "buy",
                "qty": 1,
                "remaining_qty": 1,
                "order_no": "UNKNOWN_REHEARSAL_1",
                "status": "ORDER_UNKNOWN",
            }
        ]
        expected["min_order_intents"] = 0
        expected["block_reason"] = "ORDER_UNKNOWN_UNRESOLVED"
    return RehearsalScenarioFixture(name=scenario, markets={"KR": kr, "US": us}, expected=expected)


def all_scenarios() -> list[str]:
    return [
        "kr_patha_buy",
        "us_pathb_buy",
        "us_pathb_sell_target",
        "broker_truth_fail_closed",
        "order_unknown_reconcile",
    ]
