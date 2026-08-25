from __future__ import annotations

import argparse
from collections import Counter
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

from runtime.us_swing_execution_contract import default_max_hold_sessions
from tools.us_swing_exit_counterfactual import FxLookup, _block_lcb, _load_path, simulate_exit


POLICIES = ("rank1_skip", "affordable_fallback_top3")


def _profit_factor(values: np.ndarray) -> float | None:
    positive = float(values[values > 0].sum())
    negative = float(-values[values < 0].sum())
    return float(positive / negative) if negative > 0 else None


def _candidate_outcome(
    row: Any,
    *,
    price_dir: Path,
    fx: FxLookup,
    price_cache: dict[str, pd.DataFrame],
    entry_slippage_pct: float,
    tp_pct: float,
    sl_pct: float,
    cost_pct: float,
) -> dict[str, Any] | None:
    ticker = str(row.ticker).upper()
    if ticker not in price_cache:
        price_cache[ticker] = _load_path(price_dir, ticker)
    path = price_cache[ticker]
    entry_date = str(row.entry_date_5d)
    expected_exit_date = str(row.exit_date_5d)
    bars = path[path["date"].astype(str).between(entry_date, expected_exit_date)].copy()
    if len(bars) < 5:
        return None
    bars = bars.head(5)
    entry_fx = fx.get(entry_date)
    if not entry_fx:
        return None
    raw_entry = float(row.entry_open_5d)
    entry_price = raw_entry * (1.0 + float(entry_slippage_pct) / 100.0)
    exit_date, exit_price, exit_reason = simulate_exit(
        bars,
        entry_price=entry_price,
        tp_pct=float(tp_pct),
        sl_pct=float(sl_pct),
        tie_break="sl_first",
    )
    exit_fx = fx.get(exit_date)
    if not exit_fx:
        return None
    net_pct = ((exit_price / entry_price) * (exit_fx / entry_fx) - 1.0) * 100.0 - float(cost_pct)
    return {
        "signal_date": str(row.session_date),
        "entry_date": entry_date,
        "contract_exit_date": exit_date,
        "ticker": ticker,
        "rank": int(row.selection_rank),
        "raw_entry_usd": raw_entry,
        "entry_usd": entry_price,
        "entry_fx": float(entry_fx),
        "entry_price_krw": entry_price * float(entry_fx),
        "exit_usd": float(exit_price),
        "exit_fx": float(exit_fx),
        "exit_reason": exit_reason,
        "net_pct": float(net_pct),
    }


def _metrics(
    *,
    trades: list[dict[str, Any]],
    total_sessions: int,
    skipped_slot: int,
    skipped_unaffordable: int,
    starting_sleeve_krw: float,
    account_reference_krw: float,
) -> dict[str, Any]:
    values = np.asarray([float(row["net_pct"]) for row in trades], dtype=float)
    pnl = np.asarray([float(row["pnl_krw"]) for row in trades], dtype=float)
    equity = np.concatenate(([float(starting_sleeve_krw)], float(starting_sleeve_krw) + np.cumsum(pnl)))
    peaks = np.maximum.accumulate(equity)
    drawdown = np.divide(equity - peaks, peaks, out=np.zeros_like(equity), where=peaks != 0) * 100.0
    rank_counts = Counter(int(row["rank"]) for row in trades)
    by_year: dict[str, Any] = {}
    for year in sorted({str(row["entry_date"])[:4] for row in trades}):
        year_rows = [row for row in trades if str(row["entry_date"]).startswith(year)]
        year_values = np.asarray([float(row["net_pct"]) for row in year_rows], dtype=float)
        by_year[year] = {
            "trades": len(year_rows),
            "net_pnl_krw": float(sum(float(row["pnl_krw"]) for row in year_rows)),
            "mean_net_pct": float(year_values.mean()),
            "profit_factor": _profit_factor(year_values),
            "win_rate": float((year_values > 0).mean()),
        }
    return {
        "decision_sessions": int(total_sessions),
        "trades": int(len(trades)),
        "skipped_slot_sessions": int(skipped_slot),
        "skipped_unaffordable_sessions": int(skipped_unaffordable),
        "trade_rate_per_decision_session": float(len(trades) / total_sessions) if total_sessions else 0.0,
        "annualized_trades_at_252_sessions": float(len(trades) / total_sessions * 252.0) if total_sessions else 0.0,
        "fallback_trades": int(sum(int(row["rank"]) > 1 for row in trades)),
        "rank_counts": {str(key): int(value) for key, value in sorted(rank_counts.items())},
        "mean_net_pct": float(values.mean()) if len(values) else None,
        "median_net_pct": float(np.median(values)) if len(values) else None,
        "profit_factor": _profit_factor(values) if len(values) else None,
        "win_rate": float((values > 0).mean()) if len(values) else None,
        "block_lcb_pct": _block_lcb(values) if len(values) else None,
        "worst_net_pct": float(values.min()) if len(values) else None,
        "net_pnl_krw": float(pnl.sum()),
        "ending_sleeve_krw": float(starting_sleeve_krw + pnl.sum()),
        "sleeve_return_pct": float(pnl.sum() / starting_sleeve_krw * 100.0),
        "account_return_pct": float(pnl.sum() / account_reference_krw * 100.0),
        "realized_equity_max_drawdown_pct": float(drawdown.min()),
        "by_year": by_year,
    }


def simulate_capacity_path(
    selected: pd.DataFrame,
    *,
    price_dir: Path,
    fx: FxLookup,
    policy: str,
    entry_slippage_pct: float,
    starting_sleeve_krw: float = 500_000.0,
    account_reference_krw: float = 5_000_000.0,
    base_order_budget_krw: float = 500_000.0,
    size_multiplier: float = 0.10,
    tp_pct: float = 0.12,
    sl_pct: float = 0.25,
    cost_pct: float = 0.50,
) -> tuple[dict[str, Any], pd.DataFrame]:
    if policy not in POLICIES:
        raise ValueError(f"unsupported policy: {policy}")
    required = {
        "session_date", "ticker", "selection_rank", "entry_date_5d", "exit_date_5d", "entry_open_5d"
    }
    missing = sorted(required.difference(selected.columns))
    if missing:
        raise ValueError(f"selected input missing columns: {','.join(missing)}")
    work = selected.copy()
    work["selection_rank"] = pd.to_numeric(work["selection_rank"], errors="coerce")
    work = work.dropna(subset=["selection_rank"]).sort_values(["entry_date_5d", "selection_rank"])
    session_groups = list(work.groupby("entry_date_5d", sort=True))
    price_cache: dict[str, pd.DataFrame] = {}
    capital = float(starting_sleeve_krw)
    occupied_until = ""
    trades: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    skipped_slot = 0
    skipped_unaffordable = 0
    missing_outcome_sessions = 0
    order_cap_krw = float(base_order_budget_krw) * float(size_multiplier)

    for entry_date, group in session_groups:
        entry_date = str(entry_date)
        group = group.sort_values("selection_rank")
        if occupied_until and entry_date <= occupied_until:
            skipped_slot += 1
            audit_rows.append({
                "entry_date": entry_date,
                "status": "SKIPPED_SLOT",
                "occupied_until": occupied_until,
            })
            continue
        candidates = group[group["selection_rank"].eq(1)] if policy == "rank1_skip" else group.head(3)
        evaluated: list[dict[str, Any]] = []
        for row in candidates.itertuples(index=False):
            outcome = _candidate_outcome(
                row,
                price_dir=price_dir,
                fx=fx,
                price_cache=price_cache,
                entry_slippage_pct=entry_slippage_pct,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                cost_pct=cost_pct,
            )
            if outcome is None:
                continue
            spend_cap = min(order_cap_krw, capital)
            outcome["spend_cap_krw"] = float(spend_cap)
            outcome["qty"] = int(spend_cap // float(outcome["entry_price_krw"]))
            evaluated.append(outcome)
        if not evaluated:
            missing_outcome_sessions += 1
            audit_rows.append({"entry_date": entry_date, "status": "MISSING_OUTCOME"})
            continue
        chosen = next((row for row in evaluated if int(row["qty"]) >= 1), None)
        if chosen is None:
            skipped_unaffordable += 1
            audit_rows.append({
                "entry_date": entry_date,
                "status": "SKIPPED_UNAFFORDABLE",
                "candidate_prices_krw": json.dumps(
                    {str(row["rank"]): round(float(row["entry_price_krw"]), 2) for row in evaluated},
                    sort_keys=True,
                ),
            })
            continue
        invested = int(chosen["qty"]) * float(chosen["entry_price_krw"])
        pnl_krw = invested * float(chosen["net_pct"]) / 100.0
        chosen.update({
            "status": "TRADED",
            "invested_krw": float(invested),
            "pnl_krw": float(pnl_krw),
            "capital_before_krw": float(capital),
            "capital_after_exit_krw": float(capital + pnl_krw),
        })
        capital += pnl_krw
        occupied_until = str(chosen["contract_exit_date"])
        trades.append(chosen)
        audit_rows.append(dict(chosen))

    metrics = _metrics(
        trades=trades,
        total_sessions=len(session_groups),
        skipped_slot=skipped_slot,
        skipped_unaffordable=skipped_unaffordable,
        starting_sleeve_krw=starting_sleeve_krw,
        account_reference_krw=account_reference_krw,
    )
    metrics["missing_outcome_sessions"] = int(missing_outcome_sessions)
    return metrics, pd.DataFrame(audit_rows)


def _policy_verdict(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rank1 = results["rank1_skip"]
    fallback = results["affordable_fallback_top3"]
    scenarios = sorted(rank1, key=float)
    comparisons = []
    for scenario in scenarios:
        base = rank1[scenario]
        alternative = fallback[scenario]
        comparisons.append({
            "entry_slippage_pct": float(scenario),
            "rank1_pnl_krw": base.get("net_pnl_krw"),
            "fallback_pnl_krw": alternative.get("net_pnl_krw"),
            "fallback_minus_rank1_krw": float(alternative.get("net_pnl_krw") or 0.0)
            - float(base.get("net_pnl_krw") or 0.0),
        })
    fallback_wins = sum(row["fallback_minus_rank1_krw"] > 0 for row in comparisons)
    fallback_all_positive = all(float(fallback[key].get("net_pnl_krw") or 0.0) > 0 for key in scenarios)
    rank1_all_positive = all(float(rank1[key].get("net_pnl_krw") or 0.0) > 0 for key in scenarios)
    selected = "affordable_fallback_top3" if fallback_wins == len(scenarios) and fallback_all_positive else "rank1_skip"
    return {
        "selected_policy": selected,
        "selection_rule": "fallback only when it beats rank1 and remains profitable in every slippage scenario",
        "rank1_positive_all_scenarios": rank1_all_positive,
        "fallback_positive_all_scenarios": fallback_all_positive,
        "fallback_wins_scenarios": int(fallback_wins),
        "scenario_count": int(len(scenarios)),
        "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="US swing one-slot, whole-share capacity counterfactual")
    parser.add_argument("--selected", default=str(ROOT / "reports" / "us_swing_oos_selected_20260711.csv"))
    parser.add_argument("--price-dir", default=str(ROOT / "data" / "analysis" / "us_yahoo_2y"))
    parser.add_argument("--db", default=str(ROOT / "data" / "analysis" / "us_yahoo_point_in_time.db"))
    parser.add_argument("--output", default=str(ROOT / "reports" / "us_swing_capacity_counterfactual_20260711.json"))
    parser.add_argument("--rows-output", default="", help="optional per-decision audit CSV")
    parser.add_argument("--slippage", default="0,0.25,0.5,1.0")
    parser.add_argument("--starting-sleeve-krw", type=float, default=500_000.0)
    parser.add_argument("--account-reference-krw", type=float, default=5_000_000.0)
    parser.add_argument("--base-order-budget-krw", type=float, default=500_000.0)
    parser.add_argument("--size-multiplier", type=float, default=0.10)
    args = parser.parse_args()

    selected = pd.read_csv(args.selected)
    con = sqlite3.connect(args.db)
    try:
        fx_frame = pd.read_sql_query("SELECT date,usdkrw FROM usdkrw_daily", con)
    finally:
        con.close()
    fx = FxLookup(fx_frame)
    slippages = [float(value.strip()) for value in args.slippage.split(",") if value.strip()]
    results: dict[str, dict[str, Any]] = {policy: {} for policy in POLICIES}
    audits: list[pd.DataFrame] = []
    for policy in POLICIES:
        for slippage in slippages:
            metrics, rows = simulate_capacity_path(
                selected,
                price_dir=Path(args.price_dir),
                fx=fx,
                policy=policy,
                entry_slippage_pct=slippage,
                starting_sleeve_krw=args.starting_sleeve_krw,
                account_reference_krw=args.account_reference_krw,
                base_order_budget_krw=args.base_order_budget_krw,
                size_multiplier=args.size_multiplier,
            )
            key = f"{slippage:g}"
            results[policy][key] = metrics
            rows["policy"] = policy
            rows["entry_slippage_pct"] = slippage
            audits.append(rows)
    verdict = _policy_verdict(results)
    report = {
        "schema_version": "us_swing_capacity_counterfactual_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "max_open_slots": 1,
            "max_new_per_day": 1,
            "whole_shares_only": True,
            "starting_sleeve_krw": args.starting_sleeve_krw,
            "account_reference_krw": args.account_reference_krw,
            "base_order_budget_krw": args.base_order_budget_krw,
            "size_multiplier": args.size_multiplier,
            "order_cap_krw": args.base_order_budget_krw * args.size_multiplier,
            "take_profit_pct": 0.12,
            "catastrophe_stop_pct": 0.25,
            # env 단일 소스(2026-08-25). 실제 보유창은 원장 컬럼(entry/exit_date_5d —
            # 러너가 live 계약으로 산출)이 정하고, 이 값은 메타데이터 표기다.
            "max_hold_sessions": default_max_hold_sessions(),
            "cost_pct": 0.50,
        },
        "assumptions": {
            "entry": "next-session open plus adverse entry slippage",
            "same_day_barriers": "entry-session high/low can trigger; same-bar TP/SL resolves SL first",
            "gap_fill": "post-entry-session gap beyond a barrier fills at open",
            "same_day_reentry": "disabled; a new entry requires entry_date later than prior exit_date",
            "drawdown": "realized equity at exits only; not mark-to-market",
            "fx": "latest USDKRW observation on or before entry/exit date",
        },
        "coverage": {
            "selected_rows": int(len(selected)),
            "decision_sessions": int(selected["entry_date_5d"].nunique()),
        },
        "results": results,
        "policy_verdict": verdict,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    if args.rows_output:
        rows_output = Path(args.rows_output)
        rows_output.parent.mkdir(parents=True, exist_ok=True)
        pd.concat(audits, ignore_index=True, sort=False).to_csv(rows_output, index=False)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
