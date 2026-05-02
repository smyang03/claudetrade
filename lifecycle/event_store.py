from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import json
import sqlite3

from runtime_paths import get_runtime_path
from lifecycle.models import LifecycleEvent, normalize_event_type, utc_now_iso


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


class EventStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else get_runtime_path("data", "v2_event_store.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS lifecycle_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_uuid TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    market TEXT NOT NULL,
                    runtime_mode TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    execution_id TEXT,
                    position_id TEXT,
                    prompt_version TEXT NOT NULL,
                    brain_snapshot_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    reason_code TEXT,
                    data_quality TEXT NOT NULL DEFAULT 'LEGACY_UNKNOWN',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_lifecycle_decision
                    ON lifecycle_events(decision_id, event_id);
                CREATE INDEX IF NOT EXISTS idx_lifecycle_market_session
                    ON lifecycle_events(market, runtime_mode, session_date, event_id);
                CREATE INDEX IF NOT EXISTS idx_lifecycle_ticker
                    ON lifecycle_events(market, ticker, session_date, event_id);
                CREATE INDEX IF NOT EXISTS idx_lifecycle_event_type
                    ON lifecycle_events(event_type, session_date);
                CREATE INDEX IF NOT EXISTS idx_lifecycle_execution
                    ON lifecycle_events(execution_id);
                CREATE INDEX IF NOT EXISTS idx_lifecycle_position
                    ON lifecycle_events(position_id);

                CREATE TABLE IF NOT EXISTS v2_decisions (
                    decision_id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    runtime_mode TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    brain_snapshot_id TEXT NOT NULL,
                    strategy_hint TEXT,
                    timing_style TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_v2_decisions_market_session
                    ON v2_decisions(market, runtime_mode, session_date, ticker);

                CREATE TABLE IF NOT EXISTS phase_validation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phase INTEGER NOT NULL,
                    ok INTEGER NOT NULL,
                    qa INTEGER NOT NULL DEFAULT 0,
                    simulation_report INTEGER NOT NULL DEFAULT 0,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS v2_path_runs (
                    path_run_id TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL,
                    path_type TEXT NOT NULL,
                    market TEXT NOT NULL,
                    runtime_mode TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_v2_path_runs_decision
                    ON v2_path_runs(decision_id);
                CREATE INDEX IF NOT EXISTS idx_v2_path_runs_market_session_status
                    ON v2_path_runs(market, runtime_mode, session_date, status);
                CREATE INDEX IF NOT EXISTS idx_v2_path_runs_ticker_session
                    ON v2_path_runs(market, ticker, session_date);
                """
            )

    def append(self, event: LifecycleEvent) -> int:
        evt = event.normalized()
        if not evt.decision_id:
            raise ValueError("decision_id is required for lifecycle events")
        if not evt.prompt_version:
            raise ValueError("prompt_version is required for lifecycle events")
        if not evt.brain_snapshot_id:
            raise ValueError("brain_snapshot_id is required for lifecycle events")
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO lifecycle_events (
                    event_uuid, event_type, market, runtime_mode, session_date,
                    ticker, decision_id, execution_id, position_id,
                    prompt_version, brain_snapshot_id, occurred_at, reason_code,
                    data_quality, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evt.event_uuid,
                    evt.event_type,
                    evt.market,
                    evt.runtime_mode,
                    evt.session_date,
                    evt.ticker,
                    evt.decision_id,
                    evt.execution_id,
                    evt.position_id,
                    evt.prompt_version,
                    evt.brain_snapshot_id,
                    evt.occurred_at,
                    evt.reason_code,
                    evt.data_quality,
                    json.dumps(evt.payload, ensure_ascii=False, sort_keys=True),
                    utc_now_iso(),
                ),
            )
            if evt.event_type != "QUALITY_MARKED":
                conn.execute(
                    """
                    UPDATE v2_decisions
                    SET status=?, updated_at=?
                    WHERE decision_id=?
                    """,
                    (evt.event_type, utc_now_iso(), evt.decision_id),
                )
            return int(cur.lastrowid)

    def append_many(self, events: Iterable[LifecycleEvent]) -> list[int]:
        ids: list[int] = []
        with self.connect() as conn:
            for event in events:
                evt = event.normalized()
                cur = conn.execute(
                    """
                    INSERT INTO lifecycle_events (
                        event_uuid, event_type, market, runtime_mode, session_date,
                        ticker, decision_id, execution_id, position_id,
                        prompt_version, brain_snapshot_id, occurred_at, reason_code,
                        data_quality, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evt.event_uuid,
                        evt.event_type,
                        evt.market,
                        evt.runtime_mode,
                        evt.session_date,
                        evt.ticker,
                        evt.decision_id,
                        evt.execution_id,
                        evt.position_id,
                        evt.prompt_version,
                        evt.brain_snapshot_id,
                        evt.occurred_at,
                        evt.reason_code,
                        evt.data_quality,
                        json.dumps(evt.payload, ensure_ascii=False, sort_keys=True),
                        utc_now_iso(),
                    ),
                )
                ids.append(int(cur.lastrowid))
                if evt.event_type != "QUALITY_MARKED":
                    conn.execute(
                        "UPDATE v2_decisions SET status=?, updated_at=? WHERE decision_id=?",
                        (evt.event_type, utc_now_iso(), evt.decision_id),
                    )
        return ids

    def create_decision(
        self,
        *,
        decision_id: str,
        market: str,
        runtime_mode: str,
        session_date: str,
        ticker: str,
        prompt_version: str,
        brain_snapshot_id: str,
        strategy_hint: str = "",
        timing_style: str = "",
        status: str = "CLAUDE_TRADE_READY",
        payload: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now_iso()
        ticker_value = str(ticker or "").strip().upper() if market == "US" else str(ticker or "").strip()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_decisions (
                    decision_id, market, runtime_mode, session_date, ticker,
                    prompt_version, brain_snapshot_id, strategy_hint, timing_style,
                    status, created_at, updated_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    market,
                    runtime_mode,
                    session_date,
                    ticker_value,
                    prompt_version,
                    brain_snapshot_id,
                    strategy_hint,
                    timing_style,
                    status,
                    now,
                    now,
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                ),
            )

    def find_decision(
        self,
        *,
        market: str,
        runtime_mode: str,
        session_date: str,
        ticker: str,
    ) -> dict[str, Any] | None:
        ticker_value = str(ticker or "").strip().upper() if market == "US" else str(ticker or "").strip()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM v2_decisions
                WHERE market=? AND runtime_mode=? AND session_date=? AND ticker=?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (market, runtime_mode, session_date, ticker_value),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def create_path_run(
        self,
        *,
        path_run_id: str,
        decision_id: str,
        path_type: str,
        market: str,
        runtime_mode: str,
        session_date: str,
        ticker: str,
        status: str,
        plan: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now_iso()
        ticker_value = str(ticker or "").strip().upper() if market == "US" else str(ticker or "").strip()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_path_runs (
                    path_run_id, decision_id, path_type, market, runtime_mode,
                    session_date, ticker, status, plan_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path_run_id) DO UPDATE SET
                    status=excluded.status,
                    plan_json=excluded.plan_json,
                    updated_at=excluded.updated_at
                """,
                (
                    path_run_id,
                    decision_id,
                    path_type,
                    market,
                    runtime_mode,
                    session_date,
                    ticker_value,
                    status,
                    json.dumps(plan or {}, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )

    def update_path_run(
        self,
        path_run_id: str,
        *,
        status: str | None = None,
        plan: dict[str, Any] | None = None,
        merge_plan: bool = False,
    ) -> None:
        current = self.find_path_run(path_run_id)
        if current is None:
            raise KeyError(f"path_run_id not found: {path_run_id}")
        next_status = status if status is not None else str(current.get("status") or "")
        if plan is None:
            next_plan = current.get("plan") or {}
        elif merge_plan:
            next_plan = {**(current.get("plan") or {}), **plan}
        else:
            next_plan = plan
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE v2_path_runs
                SET status=?, plan_json=?, updated_at=?
                WHERE path_run_id=?
                """,
                (
                    next_status,
                    json.dumps(next_plan or {}, ensure_ascii=False, sort_keys=True),
                    utc_now_iso(),
                    path_run_id,
                ),
            )

    def find_path_run(self, path_run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM v2_path_runs WHERE path_run_id=?",
                (path_run_id,),
            ).fetchone()
        return self._path_run_row_to_dict(row) if row else None

    def active_path_runs_for_ticker(
        self,
        *,
        market: str,
        ticker: str,
        session_date: str | None = None,
        runtime_mode: str | None = None,
    ) -> list[dict[str, Any]]:
        active_statuses = (
            "WAITING",
            "HIT",
            "ORDER_SENT",
            "ORDER_ACKED",
            "PARTIAL_FILLED",
            "FILLED",
            "SELL_SENT",
            "SELL_ACKED",
            "SELL_PARTIAL_FILLED",
            "ORDER_UNKNOWN",
        )
        ticker_value = str(ticker or "").strip().upper() if market == "US" else str(ticker or "").strip()
        where = ["market=?", "ticker=?", f"status IN ({','.join('?' for _ in active_statuses)})"]
        params: list[Any] = [market, ticker_value, *active_statuses]
        if session_date:
            where.append("session_date=?")
            params.append(session_date)
        if runtime_mode:
            where.append("runtime_mode=?")
            params.append(runtime_mode)
        sql = "SELECT * FROM v2_path_runs WHERE " + " AND ".join(where) + " ORDER BY created_at"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._path_run_row_to_dict(row) for row in rows]

    def path_runs_for_session(
        self,
        *,
        market: str | None = None,
        runtime_mode: str | None = None,
        session_date: str | None = None,
        status: str | None = None,
        path_type: str | None = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if market:
            where.append("market=?")
            params.append(market)
        if runtime_mode:
            where.append("runtime_mode=?")
            params.append(runtime_mode)
        if session_date:
            where.append("session_date=?")
            params.append(session_date)
        if status:
            where.append("status=?")
            params.append(status)
        if path_type:
            where.append("path_type=?")
            params.append(path_type)
        sql = "SELECT * FROM v2_path_runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at, path_run_id"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._path_run_row_to_dict(row) for row in rows]

    def events_for_decision(self, decision_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM lifecycle_events WHERE decision_id=? ORDER BY event_id",
                (decision_id,),
            ).fetchall()
        return [self._event_row_to_dict(row) for row in rows]

    def events_for_session(
        self,
        *,
        market: str | None = None,
        runtime_mode: str | None = None,
        session_date: str | None = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if market:
            where.append("market=?")
            params.append(market)
        if runtime_mode:
            where.append("runtime_mode=?")
            params.append(runtime_mode)
        if session_date:
            where.append("session_date=?")
            params.append(session_date)
        sql = "SELECT * FROM lifecycle_events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY event_id"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._event_row_to_dict(row) for row in rows]

    def count_events(self, event_type: str | None = None) -> int:
        with self.connect() as conn:
            if event_type:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM lifecycle_events WHERE event_type=?",
                    (normalize_event_type(event_type),),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS n FROM lifecycle_events").fetchone()
        return int(row["n"])

    def record_phase_validation(
        self,
        *,
        phase: int,
        ok: bool,
        qa: bool,
        simulation_report: bool,
        report: dict[str, Any],
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO phase_validation_runs
                    (phase, ok, qa, simulation_report, report_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    phase,
                    1 if ok else 0,
                    1 if qa else 0,
                    1 if simulation_report else 0,
                    json.dumps(report, ensure_ascii=False, sort_keys=True),
                    utc_now_iso(),
                ),
            )
            return int(cur.lastrowid)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        if "payload_json" in data:
            try:
                data["payload"] = json.loads(data.pop("payload_json") or "{}")
            except json.JSONDecodeError:
                data["payload"] = {}
        return data

    @classmethod
    def _event_row_to_dict(cls, row: sqlite3.Row) -> dict[str, Any]:
        return cls._row_to_dict(row)

    @staticmethod
    def _path_run_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        try:
            data["plan"] = json.loads(data.pop("plan_json") or "{}")
        except json.JSONDecodeError:
            data["plan"] = {}
        return data
