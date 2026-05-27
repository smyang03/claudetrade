from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any


DEFAULT_DB = Path("data/audit/candidate_audit.db")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _json_obj(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw or "{}"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        ticker = ""
        if isinstance(item, dict):
            ticker = str(item.get("ticker") or "").strip()
        else:
            ticker = str(item or "").strip()
        if ticker and ticker not in result:
            result.append(ticker)
    return result


def _pf(values: list[float]) -> float | None:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    if losses == 0:
        return round(gains, 4) if gains > 0 else None
    return round(gains / losses, 4)


def _metrics(values: list[float]) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None]
    return {
        "n": len(clean),
        "avg": round(mean(clean), 4) if clean else None,
        "median": round(median(clean), 4) if clean else None,
        "win_rate_pct": round(100.0 * sum(1 for value in clean if value > 0) / len(clean), 2) if clean else None,
        "pf": _pf(clean),
    }


def _top_day_contribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_day: dict[str, float] = defaultdict(float)
    for row in rows:
        value = row.get("return_pct")
        if value is None:
            continue
        by_day[str(row.get("session_date") or "")] += float(value)
    positive = {day: total for day, total in by_day.items() if total > 0}
    total_positive = sum(positive.values())
    if total_positive <= 0 or not positive:
        return {"top_day": "", "top_day_sum": None, "top_day_contribution_pct": None}
    top_day, top_sum = max(positive.items(), key=lambda item: item[1])
    return {
        "top_day": top_day,
        "top_day_sum": round(top_sum, 4),
        "top_day_contribution_pct": round(100.0 * top_sum / total_positive, 2),
    }


def _where(args: argparse.Namespace, alias: str = "c") -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if args.market:
        clauses.append(f"{alias}.market=?")
        params.append(str(args.market).upper())
    if args.runtime_mode:
        clauses.append(f"{alias}.runtime_mode=?")
        params.append(str(args.runtime_mode).lower())
    if args.date_from:
        clauses.append(f"{alias}.session_date>=?")
        params.append(args.date_from)
    if args.date_to:
        clauses.append(f"{alias}.session_date<=?")
        params.append(args.date_to)
    return (" AND " + " AND ".join(clauses) if clauses else ""), params


def _returns_for_tickers(
    conn: sqlite3.Connection,
    *,
    call_id: str,
    tickers: list[str],
    horizon_min: int,
) -> list[dict[str, Any]]:
    if not tickers:
        return []
    placeholders = ",".join("?" for _ in tickers)
    rows = conn.execute(
        f"""
        SELECT r.market, r.session_date, r.ticker, o.return_pct
        FROM audit_candidate_rows r
        JOIN audit_candidate_outcomes o
          ON o.candidate_key = r.candidate_key
         AND o.horizon_min = ?
        WHERE r.call_id = ?
          AND r.ticker IN ({placeholders})
          AND o.return_pct IS NOT NULL
        """,
        [horizon_min, call_id, *tickers],
    ).fetchall()
    return [dict(row) for row in rows]


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    db_path = Path(args.db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row_columns = _columns(conn, "audit_candidate_rows")
        prompt_flags: list[str] = []
        if "actual_prompt_included" in row_columns:
            prompt_flags.append("r.actual_prompt_included")
            measured_expr = "r.actual_prompt_included IS NOT NULL"
        else:
            measured_expr = "0"
        if "final_prompt_included" in row_columns:
            prompt_flags.append("r.final_prompt_included")
        prompt_flags.append("r.input_to_claude_reported")
        current_prompt_predicate = f"COALESCE({', '.join(prompt_flags)})=1"

        where_calls, call_params = _where(args, alias="c")
        calls = conn.execute(
            f"""
            SELECT c.call_id, c.market, c.session_date, c.runtime_mode, c.payload_json
            FROM audit_claude_calls c
            WHERE c.label='selection_meta_live'
            {where_calls}
            ORDER BY c.session_date, c.market, c.called_at
            """,
            call_params,
        ).fetchall()

        where_rows, row_params = _where(args, alias="r")
        current_rows = conn.execute(
            f"""
            SELECT r.market, r.session_date, r.ticker, o.return_pct
            FROM audit_candidate_rows r
            JOIN audit_candidate_outcomes o
              ON o.candidate_key = r.candidate_key
             AND o.horizon_min = ?
            WHERE {current_prompt_predicate}
              AND o.return_pct IS NOT NULL
            {where_rows}
            """,
            [args.horizon_min, *row_params],
        ).fetchall()
        visibility_rows = conn.execute(
            f"""
            SELECT
              COUNT(*) AS rows,
              COALESCE(SUM(CASE WHEN {measured_expr} THEN 1 ELSE 0 END), 0) AS measured_rows
            FROM audit_candidate_rows r
            WHERE 1=1
            {where_rows}
            """,
            row_params,
        ).fetchone()

        overlay_rows: list[dict[str, Any]] = []
        overlay_added_rows: list[dict[str, Any]] = []
        overlay_days: set[tuple[str, str]] = set()
        plan_a_days: set[tuple[str, str]] = set()
        plan_a_zero_cycles = 0
        plan_b_fallback_count = 0
        mode_counts: dict[str, int] = defaultdict(int)

        for call in calls:
            payload = _json_obj(call["payload_json"])
            mode = str(payload.get("overlay_mode") or "current_only")
            mode_counts[mode] += 1
            if bool(payload.get("overlay_plan_b_used")):
                plan_b_fallback_count += 1
            plan_a_available = int(payload.get("shadow_overlay_plan_a_available") or payload.get("overlay_plan_a_available") or 0)
            plan_a_in_prompt = int(payload.get("plan_a_in_prompt") or 0)
            day_key = (str(call["market"]), str(call["session_date"]))
            if plan_a_available > 0 or plan_a_in_prompt > 0:
                plan_a_days.add(day_key)
            else:
                plan_a_zero_cycles += 1

            shadow_tickers = _list(payload.get("shadow_overlay_tickers"))
            live_tickers = _list(payload.get("actual_prompt_tickers")) if mode == "live" else []
            pool_tickers = shadow_tickers or live_tickers
            added_tickers = _list(payload.get("shadow_overlay_added_tickers")) or _list(payload.get("overlay_added_tickers"))
            if added_tickers:
                overlay_days.add(day_key)
            overlay_rows.extend(
                _returns_for_tickers(
                    conn,
                    call_id=str(call["call_id"]),
                    tickers=pool_tickers,
                    horizon_min=args.horizon_min,
                )
            )
            overlay_added_rows.extend(
                _returns_for_tickers(
                    conn,
                    call_id=str(call["call_id"]),
                    tickers=added_tickers,
                    horizon_min=args.horizon_min,
                )
            )

        current_values = [float(row["return_pct"]) for row in current_rows]
        overlay_values = [float(row["return_pct"]) for row in overlay_rows]
        overlay_added_values = [float(row["return_pct"]) for row in overlay_added_rows]
        visibility_summary = dict(visibility_rows or {})
        visibility_total = int(visibility_summary.get("rows") or 0)
        visibility_measured = int(visibility_summary.get("measured_rows") or 0)
        gate = {
            "shadow_days_min_10": len({(str(row["market"]), str(row["session_date"])) for row in calls}) >= 10,
            "overlay_days_min_4": len(overlay_days) >= 4,
            "overlay_triggered_pf_gt_1": (_pf(overlay_added_values) or 0) > 1.0,
            "top_day_contribution_lt_40": (
                (_top_day_contribution(overlay_added_rows).get("top_day_contribution_pct") or 999) < 40
            ),
            "plan_b_fallback_zero": plan_b_fallback_count == 0,
        }
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "db_path": str(db_path),
            "filters": {
                "market": args.market,
                "runtime_mode": args.runtime_mode,
                "date_from": args.date_from,
                "date_to": args.date_to,
                "horizon_min": args.horizon_min,
            },
            "call_count": len(calls),
            "mode_counts": dict(mode_counts),
            "overlay_day_count": len(overlay_days),
            "plan_a_coverage_day_count": len(plan_a_days),
            "plan_a_zero_cycles": plan_a_zero_cycles,
            "plan_b_fallback_count": plan_b_fallback_count,
            "visibility_measurement": {
                "rows": visibility_total,
                "measured_rows": visibility_measured,
                "unmeasured_rows": visibility_total - visibility_measured,
            },
            "current_pool_pf": _metrics(current_values),
            "shadow_overlay_pool_pf": _metrics(overlay_values),
            "overlay_triggered_pf": _metrics(overlay_added_values),
            "top_day_contribution": _top_day_contribution(overlay_added_rows),
            "gate": gate,
            "gate_pass": all(gate.values()),
        }
    finally:
        conn.close()


def _markdown(payload: dict[str, Any]) -> str:
    current = payload["current_pool_pf"]
    shadow = payload["shadow_overlay_pool_pf"]
    triggered = payload["overlay_triggered_pf"]
    top = payload["top_day_contribution"]
    gate = payload["gate"]
    visibility = payload.get("visibility_measurement") or {}
    lines = [
        "# Prompt Overlay Shadow Analysis",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- db_path: {payload['db_path']}",
        f"- call_count: {payload['call_count']}",
        f"- mode_counts: {payload['mode_counts']}",
        f"- overlay_day_count: {payload['overlay_day_count']}",
        f"- plan_a_coverage_day_count: {payload['plan_a_coverage_day_count']}",
        f"- plan_a_zero_cycles: {payload['plan_a_zero_cycles']}",
        f"- plan_b_fallback_count: {payload['plan_b_fallback_count']}",
        f"- visibility_measured_rows: {visibility.get('measured_rows')}",
        f"- visibility_unmeasured_rows: {visibility.get('unmeasured_rows')}",
        "",
        "| pool | n | avg | median | win_rate_pct | pf |",
        "|---|---:|---:|---:|---:|---:|",
        f"| current | {current['n']} | {current['avg']} | {current['median']} | {current['win_rate_pct']} | {current['pf']} |",
        f"| shadow_overlay | {shadow['n']} | {shadow['avg']} | {shadow['median']} | {shadow['win_rate_pct']} | {shadow['pf']} |",
        f"| overlay_triggered | {triggered['n']} | {triggered['avg']} | {triggered['median']} | {triggered['win_rate_pct']} | {triggered['pf']} |",
        "",
        f"- top_day: {top['top_day']}",
        f"- top_day_contribution_pct: {top['top_day_contribution_pct']}",
        f"- gate_pass: {payload['gate_pass']}",
        "",
        "| gate | pass |",
        "|---|---:|",
    ]
    lines.extend(f"| {key} | {value} |" for key, value in gate.items())
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze prompt overlay shadow outcomes from candidate audit DB.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB))
    parser.add_argument("--market", choices=["KR", "US"])
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--date-from", default="")
    parser.add_argument("--date-to", default="")
    parser.add_argument("--horizon-min", type=int, default=60)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    args = parser.parse_args()

    payload = analyze(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if args.md_out:
        Path(args.md_out).write_text(_markdown(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
