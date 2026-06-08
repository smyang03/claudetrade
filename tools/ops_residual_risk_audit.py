from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path
from tools.audit_ticker_selection_attribution import (
    DEFAULT_ML_DB,
    DEFAULT_SELECTION_DB,
    audit_ticker_selection_attribution,
)
from tools.candidate_audit_outcome_catchup import run_catchup
from tools.pathb_legacy_remediation import build_report as build_pathb_report


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ro_uri(path: Path) -> str:
    return f"file:{path.resolve().as_posix()}?mode=ro"


def _json_load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _jsonl_count(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except Exception:
        return 0


def _git_status(path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", str(path.relative_to(ROOT))],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except Exception as exc:
        return f"git_status_error:{type(exc).__name__}:{exc}"
    if result.returncode != 0:
        return f"git_status_error:{result.stderr.strip()}"
    return result.stdout.strip()


def _brain_status() -> dict[str, Any]:
    path = ROOT / "state" / "brain.json"
    data = _json_load(path)
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    queue = ROOT / "state" / "brain_approval_queue.jsonl"
    git_status = _git_status(path) if path.exists() else "missing"
    return {
        "path": str(path),
        "exists": path.exists(),
        "version": meta.get("version", data.get("version", "")),
        "last_updated": meta.get("last_updated", data.get("last_updated", "")),
        "git_status": git_status,
        "git_dirty": bool(git_status),
        "pending_approval_count": _jsonl_count(queue),
        "approval_queue_path": str(queue),
        "live_update_expected": True,
        "test_mode_handling": "sandbox_copy_only",
        "operator_action": (
            "review brain diff before treating policy memory as approved"
            if git_status
            else "none"
        ),
    }


def _candidate_outcome_status(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"exists": False, "path": str(db_path), "status": "db_missing"}
    try:
        with sqlite3.connect(_ro_uri(db_path), uri=True, timeout=10) as conn:
            tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "audit_candidate_outcomes" not in tables:
                return {"exists": True, "path": str(db_path), "status": "outcomes_table_missing"}
            total, latest = conn.execute(
                "SELECT COUNT(*), MAX(label_generated_at) FROM audit_candidate_outcomes"
            ).fetchone()
            rows = conn.execute(
                """
                SELECT horizon_min, COALESCE(NULLIF(status, ''), 'unknown') AS status,
                       COUNT(*) AS rows, MAX(label_generated_at) AS latest_label_generated_at
                FROM audit_candidate_outcomes
                GROUP BY horizon_min, status
                ORDER BY horizon_min, status
                """
            ).fetchall()
    except Exception as exc:
        return {"exists": True, "path": str(db_path), "status": "read_failed", "error": str(exc)}

    horizon_status = [
        {
            "horizon_min": int(row[0] or 0),
            "status": str(row[1] or "unknown"),
            "rows": int(row[2] or 0),
            "latest_label_generated_at": row[3] or "",
        }
        for row in rows
    ]
    daily_pending = sum(item["rows"] for item in horizon_status if item["status"] == "daily_pending")
    insufficient = sum(item["rows"] for item in horizon_status if item["status"] == "insufficient_samples")
    return {
        "exists": True,
        "path": str(db_path),
        "status": "ok",
        "rows": int(total or 0),
        "latest_label_generated_at": latest or "",
        "daily_pending_rows": daily_pending,
        "insufficient_sample_rows": insufficient,
        "horizon_status": horizon_status,
    }


def _safe(name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return {"ok": True, "name": name, "result": fn()}
    except Exception as exc:
        return {"ok": False, "name": name, "error": f"{type(exc).__name__}: {exc}"}


def _parse_horizons(raw: str) -> tuple[int, ...]:
    values = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if part:
            values.append(int(part))
    return tuple(values) or (30, 60, 1440, 2880, 4320)


def _selection_summary(section: dict[str, Any]) -> dict[str, Any]:
    if not section.get("ok"):
        return {"ok": False, "error": section.get("error", "")}
    summary = (section.get("result") or {}).get("summary") or {}
    return {
        "traded_rows": summary.get("traded_rows", 0),
        "missing_execution_decision_id_rows": summary.get("missing_execution_decision_id_rows", 0),
        "watch_only_traded_rows": summary.get("watch_only_traded_rows", 0),
        "exact_backfill_candidate_rows": summary.get("exact_backfill_candidate_rows", 0),
        "watch_only_split_review_rows": summary.get("watch_only_split_review_rows", 0),
        "no_touch_rows": summary.get("no_touch_rows", 0),
    }


def _pathb_summary(section: dict[str, Any]) -> dict[str, Any]:
    if not section.get("ok"):
        return {"ok": False, "error": section.get("error", "")}
    result = section.get("result") or {}
    lifecycle = result.get("lifecycle_full_consistency") or {}
    window = result.get("lifecycle_window_consistency") or {}
    cross = result.get("cross_run_closed_lifecycle_evidence") or {}
    return {
        "current_order_unknown": (result.get("order_unknown") or {}).get("current_count", 0),
        "previous_order_unknown": (result.get("order_unknown") or {}).get("previous_count", 0),
        "stale_active_rows": (result.get("stale_active") or {}).get("count", 0),
        "recent_window_missing_events": window.get("missing_events_count", 0),
        "full_terminal_missing_events": lifecycle.get("missing_events_count", 0),
        "cross_run_closed_lifecycle_evidence": cross.get("count", 0),
    }


def _catchup_summary(section: dict[str, Any]) -> dict[str, Any]:
    if not section.get("ok"):
        return {"ok": False, "error": section.get("error", "")}
    result = section.get("result") or {}
    return {
        "planned_sessions": len(result.get("planned") or []),
        "total_planned_outcome_rows": result.get("total_planned_outcome_rows", 0),
        "total_write_rows_if_approved": result.get("total_write_rows", 0),
        "total_outcome_rows_written": result.get("total_outcome_rows", 0),
        "dry_run": result.get("dry_run", True),
    }


def build_residual_risk_audit(
    *,
    mode: str = "live",
    pathb_limit: int = 200,
    selection_sample_limit: int = 50,
    selection_start_date: str = "",
    selection_end_date: str = "",
    candidate_days: int = 5,
    candidate_horizons: tuple[int, ...] = (30, 60, 1440, 2880, 4320),
    event_db: str | Path | None = None,
    selection_db: str | Path = DEFAULT_SELECTION_DB,
    ml_db: str | Path = DEFAULT_ML_DB,
    candidate_db: str | Path | None = None,
) -> dict[str, Any]:
    candidate_path = Path(candidate_db) if candidate_db else get_runtime_path("data", "audit", "candidate_audit.db", make_parents=False)
    sections = {
        "brain": _safe("brain", _brain_status),
        "pathb": _safe(
            "pathb",
            lambda: build_pathb_report(
                db_path=event_db,
                mode=mode,
                limit=pathb_limit,
            ),
        ),
        "selection_attribution": _safe(
            "selection_attribution",
            lambda: audit_ticker_selection_attribution(
                selection_db=Path(selection_db),
                ml_db=Path(ml_db),
                mode=mode,
                market="ALL",
                start_date=selection_start_date,
                end_date=selection_end_date,
                sample_limit=selection_sample_limit,
            ),
        ),
        "candidate_outcome_status": _safe(
            "candidate_outcome_status",
            lambda: _candidate_outcome_status(candidate_path),
        ),
        "candidate_outcome_catchup_dry_run": _safe(
            "candidate_outcome_catchup_dry_run",
            lambda: run_catchup(
                db_path=candidate_path,
                days=candidate_days,
                runtime_mode=mode,
                horizons=candidate_horizons,
                dry_run=True,
                write_report=False,
            ),
        ),
    }
    candidate_status = sections["candidate_outcome_status"].get("result") or {}
    summary = {
        "live_writes_performed": False,
        "brain": (sections["brain"].get("result") or {}),
        "pathb": _pathb_summary(sections["pathb"]),
        "selection_attribution": _selection_summary(sections["selection_attribution"]),
        "candidate_outcome_status": {
            "rows": candidate_status.get("rows", 0),
            "daily_pending_rows": candidate_status.get("daily_pending_rows", 0),
            "insufficient_sample_rows": candidate_status.get("insufficient_sample_rows", 0),
            "latest_label_generated_at": candidate_status.get("latest_label_generated_at", ""),
        },
        "candidate_outcome_catchup_dry_run": _catchup_summary(sections["candidate_outcome_catchup_dry_run"]),
    }
    next_actions = []
    if summary["brain"].get("git_dirty"):
        next_actions.append("brain_dirty_review_only: keep live updates running, but do not treat brain memory as approved until reviewed")
    if int(summary["pathb"].get("cross_run_closed_lifecycle_evidence") or 0) > 0:
        next_actions.append("pathb_closed_lifecycle_audit: verify affected historical rows against broker fills before any backfill")
    if int(summary["selection_attribution"].get("exact_backfill_candidate_rows") or 0) > 0:
        next_actions.append("selection_exact_backfill: apply only after reviewing exact candidate report and taking DB backup")
    if int(summary["selection_attribution"].get("watch_only_traded_rows") or 0) > 0:
        next_actions.append("selection_watch_only_review: keep out of automated learning attribution until manually split or marked contaminated")
    if int(summary["candidate_outcome_status"].get("daily_pending_rows") or 0) > 0:
        next_actions.append("candidate_outcome_refresh: run approved outcome catchup before using daily forward audit for learning")
    if not next_actions:
        next_actions.append("none")

    return {
        "ok": all(bool(section.get("ok")) for section in sections.values()),
        "generated_at": _utc_now(),
        "mode": mode,
        "dry_run": True,
        "live_writes_performed": False,
        "summary": summary,
        "next_actions": next_actions,
        "sections": sections,
    }


def _to_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Ops Residual Risk Audit",
        "",
        f"- generated_at: {report.get('generated_at', '')}",
        f"- mode: {report.get('mode', '')}",
        f"- dry_run: {report.get('dry_run')}",
        f"- live_writes_performed: {report.get('live_writes_performed')}",
        "",
        "## Summary",
        "",
        f"- brain_git_dirty: {(summary.get('brain') or {}).get('git_dirty')}",
        f"- PathB cross-run closed lifecycle evidence: {(summary.get('pathb') or {}).get('cross_run_closed_lifecycle_evidence')}",
        f"- PathB full terminal missing events: {(summary.get('pathb') or {}).get('full_terminal_missing_events')}",
        f"- selection missing execution id: {(summary.get('selection_attribution') or {}).get('missing_execution_decision_id_rows')}",
        f"- selection watch-only traded rows: {(summary.get('selection_attribution') or {}).get('watch_only_traded_rows')}",
        f"- candidate daily pending rows: {(summary.get('candidate_outcome_status') or {}).get('daily_pending_rows')}",
        f"- candidate catchup write rows if approved: {(summary.get('candidate_outcome_catchup_dry_run') or {}).get('total_write_rows_if_approved')}",
        "",
        "## Next Actions",
        "",
    ]
    for item in report.get("next_actions") or []:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _write_report(report: dict[str, Any], root: Path) -> dict[str, str]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "ops_residual_risk_audit.json"
    md_path = out_dir / "ops_residual_risk_audit.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_to_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only residual risk audit for live ops/test-mode readiness.")
    parser.add_argument("--mode", default="live", choices=["live", "paper"])
    parser.add_argument("--pathb-limit", type=int, default=200)
    parser.add_argument("--selection-sample-limit", type=int, default=50)
    parser.add_argument("--selection-start-date", default="")
    parser.add_argument("--selection-end-date", default="")
    parser.add_argument("--candidate-days", type=int, default=5)
    parser.add_argument("--candidate-horizons", default="30,60,1440,2880,4320")
    parser.add_argument("--event-db", default="")
    parser.add_argument("--selection-db", default=str(DEFAULT_SELECTION_DB))
    parser.add_argument("--ml-db", default=str(DEFAULT_ML_DB))
    parser.add_argument("--candidate-db", default="")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-root", default=str(ROOT / ".runtime" / "ops_residual_risk_audit"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = build_residual_risk_audit(
        mode=args.mode,
        pathb_limit=args.pathb_limit,
        selection_sample_limit=args.selection_sample_limit,
        selection_start_date=args.selection_start_date,
        selection_end_date=args.selection_end_date,
        candidate_days=args.candidate_days,
        candidate_horizons=_parse_horizons(args.candidate_horizons),
        event_db=args.event_db or None,
        selection_db=args.selection_db,
        ml_db=args.ml_db,
        candidate_db=args.candidate_db or None,
    )
    if args.write_report:
        report["report_paths"] = _write_report(report, Path(args.report_root).expanduser())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        summary = report["summary"]
        print(
            "ops residual risk audit "
            f"ok={str(report['ok']).lower()} dry_run=true live_writes=false "
            f"brain_dirty={summary['brain'].get('git_dirty')} "
            f"pathb_cross_run={summary['pathb'].get('cross_run_closed_lifecycle_evidence')} "
            f"selection_missing_id={summary['selection_attribution'].get('missing_execution_decision_id_rows')} "
            f"candidate_daily_pending={summary['candidate_outcome_status'].get('daily_pending_rows')}"
        )
        if report.get("report_paths"):
            paths = report["report_paths"]
            print(f"report_json={paths['json']}")
            print(f"report_md={paths['md']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
