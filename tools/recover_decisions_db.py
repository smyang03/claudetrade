from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_BACKUP_PATH = ROOT / "data" / "ml" / "decisions_before_backfill_refresh_20260403_221805.db"
DEFAULT_CURRENT_PATH = ROOT / "data" / "ml" / "decisions.db"

FIXTURE_TICKER = "005930"
FIXTURE_FORWARD_1D = 1.8
FIXTURE_FORWARD_3D = 3.2
FIXTURE_FORWARD_5D = 5.1


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


@contextmanager
def _temporary_ml_db_path(path: Path):
    old = os.environ.get("ML_DECISIONS_DB_PATH")
    os.environ["ML_DECISIONS_DB_PATH"] = str(path)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("ML_DECISIONS_DB_PATH", None)
        else:
            os.environ["ML_DECISIONS_DB_PATH"] = old


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, timeout=10, factory=_ClosingConnection
        )
    else:
        conn = sqlite3.connect(str(path), timeout=10, factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    return conn


def _init_empty_db(path: Path) -> None:
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    from ml import db_writer

    with _temporary_ml_db_path(path):
        db_writer.init_db()


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _max_session_date(conn: sqlite3.Connection) -> str | None:
    return conn.execute("SELECT MAX(session_date) FROM decisions").fetchone()[0]


def _db_diag(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    with _connect(path, read_only=True) as conn:
        if not _table_exists(conn, "decisions"):
            return {"path": str(path), "exists": True, "decisions_table": False}
        total = int(conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0])
        min_date, max_date = conn.execute(
            "SELECT MIN(session_date), MAX(session_date) FROM decisions"
        ).fetchone()
        live = int(
            conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE COALESCE(data_source, 'live')='live'"
            ).fetchone()[0]
        )
        fixture = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM decisions
                WHERE ticker=?
                  AND ABS(COALESCE(forward_1d, -999999) - ?) < 0.000001
                  AND ABS(COALESCE(forward_3d, -999999) - ?) < 0.000001
                  AND ABS(COALESCE(forward_5d, -999999) - ?) < 0.000001
                """,
                (FIXTURE_TICKER, FIXTURE_FORWARD_1D, FIXTURE_FORWARD_3D, FIXTURE_FORWARD_5D),
            ).fetchone()[0]
        )
        seq = None
        if _table_exists(conn, "sqlite_sequence"):
            row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='decisions'").fetchone()
            seq = int(row["seq"]) if row else None
        return {
            "path": str(path),
            "exists": True,
            "decisions_table": True,
            "total_rows": total,
            "min_session_date": min_date,
            "max_session_date": max_date,
            "live_rows": live,
            "fixture_rows": fixture,
            "sqlite_sequence_decisions": seq,
        }


def _is_fixture_row(row: sqlite3.Row | dict[str, Any]) -> bool:
    try:
        return (
            str(row["ticker"]) == FIXTURE_TICKER
            and abs(float(row["forward_1d"]) - FIXTURE_FORWARD_1D) < 0.000001
            and abs(float(row["forward_3d"]) - FIXTURE_FORWARD_3D) < 0.000001
            and abs(float(row["forward_5d"]) - FIXTURE_FORWARD_5D) < 0.000001
        )
    except Exception:
        return False


def _is_live_recoverable(row: sqlite3.Row, backup_max_session: str | None) -> bool:
    session_date = str(row["session_date"] or "")
    if backup_max_session and session_date <= backup_max_session:
        return False
    if _is_fixture_row(row):
        return False
    data_source = str(row["data_source"] or "live").lower()
    if data_source not in ("", "live"):
        return False
    try:
        if int(row["is_simulated"] or 0) != 0:
            return False
    except Exception:
        return False
    return True


def _insert_rows(
    dst: sqlite3.Connection,
    dst_cols: list[str],
    rows: Iterable[sqlite3.Row],
    src_cols: list[str],
) -> int:
    cols = [col for col in dst_cols if col in src_cols]
    if not cols:
        return 0
    quoted = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO decisions ({quoted}) VALUES ({placeholders})"
    count = 0
    for row in rows:
        dst.execute(sql, [row[col] for col in cols])
        count += 1
    return count


def _copy_backup_rows(backup: sqlite3.Connection, dst: sqlite3.Connection) -> int:
    src_cols = _columns(backup, "decisions")
    dst_cols = _columns(dst, "decisions")
    rows = backup.execute("SELECT * FROM decisions ORDER BY id").fetchall()
    return _insert_rows(dst, dst_cols, rows, src_cols)


def _merge_current_rows(
    current: sqlite3.Connection,
    dst: sqlite3.Connection,
    backup_max_session: str | None,
) -> tuple[int, int, int]:
    src_cols = _columns(current, "decisions")
    dst_cols = _columns(dst, "decisions")
    rows = current.execute("SELECT * FROM decisions ORDER BY id").fetchall()
    current_ids = {
        int(row["id"])
        for row in dst.execute("SELECT id FROM decisions").fetchall()
        if row["id"] is not None
    }
    merged = 0
    skipped_fixture = 0
    id_conflicts = 0
    cols = [col for col in dst_cols if col in src_cols]
    sql = f"INSERT INTO decisions ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"

    for row in rows:
        if _is_fixture_row(row):
            skipped_fixture += 1
            continue
        if not _is_live_recoverable(row, backup_max_session):
            continue
        row_id = int(row["id"])
        if row_id in current_ids:
            id_conflicts += 1
            continue
        dst.execute(sql, [row[col] for col in cols])
        current_ids.add(row_id)
        merged += 1
    return merged, skipped_fixture, id_conflicts


def _remove_fixture_rows(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        """
        DELETE FROM decisions
        WHERE ticker=?
          AND ABS(COALESCE(forward_1d, -999999) - ?) < 0.000001
          AND ABS(COALESCE(forward_3d, -999999) - ?) < 0.000001
          AND ABS(COALESCE(forward_5d, -999999) - ?) < 0.000001
        """,
        (FIXTURE_TICKER, FIXTURE_FORWARD_1D, FIXTURE_FORWARD_3D, FIXTURE_FORWARD_5D),
    )
    return int(cur.rowcount or 0)


def _update_matching_decision(
    conn: sqlite3.Connection,
    *,
    market: str,
    ticker: str,
    session_date: str,
    values: dict[str, Any],
) -> bool:
    rows = conn.execute(
        """
        SELECT id
        FROM decisions
        WHERE market=? AND ticker=? AND session_date=? AND decision='BUY_SIGNAL'
        """,
        (market, ticker, session_date),
    ).fetchall()
    if len(rows) != 1:
        return False
    allowed = {
        "entry_price",
        "exit_price",
        "exit_reason",
        "hold_days",
        "pnl_pct",
        "filled",
        "order_status",
    }
    update = {key: value for key, value in values.items() if key in allowed and value is not None}
    if not update:
        return False
    assignments = ", ".join(f"{key}=COALESCE({key}, ?)" for key in update)
    conn.execute(
        f"UPDATE decisions SET {assignments} WHERE id=?",
        [*update.values(), rows[0]["id"]],
    )
    return True


def _supplement_from_live_jsonl(conn: sqlite3.Connection, path: Path) -> dict[str, int]:
    summary = {"events_seen": 0, "rows_updated": 0}
    if not path.exists():
        return summary
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_name = str(
                item.get("event")
                or item.get("action")
                or item.get("type")
                or item.get("status")
                or ""
            ).lower()
            if "closed" not in event_name and item.get("pnl_pct") is None:
                continue
            market = str(item.get("market") or "").upper()
            ticker = str(item.get("ticker") or item.get("symbol") or "")
            session_date = str(item.get("session_date") or item.get("date") or "")[:10]
            if not (market and ticker and session_date):
                continue
            summary["events_seen"] += 1
            updated = _update_matching_decision(
                conn,
                market=market,
                ticker=ticker,
                session_date=session_date,
                values={
                    "entry_price": item.get("entry_price"),
                    "exit_price": item.get("exit_price"),
                    "exit_reason": item.get("exit_reason") or item.get("reason"),
                    "hold_days": item.get("hold_days"),
                    "pnl_pct": item.get("pnl_pct"),
                    "filled": 1 if item.get("filled") is True else None,
                    "order_status": item.get("order_status"),
                },
            )
            if updated:
                summary["rows_updated"] += 1
    return summary


def _table_columns_by_name(conn: sqlite3.Connection) -> dict[str, set[str]]:
    tables = [
        str(row["name"])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    ]
    return {table: set(_columns(conn, table)) for table in tables}


def _supplement_from_selection_db(conn: sqlite3.Connection, path: Path) -> dict[str, int]:
    summary = {"rows_seen": 0, "rows_updated": 0}
    if not path.exists():
        return summary
    try:
        with _connect(path, read_only=True) as src:
            candidates = []
            for table, cols in _table_columns_by_name(src).items():
                if {"ticker", "market"}.issubset(cols) and ("pnl_pct" in cols or "exit_reason" in cols):
                    date_col = "session_date" if "session_date" in cols else "date" if "date" in cols else None
                    if date_col:
                        candidates.append((table, date_col, cols))
            for table, date_col, cols in candidates:
                select_cols = ["market", "ticker", f"{date_col} AS session_date"]
                for col in ("entry_price", "exit_price", "exit_reason", "hold_days", "pnl_pct", "filled", "order_status"):
                    if col in cols:
                        select_cols.append(col)
                rows = src.execute(f"SELECT {', '.join(select_cols)} FROM {table}").fetchall()
                for row in rows:
                    summary["rows_seen"] += 1
                    if _update_matching_decision(
                        conn,
                        market=str(row["market"]).upper(),
                        ticker=str(row["ticker"]),
                        session_date=str(row["session_date"])[:10],
                        values=dict(row),
                    ):
                        summary["rows_updated"] += 1
    except Exception as exc:
        summary["error"] = 1
        summary["error_message"] = str(exc)[:300]
    return summary


def _supplement_outcomes(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "live_decisions_jsonl": _supplement_from_live_jsonl(conn, ROOT / "state" / "live_decisions.jsonl"),
        "ticker_selection_log_db": _supplement_from_selection_db(conn, ROOT / "data" / "ticker_selection_log.db"),
    }


def _run_forward_update(db_path: Path) -> dict[str, Any]:
    from ml import forward_updater

    forward_updater._price_cache.clear()
    with _temporary_ml_db_path(db_path):
        return forward_updater.run(dry_run=False)


def _checkpoint_db(path: Path) -> None:
    conn = sqlite3.connect(str(path), timeout=10)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _preserve_current_db(current_path: Path) -> list[str]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    preserved: list[str] = []
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(current_path) + suffix)
        if not src.exists():
            continue
        dst = current_path.with_name(f"decisions_contaminated_{timestamp}.db{suffix}")
        shutil.copy2(src, dst)
        preserved.append(str(dst))
    return preserved


def _build_recovered_db(
    *,
    backup_path: Path,
    current_path: Path,
    target_path: Path,
    skip_forward_update: bool,
) -> dict[str, Any]:
    _init_empty_db(target_path)
    report: dict[str, Any] = {
        "backup_path": str(backup_path),
        "current_path": str(current_path),
        "target_path": str(target_path),
        "backup_diag": _db_diag(backup_path),
        "current_diag": _db_diag(current_path),
        "backup_rows_copied": 0,
        "current_rows_merged": 0,
        "current_fixture_rows_skipped": 0,
        "id_conflicts": 0,
        "fixture_rows_removed": 0,
        "outcome_supplement": {},
        "forward_update": {},
        "final_diag": {},
    }

    with _connect(backup_path, read_only=True) as backup, _connect(current_path, read_only=True) as current, _connect(target_path) as dst:
        if not _table_exists(backup, "decisions"):
            raise RuntimeError(f"backup decisions table missing: {backup_path}")
        if not _table_exists(current, "decisions"):
            raise RuntimeError(f"current decisions table missing: {current_path}")

        backup_max_session = _max_session_date(backup)
        report["backup_rows_copied"] = _copy_backup_rows(backup, dst)
        merged, skipped_fixture, id_conflicts = _merge_current_rows(current, dst, backup_max_session)
        report["current_rows_merged"] = merged
        report["current_fixture_rows_skipped"] = skipped_fixture
        report["id_conflicts"] = id_conflicts
        report["fixture_rows_removed"] = _remove_fixture_rows(dst)
        report["outcome_supplement"] = _supplement_outcomes(dst)
        dst.commit()

    if not skip_forward_update:
        report["forward_update"] = _run_forward_update(target_path)
    report["final_diag"] = _db_diag(target_path)
    return report


def recover(
    *,
    backup_path: str | Path = DEFAULT_BACKUP_PATH,
    current_path: str | Path = DEFAULT_CURRENT_PATH,
    output_path: str | Path | None = None,
    apply: bool = False,
    skip_forward_update: bool = False,
) -> dict[str, Any]:
    backup = Path(backup_path).expanduser().resolve()
    current = Path(current_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve() if output_path else None

    if not backup.exists():
        raise FileNotFoundError(f"backup DB not found: {backup}")
    if not current.exists():
        raise FileNotFoundError(f"current DB not found: {current}")

    temp_parent = current.parent if apply and output is None else None
    with tempfile.TemporaryDirectory(dir=str(temp_parent) if temp_parent else None) as tmp:
        work_db = Path(tmp) / "recovered_decisions.db"
        report = _build_recovered_db(
            backup_path=backup,
            current_path=current,
            target_path=work_db,
            skip_forward_update=skip_forward_update,
        )
        _checkpoint_db(work_db)

        if not apply:
            report["apply_mode"] = "dry_run"
            report["output_path"] = str(output) if output else None
            return report

        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists():
                output.unlink()
            shutil.copy2(work_db, output)
            report["apply_mode"] = "output_path"
            report["output_path"] = str(output)
            report["final_diag"] = _db_diag(output)
            return report

        preserved = _preserve_current_db(current)
        os.replace(work_db, current)
        report["apply_mode"] = "production_replace"
        report["output_path"] = str(current)
        report["preserved_current_files"] = preserved
        report["final_diag"] = _db_diag(current)
        return report


def _print_human(report: dict[str, Any]) -> None:
    print(f"apply_mode={report.get('apply_mode')}")
    print(f"backup_rows_copied={report.get('backup_rows_copied')}")
    print(f"current_rows_merged={report.get('current_rows_merged')}")
    print(f"current_fixture_rows_skipped={report.get('current_fixture_rows_skipped')}")
    print(f"fixture_rows_removed={report.get('fixture_rows_removed')}")
    final = report.get("final_diag", {})
    if final:
        print(
            "final_rows={total_rows} dates={min_session_date}..{max_session_date} "
            "fixture_rows={fixture_rows}".format(**final)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover ML decisions.db from verified sources.")
    parser.add_argument("--backup-path", type=Path, default=DEFAULT_BACKUP_PATH)
    parser.add_argument("--current-path", type=Path, default=DEFAULT_CURRENT_PATH)
    parser.add_argument("--output-path", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="default; do not write output or replace production")
    parser.add_argument("--apply", action="store_true", help="write --output-path or replace production DB")
    parser.add_argument("--skip-forward-update", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = recover(
        backup_path=args.backup_path,
        current_path=args.current_path,
        output_path=args.output_path,
        apply=args.apply,
        skip_forward_update=args.skip_forward_update,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
