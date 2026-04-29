from __future__ import annotations

from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent
from lifecycle.quality import evaluate_decision_quality


class DataQualityMarker:
    def __init__(self, store: EventStore | None = None):
        self.store = store or EventStore()

    def mark_decision(self, decision_id: str) -> str:
        events = self.store.events_for_decision(decision_id)
        if not events:
            return "LEGACY_UNKNOWN"
        result = evaluate_decision_quality(events)
        last = events[-1]
        self.store.append(
            LifecycleEvent(
                event_type="QUALITY_MARKED",
                market=last["market"],
                runtime_mode=last["runtime_mode"],
                session_date=last["session_date"],
                ticker=last["ticker"],
                decision_id=decision_id,
                prompt_version=last["prompt_version"],
                brain_snapshot_id=last["brain_snapshot_id"],
                data_quality=result.grade,
                payload={
                    "quality": result.grade.value,
                    "reasons": list(result.reasons),
                    "learning_allowed": result.learning_allowed,
                },
            )
        )
        return result.grade.value

