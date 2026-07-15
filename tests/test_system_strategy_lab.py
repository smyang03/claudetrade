from __future__ import annotations

import numpy as np
import pandas as pd

from tools.system_strategy_lab import barrier_exit, moving_block_lcb, trade_metrics, _trade_return


def test_trade_return_uses_fx_and_cost() -> None:
    fx = pd.Series([1400.0, 1428.0], index=["2026-01-02", "2026-01-05"])
    result = _trade_return(
        market="US",
        entry_price=100.0,
        exit_price=110.0,
        entry_date="2026-01-02",
        exit_date="2026-01-05",
        fx=fx,
        cost_pct=0.70,
    )
    assert result is not None
    gross, net = result
    assert gross == pytest_approx(12.2)
    assert net == pytest_approx(11.5)


def test_trade_return_kr_ignores_fx() -> None:
    result = _trade_return(
        market="KR",
        entry_price=100.0,
        exit_price=103.0,
        entry_date="2026-01-02",
        exit_date="2026-01-05",
        fx=pd.Series(dtype=float),
        cost_pct=0.21,
    )
    assert result is not None
    assert result[0] == pytest_approx(3.0)
    assert result[1] == pytest_approx(2.79)


def test_metrics_remove_top_three_contributors() -> None:
    frame = pd.DataFrame(
        {
            "date": [f"2026-01-{day:02d}" for day in range(1, 11)],
            "net_pct": [10.0, 9.0, 8.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0],
            "excess_pct": np.zeros(10),
            "mfe_pct": np.ones(10),
            "mae_pct": -np.ones(10),
        }
    )
    metrics = trade_metrics(frame, seed=7)
    assert metrics["mean_net_pct"] > 0
    assert metrics["mean_ex_top3_trades_pct"] == pytest_approx(-1.0)


def test_block_lcb_requires_ten_values() -> None:
    assert moving_block_lcb(np.ones(9), seed=1) is None
    assert moving_block_lcb(np.ones(10), seed=1) == pytest_approx(1.0)


def test_barrier_exit_uses_stop_on_same_bar_tie() -> None:
    window = pd.DataFrame(
        [{"date": "2026-01-02", "open": 100.0, "high": 106.0, "low": 97.0, "close": 104.0}]
    )
    exit_date, exit_price, reason = barrier_exit(
        window, entry_price=100.0, take_profit_pct=5.0, stop_loss_pct=2.0
    )
    assert exit_date == "2026-01-02"
    assert exit_price == pytest_approx(98.0)
    assert reason == "STOP"


def pytest_approx(value: float):
    import pytest

    return pytest.approx(value, abs=1e-9)
