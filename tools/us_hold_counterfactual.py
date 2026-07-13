from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from tools.us_daily_alpha_walkforward import _block_bootstrap_lcb


def simulate_exit(
    bars: pd.DataFrame,
    *,
    entry_idx: int,
    entry_price: float,
    hold_sessions: int,
    stop_pct: float | None,
) -> tuple[float, str]:
    stop_price = float(entry_price) * (1.0 - float(stop_pct or 0.0) / 100.0)
    end_idx = entry_idx + hold_sessions
    if end_idx >= len(bars):
        raise ValueError("insufficient forward bars")
    for idx in range(entry_idx + 1, end_idx + 1):
        row = bars.iloc[idx]
        if stop_pct is not None and float(row["open"]) <= stop_price:
            return float(row["open"]), str(row["date"])
        if stop_pct is not None and float(row["low"]) <= stop_price:
            return stop_price, str(row["date"])
    row = bars.iloc[end_idx]
    return float(row["close"]), str(row["date"])


def _fx_history(start: str, end: str) -> dict[str, float]:
    import yfinance as yf

    raw = yf.download(
        "KRW=X", start=start, end=end, interval="1d", auto_adjust=True, repair=True,
        progress=False, threads=False, multi_level_index=False,
    ).reset_index()
    raw["date"] = pd.to_datetime(raw["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return {str(row.date): float(row.Close) for row in raw.itertuples() if pd.notna(row.Close)}


def _metrics(frame: pd.DataFrame, column: str, seed: int) -> dict[str, Any]:
    daily = frame.dropna(subset=[column]).groupby("session_date")[column].mean().sort_index()
    values = daily.to_numpy(dtype=float)
    positive = float(values[values > 0].sum())
    negative = float(-values[values < 0].sum())
    ordered = np.sort(values)[::-1]
    return {
        "trades": int(frame[column].notna().sum()),
        "sessions": int(len(values)),
        "mean_trade_pct": float(frame[column].mean()),
        "mean_daily_pct": float(values.mean()),
        "median_daily_pct": float(np.median(values)),
        "win_rate_daily": float((values > 0).mean()),
        "profit_factor_daily": float(positive / negative) if negative > 0 else None,
        "block_bootstrap_lcb_pct": _block_bootstrap_lcb(values, seed=seed),
        "mean_ex_top3_days_pct": float(ordered[3:].mean()) if len(ordered) > 3 else None,
    }


def run(
    *,
    db_path: Path,
    price_dir: Path,
    hold_sessions: int,
    stops: list[float | None],
    cost_pct: float,
) -> dict[str, Any]:
    con = sqlite3.connect(db_path)
    try:
        positions = pd.read_sql_query(
            """
            SELECT v2_decision_id,session_date,upper(ticker) ticker,entry_price,pnl_pct_net,
                   strategy,market_regime
            FROM v2_learning_performance
            WHERE market='US' AND filled=1 AND entry_price>0
            """,
            con,
        )
    finally:
        con.close()
    fx = _fx_history(str(positions["session_date"].min()), "2026-12-31")
    output: list[dict[str, Any]] = []
    mismatch = 0
    for row in positions.to_dict("records"):
        path = price_dir / f"us_{row['ticker']}.csv"
        if not path.exists():
            continue
        bars = pd.read_csv(path)
        bars["date"] = pd.to_datetime(bars["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        indexes = bars.index[bars["date"].eq(str(row["session_date"]))].tolist()
        if not indexes or indexes[0] + hold_sessions >= len(bars):
            continue
        entry_idx = indexes[0]
        entry_price = float(row["entry_price"])
        entry_bar = bars.iloc[entry_idx]
        if not (entry_price >= float(entry_bar["low"]) * 0.95 and entry_price <= float(entry_bar["high"]) * 1.05):
            mismatch += 1
            continue
        entry_fx = fx.get(str(row["session_date"]))
        if not entry_fx:
            continue
        result = dict(row)
        for stop in stops:
            exit_price, exit_date = simulate_exit(
                bars,
                entry_idx=entry_idx,
                entry_price=entry_price,
                hold_sessions=hold_sessions,
                stop_pct=stop,
            )
            exit_fx = fx.get(exit_date)
            if not exit_fx:
                continue
            key = "no_stop" if stop is None else f"stop_{stop:g}pct"
            result[key] = ((exit_price / entry_price) * (exit_fx / entry_fx) - 1.0) * 100.0 - cost_pct
        output.append(result)
    frame = pd.DataFrame(output)
    columns = ["no_stop" if stop is None else f"stop_{stop:g}pct" for stop in stops]
    return {
        "input_filled": int(len(positions)),
        "price_consistent": int(len(frame)),
        "excluded_entry_price_mismatch": int(mismatch),
        "hold_sessions": hold_sessions,
        "cost_pct": cost_pct,
        "policies": {
            column: _metrics(frame, column, 20260710 + idx)
            for idx, column in enumerate(columns)
            if column in frame
        },
        "limitations": [
            "daily OHLC cannot establish intraday first-hit ordering beyond conservative stop rules",
            "41 live sessions remain too few for enforce authority",
            "counterfactual ignores portfolio slot contention and order slippage beyond the cost hurdle",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="US actual-entry fixed-hold Yahoo counterfactual")
    parser.add_argument("--db", default=str(ROOT / "data" / "ml" / "decisions.db"))
    parser.add_argument("--price-dir", default=str(ROOT / "data" / "price" / "us"))
    parser.add_argument("--hold-sessions", type=int, default=5)
    parser.add_argument("--stops", default="none,2.5,4,6")
    parser.add_argument("--cost-pct", type=float, default=0.50)
    args = parser.parse_args()
    stops = [None if value.strip().lower() == "none" else float(value) for value in args.stops.split(",")]
    report = run(
        db_path=Path(args.db),
        price_dir=Path(args.price_dir),
        hold_sessions=max(1, args.hold_sessions),
        stops=stops,
        cost_pct=args.cost_pct,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
