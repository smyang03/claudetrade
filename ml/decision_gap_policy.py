from __future__ import annotations

from typing import Any


LIVE_TRAINING_DATA_SOURCES: tuple[str, ...] = ("live", "live_verified_recovery")
VERIFIED_RECOVERY_DATA_SOURCES: tuple[str, ...] = ("live_verified_recovery",)

KNOWN_UNRECOVERABLE_DECISION_RANGES: tuple[dict[str, str], ...] = (
    {
        "start": "2026-04-04",
        "end": "2026-05-11",
        "status": "unrecoverable_without_original_decision_rows",
    },
)


def live_training_data_sources() -> tuple[str, ...]:
    return tuple(LIVE_TRAINING_DATA_SOURCES)


def verified_recovery_data_sources() -> tuple[str, ...]:
    return tuple(VERIFIED_RECOVERY_DATA_SOURCES)


def known_unrecoverable_decision_ranges() -> list[dict[str, str]]:
    return [dict(item) for item in KNOWN_UNRECOVERABLE_DECISION_RANGES]


def date_in_known_gap(session_date: Any) -> bool:
    value = str(session_date or "")
    return any(item["start"] <= value <= item["end"] for item in KNOWN_UNRECOVERABLE_DECISION_RANGES)
