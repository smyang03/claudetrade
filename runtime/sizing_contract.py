from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


SIZE_INTENT_RATIOS = {
    "micro": 0.10,
    "probe": 0.30,
    "reduced": 0.50,
    "normal": 1.00,
    "add": 0.30,
    "none": 0.0,
}


@dataclass
class SizingDecision:
    qty: int
    notional: float
    blocker: str | None = None
    warnings: list[str] = field(default_factory=list)
    size_intent: str = "normal"
    effective_budget: float = 0.0
    hard_budget_cap: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "qty": self.qty,
            "notional": self.notional,
            "blocker": self.blocker,
            "warnings": list(self.warnings),
            "size_intent": self.size_intent,
            "effective_budget": self.effective_budget,
            "hard_budget_cap": self.hard_budget_cap,
        }


def budget_for_size_intent(
    base_budget: float,
    *,
    size_intent: str,
    size_cap_pct: int | None = None,
) -> float:
    intent = str(size_intent or "normal").lower()
    ratio = SIZE_INTENT_RATIOS.get(intent, SIZE_INTENT_RATIOS["normal"])
    budget = max(0.0, float(base_budget or 0.0) * ratio)
    if size_cap_pct is not None:
        budget *= max(1, min(100, int(size_cap_pct))) / 100.0
    return budget


def calculate_order_quantity(
    *,
    price: float,
    base_budget: float,
    hard_budget_cap: float,
    cash_available: float,
    min_order: float = 0.0,
    size_intent: str = "normal",
    size_cap_pct: int | None = None,
    allow_one_share_over_budget: bool = False,
    one_share_max_account_pct: float = 0.0,
    total_equity: float = 0.0,
) -> SizingDecision:
    px = float(price or 0.0)
    hard_cap = max(0.0, float(hard_budget_cap or 0.0))
    if px <= 0:
        return SizingDecision(0, 0.0, blocker="invalid_price", size_intent=size_intent, hard_budget_cap=hard_cap)
    effective_budget = min(
        hard_cap if hard_cap > 0 else float(base_budget or 0.0),
        budget_for_size_intent(base_budget, size_intent=size_intent, size_cap_pct=size_cap_pct),
        float(cash_available or 0.0),
    )
    qty_by_budget = int(effective_budget // px) if effective_budget > 0 else 0
    min_qty = int(math.ceil(float(min_order or 0.0) / px)) if min_order and min_order > 0 else 0
    candidate_qty = max(qty_by_budget, min_qty)
    notional = candidate_qty * px
    if candidate_qty <= 0 and hard_cap > 0 and px > hard_cap and allow_one_share_over_budget:
        candidate_qty = 1
        notional = px
    if candidate_qty <= 0:
        if hard_cap > 0 and px > hard_cap:
            return SizingDecision(
                0,
                0.0,
                blocker="high_price_one_share_blocked",
                size_intent=size_intent,
                effective_budget=effective_budget,
                hard_budget_cap=hard_cap,
            )
        return SizingDecision(
            0,
            0.0,
            blocker="qty_zero",
            size_intent=size_intent,
            effective_budget=effective_budget,
            hard_budget_cap=hard_cap,
        )
    if notional > float(cash_available or 0.0):
        return SizingDecision(
            0,
            0.0,
            blocker="cash_shortfall",
            size_intent=size_intent,
            effective_budget=effective_budget,
            hard_budget_cap=hard_cap,
        )
    if hard_cap > 0 and notional > hard_cap:
        if candidate_qty == 1 and allow_one_share_over_budget:
            account_pct = (notional / float(total_equity or 1.0)) * 100.0 if total_equity else 100.0
            if one_share_max_account_pct and account_pct <= float(one_share_max_account_pct):
                return SizingDecision(
                    1,
                    notional,
                    warnings=["one_share_over_budget_allowed"],
                    size_intent=size_intent,
                    effective_budget=effective_budget,
                    hard_budget_cap=hard_cap,
                )
        blocker = "high_price_one_share_blocked" if candidate_qty == 1 else "budget_cap"
        return SizingDecision(
            0,
            0.0,
            blocker=blocker,
            size_intent=size_intent,
            effective_budget=effective_budget,
            hard_budget_cap=hard_cap,
        )
    if min_order and notional < float(min_order):
        return SizingDecision(
            0,
            0.0,
            blocker="min_order_not_met",
            size_intent=size_intent,
            effective_budget=effective_budget,
            hard_budget_cap=hard_cap,
        )
    return SizingDecision(
        candidate_qty,
        notional,
        size_intent=size_intent,
        effective_budget=effective_budget,
        hard_budget_cap=hard_cap,
    )


def probe_stop_weight(
    *,
    order_notional: float,
    normal_order_notional: float,
    default_probe_weight: float = 0.25,
) -> float:
    normal = float(normal_order_notional or 0.0)
    if normal <= 0:
        return max(0.0, min(1.0, float(default_probe_weight)))
    return max(0.0, min(1.0, float(order_notional or 0.0) / normal))
