from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_US_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]*$")
VALID_MARKETS = {"KR", "US"}


def normalize_market(value: Any) -> str:
    market = str(value or "").strip().upper()
    return market if market in VALID_MARKETS else ""


def _unknown_market(unknown: str) -> str:
    return normalize_market(unknown) or ""


def infer_ticker_market(ticker: Any, *, unknown: str = "KR") -> str:
    raw = str(ticker or "").strip().upper()
    if not raw:
        return _unknown_market(unknown)
    if raw.isdigit():
        return "KR"
    if _US_TICKER_RE.fullmatch(raw):
        return "US"
    return _unknown_market(unknown)


def resolve_position_market(pos: Mapping[str, Any] | None, *, unknown: str = "KR") -> str:
    if not isinstance(pos, Mapping):
        return _unknown_market(unknown)
    market = normalize_market(pos.get("market"))
    if market:
        return market
    currency = str(pos.get("display_currency") or pos.get("currency") or "").strip().upper()
    if currency == "USD":
        return "US"
    if currency == "KRW":
        return "KR"
    return infer_ticker_market(pos.get("ticker"), unknown=unknown)
