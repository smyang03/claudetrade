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
                },
                "kr_exit_policy": {
                    "env_live": "SPLIT_RUNNER_V1",
                    "start_config": "SPLIT_RUNNER_V1",
                    "runtime_snapshot": "SPLIT_RUNNER_V1",
                    "ok": True,
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


def test_dashboard_has_strategy_lane_status_surface() -> None:
    source = Path("dashboard/dashboard_server.py").read_text(encoding="utf-8")

    assert 'id="strategy-lane-status"' in source
    assert "profit.order_unknown_blocked" in source
    assert "profit.enforced_ids" in source
    assert "swingExecutionAuthority.eligible_mode" in source
    assert "coreManifests" in source
