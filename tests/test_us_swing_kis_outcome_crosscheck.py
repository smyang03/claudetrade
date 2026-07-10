from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from tools.us_swing_kis_outcome_crosscheck import crosscheck


def test_kis_outcome_crosscheck_uses_exact_entry_and_exit_dates() -> None:
    selected = pd.DataFrame([
        {
            "session_date": "2026-04-01",
            "ticker": "TEST",
            "selection_rank": 1,
            "entry_date_5d": "2026-04-01",
            "exit_date_5d": "2026-04-07",
            "entry_open_5d": 100.0,
            "exit_close_5d": 110.0,
        }
    ])
    bars = pd.DataFrame([
        {"date": "2026-04-01", "open": 100.0, "high": 101, "low": 99, "close": 100, "volume": 1},
        {"date": "2026-04-07", "open": 109.0, "high": 111, "low": 108, "close": 110.0, "volume": 1},
    ])

    with patch("tools.us_swing_kis_outcome_crosscheck._daily_ohlcv_us_kis", return_value=bars):
        report = crosscheck(selected=selected, sessions=1, token="token")

    assert report["agreement_passed"] is True
    assert report["metrics"]["coverage"] == 1.0
    assert report["rows"][0]["return_difference_pct"] == 0.0
