from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import os
from typing import Any, Mapping


class ExecutionAdvisorAction(str, Enum):
    KEEP_PLAN = "KEEP_PLAN"
    KEEP_PLAN_WITH_BROKER_ENTRY_AUDIT = "KEEP_PLAN_WITH_BROKER_ENTRY_AUDIT"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    WAIT_LIMIT = "WAIT_LIMIT"
    REPRICE_WITH_GUARD_CANDIDATE = "REPRICE_WITH_GUARD_CANDIDATE"
    CANCEL_MISSED = "CANCEL_MISSED"
    KEEP_LIMIT = "KEEP_LIMIT"
    LOWER_LIMIT_WITH_GUARD_CANDIDATE = "LOWER_LIMIT_WITH_GUARD_CANDIDATE"
    HANDOFF_HOLD_ADVISOR = "HANDOFF_HOLD_ADVISOR"
    BROKER_RECONCILE_REQUIRED = "BROKER_RECONCILE_REQUIRED"
    WAIT_BROKER_TRUTH = "WAIT_BROKER_TRUTH"
    HOLD_REVIEW_REQUIRED = "HOLD_REVIEW_REQUIRED"
    NO_EXECUTION_ADVISOR_ACTION = "NO_EXECUTION_ADVISOR_ACTION"


@dataclass(frozen=True)
class ExecutionAdvisorConfig:
    profile: str = "balanced"
    operator_drift_warn_pct: float = 0.75
    max_chase_above_zone_pct: float = 0.50
    min_upside_pct: float = 1.50
    min_entry_reward_risk: float = 0.80
    sell_limit_gap_warn_pct: float = 2.00
    sell_profit_guard_min_pct: float = 1.00
    buy_order_stale_minutes: int = 20
    max_reprice_chase_pct: float = 0.30
    claude_enabled: bool = False
    manual_only_claude: bool = True
    claude_cooldown_minutes: int = 15
    max_claude_calls_per_day: int = 5

    @classmethod
    def for_profile(cls, profile: str = "balanced", **overrides: Any) -> "ExecutionAdvisorConfig":
        name = str(profile or "balanced").strip().lower()
        presets: dict[str, dict[str, Any]] = {
            "conservative": {
                "operator_drift_warn_pct": 0.50,
                "max_chase_above_zone_pct": 0.30,
                "min_upside_pct": 2.00,
                "min_entry_reward_risk": 0.90,
                "sell_limit_gap_warn_pct": 1.50,
                "sell_profit_guard_min_pct": 1.00,
            },
            "balanced": {
                "operator_drift_warn_pct": 0.75,
                "max_chase_above_zone_pct": 0.50,
                "min_upside_pct": 1.50,
                "min_entry_reward_risk": 0.80,
                "sell_limit_gap_warn_pct": 2.00,
                "sell_profit_guard_min_pct": 1.00,
            },
            "aggressive": {
                "operator_drift_warn_pct": 1.00,
                "max_chase_above_zone_pct": 0.80,
                "min_upside_pct": 1.00,
                "min_entry_reward_risk": 0.70,
                "sell_limit_gap_warn_pct": 2.50,
                "sell_profit_guard_min_pct": 0.50,
            },
        }
        values = dict(presets.get(name, presets["balanced"]))
        values.update(overrides)
        values["profile"] = name if name in presets else "balanced"
        return cls(**values)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ExecutionAdvisorConfig":
        source = env if env is not None else os.environ
        profile = str(source.get("EXEC_ADVISOR_PROFILE", "balanced") or "balanced")
        base = cls.for_profile(profile)
        return cls.for_profile(
            profile,
            operator_drift_warn_pct=_env_float(source, "EXEC_ADVISOR_OPERATOR_DRIFT_WARN_PCT", base.operator_drift_warn_pct),
            max_chase_above_zone_pct=_env_float(source, "EXEC_ADVISOR_MAX_CHASE_ABOVE_ZONE_PCT", base.max_chase_above_zone_pct),
            min_upside_pct=_env_float(source, "EXEC_ADVISOR_MIN_UPSIDE_PCT", base.min_upside_pct),
            min_entry_reward_risk=_env_float(source, "EXEC_ADVISOR_MIN_ENTRY_REWARD_RISK", base.min_entry_reward_risk),
            sell_limit_gap_warn_pct=_env_float(source, "EXEC_ADVISOR_SELL_KEEP_LIMIT_GAP_PCT", base.sell_limit_gap_warn_pct),
            sell_profit_guard_min_pct=_env_float(source, "EXEC_ADVISOR_SELL_PROFIT_KEEP_MIN_PCT", base.sell_profit_guard_min_pct),
            buy_order_stale_minutes=_env_int(source, "EXEC_ADVISOR_BUY_ORDER_STALE_MINUTES", base.buy_order_stale_minutes),
            max_reprice_chase_pct=_env_float(source, "EXEC_ADVISOR_MAX_REPRICE_CHASE_PCT", base.max_reprice_chase_pct),
            claude_enabled=_env_bool(source, "EXEC_ADVISOR_CLAUDE_ENABLED", base.claude_enabled),
            manual_only_claude=_env_bool(source, "EXEC_ADVISOR_MANUAL_ONLY_CLAUDE", base.manual_only_claude),
            claude_cooldown_minutes=_env_int(source, "EXEC_ADVISOR_CLAUDE_COOLDOWN_MINUTES", base.claude_cooldown_minutes),
            max_claude_calls_per_day=_env_int(source, "EXEC_ADVISOR_MAX_CLAUDE_CALLS_PER_DAY", base.max_claude_calls_per_day),
        )


@dataclass(frozen=True)
class ExecutionAdvisorDecision:
    market: str
    ticker: str
    action: ExecutionAdvisorAction
    reason_code: str
    manual_or_mismatch: bool = False
    broker_truth_fresh: bool = True
    claude_candidate: bool = False
    claude_call_expected_if_enabled: bool = False
    path_run_id: str = ""
    order_no: str = ""
    source_flow: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "ticker": self.ticker,
            "action": self.action.value,
            "reason_code": self.reason_code,
            "manual_or_mismatch": self.manual_or_mismatch,
            "broker_truth_fresh": self.broker_truth_fresh,
            "claude_candidate": self.claude_candidate,
            "claude_call_expected_if_enabled": self.claude_call_expected_if_enabled,
            "path_run_id": self.path_run_id,
            "order_no": self.order_no,
            "source_flow": self.source_flow,
            "metrics": dict(self.metrics),
            **dict(self.payload),
        }


@dataclass(frozen=True)
class ClaudeCallGate:
    allowed: bool
    reason_code: str
    cooldown_key: str = ""


def evaluate_filled_pathb_position(
    *,
    market: str,
    ticker: str,
    path_run: Mapping[str, Any] | None,
    broker_position: Mapping[str, Any] | None,
    local_position: Mapping[str, Any] | None = None,
    broker_fills: list[Mapping[str, Any]] | None = None,
    config: ExecutionAdvisorConfig | None = None,
    broker_truth_fresh: bool = True,
) -> ExecutionAdvisorDecision:
    config = config or ExecutionAdvisorConfig.for_profile()
    market_key = _market_key(market)
    ticker_key = _ticker_key(market_key, ticker)
    path_run = dict(path_run or {})
    plan = dict(path_run.get("plan") or path_run.get("plan_json") or {})
    path_run_id = str(path_run.get("path_run_id") or plan.get("path_run_id") or "")

    if not broker_truth_fresh:
        return _decision(
            market_key,
            ticker_key,
            ExecutionAdvisorAction.WAIT_BROKER_TRUTH,
            "broker_truth_stale",
            broker_truth_fresh=False,
            path_run_id=path_run_id,
            source_flow="filled_pathb_position",
        )
    if not broker_position or _safe_float(broker_position.get("avg_price")) <= 0 or _safe_int(broker_position.get("qty")) <= 0:
        return _decision(
            market_key,
            ticker_key,
            ExecutionAdvisorAction.BROKER_RECONCILE_REQUIRED,
            "broker_position_missing_for_filled_pathb",
            path_run_id=path_run_id,
            source_flow="filled_pathb_position",
        )

    metrics = build_plan_economics_metrics(
        plan=plan,
        broker_position=broker_position,
        local_position=local_position,
    )
    manual_reasons = _manual_or_mismatch_reasons(
        market_key,
        ticker_key,
        plan,
        broker_position,
        broker_fills or [],
        metrics,
        config,
    )
    broken_reasons = _plan_economics_broken_reasons(metrics, config)
    manual_or_mismatch = bool(manual_reasons)
    plan_broken = bool(broken_reasons)
    metrics["manual_or_mismatch_reasons"] = manual_reasons
    metrics["plan_economics_broken_reasons"] = broken_reasons

    if manual_or_mismatch and plan_broken:
        return _decision(
            market_key,
            ticker_key,
            ExecutionAdvisorAction.REPLAN_REQUIRED,
            _join_reason(["manual_or_mismatch", *broken_reasons]),
            manual_or_mismatch=True,
            claude_candidate=True,
            claude_call_expected_if_enabled=True,
            path_run_id=path_run_id,
            order_no=_planned_order_no(plan),
            source_flow="filled_pathb_position",
            metrics=metrics,
            payload={"claude_replan_input": build_claude_replan_input(market_key, ticker_key, metrics, plan)},
        )

    if manual_or_mismatch:
        return _decision(
            market_key,
            ticker_key,
            ExecutionAdvisorAction.KEEP_PLAN_WITH_BROKER_ENTRY_AUDIT,
            _join_reason(["broker_entry_audit", *manual_reasons]),
            manual_or_mismatch=True,
            path_run_id=path_run_id,
            order_no=_planned_order_no(plan),
            source_flow="filled_pathb_position",
            metrics=metrics,
        )

    current = _safe_float(metrics.get("current_price"))
    target = _safe_float(metrics.get("target_price"))
    stop = _safe_float(metrics.get("stop_price"))
    if target > 0 and current >= target:
        return _decision(
            market_key,
            ticker_key,
            ExecutionAdvisorAction.NO_EXECUTION_ADVISOR_ACTION,
            "existing_pathb_target_exit_priority",
            path_run_id=path_run_id,
            source_flow="filled_pathb_position",
            metrics=metrics,
        )
    if stop > 0 and current <= stop:
        return _decision(
            market_key,
            ticker_key,
            ExecutionAdvisorAction.HANDOFF_HOLD_ADVISOR,
            "existing_pathb_stop_exit_priority",
            path_run_id=path_run_id,
            source_flow="filled_pathb_position",
            metrics=metrics,
        )
    if plan_broken:
        return _decision(
            market_key,
            ticker_key,
            ExecutionAdvisorAction.HOLD_REVIEW_REQUIRED,
            _join_reason(["plan_economics_degraded", *broken_reasons]),
            claude_candidate=True,
            path_run_id=path_run_id,
            source_flow="filled_pathb_position",
            metrics=metrics,
            payload={"claude_replan_input": build_claude_replan_input(market_key, ticker_key, metrics, plan)},
        )
    return _decision(
        market_key,
        ticker_key,
        ExecutionAdvisorAction.KEEP_PLAN,
        "plan_economics_intact",
        path_run_id=path_run_id,
        source_flow="filled_pathb_position",
        metrics=metrics,
    )


def evaluate_pending_buy_order(
    *,
    market: str,
    order: Mapping[str, Any],
    plan: Mapping[str, Any] | None = None,
    current_price: float | None = None,
    config: ExecutionAdvisorConfig | None = None,
    broker_truth_fresh: bool = True,
) -> ExecutionAdvisorDecision:
    config = config or ExecutionAdvisorConfig.for_profile()
    market_key = _market_key(market)
    ticker = _ticker_key(market_key, str(order.get("ticker") or ""))
    order_no = str(order.get("order_no") or "")
    if not broker_truth_fresh:
        return _decision(market_key, ticker, ExecutionAdvisorAction.WAIT_BROKER_TRUTH, "broker_truth_stale", broker_truth_fresh=False, order_no=order_no, source_flow="pending_buy_order")
    if _safe_int(order.get("remaining_qty")) <= 0:
        return _decision(market_key, ticker, ExecutionAdvisorAction.BROKER_RECONCILE_REQUIRED, "pending_buy_order_not_open", order_no=order_no, source_flow="pending_buy_order")

    limit_price = _safe_float(order.get("limit_price") or order.get("order_price"))
    current = _safe_float(current_price if current_price is not None else order.get("current_price"))
    metrics = {
        "limit_price": limit_price,
        "current_price": current,
        "current_above_limit_pct": _round_pct(_pct(current, limit_price)),
    }
    if limit_price <= 0 or current <= 0:
        return _decision(market_key, ticker, ExecutionAdvisorAction.WAIT_LIMIT, "pending_buy_missing_price", order_no=order_no, source_flow="pending_buy_order", metrics=metrics)
    if current <= limit_price:
        return _decision(market_key, ticker, ExecutionAdvisorAction.WAIT_LIMIT, "limit_still_touchable", order_no=order_no, source_flow="pending_buy_order", metrics=metrics)
    if _safe_float(metrics["current_above_limit_pct"]) <= config.max_reprice_chase_pct:
        synthetic_position = {"avg_price": current, "current_price": current, "qty": max(1, _safe_int(order.get("remaining_qty")))}
        economics = build_plan_economics_metrics(plan=dict(plan or {}), broker_position=synthetic_position)
        broken = _plan_economics_broken_reasons(economics, config)
        metrics.update({f"plan_{k}": v for k, v in economics.items() if k.endswith("_pct") or k.endswith("_risk")})
        if not broken:
            return _decision(market_key, ticker, ExecutionAdvisorAction.REPRICE_WITH_GUARD_CANDIDATE, "small_reprice_guard_candidate", order_no=order_no, source_flow="pending_buy_order", metrics=metrics)
    return _decision(market_key, ticker, ExecutionAdvisorAction.CANCEL_MISSED, "limit_missed_or_chase_too_high", order_no=order_no, source_flow="pending_buy_order", metrics=metrics)


def evaluate_open_sell_order(
    *,
    market: str,
    order: Mapping[str, Any],
    broker_position: Mapping[str, Any] | None,
    config: ExecutionAdvisorConfig | None = None,
    broker_truth_fresh: bool = True,
) -> ExecutionAdvisorDecision:
    config = config or ExecutionAdvisorConfig.for_profile()
    market_key = _market_key(market)
    ticker = _ticker_key(market_key, str(order.get("ticker") or (broker_position or {}).get("ticker") or ""))
    order_no = str(order.get("order_no") or "")
    if not broker_truth_fresh:
        return _decision(market_key, ticker, ExecutionAdvisorAction.WAIT_BROKER_TRUTH, "broker_truth_stale", broker_truth_fresh=False, order_no=order_no, source_flow="open_sell_order")
    if not broker_position or _safe_int(broker_position.get("qty")) <= 0:
        return _decision(market_key, ticker, ExecutionAdvisorAction.BROKER_RECONCILE_REQUIRED, "sell_order_without_broker_position", order_no=order_no, source_flow="open_sell_order")

    limit_price = _safe_float(order.get("limit_price") or order.get("order_price"))
    current = _safe_float(broker_position.get("current_price") or order.get("current_price"))
    avg = _safe_float(broker_position.get("avg_price"))
    pnl_pct = _pct(current, avg)
    limit_gap_pct = _pct(limit_price, current)
    metrics = {
        "limit_price": limit_price,
        "current_price": current,
        "broker_avg_price": avg,
        "pnl_pct": _round_pct(pnl_pct),
        "limit_gap_pct": _round_pct(limit_gap_pct),
    }
    if limit_price <= 0 or current <= 0 or avg <= 0:
        return _decision(market_key, ticker, ExecutionAdvisorAction.KEEP_LIMIT, "sell_guard_missing_price", order_no=order_no, source_flow="open_sell_order", metrics=metrics)
    if current >= limit_price:
        return _decision(market_key, ticker, ExecutionAdvisorAction.KEEP_LIMIT, "sell_limit_touchable", order_no=order_no, source_flow="open_sell_order", metrics=metrics)
    if limit_gap_pct <= config.sell_limit_gap_warn_pct and pnl_pct >= config.sell_profit_guard_min_pct:
        return _decision(market_key, ticker, ExecutionAdvisorAction.KEEP_LIMIT, "sell_limit_near_and_profitable", order_no=order_no, source_flow="open_sell_order", metrics=metrics)
    if pnl_pct >= config.sell_profit_guard_min_pct:
        return _decision(
            market_key,
            ticker,
            ExecutionAdvisorAction.LOWER_LIMIT_WITH_GUARD_CANDIDATE,
            "sell_limit_far_profitable_guard_candidate",
            claude_candidate=True,
            order_no=order_no,
            source_flow="open_sell_order",
            metrics=metrics,
        )
    return _decision(market_key, ticker, ExecutionAdvisorAction.KEEP_LIMIT, "sell_position_not_profitable_enough", order_no=order_no, source_flow="open_sell_order", metrics=metrics)


def evaluate_existing_position(
    *,
    market: str,
    broker_position: Mapping[str, Any],
    broker_truth_fresh: bool = True,
) -> ExecutionAdvisorDecision:
    market_key = _market_key(market)
    ticker = _ticker_key(market_key, str(broker_position.get("ticker") or ""))
    if not broker_truth_fresh:
        return _decision(market_key, ticker, ExecutionAdvisorAction.WAIT_BROKER_TRUTH, "broker_truth_stale", broker_truth_fresh=False, source_flow="existing_position")
    return _decision(
        market_key,
        ticker,
        ExecutionAdvisorAction.NO_EXECUTION_ADVISOR_ACTION,
        "no_active_execution_drift_surface",
        source_flow="existing_position",
        metrics={
            "broker_avg_price": _safe_float(broker_position.get("avg_price")),
            "current_price": _safe_float(broker_position.get("current_price")),
            "qty": _safe_int(broker_position.get("qty")),
        },
    )


def build_plan_economics_metrics(
    *,
    plan: Mapping[str, Any],
    broker_position: Mapping[str, Any],
    local_position: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    local_position = local_position or {}
    planned_entry = _first_positive(
        plan,
        "actual_entry_price",
        "entry_price",
        "filled_price",
        "fill_price",
        "buy_price",
        "planned_entry",
    )
    if planned_entry <= 0:
        planned_entry = _first_positive(local_position, "display_avg_price", "avg_price", "entry", "entry_price")
    actual_entry = _first_positive(broker_position, "avg_price", "display_avg_price", "entry", "entry_price")
    current = _first_positive(broker_position, "current_price", "display_current_price", "eval_price", "price")
    if current <= 0:
        current = actual_entry
    target = _first_positive(plan, "sell_target", "target_price", "take_profit", "tp_price", "target")
    stop = _first_positive(plan, "stop_loss", "stop_price", "loss_cap", "hard_stop", "sl_price", "stop")
    zone_high = _first_positive(plan, "buy_zone_high", "zone_high", "entry_zone_high")
    if zone_high <= 0:
        zone_high = planned_entry

    entry_drift_pct = _pct(actual_entry, planned_entry)
    above_zone_high_pct = _pct(actual_entry, zone_high)
    target_upside_pct = _pct(target, actual_entry)
    remaining_upside_pct = _pct(target, current)
    entry_stop_loss_pct = _pct(stop, actual_entry)
    entry_risk_pct = max(0.0, -entry_stop_loss_pct)
    current_stop_loss_pct = _pct(stop, current)
    current_risk_pct = max(0.0, -current_stop_loss_pct)
    return {
        "planned_entry": _round_price(planned_entry),
        "actual_entry": _round_price(actual_entry),
        "broker_avg_price": _round_price(actual_entry),
        "current_price": _round_price(current),
        "target_price": _round_price(target),
        "stop_price": _round_price(stop),
        "buy_zone_high": _round_price(zone_high),
        "entry_drift_pct": _round_pct(entry_drift_pct),
        "above_zone_high_pct": _round_pct(above_zone_high_pct),
        "target_upside_pct": _round_pct(target_upside_pct),
        "remaining_upside_pct": _round_pct(remaining_upside_pct),
        "entry_stop_loss_pct": _round_pct(entry_stop_loss_pct),
        "entry_risk_pct": _round_pct(entry_risk_pct),
        "current_stop_loss_pct": _round_pct(current_stop_loss_pct),
        "current_risk_pct": _round_pct(current_risk_pct),
        "entry_reward_risk": _round_ratio(_safe_div(target_upside_pct, entry_risk_pct)),
        "current_reward_risk": _round_ratio(_safe_div(remaining_upside_pct, current_risk_pct)),
        "pnl_pct": _round_pct(_pct(current, actual_entry)),
    }


def build_claude_replan_input(
    market: str,
    ticker: str,
    metrics: Mapping[str, Any],
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "market": _market_key(market),
        "ticker": _ticker_key(market, ticker),
        "planned_entry": metrics.get("planned_entry"),
        "actual_entry": metrics.get("actual_entry"),
        "current_price": metrics.get("current_price"),
        "target_price": metrics.get("target_price"),
        "stop_price": metrics.get("stop_price"),
        "buy_zone_high": metrics.get("buy_zone_high"),
        "entry_drift_pct": metrics.get("entry_drift_pct"),
        "above_zone_high_pct": metrics.get("above_zone_high_pct"),
        "remaining_upside_pct": metrics.get("remaining_upside_pct"),
        "entry_reward_risk": metrics.get("entry_reward_risk"),
        "current_reward_risk": metrics.get("current_reward_risk"),
        "manual_or_mismatch_reasons": list(metrics.get("manual_or_mismatch_reasons") or []),
        "plan_economics_broken_reasons": list(metrics.get("plan_economics_broken_reasons") or []),
        "current_plan": _compact_plan(plan or {}),
    }


def should_call_claude(
    decision: ExecutionAdvisorDecision,
    *,
    config: ExecutionAdvisorConfig,
    cooldown_state: Mapping[str, Any] | None = None,
    daily_call_count: int = 0,
    now: datetime | None = None,
) -> ClaudeCallGate:
    cooldown_key = f"{decision.market}:{decision.ticker}:{decision.action.value}"
    if not config.claude_enabled:
        return ClaudeCallGate(False, "claude_disabled", cooldown_key)
    if not decision.claude_candidate:
        return ClaudeCallGate(False, "not_claude_candidate", cooldown_key)
    if config.manual_only_claude and not decision.manual_or_mismatch:
        return ClaudeCallGate(False, "manual_only_claude_gate", cooldown_key)
    if daily_call_count >= max(0, int(config.max_claude_calls_per_day)):
        return ClaudeCallGate(False, "daily_claude_cap_reached", cooldown_key)
    last = (cooldown_state or {}).get(cooldown_key)
    if _cooldown_active(last, config.claude_cooldown_minutes, now=now):
        return ClaudeCallGate(False, "claude_cooldown_active", cooldown_key)
    return ClaudeCallGate(True, "claude_call_allowed", cooldown_key)


def _manual_or_mismatch_reasons(
    market: str,
    ticker: str,
    plan: Mapping[str, Any],
    broker_position: Mapping[str, Any],
    broker_fills: list[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    config: ExecutionAdvisorConfig,
) -> list[str]:
    reasons: list[str] = []
    planned_entry = _safe_float(metrics.get("planned_entry"))
    if planned_entry <= 0:
        reasons.append("planned_entry_missing")
    if abs(_safe_float(metrics.get("entry_drift_pct"))) >= config.operator_drift_warn_pct:
        reasons.append("operator_entry_drift")
    if _safe_float(metrics.get("above_zone_high_pct")) >= config.max_chase_above_zone_pct:
        reasons.append("broker_avg_above_buy_zone_high")
    if _broker_fill_order_mismatch(market, ticker, plan, broker_fills):
        reasons.append("entry_order_mismatch")
    if str(broker_position.get("manual_override") or broker_position.get("source") or "").strip().lower() in {"manual", "operator"}:
        reasons.append("manual_broker_position")
    return _unique(reasons)


def _plan_economics_broken_reasons(metrics: Mapping[str, Any], config: ExecutionAdvisorConfig) -> list[str]:
    reasons: list[str] = []
    target = _safe_float(metrics.get("target_price"))
    stop = _safe_float(metrics.get("stop_price"))
    current = _safe_float(metrics.get("current_price"))
    actual = _safe_float(metrics.get("actual_entry"))
    if target <= 0:
        reasons.append("target_missing")
    if stop <= 0:
        reasons.append("stop_missing")
    if actual <= 0:
        reasons.append("actual_entry_missing")
    if _safe_float(metrics.get("above_zone_high_pct")) >= config.max_chase_above_zone_pct:
        reasons.append("broker_avg_above_buy_zone_high")
    if _safe_float(metrics.get("target_upside_pct")) < config.min_upside_pct:
        reasons.append("entry_upside_below_min")
    if _safe_float(metrics.get("entry_reward_risk")) < config.min_entry_reward_risk:
        reasons.append("entry_reward_risk_below_min")
    if target > 0 and current >= target:
        reasons.append("current_at_or_above_target")
    return _unique(reasons)


def _broker_fill_order_mismatch(
    market: str,
    ticker: str,
    plan: Mapping[str, Any],
    broker_fills: list[Mapping[str, Any]],
) -> bool:
    planned = _planned_order_no(plan)
    if not planned:
        return False
    ticker_key = _ticker_key(market, ticker)
    fill_order_nos = {
        str(row.get("order_no") or "").strip()
        for row in broker_fills
        if _ticker_key(market, str(row.get("ticker") or "")) == ticker_key
        and str(row.get("side") or "").strip().lower() in {"buy", "b"}
        and _safe_int(row.get("filled_qty") or row.get("qty")) > 0
        and str(row.get("order_no") or "").strip()
    }
    return bool(fill_order_nos and planned not in fill_order_nos)


def _planned_order_no(plan: Mapping[str, Any]) -> str:
    return str(plan.get("entry_order_no") or plan.get("order_no") or plan.get("entry_execution_id") or "").strip()


def _compact_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "buy_zone_low",
        "buy_zone_high",
        "sell_target",
        "target_price",
        "stop_loss",
        "stop_price",
        "actual_entry_price",
        "entry_order_no",
        "entry_execution_id",
        "path_type",
        "strategy",
    )
    return {key: plan.get(key) for key in keys if key in plan}


def _decision(
    market: str,
    ticker: str,
    action: ExecutionAdvisorAction,
    reason_code: str,
    *,
    manual_or_mismatch: bool = False,
    broker_truth_fresh: bool = True,
    claude_candidate: bool = False,
    claude_call_expected_if_enabled: bool = False,
    path_run_id: str = "",
    order_no: str = "",
    source_flow: str = "",
    metrics: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
) -> ExecutionAdvisorDecision:
    return ExecutionAdvisorDecision(
        market=market,
        ticker=ticker,
        action=action,
        reason_code=reason_code,
        manual_or_mismatch=manual_or_mismatch,
        broker_truth_fresh=broker_truth_fresh,
        claude_candidate=claude_candidate,
        claude_call_expected_if_enabled=claude_call_expected_if_enabled,
        path_run_id=path_run_id,
        order_no=order_no,
        source_flow=source_flow,
        metrics=dict(metrics or {}),
        payload=dict(payload or {}),
    )


def _market_key(value: str) -> str:
    return "US" if str(value or "").strip().upper() == "US" else "KR"


def _ticker_key(market: str, ticker: str) -> str:
    raw = str(ticker or "").strip()
    return raw.upper() if _market_key(market) == "US" else raw


def _first_positive(source: Mapping[str, Any], *keys: str) -> float:
    for key in keys:
        value = _safe_float(source.get(key))
        if value > 0:
            return value
    return 0.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        return float(str(value).replace(",", ""))
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(float(str(value).replace(",", "")))
    except Exception:
        return int(default)


def _pct(numerator: float, denominator: float) -> float:
    numerator = _safe_float(numerator)
    denominator = _safe_float(denominator)
    if denominator <= 0 or numerator <= 0:
        return 0.0
    return (numerator / denominator - 1.0) * 100.0


def _safe_div(numerator: float, denominator: float) -> float:
    numerator = _safe_float(numerator)
    denominator = _safe_float(denominator)
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _round_pct(value: float) -> float:
    return round(_safe_float(value), 3)


def _round_ratio(value: float) -> float:
    return round(_safe_float(value), 3)


def _round_price(value: float) -> float:
    return round(_safe_float(value), 4)


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _join_reason(parts: list[str]) -> str:
    return ":".join(_unique([str(part or "").strip() for part in parts if str(part or "").strip()]))


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    value = env.get(key)
    if value is None or str(value).strip() == "":
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    value = env.get(key)
    if value is None or str(value).strip() == "":
        return float(default)
    return _safe_float(value, default)


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    value = env.get(key)
    if value is None or str(value).strip() == "":
        return int(default)
    return _safe_int(value, default)


def _cooldown_active(value: Any, minutes: int, *, now: datetime | None = None) -> bool:
    if value in (None, ""):
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        last = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        try:
            last = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return False
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    elapsed = (current - last.astimezone(timezone.utc)).total_seconds()
    return elapsed < max(0, int(minutes)) * 60
