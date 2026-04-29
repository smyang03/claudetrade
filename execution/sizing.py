from __future__ import annotations

from dataclasses import dataclass
import math

from config.v2 import V2Config, DEFAULT_V2_CONFIG


@dataclass(frozen=True)
class FixedSizingResult:
    market: str
    qty: int
    budget_krw: float
    order_cost_krw: float
    min_order_krw: float
    price_krw: float

    @property
    def min_order_met(self) -> bool:
        return self.qty > 0 and self.order_cost_krw >= self.min_order_krw


class FixedSizer:
    def __init__(self, config: V2Config = DEFAULT_V2_CONFIG):
        self.config = config

    def fixed_budget_krw(self, market: str, usd_krw: float = 0.0) -> float:
        market_key = str(market or "").upper()
        if market_key == "US":
            if float(getattr(self.config, "us_fixed_order_krw", 0) or 0) > 0:
                return float(self.config.us_fixed_order_krw)
            return float(self.config.us_fixed_order_usd) * float(usd_krw or 0.0)
        return float(self.config.kr_fixed_order_krw)

    def min_order_krw(self, market: str, usd_krw: float = 0.0) -> float:
        market_key = str(market or "").upper()
        if market_key == "US":
            if float(getattr(self.config, "us_min_order_krw", 0) or 0) > 0:
                return float(self.config.us_min_order_krw)
            return float(self.config.us_min_order_usd) * float(usd_krw or 0.0)
        return float(self.config.kr_min_order_krw)

    def size(self, *, market: str, price_krw: float, usd_krw: float = 0.0, cash_krw: float | None = None) -> FixedSizingResult:
        price = float(price_krw or 0.0)
        budget = self.fixed_budget_krw(market, usd_krw=usd_krw)
        if cash_krw is not None:
            budget = min(budget, max(0.0, float(cash_krw or 0.0)))
        qty = int(budget // price) if price > 0 and budget > 0 else 0
        min_order = self.min_order_krw(market, usd_krw=usd_krw)
        if price > 0 and min_order > 0 and qty * price < min_order:
            min_qty = int(math.ceil(min_order / price))
            min_cost = min_qty * price
            if cash_krw is None or min_cost <= float(cash_krw or 0.0):
                qty = min_qty
        return FixedSizingResult(
            market=str(market or "").upper(),
            qty=max(0, qty),
            budget_krw=budget,
            order_cost_krw=max(0, qty) * price,
            min_order_krw=min_order,
            price_krw=price,
        )
