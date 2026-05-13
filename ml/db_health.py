from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from ml.db_writer import _resolve_db_path


FIXTURE_TICKER = "005930"
FIXTURE_FORWARD_1D = 1.8
FIXTURE_FORWARD_3D = 3.2
FIXTURE_FORWARD_5D = 5.1


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, timeout=10, factory=_ClosingConnection
        )
    else:
        conn = sqlite3.connect(str(path), timeout=10, factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _sequence_value(conn: sqlite3.Connection, table: str) -> int | None:
    if not _table_exists(conn, "sqlite_sequence"):
        return None
    row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
    return int(row["seq"]) if row else None


def _fixture_count(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM decisions
            WHERE ticker=?
              AND ABS(COALESCE(forward_1d, -999999) - ?) < 0.000001
              AND ABS(COALESCE(forward_3d, -999999) - ?) < 0.000001
              AND ABS(COALESCE(forward_5d, -999999) - ?) < 0.000001
            """,
            (FIXTURE_TICKER, FIXTURE_FORWARD_1D, FIXTURE_FORWARD_3D, FIXTURE_FORWARD_5D),
        ).fetchone()[0]
    )


def _expected_days(market: str, end_day: date, count: int) -> tuple[list[str], str]:
    start = pd.Timestamp(end_day - timedelta(days=max(10, count * 5)))
    end = pd.Timestamp(end_day)
    try:
        from phase1_trainer.price_collector import _expected_trading_days

        days, source = _expected_trading_days(market, start, end)
    except Exception:
        days = [pd.Timestamp(day).normalize() for day in pd.bdate_range(start, end)]
        source = "pd_bdate_range_fallback"
    return [pd.Timestamp(day).strftime("%Y-%m-%d") for day in days[-count:]], source


def _last_trading_days_live_rows(conn: sqlite3.Connection, latest_session_date: str | None, count: int) -> tuple[int, dict[str, Any]]:
    if not latest_session_date:
        return 0, {"calendar_source": "none", "days": {}}

    try:
        end_day = date.fromisoformat(latest_session_date)
    except ValueError:
        end_day = date.today()

    markets = [
        str(row["market"])
        for row in conn.execute("SELECT DISTINCT market FROM decisions WHERE market IS NOT NULL")
    ]
    total = 0
    detail: dict[str, Any] = {"days": {}}
    for market in markets or ["KR", "US"]:
        days, source = _expected_days(market, end_day, count)
        placeholders = ",".join("?" for _ in days)
        rows = 0
        if days:
            params = [market, *days]
            rows = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM decisions
                    WHERE market=?
                      AND COALESCE(data_source, 'live')='live'
                      AND session_date IN ({placeholders})
                    """,
                    params,
                ).fetchone()[0]
            )
        total += rows
        detail["days"][market] = {"calendar_source": source, "days": days, "live_rows": rows}
    return total, detail


def _known_unrecoverable_ranges(conn: sqlite3.Connection) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT COUNT(*)
        FROM decisions
        WHERE session_date BETWEEN '2026-04-04' AND '2026-05-11'
        """
    ).fetchone()[0]
    if int(rows) > 0:
        return []
    return [
        {
            "start": "2026-04-04",
            "end": "2026-05-11",
            "status": "unrecoverable_without_original_decision_rows",
        }
    ]


def check_db_health(
    db_path: str | Path | None = None,
    *,
    read_only: bool = True,
    recent_trading_days: int = 3,
) -> dict[str, Any]:
    path = Path(db_path).expanduser().resolve() if db_path else _resolve_db_path()
    result: dict[str, Any] = {
        "db_path": str(path),
        "exists": path.exists(),
        "read_only": read_only,
        "ok": False,
        "errors": [],
    }
    if not path.exists():
        result["errors"].append("db_missing")
        return result

    try:
        with _connect(path, read_only=read_only) as conn:
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            result["integrity_check"] = integrity
            if integrity.lower() != "ok":
                result["errors"].append("integrity_check_failed")

            if not _table_exists(conn, "decisions"):
                result["errors"].append("decisions_table_missing")
                return result

            total = int(conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0])
            live = int(
                conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE COALESCE(data_source, 'live')='live'"
                ).fetchone()[0]
            )
            simulated = int(
                conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE COALESCE(is_simulated, 0) != 0"
                ).fetchone()[0]
            )
            min_date, latest_date = conn.execute(
                "SELECT MIN(session_date), MAX(session_date) FROM decisions"
            ).fetchone()
            min_id, max_id = conn.execute("SELECT MIN(id), MAX(id) FROM decisions").fetchone()
            seq = _sequence_value(conn, "decisions")
            fixture_rows = _fixture_count(conn)
            recent_live, recent_detail = _last_trading_days_live_rows(
                conn, latest_date, recent_trading_days
            )
            single_session_only = bool(total > 100 and min_date == latest_date)
            high_watermark = max(int(seq or 0), int(max_id or 0))
            suspicious_sequence_gap = bool(total > 0 and high_watermark > total * 10 and total < 1000)

            result.update(
                {
                    "total_rows": total,
                    "live_rows": live,
                    "simulated_rows": simulated,
                    "min_session_date": min_date,
                    "latest_session_date": latest_date,
                    "min_id": min_id,
                    "max_id": max_id,
                    "sqlite_sequence": {"decisions": seq},
                    "last_3_trading_days_live_rows": recent_live,
                    "last_3_trading_days_detail": recent_detail,
                    "contamination": {"fixture_rows": fixture_rows},
                    "suspicious_sequence_gap": suspicious_sequence_gap,
                    "single_session_only": single_session_only,
                    "gaps": {"known_unrecoverable_ranges": _known_unrecoverable_ranges(conn)},
                }
            )

            if fixture_rows:
                result["errors"].append("fixture_contamination_found")
            if suspicious_sequence_gap:
                result["errors"].append("suspicious_sequence_gap")
            if single_session_only:
                result["errors"].append("single_session_only")
            if total <= 0:
                result["errors"].append("empty_decisions")

            result["ok"] = not result["errors"]
            return result
    except Exception as exc:
        result["errors"].append(str(exc))
        return result


def _print_human(result: dict[str, Any]) -> None:
    print(f"[ML DB] ok={result.get('ok')} path={result.get('db_path')}")
    if not result.get("exists"):
        print("  missing")
        return
    print(
        "  rows={total_rows} live={live_rows} simulated={simulated_rows} "
        "dates={min_session_date}..{latest_session_date}".format(**result)
    )
    print(
        "  fixture_rows={fixture_rows} sequence={seq} recent_live={recent}".format(
            fixture_rows=result.get("contamination", {}).get("fixture_rows"),
            seq=result.get("sqlite_sequence", {}).get("decisions"),
            recent=result.get("last_3_trading_days_live_rows"),
        )
    )
    if result.get("errors"):
        print("  errors=" + ", ".join(str(e) for e in result["errors"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check ML decisions.db health.")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--read-only", action="store_true", default=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = check_db_health(args.db_path, read_only=args.read_only)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
