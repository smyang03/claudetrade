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


CONTAMINATION_WHERE = (
    "label = ? OR model LIKE ? OR raw_call_path LIKE ?"
)
CONTAMINATION_PARAMS = ("same_label", "%-test", "%CreatorTemp%")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _default_db_path() -> Path:
    return get_runtime_path("data", "audit", "agent_call_events.db")


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def scan_agent_call_event_store(db_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(db_path) if db_path is not None else _default_db_path()
    summary: dict[str, Any] = {
        "db_path": str(path),
        "exists": path.exists(),
        "matched_event_count": 0,
        "matched_replay_count": 0,
        "matched_call_ids": [],
        "matched_samples": [],
    }
    if not path.exists():
        return summary
    conn = _connect(path)
    try:
        tables = _tables(conn)
        if "agent_call_events" not in tables:
            summary["error"] = "agent_call_events table missing"
            return summary
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT call_id, label, market, call_date, model, raw_call_path
                FROM agent_call_events
                WHERE {CONTAMINATION_WHERE}
                ORDER BY known_at, call_id
                """,
                CONTAMINATION_PARAMS,
            ).fetchall()
        ]
        call_ids = [str(row.get("call_id") or "") for row in rows if row.get("call_id")]
        summary["matched_event_count"] = len(call_ids)
        summary["matched_call_ids"] = call_ids
        summary["matched_samples"] = rows[:20]
        if call_ids and "agent_call_replay_cache" in tables:
            placeholders = ",".join("?" for _ in call_ids)
            summary["matched_replay_count"] = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM agent_call_replay_cache WHERE call_id IN ({placeholders})",
                    call_ids,
                ).fetchone()[0]
            )
    finally:
        conn.close()
    return summary


def _sqlite_backup(conn: sqlite3.Connection, path: Path) -> Path:
    backup_path = path.with_name(f"{path.name}.{_utc_stamp()}.bak")
    backup = sqlite3.connect(str(backup_path))
    try:
        conn.backup(backup)
    finally:
        backup.close()
    return backup_path


def run(*, db_path: str | Path | None = None, apply: bool = False) -> dict[str, Any]:
    path = Path(db_path) if db_path is not None else _default_db_path()
    summary = scan_agent_call_event_store(path)
    summary.update(
        {
            "applied": bool(apply),
            "backup_path": "",
            "deleted_event_count": 0,
            "deleted_replay_count": 0,
        }
    )
    call_ids = list(summary.get("matched_call_ids") or [])
    if not apply or not call_ids or not path.exists():
        return summary

    conn = _connect(path)
    try:
        tables = _tables(conn)
        summary["backup_path"] = str(_sqlite_backup(conn, path))
        placeholders = ",".join("?" for _ in call_ids)
        conn.execute("BEGIN")
        if "agent_call_replay_cache" in tables:
            replay_cur = conn.execute(
                f"DELETE FROM agent_call_replay_cache WHERE call_id IN ({placeholders})",
                call_ids,
            )
            summary["deleted_replay_count"] = int(replay_cur.rowcount or 0)
        event_cur = conn.execute(
            f"DELETE FROM agent_call_events WHERE call_id IN ({placeholders})",
            call_ids,
        )
        summary["deleted_event_count"] = int(event_cur.rowcount or 0)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find and optionally remove test-contaminated rows from agent_call_events."
    )
    parser.add_argument("--db-path", default="", help="Defaults to data/audit/agent_call_events.db")
    parser.add_argument("--apply", action="store_true", help="Delete matched rows after creating a SQLite backup")
    parser.add_argument("--json", action="store_true", dest="print_json")
    args = parser.parse_args()

    summary = run(db_path=args.db_path or None, apply=args.apply)
    if args.print_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"db={summary['db_path']}")
        print(
            "matched_events={matched_event_count} matched_replay={matched_replay_count} "
            "applied={applied} deleted_events={deleted_event_count} deleted_replay={deleted_replay_count}".format(
                **summary
            )
        )
        if summary.get("backup_path"):
            print(f"backup={summary['backup_path']}")
        for sample in summary.get("matched_samples", [])[:10]:
            print(
                "sample call_id={call_id} label={label} model={model} raw_call_path={raw_call_path}".format(
                    **sample
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
