from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Metrics:
    n: int
    wins: int
    losses: int
    win_rate_pct: float
    avg_pct: float
    sum_pct: float
    profit_factor: float | str | None
    worst_pct: float
    best_pct: float
    pnl_krw: float | None = None
    delta_krw: float | None = None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Simulate proposed live risk/selection improvements from local logs."
    )
    parser.add_argument("--output-dir", default=str(ROOT / "docs" / "reports"))
    parser.add_argument("--stamp", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    args = parser.parse_args()

    closed = load_live_closed(ROOT / "state" / "live_decisions.jsonl")
    selection_trades = load_selection_trades(ROOT / "data" / "ticker_selection_log.db")
    blocked = load_blocked_signals(ROOT / "data" / "ticker_selection_log.db")

    payload = build_payload(closed, selection_trades, blocked)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"live_improvement_simulation_{args.stamp}.json"
    md_path = output_dir / f"live_improvement_simulation_{args.stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(to_markdown(payload), encoding="utf-8")
    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    return 0


def load_live_closed(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") != "closed" or row.get("pnl_pct") is None:
            continue
        row["_dt"] = parse_dt(row.get("timestamp"))
        row["_mfe_pct"] = safe_float(
            row.get("position_mfe_pct")
            if row.get("position_mfe_pct") is not None
            else row.get("peak_pnl_pct")
        )
        rows.append(row)
    return sorted(rows, key=lambda row: row["_dt"])


def load_selection_trades(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM ticker_selection_log
                WHERE bot_mode='live'
                  AND traded=1
                  AND pnl_pct IS NOT NULL
                ORDER BY COALESCE(traded_at, signal_at, selected_at)
                """
            )
        ]
    finally:
        conn.close()
    return rows


def load_blocked_signals(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM ticker_selection_log
                WHERE bot_mode='live'
                  AND signal_fired=1
                  AND traded=0
                  AND blocked_reason IS NOT NULL
                ORDER BY COALESCE(signal_at, selected_at)
                """
            )
        ]
    finally:
        conn.close()
    return rows


def build_payload(
    closed: list[dict[str, Any]],
    selection_trades: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
) -> dict[str, Any]:
    scenarios: dict[str, Callable[[dict[str, Any]], float]] = {
        "baseline": lambda row: safe_float(row.get("pnl_pct")),
        "loss_cap_3_only": lambda row: apply_loss_cap(row, 3.0),
        "loss_cap_2_only": lambda row: apply_loss_cap(row, 2.0),
        "loss_cap_1_5_only": lambda row: apply_loss_cap(row, 1.5),
        "current_code_cap3_floor0_5_at_mfe2": lambda row: apply_current_code(row),
        "proposed_cap2_mfe_protection": lambda row: apply_mfe_protection(
            row, cap_pct=2.0, preserve_ratio=0.45, breakeven_floor=0.0
        ),
        "aggressive_cap1_5_mfe_protection": lambda row: apply_mfe_protection(
            row, cap_pct=1.5, preserve_ratio=0.50, breakeven_floor=0.1
        ),
    }
    baseline_krw = sum(safe_float(row.get("pnl_krw")) for row in closed)
    scenario_rows = []
    for name, func in scenarios.items():
        row = {
            "name": name,
            "all": metrics_for_closed(closed, func, baseline_krw=baseline_krw),
            "KR": metrics_for_closed(
                [item for item in closed if item.get("market") == "KR"],
                func,
                baseline_krw=sum(safe_float(item.get("pnl_krw")) for item in closed if item.get("market") == "KR"),
            ),
            "US": metrics_for_closed(
                [item for item in closed if item.get("market") == "US"],
                func,
                baseline_krw=sum(safe_float(item.get("pnl_krw")) for item in closed if item.get("market") == "US"),
            ),
        }
        scenario_rows.append(row)

    proposed = scenarios["proposed_cap2_mfe_protection"]
    changed = []
    for row in closed:
        old_pct = safe_float(row.get("pnl_pct"))
        new_pct = proposed(row)
        if abs(new_pct - old_pct) > 1e-9:
            changed.append(
                {
                    "timestamp": row.get("timestamp", ""),
                    "market": row.get("market", ""),
                    "ticker": row.get("ticker", ""),
                    "strategy": row.get("strategy", ""),
                    "exit_reason": row.get("exit_reason", ""),
                    "old_pct": round(old_pct, 6),
                    "mfe_pct": round(safe_float(row.get("_mfe_pct")), 6),
                    "sim_pct": round(new_pct, 6),
                    "old_pnl_krw": round(safe_float(row.get("pnl_krw")), 0),
                    "sim_pnl_krw": round(estimate_krw(row, new_pct), 0),
                }
            )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "basis": {
            "live_closed_source": "state/live_decisions.jsonl",
            "selection_source": "data/ticker_selection_log.db",
            "closed_trades": len(closed),
            "selection_closed_trades": len(selection_trades),
            "blocked_signals": len(blocked),
            "assumptions": [
                "No intraday tick replay was available, so loss caps are simulated by clipping realized return at the cap.",
                "MFE protection assumes a protective stop would have filled at the simulated floor after the recorded MFE was reached.",
                "Estimated KRW PnL scales the recorded KRW PnL by simulated_pct / realized_pct where possible.",
                "trade_ready and daily-entry simulations use ticker_selection_log rows because live_decisions does not consistently include selection gate metadata.",
            ],
        },
        "live_closed_baseline": grouped_live_metrics(closed),
        "scenarios": scenario_rows,
        "proposed_changed_trades": changed,
        "selection_gate": selection_gate_payload(selection_trades),
        "blocked_opportunities": blocked_payload(blocked),
        "direction_assessment": {
            "additive_overlays": [
                "loss_cap and profit_floor improve the left tail without changing candidate generation.",
                "MFE-based profit preservation is still an overlay unless it is backed by persistent peak/stop state and broker-side reconciliation.",
            ],
            "structural_changes": [
                "trade_ready=0 blocking must live at the final execution gate with explicit override_reason logging.",
                "KR momentum demotion is a policy-router change, not just a threshold change.",
                "strategy PF demotion to paper-only needs a per-strategy lifecycle state table.",
                "Exact replay requires a unified fill ledger and intraday price path snapshots; current logs support only approximate overlay simulation.",
            ],
            "verdict": "Use risk overlays immediately, but do not keep adding isolated rules as the main path. The durable fix is an execution contract plus policy state: gate -> size -> order -> fill ledger -> exit ownership -> promotion/demotion.",
        },
    }


def grouped_live_metrics(closed: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "all": metrics_from_values([safe_float(row.get("pnl_pct")) for row in closed]).__dict__,
    }
    for market in ("KR", "US"):
        subset = [row for row in closed if row.get("market") == market]
        out[market] = metrics_from_values([safe_float(row.get("pnl_pct")) for row in subset]).__dict__
        strategies: dict[str, Any] = {}
        for strategy in sorted({str(row.get("strategy") or "") for row in subset}):
            group = [row for row in subset if str(row.get("strategy") or "") == strategy]
            strategies[strategy or "(blank)"] = metrics_from_values(
                [safe_float(row.get("pnl_pct")) for row in group]
            ).__dict__
        out[f"{market}_by_strategy"] = strategies
    return out


def selection_gate_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def by(pred: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        subset = [row for row in rows if pred(row)]
        return metrics_from_values([safe_float(row.get("pnl_pct")) for row in subset]).__dict__

    return {
        "baseline": by(lambda _row: True),
        "trade_ready_1": by(lambda row: int(row.get("trade_ready") or 0) == 1),
        "trade_ready_0": by(lambda row: int(row.get("trade_ready") or 0) == 0),
        "KR_ready_1": by(lambda row: row.get("market") == "KR" and int(row.get("trade_ready") or 0) == 1),
        "KR_ready_0": by(lambda row: row.get("market") == "KR" and int(row.get("trade_ready") or 0) == 0),
        "US_ready_1": by(lambda row: row.get("market") == "US" and int(row.get("trade_ready") or 0) == 1),
        "US_ready_0": by(lambda row: row.get("market") == "US" and int(row.get("trade_ready") or 0) == 0),
        "filters": {
            "block_not_ready": by(lambda row: int(row.get("trade_ready") or 0) == 1),
            "block_kr_momentum": by(
                lambda row: not (row.get("market") == "KR" and str(row.get("strategy_name") or "") == "momentum")
            ),
            "us_only": by(lambda row: row.get("market") == "US"),
            "us_ready_only": by(lambda row: row.get("market") == "US" and int(row.get("trade_ready") or 0) == 1),
        },
        "daily_entry_caps": daily_entry_caps(rows),
        "not_ready_traded": [
            {
                "date": row.get("date"),
                "market": row.get("market"),
                "ticker": row.get("ticker"),
                "strategy": row.get("strategy_name"),
                "pnl_pct": safe_float(row.get("pnl_pct")),
                "exit_reason": row.get("exit_reason"),
                "traded_at": row.get("traded_at"),
            }
            for row in rows
            if int(row.get("trade_ready") or 0) == 0
        ],
    }


def daily_entry_caps(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for cap in (1, 2, 3):
        selected = first_n_by_day(rows, cap, by_market=False)
        output[f"max_total_daily_entries_{cap}"] = {
            **metrics_from_values([safe_float(row.get("pnl_pct")) for row in selected]).__dict__,
            "kept": len(selected),
            "total": len(rows),
        }
    for cap in (1, 2, 3):
        selected = first_n_by_day(rows, cap, by_market=True)
        output[f"max_market_daily_entries_{cap}"] = {
            **metrics_from_values([safe_float(row.get("pnl_pct")) for row in selected]).__dict__,
            "kept": len(selected),
            "total": len(rows),
        }
    return output


def first_n_by_day(rows: list[dict[str, Any]], n: int, *, by_market: bool) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (str(row.get("date") or ""), str(row.get("market") or "")) if by_market else (str(row.get("date") or ""),)
        groups[key].append(row)
    selected: list[dict[str, Any]] = []
    for group in groups.values():
        selected.extend(
            sorted(
                group,
                key=lambda row: parse_dt(row.get("traded_at") or row.get("signal_at") or row.get("selected_at")),
            )[:n]
        )
    return selected


def blocked_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    reasons = sorted({str(row.get("blocked_reason") or "") for row in rows})
    for reason in reasons:
        subset = [row for row in rows if str(row.get("blocked_reason") or "") == reason]
        forward_1d = [safe_float(row.get("forward_1d")) for row in subset if row.get("forward_1d") is not None]
        runup_3d = [safe_float(row.get("max_runup_3d")) for row in subset if row.get("max_runup_3d") is not None]
        out.append(
            {
                "blocked_reason": reason,
                "n": len(subset),
                "ready_count": sum(1 for row in subset if int(row.get("trade_ready") or 0) == 1),
                "forward_1d": metrics_from_values(forward_1d).__dict__ if forward_1d else None,
                "max_runup_3d": metrics_from_values(runup_3d).__dict__ if runup_3d else None,
            }
        )
    return out


def metrics_for_closed(
    rows: list[dict[str, Any]],
    func: Callable[[dict[str, Any]], float],
    *,
    baseline_krw: float,
) -> dict[str, Any]:
    values = [func(row) for row in rows]
    pnl_krw = sum(estimate_krw(row, func(row)) for row in rows)
    metrics = metrics_from_values(values, pnl_krw=pnl_krw, delta_krw=pnl_krw - baseline_krw)
    return metrics.__dict__


def metrics_from_values(
    values: list[float],
    *,
    pnl_krw: float | None = None,
    delta_krw: float | None = None,
) -> Metrics:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    wins = [value for value in clean if value > 0]
    losses = [value for value in clean if value < 0]
    profit_factor: float | str | None = sum(wins) / abs(sum(losses)) if losses else (None if not wins else "inf")
    return Metrics(
        n=len(clean),
        wins=len(wins),
        losses=len(losses),
        win_rate_pct=(len(wins) / len(clean) * 100.0 if clean else 0.0),
        avg_pct=(sum(clean) / len(clean) if clean else 0.0),
        sum_pct=sum(clean),
        profit_factor=profit_factor,
        worst_pct=(min(clean) if clean else 0.0),
        best_pct=(max(clean) if clean else 0.0),
        pnl_krw=pnl_krw,
        delta_krw=delta_krw,
    )


def apply_loss_cap(row: dict[str, Any], cap_pct: float) -> float:
    return max(safe_float(row.get("pnl_pct")), -abs(float(cap_pct)))


def apply_current_code(row: dict[str, Any]) -> float:
    pct = apply_loss_cap(row, 3.0)
    if safe_float(row.get("_mfe_pct")) >= 2.0:
        pct = max(pct, 0.5)
    return pct


def apply_mfe_protection(
    row: dict[str, Any],
    *,
    cap_pct: float,
    preserve_ratio: float,
    breakeven_floor: float,
) -> float:
    pct = apply_loss_cap(row, cap_pct)
    mfe = safe_float(row.get("_mfe_pct"))
    if mfe >= 2.0:
        return max(pct, preserve_ratio * mfe)
    if mfe >= 1.0:
        return max(pct, breakeven_floor)
    return pct


def estimate_krw(row: dict[str, Any], simulated_pct: float) -> float:
    realized_pct = safe_float(row.get("pnl_pct"))
    realized_krw = safe_float(row.get("pnl_krw"))
    if abs(realized_pct) <= 1e-9:
        return realized_krw
    return realized_krw * simulated_pct / realized_pct


def parse_dt(value: Any) -> datetime:
    if not value:
        return datetime.min
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromisoformat(text.split("+")[0])
        except ValueError:
            return datetime.min


def safe_float(value: Any) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else 0.0
    except (TypeError, ValueError):
        return 0.0


def fmt_pct(value: float) -> str:
    return f"{float(value):+.3f}%"


def fmt_krw(value: float | None) -> str:
    if value is None:
        return ""
    return f"{float(value):+,.0f}"


def fmt_pf(value: float | str | None) -> str:
    if value is None:
        return "NA"
    if value == "inf":
        return "inf"
    return f"{float(value):.2f}"


def metric_row(label: str, metrics: dict[str, Any]) -> str:
    return (
        f"| {label} | {metrics['n']} | {metrics['wins']}/{metrics['losses']} | "
        f"{metrics['win_rate_pct']:.1f}% | {fmt_pct(metrics['avg_pct'])} | "
        f"{fmt_pf(metrics['profit_factor'])} | {fmt_krw(metrics.get('pnl_krw'))} | "
        f"{fmt_krw(metrics.get('delta_krw'))} |"
    )


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Live Improvement Simulation",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## Basis",
        "",
        f"- Closed trades: {payload['basis']['closed_trades']} from `state/live_decisions.jsonl`.",
        f"- Selection closed trades: {payload['basis']['selection_closed_trades']} from `data/ticker_selection_log.db`.",
        f"- Blocked signals: {payload['basis']['blocked_signals']} from `data/ticker_selection_log.db`.",
        "- No Claude calls and no broker/API calls were made.",
        "",
        "Assumptions:",
    ]
    lines.extend(f"- {item}" for item in payload["basis"]["assumptions"])

    lines.extend(
        [
            "",
            "## Scenario Result",
            "",
            "| Scenario | N | W/L | Win | Avg pct | PF | Est. PnL KRW | Delta KRW |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for scenario in payload["scenarios"]:
        lines.append(metric_row(scenario["name"], scenario["all"]))

    lines.extend(
        [
            "",
            "## Market Split",
            "",
            "| Scenario | Market | N | W/L | Win | Avg pct | PF | Est. PnL KRW | Delta KRW |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for scenario in payload["scenarios"]:
        for market in ("KR", "US"):
            metrics = scenario[market]
            lines.append(
                f"| {scenario['name']} | {market} | {metrics['n']} | {metrics['wins']}/{metrics['losses']} | "
                f"{metrics['win_rate_pct']:.1f}% | {fmt_pct(metrics['avg_pct'])} | "
                f"{fmt_pf(metrics['profit_factor'])} | {fmt_krw(metrics.get('pnl_krw'))} | "
                f"{fmt_krw(metrics.get('delta_krw'))} |"
            )

    lines.extend(
        [
            "",
            "## Proposed Scenario Changed Trades",
            "",
            "| Time | Market | Ticker | Strategy | Exit | Old | MFE | Sim | Old KRW | Sim KRW |",
            "|---|---|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["proposed_changed_trades"]:
        lines.append(
            f"| {row['timestamp']} | {row['market']} | {row['ticker']} | {row['strategy']} | "
            f"{row['exit_reason']} | {fmt_pct(row['old_pct'])} | {fmt_pct(row['mfe_pct'])} | "
            f"{fmt_pct(row['sim_pct'])} | {fmt_krw(row['old_pnl_krw'])} | {fmt_krw(row['sim_pnl_krw'])} |"
        )

    gate = payload["selection_gate"]
    lines.extend(
        [
            "",
            "## Selection Gate Simulation",
            "",
            "| Filter | N | W/L | Win | Avg pct | PF |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for key in (
        "baseline",
        "trade_ready_1",
        "trade_ready_0",
        "KR_ready_1",
        "KR_ready_0",
        "US_ready_1",
        "US_ready_0",
    ):
        item = gate[key]
        lines.append(
            f"| {key} | {item['n']} | {item['wins']}/{item['losses']} | "
            f"{item['win_rate_pct']:.1f}% | {fmt_pct(item['avg_pct'])} | {fmt_pf(item['profit_factor'])} |"
        )
    for key, item in gate["filters"].items():
        lines.append(
            f"| {key} | {item['n']} | {item['wins']}/{item['losses']} | "
            f"{item['win_rate_pct']:.1f}% | {fmt_pct(item['avg_pct'])} | {fmt_pf(item['profit_factor'])} |"
        )

    lines.extend(
        [
            "",
            "## Daily Entry Caps",
            "",
            "| Rule | Kept | N | W/L | Win | Avg pct | PF |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for key, item in gate["daily_entry_caps"].items():
        lines.append(
            f"| {key} | {item['kept']}/{item['total']} | {item['n']} | {item['wins']}/{item['losses']} | "
            f"{item['win_rate_pct']:.1f}% | {fmt_pct(item['avg_pct'])} | {fmt_pf(item['profit_factor'])} |"
        )

    lines.extend(
        [
            "",
            "## Blocked Opportunity Check",
            "",
            "| Reason | N | Ready | Forward 1D Avg | Runup 3D Avg |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in payload["blocked_opportunities"]:
        f1 = row.get("forward_1d")
        ru = row.get("max_runup_3d")
        lines.append(
            f"| {row['blocked_reason']} | {row['n']} | {row['ready_count']} | "
            f"{fmt_pct(f1['avg_pct']) if f1 else 'NA'} | {fmt_pct(ru['avg_pct']) if ru else 'NA'} |"
        )

    assessment = payload["direction_assessment"]
    lines.extend(["", "## Direction Assessment", "", "Additive overlays:"])
    lines.extend(f"- {item}" for item in assessment["additive_overlays"])
    lines.extend(["", "Structural changes:"])
    lines.extend(f"- {item}" for item in assessment["structural_changes"])
    lines.extend(["", f"Verdict: {assessment['verdict']}", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
