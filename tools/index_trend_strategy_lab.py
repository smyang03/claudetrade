#!/usr/bin/env python3
from __future__ import annotations

"""Low-turnover index sleeve research with an explicit no-lookahead contract.

Signals are formed at month-end t and applied to month t+1.  US returns are
converted to KRW with KRW=X.  Turnover is charged at the live round-trip cost
model.  The tool writes the downloaded price panel so every result is replayable.
"""

import argparse
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
ROUND_TRIP_COST_PCT = {"US": 0.70, "KR": 0.21}


@dataclass(frozen=True)
class SleeveSpec:
    name: str
    market: str
    assets: tuple[str, ...]
    method: str
    thesis: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_panel(as_of: str, cache: Path) -> tuple[pd.DataFrame, str]:
    if cache.exists():
        frame = pd.read_csv(cache, index_col=0, parse_dates=True)
        fx = pd.to_numeric(frame.get("KRW=X"), errors="coerce")
        if fx.notna().any() and fx.dropna().between(500.0, 3000.0).all():
            return frame.sort_index(), "cache"
    import yfinance as yf

    symbols = ["SPY", "QQQ", "KRW=X", "069500.KS", "229200.KS"]
    columns: dict[str, pd.Series] = {}
    for symbol in symbols:
        interval = "1d" if symbol == "KRW=X" else "1mo"
        raw = yf.download(
            symbol,
            start="2000-01-01",
            end=pd.Timestamp(as_of) + pd.Timedelta(days=1),
            interval=interval,
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if raw.empty:
            raise RuntimeError(f"no monthly data for {symbol}")
        close = raw["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = pd.to_numeric(close, errors="coerce")
        if symbol == "KRW=X":
            close = close.resample("MS").last()
        columns[symbol] = close
    frame = pd.concat(columns, axis=1).sort_index()
    frame.index = pd.to_datetime(frame.index).to_period("M").to_timestamp()
    frame = frame[~frame.index.duplicated(keep="last")]
    # The current calendar month is incomplete and is never an outcome row.
    current_month = pd.Timestamp(as_of).to_period("M").to_timestamp()
    frame = frame[frame.index < current_month]
    cache.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(cache, index_label="month")
    return frame, "yfinance_adjusted_monthly"


def _asset_returns(panel: pd.DataFrame, market: str, assets: tuple[str, ...]) -> pd.DataFrame:
    local = panel[list(assets)].pct_change(fill_method=None)
    if market == "US":
        fx_return = panel["KRW=X"].pct_change(fill_method=None)
        for asset in assets:
            local[asset] = (1.0 + local[asset]) * (1.0 + fx_return) - 1.0
    return local


def target_weights(panel: pd.DataFrame, spec: SleeveSpec) -> pd.DataFrame:
    prices = panel[list(spec.assets)].copy()
    weights = pd.DataFrame(0.0, index=prices.index, columns=spec.assets)
    if spec.method == "buy_hold":
        weights.iloc[:, 0] = 1.0
        return weights
    sma10 = prices.rolling(10, min_periods=10).mean()
    mom12 = prices.pct_change(12)
    if spec.method == "sma10_cash":
        weights.iloc[:, 0] = (prices.iloc[:, 0] > sma10.iloc[:, 0]).astype(float)
        return weights
    if spec.method == "sma10_vol12":
        asset = spec.assets[0]
        annual_vol = prices[asset].pct_change(fill_method=None).rolling(12, min_periods=12).std() * math.sqrt(12.0)
        exposure = (0.12 / annual_vol.replace(0, np.nan)).clip(lower=0.0, upper=1.0).fillna(0.0)
        weights[asset] = exposure * (prices[asset] > sma10[asset]).astype(float)
        return weights
    if spec.method == "dual_momentum":
        for idx in range(len(prices)):
            eligible = [
                asset
                for asset in spec.assets
                if pd.notna(mom12.iloc[idx][asset])
                and mom12.iloc[idx][asset] > 0
                and prices.iloc[idx][asset] > sma10.iloc[idx][asset]
            ]
            if eligible:
                selected = max(eligible, key=lambda asset: float(mom12.iloc[idx][asset]))
                weights.iat[idx, weights.columns.get_loc(selected)] = 1.0
        return weights
    raise ValueError(f"unknown method: {spec.method}")


def simulate(panel: pd.DataFrame, spec: SleeveSpec) -> pd.DataFrame:
    returns = _asset_returns(panel, spec.market, spec.assets)
    signal_weights = target_weights(panel, spec)
    held_weights = signal_weights.shift(1).fillna(0.0)  # month t signal -> month t+1 return
    gross = (held_weights * returns).sum(axis=1, min_count=1).fillna(0.0)
    turnover = held_weights.diff().abs().sum(axis=1).fillna(held_weights.abs().sum(axis=1))
    one_way_cost = ROUND_TRIP_COST_PCT[spec.market] / 2.0 / 100.0
    net = gross - turnover * one_way_cost
    output = pd.DataFrame(
        {
            "gross_return": gross,
            "net_return": net,
            "turnover": turnover,
            "invested": held_weights.abs().sum(axis=1).clip(upper=1.0),
        },
        index=panel.index,
    )
    for asset in spec.assets:
        output[f"weight_{asset}"] = held_weights[asset]
    required = list(spec.assets) + (["KRW=X"] if spec.market == "US" else [])
    common = panel[required].notna().all(axis=1).to_numpy()
    positions = np.flatnonzero(common)
    if not len(positions):
        return output.iloc[0:0].copy()
    start = int(positions[0]) + 13
    return output.iloc[start:].copy()


def block_lcb(values: np.ndarray, *, seed: int, block: int = 6, samples: int = 3000) -> float | None:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 24:
        return None
    rng = np.random.default_rng(seed)
    starts = np.arange(max(1, len(values) - block + 1))
    output = np.empty(samples, dtype=float)
    needed = int(math.ceil(len(values) / block))
    for idx in range(samples):
        sample: list[float] = []
        for start in rng.choice(starts, size=needed, replace=True):
            sample.extend(values[int(start): int(start) + block].tolist())
        output[idx] = float(np.mean(sample[: len(values)]))
    return float(np.quantile(output, 0.05) * 12.0 * 100.0)


def metrics(frame: pd.DataFrame, *, seed: int) -> dict[str, Any]:
    if frame.empty:
        return {"months": 0}
    values = frame["net_return"].astype(float).to_numpy()
    equity = np.cumprod(1.0 + values)
    years = len(values) / 12.0
    cagr = (equity[-1] ** (1.0 / years) - 1.0) if years > 0 and equity[-1] > 0 else -1.0
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1.0
    annual_vol = float(np.std(values, ddof=0) * math.sqrt(12.0))
    annual_mean = float(np.mean(values) * 12.0)
    ordered = np.sort(values)[::-1]
    ex_top3 = ordered[3:] if len(ordered) > 3 else np.array([], dtype=float)
    rolling12 = pd.Series(values).rolling(12).apply(lambda x: np.prod(1.0 + x) - 1.0, raw=True).dropna()
    return {
        "months": int(len(values)),
        "start": str(frame.index.min().date()),
        "end": str(frame.index.max().date()),
        "cagr_pct": float(cagr * 100.0),
        "total_return_pct": float((equity[-1] - 1.0) * 100.0),
        "annual_vol_pct": annual_vol * 100.0,
        "sharpe_rf0": float(annual_mean / annual_vol) if annual_vol > 0 else None,
        "max_drawdown_pct": float(np.min(drawdown) * 100.0),
        "positive_month_rate": float(np.mean(values > 0)),
        "invested_month_rate": float(frame["invested"].mean()),
        "annual_turnover": float(frame["turnover"].mean() * 12.0),
        "annual_mean_block_lcb_5pct": block_lcb(values, seed=seed),
        "annual_mean_ex_top3_months_pct": float(np.mean(ex_top3) * 12.0 * 100.0) if len(ex_top3) else None,
        "worst_rolling_12m_pct": float(rolling12.min() * 100.0) if len(rolling12) else None,
        "positive_rolling_12m_rate": float((rolling12 > 0).mean()) if len(rolling12) else None,
    }


def specs() -> list[SleeveSpec]:
    return [
        SleeveSpec("US_SPY_BUY_HOLD", "US", ("SPY",), "buy_hold", "US beta benchmark in KRW"),
        SleeveSpec("US_QQQ_BUY_HOLD", "US", ("QQQ",), "buy_hold", "NASDAQ100 beta benchmark in KRW"),
        SleeveSpec("US_SPY_SMA10_CASH", "US", ("SPY",), "sma10_cash", "SPY above ten-month SMA, otherwise KRW cash"),
        SleeveSpec("US_QQQ_SMA10_CASH", "US", ("QQQ",), "sma10_cash", "QQQ above ten-month SMA, otherwise KRW cash"),
        SleeveSpec("US_QQQ_SMA10_VOL12", "US", ("QQQ",), "sma10_vol12", "QQQ trend with a fixed 12% trailing-volatility target"),
        SleeveSpec("US_SPY_QQQ_DUAL_MOM", "US", ("SPY", "QQQ"), "dual_momentum", "Hold the stronger positive 12-month trend, otherwise cash"),
        SleeveSpec("KR_KODEX200_BUY_HOLD", "KR", ("069500.KS",), "buy_hold", "Tradable KOSPI200 ETF benchmark"),
        SleeveSpec("KR_KQ150_BUY_HOLD", "KR", ("229200.KS",), "buy_hold", "Tradable KOSDAQ150 ETF benchmark"),
        SleeveSpec("KR_KODEX200_SMA10_CASH", "KR", ("069500.KS",), "sma10_cash", "KOSPI200 ETF above ten-month SMA, otherwise cash"),
        SleeveSpec("KR_KODEX200_SMA10_VOL12", "KR", ("069500.KS",), "sma10_vol12", "KOSPI200 trend with a fixed 12% trailing-volatility target"),
        SleeveSpec("KR_200_KQ150_DUAL_MOM", "KR", ("069500.KS", "229200.KS"), "dual_momentum", "Rotate between KOSPI200/KOSDAQ150 positive trends, otherwise cash"),
    ]


def verdict(all_metrics: dict[str, Any], discovery: dict[str, Any], oos: dict[str, Any]) -> str:
    if int(oos.get("months", 0)) < 48:
        return "INSUFFICIENT_LONG_OOS"
    passed = (
        float(all_metrics.get("cagr_pct", -999)) > 0
        and float(oos.get("cagr_pct", -999)) > 0
        and float(oos.get("sharpe_rf0") or 0) >= 0.50
        and float(oos.get("annual_mean_ex_top3_months_pct") or -999) > 0
        and float(oos.get("annual_mean_block_lcb_5pct") or -999) > 0
    )
    if not passed:
        return "RESEARCH_ONLY"
    discovery_lcb = discovery.get("annual_mean_block_lcb_5pct")
    if discovery_lcb is not None and float(discovery_lcb) <= 0:
        return "SHADOW_READY_REGIME_DEPENDENT"
    return "SHADOW_READY"


def main() -> int:
    parser = argparse.ArgumentParser(description="Index trend/dual-momentum sleeve lab")
    parser.add_argument("--as-of", default=str(date.today()))
    parser.add_argument("--output-dir", default=str(ROOT / "reports"))
    parser.add_argument("--seed", type=int, default=20260715)
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    tag = args.as_of.replace("-", "")
    cache = output / f"index_trend_prices_{tag}.csv"
    panel, source = download_panel(args.as_of, cache)
    rows: list[pd.DataFrame] = []
    results: dict[str, Any] = {}
    for idx, spec in enumerate(specs()):
        simulation = simulate(panel, spec)
        simulation = simulation.assign(strategy=spec.name, market=spec.market)
        rows.append(simulation.reset_index(names="month"))
        discovery = simulation[simulation.index < "2020-01-01"]
        oos = simulation[simulation.index >= "2020-01-01"]
        recent = simulation[simulation.index >= "2024-01-01"]
        all_metrics = metrics(simulation, seed=args.seed + idx)
        discovery_metrics = metrics(discovery, seed=args.seed + 200 + idx)
        oos_metrics = metrics(oos, seed=args.seed + 100 + idx)
        results[spec.name] = {
            "contract": spec.__dict__,
            "all": all_metrics,
            "discovery_pre_2020": discovery_metrics,
            "oos_2020_plus": oos_metrics,
            "recent_2024_plus": metrics(recent, seed=args.seed + 300 + idx),
            "verdict": (
                "BENCHMARK"
                if spec.method == "buy_hold"
                else verdict(all_metrics, discovery_metrics, oos_metrics)
            ),
        }
    ledger = pd.concat(rows, ignore_index=True)
    ledger_path = output / f"index_trend_ledger_{tag}.csv"
    json_path = output / f"index_trend_strategy_lab_{tag}.json"
    ledger.to_csv(ledger_path, index=False)
    report = {
        "schema_version": "index_trend_strategy_lab_v1",
        "as_of": args.as_of,
        "authority": "SHADOW_RESEARCH_ONLY_NO_ORDER_EFFECT",
        "contract": {
            "signal_lag": "month t close determines month t+1 holdings",
            "us_currency": "KRW total return using KRW=X",
            "cost_pct_round_trip": ROUND_TRIP_COST_PCT,
            "cash_return": 0.0,
            "oos": "2020-01 onward",
            "current_incomplete_month_excluded": True,
        },
        "manifest": {
            "price_cache": str(cache.resolve()),
            "price_cache_sha256": _sha256(cache),
            "source": source,
        },
        "results": results,
    }
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(json_path),
                "ledger": str(ledger_path),
                "verdicts": {name: result["verdict"] for name, result in results.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
