from __future__ import annotations

"""Reproducible, read-only validation for a fixed partial-exit runner.

The experiment uses only completed live trades, their recorded MFE, integer
quantity and the plan target that existed for the trade.  It never writes to a
live database or imports the order path.  Results are counterfactual research,
not evidence that a limit order would necessarily have filled.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.early_tier_shadow_review import (
    DEFAULT_EVENT_DB,
    DEFAULT_ML_DB,
    load_plan_targets,
    load_trades,
    tier_counterfactual,
)


DEFAULT_OUTPUT = ROOT / "reports" / "split_exit_runner_lab_20260715.json"
LEVELS = (2.3, 3.0, 3.6, 4.0, 4.5)
FRACTIONS = (0.33, 0.50, 0.66)
COST = {"US": 0.50, "KR": 0.21}
NATIVE_LEVEL = {"US": 2.3, "KR": 3.6}


def _rounded(value: float) -> float:
    return round(float(value), 4)


def _trimmed_sum(values: list[float], remove_top: int = 0, remove_bottom: int = 0) -> float:
    ordered = sorted(values)
    start = min(remove_bottom, len(ordered))
    stop = len(ordered) - min(remove_top, max(0, len(ordered) - start))
    return sum(ordered[start:stop])


def evaluate(
    trades: list[dict[str, Any]],
    plans: dict[str, float],
    market: str,
    level: float,
    fraction: float,
    extra_partial_slippage_pct: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    monthly: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"n": 0, "actual_sum_pct": 0.0, "counterfactual_sum_pct": 0.0}
    )
    for trade in trades:
        if trade["market"] != market:
            continue
        result = tier_counterfactual(
            trade,
            plans.get(trade["did"]),
            level,
            fraction,
            COST[market],
        )
        if result is None:
            continue
        stressed = result["cf_net"] - extra_partial_slippage_pct * result["effective_f"]
        row = {
            "decision_id": trade["did"],
            "session_date": trade["session_date"],
            "qty": result["qty"],
            "mfe_pct": trade["mfe"],
            "actual_net_pct": result["actual_net"],
            "reached": result["reached"],
            "executable": result["executable"],
            "sell_qty": result["sell_qty"],
            "effective_fraction": result["effective_f"],
            "counterfactual_net_pct": result["cf_net"],
            "stressed_net_pct": stressed,
        }
        rows.append(row)
        month = str(trade["session_date"])[:7]
        monthly[month]["n"] += 1
        monthly[month]["actual_sum_pct"] += result["actual_net"]
        monthly[month]["counterfactual_sum_pct"] += result["cf_net"]

    actual = [float(row["actual_net_pct"]) for row in rows]
    counterfactual = [float(row["counterfactual_net_pct"]) for row in rows]
    stressed = [float(row["stressed_net_pct"]) for row in rows]
    # A stricter stress charges the nominal requested fraction to every
    # executable trade even when integer rounding sold less than that fraction.
    nominal_stressed = [
        float(row["counterfactual_net_pct"])
        - (extra_partial_slippage_pct * fraction if row["executable"] else 0.0)
        for row in rows
    ]
    summary = {
        "market": market,
        "level_pct": level,
        "fraction": fraction,
        "n": len(rows),
        "reached_n": sum(bool(row["reached"]) for row in rows),
        "integer_executable_n": sum(bool(row["executable"]) for row in rows),
        "actual_sum_pct": _rounded(sum(actual)),
        "counterfactual_sum_pct": _rounded(sum(counterfactual)),
        "delta_sum_pct": _rounded(sum(counterfactual) - sum(actual)),
        "counterfactual_ex_top3_sum_pct": _rounded(_trimmed_sum(counterfactual, remove_top=3)),
        "counterfactual_ex_top_bottom3_sum_pct": _rounded(
            _trimmed_sum(counterfactual, remove_top=3, remove_bottom=3)
        ),
        "stressed_sum_pct": _rounded(sum(stressed)),
        "nominal_fraction_stressed_sum_pct": _rounded(sum(nominal_stressed)),
        "monthly": {
            month: {
                "n": int(values["n"]),
                "actual_sum_pct": _rounded(float(values["actual_sum_pct"])),
                "counterfactual_sum_pct": _rounded(float(values["counterfactual_sum_pct"])),
            }
            for month, values in sorted(monthly.items())
        },
    }
    return summary, rows


def provenance(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size_bytes": stat.st_size,
        "modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fixed split-exit runner robustness lab")
    parser.add_argument("--ml-db", type=Path, default=DEFAULT_ML_DB)
    parser.add_argument("--event-db", type=Path, default=DEFAULT_EVENT_DB)
    parser.add_argument("--since", default="2026-04-01")
    parser.add_argument("--extra-partial-slippage-pct", type=float, default=0.30)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    plans = load_plan_targets(args.event_db)
    trades = load_trades(args.ml_db, args.since)
    sweeps: dict[str, Any] = {}
    primary_ledgers: dict[str, list[dict[str, Any]]] = {}
    for market in ("US", "KR"):
        market_results: dict[str, Any] = {}
        for fraction in FRACTIONS:
            fraction_results: dict[str, Any] = {}
            for level in LEVELS:
                summary, rows = evaluate(
                    trades,
                    plans,
                    market,
                    level,
                    fraction,
                    args.extra_partial_slippage_pct,
                )
                fraction_results[str(level)] = summary
                if level == NATIVE_LEVEL[market] and fraction == 0.50:
                    primary_ledgers[market] = rows
            market_results[str(fraction)] = fraction_results
        sweeps[market] = market_results

    result = {
        "authority": "SHADOW_ONLY",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "since": args.since,
            "levels_pct": LEVELS,
            "fractions": FRACTIONS,
            "round_trip_cost_pct": COST,
            "extra_partial_slippage_pct": args.extra_partial_slippage_pct,
            "same_bar_ordering": "not available; held MFE is an optimistic reach ceiling",
            "integer_quantity": "floor(qty*fraction); qty=1 remains unchanged",
            "runner": "remaining quantity keeps the actual recorded net return",
        },
        "provenance": {
            "ml_db": provenance(args.ml_db),
            "event_db": provenance(args.event_db),
        },
        "sweeps": sweeps,
        "native_50pct_ledgers": primary_ledgers,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "US_primary": sweeps["US"]["0.5"]["2.3"],
        "KR_primary": sweeps["KR"]["0.5"]["3.6"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
