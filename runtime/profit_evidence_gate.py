from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence


PROFIT_EVIDENCE_SCHEMA_VERSION = "profit_evidence_v1"
VALID_MODES = {"off", "shadow", "enforce"}
PASS_MODEL_STATES = {"PROBE", "STANDARD", "PRESS"}
PASS_DRIFT_STATES = {"HEALTHY", "STABLE"}

_SNAPSHOT_CACHE: dict[str, tuple[int, dict[str, Any]]] = {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _int(value: Any) -> int | None:
    parsed = _float(value)
    return int(parsed) if parsed is not None else None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = _text(value).lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _parse_ts(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _path_key(strategy: str) -> str:
    normalized = _upper(strategy).replace("-", "_")
    if normalized.startswith("PATH_B") or normalized in {"CLAUDE_PRICE", "PATHB"}:
        return "PATH_B"
    return "PATH_A"


def _config_candidates(base: str, market: str, path: str) -> list[str]:
    market_key = _upper(market)
    path_key = _upper(path)
    return [
        f"{base}_{market_key}_{path_key}",
        f"{base}_{path_key}",
        f"{base}_{market_key}",
        base,
    ]


def _config_text(base: str, market: str, path: str, default: str) -> str:
    for key in _config_candidates(base, market, path):
        value = os.getenv(key)
        if value not in (None, ""):
            return _text(value)
    return default


def _config_float(base: str, market: str, path: str, default: float) -> float:
    value = _float(_config_text(base, market, path, str(default)))
    return default if value is None else value


def resolve_profit_evidence_mode(market: str, strategy: str) -> tuple[str, str]:
    path = _path_key(strategy)
    mode = _config_text("PROFIT_EVIDENCE_GATE_MODE", market, path, "off").lower()
    return (mode if mode in VALID_MODES else "off"), path


def _direct_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    nested = payload.get("profit_evidence")
    if isinstance(nested, Mapping):
        return dict(nested)
    marker_keys = {
        "p_target_before_stop_calibrated",
        "expected_net_pct",
        "expected_gross_pct",
        "expected_cost_pct_p75",
    }
    if marker_keys.intersection(payload.keys()):
        return dict(payload)
    return {}


def _ticker_key(market: str, ticker: Any) -> str:
    raw = _text(ticker)
    return raw.upper() if _upper(market) == "US" else raw


def select_profit_evidence(source: Any, *, market: str, ticker: str) -> dict[str, Any]:
    if not isinstance(source, Mapping):
        return {}
    direct = _direct_evidence(source)
    if direct:
        return direct

    key = _ticker_key(market, ticker)
    for map_key in (
        "profit_evidence_by_ticker",
        "_profit_evidence_by_ticker",
        "predictions_by_ticker",
        "evidence_by_ticker",
    ):
        values = source.get(map_key)
        if not isinstance(values, Mapping):
            continue
        candidate = values.get(key) or values.get(ticker) or values.get(_upper(ticker))
        if isinstance(candidate, Mapping):
            evidence = _direct_evidence(candidate) or dict(candidate)
            for inherited in ("schema_version", "model_version", "generated_at", "decision_ts"):
                if evidence.get(inherited) in (None, "") and source.get(inherited) not in (None, ""):
                    evidence[inherited] = source.get(inherited)
            return evidence

    for list_key in ("candidate_actions", "prompt_pool", "candidates", "rows"):
        rows = source.get(list_key)
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            row_ticker = _ticker_key(market, row.get("ticker") or row.get("t"))
            if row_ticker != key:
                continue
            evidence = _direct_evidence(row)
            if evidence:
                return evidence
    return {}


def _snapshot_path(market: str) -> Path:
    template = os.getenv("PROFIT_EVIDENCE_SNAPSHOT_PATH", "state/profit_evidence_{market}.json")
    try:
        rendered = str(template).format(market=_upper(market))
    except (KeyError, ValueError):
        rendered = str(template)
    return Path(rendered)


def load_profit_evidence_snapshot(market: str) -> dict[str, Any]:
    path = _snapshot_path(market)
    try:
        stat = path.stat()
    except OSError:
        return {}
    cache_key = str(path.resolve())
    cached = _SNAPSHOT_CACHE.get(cache_key)
    if cached and cached[0] == stat.st_mtime_ns:
        return dict(cached[1])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    normalized = dict(payload) if isinstance(payload, Mapping) else {}
    _SNAPSHOT_CACHE[cache_key] = (stat.st_mtime_ns, normalized)
    return dict(normalized)


def resolve_profit_evidence(
    *,
    market: str,
    ticker: str,
    explicit: Any = None,
    sources: Sequence[Any] = (),
) -> tuple[dict[str, Any], str]:
    for source_name, source in [("explicit", explicit), *[(f"source_{idx}", value) for idx, value in enumerate(sources)]]:
        evidence = select_profit_evidence(source, market=market, ticker=ticker)
        if evidence:
            return evidence, source_name
    snapshot = load_profit_evidence_snapshot(market)
    evidence = select_profit_evidence(snapshot, market=market, ticker=ticker)
    return (evidence, "snapshot") if evidence else ({}, "missing")


@dataclass(frozen=True)
class ProfitEvidenceDecision:
    allowed: bool
    passed: bool
    would_block: bool
    mode: str
    path: str
    reason_code: str
    reasons: tuple[str, ...]
    market: str
    ticker: str
    strategy: str
    evidence_source: str
    model_version: str
    model_state: str
    p_target_before_stop_calibrated: float | None
    expected_gross_pct: float | None
    expected_cost_pct_p75: float | None
    expected_net_pct: float | None
    uncertainty: float | None
    age_min: float | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


def evaluate_profit_evidence(
    *,
    market: str,
    ticker: str,
    strategy: str,
    evidence: Mapping[str, Any] | None,
    evidence_source: str = "explicit",
    now: datetime | None = None,
) -> ProfitEvidenceDecision:
    market_key = _upper(market)
    ticker_key = _ticker_key(market_key, ticker)
    mode, path = resolve_profit_evidence_mode(market_key, strategy)
    payload = dict(evidence or {})
    if mode == "off":
        return ProfitEvidenceDecision(
            allowed=True,
            passed=True,
            would_block=False,
            mode=mode,
            path=path,
            reason_code="",
            reasons=(),
            market=market_key,
            ticker=ticker_key,
            strategy=_text(strategy),
            evidence_source=evidence_source,
            model_version=_text(payload.get("model_version")),
            model_state=_upper(payload.get("model_state")),
            p_target_before_stop_calibrated=_float(payload.get("p_target_before_stop_calibrated")),
            expected_gross_pct=_float(payload.get("expected_gross_pct")),
            expected_cost_pct_p75=_float(payload.get("expected_cost_pct_p75")),
            expected_net_pct=_float(payload.get("expected_net_pct")),
            uncertainty=_float(payload.get("uncertainty")),
            age_min=None,
        )

    reasons: list[str] = []
    if not payload:
        reasons.append("evidence_missing")

    schema_version = _text(payload.get("schema_version"))
    if schema_version != PROFIT_EVIDENCE_SCHEMA_VERSION:
        reasons.append("schema_invalid")
    model_version = _text(payload.get("model_version"))
    if not model_version:
        reasons.append("model_version_missing")
    model_state = _upper(payload.get("model_state"))
    if model_state not in PASS_MODEL_STATES:
        reasons.append("model_not_promoted")

    reference_now = now or datetime.now(timezone.utc)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=timezone.utc)
    reference_now = reference_now.astimezone(timezone.utc)
    evidence_ts = _parse_ts(payload.get("decision_ts") or payload.get("generated_at"))
    age_min: float | None = None
    if evidence_ts is None:
        reasons.append("timestamp_missing_or_invalid")
    else:
        age_min = (reference_now - evidence_ts).total_seconds() / 60.0
        max_age = _config_float("PROFIT_EVIDENCE_MAX_AGE_MIN", market_key, path, 180.0)
        if age_min < -2.0:
            reasons.append("timestamp_in_future")
        elif age_min > max_age:
            reasons.append("evidence_stale")

    calibrated_p = _float(payload.get("p_target_before_stop_calibrated"))
    if calibrated_p is None or not 0.0 <= calibrated_p <= 1.0:
        reasons.append("calibrated_probability_missing_or_invalid")
    elif calibrated_p < _config_float("PROFIT_EVIDENCE_MIN_PROB", market_key, path, 0.55):
        reasons.append("probability_below_hurdle")

    expected_gross = _float(payload.get("expected_gross_pct"))
    expected_cost = _float(payload.get("expected_cost_pct_p75"))
    expected_net = _float(payload.get("expected_net_pct"))
    if expected_gross is None:
        reasons.append("expected_gross_missing")
    if expected_cost is None:
        reasons.append("expected_cost_missing")
    else:
        min_cost = _config_float(
            "PROFIT_EVIDENCE_MIN_COST_PCT",
            market_key,
            path,
            0.50 if market_key == "US" else 0.21,
        )
        if expected_cost < min_cost:
            reasons.append("cost_understated")
    if expected_net is None:
        reasons.append("expected_net_missing")
    else:
        min_net = _config_float("PROFIT_EVIDENCE_MIN_EXPECTED_NET_PCT", market_key, path, 0.25)
        if expected_net < min_net:
            reasons.append("expected_net_below_hurdle")
    if expected_gross is not None and expected_cost is not None and expected_net is not None:
        tolerance = _config_float("PROFIT_EVIDENCE_NET_MATH_TOLERANCE_PCT", market_key, path, 0.05)
        if expected_net > expected_gross - expected_cost + tolerance:
            reasons.append("net_math_inconsistent")

    uncertainty = _float(payload.get("uncertainty"))
    if uncertainty is None or not 0.0 <= uncertainty <= 1.0:
        reasons.append("uncertainty_missing_or_invalid")
    elif uncertainty > _config_float("PROFIT_EVIDENCE_MAX_UNCERTAINTY", market_key, path, 0.25):
        reasons.append("uncertainty_too_high")

    if _bool(payload.get("ood")) is not False:
        reasons.append("ood_or_ood_missing")
    if _upper(payload.get("drift_state")) not in PASS_DRIFT_STATES:
        reasons.append("drift_unhealthy_or_missing")

    sample_n = _int(payload.get("validation_sample_n"))
    min_sample_n = int(_config_float("PROFIT_EVIDENCE_MIN_VALIDATION_N", market_key, path, 60.0))
    if sample_n is None or sample_n < min_sample_n:
        reasons.append("validation_sample_insufficient")
    net_lcb = _float(payload.get("validation_net_lcb_pct"))
    min_lcb = _config_float("PROFIT_EVIDENCE_MIN_VALIDATION_NET_LCB_PCT", market_key, path, 0.0)
    if net_lcb is None or net_lcb <= min_lcb:
        reasons.append("validation_net_lcb_not_positive")
    calibration_ece = _float(payload.get("calibration_ece"))
    max_ece = _config_float("PROFIT_EVIDENCE_MAX_CALIBRATION_ECE", market_key, path, 0.10)
    if calibration_ece is None or calibration_ece > max_ece:
        reasons.append("calibration_failed_or_missing")

    unique_reasons = tuple(dict.fromkeys(reasons))
    passed = not unique_reasons
    allowed = passed or mode != "enforce"
    return ProfitEvidenceDecision(
        allowed=allowed,
        passed=passed,
        would_block=not passed,
        mode=mode,
        path=path,
        reason_code="" if passed else "PROFIT_EVIDENCE_ABSTAIN",
        reasons=unique_reasons,
        market=market_key,
        ticker=ticker_key,
        strategy=_text(strategy),
        evidence_source=evidence_source,
        model_version=model_version,
        model_state=model_state,
        p_target_before_stop_calibrated=calibrated_p,
        expected_gross_pct=expected_gross,
        expected_cost_pct_p75=expected_cost,
        expected_net_pct=expected_net,
        uncertainty=uncertainty,
        age_min=round(age_min, 4) if age_min is not None else None,
    )
