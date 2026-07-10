from __future__ import annotations

import pandas as pd
import pytest

from tools.us_swing_exit_counterfactual import simulate_exit


def test_gap_through_stop_fills_at_open_not_stop_price() -> None:
    bars = pd.DataFrame([
        {"date": "2026-01-02", "open": 100, "high": 103, "low": 99, "close": 101},
        {"date": "2026-01-05", "open": 90, "high": 94, "low": 89, "close": 92},
    ])

    date, price, reason = simulate_exit(
        bars, entry_price=100.0, tp_pct=0.12, sl_pct=0.06, tie_break="sl_first"
    )

    assert (date, price, reason) == ("2026-01-05", 90.0, "SL_GAP")


def test_same_day_both_hit_has_explicit_conservative_and_optimistic_bounds() -> None:
    bars = pd.DataFrame([
        {"date": "2026-01-02", "open": 100, "high": 113, "low": 93, "close": 101},
    ])

    conservative = simulate_exit(
        bars, entry_price=100.0, tp_pct=0.12, sl_pct=0.06, tie_break="sl_first"
    )
    optimistic = simulate_exit(
        bars, entry_price=100.0, tp_pct=0.12, sl_pct=0.06, tie_break="tp_first"
    )

    assert conservative == ("2026-01-02", 94.0, "BOTH_SL_FIRST")
    assert optimistic[0] == "2026-01-02"
    assert optimistic[1] == pytest.approx(112.0)
    assert optimistic[2] == "BOTH_TP_FIRST"
