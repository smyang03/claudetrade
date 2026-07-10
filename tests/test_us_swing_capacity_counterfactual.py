from __future__ import annotations

from pathlib import Path

import pandas as pd

from tools.us_swing_capacity_counterfactual import simulate_capacity_path
from tools.us_swing_exit_counterfactual import FxLookup


def _write_prices(path: Path, ticker: str, start: str, price: float) -> None:
    dates = pd.bdate_range(start, periods=5).strftime("%Y-%m-%d")
    pd.DataFrame(
        {
            "date": dates,
            "open": [price] * 5,
            "high": [price * 1.01] * 5,
            "low": [price * 0.99] * 5,
            "close": [price * 1.01] * 5,
        }
    ).to_csv(path / f"us_{ticker}.csv", index=False)


def _fx() -> FxLookup:
    return FxLookup(pd.DataFrame({"date": ["2026-01-01"], "usdkrw": [1000.0]}))


def _row(signal: str, entry: str, exit_date: str, ticker: str, rank: int, price: float) -> dict:
    return {
        "session_date": signal,
        "ticker": ticker,
        "selection_rank": rank,
        "entry_date_5d": entry,
        "exit_date_5d": exit_date,
        "entry_open_5d": price,
    }


def test_one_slot_blocks_entries_through_exit_date(tmp_path: Path) -> None:
    _write_prices(tmp_path, "A", "2026-01-05", 10.0)
    _write_prices(tmp_path, "B", "2026-01-06", 10.0)
    selected = pd.DataFrame(
        [
            _row("2026-01-02", "2026-01-05", "2026-01-09", "A", 1, 10.0),
            _row("2026-01-05", "2026-01-06", "2026-01-12", "B", 1, 10.0),
        ]
    )
    metrics, rows = simulate_capacity_path(
        selected, price_dir=tmp_path, fx=_fx(), policy="rank1_skip", entry_slippage_pct=0.0
    )
    assert metrics["trades"] == 1
    assert metrics["skipped_slot_sessions"] == 1
    assert rows["status"].tolist() == ["TRADED", "SKIPPED_SLOT"]


def test_whole_share_never_rounds_up_and_fallback_can_select_rank2(tmp_path: Path) -> None:
    _write_prices(tmp_path, "EXP", "2026-01-05", 60.0)
    _write_prices(tmp_path, "CHEAP", "2026-01-05", 10.0)
    selected = pd.DataFrame(
        [
            _row("2026-01-02", "2026-01-05", "2026-01-09", "EXP", 1, 60.0),
            _row("2026-01-02", "2026-01-05", "2026-01-09", "CHEAP", 2, 10.0),
        ]
    )
    rank1, _ = simulate_capacity_path(
        selected, price_dir=tmp_path, fx=_fx(), policy="rank1_skip", entry_slippage_pct=0.0
    )
    fallback, rows = simulate_capacity_path(
        selected,
        price_dir=tmp_path,
        fx=_fx(),
        policy="affordable_fallback_top3",
        entry_slippage_pct=0.0,
    )
    assert rank1["trades"] == 0
    assert rank1["skipped_unaffordable_sessions"] == 1
    assert fallback["trades"] == 1
    assert fallback["fallback_trades"] == 1
    assert int(rows.iloc[0]["rank"]) == 2
    assert int(rows.iloc[0]["qty"]) == 5


def test_entry_slippage_can_make_one_share_unaffordable(tmp_path: Path) -> None:
    _write_prices(tmp_path, "EDGE", "2026-01-05", 50.0)
    selected = pd.DataFrame(
        [_row("2026-01-02", "2026-01-05", "2026-01-09", "EDGE", 1, 50.0)]
    )
    zero, _ = simulate_capacity_path(
        selected, price_dir=tmp_path, fx=_fx(), policy="rank1_skip", entry_slippage_pct=0.0
    )
    slipped, _ = simulate_capacity_path(
        selected, price_dir=tmp_path, fx=_fx(), policy="rank1_skip", entry_slippage_pct=0.25
    )
    assert zero["trades"] == 1
    assert slipped["trades"] == 0
    assert slipped["skipped_unaffordable_sessions"] == 1
