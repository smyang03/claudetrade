from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
from datetime import datetime, timedelta, timezone
import json
import sqlite3

from runtime_paths import get_runtime_path
from lifecycle.models import LifecycleEvent, normalize_event_type, utc_now_iso


NON_STATUS_EVENT_TYPES = {
    "QUALITY_MARKED",
    "EXECUTION_ADVISOR_DECISION",
    "PATHB_SELECTION_RECONCILE",
    "PATHB_SELECTION_RECONCILE_ERROR",
}


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


class EventStore:
    def __init__(self, path: str | Path | None = None, *, read_only: bool = False, initialize: bool = True):
        self.path = Path(path) if path is not None else get_runtime_path("data", "v2_event_store.db")
        self.read_only = bool(read_only)
        if not self.read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        if initialize and not self.read_only:
            self.init()

    def connect(self) -> sqlite3.Connection:
        if self.read_only:
            uri = f"file:{self.path.resolve().as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=30, factory=ClosingConnection)
        else:
            conn = sqlite3.connect(str(self.path), timeout=30, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        if not self.read_only:
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

                CREATE TABLE IF NOT EXISTS pathb_miss_quality (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path_run_id TEXT NOT NULL,
                    decision_id TEXT,
                    market TEXT NOT NULL,
                    runtime_mode TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    cancelled_at TEXT NOT NULL,
                    cancel_reason TEXT NOT NULL,
                    current_at_plan REAL,
                    open_price REAL,
                    buy_zone_low REAL,
                    buy_zone_high REAL,
                    cancel_if_open_above REAL,
                    cancel_trigger_price REAL,
                    reference_price REAL,
                    baseline_price REAL,
                    baseline_source TEXT,
                    market_close_at TEXT,
                    followup_due_at TEXT NOT NULL,
                    followup_filled_at TEXT,
                    followup_status TEXT NOT NULL DEFAULT 'pending',
                    zone_reentered_after_cancel INTEGER,
                    mfe_30m_pct REAL,
                    mae_30m_pct REAL,
                    observed_price_30m REAL,
                    quote_sample_count INTEGER,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_pathb_miss_quality_run_reason
                    ON pathb_miss_quality(path_run_id, cancel_reason);
                CREATE INDEX IF NOT EXISTS idx_pathb_miss_quality_due
                    ON pathb_miss_quality(followup_status, followup_due_at);
                CREATE INDEX IF NOT EXISTS idx_pathb_miss_quality_run
                    ON pathb_miss_quality(path_run_id);
                CREATE INDEX IF NOT EXISTS idx_pathb_miss_quality_market_date
                    ON pathb_miss_quality(market, session_date);
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
            if evt.event_type not in NON_STATUS_EVENT_TYPES:
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
                if evt.event_type not in NON_STATUS_EVENT_TYPES:
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

    def latest_event_attribution(
        self,
        *,
        market: str,
        runtime_mode: str,
        session_date: str,
        ticker: str,
        execution_id: str = "",
        position_id: str = "",
        decision_id: str = "",
    ) -> dict[str, Any]:
        ticker_value = str(ticker or "").strip().upper() if market == "US" else str(ticker or "").strip()
        clauses = ["market=?", "runtime_mode=?", "session_date=?", "ticker=?"]
        params: list[Any] = [market, runtime_mode, session_date, ticker_value]
        match_clauses: list[str] = []
        if execution_id:
            match_clauses.append("execution_id=?")
            params.append(execution_id)
        if position_id:
            match_clauses.append("position_id=?")
            params.append(position_id)
        if decision_id:
            match_clauses.append("decision_id=?")
            params.append(decision_id)
        if match_clauses:
            clauses.append("(" + " OR ".join(match_clauses) + ")")
        clauses.append(
            "event_type IN ('ORDER_SENT','ORDER_ACKED','PARTIAL_FILLED','FILLED','ORDER_UNKNOWN','CLOSED')"
        )
        sql = (
            "SELECT * FROM lifecycle_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY event_id DESC LIMIT 25"
        )
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        for row in rows:
            data = self._event_row_to_dict(row)
            payload = data.get("payload") or {}
            if isinstance(payload, dict) and (
                payload.get("entry_route") or payload.get("path_run_id") or payload.get("pathb_path_run_id")
            ):
                entry_route = str(payload.get("entry_route") or "")
                path_run_id = str(payload.get("path_run_id") or payload.get("pathb_path_run_id") or "")
                path_type = str(payload.get("path_type") or "")
                if not entry_route and path_run_id:
                    entry_route = "path_b"
                if not path_type and (entry_route == "path_b" or path_run_id):
                    path_type = "claude_price"
                return {
                    "entry_route": entry_route,
                    "path_type": path_type,
                    "path_run_id": path_run_id,
                    "parent_decision_id": str(payload.get("parent_decision_id") or data.get("decision_id") or ""),
                    "selection_snapshot_ts": str(payload.get("selection_snapshot_ts") or ""),
                    "strategy_used": str(payload.get("strategy_used") or payload.get("strategy") or ""),
                    "route_source": str(payload.get("route_source") or "carry_forward_event"),
                    "attribution_source_event_id": data.get("event_id"),
                    "attribution_source_event_type": data.get("event_type"),
                }
        return {}

    def record_pathb_miss_quality(
        self,
        *,
        path_run_id: str,
        decision_id: str,
        market: str,
        runtime_mode: str,
        session_date: str,
        ticker: str,
        cancel_reason: str,
        cancelled_at: str | None = None,
        current_at_plan: float | None = None,
        open_price: float | None = None,
        buy_zone_low: float | None = None,
        buy_zone_high: float | None = None,
        cancel_if_open_above: float | None = None,
        cancel_trigger_price: float | None = None,
        reference_price: float | None = None,
        market_close_at: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now_iso()
        cancelled = str(cancelled_at or now)
        try:
            base_dt = datetime.fromisoformat(cancelled.replace("Z", "+00:00"))
        except Exception:
            base_dt = datetime.now(timezone.utc)
        if base_dt.tzinfo is None:
            base_dt = base_dt.replace(tzinfo=timezone.utc)
        followup_due_at = (base_dt.astimezone(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")

        baseline_price = None
        baseline_source = ""
        for source, value in (
            ("cancel_trigger_price", cancel_trigger_price),
            ("reference_price", reference_price),
            ("buy_zone_high", buy_zone_high),
        ):
            try:
                parsed = float(value or 0)
            except Exception:
                parsed = 0.0
            if parsed > 0:
                baseline_price = parsed
                baseline_source = source
                break

        ticker_value = str(ticker or "").strip().upper() if market == "US" else str(ticker or "").strip()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO pathb_miss_quality (
                    path_run_id, decision_id, market, runtime_mode, session_date, ticker,
                    cancelled_at, cancel_reason, current_at_plan, open_price,
                    buy_zone_low, buy_zone_high, cancel_if_open_above, cancel_trigger_price,
                    reference_price, baseline_price, baseline_source, market_close_at,
                    followup_due_at, followup_status, payload_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                ON CONFLICT(path_run_id, cancel_reason) DO UPDATE SET
                    current_at_plan=excluded.current_at_plan,
                    open_price=excluded.open_price,
                    buy_zone_low=excluded.buy_zone_low,
                    buy_zone_high=excluded.buy_zone_high,
                    cancel_if_open_above=excluded.cancel_if_open_above,
                    cancel_trigger_price=excluded.cancel_trigger_price,
                    reference_price=excluded.reference_price,
                    baseline_price=excluded.baseline_price,
                    baseline_source=excluded.baseline_source,
                    market_close_at=excluded.market_close_at,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    path_run_id,
                    decision_id,
                    market,
                    runtime_mode,
                    session_date,
                    ticker_value,
                    cancelled,
                    cancel_reason,
                    current_at_plan,
                    open_price,
                    buy_zone_low,
                    buy_zone_high,
                    cancel_if_open_above,
                    cancel_trigger_price,
                    reference_price,
                    baseline_price,
                    baseline_source,
                    market_close_at,
                    followup_due_at,
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )

    def pending_pathb_miss_quality(self, *, now_iso: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM pathb_miss_quality
                WHERE followup_status='pending' AND followup_due_at <= ?
                ORDER BY followup_due_at
                LIMIT ?
                """,
                (now_iso, int(limit or 20)),
            ).fetchall()
        return [self._pathb_miss_quality_row_to_dict(row) for row in rows]

    def update_pathb_miss_quality_followup(
        self,
        row_id: int,
        *,
        followup_status: str,
        zone_reentered_after_cancel: bool | None = None,
        mfe_30m_pct: float | None = None,
        mae_30m_pct: float | None = None,
        observed_price_30m: float | None = None,
        quote_sample_count: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE pathb_miss_quality
                SET followup_status=?,
                    followup_filled_at=?,
                    zone_reentered_after_cancel=?,
                    mfe_30m_pct=?,
                    mae_30m_pct=?,
                    observed_price_30m=?,
                    quote_sample_count=?,
                    payload_json=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    followup_status,
                    now,
                    None if zone_reentered_after_cancel is None else (1 if zone_reentered_after_cancel else 0),
                    mfe_30m_pct,
                    mae_30m_pct,
                    observed_price_30m,
                    quote_sample_count,
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                    now,
                    int(row_id),
                ),
            )

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

    @staticmethod
    def _pathb_miss_quality_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        try:
            data["payload"] = json.loads(data.pop("payload_json") or "{}")
        except json.JSONDecodeError:
            data["payload"] = {}
        return data
