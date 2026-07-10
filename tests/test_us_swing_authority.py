from __future__ import annotations

from runtime.us_swing_authority import evaluate_swing_authority


def _policy() -> dict:
    return {
        "historical": {
            "min_oos_sessions": 200,
            "min_mean_net_pct": 0.25,
            "min_profit_factor": 1.2,
            "min_ex_top3_days_pct": 0.0,
            "micro_min_block_lcb_pct": -0.25,
            "probe_min_block_lcb_pct": 0.0,
            "min_stress_mean_net_pct": 0.0,
            "micro_required_cohorts": ["top3"],
            "probe_required_cohorts": ["top3", "top5"],
            "standard_required_cohorts": ["top3", "top5"],
        },
        "forward": {
            "micro_min_sessions": 5, "micro_min_matured": 15,
            "micro_min_mean_net_pct": 0.0, "micro_min_profit_factor": 1.0,
            "probe_min_sessions": 15, "probe_min_matured": 60,
            "probe_min_mean_net_pct": 0.25, "probe_min_profit_factor": 1.2,
            "probe_min_block_lcb_pct": 0.0, "probe_min_ex_top3_days_pct": 0.0,
            "standard_min_sessions": 40, "standard_min_matured": 150,
            "standard_min_mean_net_pct": 0.25, "standard_min_profit_factor": 1.2,
            "standard_min_block_lcb_pct": 0.0, "standard_min_ex_top3_days_pct": 0.0,
        },
        "authority_caps": {
            "shadow": {"size_multiplier": 0, "max_new_per_day": 0, "max_open_slots": 0},
            "micro": {"size_multiplier": 0.1, "max_new_per_day": 1, "max_open_slots": 1},
            "probe": {"size_multiplier": 0.25, "max_new_per_day": 3, "max_open_slots": 3},
            "standard": {"size_multiplier": 1, "max_new_per_day": 5, "max_open_slots": 15},
        },
    }


def _historical(lcb: float = 0.1) -> dict:
    metrics = {
        "worst_mean_net_pct": 0.5, "worst_profit_factor": 1.3,
        "worst_block_lcb_pct": lcb, "worst_ex_top3_days_pct": 0.1,
        "worst_stress_mean_net_pct": 0.1,
    }
    return {
        "sealed": True, "point_in_time": True, "lookahead_checks_passed": True,
        "critical_data_errors": [], "oos_sessions": 250,
        "cohorts": {"top3": metrics, "top5": metrics},
    }


def test_requested_probe_is_demoted_to_micro_when_forward_probe_sample_is_short() -> None:
    forward = {
        "sessions": 5, "matured": 15, "mean_net_pct": 0.3,
        "profit_factor": 1.3, "block_lcb_pct": 0.1,
        "ex_top3_days_pct": 0.1, "critical_data_errors": [],
    }
    result = evaluate_swing_authority(
        configured_mode="probe", historical_evidence=_historical(),
        forward_evidence=forward, policy=_policy(),
    )
    assert result.eligible_mode == "micro"
    assert result.effective_mode == "micro"
    assert result.size_multiplier == 0.1
    assert result.allowed_to_emit_orders is True


def test_historical_negative_lcb_blocks_probe_but_can_allow_capped_micro() -> None:
    forward = {
        "sessions": 20, "matured": 80, "mean_net_pct": 0.3,
        "profit_factor": 1.3, "block_lcb_pct": 0.1,
        "ex_top3_days_pct": 0.1, "critical_data_errors": [],
    }
    result = evaluate_swing_authority(
        configured_mode="probe", historical_evidence=_historical(-0.1),
        forward_evidence=forward, policy=_policy(),
    )
    assert result.eligible_mode == "micro"
    assert result.effective_mode == "micro"
    assert "historical_top3_block_lcb_failed" in result.blockers


def test_missing_evidence_stays_shadow() -> None:
    result = evaluate_swing_authority(
        configured_mode="micro", historical_evidence={}, forward_evidence={}, policy=_policy()
    )
    assert result.effective_mode == "shadow"
    assert result.allowed_to_emit_orders is False
    assert result.size_multiplier == 0.0
