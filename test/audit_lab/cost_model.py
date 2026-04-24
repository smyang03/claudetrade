"""Trading cost model for long-horizon simulations.

All audit backtests must use this file for cost assumptions so reports remain
comparable across runs.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    name: str
    buy_bps: float = 0.0
    sell_bps: float = 0.0
    sell_tax_bps: float = 0.0
    slippage_bps: float = 0.0

    @property
    def round_trip_bps(self) -> float:
        return self.buy_bps + self.sell_bps + self.sell_tax_bps + self.slippage_bps * 2

    def net_pnl_pct(self, gross_pnl_pct: float) -> float:
        return round(float(gross_pnl_pct) - self.round_trip_bps / 100.0, 6)

    @classmethod
    def from_name(cls, market: str, name: str = "realistic") -> "CostModel":
        market = str(market or "").upper()
        name = str(name or "realistic").lower()
        if name == "none":
            return cls(name="none")
        if name == "basic":
            if market == "KR":
                return cls(name="basic", buy_bps=1.5, sell_bps=1.5)
            return cls(name="basic")
        if market == "KR":
            # 수수료 0.015% 양방향 + 증권거래세 0.18%(매도) + 슬리피지 0.1% 양방향.
            return cls(name="realistic", buy_bps=1.5, sell_bps=1.5, sell_tax_bps=18.0, slippage_bps=10.0)
        # 미국 주식은 제로 수수료 가정 + 슬리피지 0.05% 양방향.
        return cls(name="realistic", buy_bps=0.0, sell_bps=0.0, sell_tax_bps=0.0, slippage_bps=5.0)
