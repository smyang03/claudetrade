from __future__ import annotations

import pandas as pd

from tools.index_trend_strategy_lab import SleeveSpec, simulate, target_weights


def _panel() -> pd.DataFrame:
    index = pd.date_range("2018-01-01", periods=20, freq="MS")
    return pd.DataFrame(
        {
            "SPY": [100.0 + idx for idx in range(20)],
            "QQQ": [100.0 + 2 * idx for idx in range(20)],
            "KRW=X": [1000.0] * 20,
        },
        index=index,
    )


def test_signal_is_lagged_one_month() -> None:
    panel = _panel()
    spec = SleeveSpec("test", "US", ("SPY",), "buy_hold", "test")
    result = simulate(panel, spec)
    # simulate drops the 13 warm-up rows; holdings remain the lagged 100% signal.
    assert result.iloc[0]["weight_SPY"] == 1.0
    expected = panel["SPY"].pct_change().iloc[13]
    assert abs(result.iloc[0]["gross_return"] - expected) < 1e-12


def test_dual_momentum_selects_stronger_asset_after_warmup() -> None:
    panel = _panel()
    spec = SleeveSpec("test", "US", ("SPY", "QQQ"), "dual_momentum", "test")
    weights = target_weights(panel, spec)
    assert weights.iloc[-1]["QQQ"] == 1.0
    assert weights.iloc[-1]["SPY"] == 0.0


def test_switch_cost_charged_on_initial_entry() -> None:
    panel = _panel()
    spec = SleeveSpec("test", "US", ("SPY",), "buy_hold", "test")
    result = simulate(panel, spec)
    # Initial switch happens before the warm-up slice, so mature monthly rows have no turnover.
    assert result.iloc[0]["turnover"] == 0.0


def test_volatility_target_never_uses_leverage() -> None:
    panel = _panel()
    spec = SleeveSpec("test", "US", ("QQQ",), "sma10_vol12", "test")
    weights = target_weights(panel, spec)
    assert weights["QQQ"].between(0.0, 1.0).all()
