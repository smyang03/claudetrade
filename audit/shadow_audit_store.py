from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(data: Any) -> str:
    return json.dumps(data or {}, ensure_ascii=False, sort_keys=True, default=str)


class ShadowAuditStore:
    """SQLite store for isolated shadow audit data."""

    def __init__(self, path: str | Path, *, timeout: float = 2.0) -> None:
        self.path = Path(path)
        self.timeout = float(timeout or 2.0)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=self.timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def init(self) -> None:
        conn = sqlite3.connect(str(self.path), timeout=self.timeout)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_signals (
                    signal_id TEXT PRIMARY KEY,
                    decision_id TEXT,
                    path_run_id TEXT,
                    market TEXT NOT NULL,
                    runtime_mode TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    strategy TEXT,
                    path_type TEXT,
                    source TEXT,
                    signal_at TEXT,
                    signal_at_bucket TEXT,
                    signal_price REAL,
                    risk_price_krw REAL,
                    score REAL,
                    decision TEXT,
                    block_reason TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_signals_session
                    ON audit_signals(runtime_mode, market, session_date, ticker);
                CREATE INDEX IF NOT EXISTS idx_audit_signals_decision
                    ON audit_signals(decision_id);
                CREATE INDEX IF NOT EXISTS idx_audit_signals_path_run
                    ON audit_signals(path_run_id);

                CREATE TABLE IF NOT EXISTS audit_signal_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    reason_code TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_signal_events_signal
                    ON audit_signal_events(signal_id, id);
                CREATE INDEX IF NOT EXISTS idx_audit_signal_events_type
                    ON audit_signal_events(event_type, occurred_at);

                CREATE TABLE IF NOT EXISTS audit_episodes (
                    episode_id TEXT PRIMARY KEY,
                    episode_type TEXT NOT NULL,
                    market TEXT NOT NULL,
                    runtime_mode TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    scope TEXT,
                    ticker TEXT,
                    started_at TEXT,
                    ended_at TEXT,
                    status TEXT,
                    start_reason TEXT,
                    clear_reason TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_episodes_session
                    ON audit_episodes(runtime_mode, market, session_date, episode_type);

                CREATE TABLE IF NOT EXISTS audit_signal_episode_links (
                    signal_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    link_reason TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(signal_id, episode_id)
                );

                CREATE TABLE IF NOT EXISTS audit_price_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market TEXT NOT NULL,
                    runtime_mode TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    sampled_at TEXT NOT NULL,
                    price REAL NOT NULL,
                    source TEXT,
                    quality TEXT,
                    decision_id TEXT,
                    signal_id TEXT,
                    path_run_id TEXT,
                    episode_id TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_price_samples_lookup
                    ON audit_price_samples(runtime_mode, market, session_date, ticker, sampled_at);
                CREATE INDEX IF NOT EXISTS idx_audit_price_samples_signal
                    ON audit_price_samples(signal_id, sampled_at);

                CREATE TABLE IF NOT EXISTS audit_signal_outcomes (
                    signal_id TEXT NOT NULL,
                    horizon_min INTEGER NOT NULL,
                    target_at TEXT,
                    observed_at TEXT,
                    observed_price REAL,
                    return_pct REAL,
                    max_runup_pct REAL,
                    max_drawdown_pct REAL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    UNIQUE(signal_id, horizon_min)
                );

                CREATE TABLE IF NOT EXISTS audit_trade_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT,
                    decision_id TEXT,
                    path_run_id TEXT,
                    order_no TEXT,
                    position_id TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    pnl_pct REAL,
                    exit_reason TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_trade_links_signal
                    ON audit_trade_links(signal_id);
                CREATE INDEX IF NOT EXISTS idx_audit_trade_links_decision
                    ON audit_trade_links(decision_id);

                CREATE TABLE IF NOT EXISTS audit_writer_health (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event_type TEXT,
                    queued INTEGER DEFAULT 0,
                    written INTEGER DEFAULT 0,
                    dropped INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    last_error TEXT,
                    queue_size INTEGER DEFAULT 0
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

    def write_events(self, events: Iterable[dict[str, Any]]) -> int:
        items = [dict(event or {}) for event in events]
        if not items:
            return 0
        written = 0
        conn = self.connect()
        try:
            for event in items:
                kind = str(event.get("kind") or "").strip()
                if not kind:
                    continue
                handler = getattr(self, f"_write_{kind}", None)
                if handler is None:
                    continue
                handler(conn, event)
                written += 1
            conn.commit()
        finally:
            conn.close()
        return written

    def _write_signal(self, conn: sqlite3.Connection, event: dict[str, Any]) -> None:
        now = _utc_now()
        payload = event.get("payload") or {}
        conn.execute(
            """
            INSERT INTO audit_signals (
                signal_id, decision_id, path_run_id, market, runtime_mode, session_date,
                ticker, strategy, path_type, source, signal_at, signal_at_bucket,
                signal_price, risk_price_krw, score, decision, block_reason,
                payload_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id) DO UPDATE SET
                decision_id=COALESCE(NULLIF(excluded.decision_id, ''), audit_signals.decision_id),
                path_run_id=COALESCE(NULLIF(excluded.path_run_id, ''), audit_signals.path_run_id),
                decision=COALESCE(NULLIF(excluded.decision, ''), audit_signals.decision),
                block_reason=COALESCE(NULLIF(excluded.block_reason, ''), audit_signals.block_reason),
                score=COALESCE(excluded.score, audit_signals.score),
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                event.get("signal_id"),
                event.get("decision_id", ""),
                event.get("path_run_id", ""),
                event.get("market", ""),
                event.get("runtime_mode", ""),
                event.get("session_date", ""),
                event.get("ticker", ""),
                event.get("strategy", ""),
                event.get("path_type", ""),
                event.get("source", ""),
                event.get("signal_at", ""),
                event.get("signal_at_bucket", ""),
                event.get("signal_price"),
                event.get("risk_price_krw"),
                event.get("score"),
                event.get("decision", ""),
                event.get("block_reason", ""),
                _json(payload),
                now,
                now,
            ),
        )

    def _write_signal_event(self, conn: sqlite3.Connection, event: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO audit_signal_events (
                signal_id, event_type, occurred_at, reason_code, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.get("signal_id", ""),
                event.get("event_type", ""),
                event.get("occurred_at") or _utc_now(),
                event.get("reason_code", ""),
                _json(event.get("payload") or {}),
                _utc_now(),
            ),
        )

    def _write_episode(self, conn: sqlite3.Connection, event: dict[str, Any]) -> None:
        now = _utc_now()
        conn.execute(
            """
            INSERT INTO audit_episodes (
                episode_id, episode_type, market, runtime_mode, session_date, scope,
                ticker, started_at, ended_at, status, start_reason, clear_reason,
                payload_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(episode_id) DO UPDATE SET
                ended_at=COALESCE(NULLIF(excluded.ended_at, ''), audit_episodes.ended_at),
                status=COALESCE(NULLIF(excluded.status, ''), audit_episodes.status),
                clear_reason=COALESCE(NULLIF(excluded.clear_reason, ''), audit_episodes.clear_reason),
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                event.get("episode_id", ""),
                event.get("episode_type", ""),
                event.get("market", ""),
                event.get("runtime_mode", ""),
                event.get("session_date", ""),
                event.get("scope", ""),
                event.get("ticker", ""),
                event.get("started_at", ""),
                event.get("ended_at", ""),
                event.get("status", ""),
                event.get("start_reason", ""),
                event.get("clear_reason", ""),
                _json(event.get("payload") or {}),
                now,
                now,
            ),
        )

    def _write_episode_link(self, conn: sqlite3.Connection, event: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO audit_signal_episode_links (
                signal_id, episode_id, link_reason, created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                event.get("signal_id", ""),
                event.get("episode_id", ""),
                event.get("link_reason", ""),
                _utc_now(),
            ),
        )

    def _write_price_sample(self, conn: sqlite3.Connection, event: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO audit_price_samples (
                market, runtime_mode, session_date, ticker, sampled_at, price, source,
                quality, decision_id, signal_id, path_run_id, episode_id, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.get("market", ""),
                event.get("runtime_mode", ""),
                event.get("session_date", ""),
                event.get("ticker", ""),
                event.get("sampled_at") or _utc_now(),
                float(event.get("price") or 0),
                event.get("source", ""),
                event.get("quality", ""),
                event.get("decision_id", ""),
                event.get("signal_id", ""),
                event.get("path_run_id", ""),
                event.get("episode_id", ""),
                _json(event.get("payload") or {}),
                _utc_now(),
            ),
        )

    def _write_outcome(self, conn: sqlite3.Connection, event: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO audit_signal_outcomes (
                signal_id, horizon_min, target_at, observed_at, observed_price,
                return_pct, max_runup_pct, max_drawdown_pct, status, payload_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id, horizon_min) DO UPDATE SET
                observed_at=excluded.observed_at,
                observed_price=excluded.observed_price,
                return_pct=excluded.return_pct,
                max_runup_pct=excluded.max_runup_pct,
                max_drawdown_pct=excluded.max_drawdown_pct,
                status=excluded.status,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                event.get("signal_id", ""),
                int(event.get("horizon_min") or 0),
                event.get("target_at", ""),
                event.get("observed_at", ""),
                event.get("observed_price"),
                event.get("return_pct"),
                event.get("max_runup_pct"),
                event.get("max_drawdown_pct"),
                event.get("status", ""),
                _json(event.get("payload") or {}),
                _utc_now(),
            ),
        )

    def _write_trade_link(self, conn: sqlite3.Connection, event: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO audit_trade_links (
                signal_id, decision_id, path_run_id, order_no, position_id,
                entry_price, exit_price, pnl_pct, exit_reason, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.get("signal_id", ""),
                event.get("decision_id", ""),
                event.get("path_run_id", ""),
                event.get("order_no", ""),
                event.get("position_id", ""),
                event.get("entry_price"),
                event.get("exit_price"),
                event.get("pnl_pct"),
                event.get("exit_reason", ""),
                _json(event.get("payload") or {}),
                _utc_now(),
            ),
        )

    def _write_health(self, conn: sqlite3.Connection, event: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO audit_writer_health (
                ts, event_type, queued, written, dropped, error_count, last_error, queue_size
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.get("ts") or _utc_now(),
                event.get("event_type", ""),
                int(event.get("queued") or 0),
                int(event.get("written") or 0),
                int(event.get("dropped") or 0),
                int(event.get("error_count") or 0),
                str(event.get("last_error") or "")[:500],
                int(event.get("queue_size") or 0),
            ),
        )
