from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


PLANB_CANCEL_CONFIDENCE_MIN = 0.75


ENTRY_ACTIONS = {"PROBE_READY", "BUY_READY", "ADD_READY"}


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
    max_entry_price = _num_or_none(action.get("max_entry_price") or context.get("max_entry_price"))
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
    price_ok = not (current_price is not None and max_entry_price is not None and max_entry_price > 0 and current_price > max_entry_price)
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
    validated = bool(momentum_ok and confirmation_ok and price_ok)
    failed_checks: list[str] = []
    if not momentum_ok:
        failed_checks.append("fresh_momentum_missing")
    if not confirmation_ok:
        failed_checks.append("or_vwap_volume_confirmation_missing")
    if not price_ok:
        failed_checks.append("max_entry_price_exceeded")
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
            "ret3_only_grace_used": ret3_only_grace_used,
        },
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
    gate_context["overextended"] = bool(overextended)
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
        "evidence_pack_ceiling_enabled",
        "evidence_data_state",
        "evidence_missing_fields",
        "evidence_action_ceiling",
        "evidence_partial_grace_active",
        "evidence_pack",
        "spread_bps",
        "kr_confirmation_gate_active",
        "kr_confirmation_gate_enabled",
        "kr_confirmation_gate_shadow",
        "kr_confirmation_confirmed",
        "kr_confirmation_state",
        "kr_confirmation_reason",
        "kr_confirmation_checks",
    ):
        if context.get(key) is not None:
            gate_context[key] = context.get(key)

    default_warnings: list[str] = []
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

    if bool(context.get("evidence_pack_ceiling_enabled")) and requested in {"PROBE_READY", "BUY_READY"}:
        evidence_ceiling = str(context.get("evidence_action_ceiling") or "").strip().upper()
        evidence_state = str(context.get("evidence_data_state") or "").strip().lower()
        gate_context["evidence_ceiling_applied"] = False
        if evidence_state == "missing" or evidence_ceiling in {"WATCH", "WAIT_CONFIRMATION"}:
            gate_context["evidence_ceiling_applied"] = True
            return _decision(
                "WATCH",
                reason="evidence_ceiling_watch",
                warnings=["evidence_ceiling_applied"],
                demoted_to="WATCH",
                runtime_gate_reason="evidence_action_ceiling",
            )
        if requested == "BUY_READY" and evidence_state == "partial" and bool(context.get("evidence_partial_grace_active")):
            gate_context["evidence_ceiling_grace"] = "partial_grace"
        elif requested == "BUY_READY" and (evidence_state == "partial" or evidence_ceiling == "PROBE_READY"):
            requested = "PROBE_READY"
            evidence_demoted_to = "PROBE_READY"
            evidence_runtime_reason = "evidence_action_ceiling"
            gate_context["evidence_ceiling_applied"] = True
            default_warnings.append("evidence_ceiling_applied")

    if (
        requested in {"PROBE_READY", "BUY_READY"}
        and bool(context.get("soft_gate_override_validation_enabled"))
    ):
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
        and requested in {"PROBE_READY", "BUY_READY"}
        and bool(context.get("kr_confirmation_gate_active"))
    ):
        confirmation_reason = str(context.get("kr_confirmation_reason") or "kr_confirmation_required")
        if not bool(context.get("kr_confirmation_confirmed")):
            if bool(context.get("kr_confirmation_gate_shadow")):
                default_warnings.append("kr_confirmation_required_shadow")
            else:
                return _decision(
                    "WATCH",
                    reason=confirmation_reason,
                    warnings=["kr_confirmation_required"],
                    demoted_to="WATCH",
                    runtime_gate_reason=confirmation_reason,
                )

    if requested == "PROBE_READY":
        if pathb_waiting and current_price > 0 and pathb_waiting_buy_zone_high > 0 and current_price > pathb_waiting_buy_zone_high:
            good_data = (not data_quality_missing) and str(data_quality or "").lower() in {"good", "normal", "ok"}
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
        if pathb_waiting:
            good_data = (not data_quality_missing) and str(data_quality or "").lower() in {"good", "normal", "ok"}
            has_buy_zone_context = current_price > 0 and buy_zone_high > 0
            pathb_price_allows_cancel = not has_buy_zone_context or current_price > buy_zone_high
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
            return _decision(
                "WATCH",
                reason="pullback_wait_blocked_negative_context",
                suspend_pathb=bool(pathb_waiting),
                warnings=["pullback_wait_negative_context"],
                demoted_to="WATCH",
                runtime_gate_reason="negative_pullback_context",
            )
        return _decision("PULLBACK_WAIT", route="PathB.wait", reason="pullback_wait")

    if requested == "WATCH":
        if pathb_waiting and _negative_watch_context():
            return _decision(
                "WATCH",
                reason="watch_suspends_stale_pathb",
                suspend_pathb=True,
                warnings=["pathb_waiting_negative_context_shadow"],
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
        return _decision(
            "WATCH",
            reason="watch_suspends_stale_pathb",
            suspend_pathb=True,
            warnings=["pathb_waiting_negative_context_shadow"],
        )
    return _decision("WATCH", reason="watch")
