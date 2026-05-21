from __future__ import annotations

from typing import Any


_SIDE_FIELD_NAMES = {
    "side",
    "order_side",
    "tr_side",
    "sll_buy_dvsn_cd",
    "sll_buy_dvsn",
    "seln_byov_cls",
}

_SELL_TEXT = {"sell", "s", "ask", "\ub9e4\ub3c4"}
_BUY_TEXT = {"buy", "b", "bid", "\ub9e4\uc218"}
_SELL_CODES = {"01", "1"}
_BUY_CODES = {"02", "2"}
_EMPTY_CODES = {"00"}


def canonical_broker_row_side(row: dict[str, Any]) -> tuple[str, bool]:
    """Return canonical broker side and whether any side-like field was present."""
    seen: set[str] = set()
    saw_side_field = False
    if not isinstance(row, dict):
        return "", False

    for field, value in row.items():
        if str(field or "").strip().lower() not in _SIDE_FIELD_NAMES:
            continue
        saw_side_field = True
        raw = str(value or "").strip().lower()
        if not raw:
            continue
        if raw in _SELL_TEXT or raw in _SELL_CODES:
            seen.add("sell")
        elif raw in _BUY_TEXT or raw in _BUY_CODES:
            seen.add("buy")
        elif raw in _EMPTY_CODES:
            continue
        else:
            return "", True
        if len(seen) > 1:
            return "", True

    return (next(iter(seen)) if seen else ""), saw_side_field


def broker_row_side_matches(
    row: dict[str, Any],
    side: str,
    *,
    allow_missing_side: bool = False,
) -> bool:
    wanted = str(side or "").strip().lower()
    if wanted not in {"buy", "sell"}:
        return False
    canonical, saw_side_field = canonical_broker_row_side(row)
    if canonical:
        return canonical == wanted
    return bool(allow_missing_side and not saw_side_field)
