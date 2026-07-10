from __future__ import annotations

import pandas as pd
import pytest

from tools.build_us_yahoo_point_in_time import build_ticker_frame


def _bars() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=90).strftime("%Y-%m-%d")
    close = pd.Series(range(100, 190), dtype=float)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000.0,
        }
    )


def test_features_at_date_do_not_change_when_future_prices_change() -> None:
    base = _bars()
    altered = base.copy()
    altered.loc[altered.index >= 70, "close"] *= 5.0
    left = build_ticker_frame(base).iloc[60]
    right = build_ticker_frame(altered).iloc[60]
    for column in ("rsi", "bb_pct", "momentum_20d_pct", "atr_pct", "volume_ratio"):
        assert left[column] == pytest.approx(right[column])


def test_label_starts_at_next_session_open() -> None:
    result = build_ticker_frame(_bars())
    row = result.iloc[60]
    expected = (result.iloc[63]["close"] / result.iloc[61]["open"] - 1.0) * 100.0
    assert row["gross_usd_3d_pct"] == pytest.approx(expected)
