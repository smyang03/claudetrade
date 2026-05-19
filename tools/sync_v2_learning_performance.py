from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lifecycle.quality import evaluate_decision_quality, forward_measurement_complete


DEFAULT_EVENT_DB = ROOT / "data" / "v2_event_store.db"
DEFAULT_ML_DB = ROOT / "data" / "ml" / "decisions.db"


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


V2_LEARNING_SCHEMA = """
CREATE TABLE IF NOT EXISTS v2_learning_performance (
    v2_decision_id       TEXT PRIMARY KEY,
    market               TEXT NOT NULL,
    runtime_mode         TEXT NOT NULL,
    session_date         TEXT NOT NULL,
    ticker               TEXT NOT NULL,
    status               TEXT NOT NULL,
    route                TEXT,
    path_type            TEXT,
    path_run_id          TEXT,
    strategy             TEXT,
    origin_action        TEXT,
    timing_style         TEXT,
    filled               INTEGER NOT NULL DEFAULT 0,
    closed               INTEGER NOT NULL DEFAULT 0,
    fill_event_id        INTEGER,
    close_event_id       INTEGER,
    filled_at            TEXT,
    closed_at            TEXT,
    entry_price          REAL,
    exit_price           REAL,
    qty                  REAL,
    pnl_krw              REAL,
    pnl_pct              REAL,
    mfe_pct              REAL,
    mae_pct              REAL,
    close_reason         TEXT,
    forward_complete     INTEGER NOT NULL DEFAULT 0,
    quality_grade        TEXT NOT NULL DEFAULT 'LEGACY_UNKNOWN',
    quality_reasons_json TEXT NOT NULL DEFAULT '[]',
    learning_allowed     INTEGER NOT NULL DEFAULT 0,
    source_event_count   INTEGER NOT NULL DEFAULT 0,
    synced_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_v2_learning_perf_market_session
    ON v2_learning_performance(market, runtime_mode, session_date);
CREATE INDEX IF NOT EXISTS idx_v2_learning_perf_ticker
    ON v2_learning_performance(market, ticker, session_date);
CREATE INDEX IF NOT EXISTS idx_v2_learning_perf_learning
    ON v2_learning_performance(learning_allowed, quality_grade, session_date);
"""


UPSERT_SQL = """
INSERT INTO v2_learning_performance (
    v2_decision_id, market, runtime_mode, session_date, ticker, status,
    route, path_type, path_run_id, strategy, origin_action, timing_style,
    filled, closed, fill_event_id, close_event_id, filled_at, closed_at,
    entry_price, exit_price, qty, pnl_krw, pnl_pct, mfe_pct, mae_pct,
    close_reason, forward_complete, quality_grade, quality_reasons_json,
    learning_allowed, source_event_count, synced_at
)
VALUES (
    :v2_decision_id, :market, :runtime_mode, :session_date, :ticker, :status,
    :route, :path_type, :path_run_id, :strategy, :origin_action, :timing_style,
    :filled, :closed, :fill_event_id, :close_event_id, :filled_at, :closed_at,
    :entry_price, :exit_price, :qty, :pnl_krw, :pnl_pct, :mfe_pct, :mae_pct,
    :close_reason, :forward_complete, :quality_grade, :quality_reasons_json,
    :learning_allowed, :source_event_count, :synced_at
)
ON CONFLICT(v2_decision_id) DO UPDATE SET
    market=excluded.market,
    runtime_mode=excluded.runtime_mode,
    session_date=excluded.session_date,
    ticker=excluded.ticker,
    status=excluded.status,
    route=excluded.route,
    path_type=excluded.path_type,
    path_run_id=excluded.path_run_id,
    strategy=excluded.strategy,
    origin_action=excluded.origin_action,
    timing_style=excluded.timing_style,
    filled=excluded.filled,
    closed=excluded.closed,
    fill_event_id=excluded.fill_event_id,
    close_event_id=excluded.close_event_id,
    filled_at=excluded.filled_at,
    closed_at=excluded.closed_at,
    entry_price=excluded.entry_price,
    exit_price=excluded.exit_price,
    qty=excluded.qty,
    pnl_krw=excluded.pnl_krw,
    pnl_pct=excluded.pnl_pct,
    mfe_pct=excluded.mfe_pct,
    mae_pct=excluded.mae_pct,
    close_reason=excluded.close_reason,
    forward_complete=excluded.forward_complete,
    quality_grade=excluded.quality_grade,
    quality_reasons_json=excluded.quality_reasons_json,
    learning_allowed=excluded.learning_allowed,
    source_event_count=excluded.source_event_count,
    synced_at=excluded.synced_at
"""


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(V2_LEARNING_SCHEMA)


def _json_loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
        return dict(parsed) if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _row_to_dict(row: sqlite3.Row | None, payload_key: str = "payload_json") -> dict[str, Any]:
    if row is None:
        return {}
    data = dict(row)
    if payload_key in data:
        data["payload"] = _json_loads(data.pop(payload_key))
    if "plan_json" in data:
        data["plan"] = _json_loads(data.pop("plan_json"))
    return data


def _num(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except Exception:
            continue
    return None


def _text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _first_event(events: list[dict[str, Any]], *event_types: str) -> dict[str, Any]:
    allowed = set(event_types)
    for event in events:
        if str(event.get("event_type") or "") in allowed:
            return event
    return {}


def _last_event(events: list[dict[str, Any]], *event_types: str) -> dict[str, Any]:
    allowed = set(event_types)
    for event in reversed(events):
        if str(event.get("event_type") or "") in allowed:
            return event
    return {}


def _load_decisions(
    conn: sqlite3.Connection,
    *,
    market: str | None = None,
    runtime_mode: str | None = "live",
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if market and market.upper() != "ALL":
        where.append("market=?")
        params.append(market.upper())
    if runtime_mode:
        where.append("runtime_mode=?")
        params.append(runtime_mode.lower())
    if start_date:
        where.append("session_date>=?")
        params.append(start_date)
    if end_date:
        where.append("session_date<=?")
        params.append(end_date)
    sql = "SELECT * FROM v2_decisions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY session_date, market, ticker, decision_id"
    rows = conn.execute(sql, params).fetchall()
    by_decision = {str(row["decision_id"]): _row_to_dict(row) for row in rows}

    event_where: list[str] = []
    event_params: list[Any] = []
    if market and market.upper() != "ALL":
        event_where.append("market=?")
        event_params.append(market.upper())
    if runtime_mode:
        event_where.append("runtime_mode=?")
        event_params.append(runtime_mode.lower())
    if start_date:
        event_where.append("session_date>=?")
        event_params.append(start_date)
    if end_date:
        event_where.append("session_date<=?")
        event_params.append(end_date)
    event_sql = """
        SELECT
            decision_id,
            MAX(market) AS market,
            MAX(runtime_mode) AS runtime_mode,
            MIN(session_date) AS session_date,
            MAX(ticker) AS ticker,
            MAX(prompt_version) AS prompt_version,
            MAX(brain_snapshot_id) AS brain_snapshot_id,
            MAX(event_type) AS status
        FROM lifecycle_events
    """
    if event_where:
        event_sql += " WHERE " + " AND ".join(event_where)
    event_sql += " GROUP BY decision_id ORDER BY session_date, market, ticker, decision_id"
    for event_row in conn.execute(event_sql, event_params).fetchall():
        decision_id = str(event_row["decision_id"] or "")
        if decision_id and decision_id not in by_decision:
            by_decision[decision_id] = {
                "decision_id": decision_id,
                "market": str(event_row["market"] or ""),
                "runtime_mode": str(event_row["runtime_mode"] or ""),
                "session_date": str(event_row["session_date"] or ""),
                "ticker": str(event_row["ticker"] or ""),
                "prompt_version": str(event_row["prompt_version"] or ""),
                "brain_snapshot_id": str(event_row["brain_snapshot_id"] or ""),
                "strategy_hint": "",
                "timing_style": "",
                "status": str(event_row["status"] or ""),
                "payload": {},
            }
    return sorted(
        by_decision.values(),
        key=lambda row: (
            str(row.get("session_date") or ""),
            str(row.get("market") or ""),
            str(row.get("ticker") or ""),
            str(row.get("decision_id") or ""),
        ),
    )


def _load_events(conn: sqlite3.Connection, decision_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM lifecycle_events WHERE decision_id=? ORDER BY event_id",
        (decision_id,),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _entry_path_run_id_from_events(events: list[dict[str, Any]]) -> str:
    for event in events:
        event_type = str(event.get("event_type") or "")
        if event_type not in {"FILLED", "PARTIAL_FILLED"}:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if str(payload.get("side") or "").strip().lower() == "sell":
            continue
        path_run_id = str(payload.get("path_run_id") or payload.get("pathb_path_run_id") or "").strip()
        if path_run_id:
            return path_run_id
    for event in reversed(events):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        path_run_id = str(payload.get("path_run_id") or payload.get("pathb_path_run_id") or "").strip()
        if path_run_id:
            return path_run_id
    return ""


def _load_path_run(conn: sqlite3.Connection, decision_id: str, path_run_id: str = "") -> dict[str, Any]:
    path_key = str(path_run_id or "").strip()
    if path_key:
        row = conn.execute(
            "SELECT * FROM v2_path_runs WHERE path_run_id=? LIMIT 1",
            (path_key,),
        ).fetchone()
        if row is not None:
            return _row_to_dict(row)
    row = conn.execute(
        """
        SELECT *
        FROM v2_path_runs
        WHERE decision_id=?
        ORDER BY created_at, path_run_id
        LIMIT 1
        """,
        (decision_id,),
    ).fetchone()
    return _row_to_dict(row)


def build_learning_row(decision: dict[str, Any], events: list[dict[str, Any]], path_run: dict[str, Any]) -> dict[str, Any]:
    decision_payload = dict(decision.get("payload") or {})
    path_plan = dict(path_run.get("plan") or {})
    fill = _first_event(events, "FILLED", "PARTIAL_FILLED")
    close = _last_event(events, "CLOSED")
    forward_complete = forward_measurement_complete(events)
    fill_payload = dict(fill.get("payload") or {})
    close_payload = dict(close.get("payload") or {})
    event_path_run_id = _entry_path_run_id_from_events(events)
    # QUALITY_MARKED can be provisional when written at close time. The learning
    # table always recalculates quality from the full current event set.
    quality = evaluate_decision_quality(events)
    latest_event = events[-1] if events else {}

    path_type = str(path_run.get("path_type") or close_payload.get("path_type") or fill_payload.get("path_type") or "")
    route = _text(close_payload, "entry_route", "route") or _text(fill_payload, "entry_route", "route")
    if not route and (path_type == "claude_price" or str(path_run.get("path_run_id") or "")):
        route = "path_b"
    strategy = (
        _text(close_payload, "strategy", "source_strategy", "strategy_used")
        or _text(fill_payload, "strategy", "source_strategy", "strategy_used")
        or str(decision.get("strategy_hint") or "")
        or ("claude_price" if path_type == "claude_price" else "")
    )
    close_reason = _text(close_payload, "close_reason", "exit_reason") or str(close.get("reason_code") or "")
    status = "CLOSED" if close else ("FILLED" if fill else str(decision.get("status") or latest_event.get("event_type") or ""))
    return {
        "v2_decision_id": str(decision.get("decision_id") or ""),
        "market": str(decision.get("market") or ""),
        "runtime_mode": str(decision.get("runtime_mode") or ""),
        "session_date": str(decision.get("session_date") or ""),
        "ticker": str(decision.get("ticker") or ""),
        "status": status,
        "route": route,
        "path_type": path_type,
        "path_run_id": str(path_run.get("path_run_id") or event_path_run_id or close_payload.get("path_run_id") or fill_payload.get("path_run_id") or ""),
        "strategy": strategy,
        "origin_action": str(path_plan.get("origin_action") or decision_payload.get("origin_action") or ""),
        "timing_style": str(decision.get("timing_style") or decision_payload.get("timing_style") or ""),
        "filled": 1 if fill else 0,
        "closed": 1 if close else 0,
        "fill_event_id": fill.get("event_id") if fill else None,
        "close_event_id": close.get("event_id") if close else None,
        "filled_at": fill.get("occurred_at") if fill else None,
        "closed_at": close.get("occurred_at") if close else None,
        "entry_price": _num(fill_payload, "entry_price", "actual_fill_price", "fill_price", "price", "avg_price"),
        "exit_price": _num(close_payload, "exit_price", "actual_fill_price", "fill_price", "price", "close_price"),
        "qty": _num(close_payload, "qty", "filled_qty") or _num(fill_payload, "qty", "filled_qty"),
        "pnl_krw": _num(close_payload, "pnl_krw", "pnl"),
        "pnl_pct": _num(close_payload, "pnl_pct", "position_pnl_pct"),
        "mfe_pct": _num(close_payload, "mfe_pct", "position_mfe_pct"),
        "mae_pct": _num(close_payload, "mae_pct", "position_mae_pct"),
        "close_reason": close_reason,
        "forward_complete": 1 if forward_complete else 0,
        "quality_grade": quality.grade.value,
        "quality_reasons_json": json.dumps(list(quality.reasons), ensure_ascii=False),
        "learning_allowed": 1 if quality.learning_allowed else 0,
        "source_event_count": len(events),
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def sync_v2_learning_performance(
    *,
    event_db: str | Path = DEFAULT_EVENT_DB,
    ml_db: str | Path = DEFAULT_ML_DB,
    market: str | None = None,
    runtime_mode: str | None = "live",
    start_date: str | None = None,
    end_date: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    event_path = Path(event_db)
    ml_path = Path(ml_db)
    with _connect(event_path) as event_conn:
        decisions = _load_decisions(
            event_conn,
            market=market,
            runtime_mode=runtime_mode,
            start_date=start_date,
            end_date=end_date,
        )
        rows = []
        for decision in decisions:
            decision_id = str(decision.get("decision_id") or "")
            events = _load_events(event_conn, decision_id)
            path_run = _load_path_run(event_conn, decision_id, _entry_path_run_id_from_events(events))
            rows.append(build_learning_row(decision, events, path_run))
    written = 0
    if not dry_run:
        ml_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(ml_path) as ml_conn:
            ensure_schema(ml_conn)
            ml_conn.executemany(UPSERT_SQL, rows)
            ml_conn.commit()
            written = len(rows)
    filled = sum(1 for row in rows if row["filled"])
    closed = sum(1 for row in rows if row["closed"])
    learning_allowed = sum(1 for row in rows if row["learning_allowed"])
    return {
        "event_db": str(event_path),
        "ml_db": str(ml_path),
        "dry_run": bool(dry_run),
        "selected": len(rows),
        "written": written,
        "filled": filled,
        "closed": closed,
        "forward_complete": sum(1 for row in rows if row["forward_complete"]),
        "learning_allowed": learning_allowed,
        "quality_grade_counts": {
            grade: sum(1 for row in rows if row["quality_grade"] == grade)
            for grade in sorted({row["quality_grade"] for row in rows})
        },
        "sample": rows[:5],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync V2 lifecycle truth into ML v2_learning_performance.")
    parser.add_argument("--event-db", default=str(DEFAULT_EVENT_DB))
    parser.add_argument("--ml-db", default=str(DEFAULT_ML_DB))
    parser.add_argument("--market", default="ALL", choices=["ALL", "KR", "US"])
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    summary = sync_v2_learning_performance(
        event_db=args.event_db,
        ml_db=args.ml_db,
        market=args.market,
        runtime_mode=args.runtime_mode or None,
        start_date=args.start_date or None,
        end_date=args.end_date or None,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
