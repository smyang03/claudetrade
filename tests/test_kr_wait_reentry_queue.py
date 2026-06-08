from __future__ import annotations

from datetime import datetime, timedelta

from runtime.kr_wait_reentry_queue import build_wait_reentry_items, pop_due_items, requeue_items


def test_build_wait_reentry_items_queues_kr_selected_candidates_only() -> None:
    now = datetime(2026, 6, 8, 9, 30)
    items = build_wait_reentry_items(
        market="KR",
        phase="session_open",
        session_date="2026-06-08",
        selected=["111111", "222222", "333333"],
        selection_meta={
            "candidate_actions": [
                {"ticker": "111111", "action": "WATCH"},
                {"ticker": "222222", "action": "AVOID"},
                {"ticker": "333333", "action": "BUY_READY"},
            ],
            "_candidate_action_routes": [
                {"ticker": "111111", "final_action": "WATCH", "reason": "observe"},
                {"ticker": "333333", "final_action": "HARD_BLOCK", "reason": "quarantine"},
            ],
        },
        candidates=[
            {"ticker": "111111", "score": 91},
            {"ticker": "222222", "score": 90},
            {"ticker": "333333", "score": 89},
        ],
        now=now,
        delay_min=60,
        max_candidates=5,
    )

    assert [item["ticker"] for item in items] == ["111111"]
    assert items[0]["due_at"] == "2026-06-08T10:30:00"
    assert items[0]["candidate"]["_wait_reentry"]["original_action"] == "WATCH"


def test_build_wait_reentry_items_skips_us_and_reentry_source() -> None:
    now = datetime(2026, 6, 8, 9, 30)
    common = {
        "session_date": "2026-06-08",
        "selected": ["AAPL"],
        "selection_meta": {"candidate_actions": [{"ticker": "AAPL", "action": "WATCH"}]},
        "candidates": [{"ticker": "AAPL"}],
        "now": now,
    }

    assert build_wait_reentry_items(market="US", phase="session_open", **common) == []
    assert build_wait_reentry_items(
        market="KR",
        phase="kr_wait60_reeval",
        **{**common, "selected": ["005930"], "candidates": [{"ticker": "005930"}]},
    ) == []


def test_pop_due_and_requeue_items() -> None:
    now = datetime(2026, 6, 8, 10, 30)
    queue = [
        {"id": "due", "due_at": "2026-06-08T10:29:00"},
        {"id": "future", "due_at": "2026-06-08T10:40:00"},
    ]

    due, pending = pop_due_items(queue, now=now, max_items=1)
    assert [item["id"] for item in due] == ["due"]
    assert [item["id"] for item in pending] == ["future"]

    retried = requeue_items(pending, due, now=now, retry_delay_min=2, max_attempts=1)
    assert [item["id"] for item in retried] == ["future", "due"]
    assert retried[-1]["attempts"] == 1
    assert retried[-1]["due_at"] == (now + timedelta(minutes=2)).isoformat(timespec="seconds")

    dropped = requeue_items([], retried[-1:], now=now, retry_delay_min=2, max_attempts=1)
    assert dropped == []
