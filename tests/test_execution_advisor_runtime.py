from datetime import datetime, timezone
import json

from lifecycle.event_store import EventStore
from lifecycle.models import DataQuality, LifecycleEvent, LifecycleEventType
from runtime.broker_truth_snapshot import BrokerTruthSnapshot
from runtime.execution_advisor_runtime import ExecutionAdvisorRuntime


def _fresh_snapshot(position, *, open_orders=None, today_fills=None):
    return {
        "runtime_mode": "live",
        "schema_version": 1,
        "markets": {
            "US": {
                "missing": False,
                "stale": False,
                "last_success_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "last_attempt_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "ttl_sec": 120,
                "error": "",
                "positions": [position] if position else [],
                "open_orders": open_orders or [],
                "today_fills": today_fills or [],
                "account_summary": {},
                "source": "test",
            },
            "KR": {
                "missing": True,
                "stale": True,
                "last_success_at": "",
                "last_attempt_at": "",
                "ttl_sec": 120,
                "error": "",
                "positions": [],
                "open_orders": [],
                "today_fills": [],
                "account_summary": {},
                "source": "test",
            },
        },
    }


def _store_with_manual_path(tmp_path):
    store = EventStore(tmp_path / "events.db")
    store.create_decision(
        decision_id="dec_manual",
        market="US",
        runtime_mode="live",
        session_date="2026-06-02",
        ticker="MANUAL_MISMATCH",
        prompt_version="test",
        brain_snapshot_id="brain",
        status="FILLED",
    )
    store.create_path_run(
        path_run_id="path_manual",
        decision_id="dec_manual",
        path_type="claude_price",
        market="US",
        runtime_mode="live",
        session_date="2026-06-02",
        ticker="MANUAL_MISMATCH",
        status="FILLED",
        plan={
            "actual_entry_price": 100.00,
            "buy_zone_high": 101.42,
            "sell_target": 103.91,
            "stop_loss": 93.97,
        },
    )
    return store


def test_execution_advisor_event_does_not_overwrite_decision_status(tmp_path):
    store = EventStore(tmp_path / "events.db")
    store.create_decision(
        decision_id="dec_hpe",
        market="US",
        runtime_mode="live",
        session_date="2026-06-02",
        ticker="HPE",
        prompt_version="test",
        brain_snapshot_id="brain",
        status="FILLED",
    )
    store.append(
        LifecycleEvent(
            event_type=LifecycleEventType.EXECUTION_ADVISOR_DECISION,
            market="US",
            runtime_mode="live",
            session_date="2026-06-02",
            ticker="HPE",
            decision_id="dec_hpe",
            prompt_version="execution_advisor_v1",
            brain_snapshot_id="execution_advisor",
            reason_code="plan_economics_intact",
            data_quality=DataQuality.CLEAN,
            payload={"action": "KEEP_PLAN"},
        )
    )

    decision = store.find_decision(market="US", runtime_mode="live", session_date="2026-06-02", ticker="HPE")
    assert decision["status"] == "FILLED"


def test_runtime_scan_is_read_only_and_appends_shadow_audit_event(tmp_path, monkeypatch):
    monkeypatch.setenv("EXEC_ADVISOR_ENABLED", "true")
    monkeypatch.setenv("EXEC_ADVISOR_CLAUDE_ENABLED", "false")
    store = _store_with_manual_path(tmp_path)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            _fresh_snapshot({"ticker": "MANUAL_MISMATCH", "qty": 1, "avg_price": 102.87, "current_price": 104.09}),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    runtime = ExecutionAdvisorRuntime(
        runtime_mode="live",
        event_store=store,
        broker_truth=BrokerTruthSnapshot(runtime_mode="live", path=snapshot_path),
        state_path=tmp_path / "state.json",
        append_events=True,
        enabled=True,
    )

    result = runtime.scan_market("US", force=True)

    assert result["ok"] is True
    assert result["events_appended"] == 1
    assert result["decisions"][0]["action"] == "REPLAN_REQUIRED"
    events = store.events_for_decision("dec_manual")
    advisor_events = [evt for evt in events if evt["event_type"] == "EXECUTION_ADVISOR_DECISION"]
    assert len(advisor_events) == 1
    assert advisor_events[0]["payload"]["shadow_only"] is True
    assert store.find_decision(market="US", runtime_mode="live", session_date="2026-06-02", ticker="MANUAL_MISMATCH")["status"] == "FILLED"


def test_runtime_dedupes_repeated_identical_audit_events(tmp_path, monkeypatch):
    monkeypatch.setenv("EXEC_ADVISOR_ENABLED", "true")
    monkeypatch.setenv("EXEC_ADVISOR_CLAUDE_ENABLED", "false")
    monkeypatch.setenv("EXEC_ADVISOR_EVENT_COOLDOWN_MINUTES", "15")
    store = _store_with_manual_path(tmp_path)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            _fresh_snapshot({"ticker": "MANUAL_MISMATCH", "qty": 1, "avg_price": 102.87, "current_price": 104.09}),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    runtime = ExecutionAdvisorRuntime(
        runtime_mode="live",
        event_store=store,
        broker_truth=BrokerTruthSnapshot(runtime_mode="live", path=snapshot_path),
        state_path=tmp_path / "state.json",
        append_events=True,
        enabled=True,
    )

    first = runtime.scan_market("US", force=True)
    second = runtime.scan_market("US", force=True)

    assert first["events_appended"] == 1
    assert second["events_appended"] == 0
    advisor_events = [
        evt for evt in store.events_for_decision("dec_manual")
        if evt["event_type"] == "EXECUTION_ADVISOR_DECISION"
    ]
    assert len(advisor_events) == 1


def test_runtime_disabled_returns_without_audit_or_claude(tmp_path, monkeypatch):
    monkeypatch.setenv("EXEC_ADVISOR_ENABLED", "false")
    store = _store_with_manual_path(tmp_path)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            _fresh_snapshot({"ticker": "MANUAL_MISMATCH", "qty": 1, "avg_price": 102.87, "current_price": 104.09}),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []
    runtime = ExecutionAdvisorRuntime(
        runtime_mode="live",
        event_store=store,
        broker_truth=BrokerTruthSnapshot(runtime_mode="live", path=snapshot_path),
        state_path=tmp_path / "state.json",
        claude_client=lambda payload: calls.append(payload),
        append_events=True,
        enabled=False,
    )

    result = runtime.scan_market("US")

    assert result["skipped"] is True
    assert result["reason"] == "execution_advisor_disabled"
    assert calls == []
    assert store.events_for_decision("dec_manual") == []


def test_runtime_manual_claude_skeleton_uses_fake_client_and_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("EXEC_ADVISOR_ENABLED", "true")
    monkeypatch.setenv("EXEC_ADVISOR_CLAUDE_ENABLED", "true")
    monkeypatch.setenv("EXEC_ADVISOR_CLAUDE_COOLDOWN_MINUTES", "15")
    store = _store_with_manual_path(tmp_path)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            _fresh_snapshot({"ticker": "MANUAL_MISMATCH", "qty": 1, "avg_price": 102.87, "current_price": 104.09}),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_client(payload):
        calls.append(payload)
        return {"decision": "KEEP_WITH_REPLAN", "source": "fake"}

    runtime = ExecutionAdvisorRuntime(
        runtime_mode="live",
        event_store=store,
        broker_truth=BrokerTruthSnapshot(runtime_mode="live", path=snapshot_path),
        state_path=tmp_path / "state.json",
        claude_client=fake_client,
        append_events=True,
        enabled=True,
    )

    first = runtime.scan_market("US", force=True)
    second = runtime.scan_market("US", force=True)

    assert first["decisions"][0]["claude_candidate"] is True
    assert len(calls) == 1
    assert second["ok"] is True
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["claude_calls"] == 1


def test_runtime_claude_client_error_stays_read_only_and_records_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("EXEC_ADVISOR_ENABLED", "true")
    monkeypatch.setenv("EXEC_ADVISOR_CLAUDE_ENABLED", "true")
    store = _store_with_manual_path(tmp_path)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            _fresh_snapshot({"ticker": "MANUAL_MISMATCH", "qty": 1, "avg_price": 102.87, "current_price": 104.09}),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def failing_client(payload):
        raise RuntimeError("synthetic")

    runtime = ExecutionAdvisorRuntime(
        runtime_mode="live",
        event_store=store,
        broker_truth=BrokerTruthSnapshot(runtime_mode="live", path=snapshot_path),
        state_path=tmp_path / "state.json",
        claude_client=failing_client,
        append_events=True,
        enabled=True,
    )

    result = runtime.scan_market("US", force=True)

    assert result["ok"] is True
    assert result["claude_calls"] == 0
    event = [
        evt for evt in store.events_for_decision("dec_manual")
        if evt["event_type"] == "EXECUTION_ADVISOR_DECISION"
    ][0]
    assert event["payload"]["claude_gate"]["reason_code"] == "claude_client_error:RuntimeError"
