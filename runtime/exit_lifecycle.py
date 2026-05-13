from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SYSTEM_GUARD_REASONS = {
    "loss_cap",
    "stop_loss",
    "profit_floor",
    "profit_ladder",
    "mfe_breakeven",
    "trail_stop",
    "soft_exit_floor_price",
    "broker_mismatch",
    "recovery_micro_time_stop",
    "recovery_micro_no_carry",
    "pre_close",
    "session_close",
    "operator_kill",
    "daily_loss_stop",
    "hard_loss",
}

DEFAULT_LIVE_BYPASS_REASONS = {
    "recovery_micro_time_stop",
    "recovery_micro_no_carry",
    "pre_close",
    "session_close",
    "broker_mismatch",
    "hard_loss",
    "operator_kill",
    "daily_loss_stop",
}

EXPANDABLE_LIVE_BYPASS_REASONS = {
    "trail_exit",
    "sla_exit",
}


def reason_family(reason: str, exit_candidate: dict[str, Any] | None = None, position: dict[str, Any] | None = None) -> str:
    reason_key = str(reason or "").strip()
    candidate = dict(exit_candidate or {})
    pos = dict(position or {})
    trigger = str(candidate.get("recovery_micro_exit_trigger") or pos.get("recovery_micro_exit_trigger") or "").strip()
    if reason_key == "loss_cap" and trigger == "recovery_micro_hard_loss":
        return "hard_loss"
    if reason_key == "pre_close" and (
        bool(candidate.get("recovery_micro_no_carry"))
        or bool(pos.get("recovery_micro_no_carry"))
        or trigger == "recovery_micro_pre_close"
    ):
        return "recovery_micro_no_carry"
    return reason_key


def exit_lifecycle_bypass_allowed(
    decision: dict[str, Any] | ExitLifecycleDecision | None,
    *,
    allowlist: set[str] | list[str] | tuple[str, ...] | None = None,
) -> bool:
    if decision is None:
        return False
    data = decision.to_dict() if isinstance(decision, ExitLifecycleDecision) else dict(decision or {})
    if str(data.get("final_action") or "").upper() != "SELL":
        return False
    if bool(data.get("claude_override_allowed", True)):
        return False
    allowed = set(allowlist or DEFAULT_LIVE_BYPASS_REASONS)
    return str(data.get("reason") or "") in allowed


@dataclass
class ExitLifecycleDecision:
    ticker: str
    final_action: str
    reason: str
    claude_override_allowed: bool = False
    priority: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "final_action": self.final_action,
            "reason": self.reason,
            "claude_override_allowed": self.claude_override_allowed,
            "priority": self.priority,
            "warnings": list(self.warnings),
        }


def decide_exit_lifecycle(
    position: dict[str, Any],
    *,
    exit_candidate: dict[str, Any] | None = None,
    claude_vote: str | None = None,
) -> ExitLifecycleDecision:
    ticker = str((position or {}).get("ticker") or (exit_candidate or {}).get("ticker") or "")
    candidate = dict(exit_candidate or {})
    raw_reason = str(candidate.get("reason") or "")
    reason = reason_family(raw_reason, candidate, position)
    vote = str(claude_vote or "").upper()
    if reason in SYSTEM_GUARD_REASONS:
        return ExitLifecycleDecision(
            ticker=ticker,
            final_action="SELL",
            reason=reason,
            claude_override_allowed=False,
            priority=100,
            warnings=["system_guard_precedes_claude"],
        )
    if reason:
        return ExitLifecycleDecision(
            ticker=ticker,
            final_action="SELL" if vote == "SELL" else "REVIEW",
            reason=reason,
            claude_override_allowed=True,
            priority=50,
        )
    if vote == "SELL":
        return ExitLifecycleDecision(
            ticker=ticker,
            final_action="SELL",
            reason="claude_sell",
            claude_override_allowed=True,
            priority=10,
        )
    return ExitLifecycleDecision(
        ticker=ticker,
        final_action="HOLD",
        reason="no_exit_trigger",
        claude_override_allowed=True,
        priority=0,
    )
