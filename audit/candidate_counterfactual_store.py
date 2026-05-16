from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(data: Any) -> str:
    return json.dumps(data or {}, ensure_ascii=False, sort_keys=True, default=str)


class CandidateCounterfactualStore:
    def __init__(self, path: str | Path, *, timeout: float = 5.0) -> None:
        self.path = Path(path)
        self.timeout = float(timeout or 5.0)
        self.last_upsert_errors: list[dict[str, Any]] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=self.timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def init(self) -> None:
        with closing(self.connect()) as conn:
            with conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS candidate_counterfactual_paths (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      runtime_mode TEXT NOT NULL,
                      session_date TEXT NOT NULL,
                      market TEXT NOT NULL,
                      ticker TEXT NOT NULL,
                      candidate_key TEXT,
                      call_id TEXT,
                      signal_time TEXT NOT NULL,
                      known_at TEXT NOT NULL,
                      trade_ready_action TEXT,
                      actual_path TEXT,
                      path_name TEXT NOT NULL,
                      trigger_time TEXT,
                      trigger_price REAL,
                      trigger_reason TEXT,
                      entry_price REAL,
                      entry_delay_min REAL,
                      outcome_30m_pct REAL,
                      outcome_60m_pct REAL,
                      outcome_close_pct REAL,
                      max_runup_60m_pct REAL,
                      max_drawdown_60m_pct REAL,
                      status TEXT NOT NULL DEFAULT 'PENDING',
                      metadata_json TEXT DEFAULT '{}',
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    );

                    CREATE UNIQUE INDEX IF NOT EXISTS idx_counterfactual_path_unique
                    ON candidate_counterfactual_paths (
                      runtime_mode,
                      session_date,
                      market,
                      ticker,
                      known_at,
                      path_name
                    );

                    CREATE INDEX IF NOT EXISTS idx_counterfactual_path_lookup
                    ON candidate_counterfactual_paths (
                      session_date,
                      market,
                      ticker,
                      status
                    );
                    """
                )

    def _row_payload(self, row: dict[str, Any], now: str) -> dict[str, Any]:
        return {
            "runtime_mode": str(row.get("runtime_mode") or "live"),
            "session_date": str(row.get("session_date") or ""),
            "market": str(row.get("market") or "").upper(),
            "ticker": str(row.get("ticker") or "").strip().upper()
            if str(row.get("market") or "").upper() == "US"
            else str(row.get("ticker") or "").strip(),
            "candidate_key": row.get("candidate_key"),
            "call_id": row.get("call_id"),
            "signal_time": str(row.get("signal_time") or row.get("known_at") or now),
            "known_at": str(row.get("known_at") or now),
            "trade_ready_action": row.get("trade_ready_action"),
            "actual_path": row.get("actual_path"),
            "path_name": str(row.get("path_name") or "immediate"),
            "trigger_time": row.get("trigger_time"),
            "trigger_price": row.get("trigger_price"),
            "trigger_reason": row.get("trigger_reason"),
            "entry_price": row.get("entry_price"),
            "entry_delay_min": row.get("entry_delay_min"),
            "outcome_30m_pct": row.get("outcome_30m_pct"),
            "outcome_60m_pct": row.get("outcome_60m_pct"),
            "outcome_close_pct": row.get("outcome_close_pct"),
            "max_runup_60m_pct": row.get("max_runup_60m_pct"),
            "max_drawdown_60m_pct": row.get("max_drawdown_60m_pct"),
            "status": str(row.get("status") or "PENDING"),
            "metadata_json": row.get("metadata_json") if isinstance(row.get("metadata_json"), str) else _json(row.get("metadata")),
        }

    def _upsert_path_conn(self, conn: sqlite3.Connection, row: dict[str, Any]) -> None:
        now = _now()
        payload = self._row_payload(row, now)
        conn.execute(
            """
            INSERT INTO candidate_counterfactual_paths (
              runtime_mode, session_date, market, ticker, candidate_key, call_id,
              signal_time, known_at, trade_ready_action, actual_path, path_name,
              trigger_time, trigger_price, trigger_reason, entry_price, entry_delay_min,
              outcome_30m_pct, outcome_60m_pct, outcome_close_pct,
              max_runup_60m_pct, max_drawdown_60m_pct, status, metadata_json,
              created_at, updated_at
            )
            VALUES (
              :runtime_mode, :session_date, :market, :ticker, :candidate_key, :call_id,
              :signal_time, :known_at, :trade_ready_action, :actual_path, :path_name,
              :trigger_time, :trigger_price, :trigger_reason, :entry_price, :entry_delay_min,
              :outcome_30m_pct, :outcome_60m_pct, :outcome_close_pct,
              :max_runup_60m_pct, :max_drawdown_60m_pct, :status, :metadata_json,
              :created_at, :updated_at
            )
            ON CONFLICT(runtime_mode, session_date, market, ticker, known_at, path_name)
            DO UPDATE SET
              candidate_key=excluded.candidate_key,
              call_id=excluded.call_id,
              trade_ready_action=excluded.trade_ready_action,
              actual_path=excluded.actual_path,
              trigger_time=COALESCE(excluded.trigger_time, candidate_counterfactual_paths.trigger_time),
              trigger_price=COALESCE(excluded.trigger_price, candidate_counterfactual_paths.trigger_price),
              trigger_reason=COALESCE(excluded.trigger_reason, candidate_counterfactual_paths.trigger_reason),
              entry_price=COALESCE(excluded.entry_price, candidate_counterfactual_paths.entry_price),
              entry_delay_min=COALESCE(excluded.entry_delay_min, candidate_counterfactual_paths.entry_delay_min),
              outcome_30m_pct=COALESCE(excluded.outcome_30m_pct, candidate_counterfactual_paths.outcome_30m_pct),
              outcome_60m_pct=COALESCE(excluded.outcome_60m_pct, candidate_counterfactual_paths.outcome_60m_pct),
              outcome_close_pct=COALESCE(excluded.outcome_close_pct, candidate_counterfactual_paths.outcome_close_pct),
              max_runup_60m_pct=COALESCE(excluded.max_runup_60m_pct, candidate_counterfactual_paths.max_runup_60m_pct),
              max_drawdown_60m_pct=COALESCE(excluded.max_drawdown_60m_pct, candidate_counterfactual_paths.max_drawdown_60m_pct),
              status=excluded.status,
              metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
            """,
            {**payload, "created_at": now, "updated_at": now},
        )

    def upsert_path(self, row: dict[str, Any]) -> None:
        with closing(self.connect()) as conn:
            with conn:
                self._upsert_path_conn(conn, row)

    def upsert_paths(self, rows: Iterable[dict[str, Any]]) -> int:
        count = 0
        errors: list[dict[str, Any]] = []
        with closing(self.connect()) as conn:
            with conn:
                for index, row in enumerate(rows):
                    try:
                        self._upsert_path_conn(conn, row)
                        count += 1
                    except Exception as exc:
                        errors.append(
                            {
                                "index": index,
                                "ticker": str((row or {}).get("ticker") or ""),
                                "path_name": str((row or {}).get("path_name") or ""),
                                "error": str(exc),
                            }
                        )
        self.last_upsert_errors = errors
        return count

    def fetch_rows(self, *, session_date: str = "", market: str = "", status: str = "") -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if session_date:
            where.append("session_date=?")
            params.append(session_date)
        if market:
            where.append("market=?")
            params.append(str(market).upper())
        if status:
            where.append("status=?")
            params.append(status)
        sql = "SELECT * FROM candidate_counterfactual_paths"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY session_date, market, ticker, known_at, path_name"
        with closing(self.connect()) as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def mark_outcome(self, row_id: int, **updates: Any) -> None:
        if not updates:
            return
        allowed = {
            "outcome_30m_pct",
            "outcome_60m_pct",
            "outcome_close_pct",
            "max_runup_60m_pct",
            "max_drawdown_60m_pct",
            "status",
            "metadata_json",
        }
        items = {k: v for k, v in updates.items() if k in allowed}
        if not items:
            return
        items["updated_at"] = _now()
        set_sql = ", ".join(f"{key}=:{key}" for key in items)
        with closing(self.connect()) as conn:
            with conn:
                conn.execute(
                    f"UPDATE candidate_counterfactual_paths SET {set_sql} WHERE id=:id",
                    {**items, "id": int(row_id)},
                )
