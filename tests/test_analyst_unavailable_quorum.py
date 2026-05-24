from unittest.mock import patch

from claude_memory import brain as brain_module
from minority_report import analysts as analysts_module
from minority_report import consensus as consensus_module
from minority_report import postmortem as postmortem_module


def _judgment(stance, confidence=0.7, permission="selective", size=None, reason="ok"):
    payload = {
        "stance": stance,
        "confidence": confidence,
        "key_reason": reason,
        "new_buy_permission": permission,
        "max_gross_exposure_pct": 80,
    }
    if size is not None:
        payload["suggested_size_pct"] = size
    return payload


def _unavailable(role="bear"):
    return {
        "stance": "UNAVAILABLE",
        "available": False,
        "analyst_unavailable": True,
        "status": "unavailable",
        "failure_stage": "r1",
        "key_reason": f"analyst_unavailable:{role}",
        "confidence": 0.0,
        "new_buy_permission": "block",
        "max_gross_exposure_pct": 0,
    }


def test_fallback_result_marks_unavailable_not_neutral():
    result = analysts_module._fallback_result(RuntimeError("provider raw payload"))

    assert result["stance"] == "UNAVAILABLE"
    assert result["available"] is False
    assert result["analyst_unavailable"] is True
    assert result["new_buy_permission"] == "block"
    assert "provider raw payload" not in result["key_reason"]


def test_claude_unavailable_stance_is_sanitized_to_neutral():
    result = analysts_module._sanitize_analyst_result(
        {
            "stance": "UNAVAILABLE",
            "confidence": 0.8,
            "key_reason": "model tried to emit system marker",
        },
        "bull",
    )

    assert result["stance"] == "NEUTRAL"
    assert "analyst_unavailable" not in result


def test_partial_consensus_excludes_unavailable_bear_from_scoring_and_votes():
    judgments = {
        "bull": _judgment("MILD_BULL", permission="allow", size=50),
        "bear": _unavailable("bear"),
        "neutral": _judgment("NEUTRAL", permission="selective", size=30),
    }

    with patch.object(consensus_module, "_get_weights", return_value={"bull": 1.0, "bear": 1.0, "neutral": 1.0}):
        result = consensus_module.build_consensus(judgments, market="US")

    assert result["consensus_quality"] == "partial_consensus"
    assert result["quorum_met"] is True
    assert result["available_analyst_roles"] == ["bull", "neutral"]
    assert result["unavailable_analyst_roles"] == ["bear"]
    assert result["new_buy_permission"] == "selective"
    assert result["new_buy_permission_votes_by_role"] == {"bull": "allow", "neutral": "selective"}
    assert result["size"] > 0
    assert result["size"] < result["size_before_partial_consensus_penalty"]
    assert result["mode"] != "HALT"


def test_single_available_analyst_blocks_entries_without_halt():
    judgments = {
        "bull": _judgment("MILD_BULL", permission="allow", size=50),
        "bear": _unavailable("bear"),
        "neutral": _unavailable("neutral"),
    }

    result = consensus_module.build_consensus(judgments, market="KR")

    assert result["consensus_quality"] == "partial_consensus_only"
    assert result["quorum_met"] is False
    assert result["mode"] == "NEUTRAL"
    assert result["mode"] != "HALT"
    assert result["size"] == 0
    assert result["new_buy_permission"] == "block"


def test_all_unavailable_fail_closed_blocks_entries_without_halt():
    judgments = {
        "bull": _unavailable("bull"),
        "bear": _unavailable("bear"),
        "neutral": _unavailable("neutral"),
    }

    result = consensus_module.build_consensus(judgments, market="KR")

    assert result["consensus_quality"] == "fail_closed"
    assert result["analyst_outage_fail_closed"] is True
    assert result["mode"] == "NEUTRAL"
    assert result["mode"] != "HALT"
    assert result["size"] == 0
    assert result["new_buy_permission"] == "block"


def test_unavailable_bear_cannot_trigger_minority_rule():
    judgments = {
        "bull": _judgment("MODERATE_BULL", permission="allow", size=70),
        "bear": {**_unavailable("bear"), "key_reason": "crash fraud halt", "confidence": 0.99},
        "neutral": _judgment("MILD_BULL", permission="allow", size=50),
    }

    with patch.object(consensus_module, "_get_weights", return_value={"bull": 1.0, "bear": 1.0, "neutral": 1.0}):
        result = consensus_module.build_consensus(judgments, market="US")

    assert result["minority_triggered"] is False
    assert result["mode"] != "DEFENSIVE"


def test_kr_minority_rule_keeps_existing_intraday_drop_trigger():
    judgments = {
        "bull": _judgment("AGGRESSIVE", permission="allow", size=90),
        "bear": _judgment("MILD_BEAR", confidence=0.99, reason="장중 -5% 급락 위험"),
        "neutral": _judgment("AGGRESSIVE", permission="allow", size=90),
    }

    with patch.object(consensus_module, "_get_weights", return_value={"bull": 1.0, "bear": 1.0, "neutral": 1.0}):
        result = consensus_module.build_consensus(judgments, market="KR")

    assert result["minority_triggered"] is True
    assert result["mode"] == "DEFENSIVE"


def test_unanimous_override_ignores_unavailable_role():
    judgments = {
        "bull": _judgment("NEUTRAL"),
        "bear": _unavailable("bear"),
        "neutral": _judgment("NEUTRAL"),
    }
    consensus = {"mode": "MILD_BULL", "size": 40}

    result = consensus_module.apply_unanimous_override(judgments, consensus)

    assert result["unanimous_override_applied"] is False
    assert result["unanimous_direction"] is None
    assert result["mode"] == "MILD_BULL"


def test_get_three_judgments_excludes_unavailable_from_r2_peers():
    r1_by_role = {
        "bull": _judgment("MILD_BULL"),
        "bear": _unavailable("bear"),
        "neutral": _judgment("NEUTRAL"),
    }
    r2_calls = []

    def fake_r1(analyst_type, *args, **kwargs):
        return dict(r1_by_role[analyst_type])

    def fake_r2(analyst_type, my_r1, others, *args, **kwargs):
        r2_calls.append((analyst_type, sorted(others.keys())))
        return {**my_r1, "changed": False}

    with patch.object(analysts_module, "build_active_lesson_context", return_value={"section": "", "metadata": {}}), \
         patch.object(analysts_module, "call_analyst", side_effect=fake_r1), \
         patch.object(analysts_module, "call_analyst_debate", side_effect=fake_r2), \
         patch("claude_memory.brain.generate_analyst_summary", return_value=""), \
         patch("claude_memory.brain.get_debate_summary", return_value=""), \
         patch("claude_memory.brain.save_debate_result", return_value=None):
        result = analysts_module.get_three_judgments("digest", "brain", "correction", delay=0, market="US")

    assert r2_calls == [("bull", ["neutral"]), ("neutral", ["bull"])]
    assert result["bear"]["stance"] == "UNAVAILABLE"
    assert result["bear"]["debate_skipped"] is True
    assert result["_debate"]["unavailable_roles"] == ["bear"]


def test_get_three_judgments_skips_r2_when_only_one_analyst_available():
    r1_by_role = {
        "bull": _judgment("MILD_BULL"),
        "bear": _unavailable("bear"),
        "neutral": _unavailable("neutral"),
    }

    def fake_r1(analyst_type, *args, **kwargs):
        return dict(r1_by_role[analyst_type])

    with patch.object(analysts_module, "build_active_lesson_context", return_value={"section": "", "metadata": {}}), \
         patch.object(analysts_module, "call_analyst", side_effect=fake_r1), \
         patch.object(analysts_module, "call_analyst_debate") as debate_mock, \
         patch("claude_memory.brain.generate_analyst_summary", return_value=""), \
         patch("claude_memory.brain.get_debate_summary", return_value=""), \
         patch("claude_memory.brain.save_debate_result", return_value=None):
        result = analysts_module.get_three_judgments("digest", "brain", "correction", delay=0, market="US")

    debate_mock.assert_not_called()
    assert result["bull"]["debate_skipped"] is True
    assert result["bull"]["debate_skip_reason"] == "insufficient_available_peers"
    assert sorted(result["_debate"]["unavailable_roles"]) == ["bear", "neutral"]


def test_get_three_judgments_skips_all_r2_when_all_analysts_unavailable():
    r1_by_role = {
        "bull": _unavailable("bull"),
        "bear": _unavailable("bear"),
        "neutral": _unavailable("neutral"),
    }

    def fake_r1(analyst_type, *args, **kwargs):
        return dict(r1_by_role[analyst_type])

    with patch.object(analysts_module, "build_active_lesson_context", return_value={"section": "", "metadata": {}}), \
         patch.object(analysts_module, "call_analyst", side_effect=fake_r1), \
         patch.object(analysts_module, "call_analyst_debate") as debate_mock, \
         patch("claude_memory.brain.generate_analyst_summary", return_value=""), \
         patch("claude_memory.brain.get_debate_summary", return_value=""), \
         patch("claude_memory.brain.save_debate_result", return_value=None):
        result = analysts_module.get_three_judgments("digest", "brain", "correction", delay=0, market="US")

    debate_mock.assert_not_called()
    assert sorted(result["_debate"]["unavailable_roles"]) == ["bear", "bull", "neutral"]


def test_postmortem_does_not_update_brain_performance_for_unavailable_analyst():
    today_judgment = {
        "judgments": {
            "bull": _judgment("MILD_BULL"),
            "bear": _unavailable("bear"),
            "neutral": _judgment("NEUTRAL"),
        },
        "consensus": {"mode": "NEUTRAL", "size": 0},
    }

    with patch.object(postmortem_module.BrainDB, "generate_prompt_summary", return_value=""), \
         patch.object(postmortem_module, "_recent_selection_feedback_section", return_value=""), \
         patch.object(postmortem_module.client.messages, "create", side_effect=RuntimeError("api down")), \
         patch.object(postmortem_module.BrainDB, "load", return_value={"markets": {"KR": {"recent_days": []}}}), \
         patch.object(postmortem_module.BrainDB, "update_analyst") as update_analyst, \
         patch.object(postmortem_module.BrainDB, "update_mode_performance"), \
         patch.object(postmortem_module.BrainDB, "add_daily_record") as add_daily_record, \
         patch.object(postmortem_module.BrainDB, "get_recent_selection_feedback_text", return_value=""), \
         patch.object(postmortem_module.BrainDB, "update_debate_outcome"), \
         patch.object(postmortem_module.BrainDB, "update_beliefs"), \
         patch.object(postmortem_module.BrainDB, "update_issue_pattern"), \
         patch.object(postmortem_module.BrainDB, "update_correction_guide"), \
         patch.object(postmortem_module.BrainDB, "update_strategy_performance"):
        pm = postmortem_module.run(
            "KR",
            "2026-05-22",
            today_judgment,
            {"market_change": 0.2, "pnl_pct": 0.0, "win": False},
            "digest",
            trade_log=[],
            decision_event_log=[],
        )

    updated_roles = [call.args[1] for call in update_analyst.call_args_list]
    assert updated_roles == ["bull", "neutral"]
    assert pm["bear_result"] == "UNAVAILABLE"
    assert pm["bear_why"] == "analyst unavailable during judgment"
    daily_payload = add_daily_record.call_args.args[1]
    assert daily_payload["analyst_available"]["bear"] is False
    assert daily_payload["analyst_unavailable_roles"] == ["bear"]


def test_brain_debate_history_preserves_unavailable_without_reusing_as_stance():
    r1 = {
        "bull": _judgment("MILD_BULL"),
        "bear": _unavailable("bear"),
        "neutral": _judgment("NEUTRAL"),
    }
    r2 = {
        "bull": _judgment("MILD_BULL"),
        "bear": {**_unavailable("bear"), "debate_skipped": True},
        "neutral": _judgment("NEUTRAL"),
    }
    brain = {"markets": {"KR": {"debate_history": []}}}

    with patch.object(brain_module, "load", return_value=brain), \
         patch.object(brain_module, "save"):
        brain_module.save_debate_result("KR", "2026-05-22", r1, r2)

    entry = brain["markets"]["KR"]["debate_history"][0]
    entry["outcome"] = "wrong"
    assert entry["unavailable_roles"] == ["bear"]
    assert entry["r1"]["bear"]["analyst_unavailable"] is True

    with patch.object(brain_module, "load", return_value=brain):
        summary = brain_module.get_debate_summary("KR")

    assert "outages=bear" in summary
    assert "bear=UNAVAILABLE" not in summary
    assert "bull=MILD_BULL" in summary


def test_brain_debate_save_ignores_changed_flag_when_stance_is_unchanged():
    r1 = {
        "bull": _judgment("MILD_BULL"),
        "bear": _judgment("NEUTRAL"),
        "neutral": _judgment("NEUTRAL"),
    }
    r2 = {
        "bull": _judgment("MILD_BULL"),
        "bear": {**_judgment("NEUTRAL"), "changed": True, "change_reason": "reviewed but kept"},
        "neutral": _judgment("NEUTRAL"),
    }
    brain = {"markets": {"US": {"debate_history": []}}}

    with patch.object(brain_module, "load", return_value=brain), \
         patch.object(brain_module, "save"):
        brain_module.save_debate_result("US", "2026-05-22", r1, r2)

    entry = brain["markets"]["US"]["debate_history"][0]
    assert entry["changes"] == []
    assert entry["consensus_shifted"] is False


def test_brain_debate_summary_recomputes_changes_from_actual_stances():
    brain = {
        "markets": {
            "US": {
                "debate_history": [
                    {
                        "date": "2026-05-22",
                        "r1": {
                            "bull": {"stance": "MILD_BULL"},
                            "bear": {"stance": "MILD_BULL"},
                            "neutral": {"stance": "NEUTRAL"},
                        },
                        "r2": {
                            "bull": {"stance": "MILD_BULL"},
                            "bear": {"stance": "MILD_BULL"},
                            "neutral": {"stance": "NEUTRAL"},
                        },
                        "changes": [
                            {
                                "analyst": "neutral",
                                "r1_stance": "NEUTRAL",
                                "r2_stance": "MILD_BULL",
                                "reason": "stale metadata",
                            }
                        ],
                        "consensus_shifted": False,
                        "outcome": "wrong",
                    },
                    {
                        "date": "2026-05-23",
                        "r1": {
                            "bull": {"stance": "NEUTRAL"},
                            "bear": {"stance": "NEUTRAL"},
                            "neutral": {"stance": "NEUTRAL"},
                        },
                        "r2": {
                            "bull": {"stance": "MILD_BULL", "key_reason": "actual shift"},
                            "bear": {"stance": "NEUTRAL"},
                            "neutral": {"stance": "NEUTRAL"},
                        },
                        "changes": [],
                        "consensus_shifted": False,
                        "outcome": "correct",
                    },
                ]
            }
        }
    }

    with patch.object(brain_module, "load", return_value=brain):
        summary = brain_module.get_debate_summary("US", n=2)

    assert "NEUTRAL->MILD_BULL (stale metadata)" not in summary
    assert "2026-05-22 BAD kept:" in summary
    assert "2026-05-23 OK changed BULL NEUTRAL->MILD_BULL" in summary
