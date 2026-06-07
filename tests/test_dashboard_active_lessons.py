from __future__ import annotations

import os
from unittest.mock import patch

from dashboard import dashboard_server
from minority_report import active_lessons


def _brain_payload() -> dict:
    return {
        "markets": {
            "US": {
                "mode_performance": {},
                "current_beliefs": {"learned_lessons": []},
            }
        }
    }


def _active_context(market: str, **kwargs) -> dict:
    assert market == "US"
    assert kwargs.get("prompt_scope", "selection") == "selection"
    assert os.getenv("ACTIVE_LESSONS_ENABLED") == "true"
    assert os.getenv("ACTIVE_LESSONS_SHADOW") == "false"
    assert os.getenv("ACTIVE_LESSONS_ALLOW_RECENT_DAYS") is None
    return {
        "section": "[active lessons]\n- selection: promote watch candidates when veto is weak.",
        "preview": "[active lessons]\n- selection: promote watch candidates when veto is weak.",
        "items": [
            {
                "id": "US_lesson_candidates_watch_only_missed_runup_review",
                "market": "US",
                "source": "lesson_candidates",
                "scope": "selection",
                "target_prompt_scope": "selection",
                "allowed_prompt_scopes": ["selection"],
                "text": "promote watch candidates when veto is weak.",
                "severity": "high",
                "confidence": 0.91,
                "sample_count": 42,
                "generated_at": "2026-06-05T00:00:00",
                "score": 520.0,
            }
        ],
        "ignored": [],
        "metadata": {
            "enabled": True,
            "shadow": False,
            "injected": True,
            "lesson_injected": True,
            "count": 1,
            "lesson_count": 1,
            "chars": 72,
            "ignored_reasons": {"not_breached": 2},
        },
    }


def test_active_lessons_api_uses_live_start_config_overlay() -> None:
    client = dashboard_server.app.test_client()
    with patch.object(dashboard_server, "_runtime_env", return_value={"ACTIVE_LESSONS_ENABLED": "false"}), \
         patch.object(
             dashboard_server,
             "_start_config_env_overrides",
             return_value={"ACTIVE_LESSONS_ENABLED": "true", "ACTIVE_LESSONS_SHADOW": "false"},
         ), \
         patch.object(active_lessons, "build_active_lesson_context", side_effect=_active_context):
        response = client.get("/api/active-lessons?market=US&mode=live")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["metadata"]["injected"] is True
    assert payload["env"]["ACTIVE_LESSONS_ENABLED"] == "true"
    assert payload["items"][0]["id"] == "US_lesson_candidates_watch_only_missed_runup_review"


def test_patterns_api_exposes_active_lessons_for_dashboard_card() -> None:
    client = dashboard_server.app.test_client()
    with patch.object(dashboard_server, "load_records", return_value=[]), \
         patch.object(dashboard_server, "load_brain", return_value=_brain_payload()), \
         patch.object(dashboard_server, "_runtime_env", return_value={}), \
         patch.object(
             dashboard_server,
             "_start_config_env_overrides",
             return_value={"ACTIVE_LESSONS_ENABLED": "true", "ACTIVE_LESSONS_SHADOW": "false"},
         ), \
         patch.object(active_lessons, "build_active_lesson_context", side_effect=_active_context):
        response = client.get("/api/patterns?market=US&mode=live")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["active_lessons"]["metadata"]["lesson_injected"] is True
    assert payload["active_lessons"]["items"][0]["text"] == "promote watch candidates when veto is weak."


def test_analytics_page_contains_active_lesson_panel() -> None:
    response = dashboard_server.app.test_client().get("/analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "active-lessons-list" in body
    assert "Claude 주입 교훈" in body


def test_hold_advisor_summary_api_exposes_pathb_revenue_groups() -> None:
    client = dashboard_server.app.test_client()
    fake_payload = {
        "generated_at": "2026-06-07T09:00:00+09:00",
        "scope": {"market": "US"},
        "decision_requests": {
            "by_pathb_revenue_path_decision": [
                {"market": "US", "pathb_revenue_exit_reason": "profit_ladder", "decision": "HOLD", "calls": 2}
            ]
        },
    }
    with patch("tools.analyze_hold_advisor_latency.analyze_hold_advisor_latency", return_value=fake_payload):
        response = client.get("/api/hold-advisor/summary?market=US&days=5")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["decision_requests"]["by_pathb_revenue_path_decision"][0]["pathb_revenue_exit_reason"] == "profit_ladder"
