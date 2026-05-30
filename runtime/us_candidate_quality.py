from __future__ import annotations

from typing import Any

from runtime.candidate_quality_labels import FUTURE_LABEL_FIELDS


US_QUALITY_SHADOW_VERSION = "us_quality_shadow_v1"
US_RUNTIME_QUALITY_VERSION = "us_runtime_quality_fallback:v1"
US_RUNTIME_QUALITY_REQUIRED_HISTORY_ROWS = 65


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", ""))
    except Exception:
        return default


def _pct(current: float, past: float) -> float | None:
    if current <= 0 or past <= 0:
        return None
    return (current / past - 1.0) * 100.0


def _series_value(frame: Any, column: str, index: int) -> float:
    try:
        return _as_float(frame.iloc[index][column])
    except Exception:
        return 0.0


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _as_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [str(key) for key, enabled in value.items() if enabled and str(key).strip()]
    try:
        return [str(item) for item in value if str(item).strip()]
    except Exception:
        return [str(value)]


def _grade(score: float) -> str:
    if score >= 80.0:
        return "A"
    if score >= 65.0:
        return "B"
    if score >= 50.0:
        return "C"
    return "D"


def _bucket_score(bucket: str) -> float:
    value = str(bucket or "").strip().lower()
    if value in {"momentum_now", "opening_range_pullback", "liquidity_leader"}:
        return 82.0
    if value in {"gap_pullback", "volume_surge", "most_active", "most_actives"}:
        return 68.0
    if value in {"day_gainer", "day_gainers", "gainers"}:
        return 62.0
    if value in {"unclassified", "unknown", ""}:
        return 45.0
    return 55.0


def _liquidity_score(row: dict[str, Any], gaps: list[str]) -> float:
    turnover = _as_float(row.get("turnover") or row.get("dollar_volume") or row.get("dollar_vol"), 0.0)
    price = _as_float(row.get("price") or row.get("current_price"), 0.0)
    volume = _as_float(row.get("volume"), 0.0)
    if turnover <= 0 and price > 0 and volume > 0:
        turnover = price * volume
    volume_ratio = _as_float(row.get("volume_ratio") or row.get("vol_ratio"), 0.0)
    score = 45.0
    if turnover > 0:
        if turnover >= 100_000_000:
            score += 32.0
        elif turnover >= 25_000_000:
            score += 24.0
        elif turnover >= 5_000_000:
            score += 14.0
        else:
            score += 4.0
    else:
        gaps.append("turnover_missing")
    if volume_ratio > 0:
        score += max(-8.0, min(18.0, (volume_ratio - 1.0) * 7.0))
    else:
        gaps.append("volume_ratio_missing")
    return _clamp(score)


def _momentum_score(row: dict[str, Any], gaps: list[str]) -> float:
    values: list[float] = []
    for key in ("ret_3m_pct", "ret_5m_pct", "ret_10m_pct", "ret_30m_pct"):
        if row.get(key) not in (None, ""):
            values.append(_as_float(row.get(key), 0.0))
    for key in ("us_rs20_shadow", "us_rs60_shadow", "change_pct", "change_rate"):
        if row.get(key) not in (None, ""):
            values.append(_as_float(row.get(key), 0.0))
    if not values:
        gaps.append("recent_momentum_missing")
        return 48.0
    avg = sum(values) / max(1, len(values))
    return _clamp(52.0 + avg * 4.0)


def _history_score(row: dict[str, Any], gaps: list[str]) -> float:
    required = int(_as_float(row.get("history_required_rows"), 0.0))
    usable = int(_as_float(row.get("history_usable_rows"), 0.0))
    if required <= 0:
        gaps.append("history_required_rows_missing")
        return 58.0
    ratio = _clamp(usable / max(1, required), 0.0, 1.0)
    if ratio < 1.0:
        gaps.append("history_incomplete")
    return _clamp(35.0 + ratio * 65.0)


def enrich_us_runtime_quality_fallback(
    candidate: dict[str, Any],
    candles: Any = None,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Attach a live-known common candidate quality score for US rows."""

    row = dict(candidate or {})
    if not overwrite and row.get("candidate_quality_score") not in (None, ""):
        return row
    gaps = _as_list(row.get("quality_data_gaps")) + _as_list(row.get("bucket_data_gaps"))
    gaps += _as_list(row.get("us_quality_data_gaps"))

    if candles is not None:
        try:
            candle_rows = int(len(candles))
        except Exception:
            candle_rows = 0
        if row.get("history_usable_rows") in (None, ""):
            row["history_usable_rows"] = candle_rows
        if row.get("history_required_rows") in (None, ""):
            row["history_required_rows"] = US_RUNTIME_QUALITY_REQUIRED_HISTORY_ROWS

    liquidity = _liquidity_score(row, gaps)
    momentum = _momentum_score(row, gaps)
    history = _history_score(row, gaps)
    bucket = _bucket_score(str(row.get("primary_bucket") or row.get("category") or ""))

    data_gap_count = len({gap for gap in gaps if gap})
    risk_penalty = min(18.0, data_gap_count * 2.5)
    data_quality = str(row.get("data_quality") or row.get("history_status") or "").strip().lower()
    if data_quality in {"bad", "missing", "invalid", "history_unavailable", "data_insufficient"}:
        risk_penalty += 10.0

    score = (
        liquidity * 0.25
        + momentum * 0.30
        + history * 0.20
        + bucket * 0.15
        + 10.0
        - risk_penalty
    )
    score = _clamp(score)
    ignored_future = sorted(key for key in FUTURE_LABEL_FIELDS if key in row)
    flags: list[str] = []
    if data_gap_count:
        flags.append("quality_data_gap")
    if "history_incomplete" in gaps:
        flags.append("history_incomplete")
    if ignored_future:
        flags.append("future_fields_ignored")

    components = {
        "version": US_RUNTIME_QUALITY_VERSION,
        "liquidity_turnover": round(liquidity, 4),
        "recent_momentum": round(momentum, 4),
        "history_completeness": round(history, 4),
        "bucket_support": round(bucket, 4),
        "risk_data_gap_penalty": round(-risk_penalty, 4),
        "ignored_future_fields": ignored_future,
    }
    row.update(
        {
            "candidate_quality_score": round(score, 4),
            "candidate_quality_grade": _grade(score),
            "candidate_quality_components": components,
            "candidate_quality_flags": sorted(set(_as_list(row.get("candidate_quality_flags")) + flags)),
            "quality_data_gaps": sorted({gap for gap in gaps if gap}),
            "quality_source": US_RUNTIME_QUALITY_VERSION,
        }
    )
    return row


def enrich_us_quality_shadow(candidate: dict[str, Any], candles: Any = None) -> dict[str, Any]:
    """Attach live-known US quality shadow metrics without changing rank/routing."""

    row = dict(candidate or {})
    gaps: list[str] = []
    score = 50.0

    close_now = 0.0
    close_20 = 0.0
    close_60 = 0.0
    volume_now = 0.0
    volume_20_avg = 0.0
    if candles is not None:
        try:
            if len(candles) >= 1:
                close_now = _series_value(candles, "close", -1)
                volume_now = _series_value(candles, "volume", -1)
            if len(candles) >= 21:
                close_20 = _series_value(candles, "close", -21)
                try:
                    volume_20_avg = float(candles["volume"].tail(20).mean())
                except Exception:
                    volume_20_avg = 0.0
            if len(candles) >= 61:
                close_60 = _series_value(candles, "close", -61)
        except Exception:
            gaps.append("ohlcv_parse_error")

    if close_now <= 0:
        close_now = _as_float(row.get("price"))
    rs20 = _pct(close_now, close_20)
    rs60 = _pct(close_now, close_60)
    if rs20 is None:
        gaps.append("rs20_missing")
    else:
        row["us_rs20_shadow"] = round(rs20, 4)
        score += max(-12.0, min(12.0, rs20 * 0.6))
    if rs60 is None:
        gaps.append("rs60_missing")
    else:
        row["us_rs60_shadow"] = round(rs60, 4)
        score += max(-10.0, min(10.0, rs60 * 0.35))

    if volume_now > 0 and volume_20_avg > 0:
        vol_surge = volume_now / volume_20_avg
        row["us_volume_surge_shadow"] = round(vol_surge, 4)
        score += max(-5.0, min(8.0, (vol_surge - 1.0) * 4.0))
    else:
        gaps.append("volume_surge_missing")

    liquidity = str(row.get("liquidity_bucket") or "").strip().lower()
    if liquidity == "high":
        score += 6.0
    elif liquidity in {"mid", "low"}:
        score -= 4.0

    from_high = _as_float(row.get("from_high_pct"), 0.0)
    if row.get("from_high_pct") not in (None, ""):
        if -8.0 <= from_high <= -1.0:
            score += 4.0
        elif from_high > -0.5:
            score -= 3.0
        row["us_from_high_shadow"] = round(from_high, 4)
    else:
        gaps.append("from_high_missing")

    row["us_quality_shadow_version"] = US_QUALITY_SHADOW_VERSION
    row["us_quality_score_shadow"] = round(max(0.0, min(100.0, score)), 4)
    row["us_quality_data_gaps"] = sorted(set(gaps))
    row["us_quality_shadow_only"] = True
    return enrich_us_runtime_quality_fallback(row, candles)
