from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phase1_trainer.external_data_store import DATA_TABLES, DEFAULT_DB_PATH, ExternalDataStore


def _now_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _failed_api_runs(path: Path) -> int:
    if not path.exists():
        return 0
    with sqlite3.connect(str(path)) as conn:
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "external_api_runs" not in tables:
            return 0
        return int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM external_api_runs
                WHERE lower(status) NOT IN ('ok', 'success', 'pass', 'ready')
                """
            ).fetchone()[0]
        )


def _table_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    return ExternalDataStore(path).readiness_summary(initialize=False).get("table_counts", {})


def _sqlite_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    target.with_name(target.name + "-wal").unlink(missing_ok=True)
    target.with_name(target.name + "-shm").unlink(missing_ok=True)
    with sqlite3.connect(str(source)) as src:
        with sqlite3.connect(str(target)) as dst:
            src.backup(dst)


def _delta_rows(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    tables = ["external_api_runs", *DATA_TABLES]
    return {table: int(after.get(table, 0)) - int(before.get(table, 0)) for table in tables}


def promote_external_data(
    *,
    source_db: str | Path,
    target_db: str | Path = DEFAULT_DB_PATH,
    backup_dir: str | Path | None = None,
    apply: bool = False,
    allow_failed_api_runs: bool = False,
) -> dict[str, Any]:
    source = Path(source_db).expanduser().resolve()
    target = Path(target_db).expanduser().resolve()
    backup_base = Path(backup_dir).expanduser().resolve() if backup_dir else target.parent / "backups"
    target_rows_before = _table_counts(target)
    report: dict[str, Any] = {
        "ok": False,
        "applied": False,
        "promotion_mode": "atomic_replace",
        "table_level_merge": "deferred",
        "source_db": str(source),
        "target_db": str(target),
        "backup_path": "",
        "source_readiness": {},
        "source_rows": {},
        "target_rows_before": target_rows_before,
        "target_rows_after": {},
        "delta_rows": {},
        "target_counts_before": target_rows_before,
        "target_counts_after": {},
        "failed_api_runs": 0,
        "errors": [],
    }
    if not source.exists():
        report["errors"].append("source_db_missing")
        return report
    source_readiness = ExternalDataStore(source).readiness_summary(initialize=False)
    report["source_readiness"] = source_readiness
    report["source_rows"] = dict(source_readiness.get("table_counts", {}) or {})
    if not source_readiness.get("production_ready"):
        report["errors"].append("source_not_production_ready")
    failed = _failed_api_runs(source)
    report["failed_api_runs"] = failed
    if failed and not allow_failed_api_runs:
        report["errors"].append("failed_api_runs_present")
    if report["errors"]:
        return report
    report["ok"] = True
    if not apply:
        return report

    backup_base.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = backup_base / f"{target.stem}.{_now_slug()}.bak{target.suffix}"
        _sqlite_backup(target, backup)
        report["backup_path"] = str(backup)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{_now_slug()}.tmp")
    try:
        _sqlite_backup(source, tmp)
        target.with_name(target.name + "-wal").unlink(missing_ok=True)
        target.with_name(target.name + "-shm").unlink(missing_ok=True)
        os.replace(tmp, target)
        report["applied"] = True
        target_rows_after = _table_counts(target)
        report["target_rows_after"] = target_rows_after
        report["target_counts_after"] = target_rows_after
        report["delta_rows"] = _delta_rows(target_rows_before, target_rows_after)
        return report
    except Exception as exc:
        report["ok"] = False
        report["errors"].append(f"atomic_replace_failed:{exc}")
        try:
            tmp.unlink(missing_ok=True)
            tmp.with_name(tmp.name + "-wal").unlink(missing_ok=True)
            tmp.with_name(tmp.name + "-shm").unlink(missing_ok=True)
        except Exception:
            pass
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Promote smoke external market data DB to production.")
    parser.add_argument("--source-db", required=True)
    parser.add_argument("--target-db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--backup-dir", default="")
    parser.add_argument("--allow-failed-api-runs", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    report = promote_external_data(
        source_db=args.source_db,
        target_db=args.target_db,
        backup_dir=args.backup_dir or None,
        apply=bool(args.apply),
        allow_failed_api_runs=bool(args.allow_failed_api_runs),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
