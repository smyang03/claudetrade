from __future__ import annotations

import pandas as pd

from tools.profit_path_forward_monitor import match_predictions, summarize


def test_forward_match_uses_same_path_and_nearest_timestamp() -> None:
    predictions = pd.DataFrame(
        [
            {
                "event_id": 1,
                "market": "KR",
                "session_date": "2026-07-10",
                "ticker": "005930",
                "prediction_ts": pd.Timestamp("2026-07-10T00:10:00Z"),
                "path_name": "pullback_reclaim",
                "model_version": "v1",
                "probability": 0.7,
                "expected_net_pct": 0.5,
            }
        ]
    )
    outcomes = pd.DataFrame(
        [
            {
                "path_id": 10,
                "market": "KR",
                "session_date": "2026-07-10",
                "ticker_key": "005930",
                "path_name": "pullback_reclaim",
                "outcome_ts": pd.Timestamp("2026-07-10T00:09:40Z"),
                "outcome_60m_pct": 1.0,
                "max_drawdown_60m_pct": -0.5,
            },
            {
                "path_id": 11,
                "market": "KR",
                "session_date": "2026-07-10",
                "ticker_key": "005930",
                "path_name": "pullback_reclaim",
                "outcome_ts": pd.Timestamp("2026-07-10T00:11:30Z"),
                "outcome_60m_pct": -2.0,
                "max_drawdown_60m_pct": -3.0,
            },
        ]
    )
    matched = match_predictions(predictions, outcomes, tolerance_min=5)
    assert matched.iloc[0]["path_id"] == 10


def test_forward_promotion_is_false_before_minimum_sessions() -> None:
    frame = pd.DataFrame(
        [
            {
                "market": "KR",
                "session_date": "2026-07-10",
                "probability": 0.7,
                "outcome_60m_pct": 1.0,
                "max_drawdown_60m_pct": -0.5,
            },
            {
                "market": "KR",
                "session_date": "2026-07-10",
                "probability": 0.3,
                "outcome_60m_pct": -1.0,
                "max_drawdown_60m_pct": -1.5,
            },
        ]
    )
    assert summarize(frame, min_matched=2, min_sessions=20)["promotion_eligible_forward"] is False
