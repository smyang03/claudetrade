"""Yahoo Finance secondary-quote helpers for Korean-market shadow monitoring.

The KIS quote remains authoritative.  This module deliberately has no broker,
selection, or order imports: Yahoo data is used only to measure provider
coverage, timestamp freshness, and price divergence in an append-only shadow
log.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Callable
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
PRIMARY_PRICE_SOURCE = "kis_api.get_price.provider_fresh"


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _to_kst(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
        if not isinstance(value, datetime):
            value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if value.tzinfo is None:
            # Yahoo's Korea intraday bars are exchange-local when tz metadata is
            # omitted.  Treating them as UTC would introduce a false nine-hour
            # freshness alert.
            value = value.replace(tzinfo=KST)
        return value.astimezone(KST)
    except (TypeError, ValueError, OverflowError):
        return None


def _iso_kst(value: Any) -> str:
    parsed = _to_kst(value)
    return parsed.isoformat(timespec="seconds") if parsed is not None else ""


def yahoo_symbols_for_kr(ticker: str) -> tuple[str, ...]:
    """Return the two Yahoo exchange suffixes without guessing market board."""
    code = str(ticker or "").strip().upper()
    if not (len(code) == 6 and code.isdigit()):
        return ()
    return (f"{code}.KS", f"{code}.KQ")


def _frame_value(frame: Any, name: str) -> Any:
    try:
        columns = {str(column).lower(): column for column in frame.columns}
        column = columns.get(str(name).lower())
        if column is None or len(frame.index) <= 0:
            return None
        return frame.iloc[-1][column]
    except (AttributeError, IndexError, KeyError, TypeError):
        return None


def _frame_timestamp(frame: Any) -> datetime | None:
    try:
        if len(frame.index) <= 0:
            return None
        return _to_kst(frame.index[-1])
    except (AttributeError, IndexError, TypeError):
        return None


def _default_history_fetcher(symbol: str):
    import yfinance as yf

    return yf.Ticker(symbol).history(
        period="1d",
        interval="1m",
        auto_adjust=False,
        prepost=False,
    )


def fetch_kr_yfinance_quote(
    ticker: str,
    *,
    history_fetcher: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Fetch the latest Yahoo intraday bar for a Korean ticker.

    A missing quote is data about the secondary provider, not an invitation to
    substitute a price.  The caller receives a typed status instead of an
    exception so all attempted comparisons are auditable.
    """
    fetcher = history_fetcher or _default_history_fetcher
    errors: list[str] = []
    for symbol in yahoo_symbols_for_kr(ticker):
        try:
            frame = fetcher(symbol)
        except Exception as exc:  # provider/network errors are shadow-only
            errors.append(f"{symbol}:{type(exc).__name__}")
            continue
        try:
            if frame is None or bool(frame.empty):
                errors.append(f"{symbol}:empty")
                continue
        except AttributeError:
            errors.append(f"{symbol}:invalid_frame")
            continue
        price = _number(_frame_value(frame, "Close"))
        if price is None or price <= 0:
            errors.append(f"{symbol}:close_missing")
            continue
        bar_at = _frame_timestamp(frame)
        return {
            "status": "ok",
            "provider": "yfinance",
            "ticker": str(ticker or "").strip(),
            "symbol": symbol,
            "price": price,
            "open": _number(_frame_value(frame, "Open")),
            "high": _number(_frame_value(frame, "High")),
            "low": _number(_frame_value(frame, "Low")),
            "volume": _number(_frame_value(frame, "Volume")),
            "bar_at": bar_at.isoformat(timespec="seconds") if bar_at is not None else "",
            "errors": errors,
        }
    return {
        "status": "missing",
        "provider": "yfinance",
        "ticker": str(ticker or "").strip(),
        "symbol": "",
        "price": None,
        "bar_at": "",
        "errors": errors or ["invalid_ticker"],
    }


def compare_kr_primary_to_yfinance(
    primary: dict[str, Any],
    secondary: dict[str, Any],
    *,
    captured_at: datetime | None = None,
    divergence_warn_pct: float = 1.0,
    max_stale_min: float = 20.0,
) -> dict[str, Any]:
    """Return a comparison record; never promote Yahoo data to authoritative."""
    now = (captured_at or datetime.now(KST)).astimezone(KST)
    primary_price = _number(primary.get("price"))
    secondary_price = _number(secondary.get("price"))
    primary_at = _iso_kst(primary.get("captured_at"))
    bar_at = _to_kst(secondary.get("bar_at"))
    payload = {
        "ticker": str(primary.get("ticker") or secondary.get("ticker") or "").strip(),
        "primary_provider": "kis",
        "primary_price_source": str(primary.get("price_source") or ""),
        "primary_price": primary_price,
        "primary_captured_at": primary_at,
        "secondary_provider": "yfinance",
        "secondary_status": str(secondary.get("status") or "missing"),
        "secondary_symbol": str(secondary.get("symbol") or ""),
        "secondary_price": secondary_price,
        "secondary_bar_at": bar_at.isoformat(timespec="seconds") if bar_at is not None else "",
        "secondary_errors": list(secondary.get("errors") or []),
        "execution_eligible": False,
        "selection_input": False,
    }
    if primary_price is None or primary_price <= 0:
        payload["comparison_status"] = "primary_missing"
        return payload
    if str(secondary.get("status") or "") != "ok" or secondary_price is None or secondary_price <= 0:
        payload["comparison_status"] = "secondary_missing"
        return payload

    diff = secondary_price - primary_price
    diff_pct = (diff / primary_price) * 100.0
    payload["price_diff"] = round(diff, 4)
    payload["price_diff_pct"] = round(diff_pct, 4)
    stale_min = None
    if bar_at is not None:
        stale_min = max(0.0, (now - bar_at).total_seconds() / 60.0)
    payload["secondary_stale_min"] = round(stale_min, 2) if stale_min is not None else None

    stale = stale_min is None or stale_min > max(1.0, float(max_stale_min))
    divergent = abs(diff_pct) > max(0.0, float(divergence_warn_pct))
    if stale and divergent:
        payload["comparison_status"] = "stale_and_divergent"
    elif stale:
        payload["comparison_status"] = "secondary_stale"
    elif divergent:
        payload["comparison_status"] = "price_divergent"
    else:
        payload["comparison_status"] = "within_tolerance"
    return payload


def select_fresh_kis_primary_samples(
    records: list[dict[str, Any]],
    *,
    rank_by_ticker: dict[str, int] | None = None,
    max_tickers: int = 12,
) -> list[dict[str, Any]]:
    """Choose only explicitly provider-fresh KIS samples, one latest row/ticker."""
    latest: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for raw in records:
        row = dict(raw or {})
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker or str(row.get("price_source") or "") != PRIMARY_PRICE_SOURCE:
            continue
        price = _number(row.get("price"))
        captured = _to_kst(row.get("captured_at"))
        if price is None or price <= 0 or captured is None:
            continue
        current = latest.get(ticker)
        if current is None or captured >= current[0]:
            row["ticker"] = ticker
            latest[ticker] = (captured, row)
    ranks = {str(key).strip().upper(): int(value) for key, value in (rank_by_ticker or {}).items() if str(key).strip()}
    ordered = sorted(
        (row for _, row in latest.values()),
        key=lambda row: (ranks.get(str(row.get("ticker") or "").upper(), 999999), str(row.get("ticker") or "")),
    )
    return ordered[: max(0, int(max_tickers))]
