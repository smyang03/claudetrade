from __future__ import annotations

import pandas as pd
import pytest

from tools.candidate_universe_enhancement_lab import (
    _daily_snapshot,
    _opening_cohort,
    consensus_profiles,
)


def test_daily_snapshot_uses_only_rows_before_session_date() -> None:
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-13", "2026-07-14", "2026-07-15"]),
            "open": [100.0, 101.0, 999.0],
            "high": [101.0, 103.0, 999.0],
            "low": [99.0, 100.0, 999.0],
            "close": [100.0, 102.0, 999.0],
            "volume": [1000.0, 2000.0, 999999.0],
        }
    )
    snapshot = _daily_snapshot(daily, "2026-07-15")
    assert snapshot["prev_close"] == 102.0
    assert snapshot["ret_1d_pct"] == pytest.approx(2.0)


def test_opening_cohort_excludes_candidates_known_after_open_plus_five() -> None:
    frame = pd.DataFrame(
        {
            "market": ["US", "US"],
            "known_at": [
                "2026-07-15T13:34:00+00:00",
                "2026-07-15T13:36:00+00:00",
            ],
            "h60_label_available": [1, 1],
        }
    )
    result = _opening_cohort(frame, "US")
    assert len(result) == 1
    assert result.iloc[0]["known_at"] == "2026-07-15T13:34:00+00:00"


def test_consensus_profile_keeps_only_same_session_ticker_intersection() -> None:
    ledger = pd.DataFrame(
        [
            {
                "arm": "US_BASELINE_LOGIT",
                "session_date": "2026-07-15",
                "ticker": "AAA",
                "_outcome": "TARGET_FIRST",
                "_net": 3.1,
                "target_probability": 0.7,
                "rank_score": 1.0,
            },
            {
                "arm": "US_DAILY_ONLY_FOREST",
                "session_date": "2026-07-15",
                "ticker": "AAA",
                "_outcome": "TARGET_FIRST",
                "_net": 3.1,
                "target_probability": 0.6,
                "rank_score": 0.9,
            },
            {
                "arm": "US_DAILY_ONLY_FOREST",
                "session_date": "2026-07-15",
                "ticker": "BBB",
                "_outcome": "STOP_FIRST",
                "_net": -3.0,
                "target_probability": 0.5,
                "rank_score": 0.8,
            },
        ]
    )
    profile = consensus_profiles(ledger)[
        "US_BASELINE_LOGIT__AND__US_DAILY_ONLY_FOREST"
    ]
    assert profile["n"] == 1
    assert profile["records"][0]["ticker"] == "AAA"
    assert profile["evidence_contract"]["feature_sets_disjoint"] is True
    assert profile["authority"] == "SHADOW_ONLY_NO_ORDER_AUTHORITY"
