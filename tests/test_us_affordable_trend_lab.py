from __future__ import annotations

import pandas as pd
import pytest

from tools.us_affordable_trend_lab import metrics, simulate, target_weights


def panel(prices: list[float]) -> pd.DataFrame:
    index = pd.date_range("2020-01-01", periods=len(prices), freq="MS")
    return pd.DataFrame(
        {
            "SCHG": prices,
            "BIL": [100.0 + 0.1 * idx for idx in range(len(prices))],
            "KRW=X": [1400.0] * len(prices),
        },
        index=index,
    )


def test_target_is_formed_from_completed_month_only() -> None:
    frame = panel([100.0 + idx for idx in range(16)])
    signal = target_weights(frame, sma_window=3, mom_window=3)

    assert signal.iloc[2]["SCHG"] == 0.0
    assert signal.iloc[3]["SCHG"] == 1.0
    assert signal.iloc[3]["BIL"] == 0.0


def test_simulation_applies_signal_one_month_later() -> None:
    frame = panel([100.0 + idx for idx in range(18)])
    signal = target_weights(frame, sma_window=3, mom_window=3)
    result = simulate(
        frame,
        sma_window=3,
        mom_window=3,
        one_way_cost_pct=0.25,
    )

    expected = signal.shift(1).loc[result.index, "SCHG"].fillna(0.0)
    assert result["weight_SCHG"].tolist() == expected.tolist()


def test_switch_charges_both_sides_of_turnover() -> None:
    frame = panel([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 80.0, 79.0, 78.0, 77.0])
    result = simulate(
        frame,
        sma_window=2,
        mom_window=2,
        one_way_cost_pct=0.25,
    )
    switched = result[result["turnover"] == 2.0]

    assert not switched.empty
    for timestamp, row in switched.iterrows():
        gross = row["gross_return"]
        assert row["net_return"] == pytest.approx(gross - 0.005)


def test_metrics_include_initial_capital_in_drawdown() -> None:
    frame = pd.DataFrame(
        {
            "net_return": [-0.10, 0.05],
            "turnover": [0.0, 0.0],
            "weight_SCHG": [1.0, 1.0],
        },
        index=pd.date_range("2020-01-01", periods=2, freq="MS"),
    )

    result = metrics(frame, seed=1)

    assert result["max_drawdown_pct"] == pytest.approx(-10.0)
