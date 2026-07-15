#!/usr/bin/env python3
from __future__ import annotations

"""Research orthogonal, low-turnover sleeves without touching live trading.

The lab deliberately uses a small, fixed family of rules.  Month-end signals
are shifted one month before they earn a return.  US assets are marked in KRW,
uninvested US sleeve capital is held in BIL, and ETF turnover is charged at the
configured US round-trip commission (0.50% by default).  Results are split at
2018-01-01 so the later sample is never used to fit a rule.
"""

import argparse
from dataclasses import dataclass
from datetime import date
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
FX = "KRW=X"
BIL = "BIL"
MULTI = ("SPY", "QQQ", "IWM", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC")
SECTORS = ("XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY")
ALL_SYMBOLS = tuple(dict.fromkeys((*MULTI, *SECTORS, BIL, FX)))


@dataclass(frozen=True)
class Spec:
    name: str
    assets: tuple[str, ...]
    method: str
    reserve: str
    thesis: str


def download_monthly(as_of: str, cache: Path) -> tuple[pd.DataFrame, str]:
    if cache.exists():
        frame = pd.read_csv(cache, index_col=0, parse_dates=True)
        if set(ALL_SYMBOLS).issubset(frame.columns):
            return frame.sort_index(), "cache"

    import yfinance as yf

    columns: dict[str, pd.Series] = {}
    end = pd.Timestamp(as_of) + pd.Timedelta(days=1)
    for symbol in ALL_SYMBOLS:
        interval = "1d" if symbol == FX else "1mo"
        raw = yf.download(
            symbol,
            start="2003-01-01",
            end=end,
            interval=interval,
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if raw.empty:
            raise RuntimeError(f"no data for {symbol}")
        close = raw["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = pd.to_numeric(close, errors="coerce")
        if symbol == FX:
            close = close.resample("MS").last()
        columns[symbol] = close
    frame = pd.concat(columns, axis=1).sort_index()
    frame.index = pd.to_datetime(frame.index).to_period("M").to_timestamp()
    frame = frame[~frame.index.duplicated(keep="last")]
    frame = frame[frame.index < pd.Timestamp(as_of).to_period("M").to_timestamp()]
    cache.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(cache, index_label="month")
    return frame, "yfinance_adjusted"


def specs() -> list[Spec]:
    return [
        Spec("QQQ_BUY_HOLD", ("QQQ",), "buy_hold", BIL, "KRW marked Nasdaq beta"),
        Spec("BIL_USD_RESERVE", (BIL,), "buy_hold", BIL, "USD T-bill reserve benchmark"),
        Spec("QQQ_SMA10_BIL", ("QQQ",), "single_trend", BIL, "QQQ above SMA10, otherwise BIL"),
        Spec("MULTI_EW_TREND_BIL", MULTI, "ew_trend", BIL, "Independent trend across nine asset classes"),
        Spec("MULTI_TOP3_MOM_BIL", MULTI, "top3", BIL, "Top-three positive 12m trends, otherwise BIL"),
        Spec("MULTI_INVOL_TREND_BIL", MULTI, "invol", BIL, "Trend assets sized by trailing inverse volatility"),
        Spec("SECTOR_EW_TREND_BIL", SECTORS, "ew_trend", BIL, "Independent trend across old US sector ETFs"),
        Spec("SECTOR_TOP3_MOM_BIL", SECTORS, "top3", BIL, "Top-three positive sector trends"),
    ]


def target_weights(panel: pd.DataFrame, spec: Spec) -> pd.DataFrame:
    assets = list(dict.fromkeys((*spec.assets, spec.reserve)))
    prices = panel[assets]
    weights = pd.DataFrame(0.0, index=panel.index, columns=assets)
    if spec.method == "buy_hold":
        weights[spec.assets[0]] = 1.0
        return weights

    signal_prices = panel[list(spec.assets)]
    sma10 = signal_prices.rolling(10, min_periods=10).mean()
    mom12 = signal_prices.pct_change(12, fill_method=None)
    eligible = (signal_prices > sma10) & (mom12 > 0)

    for idx in range(len(panel)):
        chosen = [asset for asset in spec.assets if bool(eligible.iloc[idx].get(asset, False))]
        if spec.method == "single_trend":
            if chosen:
                weights.iat[idx, weights.columns.get_loc(spec.assets[0])] = 1.0
            else:
                weights.iat[idx, weights.columns.get_loc(spec.reserve)] = 1.0
            continue
        if spec.method == "ew_trend":
            base = 1.0 / len(spec.assets)
            for asset in chosen:
                weights.iat[idx, weights.columns.get_loc(asset)] = base
            weights.iat[idx, weights.columns.get_loc(spec.reserve)] += 1.0 - base * len(chosen)
            continue
        if spec.method == "top3":
            ranked = sorted(chosen, key=lambda asset: float(mom12.iloc[idx][asset]), reverse=True)[:3]
            if ranked:
                for asset in ranked:
                    weights.iat[idx, weights.columns.get_loc(asset)] = 1.0 / len(ranked)
            else:
                weights.iat[idx, weights.columns.get_loc(spec.reserve)] = 1.0
            continue
        if spec.method == "invol":
            if chosen:
                trailing = signal_prices.pct_change(fill_method=None).rolling(12, min_periods=12).std()
                inverse = pd.Series({asset: 1.0 / float(trailing.iloc[idx][asset]) for asset in chosen})
                inverse = inverse.replace([np.inf, -np.inf], np.nan).dropna()
                if len(inverse):
                    raw = inverse / inverse.sum()
                    # A hard 35% cap prevents a single low-volatility bond ETF
                    # from becoming the entire sleeve.  Unused risk stays in BIL.
                    capped = raw.clip(upper=0.35)
                    for asset, weight in capped.items():
                        weights.iat[idx, weights.columns.get_loc(asset)] = float(weight)
                    weights.iat[idx, weights.columns.get_loc(spec.reserve)] += 1.0 - float(capped.sum())
                    continue
            weights.iat[idx, weights.columns.get_loc(spec.reserve)] = 1.0
            continue
        raise ValueError(spec.method)
    return weights


def simulate(panel: pd.DataFrame, spec: Spec, one_way_cost_pct: float) -> pd.DataFrame:
    assets = list(dict.fromkeys((*spec.assets, spec.reserve)))
    local = panel[assets].pct_change(fill_method=None)
    fx_ret = panel[FX].pct_change(fill_method=None)
    krw_returns = local.apply(lambda column: (1.0 + column) * (1.0 + fx_ret) - 1.0)
    signal = target_weights(panel, spec)
    held = signal.shift(1).fillna(0.0)
    gross = (held * krw_returns).sum(axis=1, min_count=1).fillna(0.0)
    turnover = held.diff().abs().sum(axis=1).fillna(held.abs().sum(axis=1))
    net = gross - turnover * one_way_cost_pct / 100.0
    output = pd.DataFrame({"gross_return": gross, "net_return": net, "turnover": turnover}, index=panel.index)
    for asset in assets:
        output[f"weight_{asset}"] = held[asset]
    valid = panel[[*assets, FX]].notna().all(axis=1).to_numpy()
    positions = np.flatnonzero(valid)
    if not len(positions):
        return output.iloc[0:0]
    return output.iloc[int(positions[0]) + 13 :]


def block_lcb(values: np.ndarray, seed: int, samples: int = 3000, block: int = 6) -> float | None:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 36:
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
    return float(np.quantile(means, 0.05) * 12.0 * 100.0)


def metrics(frame: pd.DataFrame, seed: int) -> dict[str, Any]:
    if frame.empty:
        return {"months": 0}
    values = frame.net_return.astype(float).to_numpy()
    # Include the initial 1.0 capital point.  Without it, a drawdown that
    # starts in the first measured month is silently understated.
    equity = np.concatenate(([1.0], np.cumprod(1.0 + values)))
    years = len(values) / 12.0
    peak = np.maximum.accumulate(equity)
    vol = float(np.std(values, ddof=0) * math.sqrt(12.0))
    ann_mean = float(np.mean(values) * 12.0)
    ex_top3 = np.sort(values)[::-1][3:]
    rolling = pd.Series(values).rolling(12).apply(lambda x: np.prod(1.0 + x) - 1.0, raw=True).dropna()
    return {
        "months": int(len(values)),
        "start": str(frame.index.min().date()),
        "end": str(frame.index.max().date()),
        "cagr_pct": float((equity[-1] ** (1.0 / years) - 1.0) * 100.0),
        "sharpe_rf0": float(ann_mean / vol) if vol > 0 else None,
        "max_drawdown_pct": float(np.min(equity / peak - 1.0) * 100.0),
        "annual_turnover": float(frame.turnover.mean() * 12.0),
        "positive_month_rate": float(np.mean(values > 0)),
        "annual_mean_ex_top3_pct": float(np.mean(ex_top3) * 12.0 * 100.0) if len(ex_top3) else None,
        "annual_mean_block_lcb_5pct": block_lcb(values, seed),
        "worst_rolling_12m_pct": float(rolling.min() * 100.0) if len(rolling) else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=str(date.today()))
    parser.add_argument("--output-dir", default=str(ROOT / "reports"))
    parser.add_argument("--one-way-cost-pct", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=20260715)
    args = parser.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    tag = args.as_of.replace("-", "")
    cache = out / f"strategy_frontier_prices_{tag}.csv"
    panel, source = download_monthly(args.as_of, cache)
    results: dict[str, Any] = {
        "as_of": args.as_of,
        "source": source,
        "contract": {
            "signal_lag": "month t signal -> month t+1 return",
            "currency": "all US assets including BIL marked in KRW",
            "one_way_cost_pct": args.one_way_cost_pct,
            "discovery_end": "2017-12-31",
            "oos_start": "2018-01-01",
        },
        "strategies": {},
    }
    ledger: list[pd.DataFrame] = []
    for idx, spec in enumerate(specs()):
        sim = simulate(panel, spec, args.one_way_cost_pct)
        stressed = simulate(panel, spec, args.one_way_cost_pct + 0.25)
        result = {
            "thesis": spec.thesis,
            "all": metrics(sim, args.seed + idx),
            "discovery": metrics(sim[sim.index < "2018-01-01"], args.seed + 100 + idx),
            "oos": metrics(sim[sim.index >= "2018-01-01"], args.seed + 200 + idx),
            "recent_2022_plus": metrics(sim[sim.index >= "2022-01-01"], args.seed + 300 + idx),
            "oos_cost_stress": metrics(stressed[stressed.index >= "2018-01-01"], args.seed + 400 + idx),
        }
        results["strategies"][spec.name] = result
        ledger.append(sim.assign(strategy=spec.name).reset_index(names="month"))
    (out / f"strategy_frontier_lab_{tag}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pd.concat(ledger, ignore_index=True).to_csv(out / f"strategy_frontier_ledger_{tag}.csv", index=False)
    for name, result in results["strategies"].items():
        oos = result["oos"]
        print(
            f"{name:26s} OOS n={oos.get('months', 0):3d} "
            f"CAGR={oos.get('cagr_pct', float('nan')):7.2f}% "
            f"Sharpe={oos.get('sharpe_rf0', float('nan')):5.2f} "
            f"MDD={oos.get('max_drawdown_pct', float('nan')):7.2f}% "
            f"LCB={oos.get('annual_mean_block_lcb_5pct')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
