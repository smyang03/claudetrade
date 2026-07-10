from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


VALID_AUTHORITY_MODES = {"shadow", "micro", "probe", "standard"}
AUTHORITY_ORDER = {"shadow": 0, "micro": 1, "probe": 2, "standard": 3}


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _at_least(value: Any, threshold: float) -> bool:
    parsed = _number(value)
    return parsed is not None and parsed >= float(threshold)


def _above(value: Any, threshold: float) -> bool:
    parsed = _number(value)
    return parsed is not None and parsed > float(threshold)


@dataclass(frozen=True)
class SwingAuthorityDecision:
    configured_mode: str
    eligible_mode: str
    effective_mode: str
    allowed_to_emit_orders: bool
    size_multiplier: float
    max_new_per_day: int
    max_open_slots: int
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        output = asdict(self)
        output["blockers"] = list(self.blockers)
        output["warnings"] = list(self.warnings)
        return output


def load_swing_policy(path: str | Path) -> dict[str, Any]:
    policy_path = Path(path)
    raw = policy_path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "us_swing_accelerated_v1":
        raise ValueError("invalid US swing policy")
    payload["_policy_sha256"] = hashlib.sha256(raw).hexdigest()
    return payload


def _historical_checks(
    evidence: Mapping[str, Any], policy: Mapping[str, Any], *, mode: str
) -> list[str]:
    thresholds = policy.get("historical") if isinstance(policy.get("historical"), Mapping) else {}
    blockers: list[str] = []
    if evidence.get("sealed") is not True:
        blockers.append("historical_not_sealed")
    expected_policy_hash = str(policy.get("_policy_sha256") or "")
    if expected_policy_hash and str(evidence.get("policy_sha256") or "") != expected_policy_hash:
        blockers.append("historical_policy_hash_mismatch")
    if evidence.get("point_in_time") is not True:
        blockers.append("historical_not_point_in_time")
    if evidence.get("lookahead_checks_passed") is not True:
        blockers.append("historical_lookahead_check_failed")
    if evidence.get("critical_data_errors"):
        blockers.append("historical_critical_data_error")
    if not _at_least(evidence.get("oos_sessions"), thresholds.get("min_oos_sessions", 200)):
        blockers.append("historical_oos_sessions_insufficient")
    cohorts = evidence.get("cohorts") if isinstance(evidence.get("cohorts"), Mapping) else {}
    required_value = thresholds.get(f"{mode}_required_cohorts")
    if not isinstance(required_value, list):
        required_value = ["top3", "top5"]
    required = tuple(str(value) for value in required_value)
    for name in required:
        metrics = cohorts.get(name) if isinstance(cohorts.get(name), Mapping) else {}
        prefix = f"historical_{name}"
        if not _at_least(metrics.get("worst_mean_net_pct"), thresholds.get("min_mean_net_pct", 0.25)):
            blockers.append(f"{prefix}_mean_below_hurdle")
        if not _at_least(metrics.get("worst_profit_factor"), thresholds.get("min_profit_factor", 1.2)):
            blockers.append(f"{prefix}_profit_factor_below_hurdle")
        if not _above(metrics.get("worst_ex_top3_days_pct"), thresholds.get("min_ex_top3_days_pct", 0.0)):
            blockers.append(f"{prefix}_concentration_failed")
        lcb_key = "probe_min_block_lcb_pct" if mode in {"probe", "standard"} else "micro_min_block_lcb_pct"
        if not _above(metrics.get("worst_block_lcb_pct"), thresholds.get(lcb_key, 0.0)):
            blockers.append(f"{prefix}_block_lcb_failed")
        if not _above(metrics.get("worst_stress_mean_net_pct"), thresholds.get("min_stress_mean_net_pct", 0.0)):
            blockers.append(f"{prefix}_cost_stress_failed")
    return blockers


def _forward_checks(evidence: Mapping[str, Any], policy: Mapping[str, Any], *, mode: str) -> list[str]:
    thresholds = policy.get("forward") if isinstance(policy.get("forward"), Mapping) else {}
    blockers: list[str] = []
    prefix = mode if mode in {"micro", "probe", "standard"} else "micro"
    if evidence.get("critical_data_errors"):
        blockers.append("forward_critical_data_error")
    if not _at_least(evidence.get("sessions"), thresholds.get(f"{prefix}_min_sessions", 5)):
        blockers.append("forward_sessions_insufficient")
    if not _at_least(evidence.get("matured"), thresholds.get(f"{prefix}_min_matured", 15)):
        blockers.append("forward_matured_insufficient")
    if not _at_least(evidence.get("mean_net_pct"), thresholds.get(f"{prefix}_min_mean_net_pct", 0.0)):
        blockers.append("forward_mean_below_hurdle")
    if not _at_least(evidence.get("profit_factor"), thresholds.get(f"{prefix}_min_profit_factor", 1.0)):
        blockers.append("forward_profit_factor_below_hurdle")
    if prefix in {"probe", "standard"}:
        if not _above(evidence.get("block_lcb_pct"), thresholds.get(f"{prefix}_min_block_lcb_pct", 0.0)):
            blockers.append("forward_block_lcb_failed")
        if not _above(evidence.get("ex_top3_days_pct"), thresholds.get(f"{prefix}_min_ex_top3_days_pct", 0.0)):
            blockers.append("forward_concentration_failed")
    return blockers


def evaluate_swing_authority(
    *,
    configured_mode: str,
    historical_evidence: Mapping[str, Any] | None,
    forward_evidence: Mapping[str, Any] | None,
    policy: Mapping[str, Any],
) -> SwingAuthorityDecision:
    requested = str(configured_mode or "shadow").strip().lower()
    warnings: list[str] = []
    if requested not in VALID_AUTHORITY_MODES:
        warnings.append("invalid_configured_mode_forced_shadow")
        requested = "shadow"
    historical = historical_evidence or {}
    forward = forward_evidence or {}
    eligible = "shadow"
    blockers_by_mode: dict[str, list[str]] = {}
    for mode in ("micro", "probe", "standard"):
        blockers = _historical_checks(historical, policy, mode=mode)
        blockers.extend(_forward_checks(forward, policy, mode=mode))
        blockers_by_mode[mode] = list(dict.fromkeys(blockers))
        if not blockers:
            eligible = mode
    effective = requested if AUTHORITY_ORDER[requested] <= AUTHORITY_ORDER[eligible] else eligible
    blockers = blockers_by_mode.get(requested, []) if requested != "shadow" else []
    if requested != effective:
        warnings.append(f"configured_{requested}_demoted_to_{effective}")
    caps = policy.get("authority_caps") if isinstance(policy.get("authority_caps"), Mapping) else {}
    cap = caps.get(effective) if isinstance(caps.get(effective), Mapping) else {}
    return SwingAuthorityDecision(
        configured_mode=requested,
        eligible_mode=eligible,
        effective_mode=effective,
        allowed_to_emit_orders=effective != "shadow",
        size_multiplier=float(cap.get("size_multiplier", 0.0) or 0.0),
        max_new_per_day=int(cap.get("max_new_per_day", 0) or 0),
        max_open_slots=int(cap.get("max_open_slots", 0) or 0),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )
