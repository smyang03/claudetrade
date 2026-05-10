from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


PLANB_CANCEL_CONFIDENCE_MIN = 0.75


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
    data_quality: str = "good",
    planb_cancel_confidence_min: float = PLANB_CANCEL_CONFIDENCE_MIN,
    execution_context: dict[str, Any] | None = None,
) -> RouteDecision:
    ticker = str((action or {}).get("ticker") or "")
    market_text = str(market or (action or {}).get("market") or "").upper()
    requested = str((action or {}).get("action") or "WATCH")
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
    data_quality = str(context.get("data_quality") or data_quality or "good")

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
            original_action=requested,
            demoted_to=demoted_to,
            runtime_gate_reason=runtime_gate_reason,
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
            good_data = str(data_quality or "").lower() in {"good", "normal", "ok"}
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
            good_data = str(data_quality or "").lower() in {"good", "normal", "ok"}
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
