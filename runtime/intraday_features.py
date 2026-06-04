from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from runtime.post_open_features import build_post_open_snapshot, pct_change, returns_from_price_history


RETURN_FIELDS = ("ret_3m_pct", "ret_5m_pct", "ret_10m_pct", "ret_30m_pct")
SESSION_MINUTES = {"KR": 390.0, "US": 390.0}
OPENING_RANGE_MIN = {"KR": 10, "US": 15}


def _market_key(market: str) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


def _ticker_key(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if _market_key(market) == "US" else text


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(str(value).replace(",", ""))
    except Exception:
        return None
    if parsed != parsed:
        return None
    return parsed


def _positive(value: Any) -> float | None:
    parsed = _num(value)
    return parsed if parsed is not None and parsed > 0 else None


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _ts_from_row(row: dict[str, Any]) -> datetime | None:
    direct = _first(row, "ts", "timestamp", "datetime", "DateTime", "date_time")
    parsed = _parse_dt(direct)
    if parsed is not None:
        return parsed
    date_raw = _first(row, "date", "Date", "stck_bsop_date", "xymd", "bas_dt")
    time_raw = _first(row, "time", "Time", "stck_cntg_hour", "hour")
    if date_raw in (None, "") or time_raw in (None, ""):
        return None
    date_text = str(date_raw).strip()
    time_text = str(time_raw).strip().split(".")[0].zfill(6)
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d%H%M%S"):
        try:
            return datetime.strptime(date_text + time_text, fmt)
        except Exception:
            continue
    return None


def _records(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if hasattr(raw, "to_dict"):
        try:
            return list(raw.to_dict("records"))
        except Exception:
            return []
    if isinstance(raw, dict):
        return [raw]
    try:
        return [dict(item) for item in raw or [] if isinstance(item, dict)]
    except Exception:
        return []


def normalize_intraday_candles(raw: Any, *, market: str, ticker: str, source: str = "") -> list[dict[str, Any]]:
    market_key = _market_key(market)
    ticker_key = _ticker_key(market_key, ticker)
    by_ts: dict[str, dict[str, Any]] = {}
    for row in _records(raw):
        ts = _ts_from_row(row)
        close = _positive(_first(row, "close", "Close", "stck_prpr", "last", "price"))
        if ts is None or close is None:
            continue
        open_price = _positive(_first(row, "open", "Open", "stck_oprc", "opn")) or close
        high = _positive(_first(row, "high", "High", "stck_hgpr", "hgpr")) or max(open_price, close)
        low = _positive(_first(row, "low", "Low", "stck_lwpr", "lwpr")) or min(open_price, close)
        high = max(high, open_price, close)
        low = min(low, open_price, close)
        volume = _num(_first(row, "volume", "Volume", "cntg_vol", "tvol", "acml_vol")) or 0.0
        iso = ts.isoformat(timespec="seconds")
        by_ts[iso] = {
            "ts": iso,
            "ticker": ticker_key,
            "market": market_key,
            "open": float(open_price),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": max(0.0, float(volume)),
            "source": str(source or row.get("source") or ""),
        }
    return [by_ts[key] for key in sorted(by_ts)]


def _missing_fields(features: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if _positive(features.get("current_price")) is None:
        missing.append("current_price")
    if all(_num(features.get(field)) is None for field in ("ret_3m_pct", "ret_5m_pct")):
        missing.extend(["ret_3m_pct", "ret_5m_pct"])
    if features.get("opening_range_break") is None:
        missing.append("opening_range_break")
    if _num(features.get("vwap_distance_pct")) is None:
        missing.append("vwap_distance_pct")
    if _num(features.get("volume_ratio_open")) is None:
        missing.append("volume_ratio_open")
    return list(dict.fromkeys(missing))


def _quality(missing: list[str], bar_count: int) -> str:
    if bar_count <= 0 or "current_price" in missing:
        return "minute_missing"
    return "minute_complete" if not missing else "minute_partial"


def classify_intraday_feature_quality(features: dict[str, Any]) -> tuple[str, list[str]]:
    missing = _missing_fields(features)
    return _quality(missing, int(features.get("bar_count") or 0)), missing


def compute_intraday_features(
    candles: Any,
    *,
    market: str,
    ticker: str,
    regular_open: Any,
    known_at: Any,
    avg_daily_volume: float | None = None,
    opening_range_min: int | None = None,
    source: str = "",
) -> dict[str, Any]:
    market_key = _market_key(market)
    ticker_key = _ticker_key(market_key, ticker)
    open_dt = _parse_dt(regular_open)
    known_dt = _parse_dt(known_at)
    if open_dt is None:
        open_dt = known_dt or datetime.now()
    if known_dt is None:
        known_dt = datetime.now()
    normalized = normalize_intraday_candles(candles, market=market_key, ticker=ticker_key, source=source)
    usable = [
        candle
        for candle in normalized
        if (parsed := _parse_dt(candle.get("ts"))) is not None and open_dt <= parsed <= known_dt
    ]
    base = {
        "ticker": ticker_key,
        "market": market_key,
        "known_at": known_dt.isoformat(timespec="seconds"),
        "anchor_at": open_dt.isoformat(timespec="seconds"),
        "anchor_price": None,
        "current_price": None,
        "bar_count": len(usable),
        "source": str(source or (usable[-1].get("source") if usable else "")),
    }
    if not usable:
        base.update({field: None for field in RETURN_FIELDS})
        base.update(
            {
                "from_open_high_pct": None,
                "pullback_from_high_pct": None,
                "opening_range_high": None,
                "opening_range_low": None,
                "opening_range_break": None,
                "vwap": None,
                "vwap_distance_pct": None,
                "volume_ratio_open": None,
                "momentum_state": "unknown",
            }
        )
        base["data_quality"], base["missing_fields"] = classify_intraday_feature_quality(base)
        return base

    first = usable[0]
    latest = usable[-1]
    anchor_price = _positive(first.get("open")) or _positive(first.get("close")) or 0.0
    current_price = _positive(latest.get("close")) or anchor_price
    history = [{"ts": row["ts"], "price": row["close"]} for row in usable]
    returns = returns_from_price_history(
        history,
        anchor_at=open_dt.isoformat(timespec="seconds"),
        anchor_price=float(anchor_price),
        known_at=known_dt.isoformat(timespec="seconds"),
    )
    open_high = max(float(row.get("high") or 0.0) for row in usable)
    opening_high = None
    opening_low = None
    or_minutes = int(opening_range_min or OPENING_RANGE_MIN.get(market_key, 15))
    range_end = open_dt + timedelta(minutes=max(1, or_minutes))
    if known_dt >= range_end:
        range_rows = [
            row
            for row in usable
            if (parsed := _parse_dt(row.get("ts"))) is not None and parsed <= range_end
        ]
        if range_rows:
            opening_high = max(float(row.get("high") or 0.0) for row in range_rows)
            opening_low = min(float(row.get("low") or 0.0) for row in range_rows)

    volume_sum = sum(float(row.get("volume") or 0.0) for row in usable)
    vwap = None
    vwap_distance = None
    if volume_sum > 0:
        vwap = sum(float(row.get("close") or 0.0) * float(row.get("volume") or 0.0) for row in usable) / volume_sum
        vwap_distance = pct_change(float(current_price), vwap)

    volume_ratio = None
    avg_volume = _positive(avg_daily_volume)
    if avg_volume and volume_sum > 0:
        elapsed_min = max(1.0, (known_dt - open_dt).total_seconds() / 60.0)
        session_min = SESSION_MINUTES.get(market_key, 390.0)
        expected = avg_volume * min(1.0, elapsed_min / session_min)
        if expected > 0:
            volume_ratio = volume_sum / expected

    snapshot = build_post_open_snapshot(
        market=market_key,
        ticker=ticker_key,
        known_at=known_dt.isoformat(timespec="seconds"),
        anchor_at=open_dt.isoformat(timespec="seconds"),
        anchor_price=float(anchor_price),
        current_price=float(current_price),
        returns=returns,
        open_high=open_high,
        opening_range_high=opening_high,
        volume_ratio_open=volume_ratio,
        vwap_distance_pct=vwap_distance,
        data_quality="minute_partial",
        market_session_date=open_dt.date().isoformat(),
    ).to_dict()
    snapshot.update(
        {
            "opening_range_high": opening_high,
            "opening_range_low": opening_low,
            "vwap": vwap,
            "bar_count": len(usable),
            "source": str(source or latest.get("source") or "intraday_minute"),
        }
    )
    snapshot["data_quality"], snapshot["missing_fields"] = classify_intraday_feature_quality(snapshot)
    return snapshot
