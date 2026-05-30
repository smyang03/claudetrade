from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path
from tools.order_unknown_evidence import (
    attach_exposure_evidence,
    load_broker_truth_snapshot_for_db,
    mark_order_unknown_remediation_hint,
    pathb_local_exposure_index,
    pathb_operator_context,
    safe_json_object,
    session_date_guess,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_db_path() -> Path:
    return get_runtime_path("data", "v2_event_store.db")


def _report_path(report_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return report_dir / f"order_unknown_remediation_{stamp}.json"


def _connect_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_write(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _load_broker_snapshot(mode: str, path: Path | None) -> dict[str, Any]:
    if path is not None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            return {"markets": {}, "load_error": f"{type(exc).__name__}: {exc}"}
    try:
        return load_broker_truth_snapshot_for_db(mode)
    except Exception as exc:
        return {"markets": {}, "load_error": f"{type(exc).__name__}: {exc}"}


def _scan_rows(
    db_path: Path,
    *,
    mode: str,
    market: str,
    session_before: str,
    path_run_id: str = "",
) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    where = [
        "runtime_mode=?",
        "market=?",
        "status='ORDER_UNKNOWN'",
        "path_type='claude_price'",
        "session_date < ?",
    ]
    params: list[Any] = [mode, market, session_before]
    if path_run_id:
        where.append("path_run_id=?")
        params.append(path_run_id)
    query = f"""
        SELECT market, runtime_mode, session_date, ticker, path_run_id, path_type, status, updated_at, plan_json
        FROM v2_path_runs
        WHERE {' AND '.join(where)}
        ORDER BY updated_at DESC
        LIMIT 200
    """
    with _connect_readonly(db_path) as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def evaluate_rows(
    rows: list[dict[str, Any]],
    *,
    mode: str,
    session_before: str,
    exposure_by_path: dict[str, dict[str, Any]] | None = None,
    broker_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if exposure_by_path is None:
        exposure_by_path, _by_ticker = pathb_local_exposure_index(mode)
    if broker_snapshot is None:
        broker_snapshot = _load_broker_snapshot(mode, None)
    evaluated: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        plan = safe_json_object(item.pop("plan_json", {}))
        plan_keys = sorted(plan.keys())
        item["plan_key_count"] = len(plan_keys)
        item["plan_keys_sample"] = plan_keys[:20]
        item.update(pathb_operator_context(item, plan))
        item["order_unknown_phase"] = str(plan.get("order_unknown_phase") or "")
        item["order_unknown_age_sec"] = plan.get("order_unknown_age_sec")
        item["order_unknown_reconcile_attempts"] = plan.get("order_unknown_reconcile_attempts")
        attach_exposure_evidence(item, exposure_by_path, broker_snapshot)
        mark_order_unknown_remediation_hint(
            item,
            mode=mode,
            previous_session=True,
            session_before=session_before,
        )
        item["planned_status"] = "CANCELLED" if item.get("remediation_allowed") else str(item.get("status") or "")
        evaluated.append(item)
    return evaluated


def _plan_updates(item: dict[str, Any]) -> dict[str, Any]:
    now = _utc_now_iso()
    return {
        "audited_remediation": True,
        "order_unknown_resolution": "audited_no_exposure_previous_session",
        "order_unknown_resolution_at": now,
        "order_unknown_remediation_allowed": True,
        "remediation_allowed": True,
        "remediation_blockers": [],
        "manual_reconciliation_required": False,
        "cancel_reason": "order_unknown_audited_no_exposure",
        "broker_truth_last_success_at": str(item.get("broker_truth_last_success_at") or ""),
        "remediation_source": "tools/order_unknown_remediation.py",
    }


def apply_remediation(db_path: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    now = _utc_now_iso()
    with _connect_write(db_path) as conn:
        for item in rows:
            if not bool(item.get("remediation_allowed")):
                continue
            path_run_id = str(item.get("path_run_id") or "").strip()
            if not path_run_id:
                continue
            current = conn.execute(
                "SELECT status, path_type, plan_json FROM v2_path_runs WHERE path_run_id=? AND path_type='claude_price'",
                (path_run_id,),
            ).fetchone()
            if current is None or str(current["status"] or "").upper() != "ORDER_UNKNOWN":
                continue
            current_plan = safe_json_object(current["plan_json"])
            next_plan = {**current_plan, **_plan_updates(item)}
            conn.execute(
                """
                UPDATE v2_path_runs
                SET status=?, plan_json=?, updated_at=?
                WHERE path_run_id=? AND status='ORDER_UNKNOWN' AND path_type='claude_price'
                """,
                ("CANCELLED", json.dumps(next_plan, ensure_ascii=False, sort_keys=True), now, path_run_id),
            )
            applied.append(
                {
                    "path_run_id": path_run_id,
                    "before_status": "ORDER_UNKNOWN",
                    "after_status": "CANCELLED",
                    "ticker": item.get("ticker"),
                    "market": item.get("market"),
                    "path_type": current["path_type"],
                    "session_date": item.get("session_date"),
                }
            )
        conn.commit()
    return applied


def build_report(
    *,
    db_path: Path,
    mode: str,
    market: str,
    session_before: str,
    rows: list[dict[str, Any]],
    apply: bool,
    applied: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    eligible = [row for row in rows if bool(row.get("remediation_allowed"))]
    blocked = [row for row in rows if not bool(row.get("remediation_allowed"))]
    return {
        "generated_at": _utc_now_iso(),
        "db_path": str(db_path),
        "mode": mode,
        "market": market,
        "session_before": session_before,
        "apply": bool(apply),
        "dry_run": not bool(apply),
        "total_count": len(rows),
        "eligible_count": len(eligible),
        "blocked_count": len(blocked),
        "applied_count": len(applied or []),
        "applied": applied or [],
        "rows": rows,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audited PathB ORDER_UNKNOWN remediation helper")
    parser.add_argument("--mode", choices=["live", "paper"], default="live")
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    parser.add_argument("--session-before", default="")
    parser.add_argument("--path-run-id", default="")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--broker-snapshot", default="")
    parser.add_argument("--report-dir", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-bulk-apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _print_cli_error(message: str, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"ERROR: {message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.apply and args.dry_run:
        _print_cli_error("--apply and --dry-run are mutually exclusive", json_output=bool(args.json))
        return 2
    if args.apply and not str(args.path_run_id or "").strip() and not args.allow_bulk_apply:
        _print_cli_error(
            "--apply requires --path-run-id unless --allow-bulk-apply is explicitly supplied",
            json_output=bool(args.json),
        )
        return 2
    mode = str(args.mode)
    market = str(args.market).upper()
    session_before = str(args.session_before or session_date_guess(market))
    db_path = Path(args.db_path).expanduser().resolve() if args.db_path else _default_db_path()
    broker_snapshot_path = Path(args.broker_snapshot).expanduser().resolve() if args.broker_snapshot else None
    report_dir = Path(args.report_dir).expanduser().resolve() if args.report_dir else get_runtime_path("data", "v2_reports")

    raw_rows = _scan_rows(
        db_path,
        mode=mode,
        market=market,
        session_before=session_before,
        path_run_id=str(args.path_run_id or "").strip(),
    )
    exposure_by_path, _by_ticker = pathb_local_exposure_index(mode)
    broker_snapshot = _load_broker_snapshot(mode, broker_snapshot_path)
    rows = evaluate_rows(
        raw_rows,
        mode=mode,
        session_before=session_before,
        exposure_by_path=exposure_by_path,
        broker_snapshot=broker_snapshot,
    )
    applied: list[dict[str, Any]] = []
    if args.apply:
        applied = apply_remediation(db_path, rows)
        report_dir.mkdir(parents=True, exist_ok=True)
        report = build_report(
            db_path=db_path,
            mode=mode,
            market=market,
            session_before=session_before,
            rows=rows,
            apply=True,
            applied=applied,
        )
        path = _report_path(report_dir)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_path"] = str(path)
    else:
        report = build_report(
            db_path=db_path,
            mode=mode,
            market=market,
            session_before=session_before,
            rows=rows,
            apply=False,
            applied=[],
        )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"ORDER_UNKNOWN remediation dry_run={not args.apply} "
            f"total={report['total_count']} eligible={report['eligible_count']} blocked={report['blocked_count']} "
            f"applied={report['applied_count']}"
        )
        if report.get("report_path"):
            print(f"report_path={report['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
