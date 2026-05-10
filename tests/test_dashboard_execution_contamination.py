from __future__ import annotations

from unittest.mock import patch

from dashboard import dashboard_server
from trading_bot import TradingBot


def _brain_recent_day(date: str = "2026-05-08") -> dict:
    return {
        "markets": {
            "US": {
                "trained_days": 1,
                "recent_days": [{"date": date, "key_lesson": "", "pnl_pct": -0.1, "trades": 1}],
                "current_beliefs": {},
                "analyst_performance": {},
            }
        },
        "correction_guide": {},
    }


def _record(date: str, issues: list[str], contaminated: bool = True) -> dict:
    return {
        "date": date,
        "market": "US",
        "actual_result": {
            "execution_contaminated": contaminated,
            "execution_issues": issues,
            "pnl_pct": -0.1,
            "win": False,
            "trades": 1,
        },
        "judgments": {},
        "postmortem": {},
    }


def test_execution_issue_payload_treats_broker_position_removed_as_warning_only() -> None:
    payload = dashboard_server._execution_issue_payload(["broker_position_removed"], raw_contaminated=True)

    assert payload["execution_contaminated_raw"] is True
    assert payload["execution_contaminated"] is False
    assert payload["execution_learning_excluded"] is False
    assert payload["execution_warning"] is True
    assert payload["execution_issue_labels"] == ["브로커 미보유 포지션 정리"]


def test_execution_issue_payload_excludes_sell_failed_and_broker_sync_trade() -> None:
    payload = dashboard_server._execution_issue_payload(
        ["sell_failed:intraday_review_sell", "broker_sync_trade"],
        raw_contaminated=True,
    )

    assert payload["execution_contaminated_raw"] is True
    assert payload["execution_contaminated"] is True
    assert payload["execution_learning_excluded"] is True
    assert payload["execution_warning"] is False
    assert "매도 실패(intraday_review_sell)" in payload["execution_issue_labels"]
    assert "브로커 동기화 거래" in payload["execution_issue_labels"]


def test_brain_history_keeps_warning_only_cleanup_out_of_key_lesson() -> None:
    client = dashboard_server.app.test_client()
    with patch.object(dashboard_server, "load_brain", return_value=_brain_recent_day()), patch.object(
        dashboard_server,
        "load_records",
        return_value=[_record("2026-05-08", ["broker_position_removed"])],
    ):
        response = client.get("/api/brain/history?market=US")

    assert response.status_code == 200
    item = response.get_json()["recent_days"][0]
    assert item["execution_contaminated_raw"] is True
    assert item["execution_contaminated"] is False
    assert item["execution_learning_excluded"] is False
    assert item["execution_warning"] is True
    assert item["key_lesson"] == ""


def test_brain_history_marks_trade_affecting_issue_as_execution_contamination() -> None:
    client = dashboard_server.app.test_client()
    with patch.object(dashboard_server, "load_brain", return_value=_brain_recent_day()), patch.object(
        dashboard_server,
        "load_records",
        return_value=[_record("2026-05-08", ["sell_failed:intraday_review_sell", "broker_sync_trade"])],
    ):
        response = client.get("/api/brain/history?market=US")

    assert response.status_code == 200
    item = response.get_json()["recent_days"][0]
    assert item["execution_contaminated_raw"] is True
    assert item["execution_contaminated"] is True
    assert item["execution_learning_excluded"] is True
    assert item["execution_warning"] is False
    assert item["key_lesson"] == ""
    assert "매도 실패(intraday_review_sell)" in item["execution_issue_labels"]


def test_dashboard_page_has_execution_warning_and_readable_label_rendering() -> None:
    response = dashboard_server.app.test_client().get("/")
    analytics_response = dashboard_server.app.test_client().get("/analytics")

    assert response.status_code == 200
    assert analytics_response.status_code == 200
    body = response.get_data(as_text=True)
    analytics_body = analytics_response.get_data(as_text=True)
    assert "실행경고" in body
    assert "execution_issue_labels" in body
    assert "day.key_lesson || executionMarker" not in analytics_body
    assert "const lessonText = day.key_lesson || '';" in analytics_body


def test_trading_bot_execution_health_keeps_sync_cleanup_as_warning() -> None:
    bot = TradingBot.__new__(TradingBot)
    bot._execution_flags = {"US": {"broker_position_removed"}}
    bot.decision_event_log = []
    bot.pending_orders = []

    health = TradingBot._build_execution_health(bot, "US", [])

    assert health["contaminated"] is True
    assert health["learning_excluded"] is False
    assert health["warning"] is True
    assert health["labels"] == ["브로커 미보유 포지션 정리"]


def test_trading_bot_execution_health_excludes_broker_sync_trade() -> None:
    bot = TradingBot.__new__(TradingBot)
    bot._execution_flags = {"US": set()}
    bot.decision_event_log = []
    bot.pending_orders = []

    health = TradingBot._build_execution_health(bot, "US", [{"strategy": "broker_sync"}])

    assert health["contaminated"] is True
    assert health["learning_excluded"] is True
    assert health["warning"] is False
    assert "broker_sync_trade" in health["reasons"]
