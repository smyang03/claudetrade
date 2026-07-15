#!/usr/bin/env python3
from __future__ import annotations

"""Integrated KRW investor core research; strictly SHADOW_ONLY.

Target allocation (100%):
  16% QUAL, 16% MTUM, 16% QQQ,
  32% diversified US inverse-volatility trend sleeve,
  10% KODEX momentum, 10% KODEX quality/blue-chip trend sleeve.

The Korean factor sleeve moves an ineligible half to KODEX short bonds.  The
US trend sleeve moves unused risk to BIL.  Signals observed at month end t are
held only for month t+1.  Portfolio turnover is calculated after allowing the
prior month's holdings to drift, rather than from target-to-target differences.
"""

import argparse
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
KR_BOND = "153130.KS"
KR_MOM = "275280.KS"
KR_QUALITY = "275300.KS"
MULTI = ("SPY", "QQQ", "IWM", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC")
US_ASSETS = tuple(dict.fromkeys(("QUAL", "MTUM", "QQQ", *MULTI, BIL)))
KR_ASSETS = (KR_MOM, KR_QUALITY, KR_BOND)
ALL_SYMBOLS = (*US_ASSETS, *KR_ASSETS, FX)


def download_monthly(as_of: str, cache: Path) -> tuple[pd.DataFrame, str]:
    if cache.exists():
        frame = pd.read_csv(cache, index_col=0, parse_dates=True)
        if set(ALL_SYMBOLS).issubset(frame.columns):
            return frame.sort_index(), "cache"
    import yfinance as yf

    data: dict[str, pd.Series] = {}
    end = pd.Timestamp(as_of) + pd.Timedelta(days=1)
    for symbol in ALL_SYMBOLS:
        raw = yf.download(
            symbol,
            start="2013-01-01",
            end=end,
            interval="1d" if symbol == FX else "1mo",
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
        data[symbol] = close
    panel = pd.concat(data, axis=1).sort_index()
    panel.index = pd.to_datetime(panel.index).to_period("M").to_timestamp()
    panel = panel[~panel.index.duplicated(keep="last")]
    panel = panel[panel.index < pd.Timestamp(as_of).to_period("M").to_timestamp()]
    cache.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(cache, index_label="month")
    return panel, "yfinance_adjusted"


def _capped_inverse_vol(values: pd.Series, cap: float) -> pd.Series:
    inverse = (1.0 / values).replace([np.inf, -np.inf], np.nan).dropna()
    if inverse.empty:
        return inverse
    raw = inverse / inverse.sum()
    return raw.clip(upper=cap)


def signal_weights(panel: pd.DataFrame, *, sma_months: int = 10, momentum_months: int = 12, cap: float = 0.35) -> pd.DataFrame:
    columns = list(dict.fromkeys((*US_ASSETS, *KR_ASSETS)))
    weights = pd.DataFrame(0.0, index=panel.index, columns=columns)
    # Always-on low-turnover factor/growth block: 48% total.
    weights["QUAL"] = 0.16
    weights["MTUM"] = 0.16
    weights["QQQ"] = 0.16

    multi_prices = panel[list(MULTI)]
    multi_eligible = (
        (multi_prices > multi_prices.rolling(sma_months, min_periods=sma_months).mean())
        & (multi_prices.pct_change(momentum_months, fill_method=None) > 0)
    )
    multi_vol = multi_prices.pct_change(fill_method=None).rolling(12, min_periods=12).std()
    for idx in range(len(panel)):
        eligible = [asset for asset in MULTI if bool(multi_eligible.iloc[idx][asset])]
        sleeve = _capped_inverse_vol(multi_vol.iloc[idx][eligible], cap) if eligible else pd.Series(dtype=float)
        allocated = 0.0
        for asset, raw_weight in sleeve.items():
            weight = 0.32 * float(raw_weight)
            weights.iat[idx, weights.columns.get_loc(asset)] += weight
            allocated += weight
        weights.iat[idx, weights.columns.get_loc(BIL)] += 0.32 - allocated

    kr_prices = panel[[KR_MOM, KR_QUALITY]]
    kr_eligible = (
        (kr_prices > kr_prices.rolling(sma_months, min_periods=sma_months).mean())
        & (kr_prices.pct_change(momentum_months, fill_method=None) > 0)
    )
    for idx in range(len(panel)):
        allocated = 0.0
        for asset in (KR_MOM, KR_QUALITY):
            if bool(kr_eligible.iloc[idx][asset]):
                weights.iat[idx, weights.columns.get_loc(asset)] = 0.10
                allocated += 0.10
        weights.iat[idx, weights.columns.get_loc(KR_BOND)] = 0.20 - allocated
    return weights


def asset_returns_krw(panel: pd.DataFrame) -> pd.DataFrame:
    output = pd.DataFrame(index=panel.index)
    fx_return = panel[FX].pct_change(fill_method=None)
    for asset in US_ASSETS:
        local = panel[asset].pct_change(fill_method=None)
        output[asset] = (1.0 + local) * (1.0 + fx_return) - 1.0
    for asset in KR_ASSETS:
        output[asset] = panel[asset].pct_change(fill_method=None)
    return output


def simulate(
    panel: pd.DataFrame,
    *,
    us_one_way_cost_pct: float = 0.25,
    kr_one_way_cost_pct: float = 0.105,
    annual_drag_pct: float = 0.0,
) -> pd.DataFrame:
    signals = signal_weights(panel)
    targets = signals.shift(1).fillna(0.0)  # no lookahead
    returns = asset_returns_krw(panel)
    output: list[dict[str, Any]] = []
    end_weights = pd.Series(0.0, index=targets.columns)
    for stamp in targets.index:
        target = targets.loc[stamp].fillna(0.0)
        asset_return = returns.loc[stamp].reindex(target.index).fillna(0.0)
        if end_weights.sum() > 0:
            pretrade = end_weights / end_weights.sum()
        else:
            pretrade = pd.Series(0.0, index=target.index)
        delta = (target - pretrade).abs()
        us_turnover = float(delta.reindex(US_ASSETS).fillna(0.0).sum())
        kr_turnover = float(delta.reindex(KR_ASSETS).fillna(0.0).sum())
        cost = us_turnover * us_one_way_cost_pct / 100.0 + kr_turnover * kr_one_way_cost_pct / 100.0
        gross = float((target * asset_return).sum())
        net = gross - cost - annual_drag_pct / 100.0 / 12.0
        grown = target * (1.0 + asset_return)
        end_weights = grown / grown.sum() if grown.sum() > 0 else target.copy()
        row: dict[str, Any] = {
            "month": stamp,
            "gross_return": gross,
            "net_return": net,
            "us_turnover": us_turnover,
            "kr_turnover": kr_turnover,
            "cost_return": cost,
        }
        row.update({f"weight_{asset}": float(target[asset]) for asset in target.index})
        output.append(row)
    frame = pd.DataFrame(output).set_index("month")
    valid = panel[[*ALL_SYMBOLS]].notna().all(axis=1).to_numpy()
    positions = np.flatnonzero(valid)
    if not len(positions):
        return frame.iloc[0:0]
    return frame.iloc[int(positions[0]) + 13 :]


def block_lcb(values: np.ndarray, seed: int, block: int = 6, samples: int = 5000) -> float | None:
    values = np.asarray(values, dtype=float)
    if len(values) < 36:
        return None
    rng = np.random.default_rng(seed)
    starts = np.arange(len(values) - block + 1)
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
    equity = np.concatenate(([1.0], np.cumprod(1.0 + values)))
    years = len(values) / 12.0
    vol = float(np.std(values, ddof=0) * math.sqrt(12.0))
    ordered = np.sort(values)[::-1][3:]
    rolling = pd.Series(values).rolling(12).apply(lambda x: np.prod(1.0 + x) - 1.0, raw=True).dropna()
    return {
        "months": int(len(values)),
        "total_return_pct": float((equity[-1] - 1.0) * 100.0),
        "cagr_pct": float((equity[-1] ** (1.0 / years) - 1.0) * 100.0) if len(values) >= 12 else None,
        "sharpe_rf0": float(np.mean(values) * 12.0 / vol) if vol > 0 else None,
        "max_drawdown_pct": float(np.min(equity / np.maximum.accumulate(equity) - 1.0) * 100.0),
        "worst_rolling_12m_pct": float(rolling.min() * 100.0) if len(rolling) else None,
        "annual_mean_ex_top3_pct": float(np.mean(ordered) * 12.0 * 100.0) if len(ordered) else None,
        "annual_mean_block_lcb_5pct": block_lcb(values, seed),
        "annual_us_turnover": float(frame.us_turnover.mean() * 12.0),
        "annual_kr_turnover": float(frame.kr_turnover.mean() * 12.0),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=str(date.today()))
    parser.add_argument("--output-dir", default=str(ROOT / "reports"))
    parser.add_argument("--seed", type=int, default=20260715)
    args = parser.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    tag = args.as_of.replace("-", "")
    panel, source = download_monthly(args.as_of, out / f"integrated_core_prices_{tag}.csv")
    base = simulate(panel)
    stressed = simulate(panel, us_one_way_cost_pct=0.50, kr_one_way_cost_pct=0.25, annual_drag_pct=0.50)
    result = {
        "as_of": args.as_of,
        "authority": "SHADOW_ONLY",
        "source": source,
        "contract": {
            "signal_lag": "month t signal -> month t+1 return",
            "weights": {"US_QUAL_MOM": 0.32, "US_MULTI_TREND": 0.32, "US_QQQ": 0.16, "KR_FACTOR_TREND": 0.20},
            "base_one_way_cost_pct": {"US": 0.25, "KR": 0.105},
            "stress": "US/KR one-way 0.50/0.25% plus 0.50% annual tax/slippage drag",
        },
        "base": {
            "discovery_2018_2021": metrics(base[(base.index >= "2018-01-01") & (base.index < "2022-01-01")], args.seed),
            "oos_2022_2025": metrics(base[(base.index >= "2022-01-01") & (base.index < "2026-01-01")], args.seed + 1),
            "forward_2026_ytd": metrics(base[base.index >= "2026-01-01"], args.seed + 6),
            "all_2018_plus": metrics(base[base.index >= "2018-01-01"], args.seed + 2),
        },
        "stress": {
            "discovery_2018_2021": metrics(stressed[(stressed.index >= "2018-01-01") & (stressed.index < "2022-01-01")], args.seed + 3),
            "oos_2022_2025": metrics(stressed[(stressed.index >= "2022-01-01") & (stressed.index < "2026-01-01")], args.seed + 4),
            "forward_2026_ytd": metrics(stressed[stressed.index >= "2026-01-01"], args.seed + 7),
            "all_2018_plus": metrics(stressed[stressed.index >= "2018-01-01"], args.seed + 5),
        },
    }
    (out / f"integrated_core_strategy_lab_{tag}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    base.reset_index().to_csv(out / f"integrated_core_ledger_{tag}.csv", index=False)
    for contract in ("base", "stress"):
        print(contract.upper())
        for period, values in result[contract].items():
            print(period, json.dumps(values, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
