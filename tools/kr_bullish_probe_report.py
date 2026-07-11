from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any


DEFAULT_DB = Path("data/audit/candidate_audit.db")
DEFAULT_PATHS = ("immediate", "volume_surge", "wait_30m", "wait_60m")


def _connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _minutes_after_open(value: str) -> float | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return float(stamp.hour * 60 + stamp.minute - 540)
    except Exception:
        return None


def replay_session_selection(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    index_change_pct: float,
    breadth_up_ratio_pct: float,
    plan_a_min: float = 65.0,
    risk_max: float = 35.0,
    entry_window_max_min: float = 90.0,
) -> list[dict[str, Any]]:
    if index_change_pct < 2.0 or breadth_up_ratio_pct < 60.0:
        return []
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT candidate_key, session_date, known_at, ticker, price,
                   trainer_plan_a_score, trainer_risk_score, trainer_candidate_state,
                   evidence_action_ceiling, claude_action, route_final_action,
                   route_runtime_gate_reason, consensus_mode
            FROM audit_candidate_rows
            WHERE runtime_mode='live' AND market='KR' AND session_date=?
              AND consensus_mode IN ('MILD_BULL', 'MODERATE_BULL', 'AGGRESSIVE')
              AND trainer_candidate_state='PLAN_A'
              AND trainer_plan_a_score>=? AND trainer_risk_score<=?
              AND evidence_action_ceiling='BUY_READY'
              AND claude_action='WATCH'
              AND COALESCE(route_final_action, 'WATCH') IN ('', 'WATCH')
              AND COALESCE(route_runtime_gate_reason, '') IN ('', 'watch', 'judgment_not_executable')
            ORDER BY known_at, trainer_plan_a_score DESC, trainer_risk_score, prompt_rank, ticker
            """,
            (session_date, plan_a_min, risk_max),
        )
    ]
    eligible = []
    for row in rows:
        elapsed = _minutes_after_open(str(row.get("known_at") or ""))
        if elapsed is not None and 0.0 <= elapsed <= entry_window_max_min:
            eligible.append(row)
    if not eligible:
        return []
    first_known_at = str(eligible[0].get("known_at") or "")
    cohort = [row for row in eligible if str(row.get("known_at") or "") == first_known_at]
    cohort.sort(
        key=lambda row: (
            -float(row.get("trainer_plan_a_score") or 0.0),
            float(row.get("trainer_risk_score") or 999.0),
            str(row.get("ticker") or ""),
        )
    )
    selected = dict(cohort[0])
    selected.update(
        {
            "selection_source": "historical_rule_replay",
            "index_change_pct": index_change_pct,
            "breadth_up_ratio_pct": breadth_up_ratio_pct,
        }
    )
    return [selected]


def load_recorded_selections(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    columns = _columns(conn, "audit_candidate_rows")
    if "bullish_probe_selected" not in columns:
        return []
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT candidate_key, session_date, known_at, ticker, price,
                   trainer_plan_a_score, trainer_risk_score, bullish_probe_cost_pct,
                   bullish_probe_version, bullish_probe_reason,
                   bullish_probe_claude_reason, bullish_probe_recheck_condition
            FROM audit_candidate_rows
            WHERE runtime_mode='live' AND market='KR' AND bullish_probe_selected=1
            ORDER BY session_date, known_at
            """
        )
    ]
    distinct: dict[str, dict[str, Any]] = {}
    for row in rows:
        distinct.setdefault(str(row.get("session_date") or ""), row)
    return list(distinct.values())


def _path_rows(conn: sqlite3.Connection, selection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT candidate_key, path_name, known_at, trigger_time, entry_price, outcome_close_pct,
                   outcome_30m_pct, outcome_60m_pct, status, metadata_quality, label_source
            FROM candidate_counterfactual_paths
            WHERE runtime_mode='live' AND market='KR' AND session_date=? AND ticker=?
            ORDER BY known_at, id
            """,
            (selection.get("session_date"), selection.get("ticker")),
        )
    ]
    target_at = datetime.fromisoformat(str(selection.get("known_at") or "").replace("Z", "+00:00"))
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("candidate_key") or row.get("known_at") or "")].append(row)
    closest: list[dict[str, Any]] = []
    closest_delta = float("inf")
    for group in groups.values():
        try:
            known_at = datetime.fromisoformat(str(group[0].get("known_at") or "").replace("Z", "+00:00"))
            delta = abs((known_at - target_at).total_seconds())
        except Exception:
            continue
        if delta < closest_delta:
            closest_delta = delta
            closest = group
    if closest_delta > 120.0:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in closest:
        path = str(row.get("path_name") or "")
        if path in DEFAULT_PATHS and path not in out:
            out[path] = row
    return out


def _metrics(values: list[float]) -> dict[str, Any]:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    return {
        "n": len(values),
        "mean_net_pct": mean(values) if values else None,
        "median_net_pct": median(values) if values else None,
        "win_rate_pct": (sum(value > 0 for value in values) / len(values) * 100.0) if values else None,
        "profit_factor": (gains / losses) if losses > 0 else None,
        "worst_net_pct": min(values) if values else None,
    }


def build_report(
    conn: sqlite3.Connection,
    selections: list[dict[str, Any]],
    *,
    cost_pct: float = 0.5,
    order_cap_krw: float = 200000.0,
    minimum_sessions: int = 20,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    values_by_path: dict[str, list[float]] = defaultdict(list)
    for selection in selections:
        paths = _path_rows(conn, selection)
        for path in DEFAULT_PATHS:
            result = paths.get(path) or {}
            gross = result.get("outcome_close_pct")
            net = float(gross) - cost_pct if gross is not None else None
            entry_price = result.get("entry_price")
            quantity = int(order_cap_krw // float(entry_price)) if entry_price and float(entry_price) > 0 else 0
            invested_krw = quantity * float(entry_price) if quantity else 0.0
            row = {
                "session_date": selection.get("session_date"),
                "known_at": selection.get("known_at"),
                "ticker": selection.get("ticker"),
                "reference_price": selection.get("price"),
                "trainer_plan_a_score": selection.get("trainer_plan_a_score"),
                "trainer_risk_score": selection.get("trainer_risk_score"),
                "path_name": path,
                "entry_price": entry_price,
                "simulated_order_cap_krw": order_cap_krw,
                "simulated_quantity": quantity,
                "simulated_invested_krw": invested_krw,
                "gross_close_pct": gross,
                "assumed_round_trip_cost_pct": cost_pct,
                "net_close_pct": net,
                "simulated_net_pnl_krw": invested_krw * net / 100.0 if net is not None else None,
                "status": result.get("status") or "missing",
                "label_source": result.get("label_source") or "",
            }
            rows.append(row)
            if net is not None:
                values_by_path[path].append(net)
    metrics = {path: _metrics(values_by_path.get(path, [])) for path in DEFAULT_PATHS}
    matured_sessions = len({str(row.get("session_date")) for row in rows if row.get("net_close_pct") is not None})
    return {
        "version": "kr_bullish_strength_probe_report.v1",
        "shadow_only": True,
        "non_executable": True,
        "selection_sessions": len({str(row.get("session_date")) for row in selections}),
        "matured_sessions": matured_sessions,
        "minimum_sessions": minimum_sessions,
        "promotion_eligible": False,
        "promotion_reason": (
            "manual_review_required_after_minimum_sessions"
            if matured_sessions >= minimum_sessions
            else "insufficient_distinct_sessions"
        ),
        "assumed_round_trip_cost_pct": cost_pct,
        "simulated_order_cap_krw": order_cap_krw,
        "metrics_by_path": metrics,
        "rows": rows,
    }


def _write_report(payload: dict[str, Any], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    md = out.with_suffix(".md")
    lines = [
        "# KR Bullish Strength Probe Shadow Report",
        "",
        f"- selection_sessions: {payload['selection_sessions']}",
        f"- matured_sessions: {payload['matured_sessions']} / {payload['minimum_sessions']}",
        f"- assumed_round_trip_cost_pct: {payload['assumed_round_trip_cost_pct']}",
        f"- simulated_order_cap_krw: {payload['simulated_order_cap_krw']}",
        f"- promotion_eligible: {str(payload['promotion_eligible']).lower()} ({payload['promotion_reason']})",
        "",
        "| path | n | mean net % | median net % | win % | PF | worst net % |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for path, metric in payload["metrics_by_path"].items():
        def fmt(value: Any) -> str:
            if value is None:
                return "-"
            if isinstance(value, float) and math.isinf(value):
                return "inf"
            return f"{float(value):.4f}"
        lines.append(
            f"| {path} | {metric['n']} | {fmt(metric['mean_net_pct'])} | {fmt(metric['median_net_pct'])} | "
            f"{fmt(metric['win_rate_pct'])} | {fmt(metric['profit_factor'])} | {fmt(metric['worst_net_pct'])} |"
        )
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--replay-session", default="")
    parser.add_argument("--index-change-pct", type=float)
    parser.add_argument("--breadth-up-ratio-pct", type=float)
    parser.add_argument("--cost-pct", type=float, default=0.5)
    parser.add_argument("--order-cap-krw", type=float, default=200000.0)
    parser.add_argument("--minimum-sessions", type=int, default=20)
    parser.add_argument("--out", type=Path, default=Path("reports/kr_bullish_strength_probe_shadow.json"))
    args = parser.parse_args()
    conn = _connect(args.db)
    try:
        selections = load_recorded_selections(conn)
        if args.replay_session:
            if args.index_change_pct is None or args.breadth_up_ratio_pct is None:
                parser.error("--replay-session requires --index-change-pct and --breadth-up-ratio-pct")
            selections.extend(
                replay_session_selection(
                    conn,
                    session_date=args.replay_session,
                    index_change_pct=args.index_change_pct,
                    breadth_up_ratio_pct=args.breadth_up_ratio_pct,
                )
            )
        payload = build_report(
            conn,
            selections,
            cost_pct=args.cost_pct,
            order_cap_krw=args.order_cap_krw,
            minimum_sessions=args.minimum_sessions,
        )
    finally:
        conn.close()
    _write_report(payload, args.out)
    print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
