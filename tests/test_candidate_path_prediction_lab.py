from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from audit.candidate_audit_store import CandidateAuditStore
from tools.candidate_path_prediction_lab import (
    FEATURE_GROUPS,
    _daily_top,
    _post_open_features,
    first_passage,
    label_candidate_path,
    load_first_candidates,
    next_complete_minute,
    session_entry_floor,
)


def _bars(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])
    frame["ts_utc"] = pd.to_datetime(frame["ts"], utc=True)
    frame["source"] = "test"
    return frame


def test_next_complete_minute_does_not_use_partial_decision_bar() -> None:
    assert str(next_complete_minute("2026-07-16T00:00:00+00:00")) == "2026-07-16 00:00:00+00:00"
    assert str(next_complete_minute("2026-07-16T00:00:01+00:00")) == "2026-07-16 00:01:00+00:00"


def test_first_passage_is_stop_first_when_same_bar_touches_both() -> None:
    window = pd.DataFrame([{"elapsed_min": 0.0, "high": 104.0, "low": 96.0}])
    result = first_passage(
        window,
        entry_price=100.0,
        target_pct=2.3,
        stop_pct=2.5,
        horizon_return_pct=1.0,
    )
    assert result.outcome == "STOP_FIRST"
    assert result.target_before_stop == 0
    assert result.policy_gross_pct == -2.5


def test_label_candidate_path_ignores_partial_bar_and_orders_later_target() -> None:
    rows = [
        ("2026-07-16T00:00:00+00:00", 100.0, 110.0, 90.0, 100.0),
    ]
    for minute in range(1, 61):
        high = 103.0 if minute == 5 else 101.0
        timestamp = pd.Timestamp("2026-07-16T00:00:00Z") + pd.Timedelta(minutes=minute)
        rows.append((timestamp.isoformat(), 100.0, high, 99.5, 100.5))
    bars = _bars(rows)
    labels, reason = label_candidate_path(
        bars,
        known_at="2026-07-16T00:00:30+00:00",
        market="US",
    )
    assert reason == "ok"
    assert labels is not None
    assert labels["entry_ts"] == "2026-07-16T00:01:00+00:00"
    assert labels["h60_t2p3_s2p5_outcome"] == "TARGET_FIRST"
    assert labels["h60_t2p3_s2p5_target_before_stop"] == 1
    assert round(labels["h60_t2p3_s2p5_policy_net_pct"], 6) == 1.8


def test_no_touch_marks_horizon_return_and_cost() -> None:
    rows = []
    for minute in range(60):
        rows.append((f"2026-07-16T00:{minute:02d}:00+00:00", 100.0, 101.0, 99.0, 100.4))
    labels, reason = label_candidate_path(
        _bars(rows),
        known_at="2026-07-16T00:00:00+00:00",
        market="KR",
    )
    assert reason == "ok"
    assert labels is not None
    assert labels["h60_t3p6_s2p5_outcome"] == "NO_TOUCH"
    assert round(labels["h60_t3p6_s2p5_policy_net_pct"], 2) == 0.19


def test_post_open_features_reject_future_snapshot() -> None:
    raw = '{"known_at":"2026-07-16T00:01:00+00:00","ret_3m_pct":1.2}'
    assert _post_open_features(raw, candidate_known_at="2026-07-16T00:00:00+00:00") == {}


def test_daily_top_selects_within_each_session() -> None:
    rows = pd.DataFrame(
        {
            "session_date": ["2026-07-15", "2026-07-15", "2026-07-16", "2026-07-16"],
            "ticker": ["A", "B", "C", "D"],
        }
    )
    selected = _daily_top(rows, [0.1, 0.9, 0.8, 0.2], top_k=1)
    assert selected["ticker"].tolist() == ["B", "C"]


def test_candidate_time_models_exclude_realized_entry_fields() -> None:
    assert "entry_delay_sec" not in FEATURE_GROUPS["combined"]
    assert "entry_vs_candidate_pct" not in FEATURE_GROUPS["combined"]


def test_first_candidate_loader_prefers_immutable_registry_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        store = CandidateAuditStore(db)
        base = {
            "runtime_mode": "live",
            "market": "US",
            "session_date": "2026-07-16",
            "ticker": "PYPL",
            "volume_ratio": 2.0,
        }
        store.upsert_candidate(
            {
                **base,
                "call_id": "first",
                "known_at": "2026-07-16T13:31:00+00:00",
                "price": 74.0,
                "trainer_prompt_score": 0.4,
            }
        )
        store.upsert_candidate(
            {
                **base,
                "call_id": "later",
                "known_at": "2026-07-16T13:34:00+00:00",
                "price": 78.0,
                "trainer_prompt_score": 0.9,
            }
        )
        frame = load_first_candidates(db, markets=["US"])

    assert len(frame) == 1
    assert frame.iloc[0]["candidate_price"] == 74.0
    assert frame.iloc[0]["trainer_prompt_score"] == 0.4
    assert str(frame.iloc[0]["candidate_key"]).startswith("creg_")


def test_session_entry_floor_respects_market_clock_and_dst() -> None:
    assert str(session_entry_floor("2026-07-16T00:00:00Z", market="KR", entry_lag_min=5)) == (
        "2026-07-16 00:05:00+00:00"
    )
    assert str(session_entry_floor("2026-07-15T13:00:00Z", market="US", entry_lag_min=5)) == (
        "2026-07-15 13:35:00+00:00"
    )


def test_label_candidate_path_rejects_cross_session_entry() -> None:
    # 7/2 장전(09:22 ET) 후보인데 분봉이 7/8부터만 존재 → 다음 세션 개장 봉으로
    # 진입하면 안 된다 (홀드아웃 27픽 중 14픽 오염 실측 재발 방지).
    rows = []
    for minute in range(0, 70):
        timestamp = pd.Timestamp("2026-07-08T13:30:00Z") + pd.Timedelta(minutes=minute)
        rows.append((timestamp.isoformat(), 100.0, 101.0, 99.0, 100.0))
    labels, reason = label_candidate_path(
        _bars(rows),
        known_at="2026-07-02T13:22:00+00:00",
        market="US",
        entry_lag_min=5,
    )
    assert labels is None
    assert reason == "no_same_session_bar"


def test_label_candidate_path_rejects_late_entry_for_preopen_candidate() -> None:
    # 같은 세션이라도 계약 진입 시점(개장+5분=13:35Z)보다 10분 넘게 늦은 첫 봉은 거부.
    rows = []
    for minute in range(0, 70):
        timestamp = pd.Timestamp("2026-07-02T13:50:00Z") + pd.Timedelta(minutes=minute)
        rows.append((timestamp.isoformat(), 100.0, 101.0, 99.0, 100.0))
    labels, reason = label_candidate_path(
        _bars(rows),
        known_at="2026-07-02T13:22:00+00:00",
        market="US",
        entry_lag_min=5,
    )
    assert labels is None
    assert reason == "entry_delay_exceeded"
