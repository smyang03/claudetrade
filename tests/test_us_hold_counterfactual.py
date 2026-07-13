from __future__ import annotations

import pandas as pd

from tools.us_hold_counterfactual import simulate_exit


def _bars() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": "2026-01-01", "open": 100, "high": 101, "low": 99, "close": 100},
            {"date": "2026-01-02", "open": 96, "high": 97, "low": 95, "close": 96},
            {"date": "2026-01-03", "open": 98, "high": 102, "low": 97, "close": 101},
        ]
    )


def test_gap_below_stop_exits_at_open() -> None:
    price, date = simulate_exit(_bars(), entry_idx=0, entry_price=100, hold_sessions=2, stop_pct=2.5)
    assert price == 96
    assert date == "2026-01-02"


def test_no_stop_exits_at_horizon_close() -> None:
    price, date = simulate_exit(_bars(), entry_idx=0, entry_price=100, hold_sessions=2, stop_pct=None)
    assert price == 101
    assert date == "2026-01-03"
