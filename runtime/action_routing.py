from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


PLANB_CANCEL_CONFIDENCE_MIN = 0.75


ENTRY_ACTIONS = {"PROBE_READY", "BUY_READY", "ADD_READY"}
KR_CONFIRMATION_ACTIONS = {"PROBE_READY", "BUY_READY"}


@dataclass
class RouteDecision:
    ticker: str
    market: str
    final_action: str
    route: str | None = None
    reason: str = ""
    original_action: str = ""
    demoted_to: str = ""
    runtime_gate_reason: str = ""
    runtime_gate: dict[str, Any] = field(default_factory=dict)
    cancel_pathb: bool = False
    suspend_pathb: bool = False
    warnings: list[str] = field(default_factory=list)
    confirmation_state: str = ""
    confirmation_reason: str = ""
    confirmation_shadow: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "market": self.market,
            "final_action": self.final_action,
            "route": self.route,
            "reason": self.reason,
            "original_action": self.original_action,
            "demoted_to": self.demoted_to,
            "runtime_gate_reason": self.runtime_gate_reason,
            "runtime_gate": dict(self.runtime_gate),
            "cancel_pathb": self.cancel_pathb,
            "suspend_pathb": self.suspend_pathb,
            "warnings": list(self.warnings),
            "confirmation_state": self.confirmation_state,
            "confirmation_reason": self.confirmation_reason,
            "confirmation_shadow": self.confirmation_shadow,
        }


def has_pullback_target(price_targets: dict[str, Any] | None) -> bool:
    targets = price_targets or {}
    # Live PathB registration delegates to parse_plan_from_claude(), which needs a
    # complete executable plan. Loose hints stay in WATCH instead of creating a
    # wait run that fails later.
    required = ("buy_zone_low", "buy_zone_high", "sell_target", "stop_loss", "hold_days", "confidence")
    return all(targets.get(key) not in (None, "") for key in required)


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _num_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _resolve_entry_price_cap(action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    price_targets = dict((action or {}).get("price_targets") or {})
    candidates = [
        ("action.max_entry_price", (action or {}).get("max_entry_price")),
        ("context.max_entry_price", (context or {}).get("max_entry_price")),
        ("price_targets.max_entry_price", price_targets.get("max_entry_price")),
        ("price_targets.cancel_if_open_above", price_targets.get("cancel_if_open_above")),
        ("price_targets.buy_zone_high", price_targets.get("buy_zone_high")),
        ("context.cancel_if_open_above", (context or {}).get("cancel_if_open_above")),
        ("context.buy_zone_high", (context or {}).get("buy_zone_high")),
    ]
    raw_candidates: dict[str, Any] = {source: value for source, value in candidates}
    for source, value in candidates:
        parsed = _num_or_none(value)
        if parsed is not None and parsed > 0:
            return {
                "entry_price_cap": float(parsed),
                "entry_price_cap_source": source,
                "entry_price_cap_missing": False,
                "entry_price_cap_candidates": raw_candidates,
            }
    return {
        "entry_price_cap": None,
        "entry_price_cap_source": "",
        "entry_price_cap_missing": True,
        "entry_price_cap_candidates": raw_candidates,
    }


def _entry_window_bucket(elapsed_min: Any) -> str:
    elapsed = _num_or_none(elapsed_min)
    if elapsed is None:
        return "UNKNOWN"
    if elapsed < 0:
        return "OUTSIDE_SESSION"
    if elapsed < 30:
        return "OPEN_0_30"
    if elapsed < 60:
        return "OPEN_30_60"
    if elapsed < 90:
        return "OPEN_60_90"
    if elapsed <= 270:
        return "OPEN_90_270"
    return "LATE_AFTER_270"


def _normalized_tokens(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        text = str(value or "").replace(";", ",")
        raw_items = text.split(",") if "," in text else text.split()
    tokens: list[str] = []
    for item in raw_items:
        token = str(item or "").strip().lower().replace("-", "_")
        if token:
            tokens.append(token)
    return list(dict.fromkeys(tokens))


def _kr_risk_combo_confirmation_ok(context: dict[str, Any]) -> bool:
    if _boolish(context.get("kr_confirmation_confirmed")):
        return True
    if _boolish(context.get("opening_range_break")) or _boolish(context.get("vwap_reclaim")):
        return True
    current_price = _num_or_none(context.get("current_price"))
    vwap = _num_or_none(context.get("vwap") or context.get("vwap_proxy"))
    if current_price is not None and vwap is not None and current_price >= vwap:
        return True
    volume_ratio = _num_or_none(context.get("volume_ratio_open") or context.get("volume_acceleration"))
    if volume_ratio is not None and volume_ratio > 1.0:
        return True
    return False


def _context_or_env_float(context: dict[str, Any], key: str, default: float, *, market: str = "") -> float:
    for raw_key in (
        key,
        f"{key}_{str(market or '').upper()}",
    ):
        value = context.get(raw_key)
        if value not in (None, ""):
            parsed = _num_or_none(value)
            return float(default if parsed is None else parsed)
    for env_key in (
        f"{key}_{str(market or '').upper()}",
        key,
    ):
        raw = os.getenv(env_key)
        if raw not in (None, ""):
            parsed = _num_or_none(raw)
            return float(default if parsed is None else parsed)
    return float(default)


def validate_soft_gate_override(action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Validate Claude soft-gate overrides against supplied evidence.

    This intentionally does not create hard blocks by itself; callers choose
    whether to demote. The contract is evidence-backed overrides only.
    """
    overrides = action.get("soft_gate_overrides")
    if not isinstance(overrides, list):
        overrides = []
    soft_gates = context.get("soft_gates")
    if not isinstance(soft_gates, list):
        soft_gates = []
    gates = [str(g).strip().lower() for g in [*soft_gates, *overrides] if str(g or "").strip()]
    gates = list(dict.fromkeys(gates))
    if not gates:
        return {"required": False, "validated": True, "reason": "no_soft_gate_override", "gates": []}

    ret_3m = _num_or_none(context.get("ret_3m_pct"))
    ret_5m = _num_or_none(context.get("ret_5m_pct"))
    market_key = str(context.get("market") or (action or {}).get("market") or "").upper()
    elapsed_min = _num_or_none(context.get("market_open_elapsed_min"))
    ret3_only_grace_min = _context_or_env_float(
        context,
        "SOFT_GATE_ALLOW_RET3_ONLY_FIRST_MIN",
        5.0,
        market=market_key,
    )
    current_price = _num_or_none(context.get("current_price"))
    vwap = _num_or_none(context.get("vwap") or context.get("vwap_proxy"))
    price_cap_info = _resolve_entry_price_cap(action or {}, context or {})
    entry_price_cap = _num_or_none(price_cap_info.get("entry_price_cap"))
    entry_price_cap_missing = bool(price_cap_info.get("entry_price_cap_missing"))
    volume = _num_or_none(
        context.get("volume_ratio_open")
        or context.get("volume_acceleration")
        or context.get("volume_accel")
    )
    min_volume = _num_or_none(context.get("soft_gate_min_volume_ratio_open") or context.get("soft_gate_min_volume_acceleration"))
    if min_volume is None:
        min_volume = 0.0
    opening_break = _boolish(context.get("opening_range_break")) or (
        current_price is not None
        and _num_or_none(context.get("opening_range_high")) is not None
        and current_price >= float(_num_or_none(context.get("opening_range_high")) or 0.0)
    )
    vwap_ok = _boolish(context.get("vwap_reclaim")) or (
        current_price is not None and vwap is not None and current_price >= vwap
    )
    legacy_max_entry_price = _num_or_none(action.get("max_entry_price") or context.get("max_entry_price"))
    price_check_cap = entry_price_cap if market_key == "KR" else legacy_max_entry_price
    price_ok = not (current_price is not None and price_check_cap is not None and current_price > price_check_cap)
    route_requested = str(
        context.get("route_requested_action")
        or context.get("requested_action")
        or (action or {}).get("action")
        or ""
    ).strip().upper()
    entry_price_cap_ok = not (
        market_key == "KR"
        and route_requested == "BUY_READY"
        and entry_price_cap_missing
    )
    ret3_only_grace_used = bool(
        ret_3m is not None
        and ret_5m is None
        and ret_3m > 0
        and elapsed_min is not None
        and ret3_only_grace_min > 0
        and elapsed_min <= ret3_only_grace_min
    )
    momentum_ok = bool(
        (ret_3m is not None and ret_5m is not None and ret_3m > 0 and ret_5m > 0)
        or ret3_only_grace_used
    )
    volume_ok = volume is not None and volume >= float(min_volume or 0.0)
    confirmation_ok = bool(opening_break or vwap_ok or volume_ok)
    validated = bool(momentum_ok and confirmation_ok and price_ok and entry_price_cap_ok)
    failed_checks: list[str] = []
    if not momentum_ok:
        failed_checks.append("fresh_momentum_missing")
    if not confirmation_ok:
        failed_checks.append("or_vwap_volume_confirmation_missing")
    if not price_ok:
        failed_checks.append("max_entry_price_exceeded")
    if not entry_price_cap_ok:
        failed_checks.append("entry_price_cap_missing")
    return {
        "required": True,
        "validated": validated,
        "reason": "soft_gate_override_validated" if validated else "soft_gate_override_failed",
        "gates": gates,
        "checks": {
            "momentum_ok": momentum_ok,
            "opening_range_break": bool(opening_break),
            "vwap_ok": bool(vwap_ok),
            "volume_ok": bool(volume_ok),
            "price_ok": bool(price_ok),
            "entry_price_cap_ok": bool(entry_price_cap_ok),
            "entry_price_cap_missing": bool(entry_price_cap_missing),
            "ret3_only_grace_used": ret3_only_grace_used,
        },
        "entry_price_cap": entry_price_cap,
        "entry_price_cap_source": str(price_cap_info.get("entry_price_cap_source") or ""),
        "failed_checks": failed_checks,
    }


def route_candidate_action(
    action: dict[str, Any],
    *,
    market: str,
    gate_final_action: str | None = None,
    gate_blocker: str | None = None,
    has_local_position: bool = False,
    has_broker_position: bool = False,
    active_order_route: str | None = None,
    pathb_waiting: bool = False,
    pathb_active_order: bool = False,
    add_enabled: bool = False,
    overextended: bool = False,
    data_quality: str = "missing",
    planb_cancel_confidence_min: float = PLANB_CANCEL_CONFIDENCE_MIN,
    execution_context: dict[str, Any] | None = None,
) -> RouteDecision:
    ticker = str((action or {}).get("ticker") or "")
    market_text = str(market or (action or {}).get("market") or "").upper()
    requested = str((action or {}).get("action") or "WATCH")
    original_requested = requested
    confidence = float((action or {}).get("confidence") or 0.0)
    price_targets = dict((action or {}).get("price_targets") or {})
    context = dict(execution_context or {})
    context.setdefault("market", market_text)
    if ticker:
        context.setdefault("ticker", ticker)

    def _positive_float(value: Any) -> float:
        try:
            parsed = float(value)
        except Exception:
            return 0.0
        return parsed if parsed > 0 else 0.0

    current_price = _positive_float(
        context.get("current_price")
        or price_targets.get("current_price")
        or price_targets.get("reference_price")
    )
    buy_zone_high = _positive_float(context.get("buy_zone_high") or price_targets.get("buy_zone_high"))
    pathb_waiting_buy_zone_high = _positive_float(context.get("pathb_waiting_buy_zone_high"))
    pathb_waiting_buy_zone_low = _positive_float(context.get("pathb_waiting_buy_zone_low"))
    cancel_if_open_above = _positive_float(
        context.get("cancel_if_open_above") or price_targets.get("cancel_if_open_above")
    )
    price_cap_info = _resolve_entry_price_cap(action or {}, context or {})
    entry_price_cap = _num_or_none(price_cap_info.get("entry_price_cap"))
    entry_price_cap_missing = bool(price_cap_info.get("entry_price_cap_missing"))
    if context.get("overextended") is not None:
        overextended = bool(context.get("overextended"))
    elif str(context.get("momentum_state") or "").strip().lower() == "overextended":
        overextended = True
    else:
        try:
            ret_5m = float(context.get("ret_5m_pct"))
            threshold = float(context.get("threshold_used"))
            overextended = ret_5m >= threshold
        except Exception:
            pass
    raw_data_quality = context.get("data_quality")
    data_quality_missing = bool(context.get("data_quality_missing"))
    if raw_data_quality in (None, ""):
        raw_data_quality = data_quality
    data_quality = str(raw_data_quality or "missing").strip().lower()
    if data_quality in {"", "missing", "unknown", "none", "null"}:
        data_quality_missing = True
        data_quality = "missing"

    gate_context = {
        key: context.get(key)
        for key in (
            "market",
            "ticker",
            "momentum_state",
            "ret_5m_pct",
            "threshold_used",
            "pullback_from_high_pct",
            "data_quality",
        )
        if context.get(key) is not None
    }
    if current_price > 0:
        gate_context["current_price"] = current_price
    gate_context["data_quality_missing"] = bool(data_quality_missing)
    if buy_zone_high > 0:
        gate_context["buy_zone_high"] = buy_zone_high
    if pathb_waiting_buy_zone_high > 0:
        gate_context["pathb_waiting_buy_zone_high"] = pathb_waiting_buy_zone_high
    if pathb_waiting_buy_zone_low > 0:
        gate_context["pathb_waiting_buy_zone_low"] = pathb_waiting_buy_zone_low
    if cancel_if_open_above > 0:
        gate_context["cancel_if_open_above"] = cancel_if_open_above
    gate_context["entry_price_cap"] = entry_price_cap
    gate_context["entry_price_cap_source"] = str(price_cap_info.get("entry_price_cap_source") or "")
    gate_context["entry_price_cap_missing"] = bool(entry_price_cap_missing)
    gate_context["entry_price_cap_candidates"] = dict(price_cap_info.get("entry_price_cap_candidates") or {})
    gate_context["overextended"] = bool(overextended)
    pathb_hysteresis_enabled = True
    if context.get("pathb_suspend_hysteresis_enabled") not in (None, ""):
        pathb_hysteresis_enabled = _boolish(context.get("pathb_suspend_hysteresis_enabled"))
    else:
        raw_hysteresis = os.getenv(
            f"{market_text}_PATHB_SUSPEND_HYSTERESIS_ENABLED",
            os.getenv("PATHB_SUSPEND_HYSTERESIS_ENABLED", "true"),
        )
        pathb_hysteresis_enabled = _boolish(raw_hysteresis)
    pathb_negative_watch_count = int(_num_or_none(context.get("pathb_wait_negative_watch_count")) or 0)
    if pathb_negative_watch_count <= 0:
        pathb_negative_watch_count = 1 if pathb_waiting else 0
    pathb_suspend_threshold = int(
        _num_or_none(context.get("pathb_suspend_negative_watch_threshold"))
        or _num_or_none(os.getenv(f"{market_text}_PATHB_SUSPEND_NEGATIVE_WATCH_THRESHOLD"))
        or _num_or_none(os.getenv("PATHB_SUSPEND_NEGATIVE_WATCH_THRESHOLD"))
        or 3
    )
    pathb_suspend_threshold = max(1, pathb_suspend_threshold)
    pathb_explicit_invalidation = _boolish(context.get("pathb_explicit_invalidation"))
    gate_context["pathb_waiting"] = bool(pathb_waiting)
    gate_context["pathb_wait_negative_watch_count"] = pathb_negative_watch_count
    gate_context["pathb_suspend_negative_watch_threshold"] = pathb_suspend_threshold
    gate_context["pathb_suspend_hysteresis_enabled"] = bool(pathb_hysteresis_enabled)
    gate_context["pathb_explicit_invalidation"] = bool(pathb_explicit_invalidation)
    for key in (
        "ret_3m_pct",
        "ret_10m_pct",
        "ret_30m_pct",
        "opening_range_high",
        "opening_range_low",
        "vwap",
        "vwap_proxy",
        "volume_acceleration",
        "volume_ratio_open",
        "opening_range_break",
        "vwap_reclaim",
        "max_entry_price",
        "soft_gates",
        "soft_gate_override_validation_enabled",
        "soft_gate_min_volume_ratio_open",
        "SOFT_GATE_ALLOW_RET3_ONLY_FIRST_MIN",
        "SOFT_GATE_ALLOW_RET3_ONLY_FIRST_MIN_KR",
        "SOFT_GATE_ALLOW_RET3_ONLY_FIRST_MIN_US",
        "market_open_elapsed_min",
        "entry_window_bucket",
        "freshness_verdict",
        "trainer_tier",
        "cohort_reliability",
        "evidence_pack_ceiling_enabled",
        "evidence_data_state",
        "evidence_missing_fields",
        "evidence_action_ceiling",
        "evidence_fail_closed",
        "evidence_fail_closed_reason",
        "evidence_provider",
        "evidence_requested",
        "evidence_complete",
        "evidence_coverage_ratio",
        "evidence_partial_grace_active",
        "evidence_pack",
        "spread_bps",
        "kr_confirmation_gate_active",
        "kr_confirmation_gate_enabled",
        "kr_confirmation_gate_shadow",
        "kr_confirmation_gate_mode",
        "kr_confirmation_confirmed",
        "kr_confirmation_state",
        "kr_confirmation_reason",
        "kr_confirmation_checks",
        "kr_confirmation_score",
        "kr_confirmation_score_items",
        "kr_confirmation_threshold",
        "kr_confirmation_fast_window_ok",
        "kr_confirmation_fast_window_elapsed_min",
        "kr_confirmation_fast_window_min",
        "kr_confirmation_fast_window_elapsed_missing",
        "vi_state",
        "vi_active",
        "vi_data_quality",
        "orderbook_snapshot",
        "orderbook_data_quality",
        "orderbook_imbalance",
        "orderbook_support",
        "microstructure_data_quality",
    ):
        if context.get(key) is not None:
            gate_context[key] = context.get(key)
    if "entry_window_bucket" not in gate_context:
        gate_context["entry_window_bucket"] = _entry_window_bucket(context.get("market_open_elapsed_min"))

    action_risk_tags = _normalized_tokens((action or {}).get("risk_tags"))
    context_risk_tags = _normalized_tokens(context.get("risk_tags"))
    risk_tags = action_risk_tags + [token for token in context_risk_tags if token not in action_risk_tags]
    from_high_bucket = str((action or {}).get("from_high_bucket") or context.get("from_high_bucket") or "").strip().lower()
    if from_high_bucket:
        gate_context["from_high_bucket"] = from_high_bucket
    if risk_tags:
        gate_context["risk_tags"] = list(risk_tags)
    has_or_missing = bool({"or_missing", "opening_range_missing", "or_not_ready"} & set(risk_tags))
    has_high_entry = bool({"at_high", "near_high", "high_entry", "near_session_high"} & set(risk_tags)) or from_high_bucket in {
        "at_high",
        "near_high",
        "high",
    }
    kr_risk_combo_active = bool(market_text == "KR" and has_or_missing and has_high_entry)

    default_warnings: list[str] = []
    trainer_tier = str(context.get("trainer_tier") or "").strip().upper()
    freshness_verdict = str(
        context.get("freshness_verdict")
        or (action or {}).get("freshness_verdict")
        or ""
    ).strip().upper()
    if freshness_verdict and not gate_context.get("freshness_verdict"):
        gate_context["freshness_verdict"] = freshness_verdict
    if market_text == "KR" and trainer_tier == "WATCH":
        if entry_price_cap_missing:
            default_warnings.append("trainer_watch_price_cap_missing")
        if freshness_verdict in {"STALE", "LATE_CHASE"}:
            default_warnings.append("trainer_watch_late_chase")
        if overextended:
            default_warnings.append("trainer_watch_overextended")
    evidence_demoted_to = ""
    evidence_runtime_reason = ""

    def _decision(
        final_action: str,
        *,
        route: str | None = None,
        reason: str = "",
        cancel_pathb: bool = False,
        suspend_pathb: bool = False,
        warnings: list[str] | None = None,
        demoted_to: str = "",
        runtime_gate_reason: str = "",
    ) -> RouteDecision:
        gate_payload = dict(gate_context)
        if runtime_gate_reason:
            gate_payload["reason"] = runtime_gate_reason
        if demoted_to:
            gate_payload["demoted_to"] = demoted_to
        return RouteDecision(
            ticker,
            market_text,
            final_action,
            route=route,
            reason=reason,
            original_action=original_requested,
            demoted_to=demoted_to or (
                evidence_demoted_to if evidence_demoted_to and final_action == evidence_demoted_to else ""
            ),
            runtime_gate_reason=runtime_gate_reason or (
                evidence_runtime_reason if evidence_demoted_to and final_action == evidence_demoted_to else ""
            ),
            runtime_gate=gate_payload,
            cancel_pathb=cancel_pathb,
            suspend_pathb=suspend_pathb,
            warnings=list(dict.fromkeys([*default_warnings, *(warnings or [])])),
            confirmation_state=str(gate_payload.get("kr_confirmation_state") or ""),
            confirmation_reason=str(gate_payload.get("kr_confirmation_reason") or ""),
            confirmation_shadow=bool(gate_payload.get("kr_confirmation_gate_shadow")),
        )

    def _negative_watch_context() -> bool:
        momentum = str(context.get("momentum_state") or "").strip().lower()
        quality = str(data_quality or "").strip().lower()
        reason_text = " ".join(
            str(value or "")
            for value in (
                (action or {}).get("reason"),
                (action or {}).get("invalidation_condition"),
                context.get("reason"),
            )
        ).lower()
        if momentum in {"fade", "fading", "weak", "weakening", "direction_unconfirmed"}:
            return True
        if quality in {"bad", "bad_data", "stale", "invalid"}:
            return True
        negative_keywords = (
            "fade",
            "fading",
            "direction_unconfirmed",
            "volume_collapse",
            "weakening",
            "거래량 급감",
            "방향 미확인",
            "반등 확인",
            "반등 실패",
        )
        return any(keyword in reason_text for keyword in negative_keywords)

    def _kr_healthy_pullback_shadow_payload() -> dict[str, Any]:
        if market_text != "KR" or str(original_requested or "").upper() != "PULLBACK_WAIT":
            return {}

        def _context_bool(key: str) -> bool:
            return _boolish(context.get(key))

        def _feature_bool(key: str) -> bool:
            value = context.get(key)
            if isinstance(value, bool):
                return value
            return _boolish(value)

        evidence_pack = context.get("evidence_pack") if isinstance(context.get("evidence_pack"), dict) else {}
        fade_checks = (
            evidence_pack.get("fade_recovered_checks")
            if isinstance(evidence_pack.get("fade_recovered_checks"), dict)
            else {}
        )
        risk_view = (
            evidence_pack.get("risk_control_view")
            if isinstance(evidence_pack.get("risk_control_view"), dict)
            else {}
        )
        hard_blocks = context.get("hard_blocks")
        if not isinstance(hard_blocks, (list, tuple, set)):
            hard_blocks = risk_view.get("hard_blocks") if isinstance(risk_view.get("hard_blocks"), list) else []
        target_complete = has_pullback_target(price_targets)
        cap = buy_zone_high if buy_zone_high > 0 else _positive_float(price_targets.get("buy_zone_high"))
        evidence_state = str(context.get("evidence_data_state") or "").strip().lower()
        quality = str(data_quality or context.get("data_quality") or "").strip().lower()
        fail_closed = _context_bool("evidence_fail_closed") or _context_bool("fail_closed")
        vi_active = _context_bool("vi_active") or str(context.get("vi_state") or "").strip().upper() in {
            "VI_ACTIVE",
            "HALT",
            "SUSPENDED",
        }
        halted = any(
            _context_bool(key)
            for key in ("halted", "trading_halt", "suspended", "trade_suspended", "market_halt")
        )
        repeated_failed = int(_num_or_none(context.get("repeated_failed_ready_count")) or 0)
        pullback = _num_or_none(
            context.get("pullback_from_high_pct")
            if context.get("pullback_from_high_pct") not in (None, "")
            else context.get("pullback_from_high")
        )
        spread_ok = True
        if context.get("spread_ok") not in (None, ""):
            spread_ok = _context_bool("spread_ok")
        elif fade_checks.get("spread_ok") is not None:
            spread_ok = bool(fade_checks.get("spread_ok"))
        momentum = str(context.get("momentum_state") or "").strip().lower()
        vwap_reclaim = _feature_bool("vwap_reclaim")
        opening_range_break = _feature_bool("opening_range_break")
        recovery_signals = {
            "vwap_reclaim": bool(vwap_reclaim),
            "opening_range_break": bool(opening_range_break),
            "momentum_sustained": momentum == "sustained",
        }
        # These three recovery signals are intentionally OR: at least one is
        # enough for shadow classification, but overextended never auto-accepts.
        recovery_ok = any(recovery_signals.values())
        overextended_momentum = bool(overextended) or momentum == "overextended"
        deep_fade = pullback is not None and pullback <= -8.0
        reasons: list[str] = []
        if not target_complete:
            reasons.append("missing_complete_price_targets")
        if cap <= 0:
            reasons.append("missing_buy_zone_cap")
        if current_price <= 0:
            reasons.append("missing_current_price")
        elif cap > 0 and current_price > cap:
            reasons.append("price_above_buy_zone_cap")
        if evidence_state != "confirmed":
            reasons.append("evidence_not_confirmed")
        if quality != "minute_complete":
            reasons.append("data_quality_not_minute_complete")
        if fail_closed:
            reasons.append("evidence_fail_closed")
        if hard_blocks:
            reasons.append("hard_blocks_present")
        if vi_active:
            reasons.append("vi_active")
        if halted:
            reasons.append("halt_or_suspended")
        if not spread_ok:
            reasons.append("spread_not_confirmed")
        if repeated_failed > 0:
            reasons.append("repeated_failed_ready")
        if deep_fade:
            reasons.append("deep_fade")
        if not recovery_ok:
            reasons.append("missing_recovery_signal")
        if overextended_momentum:
            reasons.append("overextended_needs_review")

        base_ok = not any(reason != "overextended_needs_review" for reason in reasons)
        if base_ok and overextended_momentum:
            decision = "needs_review_overextended"
        elif base_ok:
            decision = "accepted"
        else:
            decision = "rejected"
        return {
            "event": "kr_healthy_pullback_shadow",
            "shadow_only": True,
            "no_main_execution": True,
            "pathb_wait_registration": False,
            "v2_path_run_created": False,
            "order_created": False,
            "would_have_pathb_wait": decision == "accepted",
            "shadow_decision": decision,
            "shadow_reason": "healthy_pullback_candidate" if decision == "accepted" else ",".join(reasons),
            "checks": {
                "price_targets_complete": bool(target_complete),
                "current_price_inside_cap": bool(current_price > 0 and cap > 0 and current_price <= cap),
                "evidence_confirmed": evidence_state == "confirmed",
                "data_quality_minute_complete": quality == "minute_complete",
                "fail_closed_clear": not fail_closed,
                "hard_blocks_clear": not bool(hard_blocks),
                "vi_safe": not vi_active,
                "halt_clear": not halted,
                "spread_ok": bool(spread_ok),
                "repeated_failed_ready_clear": repeated_failed <= 0,
                "deep_fade_clear": not deep_fade,
                "recovery_signal": recovery_ok,
                "overextended": bool(overextended_momentum),
            },
            "recovery_signals": recovery_signals,
            "current_price": current_price if current_price > 0 else None,
            "buy_zone_high": cap if cap > 0 else None,
            "momentum_state": momentum,
            "pullback_from_high_pct": pullback,
            "evidence_data_state": evidence_state,
            "data_quality": quality,
            "repeated_failed_ready_count": repeated_failed,
            "reasons": reasons,
        }

    def _pathb_negative_watch_suspend_decision() -> tuple[bool, str, str]:
        if not pathb_waiting:
            return False, "", ""
        quality = str(data_quality or "").strip().lower()
        hard_invalidation = pathb_explicit_invalidation or quality in {"bad", "bad_data", "invalid"}
        if (
            market_text == "KR"
            and pathb_hysteresis_enabled
            and not hard_invalidation
            and pathb_negative_watch_count < pathb_suspend_threshold
        ):
            return False, "watch_keeps_pathb_waiting_hysteresis", "pathb_suspend_hysteresis"
        return True, "watch_suspends_stale_pathb", ""

    if gate_final_action == "HARD_BLOCK" or gate_blocker:
        return _decision("HARD_BLOCK", reason=gate_blocker or "hard_safety")

    if active_order_route:
        return _decision(
            "WATCH",
            reason=f"active_order_lock:{active_order_route}",
            warnings=["route_locked_by_active_order"],
        )

    if pathb_active_order and requested in {"PROBE_READY", "BUY_READY", "ADD_READY"}:
        return _decision(
            "WATCH",
            reason="pathb_active_order_blocks_plana",
            warnings=["same_ticker_route_lock"],
        )

    if kr_risk_combo_active and requested in {"BUY_READY", "PULLBACK_WAIT"}:
        gate_context["kr_risk_combo_gate"] = {
            "active": True,
            "tags": list(risk_tags),
            "has_or_missing": has_or_missing,
            "has_high_entry": has_high_entry,
        }
        if requested == "BUY_READY":
            requested = "PROBE_READY"
            evidence_demoted_to = "PROBE_READY"
            evidence_runtime_reason = "kr_risk_combo_gate"
            gate_context["kr_risk_combo_gate"]["result"] = "demote_to_probe"
            default_warnings.append("kr_risk_combo_demoted")
        elif requested == "PULLBACK_WAIT" and not _kr_risk_combo_confirmation_ok(context):
            gate_context["kr_risk_combo_gate"]["result"] = "watch_confirmation_required"
            return _decision(
                "WATCH",
                reason="kr_risk_combo_confirmation_required",
                warnings=["kr_risk_combo_confirmation_required"],
                demoted_to="WATCH",
                runtime_gate_reason="kr_risk_combo_gate",
            )

    if bool(context.get("evidence_pack_ceiling_enabled")) and requested in {"PROBE_READY", "BUY_READY"}:
        evidence_ceiling = str(context.get("evidence_action_ceiling") or "").strip().upper()
        evidence_state = str(context.get("evidence_data_state") or "").strip().lower()
        gate_context["evidence_ceiling_applied"] = False
        if evidence_state == "missing" or evidence_ceiling in {"WATCH", "WAIT_CONFIRMATION"}:
            gate_context["evidence_ceiling_applied"] = True
            gate_context["demotion_layer"] = "evidence_ceiling"
            fail_closed_reason = str(context.get("evidence_fail_closed_reason") or "").strip()
            runtime_reason = fail_closed_reason or (
                "data_fail_closed_watch_only" if bool(context.get("evidence_fail_closed")) else "evidence_action_ceiling"
            )
            return _decision(
                "WATCH",
                reason="data_fail_closed_watch_only" if bool(context.get("evidence_fail_closed")) else "evidence_ceiling_watch",
                warnings=["evidence_ceiling_applied"],
                demoted_to="WATCH",
                runtime_gate_reason=runtime_reason,
            )
        if requested == "BUY_READY" and evidence_state == "partial" and bool(context.get("evidence_partial_grace_active")):
            gate_context["evidence_ceiling_grace"] = "partial_grace"
        elif requested == "BUY_READY" and (evidence_state == "partial" or evidence_ceiling == "PROBE_READY"):
            requested = "PROBE_READY"
            evidence_demoted_to = "PROBE_READY"
            evidence_runtime_reason = "evidence_action_ceiling"
            gate_context["evidence_ceiling_applied"] = True
            gate_context["demotion_layer"] = "evidence_ceiling"
            default_warnings.append("evidence_ceiling_applied")

    if bool(context.get("evidence_pack_ceiling_enabled")) and requested == "PULLBACK_WAIT":
        evidence_ceiling = str(context.get("evidence_action_ceiling") or "").strip().upper()
        evidence_state = str(context.get("evidence_data_state") or "").strip().lower()
        gate_reasons: list[str] = []
        if evidence_state == "missing":
            gate_reasons.append("evidence_missing")
        if evidence_ceiling in {"WATCH", "WAIT_CONFIRMATION", "PROBE_READY"}:
            gate_reasons.append(f"evidence_ceiling_{evidence_ceiling.lower()}")
        if gate_reasons:
            gate_mode = str(os.getenv("PULLBACK_WAIT_EVIDENCE_GATE_MODE", "shadow") or "shadow").strip().lower()
            live_gate = gate_mode in {"live", "enforce", "block"}
            gate_context["pullback_wait_evidence_gate"] = {
                "demoted_to_watch": live_gate,
                "shadow_only": not live_gate,
                "mode": "live" if live_gate else "shadow",
                "reasons": gate_reasons,
                "evidence_action_ceiling": evidence_ceiling,
                "evidence_data_state": evidence_state,
            }
            if live_gate:
                return _decision(
                    "WATCH",
                    reason="pullback_wait_evidence_gate",
                    warnings=["pullback_wait_evidence_gate"],
                    demoted_to="WATCH",
                    runtime_gate_reason="pullback_wait_evidence_gate",
                )
            default_warnings.append("pullback_wait_evidence_shadow")

    if (
        requested in {"PROBE_READY", "BUY_READY"}
        and bool(context.get("soft_gate_override_validation_enabled"))
    ):
        context["route_requested_action"] = requested
        gate_context["route_requested_action"] = requested
        validation = validate_soft_gate_override(action or {}, context)
        gate_context["soft_gate_override_validation"] = validation
        if validation.get("required") and not validation.get("validated"):
            return _decision(
                "WATCH",
                reason=str(validation.get("reason") or "soft_gate_override_failed"),
                warnings=["soft_gate_override_failed"],
                demoted_to="WATCH",
                runtime_gate_reason=str(validation.get("reason") or "soft_gate_override_failed"),
            )

    if (
        market_text == "KR"
        and requested in KR_CONFIRMATION_ACTIONS
        and bool(context.get("kr_confirmation_gate_active"))
    ):
        confirmation_reason = str(context.get("kr_confirmation_reason") or "kr_confirmation_required")
        if not bool(context.get("kr_confirmation_confirmed")):
            if bool(context.get("kr_confirmation_gate_shadow")):
                default_warnings.append("kr_confirmation_required_shadow")
            else:
                gate_context["demotion_layer"] = "kr_confirmation_gate"
                return _decision(
                    "WATCH",
                    reason=confirmation_reason,
                    warnings=["kr_confirmation_required"],
                    demoted_to="WATCH",
                    runtime_gate_reason=confirmation_reason,
                )

    if requested == "PROBE_READY":
        if pathb_waiting and current_price > 0 and pathb_waiting_buy_zone_high > 0 and current_price > pathb_waiting_buy_zone_high:
            good_data = (not data_quality_missing) and str(data_quality or "").lower() in {"good", "normal", "ok", "minute_complete"}
            if confidence >= float(planb_cancel_confidence_min) and not overextended and good_data:
                return _decision(
                    "PROBE_READY",
                    route="PlanA.probe",
                    reason="probe_ready_cancels_pathb_above_zone",
                    cancel_pathb=True,
                )
            return _decision(
                "WATCH",
                reason="probe_blocked_above_pathb_zone",
                warnings=["probe_above_pathb_buy_zone"],
                demoted_to="WATCH",
                runtime_gate_reason="above_pathb_buy_zone",
            )
        return _decision("PROBE_READY", route="PlanA.probe", reason="probe_ready")

    if requested == "BUY_READY":
        if current_price > 0 and cancel_if_open_above > 0 and current_price > cancel_if_open_above:
            return _decision(
                "WATCH",
                reason="buy_ready_chase_blocked",
                warnings=["runtime_gate_chase_blocked"],
                demoted_to="WATCH",
                runtime_gate_reason="chase_above_cancel",
            )
        if market_text == "KR" and current_price > 0 and entry_price_cap is not None and current_price > entry_price_cap:
            return _decision(
                "WATCH",
                reason="buy_ready_price_cap_exceeded",
                warnings=["runtime_gate_price_cap_exceeded"],
                demoted_to="WATCH",
                runtime_gate_reason="entry_price_cap_exceeded",
            )
        if pathb_waiting:
            good_data = (not data_quality_missing) and str(data_quality or "").lower() in {"good", "normal", "ok", "minute_complete"}
            has_buy_zone_context = current_price > 0 and buy_zone_high > 0
            pathb_price_allows_cancel = not has_buy_zone_context or current_price > buy_zone_high
            if market_text == "KR" and entry_price_cap_missing:
                return _decision(
                    "WATCH",
                    reason="pathb_waiting_kept_missing_price_cap",
                    warnings=["buy_ready_missing_price_cap_keeps_pathb"],
                    demoted_to="WATCH",
                    runtime_gate_reason="missing_price_cap",
                )
            if confidence >= float(planb_cancel_confidence_min) and not overextended and good_data and pathb_price_allows_cancel:
                return _decision(
                    "BUY_READY",
                    route="PlanA.buy",
                    reason="buy_ready_cancels_pathb_waiting",
                    cancel_pathb=True,
                )
            keep_reason = "pathb_waiting_kept"
            keep_warning = "buy_ready_not_confident_enough_to_cancel_pathb"
            gate_reason = ""
            if overextended:
                keep_reason = "pathb_waiting_kept_overextended"
                keep_warning = "buy_ready_overextended_keeps_pathb"
                gate_reason = "overextended"
            elif not good_data:
                keep_reason = "pathb_waiting_kept_bad_data"
                keep_warning = "buy_ready_bad_data_keeps_pathb"
                gate_reason = "data_quality"
            elif not pathb_price_allows_cancel:
                keep_reason = "pathb_waiting_kept_inside_buy_zone"
                keep_warning = "buy_ready_inside_pathb_buy_zone_keeps_wait"
                gate_reason = "inside_buy_zone"
            return _decision(
                "WATCH",
                reason=keep_reason,
                warnings=[keep_warning],
                demoted_to="WATCH",
                runtime_gate_reason=gate_reason,
            )
        if overextended:
            return _decision(
                "PROBE_READY",
                route="PlanA.probe",
                reason="buy_ready_demoted_overextended",
                warnings=["runtime_gate_overextended_demoted"],
                demoted_to="PROBE_READY",
                runtime_gate_reason="overextended",
            )
        if market_text == "KR" and entry_price_cap_missing:
            return _decision(
                "PROBE_READY",
                route="PlanA.probe",
                reason="kr_buy_ready_missing_price_cap_demoted",
                warnings=["kr_missing_price_cap_demoted"],
                demoted_to="PROBE_READY",
                runtime_gate_reason="missing_price_cap",
            )
        return _decision("BUY_READY", route="PlanA.buy", reason="buy_ready")

    if requested == "ADD_READY":
        if not has_local_position or not has_broker_position:
            return _decision("WATCH", reason="add_without_position")
        if not add_enabled:
            return _decision("WATCH", reason="add_shadow_only")
        return _decision("ADD_READY", route="PlanA.add", reason="add_ready")

    if requested == "PULLBACK_WAIT":
        if not has_pullback_target(price_targets):
            return _decision("WATCH", reason="missing_pullback_target")
        if _negative_watch_context():
            kr_healthy_shadow = _kr_healthy_pullback_shadow_payload()
            if kr_healthy_shadow:
                gate_context["kr_healthy_pullback_shadow"] = kr_healthy_shadow
            suspend_pathb, _, hysteresis_reason = _pathb_negative_watch_suspend_decision()
            return _decision(
                "WATCH",
                reason="pullback_wait_blocked_negative_context",
                suspend_pathb=suspend_pathb,
                warnings=["pullback_wait_negative_context"],
                demoted_to="WATCH",
                runtime_gate_reason=hysteresis_reason or "negative_pullback_context",
            )
        return _decision("PULLBACK_WAIT", route="PathB.wait", reason="pullback_wait")

    if requested == "WATCH":
        if pathb_waiting and _negative_watch_context():
            suspend_pathb, reason, hysteresis_reason = _pathb_negative_watch_suspend_decision()
            return _decision(
                "WATCH",
                reason=reason,
                suspend_pathb=suspend_pathb,
                warnings=["pathb_waiting_negative_context_shadow"],
                runtime_gate_reason=hysteresis_reason,
            )
        return _decision("WATCH", reason="watch")

    if requested == "AVOID":
        return _decision(
            "WATCH",
            reason="claude_avoid",
            suspend_pathb=bool(pathb_waiting),
            warnings=["pathb_waiting_avoid_shadow"] if pathb_waiting else [],
        )

    if requested == "EXPIRED":
        return _decision("EXPIRED", reason="action_expired")

    if pathb_waiting and _negative_watch_context():
        suspend_pathb, reason, hysteresis_reason = _pathb_negative_watch_suspend_decision()
        return _decision(
            "WATCH",
            reason=reason,
            suspend_pathb=suspend_pathb,
            warnings=["pathb_waiting_negative_context_shadow"],
            runtime_gate_reason=hysteresis_reason,
        )
    return _decision("WATCH", reason="watch")
