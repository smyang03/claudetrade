"""SQLite storage for market-data collection and audit results.

This module is intentionally isolated from live trading code. It stores only
collection metadata, quality issues, and backtest audit outputs.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .config import MARKET_DATA_DB


SCHEMA_VERSION = 1


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY,
    run_id TEXT UNIQUE NOT NULL,
    market TEXT,
    timeframe TEXT,
    started_at TEXT,
    completed_at TEXT,
    status TEXT,
    symbols_requested INTEGER DEFAULT 0,
    symbols_success INTEGER DEFAULT 0,
    symbols_failed INTEGER DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS symbol_master (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    raw_symbol TEXT,
    market TEXT NOT NULL,
    name TEXT,
    exchange TEXT,
    listing_date TEXT,
    delisting_date TEXT,
    is_active INTEGER DEFAULT 1,
    universe_group TEXT,
    universe_sources_json TEXT,
    UNIQUE(symbol, market)
);

CREATE TABLE IF NOT EXISTS symbol_resolution (
    id INTEGER PRIMARY KEY,
    raw_symbol TEXT NOT NULL,
    resolved_symbol TEXT,
    market TEXT,
    status TEXT,
    tried_at TEXT,
    error_msg TEXT
);

CREATE TABLE IF NOT EXISTS ohlcv_manifest (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    file_path TEXT NOT NULL,
    storage_format TEXT,
    row_count INTEGER,
    start_date TEXT,
    end_date TEXT,
    missing_rate REAL,
    quality_grade TEXT,
    adjusted_price INTEGER DEFAULT 1,
    collected_at TEXT,
    run_id TEXT,
    UNIQUE(symbol, market, timeframe)
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    timeframe TEXT,
    issue_type TEXT NOT NULL,
    issue_date TEXT,
    detail TEXT,
    severity TEXT,
    detected_at TEXT
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY,
    run_id TEXT UNIQUE NOT NULL,
    market TEXT,
    strategy TEXT,
    engine_version TEXT,
    data_start TEXT,
    data_end TEXT,
    cost_model TEXT,
    entry_model TEXT,
    params_json TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    entry_price REAL,
    exit_date TEXT,
    exit_price REAL,
    return_pct REAL,
    held_days INTEGER,
    exit_reason TEXT,
    regime TEXT,
    entry_gap_pct REAL,
    entry_day_sl_breach INTEGER,
    cost_pct REAL,
    net_return_pct REAL
);

CREATE TABLE IF NOT EXISTS strategy_metrics (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    market TEXT,
    strategy TEXT,
    regime TEXT,
    year INTEGER,
    universe_group TEXT,
    entry_model TEXT,
    trade_count INTEGER,
    win_rate REAL,
    avg_return REAL,
    avg_net_return REAL,
    profit_factor REAL,
    max_drawdown REAL,
    sharpe REAL,
    held_days_0_count INTEGER,
    held_days_0_avg_return REAL,
    UNIQUE(run_id, market, strategy, regime, year, universe_group, entry_model)
);

CREATE TABLE IF NOT EXISTS critical_flags (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    flag_type TEXT NOT NULL,
    market TEXT,
    strategy TEXT,
    value REAL,
    threshold REAL,
    severity TEXT,
    created_at TEXT
);
"""


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@contextmanager
def connect(db_path: Path = MARKET_DATA_DB) -> Iterator[sqlite3.Connection]:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_database(db_path: Path = MARKET_DATA_DB) -> Path:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, utc_now()),
        )
    return Path(db_path)


def table_names(db_path: Path = MARKET_DATA_DB) -> set[str]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row["name"]) for row in rows}


def start_collection_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    market: str,
    timeframe: str,
    symbols_requested: int,
    notes: str = "",
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO collection_runs(
            run_id, market, timeframe, started_at, completed_at, status,
            symbols_requested, symbols_success, symbols_failed, notes
        )
        VALUES (?, ?, ?, ?, NULL, 'running', ?, 0, 0, ?)
        """,
        (run_id, market, timeframe, utc_now(), int(symbols_requested), notes),
    )


def complete_collection_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    symbols_success: int,
    symbols_failed: int,
    notes: str = "",
) -> None:
    conn.execute(
        """
        UPDATE collection_runs
           SET completed_at = ?, status = ?, symbols_success = ?,
               symbols_failed = ?, notes = COALESCE(NULLIF(?, ''), notes)
         WHERE run_id = ?
        """,
        (utc_now(), status, int(symbols_success), int(symbols_failed), notes, run_id),
    )


def upsert_symbol_master(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    raw_symbol: str,
    market: str,
    universe_group: str,
    universe_sources: list[str] | tuple[str, ...] = (),
    name: str = "",
    exchange: str = "",
    is_active: int = 1,
) -> None:
    conn.execute(
        """
        INSERT INTO symbol_master(
            symbol, raw_symbol, market, name, exchange, is_active,
            universe_group, universe_sources_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, market) DO UPDATE SET
            raw_symbol = excluded.raw_symbol,
            name = excluded.name,
            exchange = excluded.exchange,
            is_active = excluded.is_active,
            universe_group = excluded.universe_group,
            universe_sources_json = excluded.universe_sources_json
        """,
        (
            symbol,
            raw_symbol,
            market,
            name,
            exchange,
            int(is_active),
            universe_group,
            json.dumps(list(universe_sources), ensure_ascii=False),
        ),
    )


def insert_symbol_resolution(
    conn: sqlite3.Connection,
    *,
    raw_symbol: str,
    resolved_symbol: str,
    market: str,
    status: str,
    error_msg: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO symbol_resolution(
            raw_symbol, resolved_symbol, market, status, tried_at, error_msg
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (raw_symbol, resolved_symbol, market, status, utc_now(), error_msg),
    )


def upsert_ohlcv_manifest(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO ohlcv_manifest(
            symbol, market, timeframe, file_path, storage_format, row_count,
            start_date, end_date, missing_rate, quality_grade, adjusted_price,
            collected_at, run_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, market, timeframe) DO UPDATE SET
            file_path = excluded.file_path,
            storage_format = excluded.storage_format,
            row_count = excluded.row_count,
            start_date = excluded.start_date,
            end_date = excluded.end_date,
            missing_rate = excluded.missing_rate,
            quality_grade = excluded.quality_grade,
            adjusted_price = excluded.adjusted_price,
            collected_at = excluded.collected_at,
            run_id = excluded.run_id
        """,
        (
            row.get("symbol"),
            row.get("market"),
            row.get("timeframe"),
            row.get("file_path"),
            row.get("storage_format"),
            int(row.get("row_count", 0) or 0),
            row.get("start_date", ""),
            row.get("end_date", ""),
            float(row.get("missing_rate", 0.0) or 0.0),
            row.get("quality_grade", ""),
            int(row.get("adjusted_price", 1)),
            row.get("collected_at") or utc_now(),
            row.get("run_id", ""),
        ),
    )


def insert_quality_issues(conn: sqlite3.Connection, issues: list[dict]) -> None:
    for issue in issues:
        conn.execute(
            """
            INSERT INTO data_quality_issues(
                symbol, market, timeframe, issue_type, issue_date,
                detail, severity, detected_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                issue.get("symbol"),
                issue.get("market"),
                issue.get("timeframe"),
                issue.get("issue_type"),
                issue.get("issue_date", ""),
                issue.get("detail", ""),
                issue.get("severity", "warn"),
                issue.get("detected_at") or utc_now(),
            ),
        )
