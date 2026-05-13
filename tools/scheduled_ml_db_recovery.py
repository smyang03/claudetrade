from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BACKUP_PATH = ROOT / "data" / "ml" / "decisions_before_backfill_refresh_20260403_221805.db"
CURRENT_PATH = ROOT / "data" / "ml" / "decisions.db"
STAGING_DIR = ROOT / "data" / "ml" / "recovery_staging"
LOG_DIR = ROOT / "logs"

TASK_NAME = "ClaudeTrade_MLDB_Recovery_20260513_0600"
WRITER_PATTERNS = (
    "trading_bot.py",
    "ml/test_full.py",
    "ml\\test_full.py",
    "ml/forward_updater.py",
    "ml\\forward_updater.py",
    "ml.forward_updater",
    "ml/backfill.py",
    "ml\\backfill.py",
    "tools/recover_decisions_db.py",
    "tools\\recover_decisions_db.py",
)


def _log_path() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return LOG_DIR / f"ml_db_recovery_{stamp}.log"


def _write_log(path: Path, message: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
    print(line, flush=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _json_log(path: Path, label: str, payload: Any) -> None:
    _write_log(path, f"{label}: {json.dumps(payload, ensure_ascii=False, sort_keys=True)}")


def _scan_processes() -> list[dict[str, Any]]:
    command = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "process scan failed")
    raw = proc.stdout.strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _find_writer_processes() -> list[dict[str, Any]]:
    own_pid = os.getpid()
    conflicts: list[dict[str, Any]] = []
    for proc in _scan_processes():
        pid = int(proc.get("ProcessId") or 0)
        if pid == own_pid:
            continue
        command_line = str(proc.get("CommandLine") or "")
        normalized = command_line.lower()
        if "scheduled_ml_db_recovery.py" in normalized:
            continue
        if any(pattern.lower() in normalized for pattern in WRITER_PATTERNS):
            conflicts.append(
                {
                    "pid": pid,
                    "name": proc.get("Name"),
                    "command_line": command_line[:500],
                }
            )
    return conflicts


def _assert_db_write_window(path: Path) -> None:
    conn = sqlite3.connect(str(path), timeout=1)
    try:
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")
    finally:
        conn.close()


def _preflight(log_path: Path, *, strict_process_check: bool = False) -> None:
    if not BACKUP_PATH.exists():
        raise FileNotFoundError(f"backup DB missing: {BACKUP_PATH}")
    if not CURRENT_PATH.exists():
        raise FileNotFoundError(f"current DB missing: {CURRENT_PATH}")
    if strict_process_check:
        conflicts = _find_writer_processes()
        if conflicts:
            _json_log(log_path, "writer_process_conflicts", conflicts)
            raise RuntimeError("writer process is still running; recovery aborted")
    _assert_db_write_window(CURRENT_PATH)
    _write_log(log_path, "preflight_ok")


def _build_and_check_staging(log_path: Path) -> Path:
    from ml import db_health
    from tools import recover_decisions_db

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    staging_path = STAGING_DIR / f"recovered_decisions_{stamp}.db"
    report = recover_decisions_db.recover(
        backup_path=BACKUP_PATH,
        current_path=CURRENT_PATH,
        output_path=staging_path,
        apply=True,
        skip_forward_update=False,
    )
    _json_log(log_path, "staging_recover_report", report)
    health = db_health.check_db_health(staging_path, read_only=True)
    _json_log(log_path, "staging_health", health)
    if not health.get("ok"):
        raise RuntimeError("staging DB health failed; production replace aborted")
    return staging_path


def _replace_production(log_path: Path, *, strict_process_check: bool = False) -> None:
    from ml import db_health
    from tools import recover_decisions_db

    _preflight(log_path, strict_process_check=strict_process_check)
    report = recover_decisions_db.recover(
        backup_path=BACKUP_PATH,
        current_path=CURRENT_PATH,
        output_path=None,
        apply=True,
        skip_forward_update=False,
    )
    _json_log(log_path, "production_recover_report", report)
    health = db_health.check_db_health(CURRENT_PATH, read_only=True)
    _json_log(log_path, "production_health", health)
    if not health.get("ok"):
        raise RuntimeError("production DB health failed after replacement")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-time safe scheduled ML decisions DB recovery.")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--staging-only", action="store_true")
    parser.add_argument(
        "--strict-process-check",
        action="store_true",
        help="abort when trading/recovery writer processes are visible",
    )
    args = parser.parse_args(argv)

    log_path = _log_path()
    try:
        _write_log(log_path, f"start task={TASK_NAME} root={ROOT}")
        _preflight(log_path, strict_process_check=args.strict_process_check)
        if args.preflight_only:
            _write_log(log_path, "done preflight_only")
            return 0
        staging_path = _build_and_check_staging(log_path)
        _write_log(log_path, f"staging_ok path={staging_path}")
        if args.staging_only:
            _write_log(log_path, "done staging_only")
            return 0
        _replace_production(log_path, strict_process_check=args.strict_process_check)
        _write_log(log_path, "done production_replace_ok")
        return 0
    except Exception as exc:
        _write_log(log_path, f"failed: {exc}")
        _write_log(log_path, traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
