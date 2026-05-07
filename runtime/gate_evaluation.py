from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class GateEvaluation:
    ticker: str
    market: str
    known_at: str
    claude_action: str = "WATCH"
    final_action: str = "WATCH"
    hard_safety: dict[str, Any] = field(default_factory=dict)
    soft_safety: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict)
    sizing: dict[str, Any] = field(default_factory=dict)
    affordability: dict[str, Any] = field(default_factory=dict)
    route_lock: dict[str, Any] = field(default_factory=dict)
    blocker: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "market": self.market,
            "known_at": self.known_at,
            "claude_action": self.claude_action,
            "final_action": self.final_action,
            "hard_safety": self.hard_safety,
            "soft_safety": self.soft_safety,
            "timing": self.timing,
            "sizing": self.sizing,
            "affordability": self.affordability,
            "route_lock": self.route_lock,
            "blocker": self.blocker,
            "warnings": list(self.warnings),
        }


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def unconfirmed_soft_cap(
    judgment_gate_reason: str,
    *,
    cap_pct: int = 70,
) -> dict[str, Any]:
    if str(judgment_gate_reason or "") != "ok_unconfirmed":
        return {"applies": False, "size_cap_pct": None, "warnings": []}
    cap = max(1, min(100, int(cap_pct or 70)))
    return {
        "applies": True,
        "size_cap_pct": cap,
        "warnings": ["unconfirmed_phase_size_cap"],
    }


def apply_size_cap_once(
    current_size_pct: int,
    *,
    size_cap_pct: int | None,
) -> tuple[int, bool]:
    size = max(1, min(100, int(current_size_pct or 1)))
    if size_cap_pct is None:
        return size, False
    cap = max(1, min(100, int(size_cap_pct)))
    if size > cap:
        return cap, True
    return size, False


def build_judgment_gate_evaluation(
    *,
    market: str,
    ticker: str,
    judgment_gate_ok: bool,
    judgment_gate_reason: str,
    cap_pct: int = 70,
) -> GateEvaluation:
    soft_cap = unconfirmed_soft_cap(judgment_gate_reason, cap_pct=cap_pct)
    if judgment_gate_ok:
        final_action = "WATCH"
        blocker = None
        hard = {"passed": True}
    else:
        final_action = "HARD_BLOCK"
        blocker = "judgment_not_executable"
        hard = {"passed": False, "reason": judgment_gate_reason}
    return GateEvaluation(
        ticker=str(ticker),
        market=str(market).upper(),
        known_at=now_iso(),
        claude_action="WATCH",
        final_action=final_action,
        hard_safety=hard,
        soft_safety={
            "passed": True,
            "size_cap_pct": soft_cap.get("size_cap_pct"),
            "warnings": soft_cap.get("warnings") or [],
        },
        timing={"passed": True},
        sizing={},
        affordability={},
        blocker=blocker,
        warnings=list(soft_cap.get("warnings") or []),
    )
