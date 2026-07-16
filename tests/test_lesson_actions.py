from pathlib import Path

from minority_report.lesson_actions import (
    AUTHORITY,
    classify_lesson_action,
    find_lesson_action,
    record_lesson_actions,
)
from telegram_reporter import _lesson_action_block


def _ops(*, conversion=None, conversion_n=0, watch=None, watch_n=0):
    return {
        "metrics": {
            "trade_ready_signal_conversion": {
                "value": conversion,
                "sample": conversion_n,
                "threshold": 10.0,
                "breached": conversion is not None and conversion < 10.0,
            },
            "watch_only_missed_runup_ratio": {
                "value": watch,
                "sample": watch_n,
                "threshold": 30.0,
                "breached": watch is not None and watch > 30.0,
            },
        }
    }


def _classify(**overrides):
    values = {
        "market": "US",
        "session_date": "2026-07-15",
        "postmortem": {},
        "actual_result": {"trades": 0, "pnl_krw": 0},
        "trade_log": [],
        "ops_review_snapshot": {},
        "runtime_safety_summary": {},
    }
    values.update(overrides)
    return classify_lesson_action(**values)


def test_isolated_strategy_generic_exit_is_exit_owner_action() -> None:
    action = _classify(
        actual_result={"trades": 1, "pnl_krw": -232},
        trade_log=[
            {
                "ticker": "SCHG",
                "source_strategy": "us_schg_bil_trend_v1",
                "reason": "intraday_review_sell",
                "exit_owner": "",
            }
        ],
    )

    assert action["root_cause"] == "EXIT_OWNER"
    assert action["status"] == "ACTION_REQUIRED"
    assert action["authority"] == AUTHORITY
    assert action["automatic_enforcement"] is False
    violations = action["evidence"]["isolated_exit_owner_violations"]
    assert violations == [
        {
            "ticker": "SCHG",
            "source_strategy": "us_schg_bil_trend_v1",
            "exit_owner": "",
            "reason": "intraday_review_sell",
        }
    ]


def test_isolated_strategy_hard_stop_is_not_owner_violation() -> None:
    action = _classify(
        actual_result={"trades": 1, "pnl_krw": -500},
        trade_log=[
            {
                "ticker": "SCHG",
                "source_strategy": "us_schg_bil_trend_v1",
                "reason": "hard_stop",
                "exit_owner": "system_hard_rule",
            }
        ],
    )

    assert action["root_cause"] == "EXIT_POLICY"
    assert action["evidence"]["isolated_exit_owner_violations"] == []


def test_entry_pipeline_action_does_not_authorize_gate_loosen() -> None:
    action = _classify(
        market="KR",
        actual_result={"trades": 0, "pnl_krw": 0},
        ops_review_snapshot=_ops(watch=65.3, watch_n=251),
    )

    assert action["root_cause"] == "ENTRY_PIPELINE"
    assert action["status"] == "NEEDS_REVIEW"
    assert "do not loosen live gates" in action["recommended_action"]
    assert action["validation_contract"]["min_forward_samples"] == 15
    assert action["validation_contract"]["automatic_enforcement"] is False


def test_execution_contamination_has_priority_over_pipeline_metrics() -> None:
    action = _classify(
        actual_result={
            "trades": 0,
            "pnl_krw": 0,
            "execution_contaminated": True,
            "execution_issues": ["broker_truth_mismatch"],
        },
        ops_review_snapshot=_ops(conversion=2.0, conversion_n=30, watch=50.0, watch_n=100),
    )

    assert action["root_cause"] == "EXECUTION"
    assert action["status"] == "ACTION_REQUIRED"


def test_registry_upsert_is_idempotent_and_preserves_operator_fields(tmp_path: Path) -> None:
    path = tmp_path / "lesson_action_registry.json"
    first = _classify()
    first["operator_status"] = "ACKNOWLEDGED"
    first["operator_notes"] = "owner reviewed"
    record_lesson_actions([first], path=path)

    replacement = _classify(postmortem={"key_lesson": "updated narrative"})
    registry = record_lesson_actions([replacement, replacement], path=path)

    assert registry["summary"]["actions"] == 1
    assert len(registry["patterns"]) == 1
    stored = find_lesson_action("US", "2026-07-15", path=path)
    assert stored["operator_status"] == "ACKNOWLEDGED"
    assert stored["operator_notes"] == "owner reviewed"
    assert stored["postmortem_lesson"] == "updated narrative"


def test_pattern_becomes_review_due_but_never_auto_enforces(tmp_path: Path) -> None:
    path = tmp_path / "lesson_action_registry.json"
    owner = _classify(
        actual_result={"trades": 1, "pnl_krw": -100},
        trade_log=[
            {
                "ticker": "SCHG",
                "source_strategy": "us_schg_bil_trend_v1",
                "reason": "intraday_review_sell",
            }
        ],
    )
    later = []
    for day in range(16, 21):
        later.append(_classify(session_date=f"2026-07-{day:02d}"))
    registry = record_lesson_actions([owner, *later], path=path)

    pattern = registry["patterns"]["US:EXIT_OWNER:us_schg_bil_trend_v1"]
    assert pattern["forward_observation"]["status"] == "REVIEW_DUE"
    assert pattern["forward_observation"]["market_sessions_since_last_occurrence"] == 5
    assert registry["automatic_enforcement"] is False


def test_telegram_action_block_labels_no_trade_authority() -> None:
    action = _classify(
        market="KR",
        ops_review_snapshot=_ops(watch=65.3, watch_n=251),
    )
    text = _lesson_action_block(action)

    assert "매매권한 없음" in text
    assert "ENTRY_PIPELINE" in text
    assert "bounded_rejudge_incremental_net_pct" in text
