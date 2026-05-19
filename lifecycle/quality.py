from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lifecycle.models import DataQuality


@dataclass(frozen=True)
class QualityResult:
    grade: DataQuality
    reasons: tuple[str, ...]
    learning_allowed: bool


def evaluate_decision_quality(events: list[dict[str, Any]]) -> QualityResult:
    event_types = [str(event.get("event_type", "") or "") for event in events]
    reasons: list[str] = []

    if "FILLED" in event_types and "ORDER_SENT" not in event_types:
        reasons.append("FILLED_WITHOUT_ORDER_SENT")
    if "CLOSED" in event_types and not ({"FILLED", "PARTIAL_FILLED"} & set(event_types)):
        reasons.append("CLOSED_WITHOUT_FILL")
    if reasons:
        return QualityResult(DataQuality.DIRTY, tuple(reasons), learning_allowed=False)

    suspect = []
    if "ORDER_UNKNOWN" in event_types and "ORDER_RECOVERED" not in event_types:
        suspect.append("ORDER_UNKNOWN_UNRESOLVED")
    if "FORWARD_PENDING_DATA" in event_types and not forward_measurement_complete(events):
        suspect.append("FORWARD_PENDING_DATA")
    for event in events:
        if event.get("event_type") == "CLOSED":
            payload = event.get("payload") or {}
            if str(payload.get("close_reason") or event.get("reason_code") or "") == "CLOSED_BROKER_SYNC":
                suspect.append("CLOSED_BROKER_SYNC")
                break
    if suspect:
        return QualityResult(DataQuality.SUSPECT, tuple(suspect), learning_allowed=False)

    if not events:
        return QualityResult(DataQuality.LEGACY_UNKNOWN, ("NO_EVENTS",), learning_allowed=False)

    if "FORWARD_MEASURED" not in event_types:
        return QualityResult(DataQuality.LEGACY_UNKNOWN, ("FORWARD_NOT_MEASURED",), learning_allowed=False)

    return QualityResult(DataQuality.CLEAN, tuple(), learning_allowed=True)


def forward_measurement_complete(events: list[dict[str, Any]]) -> bool:
    due: set[int] = set()
    measured: set[int] = set()
    has_pending = False
    has_measured = False
    for event in events:
        if str(event.get("event_type") or "") != "FORWARD_PENDING_DATA":
            continue
        has_pending = True
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        due.update(_parse_horizon_values(payload.get("due_horizons") or []))
    for event in events:
        if str(event.get("event_type") or "") != "FORWARD_MEASURED":
            continue
        has_measured = True
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if payload.get("complete") is True:
            return True
        if not due:
            due.update(_parse_horizon_values(payload.get("due_horizons") or []))
        measured.update(_parse_horizon_values(payload.get("all_measured_horizons") or payload.get("measured_horizons") or []))
    if not has_measured:
        return False
    if not has_pending and not due:
        return True
    if not due:
        due = {1, 3, 5}
    return bool(measured) and due.issubset(measured)


def _parse_horizon_values(values: Any) -> set[int]:
    parsed: set[int] = set()
    if isinstance(values, (str, int)):
        values = [values]
    for item in values or []:
        text = str(item).strip().lower().removesuffix("d")
        try:
            parsed.add(int(text))
        except ValueError:
            continue
    return parsed


def live_clean_learning_allowed(*, runtime_mode: str, quality: DataQuality | str, forward_complete: bool) -> bool:
    quality_value = quality.value if isinstance(quality, DataQuality) else str(quality)
    return runtime_mode == "live" and quality_value == DataQuality.CLEAN.value and bool(forward_complete)

