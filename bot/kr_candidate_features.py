from __future__ import annotations

import math
from typing import Any

import pandas as pd


QUALITY_FEATURE_KEYS: tuple[str, ...] = (
    "ret_5d_pct",
    "ret_20d_pct",
    "ret_60d_pct",
    "index_ret_20d_pct",
    "index_ret_60d_pct",
    "rs_20d_vs_board",
    "rs_60d_vs_board",
    "volatility_20d_pct",
    "avg_turnover_20d",
    "turnover_today",
    "turnover_vs_20d",
    "volume_vs_20d",
    "from_52w_high_pct",
    "drawdown_20d_pct",
    "foreign_net_qty_1d",
    "institution_net_qty_1d",
    "individual_net_qty_1d",
    "foreign_net_qty_5d",
    "institution_net_qty_5d",
    "flow_window_5d_count",
    "flow_data_quality",
    "investor_flow_quality",
    "flow_quality_flags",
    "candidate_quality_score",
    "candidate_quality_grade",
    "candidate_quality_components",
    "candidate_quality_flags",
    "quality_data_gaps",
    "quality_source",
)


def enrich_kr_candidate_with_features(
    candidate: dict[str, Any],
    ohlcv: Any,
    *,
    index_ohlcv: Any | None = None,
    flow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = dict(candidate or {})
    features = build_kr_candidate_features(row, ohlcv, index_ohlcv=index_ohlcv, flow=flow)
    row.update(features)
    return row


def build_kr_candidate_features(
    candidate: dict[str, Any],
    ohlcv: Any,
    *,
    index_ohlcv: Any | None = None,
    flow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gaps = _gap_list((candidate or {}).get("quality_data_gaps"))
    frame = _clean_ohlcv(ohlcv)
    if frame.empty:
        return _with_score(
            {
                "quality_source": "kr_candidate_features:v1",
                "quality_data_gaps": gaps + ["ohlcv_missing"],
            }
        )

    features: dict[str, Any] = {
        "quality_source": "kr_candidate_features:v1",
        "quality_data_gaps": gaps,
    }
    close = frame["close"].astype(float)
    high = frame["high"].astype(float) if "high" in frame else close
    volume = frame["volume"].astype(float).clip(lower=0.0)
    turnover_series = (close.clip(lower=0.0) * volume).replace([math.inf, -math.inf], pd.NA)

    for days in (5, 20, 60):
        value = _return_pct(close, days)
        key = f"ret_{days}d_pct"
        if value is None:
            gaps.append(f"{key}_missing")
        else:
            features[key] = value

    vol20 = _volatility_pct(close, 20)
    if vol20 is None:
        gaps.append("volatility_20d_pct_missing")
    else:
        features["volatility_20d_pct"] = vol20

    avg_turnover_20d = _window_mean(turnover_series, 20)
    if avg_turnover_20d is None:
        gaps.append("avg_turnover_20d_missing")
    else:
        features["avg_turnover_20d"] = round(avg_turnover_20d, 2)

    current_price = _positive_float(candidate.get("price")) or _last_float(close)
    current_volume = _positive_float(candidate.get("volume")) or _last_float(volume)
    if current_price is not None and current_volume is not None:
        turnover_today = current_price * current_volume
        features["turnover_today"] = round(turnover_today, 2)
        if avg_turnover_20d and avg_turnover_20d > 0:
            features["turnover_vs_20d"] = round(turnover_today / avg_turnover_20d, 4)
    else:
        gaps.append("turnover_today_missing")

    avg_volume_20d = _window_mean(volume, 20)
    if avg_volume_20d and avg_volume_20d > 0 and current_volume is not None:
        features["volume_vs_20d"] = round(current_volume / avg_volume_20d, 4)
    elif current_volume is None:
        gaps.append("volume_today_missing")
    else:
        gaps.append("avg_volume_20d_missing")

    from_52w = _from_high_pct(close, high, 252)
    if from_52w is None:
        gaps.append("from_52w_high_pct_missing")
    else:
        features["from_52w_high_pct"] = from_52w

    drawdown20 = _from_high_pct(close, high, 20)
    if drawdown20 is None:
        gaps.append("drawdown_20d_pct_missing")
    else:
        features["drawdown_20d_pct"] = drawdown20

    if index_ohlcv is not None:
        features.update(_relative_strength_features(close, index_ohlcv, gaps))

    if flow:
        _apply_flow(features, flow)

    return _with_score(features)


def score_kr_candidate_quality(features: dict[str, Any]) -> tuple[float, str, dict[str, float], list[str]]:
    flags: list[str] = []
    components: dict[str, float] = {}

    avg_turnover = _positive_float(features.get("avg_turnover_20d")) or 0.0
    turnover_vs = _positive_float(features.get("turnover_vs_20d"))
    liquidity = _score_log_scale(avg_turnover, low=500_000_000.0, high=20_000_000_000.0)
    if turnover_vs is not None:
        if turnover_vs < 0.5:
            liquidity *= 0.75
            flags.append("turnover_below_average")
        elif turnover_vs > 8.0:
            liquidity *= 0.85
            flags.append("turnover_spike_extreme")
    components["liquidity"] = liquidity

    rs20 = _optional_float(features.get("rs_20d_vs_board"))
    rs60 = _optional_float(features.get("rs_60d_vs_board"))
    ret20 = _optional_float(features.get("ret_20d_pct"))
    ret60 = _optional_float(features.get("ret_60d_pct"))
    rs_inputs = [value for value in (rs20, rs60) if value is not None]
    if rs_inputs:
        rs_value = sum(_score_centered_pct(value, scale=18.0) for value in rs_inputs) / len(rs_inputs)
    else:
        ret_inputs = [value for value in (ret20, ret60) if value is not None]
        rs_value = sum(_score_centered_pct(value, scale=25.0) for value in ret_inputs) / len(ret_inputs) if ret_inputs else 40.0
        flags.append("rs_board_missing")
    components["relative_strength"] = rs_value

    drawdown = _optional_float(features.get("drawdown_20d_pct"))
    from_52w = _optional_float(features.get("from_52w_high_pct"))
    trend_parts = []
    for value in (ret20, ret60):
        if value is not None:
            trend_parts.append(_score_centered_pct(value, scale=30.0))
    if drawdown is not None:
        trend_parts.append(max(0.0, min(100.0, 100.0 + drawdown * 4.0)))
    if from_52w is not None:
        # Too far below high is weak; exactly at high after a spike is not automatically ideal.
        if from_52w <= -45.0:
            high_score = 20.0
        elif from_52w >= -1.0:
            high_score = 65.0
        else:
            high_score = max(35.0, min(90.0, 90.0 + from_52w * 1.2))
        trend_parts.append(high_score)
    components["trend_quality"] = sum(trend_parts) / len(trend_parts) if trend_parts else 40.0

    flow_values = [
        _optional_float(features.get("foreign_net_qty_1d")),
        _optional_float(features.get("institution_net_qty_1d")),
        _optional_float(features.get("foreign_net_qty_5d")),
        _optional_float(features.get("institution_net_qty_5d")),
    ]
    known_flow = [value for value in flow_values if value is not None]
    if known_flow:
        positives = sum(1 for value in known_flow if value > 0)
        negatives = sum(1 for value in known_flow if value < 0)
        components["flow_support"] = max(0.0, min(100.0, 50.0 + positives * 15.0 - negatives * 12.0))
    else:
        components["flow_support"] = 45.0
        flags.append("flow_missing")

    risk = 100.0
    volatility = _optional_float(features.get("volatility_20d_pct"))
    if volatility is not None:
        if volatility >= 7.5:
            risk -= 30.0
            flags.append("volatility_extreme")
        elif volatility >= 5.0:
            risk -= 18.0
            flags.append("volatility_high")
    if turnover_vs is not None and turnover_vs > 12.0:
        risk -= 20.0
        flags.append("turnover_spike_chase_risk")
    components["risk_adjustment"] = max(0.0, min(100.0, risk))

    weights = {
        "liquidity": 0.25,
        "relative_strength": 0.30,
        "trend_quality": 0.20,
        "flow_support": 0.15,
        "risk_adjustment": 0.10,
    }
    score = sum(components[key] * weight for key, weight in weights.items())
    gaps = features.get("quality_data_gaps")
    severe_gap = isinstance(gaps, list) and "ohlcv_missing" in gaps
    grade = _grade(score, severe_gap=severe_gap)
    return round(score, 2), grade, {k: round(v, 2) for k, v in components.items()}, sorted(set(flags))


def rolling_flow_features(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate cached daily investor-flow records.

    Records should be ordered or unordered daily dictionaries containing foreign
    and institution net quantities. Missing values are ignored, not treated as 0.
    """
    clean = [dict(item or {}) for item in records if isinstance(item, dict)]
    if not clean:
        return {"flow_window_5d_count": 0}
    if any(row.get("date") or row.get("target_date") for row in clean):
        clean.sort(key=lambda row: str(row.get("date") or row.get("target_date") or ""))
    last5 = clean[-5:]

    def _sum(key: str, rows: list[dict[str, Any]]) -> float | None:
        values = [_optional_float(row.get(key)) for row in rows]
        known = [value for value in values if value is not None]
        return round(sum(known), 4) if known else None

    out: dict[str, Any] = {
        "flow_window_5d_count": len(last5),
        "foreign_net_qty_5d": _sum("foreign", last5),
        "institution_net_qty_5d": _sum("institution", last5),
    }
    return {key: value for key, value in out.items() if value is not None}


def _with_score(features: dict[str, Any]) -> dict[str, Any]:
    score, grade, components, flags = score_kr_candidate_quality(features)
    out = dict(features)
    out["candidate_quality_score"] = score
    out["candidate_quality_grade"] = grade
    out["candidate_quality_components"] = components
    if flags:
        out["candidate_quality_flags"] = flags
    out["quality_data_gaps"] = sorted(set(_gap_list(out.get("quality_data_gaps"))))
    return out


def _gap_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _clean_ohlcv(raw: Any) -> pd.DataFrame:
    if raw is None:
        return pd.DataFrame()
    try:
        frame = raw.copy() if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw)
    except Exception:
        return pd.DataFrame()
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame()
    frame.columns = [str(column).lower() for column in frame.columns]
    if "close" not in frame.columns:
        return pd.DataFrame()
    if "high" not in frame.columns:
        frame["high"] = frame["close"]
    if "volume" not in frame.columns:
        frame["volume"] = 0.0
    for column in ("close", "high", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["close"]).reset_index(drop=True)
    return frame


def _return_pct(series: pd.Series, days: int) -> float | None:
    if len(series) <= days:
        return None
    current = _last_float(series)
    base = _optional_float(series.iloc[-days - 1])
    if current is None or base is None or base <= 0:
        return None
    return round((current / base - 1.0) * 100.0, 4)


def _volatility_pct(series: pd.Series, days: int) -> float | None:
    if len(series) <= max(2, days):
        return None
    returns = series.astype(float).pct_change().dropna().tail(days)
    if returns.empty:
        return None
    return round(float(returns.std(ddof=0)) * 100.0, 4)


def _window_mean(series: pd.Series, days: int) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna().tail(days)
    if values.empty:
        return None
    return float(values.mean())


def _from_high_pct(close: pd.Series, high: pd.Series, days: int) -> float | None:
    if close.empty or high.empty:
        return None
    current = _last_float(close)
    window_high = _window_max(high, min(days, len(high)))
    if current is None or window_high is None or window_high <= 0:
        return None
    return round((current - window_high) / window_high * 100.0, 4)


def _window_max(series: pd.Series, days: int) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna().tail(days)
    if values.empty:
        return None
    return float(values.max())


def _relative_strength_features(close: pd.Series, index_ohlcv: Any, gaps: list[str]) -> dict[str, Any]:
    index_frame = _clean_ohlcv(index_ohlcv)
    if index_frame.empty:
        gaps.append("index_history_missing")
        return {}
    out: dict[str, Any] = {}
    index_close = index_frame["close"].astype(float)
    for days in (20, 60):
        stock_ret = _return_pct(close, days)
        index_ret = _return_pct(index_close, days)
        if index_ret is None:
            gaps.append(f"index_ret_{days}d_pct_missing")
            continue
        out[f"index_ret_{days}d_pct"] = index_ret
        if stock_ret is not None:
            out[f"rs_{days}d_vs_board"] = round(stock_ret - index_ret, 4)
    return out


def _apply_flow(features: dict[str, Any], flow: dict[str, Any]) -> None:
    mapping = {
        "foreign": "foreign_net_qty_1d",
        "institution": "institution_net_qty_1d",
        "individual": "individual_net_qty_1d",
        "foreign_net_qty_5d": "foreign_net_qty_5d",
        "institution_net_qty_5d": "institution_net_qty_5d",
        "flow_window_5d_count": "flow_window_5d_count",
    }
    for raw_key, out_key in mapping.items():
        value = _optional_float(flow.get(raw_key))
        if value is not None:
            features[out_key] = value
    quality = str(flow.get("flow_data_quality") or flow.get("investor_flow_quality") or "").strip()
    if quality:
        features["flow_data_quality"] = quality
        features["investor_flow_quality"] = quality
        if quality == "bad_zero_flow_cluster":
            gaps = _gap_list(features.get("quality_data_gaps"))
            gaps.append("flow_invalid_all_zero_cluster")
            features["quality_data_gaps"] = sorted(set(gaps))
            features["flow_values_trusted"] = False
            features["flow_unavailable_reason"] = "all_zero_cluster"
    flags = flow.get("flow_quality_flags")
    if isinstance(flags, (list, tuple, set)):
        clean_flags = [str(flag).strip() for flag in flags if str(flag).strip()]
        if clean_flags:
            features["flow_quality_flags"] = clean_flags


def _score_log_scale(value: float, *, low: float, high: float) -> float:
    if value <= 0:
        return 0.0
    low_log = math.log1p(max(low, 1.0))
    high_log = math.log1p(max(high, low + 1.0))
    value_log = math.log1p(value)
    return max(0.0, min(100.0, (value_log - low_log) / (high_log - low_log) * 100.0))


def _score_centered_pct(value: float, *, scale: float) -> float:
    return max(0.0, min(100.0, 50.0 + (float(value) / max(scale, 1.0)) * 50.0))


def _grade(score: float, *, severe_gap: bool) -> str:
    if severe_gap:
        return "D"
    if score >= 75.0:
        return "A"
    if score >= 60.0:
        return "B"
    if score >= 45.0:
        return "C"
    return "D"


def _positive_float(value: Any) -> float | None:
    parsed = _optional_float(value)
    return parsed if parsed is not None and parsed > 0 else None


def _optional_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(str(value).replace(",", "").strip())
    except Exception:
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _last_float(series: pd.Series) -> float | None:
    if series.empty:
        return None
    return _optional_float(series.iloc[-1])
