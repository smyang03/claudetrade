from __future__ import annotations

from pathlib import Path

from runtime.rehearsal.context import create_rehearsal_context
from runtime.rehearsal.fixtures import all_scenarios, fixture_for_scenario
from runtime.rehearsal.scenarios import run_rehearsal_scenario


def test_ops_rehearsal_scenarios_run_in_sandbox(tmp_path: Path) -> None:
    for scenario in all_scenarios():
        ctx = create_rehearsal_context(scenario=scenario, runtime_root=tmp_path / scenario)
        result = run_rehearsal_scenario(ctx, fixture_for_scenario(scenario))
        assert result["ok"] is True
        assert result["runtime_mode"] == "live"
        assert result["is_paper"] is False
        assert Path(result["sandbox_root"]).resolve() == ctx.sandbox_root
        assert result["network_guard_calls"] == []
        assert result["network_guard"]["call_count"] == 0
        assert result["write_guard"]["call_count"] >= 0
        assert len(result["write_guard"]["calls_sample"]) <= 25
        assert ctx.report_path.exists()
        for intent in result["order_intents"]:
            assert intent["order_send"] is False
            assert intent["broker_call"] is False
            assert intent["claude_call"] is False
            assert intent["rehearsal"] is True
            assert intent["learning_excluded"] is True
            assert intent["do_not_learn"] is True


def test_fail_closed_and_order_unknown_do_not_create_order_intents(tmp_path: Path) -> None:
    for scenario in ("broker_truth_fail_closed", "order_unknown_reconcile"):
        ctx = create_rehearsal_context(scenario=scenario, runtime_root=tmp_path / scenario)
        result = run_rehearsal_scenario(ctx, fixture_for_scenario(scenario))
        assert result["order_intents"] == []
        reasons = {event.get("reason") for event in result["events"]}
        assert reasons & {"BLOCKED_BROKER_TRUTH", "ORDER_UNKNOWN_UNRESOLVED"}
