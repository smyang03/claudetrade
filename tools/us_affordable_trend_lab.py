#!/usr/bin/env python3
from __future__ import annotations

"""Affordable US one-slot trend sleeve, research only.

The live-shaped contract rotates monthly between SCHG and BIL.  A signal made
with month t's completed close is applied only to month t+1.  Both ETF returns
are converted to KRW and turnover is charged on every switch.  The small,
pre-declared SMA/momentum neighbourhood is reported in full to avoid presenting
only the best historical parameter.
"""

import argparse
import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = ("SCHG", "BIL", "KRW=X")
SMA_WINDOWS = (8, 10, 12)
MOM_WINDOWS = (9, 12)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_panel(as_of: str, cache: Path) -> tuple[pd.DataFrame, str]:
    if cache.exists():
        cached = pd.read_csv(cache, index_col=0, parse_dates=True)
        if set(SYMBOLS).issubset(cached.columns):
            return cached.sort_index(), "cache"

    import yfinance as yf

    output: dict[str, pd.Series] = {}
    end = pd.Timestamp(as_of) + pd.Timedelta(days=1)
    for symbol in SYMBOLS:
        interval = "1d" if symbol == "KRW=X" else "1mo"
        raw = yf.download(
            symbol,
            start="2007-01-01",
            end=end,
            interval=interval,
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if raw.empty:
            raise RuntimeError(f"no adjusted history for {symbol}")
        close = raw["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = pd.to_numeric(close, errors="coerce")
        if symbol == "KRW=X":
            close = close.resample("MS").last()
        output[symbol] = close
    panel = pd.concat(output, axis=1).sort_index()
    panel.index = pd.to_datetime(panel.index).to_period("M").to_timestamp()
    panel = panel[~panel.index.duplicated(keep="last")]
    panel = panel[panel.index < pd.Timestamp(as_of).to_period("M").to_timestamp()]
    cache.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(cache, index_label="month")
    return panel, "yfinance_adjusted"


def target_weights(panel: pd.DataFrame, sma_window: int, mom_window: int) -> pd.DataFrame:
    risk = panel["SCHG"]
    eligible = (risk > risk.rolling(sma_window, min_periods=sma_window).mean()) & (
        risk.pct_change(mom_window, fill_method=None) > 0
    )
    weights = pd.DataFrame(index=panel.index, columns=["SCHG", "BIL"], dtype=float)
    weights["SCHG"] = eligible.astype(float)
    weights["BIL"] = 1.0 - weights["SCHG"]
    return weights


def simulate(
    panel: pd.DataFrame,
    *,
    sma_window: int,
    mom_window: int,
    one_way_cost_pct: float,
    mode: str = "trend",
) -> pd.DataFrame:
    local = panel[["SCHG", "BIL"]].pct_change(fill_method=None)
    fx = panel["KRW=X"].pct_change(fill_method=None)
    krw = local.apply(lambda column: (1.0 + column) * (1.0 + fx) - 1.0)
    if mode == "trend":
        signal = target_weights(panel, sma_window, mom_window)
    elif mode == "risk_buy_hold":
        signal = pd.DataFrame({"SCHG": 1.0, "BIL": 0.0}, index=panel.index)
    elif mode == "reserve_buy_hold":
        signal = pd.DataFrame({"SCHG": 0.0, "BIL": 1.0}, index=panel.index)
    else:
        raise ValueError(mode)
    held = signal.shift(1).fillna(0.0)
    gross = (held * krw).sum(axis=1, min_count=1).fillna(0.0)
    turnover = held.diff().abs().sum(axis=1).fillna(held.abs().sum(axis=1))
    net = gross - turnover * one_way_cost_pct / 100.0
    result = pd.DataFrame(
        {
            "gross_return": gross,
            "net_return": net,
            "turnover": turnover,
            "weight_SCHG": held["SCHG"],
            "weight_BIL": held["BIL"],
        },
        index=panel.index,
    )
    common = panel[["SCHG", "BIL", "KRW=X"]].notna().all(axis=1).to_numpy()
    positions = np.flatnonzero(common)
    if not len(positions):
        return result.iloc[0:0]
    warmup = max(sma_window, mom_window) + 1
    return result.iloc[int(positions[0]) + warmup :]


def block_lcb(values: np.ndarray, seed: int, samples: int = 5000, block: int = 6) -> float | None:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 36:
        return None
    rng = np.random.default_rng(seed)
    starts = np.arange(max(1, len(values) - block + 1))
    needed = int(math.ceil(len(values) / block))
    means = np.empty(samples, dtype=float)
    for idx in range(samples):
        sample: list[float] = []
        for start in rng.choice(starts, needed, replace=True):
            sample.extend(values[int(start) : int(start) + block].tolist())
        means[idx] = float(np.mean(sample[: len(values)]))
    return float(np.quantile(means, 0.05) * 12.0 * 100.0)


def metrics(frame: pd.DataFrame, seed: int) -> dict[str, Any]:
    if frame.empty:
        return {"months": 0}
    values = frame["net_return"].astype(float).to_numpy()
    equity = np.concatenate(([1.0], np.cumprod(1.0 + values)))
    peak = np.maximum.accumulate(equity)
    years = len(values) / 12.0
    annual_vol = float(np.std(values, ddof=0) * math.sqrt(12.0))
    annual_mean = float(np.mean(values) * 12.0)
    ex_top3 = np.sort(values)[::-1][3:]
    rolling = pd.Series(values).rolling(12).apply(lambda x: np.prod(1.0 + x) - 1.0, raw=True).dropna()
    return {
        "months": int(len(values)),
        "start": str(frame.index.min().date()),
        "end": str(frame.index.max().date()),
        "total_return_pct": float((equity[-1] - 1.0) * 100.0),
        "cagr_pct": float((equity[-1] ** (1.0 / years) - 1.0) * 100.0),
        "sharpe_rf0": float(annual_mean / annual_vol) if annual_vol > 0 else None,
        "max_drawdown_pct": float(np.min(equity / peak - 1.0) * 100.0),
        "annual_turnover": float(frame["turnover"].mean() * 12.0),
        "risk_on_month_rate": float(frame["weight_SCHG"].mean()),
        "annual_mean_ex_top3_pct": (
            float(np.mean(ex_top3) * 12.0 * 100.0) if len(ex_top3) else None
        ),
        "annual_mean_block_lcb_5pct": block_lcb(values, seed),
        "worst_rolling_12m_pct": float(rolling.min() * 100.0) if len(rolling) else None,
    }


def segments(frame: pd.DataFrame, seed: int) -> dict[str, Any]:
    return {
        "all": metrics(frame, seed),
        "discovery_pre_2018": metrics(frame[frame.index < "2018-01-01"], seed + 1),
        "oos_2018_plus": metrics(frame[frame.index >= "2018-01-01"], seed + 2),
        "recent_2022_plus": metrics(frame[frame.index >= "2022-01-01"], seed + 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Affordable SCHG/BIL monthly trend lab")
    parser.add_argument("--as-of", default=str(date.today()))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--one-way-cost-pct", type=float, default=0.25)
    parser.add_argument("--stress-one-way-cost-pct", type=float, default=0.50)
    parser.add_argument("--seed", type=int, default=20260715)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    tag = args.as_of.replace("-", "")
    cache = args.output_dir / f"us_affordable_trend_prices_{tag}.csv"
    panel, source = download_panel(args.as_of, cache)
    strategies: dict[str, Any] = {}
    ledger: list[pd.DataFrame] = []
    for idx, (sma_window, mom_window) in enumerate(
        (sma, mom) for sma in SMA_WINDOWS for mom in MOM_WINDOWS
    ):
        name = f"SCHG_SMA{sma_window}_MOM{mom_window}_BIL"
        base = simulate(
            panel,
            sma_window=sma_window,
            mom_window=mom_window,
            one_way_cost_pct=args.one_way_cost_pct,
        )
        stress = simulate(
            panel,
            sma_window=sma_window,
            mom_window=mom_window,
            one_way_cost_pct=args.stress_one_way_cost_pct,
        )
        strategies[name] = {
            "params": {"sma_months": sma_window, "momentum_months": mom_window},
            "base": segments(base, args.seed + idx * 10),
            "stress_oos_2018_plus": metrics(stress[stress.index >= "2018-01-01"], args.seed + idx * 10 + 9),
            "next_incomplete_month_signal": (
                "SCHG" if bool(target_weights(panel, sma_window, mom_window).iloc[-1]["SCHG"]) else "BIL"
            ),
        }
        ledger.append(base.assign(strategy=name).reset_index(names="month"))

    for idx, (name, mode) in enumerate((
        ("SCHG_BUY_HOLD", "risk_buy_hold"),
        ("BIL_BUY_HOLD", "reserve_buy_hold"),
    )):
        frame = simulate(
            panel,
            sma_window=10,
            mom_window=12,
            one_way_cost_pct=args.one_way_cost_pct,
            mode=mode,
        )
        strategies[name] = {"base": segments(frame, args.seed + 100 + idx)}
        ledger.append(frame.assign(strategy=name).reset_index(names="month"))

    central = strategies["SCHG_SMA10_MOM12_BIL"]
    neighbours = [
        strategy["base"]["oos_2018_plus"]
        for name, strategy in strategies.items()
        if name.startswith("SCHG_SMA")
    ]
    result = {
        "schema_version": "us_affordable_trend_lab_v1",
        "as_of": args.as_of,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authority": "SHADOW_RESEARCH_ONLY_NO_ORDER_EFFECT",
        "source": source,
        "contract": {
            "risk_asset": "SCHG",
            "reserve_asset": "BIL",
            "signal_lag": "completed month t close -> month t+1 holding",
            "currency": "KRW total return using KRW=X",
            "one_way_cost_pct": args.one_way_cost_pct,
            "stress_one_way_cost_pct": args.stress_one_way_cost_pct,
            "incomplete_current_month_excluded": True,
            "parameter_grid": {"sma_months": SMA_WINDOWS, "momentum_months": MOM_WINDOWS},
        },
        "manifest": {"price_cache": str(cache.resolve()), "price_cache_sha256": sha256(cache)},
        "central_contract": central,
        "neighbourhood_oos_range": {
            "variants": len(neighbours),
            "cagr_pct": [min(item["cagr_pct"] for item in neighbours), max(item["cagr_pct"] for item in neighbours)],
            "sharpe_rf0": [min(item["sharpe_rf0"] for item in neighbours), max(item["sharpe_rf0"] for item in neighbours)],
            "max_drawdown_pct": [min(item["max_drawdown_pct"] for item in neighbours), max(item["max_drawdown_pct"] for item in neighbours)],
            "block_lcb_pct": [
                min(item["annual_mean_block_lcb_5pct"] for item in neighbours),
                max(item["annual_mean_block_lcb_5pct"] for item in neighbours),
            ],
        },
        "strategies": strategies,
    }
    json_path = args.output_dir / f"us_affordable_trend_lab_{tag}.json"
    ledger_path = args.output_dir / f"us_affordable_trend_ledger_{tag}.csv"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    pd.concat(ledger, ignore_index=True).to_csv(ledger_path, index=False)
    print(json.dumps({
        "json": str(json_path),
        "ledger": str(ledger_path),
        "central_oos": central["base"]["oos_2018_plus"],
        "central_stress_oos": central["stress_oos_2018_plus"],
        "neighbourhood_oos_range": result["neighbourhood_oos_range"],
        "next_signal": central["next_incomplete_month_signal"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
