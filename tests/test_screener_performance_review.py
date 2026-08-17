from datetime import datetime

import pytest

from tools.screener_performance_review import (
    KST,
    _MINUTE_CACHE,
    _rankdata,
    _session_topk,
    _session_topk_counterfactual,
    attach_local_forward,
)


def test_rankdata_uses_average_rank_for_ties():
    assert _rankdata([10.0, 20.0, 20.0, 40.0]) == [1.0, 2.5, 2.5, 4.0]


def test_session_topk_freezes_selection_before_label_filter():
    rows = [
        {"session_date": "2026-07-01", "raw_rank": 1, "local_return_60m_pct": None},
        {"session_date": "2026-07-01", "raw_rank": 2, "local_return_60m_pct": 1.0},
        {"session_date": "2026-07-01", "raw_rank": 3, "local_return_60m_pct": 10.0},
    ]

    result = _session_topk(
        rows,
        "raw_rank",
        lower_is_better=True,
        ks=(2,),
        horizon_min=60,
    )["top_2"]

    assert result["selected_candidates"] == 2
    assert result["matched_labels"] == 1
    assert result["label_coverage"] == 0.5
    assert result["mean_session_gross_pct"] == 1.0
    assert result["fully_labeled_sessions"] == 0


def test_attach_local_forward_never_uses_bar_before_known_at():
    ticker = "POINTINTIME"
    key = ("KR", ticker)
    _MINUTE_CACHE[key] = [
        (datetime(2026, 7, 1, 9, 0, tzinfo=KST), 90.0, 95.0, 89.0, 94.0),
        (datetime(2026, 7, 1, 9, 1, tzinfo=KST), 100.0, 102.0, 99.0, 101.0),
        (datetime(2026, 7, 1, 10, 1, tzinfo=KST), 109.0, 112.0, 98.0, 110.0),
    ]
    rows = [
        {
            "market": "KR",
            "ticker": ticker,
            "known_at": "2026-07-01T09:00:30+09:00",
        }
    ]

    try:
        attach_local_forward(rows, horizon_min=60)
    finally:
        _MINUTE_CACHE.pop(key, None)

    assert rows[0]["local_entry_at"] == "2026-07-01T09:01:00+09:00"
    assert rows[0]["local_entry_price"] == 100.0
    assert rows[0]["local_return_60m_pct"] == pytest.approx(10.0)
    assert rows[0]["local_mfe_60m_pct"] == pytest.approx(12.0)
    assert rows[0]["local_mae_60m_pct"] == pytest.approx(-2.0)


def test_topk_counterfactual_requires_coverage_on_both_arms():
    rows = [
        {
            "session_date": "2026-07-01",
            "ticker": "A",
            "raw_rank": 1,
            "trainer_score_rank": 2,
            "local_return_60m_pct": 1.0,
        },
        {
            "session_date": "2026-07-01",
            "ticker": "B",
            "raw_rank": 2,
            "trainer_score_rank": 3,
            "local_return_60m_pct": None,
        },
        {
            "session_date": "2026-07-01",
            "ticker": "C",
            "raw_rank": 3,
            "trainer_score_rank": 1,
            "local_return_60m_pct": 5.0,
        },
    ]

    result = _session_topk_counterfactual(
        rows,
        control_field="raw_rank",
        treatment_field="trainer_score_rank",
        control_lower_is_better=True,
        treatment_lower_is_better=True,
        ks=(2,),
        min_label_coverage=0.8,
    )["top_2"]

    assert result["paired_sessions"] == 1
    assert result["mean_treatment_minus_control_pct"] == 2.0
    assert result["coverage_qualified_sessions"] == 0
