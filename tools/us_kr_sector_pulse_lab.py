#!/usr/bin/env python3
from __future__ import annotations

"""No-lookahead US sector-close to KR sector-ETF pulse matrix.

This is a discovery lab, not a live strategy.  The full predeclared matrix is
kept in the output so the best-looking cell cannot be mistaken for a clean
out-of-sample result.
"""

import argparse
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PAIRS = {
    "SOXX": "091160.KS",  # semiconductors
    "XLV": "227550.KS",   # health care
    "XLF": "139220.KS",   # financials
    "ITA": "309230.KS",   # aerospace/defence
    "LIT": "305720.KS",   # batteries
}
BENCHMARK = "069500.KS"
INVERSE = "114800.KS"
KR_ROUND_TRIP_COST_PCT = 0.21
THRESHOLDS = (1.0, 1.5, 2.0)
HOLDS = (1, 3, 5)


def download(symbols: list[str], start: str, end: str, cache: Path) -> dict[str, pd.DataFrame]:
    if cache.exists():
        raw = pd.read_pickle(cache)
        if set(symbols).issubset(raw):
            return raw
    import yfinance as yf

    output: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frame = yf.Ticker(symbol).history(
            start=start,
            end=end,
            auto_adjust=True,
            actions=False,
        )
        if frame.empty:
            raise RuntimeError(f"no data for {symbol}")
        frame = frame[["Open", "Close"]].apply(pd.to_numeric, errors="coerce").dropna()
        frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
        output[symbol] = frame[~frame.index.duplicated(keep="last")].sort_index()
    cache.parent.mkdir(parents=True, exist_ok=True)
    pd.to_pickle(output, cache)
    return output


def next_after(index: pd.DatetimeIndex, stamp: pd.Timestamp) -> pd.Timestamp | None:
    position = int(index.searchsorted(stamp, side="right"))
    return index[position] if position < len(index) else None


def top_sector_signals(
    data: dict[str, pd.DataFrame], threshold: float
) -> list[tuple[pd.Timestamp, str, str, float]]:
    by_date: dict[pd.Timestamp, tuple[str, str, float]] = {}
    for leader, target in PAIRS.items():
        returns = data[leader].Close.pct_change() * 100.0
        for stamp, value in returns.items():
            if pd.isna(value) or float(value) < threshold:
                continue
            prior = by_date.get(stamp)
            if prior is None or float(value) > prior[2]:
                by_date[stamp] = (leader, target, float(value))
    return [(stamp, *by_date[stamp]) for stamp in sorted(by_date)]


def asset_return(
    frame: pd.DataFrame,
    signal_date: pd.Timestamp,
    hold: int,
    *,
    not_before: pd.Timestamp | None = None,
) -> tuple[pd.Timestamp, pd.Timestamp, float] | None:
    entry_date = next_after(frame.index, signal_date)
    if not_before is not None:
        eligible = frame.index[frame.index >= not_before]
        entry_date = eligible[0] if len(eligible) else None
    if entry_date is None:
        return None
    entry_pos = int(frame.index.get_loc(entry_date))
    exit_pos = entry_pos + hold - 1
    if exit_pos >= len(frame):
        return None
    exit_date = frame.index[exit_pos]
    net = (float(frame.iloc[exit_pos].Close) / float(frame.iloc[entry_pos].Open) - 1.0) * 100.0
    return entry_date, exit_date, net - KR_ROUND_TRIP_COST_PCT


def simulate(data: dict[str, pd.DataFrame], threshold: float, hold: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for signal_date, leader, target, signal_return in top_sector_signals(data, threshold):
        sector = asset_return(data[target], signal_date, hold)
        if sector is None:
            continue
        entry_date, exit_date, sector_net = sector
        benchmark = asset_return(data[BENCHMARK], signal_date, hold, not_before=entry_date)
        inverse = asset_return(data[INVERSE], signal_date, hold, not_before=entry_date)
        if benchmark is None or inverse is None:
            continue
        rows.append(
            {
                "threshold_pct": threshold,
                "hold_sessions": hold,
                "signal_date": str(signal_date.date()),
                "entry_date": str(entry_date.date()),
                "exit_date": str(exit_date.date()),
                "us_leader": leader,
                "kr_target": target,
                "leader_return_pct": signal_return,
                "sector_net_pct": sector_net,
                "benchmark_net_pct": benchmark[2],
                "sector_minus_benchmark_pct": sector_net - benchmark[2],
                "sector_inverse_50_50_net_pct": 0.5 * (sector_net + inverse[2]),
            }
        )
    return pd.DataFrame(rows)


def block_lcb(values: np.ndarray, seed: int, samples: int = 3000, block: int = 5) -> float | None:
    values = np.asarray(values, dtype=float)
    if len(values) < 25:
        return None
    rng = np.random.default_rng(seed)
    starts = np.arange(max(1, len(values) - block + 1))
    needed = int(math.ceil(len(values) / block))
    means = []
    for _ in range(samples):
        sample: list[float] = []
        for start in rng.choice(starts, needed, replace=True):
            sample.extend(values[int(start) : int(start) + block])
        means.append(float(np.mean(sample[: len(values)])))
    return float(np.quantile(means, 0.05))


def metrics(frame: pd.DataFrame, column: str, seed: int) -> dict[str, Any]:
    if frame.empty:
        return {"n": 0}
    values = frame[column].astype(float).to_numpy()
    gains = values[values > 0].sum()
    losses = -values[values < 0].sum()
    ordered = np.sort(values)[::-1]
    return {
        "n": int(len(values)),
        "mean_pct": float(np.mean(values)),
        "median_pct": float(np.median(values)),
        "win_rate": float(np.mean(values > 0)),
        "profit_factor": float(gains / losses) if losses > 0 else None,
        "sum_pct_units": float(np.sum(values)),
        "sum_ex_top3_pct_units": float(np.sum(ordered[3:])) if len(ordered) > 3 else None,
        "block_mean_lcb_5pct": block_lcb(values, seed),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=str(date.today()))
    parser.add_argument("--output-dir", default=str(ROOT / "reports"))
    parser.add_argument("--seed", type=int, default=20260715)
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    tag = args.as_of.replace("-", "")
    end = str((pd.Timestamp(args.as_of) + pd.Timedelta(days=1)).date())
    symbols = sorted(set(PAIRS) | set(PAIRS.values()) | {BENCHMARK, INVERSE})
    data = download(
        symbols,
        "2018-01-01",
        end,
        output_dir / f"us_kr_sector_pulse_prices_{tag}.pkl",
    )
    result: dict[str, Any] = {
        "as_of": args.as_of,
        "authority": "POST_SELECTION_SHADOW_ONLY",
        "contract": {
            "signal": "strongest of five fixed US sector ETFs at US close",
            "entry": "mapped KR sector ETF at first KR open strictly after signal date",
            "cost_pct": KR_ROUND_TRIP_COST_PCT,
            "matrix": {"thresholds": THRESHOLDS, "hold_sessions": HOLDS},
            "discovery_end": "2022-12-31",
            "oos_start": "2023-01-01",
        },
        "cells": {},
    }
    ledgers: list[pd.DataFrame] = []
    for threshold in THRESHOLDS:
        for hold in HOLDS:
            frame = simulate(data, threshold, hold)
            ledgers.append(frame)
            key = f"threshold_{threshold}_hold_{hold}"
            dates = pd.to_datetime(frame.signal_date) if len(frame) else pd.Series(dtype="datetime64[ns]")
            periods = {
                "all": frame,
                "discovery": frame[dates < "2023-01-01"],
                "oos": frame[dates >= "2023-01-01"],
            }
            result["cells"][key] = {
                name: {
                    column: metrics(subset, column, args.seed + hold)
                    for column in (
                        "sector_net_pct",
                        "benchmark_net_pct",
                        "sector_minus_benchmark_pct",
                        "sector_inverse_50_50_net_pct",
                    )
                }
                for name, subset in periods.items()
            }
    json_path = output_dir / f"us_kr_sector_pulse_lab_{tag}.json"
    csv_path = output_dir / f"us_kr_sector_pulse_ledger_{tag}.csv"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.concat(ledgers, ignore_index=True).to_csv(csv_path, index=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"WROTE {json_path}")
    print(f"WROTE {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
