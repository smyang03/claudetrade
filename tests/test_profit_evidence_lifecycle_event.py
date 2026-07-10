from __future__ import annotations

from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent


def test_profit_shadow_event_is_valid_and_does_not_change_decision_status(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    store.create_decision(
        decision_id="dec1",
        market="KR",
        runtime_mode="live",
        session_date="2026-07-10",
        ticker="005930",
        prompt_version="v1",
        brain_snapshot_id="brain1",
        status="WAIT_TIMING",
    )
    store.append(
        LifecycleEvent(
            event_type="PROFIT_EVIDENCE_SHADOW",
            market="KR",
            runtime_mode="live",
            session_date="2026-07-10",
            ticker="005930",
            decision_id="dec1",
            prompt_version="v1",
            brain_snapshot_id="brain1",
            payload={"evidence": {"model_version": "shadow-v1"}},
        )
    )
    with store.connect() as con:
        status = con.execute("SELECT status FROM v2_decisions WHERE decision_id='dec1'").fetchone()["status"]
    assert status == "WAIT_TIMING"
    assert store.count_events("PROFIT_EVIDENCE_SHADOW") == 1
