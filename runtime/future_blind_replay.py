from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable


@dataclass
class ReplayBaseline:
    scenario: str
    decision_at: str
    used_snapshots: int
    skipped_future_snapshots: int
    missing_funnel_log: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "decision_at": self.decision_at,
            "used_snapshots": self.used_snapshots,
            "skipped_future_snapshots": self.skipped_future_snapshots,
            "missing_funnel_log": self.missing_funnel_log,
            "notes": list(self.notes),
        }


def parse_dt(value: Any) -> datetime:
    text = str(value or "").strip()
    if text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.min


def snapshot_is_known(snapshot: dict[str, Any], *, decision_at: Any) -> bool:
    known_at = snapshot.get("known_at") or snapshot.get("captured_at") or snapshot.get("written_at")
    return parse_dt(known_at) <= parse_dt(decision_at)


def filter_known_snapshots(
    snapshots: Iterable[dict[str, Any]],
    *,
    decision_at: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    used: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if snapshot_is_known(snapshot, decision_at=decision_at):
            used.append(dict(snapshot))
        else:
            skipped.append(dict(snapshot))
    return used, skipped


def build_replay_baseline(
    *,
    scenario: str,
    decision_at: Any,
    snapshots: Iterable[dict[str, Any]],
    missing_funnel_log: bool = False,
) -> ReplayBaseline:
    used, skipped = filter_known_snapshots(snapshots, decision_at=decision_at)
    notes: list[str] = []
    if missing_funnel_log:
        notes.append("missing_funnel_log")
    if skipped:
        notes.append("future_snapshots_skipped")
    return ReplayBaseline(
        scenario=str(scenario),
        decision_at=str(decision_at),
        used_snapshots=len(used),
        skipped_future_snapshots=len(skipped),
        missing_funnel_log=missing_funnel_log,
        notes=notes,
    )
