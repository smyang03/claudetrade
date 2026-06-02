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

from audit.candidate_audit_store import CandidateAuditStore
from runtime_paths import get_runtime_path


JSON_COLUMNS = {"evidence_missing_fields_json", "post_open_features_json", "kr_confirmation_snapshot_json"}
MISSING_QUALITY = {"", "missing", "unknown", "none", "null"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _decode_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, (tuple, set)):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return list(parsed)
        except Exception:
            pass
        return [text]
    return [value]


def _boolish(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _runtime_gate_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    runtime_gate = payload.get("runtime_gate")
    if isinstance(runtime_gate, dict):
        return dict(runtime_gate)
    route = payload.get("route")
    if isinstance(route, dict) and isinstance(route.get("runtime_gate"), dict):
        return dict(route.get("runtime_gate") or {})
    return {}


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        parsed = _decode_json(value)
        if parsed:
            return parsed
    return {}


def _confirmation_snapshot(gate: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = _first_dict(
        payload.get("kr_confirmation_snapshot"),
        payload.get("kr_confirmation_snapshot_json"),
        gate.get("kr_confirmation_snapshot"),
    )
    if snapshot:
        return snapshot
    keys = (
        "kr_confirmation_state",
        "kr_confirmation_reason",
        "kr_confirmation_checks",
        "kr_confirmation_score",
        "kr_confirmation_score_items",
        "kr_confirmation_threshold",
        "kr_confirmation_fast_window_ok",
        "kr_confirmation_fast_window_elapsed_min",
        "kr_confirmation_fast_window_min",
        "kr_confirmation_fast_window_elapsed_missing",
    )
    return {key: gate.get(key) for key in keys if gate.get(key) not in (None, "", [], {})}


def _desired_values(payload: dict[str, Any]) -> dict[str, Any]:
    gate = _runtime_gate_from_payload(payload)
    evidence_pack = _decode_json(gate.get("evidence_pack"))
    post_open = _first_dict(
        payload.get("post_open_features"),
        payload.get("post_open_features_json"),
        gate.get("post_open_features"),
        evidence_pack.get("post_open_confirmation"),
    )
    quality = (
        gate.get("data_quality")
        or post_open.get("data_quality")
        or evidence_pack.get("data_quality")
        or payload.get("data_quality")
    )
    quality_text = str(quality or "").strip()
    desired: dict[str, Any] = {}
    if quality_text:
        desired["data_quality"] = quality_text
    if gate.get("data_quality_missing") not in (None, ""):
        desired["data_quality_missing"] = 1 if _boolish(gate.get("data_quality_missing")) else 0
    elif quality_text:
        desired["data_quality_missing"] = 1 if quality_text.lower() in MISSING_QUALITY else 0
    evidence_state = str(gate.get("evidence_data_state") or evidence_pack.get("data_state") or "").strip()
    if evidence_state:
        desired["evidence_data_state"] = evidence_state
    missing = _as_list(gate.get("evidence_missing_fields") or evidence_pack.get("missing_fields"))
    if missing:
        desired["evidence_missing_fields_json"] = missing
    if post_open:
        desired["post_open_features_json"] = post_open
    confirmation = _confirmation_snapshot(gate, payload)
    if confirmation:
        desired["kr_confirmation_snapshot_json"] = confirmation
    return desired


def _current_empty(column: str, value: Any) -> bool:
    if column == "data_quality_missing":
        return value is None
    if value is None:
        return True
    if column in JSON_COLUMNS:
        if str(value or "").strip() in {"", "{}", "[]"}:
            return True
        parsed = _decode_json(value)
        if parsed:
            return False
        try:
            parsed_list = json.loads(str(value))
            return parsed_list in ({}, [])
        except Exception:
            return False
    return str(value or "").strip() == ""


def _same_value(column: str, current: Any, desired: Any) -> bool:
    if column == "data_quality_missing":
        if current is None:
            return False
        try:
            return int(current) == int(desired)
        except Exception:
            return False
    if column in JSON_COLUMNS:
        try:
            current_parsed = json.loads(str(current or "{}"))
        except Exception:
            current_parsed = current
        return current_parsed == desired
    return str(current or "").strip() == str(desired or "").strip()


def _connect(path: Path, *, readonly: bool) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    else:
        conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(audit_candidate_rows)").fetchall()}


def _row_query(columns: set[str], *, where_sql: str) -> str:
    optional = [
        column
        for column in (
            "data_quality",
            "data_quality_missing",
            "evidence_data_state",
            "evidence_missing_fields_json",
            "post_open_features_json",
            "kr_confirmation_snapshot_json",
        )
        if column in columns
    ]
    selected = ", ".join(["candidate_key", "market", "session_date", "ticker", "payload_json", *optional])
    return f"SELECT {selected} FROM audit_candidate_rows {where_sql} ORDER BY market, session_date, ticker"


def build_runtime_evidence_backfill_plan(
    *,
    db_path: Path,
    runtime_mode: str = "live",
    market: str = "",
    session_date: str = "",
    limit: int = 0,
    overwrite: bool = False,
) -> dict[str, Any]:
    if not db_path.exists():
        return {"ok": False, "error": f"missing db: {db_path}", "db_path": str(db_path)}
    where = ["runtime_mode=?"]
    params: list[Any] = [str(runtime_mode or "live").lower()]
    if market:
        where.append("market=?")
        params.append(str(market).upper())
    if session_date:
        where.append("session_date=?")
        params.append(str(session_date))
    where_sql = "WHERE " + " AND ".join(where)
    if limit > 0:
        where_sql += f" LIMIT {int(limit)}"

    eligible: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    scanned = 0
    with _connect(db_path, readonly=True) as conn:
        cols = _columns(conn)
        missing_schema = sorted(
            {
                "data_quality",
                "data_quality_missing",
                "evidence_data_state",
                "evidence_missing_fields_json",
                "post_open_features_json",
                "kr_confirmation_snapshot_json",
            }
            - cols
        )
        for row in conn.execute(_row_query(cols, where_sql=where_sql), params).fetchall():
            scanned += 1
            payload = _decode_json(row["payload_json"])
            desired = _desired_values(payload)
            if not desired:
                continue
            updates: dict[str, Any] = {}
            row_conflicts: dict[str, dict[str, Any]] = {}
            for column, desired_value in desired.items():
                if column not in cols:
                    updates[column] = desired_value
                    continue
                current = row[column]
                if _same_value(column, current, desired_value):
                    continue
                if _current_empty(column, current) or overwrite:
                    updates[column] = desired_value
                else:
                    row_conflicts[column] = {"current": current, "desired": desired_value}
            if updates:
                eligible.append(
                    {
                        "candidate_key": row["candidate_key"],
                        "market": row["market"],
                        "session_date": row["session_date"],
                        "ticker": row["ticker"],
                        "updates": updates,
                    }
                )
            if row_conflicts:
                conflicts.append(
                    {
                        "candidate_key": row["candidate_key"],
                        "market": row["market"],
                        "session_date": row["session_date"],
                        "ticker": row["ticker"],
                        "conflicts": row_conflicts,
                    }
                )
    return {
        "ok": True,
        "db_path": str(db_path),
        "runtime_mode": str(runtime_mode or "live").lower(),
        "market": str(market or "").upper(),
        "session_date": str(session_date or ""),
        "overwrite": bool(overwrite),
        "scanned_count": scanned,
        "eligible_count": len(eligible),
        "conflict_count": len(conflicts),
        "missing_schema_columns": missing_schema,
        "eligible": eligible,
        "conflicts": conflicts[:50],
    }


def apply_runtime_evidence_backfill(db_path: Path, plan: dict[str, Any]) -> int:
    CandidateAuditStore(db_path)
    rows = list(plan.get("eligible") or [])
    if not rows:
        return 0
    now = _utc_now()
    with _connect(db_path, readonly=False) as conn:
        cols = _columns(conn)
        applied = 0
        for item in rows:
            updates = {
                key: value
                for key, value in dict(item.get("updates") or {}).items()
                if key in cols
            }
            if not updates:
                continue
            updates["updated_at"] = now
            serialized = []
            for key, value in updates.items():
                if key in JSON_COLUMNS and not isinstance(value, str):
                    serialized.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
                else:
                    serialized.append(value)
            set_clause = ", ".join(f"{key}=?" for key in updates)
            conn.execute(
                f"UPDATE audit_candidate_rows SET {set_clause} WHERE candidate_key=?",
                [*serialized, item.get("candidate_key")],
            )
            applied += 1
        conn.commit()
    return applied


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill candidate audit evidence columns from payload runtime_gate.")
    parser.add_argument("--db-path", default=str(get_runtime_path("data", "audit", "candidate_audit.db")))
    parser.add_argument("--mode", default="live", choices=["live", "paper"])
    parser.add_argument("--market", default="", choices=["", "KR", "US"])
    parser.add_argument("--date", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.apply and args.dry_run:
        print("--apply and --dry-run are mutually exclusive", file=sys.stderr)
        return 2
    db_path = Path(args.db_path)
    plan = build_runtime_evidence_backfill_plan(
        db_path=db_path,
        runtime_mode=args.mode,
        market=args.market,
        session_date=args.date,
        limit=max(0, int(args.limit or 0)),
        overwrite=bool(args.overwrite),
    )
    applied = 0
    if args.apply and plan.get("ok"):
        applied = apply_runtime_evidence_backfill(db_path, plan)
    report = {
        **plan,
        "dry_run": not bool(args.apply),
        "apply": bool(args.apply),
        "applied_count": applied,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        print(
            "candidate audit runtime evidence backfill "
            f"dry_run={report['dry_run']} scanned={report.get('scanned_count', 0)} "
            f"eligible={report.get('eligible_count', 0)} conflicts={report.get('conflict_count', 0)} "
            f"applied={applied}"
        )
        if report.get("missing_schema_columns"):
            print(f"missing_schema_columns={','.join(report['missing_schema_columns'])}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
