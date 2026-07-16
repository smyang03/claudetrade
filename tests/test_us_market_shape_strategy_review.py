from __future__ import annotations

import json

from tools.us_market_shape_strategy_review import (
    AUTHORITY,
    classify_us_open_context,
    select_open15_snapshot,
)


def test_open15_snapshot_uses_richest_point_in_time_row() -> None:
    rows = [
        {
            "known_at": "2026-07-16T22:40:00+09:00",
            "post_open_features_json": json.dumps(
                {
                    "market_open_elapsed_min": 10.0,
                    "ret_5m_pct": 0.4,
                    "ret_10m_pct": None,
                    "vwap_distance_pct": None,
                    "opening_range_break": None,
                }
            ),
        },
        {
            "known_at": "2026-07-16T22:44:00+09:00",
            "post_open_features_json": json.dumps(
                {
                    "market_open_elapsed_min": 14.0,
                    "ret_5m_pct": 0.5,
                    "ret_10m_pct": 0.8,
                    "vwap_distance_pct": 0.2,
                    "opening_range_break": False,
                }
            ),
        },
        {
            "known_at": "2026-07-16T22:50:00+09:00",
            "post_open_features_json": json.dumps(
                {
                    "market_open_elapsed_min": 20.0,
                    "ret_5m_pct": 2.0,
                    "ret_10m_pct": 3.0,
                    "vwap_distance_pct": 1.0,
                    "opening_range_break": True,
                }
            ),
        },
    ]

    selected = select_open15_snapshot(rows)

    assert selected is not None
    raw, payload = selected
    assert raw["known_at"] == "2026-07-16T22:44:00+09:00"
    assert payload["market_open_elapsed_min"] == 14.0


def test_us_open_context_is_observational_and_rejects_overextension() -> None:
    positive = classify_us_open_context(
        {
            "ret_5m_pct": 1.0,
            "ret_10m_pct": 1.4,
            "vwap_distance_pct": 0.3,
            "pullback_from_high_pct": -0.5,
            "opening_range_break": True,
            "momentum_state": "early_strength",
        },
        news_count=2,
        news_type="direct_catalyst",
    )
    overextended = classify_us_open_context(
        {
            "ret_5m_pct": 3.5,
            "ret_10m_pct": 4.0,
            "vwap_distance_pct": 1.0,
            "pullback_from_high_pct": -0.2,
            "opening_range_break": True,
            "momentum_state": "overextended",
        },
        news_count=3,
        news_type="direct_catalyst",
    )

    assert positive["positive_tape"] is True
    assert positive["controlled_pullback"] is True
    assert "CATALYST" in positive["tags"]
    assert positive["authority"] == AUTHORITY
    assert overextended["positive_tape"] is False
    assert overextended["authority"] == AUTHORITY
