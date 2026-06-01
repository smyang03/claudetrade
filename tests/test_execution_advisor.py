from datetime import datetime, timezone

from execution.execution_advisor import (
    ExecutionAdvisorAction,
    ExecutionAdvisorConfig,
    evaluate_filled_pathb_position,
    evaluate_open_sell_order,
    should_call_claude,
)


def test_manual_mismatch_replan_required_uses_actual_broker_entry_metrics():
    config = ExecutionAdvisorConfig.for_profile("balanced")
    decision = evaluate_filled_pathb_position(
        market="US",
        ticker="MANUAL_MISMATCH",
        path_run={
            "path_run_id": "path_manual",
            "plan": {
                "actual_entry_price": 100.00,
                "buy_zone_high": 101.42,
                "sell_target": 103.91,
                "stop_loss": 93.97,
            },
        },
        broker_position={"ticker": "MANUAL_MISMATCH", "qty": 1, "avg_price": 102.87, "current_price": 104.09},
        config=config,
    )

    assert decision.action == ExecutionAdvisorAction.REPLAN_REQUIRED
    assert decision.manual_or_mismatch is True
    assert decision.claude_candidate is True
    assert decision.metrics["entry_drift_pct"] == 2.87
    assert decision.metrics["above_zone_high_pct"] == 1.43
    assert decision.metrics["remaining_upside_pct"] == -0.173
    assert decision.metrics["entry_reward_risk"] == 0.117
    assert decision.metrics["current_reward_risk"] == -0.018


def test_manual_replan_claude_gate_respects_cooldown_and_daily_cap():
    config = ExecutionAdvisorConfig.for_profile("balanced", claude_enabled=True)
    decision = evaluate_filled_pathb_position(
        market="US",
        ticker="MANUAL_MISMATCH",
        path_run={
            "path_run_id": "path_manual",
            "plan": {
                "actual_entry_price": 100.00,
                "buy_zone_high": 101.42,
                "sell_target": 103.91,
                "stop_loss": 93.97,
            },
        },
        broker_position={"ticker": "MANUAL_MISMATCH", "qty": 1, "avg_price": 102.87, "current_price": 104.09},
        config=config,
    )

    gate = should_call_claude(decision, config=config, now=datetime(2026, 6, 2, tzinfo=timezone.utc))
    assert gate.allowed is True
    cooldown_gate = should_call_claude(
        decision,
        config=config,
        cooldown_state={gate.cooldown_key: "2026-06-02T00:00:00+00:00"},
        now=datetime(2026, 6, 2, 0, 5, tzinfo=timezone.utc),
    )
    assert cooldown_gate.allowed is False
    assert cooldown_gate.reason_code == "claude_cooldown_active"
    cap_gate = should_call_claude(decision, config=config, daily_call_count=config.max_claude_calls_per_day)
    assert cap_gate.allowed is False
    assert cap_gate.reason_code == "daily_claude_cap_reached"


def test_clean_filled_pathb_position_keeps_existing_plan():
    decision = evaluate_filled_pathb_position(
        market="US",
        ticker="HPE",
        path_run={
            "path_run_id": "path_hpe",
            "plan": {
                "actual_entry_price": 100.0,
                "buy_zone_high": 101.0,
                "sell_target": 104.0,
                "stop_loss": 98.0,
            },
        },
        broker_position={"ticker": "HPE", "qty": 10, "avg_price": 100.1, "current_price": 101.0},
        config=ExecutionAdvisorConfig.for_profile("balanced"),
    )

    assert decision.action == ExecutionAdvisorAction.KEEP_PLAN
    assert decision.claude_candidate is False


def test_conservative_profile_surfaces_borderline_hold_review_without_manual_claude_call():
    run = {
        "path_run_id": "path_dell",
        "plan": {
            "actual_entry_price": 100.0,
            "buy_zone_high": 100.5,
            "sell_target": 102.1,
            "stop_loss": 97.5,
        },
    }
    broker_position = {"ticker": "DELL", "qty": 5, "avg_price": 100.0, "current_price": 100.4}

    balanced = evaluate_filled_pathb_position(
        market="US",
        ticker="DELL",
        path_run=run,
        broker_position=broker_position,
        config=ExecutionAdvisorConfig.for_profile("balanced"),
    )
    conservative_config = ExecutionAdvisorConfig.for_profile("conservative", claude_enabled=True)
    conservative = evaluate_filled_pathb_position(
        market="US",
        ticker="DELL",
        path_run=run,
        broker_position=broker_position,
        config=conservative_config,
    )

    assert balanced.action == ExecutionAdvisorAction.KEEP_PLAN
    assert conservative.action == ExecutionAdvisorAction.HOLD_REVIEW_REQUIRED
    assert conservative.claude_candidate is True
    gate = should_call_claude(conservative, config=conservative_config)
    assert gate.allowed is False
    assert gate.reason_code == "manual_only_claude_gate"


def test_open_sell_order_profile_sensitivity_keeps_or_lowers_limit():
    order = {"ticker": "BBY", "side": "sell", "remaining_qty": 3, "limit_price": 102.8, "order_no": "sell1"}
    position = {"ticker": "BBY", "qty": 3, "avg_price": 100.0, "current_price": 101.0}

    balanced = evaluate_open_sell_order(
        market="US",
        order=order,
        broker_position=position,
        config=ExecutionAdvisorConfig.for_profile("balanced"),
    )
    conservative = evaluate_open_sell_order(
        market="US",
        order=order,
        broker_position=position,
        config=ExecutionAdvisorConfig.for_profile("conservative"),
    )

    assert balanced.action == ExecutionAdvisorAction.KEEP_LIMIT
    assert conservative.action == ExecutionAdvisorAction.LOWER_LIMIT_WITH_GUARD_CANDIDATE


def test_above_buy_zone_high_is_standalone_replan_break_condition():
    decision = evaluate_filled_pathb_position(
        market="US",
        ticker="CHASED",
        path_run={
            "path_run_id": "path_chased",
            "plan": {
                "actual_entry_price": 100.0,
                "buy_zone_high": 100.5,
                "sell_target": 110.0,
                "stop_loss": 90.0,
            },
        },
        broker_position={"ticker": "CHASED", "qty": 1, "avg_price": 101.1, "current_price": 101.2},
        config=ExecutionAdvisorConfig.for_profile("balanced"),
    )

    assert decision.action == ExecutionAdvisorAction.REPLAN_REQUIRED
    assert "broker_avg_above_buy_zone_high" in decision.metrics["plan_economics_broken_reasons"]


def test_stale_or_missing_broker_truth_fail_closed_for_advisor():
    stale = evaluate_filled_pathb_position(
        market="US",
        ticker="HPE",
        path_run={"path_run_id": "path_hpe", "plan": {}},
        broker_position={"ticker": "HPE", "qty": 1, "avg_price": 10.0, "current_price": 10.0},
        broker_truth_fresh=False,
    )
    missing = evaluate_filled_pathb_position(
        market="US",
        ticker="HPE",
        path_run={"path_run_id": "path_hpe", "plan": {}},
        broker_position=None,
    )

    assert stale.action == ExecutionAdvisorAction.WAIT_BROKER_TRUTH
    assert missing.action == ExecutionAdvisorAction.BROKER_RECONCILE_REQUIRED
