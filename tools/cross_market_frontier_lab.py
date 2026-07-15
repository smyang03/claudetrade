#!/usr/bin/env python3
from __future__ import annotations

"""No-lookahead US/KR lead-lag strategy screen.

US session d-1 is known before the Korean open on d.  Korean session d is
known before the US open on d.  Every trade enters at the receiving market's
open and exits at its close (or a fixed later close) with round-trip cost.
The small fixed rule set is evaluated before 2020 and from 2020 onward.
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


@dataclass(frozen=True)
class Rule:
    name: str
    receiving_market: str
    asset: str
    signal: str
    op: str
    threshold: float
    hold_sessions: int = 1


RULES = (
    Rule("KR200_CONT_AFTER_QQQ_UP1", "KR", "069500.KS", "qqq_ret", ">=", 1.0),
    Rule("KR200_REBOUND_AFTER_QQQ_DOWN1", "KR", "069500.KS", "qqq_ret", "<=", -1.0),
    Rule("KR_INVERSE_FADE_AFTER_QQQ_UP1", "KR", "114800.KS", "qqq_ret", ">=", 1.0),
    Rule("KR_SEMI_CONT_AFTER_SMH_UP1", "KR", "091160.KS", "smh_ret", ">=", 1.0),
    Rule("KR_SEMI_RESIDUAL_AFTER_SMH_MINUS_QQQ_UP1", "KR", "091160.KS", "smh_residual", ">=", 1.0),
    Rule("KR_SEMI_REBOUND_AFTER_SMH_DOWN1", "KR", "091160.KS", "smh_ret", "<=", -1.0),
    Rule("KR_SEMI_RESIDUAL_3D", "KR", "091160.KS", "smh_residual", ">=", 1.0, 3),
    Rule("US_QQQ_REBOUND_AFTER_KR200_DOWN1", "US", "QQQ", "kr200_ret", "<=", -1.0),
    Rule("US_EWY_CONT_AFTER_KR200_UP1", "US", "EWY", "kr200_ret", ">=", 1.0),
    Rule("US_EWY_REBOUND_AFTER_KR200_DOWN1", "US", "EWY", "kr200_ret", "<=", -1.0),
)


def download(symbols: list[str], start: str, end: str, cache: Path) -> dict[str, pd.DataFrame]:
    if cache.exists():
        raw = pd.read_pickle(cache)
        if set(symbols).issubset(raw):
            return raw
    import yfinance as yf

    output: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frame = yf.download(
            symbol,
            start=start,
            end=end,
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if frame.empty:
            raise RuntimeError(f"no data for {symbol}")
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = frame.columns.get_level_values(0)
        frame = frame[["Open", "High", "Low", "Close"]].apply(pd.to_numeric, errors="coerce").dropna()
        frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
        output[symbol] = frame[~frame.index.duplicated(keep="last")].sort_index()
    cache.parent.mkdir(parents=True, exist_ok=True)
    pd.to_pickle(output, cache)
    return output


def _last_before(index: pd.DatetimeIndex, stamp: pd.Timestamp) -> pd.Timestamp | None:
    pos = int(index.searchsorted(stamp, side="left")) - 1
    return index[pos] if pos >= 0 else None


def _last_on_or_before(index: pd.DatetimeIndex, stamp: pd.Timestamp) -> pd.Timestamp | None:
    pos = int(index.searchsorted(stamp, side="right")) - 1
    return index[pos] if pos >= 0 else None


def build_event_rows(data: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    qqq = data["QQQ"].copy()
    smh = data["SMH"].copy()
    kr200 = data["069500.KS"].copy()
    us_signal = pd.DataFrame(index=qqq.index)
    us_signal["qqq_ret"] = qqq.Close.pct_change() * 100.0
    us_signal["smh_ret"] = smh.Close.reindex(qqq.index).pct_change() * 100.0
    us_signal["smh_residual"] = us_signal.smh_ret - us_signal.qqq_ret
    kr_signal = pd.DataFrame(index=kr200.index)
    kr_signal["kr200_ret"] = kr200.Close.pct_change() * 100.0

    kr_rows: list[dict[str, Any]] = []
    for stamp in kr200.index:
        source = _last_before(us_signal.index, stamp)
        if source is not None:
            row = us_signal.loc[source].to_dict()
            row.update({"date": stamp, "source_date": source})
            kr_rows.append(row)
    us_rows: list[dict[str, Any]] = []
    for stamp in qqq.index:
        source = _last_on_or_before(kr_signal.index, stamp)
        if source is not None:
            row = kr_signal.loc[source].to_dict()
            row.update({"date": stamp, "source_date": source})
            us_rows.append(row)
    return pd.DataFrame(kr_rows).set_index("date"), pd.DataFrame(us_rows).set_index("date")


def trades_for_rule(rule: Rule, signals: pd.DataFrame, data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    asset = data[rule.asset]
    rows: list[dict[str, Any]] = []
    for stamp, signal_row in signals.iterrows():
        value = signal_row.get(rule.signal)
        if pd.isna(value):
            continue
        fire = float(value) >= rule.threshold if rule.op == ">=" else float(value) <= rule.threshold
        if not fire or stamp not in asset.index:
            continue
        pos = int(asset.index.get_loc(stamp))
        exit_pos = pos + rule.hold_sessions - 1
        if exit_pos >= len(asset):
            continue
        entry = float(asset.iloc[pos].Open)
        exit_price = float(asset.iloc[exit_pos].Close)
        gross = (exit_price / entry - 1.0) * 100.0
        cost = 0.21 if rule.receiving_market == "KR" else 0.50
        rows.append(
            {
                "strategy": rule.name,
                "market": rule.receiving_market,
                "asset": rule.asset,
                "signal_date": str(pd.Timestamp(signal_row.source_date).date()),
                "entry_date": str(stamp.date()),
                "exit_date": str(asset.index[exit_pos].date()),
                "signal_value": float(value),
                "gross_pct": gross,
                "net_pct": gross - cost,
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
        means.append(np.mean(sample[: len(values)]))
    return float(np.quantile(means, 0.05))


def metrics(frame: pd.DataFrame, seed: int) -> dict[str, Any]:
    if frame.empty:
        return {"n": 0}
    values = frame.net_pct.astype(float).to_numpy()
    wins = values[values > 0].sum()
    losses = -values[values < 0].sum()
    ordered = np.sort(values)[::-1]
    return {
        "n": int(len(values)),
        "mean_net_pct": float(np.mean(values)),
        "median_net_pct": float(np.median(values)),
        "win_rate": float(np.mean(values > 0)),
        "profit_factor": float(wins / losses) if losses > 0 else None,
        "sum_net_pct": float(np.sum(values)),
        "mean_ex_top3_pct": float(np.mean(ordered[3:])) if len(ordered) > 3 else None,
        "block_mean_lcb_5pct": block_lcb(values, seed),
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
    symbols = sorted({rule.asset for rule in RULES} | {"QQQ", "SMH", "069500.KS"})
    end = str((pd.Timestamp(args.as_of) + pd.Timedelta(days=1)).date())
    data = download(symbols, "2009-01-01", end, out / f"cross_market_prices_{tag}.pkl")
    kr_signals, us_signals = build_event_rows(data)
    result: dict[str, Any] = {
        "as_of": args.as_of,
        "contract": {
            "KR": "latest US session strictly before KR date; enter KR open",
            "US": "latest KR session on/before US date; enter US open",
            "cost_pct": {"KR": 0.21, "US": 0.50},
            "discovery_end": "2019-12-31",
            "oos_start": "2020-01-01",
        },
        "strategies": {},
    }
    ledgers: list[pd.DataFrame] = []
    for idx, rule in enumerate(RULES):
        signals = kr_signals if rule.receiving_market == "KR" else us_signals
        trades = trades_for_rule(rule, signals, data)
        trades["entry_date_dt"] = pd.to_datetime(trades.entry_date) if len(trades) else pd.Series(dtype="datetime64[ns]")
        result["strategies"][rule.name] = {
            "all": metrics(trades, args.seed + idx),
            "discovery": metrics(trades[trades.entry_date_dt < "2020-01-01"], args.seed + 100 + idx),
            "oos": metrics(trades[trades.entry_date_dt >= "2020-01-01"], args.seed + 200 + idx),
            "recent_2024_plus": metrics(trades[trades.entry_date_dt >= "2024-01-01"], args.seed + 300 + idx),
        }
        ledgers.append(trades.drop(columns=["entry_date_dt"], errors="ignore"))
    (out / f"cross_market_frontier_lab_{tag}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.concat(ledgers, ignore_index=True).to_csv(out / f"cross_market_frontier_trades_{tag}.csv", index=False)
    for name, periods in result["strategies"].items():
        d, o = periods["discovery"], periods["oos"]
        print(f"{name:48s} DISC n={d.get('n',0):4d} mean={d.get('mean_net_pct')} | OOS n={o.get('n',0):4d} mean={o.get('mean_net_pct')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
