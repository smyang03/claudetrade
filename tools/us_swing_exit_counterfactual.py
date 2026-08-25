from __future__ import annotations

import argparse
from bisect import bisect_right
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _block_lcb(values: np.ndarray, *, seed: int = 20260711) -> float | None:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 10:
        return None
    rng = np.random.default_rng(seed)
    block = min(5, len(values))
    starts = np.arange(max(1, len(values) - block + 1))
    means = []
    for _ in range(2000):
        sampled: list[float] = []
        while len(sampled) < len(values):
            start = int(rng.choice(starts))
            sampled.extend(values[start:start + block].tolist())
        means.append(float(np.mean(sampled[:len(values)])))
    return float(np.quantile(means, 0.05))


def _metrics(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    work = frame.dropna(subset=[column]).copy()
    if work.empty:
        return {"rows": 0, "sessions": 0}
    daily = work.groupby("session_date", sort=True)[column].mean()
    values = daily.to_numpy(dtype=float)
    positive = float(values[values > 0].sum())
    negative = float(-values[values < 0].sum())
    ordered = np.sort(values)[::-1]
    by_year = {}
    for year, group in work.groupby(work["session_date"].astype(str).str[:4]):
        year_daily = group.groupby("session_date")[column].mean().to_numpy(dtype=float)
        year_pos = float(year_daily[year_daily > 0].sum())
        year_neg = float(-year_daily[year_daily < 0].sum())
        by_year[str(year)] = {
            "sessions": int(len(year_daily)),
            "mean_net_pct": float(year_daily.mean()),
            "profit_factor": float(year_pos / year_neg) if year_neg > 0 else None,
            "win_rate": float((year_daily > 0).mean()),
            "block_lcb_pct": _block_lcb(year_daily),
        }
    return {
        "rows": int(len(work)),
        "sessions": int(len(daily)),
        "mean_net_pct": float(values.mean()),
        "median_net_pct": float(np.median(values)),
        "profit_factor": float(positive / negative) if negative > 0 else None,
        "win_rate": float((values > 0).mean()),
        "p10_net_pct": float(np.quantile(values, 0.10)),
        "worst_net_pct": float(values.min()),
        "block_lcb_pct": _block_lcb(values),
        "ex_top3_days_pct": float(ordered[3:].mean()) if len(ordered) > 3 else None,
        "by_year": by_year,
    }


def contract_verdict(subsets: dict[str, Any]) -> dict[str, Any]:
    rank1 = dict(subsets.get("rank1") or {})
    rejected = dict(rank1.get("live_tp12_sl6_conservative") or {})
    recommended = dict(rank1.get("tp12_sl25") or {})
    year_2025 = dict((recommended.get("by_year") or {}).get("2025") or {})
    passed = bool(
        float(recommended.get("mean_net_pct") or -999) > 0.25
        and float(recommended.get("profit_factor") or 0) >= 1.20
        and float(recommended.get("block_lcb_pct") or -999) > -0.25
        and float(recommended.get("ex_top3_days_pct") or -999) > 0
        and float(year_2025.get("mean_net_pct") or -999) > 0
        and float(year_2025.get("profit_factor") or 0) >= 1.0
    )
    return {
        "passed": passed,
        "selected_contract": "tp12_sl25",
        "selected_metrics": {
            key: recommended.get(key)
            for key in ("mean_net_pct", "profit_factor", "block_lcb_pct", "ex_top3_days_pct")
        },
        "selected_2025": {
            key: year_2025.get(key) for key in ("mean_net_pct", "profit_factor")
        },
        "rejected_contract": "tp12_sl6",
        "rejected_metrics": {
            key: rejected.get(key)
            for key in ("mean_net_pct", "profit_factor", "block_lcb_pct", "ex_top3_days_pct")
        },
    }


class FxLookup:
    def __init__(self, frame: pd.DataFrame) -> None:
        work = frame.copy()
        work["date"] = work["date"].astype(str)
        work["usdkrw"] = pd.to_numeric(work["usdkrw"], errors="coerce")
        work = work.dropna(subset=["date", "usdkrw"]).sort_values("date")
        self.dates = work["date"].tolist()
        self.values = work["usdkrw"].astype(float).tolist()

    def get(self, date: str) -> float | None:
        idx = bisect_right(self.dates, str(date)) - 1
        return self.values[idx] if idx >= 0 else None


def _load_path(price_dir: Path, ticker: str) -> pd.DataFrame:
    path = price_dir / f"us_{ticker.upper()}.csv"
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path)
    frame.columns = [str(column).lower() for column in frame.columns]
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date")


def simulate_exit(
    bars: pd.DataFrame,
    *,
    entry_price: float,
    tp_pct: float | None,
    sl_pct: float | None,
    tie_break: str,
    be_lock_trigger_pct: float | None = None,
) -> tuple[str, float, str]:
    """be_lock_trigger_pct(2026-08-25, %단위): 봉우리가 트리거 이상 도달하면 손절선을
    본전으로 올린다. 일봉 근사는 **전일까지의 봉우리**로만 손절선을 갱신한다(당일
    고가→당일 이탈 순서 모호성 배제 — 라이브 분봉 대비 보수). None/0이면 기존 동작."""
    if bars.empty:
        raise ValueError("price_path_missing")
    tp_price = entry_price * (1.0 + float(tp_pct)) if tp_pct is not None else None
    sl_price = entry_price * (1.0 - float(sl_pct)) if sl_pct is not None else None
    be_trigger = float(be_lock_trigger_pct or 0.0)
    stop_price = sl_price
    peak = entry_price
    for idx, row in enumerate(bars.itertuples(index=False)):
        opened = float(row.open)
        high = float(row.high)
        low = float(row.low)
        date = str(row.date)
        be_stop_active = stop_price is not None and sl_price is not None and stop_price > sl_price
        if idx > 0:
            if stop_price is not None and opened <= stop_price:
                return date, opened, ("BE_GAP" if be_stop_active else "SL_GAP")
            if tp_price is not None and opened >= tp_price:
                return date, opened, "TP_GAP"
        hit_sl = stop_price is not None and low <= stop_price
        hit_tp = tp_price is not None and high >= tp_price
        if hit_sl and hit_tp:
            if tie_break == "tp_first":
                return date, float(tp_price), "BOTH_TP_FIRST"
            return date, float(stop_price), ("BE" if be_stop_active else "BOTH_SL_FIRST")
        if hit_sl:
            return date, float(stop_price), ("BE" if be_stop_active else "SL")
        if hit_tp:
            return date, float(tp_price), "TP"
        peak = max(peak, high)
        if be_trigger > 0 and entry_price > 0 and (peak / entry_price - 1.0) * 100.0 >= be_trigger:
            stop_price = max(stop_price or entry_price, entry_price)
    last = bars.iloc[-1]
    return str(last["date"]), float(last["close"]), "FIFTH_CLOSE"


def evaluate_counterfactual(
    *, selected: pd.DataFrame, price_dir: Path, fx: FxLookup, cost_pct: float = 0.5
) -> tuple[dict, pd.DataFrame]:
    configs = {
        "fifth_close": (None, None, "sl_first"),
        "live_tp12_sl6_conservative": (0.12, 0.06, "sl_first"),
        "live_tp12_sl6_optimistic": (0.12, 0.06, "tp_first"),
        "tp12_no_sl": (0.12, None, "sl_first"),
        "tp12_sl8": (0.12, 0.08, "sl_first"),
        "tp12_sl10": (0.12, 0.10, "sl_first"),
        "tp12_sl12": (0.12, 0.12, "sl_first"),
        "tp12_sl15": (0.12, 0.15, "sl_first"),
        "tp12_sl20": (0.12, 0.20, "sl_first"),
        "tp12_sl25": (0.12, 0.25, "sl_first"),
        "tp12_sl30": (0.12, 0.30, "sl_first"),
        "tp16_sl6": (0.16, 0.06, "sl_first"),
        "no_tp_sl6": (None, 0.06, "sl_first"),
    }
    cache: dict[str, pd.DataFrame] = {}
    rows: list[dict] = []
    errors: list[dict] = []
    for item in selected.itertuples(index=False):
        ticker = str(item.ticker)
        if ticker not in cache:
            cache[ticker] = _load_path(price_dir, ticker)
        path = cache[ticker]
        bars = path[
            path["date"].astype(str).between(str(item.entry_date_5d), str(item.exit_date_5d))
        ].copy()
        if len(bars) < 5:
            errors.append({"session_date": str(item.session_date), "ticker": ticker, "reason": "five_bar_path_missing"})
            continue
        entry_price = float(item.entry_open_5d)
        entry_fx = fx.get(str(item.entry_date_5d))
        if not entry_fx:
            errors.append({"session_date": str(item.session_date), "ticker": ticker, "reason": "entry_fx_missing"})
            continue
        record = item._asdict()
        for name, (tp, sl, tie_break) in configs.items():
            exit_date, exit_price, reason = simulate_exit(
                bars, entry_price=entry_price, tp_pct=tp, sl_pct=sl, tie_break=tie_break
            )
            exit_fx = fx.get(exit_date)
            if not exit_fx:
                errors.append({"session_date": str(item.session_date), "ticker": ticker, "reason": f"exit_fx_missing:{name}"})
                record[f"{name}_net_pct"] = np.nan
                continue
            net = ((exit_price / entry_price) * (exit_fx / entry_fx) - 1.0) * 100.0 - float(cost_pct)
            record[f"{name}_net_pct"] = net
            record[f"{name}_exit_date"] = exit_date
            record[f"{name}_exit_price"] = exit_price
            record[f"{name}_reason"] = reason
        rows.append(record)
    outcomes = pd.DataFrame(rows)
    subsets = {
        "top3": outcomes,
        "rank1": outcomes[outcomes["selection_rank"].eq(1)],
        "hurdle_top3": outcomes[
            outcomes["probability"].ge(0.55) & outcomes["predicted_net_pct"].ge(0.25)
        ],
        "hurdle_rank1": outcomes[
            outcomes["selection_rank"].eq(1)
            & outcomes["probability"].ge(0.55)
            & outcomes["predicted_net_pct"].ge(0.25)
        ],
    }
    report_subsets: dict[str, Any] = {}
    for subset_name, subset in subsets.items():
        report_subsets[subset_name] = {}
        for config_name in configs:
            column = f"{config_name}_net_pct"
            metrics = _metrics(subset, column)
            reason_column = f"{config_name}_reason"
            metrics["exit_reasons"] = {
                str(key): int(value) for key, value in subset[reason_column].value_counts().items()
            }
            report_subsets[subset_name][config_name] = metrics
    integrity_diff = (
        outcomes["fifth_close_net_pct"] - pd.to_numeric(outcomes["net_krw_5d_pct"], errors="coerce")
    ).abs()
    report = {
        "schema_version": "us_swing_exit_counterfactual_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "assumptions": {
            "entry": "session open proxy; live +5..30 minute slippage not available in daily bars",
            "same_day_both_hit": "conservative result uses SL first; optimistic sensitivity uses TP first",
            "gap_fill": "next-session gap beyond barrier fills at open",
            "cost_pct": float(cost_pct),
            "fx": "latest available USDKRW on or before exit date",
        },
        "coverage": {
            "selected_rows": int(len(selected)),
            "simulated_rows": int(len(outcomes)),
            "errors": errors,
        },
        "integrity": {
            "fifth_close_vs_stored_abs_diff_max_pct": float(integrity_diff.max()) if len(integrity_diff) else None,
            "fifth_close_vs_stored_abs_diff_median_pct": float(integrity_diff.median()) if len(integrity_diff) else None,
        },
        "subsets": report_subsets,
        "live_contract_verdict": contract_verdict(report_subsets),
    }
    return report, outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description="Test live TP/SL contract against exact US swing OOS paths")
    parser.add_argument("--selected", default=str(ROOT / "reports" / "us_swing_oos_selected_20260711.csv"))
    parser.add_argument("--price-dir", default=str(ROOT / "data" / "analysis" / "us_yahoo_2y"))
    parser.add_argument("--db", default=str(ROOT / "data" / "analysis" / "us_yahoo_point_in_time.db"))
    parser.add_argument("--output", default=str(ROOT / "reports" / "us_swing_exit_counterfactual_20260711.json"))
    parser.add_argument("--rows-output", default=str(ROOT / "reports" / "us_swing_exit_counterfactual_rows_20260711.csv"))
    parser.add_argument("--refresh-verdict-only", action="store_true")
    args = parser.parse_args()
    if args.refresh_verdict_only:
        output = Path(args.output)
        report = json.loads(output.read_text(encoding="utf-8"))
        report["live_contract_verdict"] = contract_verdict(dict(report.get("subsets") or {}))
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(report["live_contract_verdict"], ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["live_contract_verdict"]["passed"] else 1
    selected = pd.read_csv(args.selected)
    con = sqlite3.connect(args.db)
    try:
        fx_frame = pd.read_sql_query("SELECT date,usdkrw FROM usdkrw_daily", con)
    finally:
        con.close()
    report, outcomes = evaluate_counterfactual(
        selected=selected, price_dir=Path(args.price_dir), fx=FxLookup(fx_frame), cost_pct=0.5
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    outcomes.to_csv(args.rows_output, index=False)
    summary = {
        "schema_version": report["schema_version"],
        "coverage": report["coverage"],
        "integrity": report["integrity"],
        "live_contract_verdict": report["live_contract_verdict"],
        "rank1": report["subsets"]["rank1"],
        "hurdle_rank1": report["subsets"]["hurdle_rank1"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["live_contract_verdict"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
