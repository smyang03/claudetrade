"""Read-only adapter for collected yfinance market-data files.

This module is the bridge between Phase-1 collection metadata and the isolated
backtest engine. It never downloads data and never imports live trading modules.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from indicators import calc_all

from .config import MARKET_DATA_DB
from .data_quality import normalize_ohlcv_frame


QUALITY_RANK = {"FAIL": 0, "C": 1, "B": 2, "A": 3}


def _quality_rank(value: str) -> int:
    return QUALITY_RANK.get(str(value or "FAIL").upper(), 0)


def allowed_quality_grades(min_quality: str = "C") -> set[str]:
    threshold = _quality_rank(min_quality)
    return {grade for grade, rank in QUALITY_RANK.items() if rank >= threshold}


def collected_manifest_rows(
    market: str,
    *,
    db_path: Path = MARKET_DATA_DB,
    min_quality: str = "C",
    timeframe: str = "daily",
) -> list[dict[str, Any]]:
    """Return collected OHLCV manifest rows that meet the quality threshold."""

    market_u = str(market or "").upper()
    grades = allowed_quality_grades(min_quality)
    if not Path(db_path).exists():
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                m.symbol,
                m.market,
                m.timeframe,
                m.file_path,
                m.storage_format,
                m.row_count,
                m.start_date,
                m.end_date,
                m.missing_rate,
                m.quality_grade,
                COALESCE(s.universe_group, '') AS universe_group
            FROM ohlcv_manifest m
            LEFT JOIN symbol_master s
              ON s.symbol = m.symbol AND s.market = m.market
            WHERE m.market = ?
              AND m.timeframe = ?
            ORDER BY m.symbol
            """,
            (market_u, timeframe),
        ).fetchall()
    finally:
        conn.close()

    usable: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        path = Path(str(record.get("file_path") or ""))
        if str(record.get("quality_grade", "")).upper() not in grades:
            continue
        if not path.exists():
            continue
        usable.append(record)
    return usable


def available_collected_tickers(
    market: str,
    *,
    db_path: Path = MARKET_DATA_DB,
    min_quality: str = "C",
    timeframe: str = "daily",
    limit: int = 0,
) -> list[str]:
    rows = collected_manifest_rows(market, db_path=db_path, min_quality=min_quality, timeframe=timeframe)
    symbols = [str(row["symbol"]) for row in rows]
    return symbols[:limit] if limit and limit > 0 else symbols


def collected_universe_group_map(
    market: str,
    *,
    db_path: Path = MARKET_DATA_DB,
    min_quality: str = "C",
) -> dict[str, str]:
    rows = collected_manifest_rows(market, db_path=db_path, min_quality=min_quality)
    return {str(row["symbol"]): str(row.get("universe_group") or "unknown") for row in rows}


def latest_collected_end_date(
    market: str,
    *,
    db_path: Path = MARKET_DATA_DB,
    min_quality: str = "C",
    timeframe: str = "daily",
) -> str:
    rows = collected_manifest_rows(market, db_path=db_path, min_quality=min_quality, timeframe=timeframe)
    dates = sorted(str(row.get("end_date") or "") for row in rows if row.get("end_date"))
    return dates[-1] if dates else ""


def _manifest_path_for_symbol(market: str, ticker: str, db_path: Path, timeframe: str = "daily") -> Path | None:
    if not Path(db_path).exists():
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT file_path
              FROM ohlcv_manifest
             WHERE market = ?
               AND symbol = ?
               AND timeframe = ?
             LIMIT 1
            """,
            (str(market or "").upper(), ticker, timeframe),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return Path(str(row["file_path"]))


def load_collected_price_frame(
    market: str,
    ticker: str,
    *,
    db_path: Path = MARKET_DATA_DB,
) -> pd.DataFrame:
    """Load one collected OHLCV file and add the standard indicator columns."""

    path = _manifest_path_for_symbol(market, ticker, db_path, "daily")
    if path is None or not path.exists():
        return pd.DataFrame()
    if path.suffix.lower() == ".parquet":
        raw = pd.read_parquet(path)
    else:
        raw = pd.read_csv(path)
    frame = normalize_ohlcv_frame(raw)
    if frame.empty:
        return pd.DataFrame()
    frame = frame.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    return calc_all(frame)


def load_collected_intraday_frame(
    market: str,
    ticker: str,
    *,
    timeframe: str = "5m",
    db_path: Path = MARKET_DATA_DB,
) -> pd.DataFrame:
    """Load one collected intraday OHLCV file without adding daily indicators."""

    path = _manifest_path_for_symbol(market, ticker, db_path, timeframe)
    if path is None or not path.exists():
        return pd.DataFrame()
    if path.suffix.lower() == ".parquet":
        raw = pd.read_parquet(path)
    else:
        raw = pd.read_csv(path)
    frame = normalize_ohlcv_frame(raw)
    if frame.empty:
        return pd.DataFrame()
    return frame.sort_values("date").drop_duplicates("date").reset_index(drop=True)
