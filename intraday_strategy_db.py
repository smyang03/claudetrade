"""
intraday_strategy_db.py

OR/VWAP 계열 장중 전략 실험용 raw 데이터 저장소.
현재는 스키마/인터페이스만 먼저 준비하고, 실제 기록은 전략 구현 시점에 연결한다.
"""

import os
import sqlite3
from datetime import datetime
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "intraday_strategy_log.db")


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS intraday_strategy_log (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                ts                     TEXT NOT NULL,
                session_date           TEXT NOT NULL,
                market                 TEXT NOT NULL,
                ticker                 TEXT NOT NULL,
                strategy_name          TEXT NOT NULL,
                stage                  TEXT NOT NULL,   -- probe|signal|trade|outcome

                -- OR 계열
                or_formed              INTEGER,
                or_high                REAL,
                or_low                 REAL,
                or_range_pct           REAL,
                pullback_depth_pct     REAL,
                entry_window_elapsed_min REAL,

                -- VWAP 계열 (향후)
                vwap                   REAL,
                vwap_deviation_pct     REAL,
                vwap_confirm_candles   INTEGER,
                vwap_zscore            REAL,

                -- 공통 진단
                price                  REAL,
                volume                 REAL,
                vol_ratio              REAL,
                from_high_pct          REAL,
                signal_fired           INTEGER DEFAULT 0,
                traded                 INTEGER DEFAULT 0,
                blocked_reason         TEXT,
                pnl_pct                REAL,
                note                   TEXT,
                created_at             TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_intraday_log_date_market "
            "ON intraday_strategy_log(session_date, market)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_intraday_log_ticker "
            "ON intraday_strategy_log(market, ticker, strategy_name)"
        )


def insert_probe(
    session_date: str,
    market: str,
    ticker: str,
    strategy_name: str,
    **kwargs: Any,
) -> int:
    now = kwargs.pop("ts", None) or datetime.now().isoformat()
    payload = {
        "ts": now,
        "session_date": session_date,
        "market": market,
        "ticker": ticker,
        "strategy_name": strategy_name,
        "stage": kwargs.pop("stage", "probe"),
        "or_formed": kwargs.pop("or_formed", None),
        "or_high": kwargs.pop("or_high", None),
        "or_low": kwargs.pop("or_low", None),
        "or_range_pct": kwargs.pop("or_range_pct", None),
        "pullback_depth_pct": kwargs.pop("pullback_depth_pct", None),
        "entry_window_elapsed_min": kwargs.pop("entry_window_elapsed_min", None),
        "vwap": kwargs.pop("vwap", None),
        "vwap_deviation_pct": kwargs.pop("vwap_deviation_pct", None),
        "vwap_confirm_candles": kwargs.pop("vwap_confirm_candles", None),
        "vwap_zscore": kwargs.pop("vwap_zscore", None),
        "price": kwargs.pop("price", None),
        "volume": kwargs.pop("volume", None),
        "vol_ratio": kwargs.pop("vol_ratio", None),
        "from_high_pct": kwargs.pop("from_high_pct", None),
        "signal_fired": kwargs.pop("signal_fired", 0),
        "traded": kwargs.pop("traded", 0),
        "blocked_reason": kwargs.pop("blocked_reason", None),
        "pnl_pct": kwargs.pop("pnl_pct", None),
        "note": kwargs.pop("note", None),
    }
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO intraday_strategy_log (
                ts, session_date, market, ticker, strategy_name, stage,
                or_formed, or_high, or_low, or_range_pct, pullback_depth_pct, entry_window_elapsed_min,
                vwap, vwap_deviation_pct, vwap_confirm_candles, vwap_zscore,
                price, volume, vol_ratio, from_high_pct,
                signal_fired, traded, blocked_reason, pnl_pct, note
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            tuple(payload.values()),
        )
        return int(cur.lastrowid)


def update_signal(row_id: int, blocked_reason: str = "") -> None:
    if not row_id:
        return
    with _conn() as conn:
        conn.execute(
            """
            UPDATE intraday_strategy_log
            SET stage='signal', signal_fired=1, blocked_reason=?
            WHERE id=?
            """,
            (blocked_reason or None, row_id),
        )


def update_trade(row_id: int) -> None:
    if not row_id:
        return
    with _conn() as conn:
        conn.execute(
            """
            UPDATE intraday_strategy_log
            SET stage='trade', traded=1
            WHERE id=?
            """,
            (row_id,),
        )


def update_outcome(row_id: int, pnl_pct: float, note: str = "") -> None:
    if not row_id:
        return
    with _conn() as conn:
        conn.execute(
            """
            UPDATE intraday_strategy_log
            SET stage='outcome', pnl_pct=?, note=?
            WHERE id=?
            """,
            (pnl_pct, note or None, row_id),
        )
