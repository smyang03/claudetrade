from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_FACT_DB = ROOT / "data" / "ml" / "claude_decision_facts.db"
DEFAULT_LOGS_DIR = ROOT / "logs" / "daily_judgment"

FACT_MARKET_JUDGMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_market_judgment (
    market_key TEXT PRIMARY KEY,
    runtime_mode TEXT NOT NULL DEFAULT 'live',
    session_date TEXT NOT NULL,
    market TEXT NOT NULL,
    consensus_mode TEXT,
    consensus_dir TEXT,
    actual_dir TEXT,
    market_change_pct REAL,
    hit INTEGER,
    parse_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_quality TEXT NOT NULL DEFAULT 'unknown',
    source_refs_json TEXT NOT NULL DEFAULT '{}'
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(data: Any) -> str:
    return json.dumps(data if data is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(db_path: str | Path = DEFAULT_FACT_DB) -> None:
    with closing(_connect(Path(db_path))) as conn:
        conn.executescript(FACT_MARKET_JUDGMENT_SCHEMA)
        _ensure_market_judgment_columns(conn)
        conn.execute("DROP INDEX IF EXISTS idx_fact_market_judgment_session")
        conn.execute(
            """
            CREATE INDEX idx_fact_market_judgment_session
                ON fact_market_judgment(runtime_mode, market, session_date, parse_status)
            """
        )
        conn.commit()


def _ensure_market_judgment_columns(conn: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(fact_market_judgment)").fetchall()
    }
    required_columns = {
        "runtime_mode": "TEXT NOT NULL DEFAULT 'live'",
        "consensus_mode": "TEXT",
        "consensus_dir": "TEXT",
        "actual_dir": "TEXT",
        "market_change_pct": "REAL",
        "hit": "INTEGER",
        "parse_status": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "created_at": "TEXT NOT NULL DEFAULT ''",
        "updated_at": "TEXT NOT NULL DEFAULT ''",
        "source_quality": "TEXT NOT NULL DEFAULT 'unknown'",
        "source_refs_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    for column, ddl in required_columns.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE fact_market_judgment ADD COLUMN {column} {ddl}")
    _migrate_legacy_market_judgment_keys(conn)


def _migrate_legacy_market_judgment_keys(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT market_key, runtime_mode, session_date, market
        FROM fact_market_judgment
        WHERE market_key NOT LIKE 'live:%'
          AND market_key NOT LIKE 'paper:%'
        """
    ).fetchall()
    for row in rows:
        old_key = str(row["market_key"] or "")
        runtime_mode = _runtime_mode(str(row["runtime_mode"] or "live"))
        session_date = str(row["session_date"] or "")[:10]
        market = str(row["market"] or "").upper()
        if not old_key or not session_date or not market:
            continue
        new_key = _market_key(runtime_mode, session_date, market)
        if new_key == old_key:
            continue
        exists = conn.execute(
            "SELECT 1 FROM fact_market_judgment WHERE market_key=?",
            (new_key,),
        ).fetchone()
        if exists:
            conn.execute("DELETE FROM fact_market_judgment WHERE market_key=?", (old_key,))
        else:
            conn.execute(
                "UPDATE fact_market_judgment SET market_key=? WHERE market_key=?",
                (new_key, old_key),
            )


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _market(value: str) -> str:
    key = str(value or "").strip().upper()
    return key if key in {"KR", "US", "ALL"} else key


def _runtime_mode(value: str = "live") -> str:
    key = str(value or "live").strip().lower()
    return key or "live"


def _runtime_mode_for_file(parsed_name: tuple[str, str, str] | None, runtime_mode: str) -> str:
    file_mode = str(parsed_name[2] or "").lower() if parsed_name else ""
    return _runtime_mode(file_mode or runtime_mode)


def _market_key(runtime_mode: str, session_date: str, market: str) -> str:
    return f"{_runtime_mode(runtime_mode)}:{session_date}:{market}"


def _resolve_dates(date: str = "", start_date: str = "", end_date: str = "") -> tuple[str, str]:
    if date:
        return date, date
    return start_date, end_date


def _date_from_filename(path: Path) -> tuple[str, str, str] | None:
    match = re.match(r"(?:(live|paper)_)?(\d{8})_(KR|US)\.json$", path.name, re.I)
    if not match:
        return None
    mode = (match.group(1) or "").lower()
    raw_date = match.group(2)
    market = match.group(3).upper()
    return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}", market, mode


def _iter_log_files(logs_dir: Path, *, start_date: str, end_date: str, market: str, runtime_mode: str) -> list[Path]:
    if not logs_dir.exists():
        return []
    grouped: dict[tuple[str, str], list[tuple[int, float, Path]]] = defaultdict(list)
    runtime_key = _runtime_mode(runtime_mode)
    for path in logs_dir.glob("*.json"):
        parsed = _date_from_filename(path)
        if not parsed:
            continue
        session_date, file_market, file_mode = parsed
        if start_date and session_date < start_date:
            continue
        if end_date and session_date > end_date:
            continue
        if market != "ALL" and file_market != market:
            continue
        if file_mode and file_mode != runtime_key:
            continue
        if file_mode == runtime_key:
            priority = 0
        else:
            priority = 1
        grouped[(session_date, file_market)].append((priority, -path.stat().st_mtime, path))
    return [sorted(items)[0][2] for items in grouped.values()]


def _direction_from_mode(mode: str, fallback: str = "") -> str:
    fallback_key = str(fallback or "").strip().lower()
    if fallback_key in {"bull", "bear", "flat"}:
        return fallback_key
    key = str(mode or "").strip().upper()
    if "BULL" in key:
        return "bull"
    if "BEAR" in key or "DEFENSIVE" in key:
        return "bear"
    if key in {"NEUTRAL", "FLAT", "CAUTIOUS"}:
        return "flat"
    return ""


def _direction_from_change(change: float | None) -> str:
    if change is None:
        return ""
    if change > 0:
        return "bull"
    if change < 0:
        return "bear"
    return "flat"


def _infer_market_change_from_events(payload: dict[str, Any]) -> float | None:
    patterns = (
        r"(?:S&P500|SP500|KOSPI|KOSDAQ|kospi|kosdaq|코스피|코스닥)\s*([+-]\d+(?:\.\d+)?)%",
        r"(?:market_change|market change|지수)\D*([+-]\d+(?:\.\d+)?)%",
    )
    for event in reversed(payload.get("session_events") or []):
        if not isinstance(event, dict):
            continue
        text = " ".join(str(event.get(key) or "") for key in ("reason", "trigger", "warning", "message"))
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return _as_float(match.group(1))
    return None


def parse_market_judgment_file(
    path: str | Path,
    *,
    runtime_mode: str = "live",
    now: str | None = None,
) -> dict[str, Any]:
    file_path = Path(path)
    stamp = now or _utc_now()
    parsed_name = _date_from_filename(file_path)
    runtime_key = _runtime_mode_for_file(parsed_name, runtime_mode)
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as exc:
        session_date, market, _ = parsed_name or ("", "", "")
        return {
            "market_key": _market_key(runtime_key, session_date, market),
            "runtime_mode": runtime_key,
            "session_date": session_date,
            "market": market,
            "consensus_mode": "",
            "consensus_dir": "",
            "actual_dir": "",
            "market_change_pct": None,
            "hit": None,
            "parse_status": "READ_ERROR",
            "created_at": stamp,
            "updated_at": stamp,
            "source_quality": "error",
            "source_refs_json": _json({"path": str(file_path), "error": str(exc)}),
        }

    session_date = str(payload.get("date") or (parsed_name[0] if parsed_name else ""))[:10]
    market = str(payload.get("market") or (parsed_name[1] if parsed_name else "")).upper()
    consensus = payload.get("consensus") if isinstance(payload.get("consensus"), dict) else {}
    consensus_mode = str(consensus.get("mode") or "")
    consensus_dir = _direction_from_mode(consensus_mode, str(consensus.get("unanimous_direction") or ""))

    actual = payload.get("actual_result") if isinstance(payload.get("actual_result"), dict) else {}
    market_change = _as_float(actual.get("market_change"))
    if market_change is None:
        market_change = _infer_market_change_from_events(payload)
    actual_dir = _direction_from_change(market_change)

    hit = None
    if consensus_dir and actual_dir:
        hit = 1 if consensus_dir == actual_dir else 0

    if not session_date or not market or not consensus_mode:
        parse_status = "UNSUPPORTED_LOG_FORMAT"
        source_quality = "partial"
    elif market_change is None:
        parse_status = "PARTIAL_ACTUAL_MISSING"
        source_quality = "partial"
    else:
        parse_status = "OK"
        source_quality = "complete"

    return {
        "market_key": _market_key(runtime_key, session_date, market),
        "runtime_mode": runtime_key,
        "session_date": session_date,
        "market": market,
        "consensus_mode": consensus_mode,
        "consensus_dir": consensus_dir,
        "actual_dir": actual_dir,
        "market_change_pct": market_change,
        "hit": hit,
        "parse_status": parse_status,
        "created_at": stamp,
        "updated_at": stamp,
        "source_quality": source_quality,
        "source_refs_json": _json({"path": str(file_path), "file_name": file_path.name, "runtime_mode": runtime_key}),
    }


def _upsert(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    columns = list(row.keys())
    placeholders = ", ".join(f":{column}" for column in columns)
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns if column != "market_key")
    conn.execute(
        f"""
        INSERT INTO fact_market_judgment ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(market_key) DO UPDATE SET {updates}
        """,
        row,
    )


def build_market_judgment_facts(
    *,
    db_path: str | Path = DEFAULT_FACT_DB,
    logs_dir: str | Path = DEFAULT_LOGS_DIR,
    date: str = "",
    start_date: str = "",
    end_date: str = "",
    market: str = "ALL",
    runtime_mode: str = "live",
    dry_run: bool = False,
) -> dict[str, Any]:
    start_date, end_date = _resolve_dates(date=date, start_date=start_date, end_date=end_date)
    market_key = _market(market or "ALL")
    runtime_key = _runtime_mode(runtime_mode)
    started = _utc_now()
    summary: dict[str, Any] = {
        "started_at": started,
        "finished_at": "",
        "db_path": str(db_path),
        "logs_dir": str(logs_dir),
        "start_date": start_date,
        "end_date": end_date,
        "market": market_key,
        "runtime_mode": runtime_key,
        "dry_run": bool(dry_run),
        "files_seen": 0,
        "facts_generated": 0,
        "facts_written": 0,
        "by_parse_status": {},
        "status": "OK",
    }
    files = _iter_log_files(Path(logs_dir), start_date=start_date, end_date=end_date, market=market_key, runtime_mode=runtime_key)
    summary["files_seen"] = len(files)
    rows = [parse_market_judgment_file(path, runtime_mode=runtime_key, now=started) for path in files]
    summary["facts_generated"] = len(rows)
    counts: Counter[str] = Counter()
    for row in rows:
        counts[str(row.get("parse_status") or "UNKNOWN")] += 1
    summary["by_parse_status"] = dict(counts)

    if not dry_run:
        init_schema(db_path)
        with closing(_connect(Path(db_path))) as conn:
            with conn:
                for row in rows:
                    _upsert(conn, row)
        summary["facts_written"] = len(rows)
    summary["finished_at"] = _utc_now()
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build best-effort market judgment facts from daily judgment logs.")
    parser.add_argument("--db", default=str(DEFAULT_FACT_DB))
    parser.add_argument("--logs-dir", default=str(DEFAULT_LOGS_DIR))
    parser.add_argument("--date", default="")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--market", default="ALL")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    summary = build_market_judgment_facts(
        db_path=args.db,
        logs_dir=args.logs_dir,
        date=args.date,
        start_date=args.start_date,
        end_date=args.end_date,
        market=args.market,
        runtime_mode=args.runtime_mode,
        dry_run=bool(args.dry_run),
    )
    if args.json:
        print(_json(summary))
    else:
        print(
            "market judgment facts: "
            f"files={summary['files_seen']} generated={summary['facts_generated']} "
            f"written={summary['facts_written']} status={summary['by_parse_status']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
