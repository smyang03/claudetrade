from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from tools.us_swing_shadow_runner import (
    classify_breadth_context,
    ensure_schema,
    mature_pending,
    summarize_forward,
)


def test_mature_pending_uses_next_open_fifth_close_fx_and_cost(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    price_dir.mkdir()
    pd.DataFrame([
        {"date": "2026-01-02", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
        {"date": "2026-01-05", "open": 101, "high": 102, "low": 100, "close": 101, "volume": 1000},
        {"date": "2026-01-06", "open": 102, "high": 103, "low": 101, "close": 102, "volume": 1000},
        {"date": "2026-01-07", "open": 103, "high": 104, "low": 102, "close": 103, "volume": 1000},
        {"date": "2026-01-08", "open": 104, "high": 105, "low": 103, "close": 104, "volume": 1000},
        {"date": "2026-01-09", "open": 105, "high": 112, "low": 104, "close": 110, "volume": 1000},
    ]).to_csv(price_dir / "us_TEST.csv", index=False)
    con = sqlite3.connect(":memory:")
    ensure_schema(con)
    con.execute(
        "INSERT INTO signals(signal_date,ticker,feature_date,model_version,rank,created_at,status) VALUES (?,?,?,?,?,?,?)",
        ("2026-01-05", "TEST", "2026-01-02", "m", 1, "now", "PENDING"),
    )
    result = mature_pending(
        con, price_dir=price_dir,
        fx_map={"2026-01-05": 1400.0, "2026-01-09": 1400.0}, cost_pct=0.5,
    )
    row = con.execute("SELECT entry_price,exit_price,net_krw_pct,status FROM signals").fetchone()
    assert result["matured_now"] == 1
    assert row[0] == 101.0
    assert row[1] == 110.0
    assert abs(row[2] - ((110 / 101 - 1) * 100 - 0.5)) < 1e-9
    assert row[3] == "MATURED"
    assert summarize_forward(con)["matured"] == 1


def test_breadth_context_is_observational_three_way_tag() -> None:
    assert classify_breadth_context(-0.31) == "NARROW"
    assert classify_breadth_context(0.00) == "BALANCED"
    assert classify_breadth_context(0.31) == "BROAD"
    assert classify_breadth_context(None) == "MISSING"


def test_schema_migrates_existing_signal_table_with_breadth_columns() -> None:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE signals(signal_date TEXT, ticker TEXT, status TEXT)")
    ensure_schema(con)
    columns = {row[1] for row in con.execute("PRAGMA table_info(signals)")}
    assert "prior_narrow_excess_pct" in columns
    assert "breadth_context_state" in columns
