from pathlib import Path

from interface.v2_telegram import _format_health


def _summary() -> dict:
    return {
        "broker_truth": {"markets": {}},
        "system_health": {
            "strategy_trackers": {
                "core_shadow": {
                    "status": "healthy",
                    "stale": False,
                    "last_success_at": "2026-07-15T20:00:00+09:00",
                },
                "paired_exit": {
                    "clock_status": "RUNNING",
                    "gate_sample_total": 3,
                    "paired_eligible_total": 3,
                    "paired_eligible_7d": 1,
                },
                "us_swing": {
                    "authority": {"configured_mode": "micro", "effective_mode": "micro"},
                    "research_authority": {"configured_mode": "micro", "effective_mode": "shadow"},
                    "execution_authority": {
                        "eligible_mode": "micro_operator_trial",
                        "effective_mode": "micro",
                    },
                    "execution": {
                        "max_order_krw": 300000,
                        "operator_override_applied": True,
                        "status": "EVALUATED",
                        "reason": "",
                        "generated_at": "2026-07-15T22:40:00+09:00",
                    },
                    "execution_status_stale": False,
                    "active_execution_shadow": {
                        "state": "ACTIVE_UNMATURED",
                        "rows": [{
                            "ticker": "SMCI",
                            "observed_sessions": 4,
                            "max_hold_sessions": 5,
                            "expected_maturity_session": "2026-07-16",
                        }],
                    },
                    "live_configured_mode": "micro",
                    "config_matches_runtime": True,
                    "stale": False,
                },
                "profit_strategies": {
                    "authority_mode": "micro",
                    "kill_switch": "false",
                    "config_matches_runtime": True,
                    "enforced_ids": ["US_SCHG_BIL_TREND_V1", "KR_FACTOR_TREND_V1"],
                    "disabled_shadow_ids": ["US_CONSENSUS_3D_V1", "KR_US_SECTOR_PULSE_3D_V0"],
                    "order_unknown_blocked": True,
                    "order_unknown_markets": ["US"],
                    "order_unknown_count": 1,
                    "US": {"signal_count": 2},
                    "KR": {"signal_count": 1},
                    "core_live_manifests": {
                        "US": {"valid": True},
                        "KR": {"valid": True},
                    },
                    "core_analyst_entry_policy": {
                        "policy": "isolated",
                        "ok": True,
                        "last_direction_block_observed": True,
                        "last_applied": True,
                    },
                },
                "kr_exit_policy": {
                    "env_live": "SPLIT_RUNNER_V1",
                    "start_config": "SPLIT_RUNNER_V1",
                    "runtime_snapshot": "SPLIT_RUNNER_V1",
                    "ok": True,
                },
                "runtime_handoff": {
                    "present": True,
                    "anchor_count": 2,
                    "feature_count": 2,
                    "filter_dropped_total": 442,
                    "written_at": "2026-07-16T12:00:00+09:00",
                },
            }
        },
    }


def test_telegram_health_shows_enforced_shadow_and_fail_closed_state() -> None:
    text = _format_health(_summary())

    assert "US_SCHG_BIL_TREND_V1" in text
    assert "KR_FACTOR_TREND_V1" in text
    assert "US_CONSENSUS_3D_V1" in text
    assert "KR_US_SECTOR_PULSE_3D_V0" in text
    assert "전략 ORDER_UNKNOWN 차단" in text
    assert "markets=US count=1" in text
    assert "US swing: research=micro→shadow execution=micro_operator_trial" in text
    assert "budget=300,000KRW" in text
    assert "core live manifest" in text
    assert "US swing forward: state=ACTIVE_UNMATURED ticker=SMCI sessions=4/5 maturity=2026-07-16" in text
    assert "core analyst entry: policy=isolated" in text
    assert "gross_cap=enforced" in text
    assert "runtime handoff:" in text
    assert "filtered=442" in text


def test_dashboard_has_strategy_lane_status_surface() -> None:
    source = Path("dashboard/dashboard_server.py").read_text(encoding="utf-8")

    assert 'id="strategy-lane-status"' in source
    assert "profit.order_unknown_blocked" in source
    assert "profit.enforced_ids" in source
    assert "swingExecutionAuthority.eligible_mode" in source
    assert "coreManifests" in source
    assert "swingActive.state" in source
    assert "coreEntryPolicy.policy" in source
    assert "runtimeHandoff.filter_dropped_total" in source
