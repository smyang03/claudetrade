from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SYSTEM_GUARD_REASONS = {
    "loss_cap",
    "stop_loss",
    "profit_floor",
    "mfe_breakeven",
    "trail_stop",
    "soft_exit_floor_price",
    "broker_mismatch",
    "recovery_micro_time_stop",
    "recovery_micro_no_carry",
    "pre_close",
    "session_close",
}


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
    reason = str(candidate.get("reason") or "")
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
