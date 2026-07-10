from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


_ARTIFACT_CACHE: dict[str, tuple[int, dict[str, Any]]] = {}


def _enabled(market: str) -> bool:
    market_key = str(market or "").upper()
    raw = os.getenv(f"PROFIT_PATH_SHADOW_ENABLED_{market_key}")
    if raw in (None, ""):
        raw = os.getenv("PROFIT_PATH_SHADOW_ENABLED", "false")
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _artifact_path(market: str) -> Path:
    market_key = str(market or "").upper()
    template = os.getenv(f"PROFIT_PATH_MODEL_PATH_{market_key}") or os.getenv(
        "PROFIT_PATH_MODEL_PATH", "state/models/profit_path_{market}.json"
    )
    try:
        return Path(str(template).format(market=market_key))
    except (KeyError, ValueError):
        return Path(str(template))


def _load_artifact(market: str) -> tuple[dict[str, Any], str]:
    path = _artifact_path(market)
    try:
        stat = path.stat()
    except OSError:
        return {}, str(path)
    key = str(path.resolve())
    cached = _ARTIFACT_CACHE.get(key)
    if cached and cached[0] == stat.st_mtime_ns:
        return cached[1], str(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, str(path)
    artifact = dict(payload) if isinstance(payload, dict) else {}
    _ARTIFACT_CACHE[key] = (stat.st_mtime_ns, artifact)
    return artifact, str(path)


def _num(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _merge_sources(context: Any, sources: Sequence[Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for source in [*sources, context]:
        if isinstance(source, Mapping):
            output.update(dict(source))
    return output


def _post_open(row: Mapping[str, Any], sources: Sequence[Any], market: str, ticker: str) -> dict[str, Any]:
    direct = row.get("post_open_features")
    if isinstance(direct, Mapping):
        return dict(direct)
    ticker_key = str(ticker or "").upper() if str(market or "").upper() == "US" else str(ticker or "")
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for key in ("_post_open_features_by_ticker", "post_open_features_by_ticker"):
            values = source.get(key)
            if not isinstance(values, Mapping):
                continue
            candidate = values.get(ticker_key) or values.get(ticker)
            if isinstance(candidate, Mapping):
                return dict(candidate)
    return {}


def _path_name(strategy: str, row: Mapping[str, Any]) -> str:
    explicit = str(row.get("profit_path_name") or row.get("path_name") or "").strip()
    if explicit:
        return explicit
    normalized = str(strategy or "").strip().lower()
    if normalized in {"path_b", "pathb", "claude_price"} or normalized.startswith("path_b"):
        reason = str(row.get("signal_reason") or row.get("reason") or "").lower()
        if "vwap" in reason:
            return "vwap_reclaim"
        return "pullback_reclaim"
    return "immediate"


def build_runtime_feature_row(
    *,
    market: str,
    ticker: str,
    strategy: str,
    context: Any = None,
    sources: Sequence[Any] = (),
) -> dict[str, Any]:
    row = _merge_sources(context, sources)
    post_open = _post_open(row, sources, market, ticker)
    candidate_price = _num(
        _first(row, "candidate_price", "price", "current_price", "close", "raw_price", "entry_price")
        or _first(post_open, "current_price")
    )
    entry_price = _num(_first(row, "entry_price", "limit_price", "order_price", "raw_price"))
    entry_vs_candidate = None
    if candidate_price and candidate_price > 0 and entry_price and entry_price > 0:
        entry_vs_candidate = (entry_price / candidate_price - 1.0) * 100.0
    feature = {
        "candidate_price": candidate_price,
        "change_pct": _num(_first(row, "change_pct", "change", "chg_pct")),
        "volume_ratio": _num(_first(row, "volume_ratio", "vol_ratio", "rvol")),
        "from_high_pct": _num(_first(row, "from_high_pct") or _first(post_open, "pullback_from_high_pct")),
        "raw_score_current": _num(_first(row, "raw_score_current", "screen_score", "score")),
        "entry_delay_min": _num(
            _first(row, "entry_delay_min", "candidate_age_min") or _first(post_open, "market_open_elapsed_min")
        ),
        "entry_vs_candidate_pct": entry_vs_candidate,
        "market_open_elapsed_min": _num(_first(post_open, "market_open_elapsed_min")),
        "ret_3m_pct": _num(_first(post_open, "ret_3m_pct") or _first(row, "ret_3m_pct")),
        "ret_5m_pct": _num(_first(post_open, "ret_5m_pct") or _first(row, "ret_5m_pct")),
        "ret_10m_pct": _num(_first(post_open, "ret_10m_pct") or _first(row, "ret_10m_pct")),
        "ret_30m_pct": _num(_first(post_open, "ret_30m_pct") or _first(row, "ret_30m_pct")),
        "volume_ratio_open": _num(_first(post_open, "volume_ratio_open", "volume_acceleration")),
        "vwap_distance_pct": _num(_first(post_open, "vwap_distance_pct")),
        "pullback_from_high_pct": _num(_first(post_open, "pullback_from_high_pct")),
        "path_name": _path_name(strategy, row),
        "primary_bucket": str(_first(row, "primary_bucket") or "__MISSING__"),
        "recommended_strategy": str(_first(row, "recommended_strategy") or strategy or "__MISSING__"),
        "candidate_source": str(_first(row, "candidate_source", "source") or "__MISSING__"),
        "liquidity_bucket": str(_first(row, "liquidity_bucket") or "__MISSING__"),
        "market_type": str(_first(row, "market_type") or "__MISSING__"),
        "consensus_mode": str(_first(row, "consensus_mode", "mode") or "__MISSING__"),
        "mode_family": str(_first(row, "mode_family") or "__MISSING__"),
        "route_source": str(_first(row, "route_source", "source") or "__MISSING__"),
        "data_quality": str(_first(post_open, "data_quality") or _first(row, "data_quality") or "__MISSING__"),
    }
    return feature


def _uncertainty(artifact: Mapping[str, Any], probability: float) -> float:
    raw_probability = artifact.get("calibration_probability")
    raw_labels = artifact.get("calibration_labels")
    calibration_probability = np.asarray([] if raw_probability is None else raw_probability, dtype=float)
    calibration_labels = np.asarray([] if raw_labels is None else raw_labels, dtype=float)
    if len(calibration_probability) <= 0 or len(calibration_probability) != len(calibration_labels):
        return 1.0
    edges = np.linspace(0.0, 1.0, 11)
    bin_idx = min(9, max(0, int(np.searchsorted(edges, probability, side="right") - 1)))
    mask = (calibration_probability >= edges[bin_idx]) & (
        calibration_probability <= edges[bin_idx + 1]
        if bin_idx == 9
        else calibration_probability < edges[bin_idx + 1]
    )
    count = int(mask.sum())
    if count <= 0:
        return 1.0
    hit = float(calibration_labels[mask].mean())
    sampling = 1.96 * math.sqrt(max(hit * (1.0 - hit), 1e-6) / count)
    return min(1.0, max(abs(hit - probability), sampling))


def _ood(artifact: Mapping[str, Any], feature: Mapping[str, Any]) -> bool:
    bounds = artifact.get("numeric_bounds") or {}
    outliers = 0
    observed = 0
    for key, limits in dict(bounds).items():
        value = _num(feature.get(key))
        if value is None:
            continue
        observed += 1
        low, high = _num((limits or {}).get("low")), _num((limits or {}).get("high"))
        if low is not None and high is not None and (value < low or value > high):
            outliers += 1
    return outliers >= 4 or observed < 5


def _isotonic_transform(calibrator: Mapping[str, Any], value: float) -> float:
    x = np.asarray(calibrator.get("x") or [], dtype=float)
    y = np.asarray(calibrator.get("y") or [], dtype=float)
    if len(x) <= 0 or len(x) != len(y):
        raise ValueError("invalid portable isotonic calibrator")
    return float(np.interp(float(value), x, y, left=y[0], right=y[-1]))


def _portable_probability(artifact: Mapping[str, Any], feature: Mapping[str, Any]) -> float:
    portable = dict(artifact.get("portable_model") or {})
    numeric = dict(portable.get("numeric") or {})
    categorical = dict(portable.get("categorical") or {})
    numeric_features = list(numeric.get("features") or [])
    statistics = np.asarray(numeric.get("imputer_statistics") or [], dtype=float)
    indicator_features = [int(value) for value in (numeric.get("indicator_features") or [])]
    if len(numeric_features) != len(statistics):
        raise ValueError("portable numeric contract mismatch")
    values: list[float] = []
    missing: list[bool] = []
    for idx, name in enumerate(numeric_features):
        parsed = _num(feature.get(name))
        missing.append(parsed is None)
        values.append(float(statistics[idx]) if parsed is None else float(parsed))
    values.extend(1.0 if missing[idx] else 0.0 for idx in indicator_features)
    transformed = np.asarray(values, dtype=float)
    scale_mean = np.asarray(numeric.get("scale_mean") or [], dtype=float)
    scale = np.asarray(numeric.get("scale") or [], dtype=float)
    if len(transformed) != len(scale_mean) or len(transformed) != len(scale):
        raise ValueError("portable numeric scaling mismatch")
    transformed = (transformed - scale_mean) / np.where(scale == 0.0, 1.0, scale)

    encoded: list[float] = transformed.tolist()
    categorical_features = list(categorical.get("features") or [])
    categories = list(categorical.get("categories") or [])
    fill_values = list(categorical.get("imputer_statistics") or [])
    if len(categorical_features) != len(categories):
        raise ValueError("portable categorical contract mismatch")
    for idx, name in enumerate(categorical_features):
        raw = feature.get(name)
        if raw in (None, ""):
            raw = fill_values[idx] if idx < len(fill_values) else "__MISSING__"
        text = str(raw)
        encoded.extend(1.0 if text == str(category) else 0.0 for category in categories[idx])

    coefficients = np.asarray(portable.get("coefficients") or [], dtype=float)
    if len(coefficients) != len(encoded):
        raise ValueError("portable coefficient contract mismatch")
    decision = float(np.dot(coefficients, np.asarray(encoded, dtype=float))) + float(portable.get("intercept") or 0.0)
    if decision >= 0:
        return float(1.0 / (1.0 + math.exp(-decision)))
    exp_value = math.exp(decision)
    return float(exp_value / (1.0 + exp_value))


def predict_profit_path_evidence(
    *,
    market: str,
    ticker: str,
    strategy: str,
    context: Any = None,
    sources: Sequence[Any] = (),
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    if not _enabled(market_key):
        return {}
    artifact, artifact_path = _load_artifact(market_key)
    metadata = dict(artifact.get("metadata") or {})
    probability_calibrator = dict(artifact.get("probability_calibrator") or {})
    return_calibrator = dict(artifact.get("return_calibrator") or {})
    if not metadata or not artifact.get("portable_model") or not probability_calibrator or not return_calibrator:
        return {}
    feature = build_runtime_feature_row(
        market=market_key,
        ticker=ticker,
        strategy=strategy,
        context=context,
        sources=sources,
    )
    try:
        raw_probability = _portable_probability(artifact, feature)
        probability = _isotonic_transform(probability_calibrator, raw_probability)
        expected_gross = _isotonic_transform(return_calibrator, raw_probability)
    except Exception:
        return {}
    cost = float(metadata.get("cost_pct") or (0.50 if market_key == "US" else 0.21))
    return {
        "schema_version": "profit_evidence_v1",
        "model_version": str(metadata.get("model_version") or ""),
        "model_state": "SHADOW",
        "decision_ts": datetime.now(timezone.utc).isoformat(),
        "p_target_before_stop_raw": raw_probability,
        "p_target_before_stop_calibrated": probability,
        "expected_gross_pct": expected_gross,
        "expected_cost_pct_p75": cost,
        "expected_net_pct": expected_gross - cost,
        "uncertainty": _uncertainty(artifact, probability),
        "ood": _ood(artifact, feature),
        "drift_state": str(metadata.get("drift_state") or "degraded"),
        "validation_sample_n": int(metadata.get("validation_n") or 0),
        "validation_selected_n": int(metadata.get("validation_selected_n") or 0),
        "validation_auc": _num(metadata.get("validation_auc")),
        "validation_net_lcb_pct": _num(metadata.get("validation_net_lcb_pct")),
        "calibration_ece": _num(metadata.get("calibration_ece")),
        "path_name": feature.get("path_name"),
        "feature_snapshot": feature,
        "artifact_path": artifact_path,
        "promotion_eligible_backtest": bool(metadata.get("promotion_eligible_backtest")),
    }
