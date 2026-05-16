from __future__ import annotations

from typing import Any


US_QUALITY_SHADOW_VERSION = "us_quality_shadow_v1"


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
    return row
