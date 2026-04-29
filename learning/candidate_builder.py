from __future__ import annotations

from typing import Any

from lifecycle.quality import evaluate_decision_quality
from performance.decomposition import decompose_decision_events


class BrainCandidateBuilder:
    def build_candidate(self, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not events:
            return None
        quality = evaluate_decision_quality(events)
        perf = decompose_decision_events(events)
        forward_complete = any(str(event.get("event_type")) == "FORWARD_MEASURED" for event in events)
        return {
            "decision_id": perf.decision_id,
            "market": perf.market,
            "ticker": perf.ticker,
            "data_quality": quality.grade.value,
            "forward_complete": forward_complete,
            "selection_alpha": perf.selection_alpha,
            "actual_trade_result": perf.actual_trade_result,
            "entry_delay_minutes": perf.entry_delay_minutes,
            "exit_efficiency": perf.exit_efficiency,
            "learning_allowed": quality.learning_allowed,
            "quality_reasons": list(quality.reasons),
        }

