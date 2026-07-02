from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from minority_report import postmortem


def test_extract_json_repairs_fenced_trailing_comma_and_truncation() -> None:
    assert postmortem._extract_json('```json\n{"a": 1,}\n```') == {"a": 1}
    assert postmortem._extract_json('prefix {"a": ["x"') == {"a": ["x"]}


def test_postmortem_parse_failure_writes_fallback_daily_record_without_policy_learning() -> None:
    calls = {"beliefs": [], "issue_patterns": [], "daily_records": []}

    def fake_create(*, model, max_tokens, messages, **kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(text="not json")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    no_op = lambda *args, **kwargs: None

    with patch.object(postmortem.client.messages, "create", side_effect=fake_create), patch.object(
        postmortem,
        "credit_record",
        no_op,
    ), patch.object(
        postmortem,
        "save_raw_call",
        no_op,
    ), patch.object(
        postmortem.BrainDB,
        "generate_prompt_summary",
        return_value="",
    ), patch.object(
        postmortem.BrainDB,
        "load",
        return_value={"markets": {"KR": {"recent_days": []}}},
    ), patch.object(
        postmortem.BrainDB,
        "update_analyst",
        no_op,
    ), patch.object(
        postmortem.BrainDB,
        "update_mode_performance",
        no_op,
    ), patch.object(
        postmortem.BrainDB,
        "update_beliefs",
        side_effect=lambda *a, **k: calls["beliefs"].append((a, k)),
    ), patch.object(
        postmortem.BrainDB,
        "update_issue_pattern",
        side_effect=lambda *a, **k: calls["issue_patterns"].append((a, k)),
    ), patch.object(
        postmortem.BrainDB,
        "add_daily_record",
        side_effect=lambda *a, **k: calls["daily_records"].append((a, k)),
    ), patch.object(
        postmortem.BrainDB,
        "get_recent_selection_feedback_text",
        return_value="",
    ), patch.object(
        postmortem.BrainDB,
        "update_strategy_performance",
        no_op,
    ), patch.object(
        postmortem.BrainDB,
        "update_debate_outcome",
        no_op,
    ), patch.object(
        postmortem.BrainDB,
        "update_correction_guide",
        no_op,
    ):
        postmortem.run(
            "KR",
            "2026-05-12",
            {
                "judgments": {
                    "bull": {"stance": "CAUTIOUS", "key_reason": "risk"},
                    "bear": {"stance": "NEUTRAL", "key_reason": "flat"},
                    "neutral": {"stance": "NEUTRAL", "key_reason": "flat"},
                },
                "consensus": {"mode": "DEFENSIVE"},
            },
            {"market_change": -0.5, "pnl_pct": -0.3, "pnl_krw": -29510.1605, "win": False, "trades": 1},
            "KR digest",
            trade_log=[
                {
                    "side": "sell",
                    "ticker": "018880",
                    "strategy": "momentum",
                    "pnl": -26680.5055,
                    "pnl_pct": -12.5,
                    "reason": "stop_loss",
                }
            ],
            decision_event_log=[],
        )

    assert calls["beliefs"] == []
    assert calls["issue_patterns"] == []
    daily_record = calls["daily_records"][0][0][1]
    assert daily_record["key_lesson"]
    assert daily_record["issue_type"] == "postmortem_parse_error"
    assert daily_record["best_trade"]
    assert daily_record["worst_trade"]


def test_postmortem_parse_failure_with_execution_contamination_keeps_policy_excluded() -> None:
    calls = {"beliefs": [], "issue_patterns": [], "daily_records": []}

    def fake_create(*, model, max_tokens, messages, **kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(text="not json")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    no_op = lambda *args, **kwargs: None

    with patch.object(postmortem.client.messages, "create", side_effect=fake_create), patch.object(
        postmortem,
        "credit_record",
        no_op,
    ), patch.object(
        postmortem,
        "save_raw_call",
        no_op,
    ), patch.object(
        postmortem.BrainDB,
        "generate_prompt_summary",
        return_value="",
    ), patch.object(
        postmortem.BrainDB,
        "load",
        return_value={"markets": {"KR": {"recent_days": []}}},
    ), patch.object(
        postmortem.BrainDB,
        "update_analyst",
        no_op,
    ), patch.object(
        postmortem.BrainDB,
        "update_mode_performance",
        no_op,
    ), patch.object(
        postmortem.BrainDB,
        "update_beliefs",
        side_effect=lambda *a, **k: calls["beliefs"].append((a, k)),
    ), patch.object(
        postmortem.BrainDB,
        "update_issue_pattern",
        side_effect=lambda *a, **k: calls["issue_patterns"].append((a, k)),
    ), patch.object(
        postmortem.BrainDB,
        "add_daily_record",
        side_effect=lambda *a, **k: calls["daily_records"].append((a, k)),
    ), patch.object(
        postmortem.BrainDB,
        "get_recent_selection_feedback_text",
        return_value="",
    ), patch.object(
        postmortem.BrainDB,
        "update_strategy_performance",
        no_op,
    ), patch.object(
        postmortem.BrainDB,
        "update_debate_outcome",
        no_op,
    ), patch.object(
        postmortem.BrainDB,
        "update_correction_guide",
        no_op,
    ):
        postmortem.run(
            "KR",
            "2026-05-12",
            {
                "judgments": {
                    "bull": {"stance": "CAUTIOUS", "key_reason": "risk"},
                    "bear": {"stance": "NEUTRAL", "key_reason": "flat"},
                    "neutral": {"stance": "NEUTRAL", "key_reason": "flat"},
                },
                "consensus": {"mode": "DEFENSIVE"},
            },
            {
                "market_change": -0.5,
                "pnl_pct": -0.3,
                "pnl_krw": -29510.1605,
                "win": False,
                "trades": 1,
                "execution_contaminated": True,
            },
            "KR digest",
            trade_log=[
                {
                    "side": "sell",
                    "ticker": "018880",
                    "strategy": "momentum",
                    "pnl": -26680.5055,
                    "pnl_pct": -12.5,
                    "reason": "stop_loss",
                }
            ],
            decision_event_log=[],
        )

    assert calls["beliefs"] == []
    assert calls["issue_patterns"] == []
    daily_record = calls["daily_records"][0][0][1]
    assert daily_record["key_lesson"] == ""
    assert daily_record["issue_type"] == "execution_contaminated_postmortem_parse_error"
    assert daily_record["postmortem_fallback_note"]
    assert daily_record["execution_learning_excluded"] is True
