from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ops_us_high_price_simulation import (  # noqa: E402
    DEFAULT_EVENT_DB,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PERF_DB,
    KST,
    _as_float,
    _connect_ro,
    _json_obj,
    _output_dir,
    _stats,
)


def _now_text() -> str:
    return datetime.now(KST).replace(microsecond=0).isoformat()


def _sum_stats(values: list[float]) -> dict[str, Any]:
    vals = [float(value) for value in values if value is not None]
    return {"sum": round(sum(vals), 4), **_stats(vals)}


def _quality_reasons(raw: Any) -> list[str]:
    try:
        parsed = json.loads(str(raw or "[]"))
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _closed_without_fill_rows(perf_conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = perf_conn.execute(
        """
        select v2_decision_id, market, runtime_mode, session_date, ticker, status,
               route, path_type, path_run_id, strategy, origin_action, filled, closed,
               fill_event_id, close_event_id, filled_at, closed_at, entry_price,
               exit_price, qty, pnl_krw, pnl_pct, mfe_pct, mae_pct, close_reason,
               quality_grade, quality_reasons_json, learning_allowed
        from v2_learning_performance
        where runtime_mode='live'
          and closed=1
          and filled=0
        order by market, session_date, ticker, v2_decision_id
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["quality_reasons"] = _quality_reasons(item.get("quality_reasons_json"))
        if "CLOSED_WITHOUT_FILL" in item["quality_reasons"]:
            out.append(item)
    return out


def _events_for_case(
    event_conn: sqlite3.Connection,
    *,
    decision_id: str,
    path_run_id: str,
) -> list[dict[str, Any]]:
    rows = event_conn.execute(
        """
        select event_id, event_type, market, runtime_mode, session_date, ticker,
               decision_id, execution_id, occurred_at, reason_code, payload_json
        from lifecycle_events
        where decision_id = ?
           or payload_json like ?
        order by occurred_at, event_id
        """,
        (decision_id, f"%{path_run_id}%"),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["payload"] = _json_obj(item.pop("payload_json", "{}"))
        out.append(item)
    return out


def _path_runs_for_case(
    event_conn: sqlite3.Connection,
    *,
    decision_id: str,
    path_run_id: str,
) -> list[dict[str, Any]]:
    rows = event_conn.execute(
        """
        select path_run_id, decision_id, path_type, market, runtime_mode,
               session_date, ticker, status, plan_json, created_at, updated_at
        from v2_path_runs
        where decision_id = ?
           or path_run_id = ?
        order by created_at, path_run_id
        """,
        (decision_id, path_run_id),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["plan"] = _json_obj(item.pop("plan_json", "{}"))
        out.append(item)
    return out


def _entry_order_events(events: list[dict[str, Any]], path_run_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if str(event.get("event_type") or "") not in {"ORDER_SENT", "ORDER_ACKED"}:
            continue
        if str(payload.get("path_run_id") or "") != path_run_id:
            continue
        if str(payload.get("side") or "buy").lower() == "sell":
            continue
        out.append(event)
    return out


def _entry_fill_events(events: list[dict[str, Any]], path_run_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if str(event.get("event_type") or "") not in {"FILLED", "PARTIAL_FILLED"}:
            continue
        if str(payload.get("path_run_id") or "") != path_run_id:
            continue
        if str(payload.get("side") or "buy").lower() == "sell":
            continue
        out.append(event)
    return out


def _close_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if str(event.get("event_type") or "") == "CLOSED"]


def _plan_num(plan: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = _as_float(plan.get(key))
        if value > 0:
            return value
    return 0.0


def _classify_case(row: dict[str, Any], path_runs: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    row_path_run_id = str(row.get("path_run_id") or "")
    close_events = _close_events(events)
    linked_close_events = [
        event
        for event in close_events
        if str((event.get("payload") or {}).get("path_run_id") or "") == row_path_run_id
    ]
    any_close_path = next(
        (
            str((event.get("payload") or {}).get("path_run_id") or "")
            for event in close_events
            if str((event.get("payload") or {}).get("path_run_id") or "")
        ),
        "",
    )
    effective_path_run_id = any_close_path or row_path_run_id
    runs_by_id = {str(run.get("path_run_id") or ""): run for run in path_runs}
    effective_run = runs_by_id.get(effective_path_run_id) or runs_by_id.get(row_path_run_id) or (path_runs[0] if path_runs else {})
    effective_plan = effective_run.get("plan") if isinstance(effective_run.get("plan"), dict) else {}
    order_events = _entry_order_events(events, str(effective_run.get("path_run_id") or effective_path_run_id))
    fill_events = _entry_fill_events(events, str(effective_run.get("path_run_id") or effective_path_run_id))
    close_event = linked_close_events[0] if linked_close_events else (close_events[0] if close_events else {})
    close_payload = close_event.get("payload") if isinstance(close_event.get("payload"), dict) else {}
    row_path_run_status = str((runs_by_id.get(row_path_run_id) or {}).get("status") or "")
    effective_status = str(effective_run.get("status") or "")
    actual_entry = _plan_num(effective_plan, "actual_entry_price", "fill_price_native", "entry_price")
    filled_qty = _plan_num(effective_plan, "filled_qty", "entry_qty", "qty") or _as_float(close_payload.get("qty"))
    has_broker_close = bool(close_payload.get("broker_fill_confirmed") or close_payload.get("broker_filled_qty"))
    has_backfill_plan = bool(effective_plan.get("backfilled_from_broker_truth") or effective_plan.get("broker_today_fill_evidence"))
    path_run_mismatch = bool(any_close_path and any_close_path != row_path_run_id)
    manual_cleanup = effective_plan.get("manual_cleanup") if isinstance(effective_plan.get("manual_cleanup"), dict) else {}

    if fill_events:
        classification = "already_has_fill_event"
        confidence = "none"
        action = "resync_only"
    elif path_run_mismatch:
        classification = "path_run_attribution_mismatch"
        confidence = "high" if actual_entry > 0 and filled_qty > 0 and close_events else "medium"
        action = "repair_path_run_attribution_and_append_fill_event"
    elif linked_close_events and actual_entry > 0 and filled_qty > 0:
        classification = "linked_closed_without_fill_event"
        confidence = "high"
        action = "append_audited_fill_event_and_resync"
    elif close_events and order_events and manual_cleanup:
        classification = "closed_event_unlinked_manual_cleanup_conflict"
        confidence = "manual_review"
        action = "manual_review_before_backfill"
    elif close_events and order_events:
        classification = "closed_event_unlinked_to_path_run"
        confidence = "medium"
        action = "manual_review_before_backfill"
    else:
        classification = "insufficient_fill_evidence"
        confidence = "low"
        action = "do_not_backfill_without_broker_evidence"

    return {
        "v2_decision_id": row.get("v2_decision_id"),
        "market": row.get("market"),
        "session_date": row.get("session_date"),
        "ticker": row.get("ticker"),
        "strategy": row.get("strategy"),
        "path_type": row.get("path_type"),
        "row_path_run_id": row_path_run_id,
        "effective_path_run_id": str(effective_run.get("path_run_id") or effective_path_run_id),
        "row_path_run_status": row_path_run_status,
        "effective_path_run_status": effective_status,
        "path_run_mismatch": path_run_mismatch,
        "event_count": len(events),
        "path_run_count": len(path_runs),
        "entry_order_event_count": len(order_events),
        "entry_fill_event_count": len(fill_events),
        "close_event_count": len(close_events),
        "close_event_path_run_linked": bool(linked_close_events),
        "manual_cleanup_conflict": bool(manual_cleanup),
        "actual_entry_price": round(actual_entry, 6) if actual_entry else None,
        "filled_qty_evidence": round(filled_qty, 6) if filled_qty else None,
        "broker_fill_evidence": bool(has_broker_close or has_backfill_plan),
        "pnl_pct": round(_as_float(row.get("pnl_pct")), 6),
        "close_reason": row.get("close_reason"),
        "classification": classification,
        "repair_confidence": confidence,
        "recommended_action": action,
    }


def _perf_rows(perf_conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = perf_conn.execute(
        """
        select market, runtime_mode, path_type, strategy, filled, closed,
               learning_allowed, pnl_pct
        from v2_learning_performance
        where runtime_mode='live'
          and closed=1
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _market_path_stats(rows: list[dict[str, Any]], market: str, path_type: str) -> dict[str, Any]:
    values = [
        _as_float(row.get("pnl_pct"))
        for row in rows
        if str(row.get("market") or "") == market
        and str(row.get("path_type") or "") == path_type
        and int(row.get("filled") or 0) == 1
        and int(row.get("closed") or 0) == 1
    ]
    return _sum_stats(values)


def _distortion_summary(perf_rows: list[dict[str, Any]], cases: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[(str(case.get("market") or ""), str(case.get("path_type") or ""))].append(case)

    groups: dict[str, Any] = {}
    for (market, path_type), group_cases in sorted(grouped.items()):
        current = _market_path_stats(perf_rows, market, path_type)
        high_cases = [case for case in group_cases if case.get("repair_confidence") == "high"]
        repairable_cases = [
            case
            for case in group_cases
            if str(case.get("repair_confidence") or "") in {"high", "medium", "manual_review"}
        ]
        current_values = [
            _as_float(row.get("pnl_pct"))
            for row in perf_rows
            if str(row.get("market") or "") == market
            and str(row.get("path_type") or "") == path_type
            and int(row.get("filled") or 0) == 1
            and int(row.get("closed") or 0) == 1
        ]
        high_values = current_values + [_as_float(case.get("pnl_pct")) for case in high_cases]
        repairable_values = current_values + [_as_float(case.get("pnl_pct")) for case in repairable_cases]
        groups[f"{market}:{path_type}"] = {
            "current_filled_closed": current,
            "closed_without_fill": _sum_stats([_as_float(case.get("pnl_pct")) for case in group_cases]),
            "adjusted_high_confidence_only": _sum_stats(high_values),
            "adjusted_repairable_or_review": _sum_stats(repairable_values),
            "high_confidence_count": len(high_cases),
            "repairable_or_review_count": len(repairable_cases),
            "delta_avg_high_confidence": round(_sum_stats(high_values)["avg"] - current["avg"], 4),
            "delta_avg_repairable_or_review": round(_sum_stats(repairable_values)["avg"] - current["avg"], 4),
        }
    return groups


def _render_md(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# CLOSED_WITHOUT_FILL audit",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- live_writes_performed: {payload['live_writes_performed']}",
        f"- case_count: {summary['case_count']}",
        f"- classification_counts: {json.dumps(summary['classification_counts'], ensure_ascii=False, sort_keys=True)}",
        f"- repair_confidence_counts: {json.dumps(summary['repair_confidence_counts'], ensure_ascii=False, sort_keys=True)}",
        "",
        "## Performance Distortion",
    ]
    for key, item in summary["performance_distortion"].items():
        lines.extend(
            [
                "",
                f"### {key}",
                f"- current_filled_closed: {json.dumps(item['current_filled_closed'], ensure_ascii=False, sort_keys=True)}",
                f"- closed_without_fill: {json.dumps(item['closed_without_fill'], ensure_ascii=False, sort_keys=True)}",
                f"- adjusted_high_confidence_only: {json.dumps(item['adjusted_high_confidence_only'], ensure_ascii=False, sort_keys=True)}",
                f"- adjusted_repairable_or_review: {json.dumps(item['adjusted_repairable_or_review'], ensure_ascii=False, sort_keys=True)}",
                f"- delta_avg_high_confidence: {item['delta_avg_high_confidence']}",
                f"- delta_avg_repairable_or_review: {item['delta_avg_repairable_or_review']}",
            ]
        )
    lines.extend(["", "## Cases"])
    for case in summary["cases"]:
        lines.extend(
            [
                "",
                f"### {case['session_date']} {case['ticker']}",
                f"- decision_id: `{case['v2_decision_id']}`",
                f"- row_path_run_id: `{case['row_path_run_id']}`",
                f"- effective_path_run_id: `{case['effective_path_run_id']}`",
                f"- classification: `{case['classification']}`",
                f"- repair_confidence: `{case['repair_confidence']}`",
                f"- recommended_action: `{case['recommended_action']}`",
                f"- pnl_pct: {case['pnl_pct']}",
                f"- broker_fill_evidence: {case['broker_fill_evidence']}",
                f"- manual_cleanup_conflict: {case['manual_cleanup_conflict']}",
            ]
        )
    lines.extend(
        [
            "",
            "## Apply Prohibitions",
            "",
            "- Do not write live DB from this report.",
            "- Do not backfill rows with manual cleanup conflict without operator review.",
            "- Do not change slippage, sizing, broker truth, or exit policies based on these attribution rows.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_closed_without_fill_audit(
    *,
    event_db: str | Path = DEFAULT_EVENT_DB,
    perf_db: str | Path = DEFAULT_PERF_DB,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    output_dir: str = "",
) -> dict[str, Any]:
    out_dir = _output_dir(Path(output_root), output_dir)
    with _connect_ro(Path(perf_db)) as perf_conn, _connect_ro(Path(event_db)) as event_conn:
        cwf_rows = _closed_without_fill_rows(perf_conn)
        perf_rows = _perf_rows(perf_conn)
        cases = []
        for row in cwf_rows:
            events = _events_for_case(
                event_conn,
                decision_id=str(row.get("v2_decision_id") or ""),
                path_run_id=str(row.get("path_run_id") or ""),
            )
            path_runs = _path_runs_for_case(
                event_conn,
                decision_id=str(row.get("v2_decision_id") or ""),
                path_run_id=str(row.get("path_run_id") or ""),
            )
            cases.append(_classify_case(row, path_runs, events))

    payload = {
        "ok": True,
        "generated_at": _now_text(),
        "live_writes_performed": False,
        "inputs": {"event_db": str(Path(event_db)), "perf_db": str(Path(perf_db))},
        "summary": {
            "case_count": len(cases),
            "classification_counts": dict(sorted(Counter(str(case.get("classification") or "") for case in cases).items())),
            "repair_confidence_counts": dict(sorted(Counter(str(case.get("repair_confidence") or "") for case in cases).items())),
            "performance_distortion": _distortion_summary(perf_rows, cases),
            "cases": cases,
        },
        "apply_prohibitions": [
            "do not write live DB from this report",
            "do not backfill manual-cleanup-conflict rows without operator review",
            "do not change live slippage/sizing/exit policy from attribution-only rows",
        ],
    }
    json_path = out_dir / "closed_without_fill_audit.json"
    md_path = out_dir / "closed_without_fill_audit.md"
    csv_path = out_dir / "closed_without_fill_cases.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_md(payload), encoding="utf-8")
    _write_csv(csv_path, cases)
    payload["output_paths"] = {"json": str(json_path), "md": str(md_path), "csv": str(csv_path)}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only CLOSED_WITHOUT_FILL attribution audit.")
    parser.add_argument("--event-db", default=str(DEFAULT_EVENT_DB))
    parser.add_argument("--perf-db", default=str(DEFAULT_PERF_DB))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = build_closed_without_fill_audit(
        event_db=args.event_db,
        perf_db=args.perf_db,
        output_root=args.output_root,
        output_dir=args.output_dir,
    )
    if args.json:
        print(json.dumps({"ok": payload["ok"], "summary": payload["summary"], "output_paths": payload["output_paths"]}, ensure_ascii=False, indent=2))
    else:
        print(
            "closed_without_fill_audit "
            f"cases={payload['summary']['case_count']} "
            f"outputs={payload['output_paths']['md']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
