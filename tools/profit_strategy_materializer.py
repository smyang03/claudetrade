from __future__ import annotations

"""Materialize point-in-time signals for the bounded profit-strategy handoff.

This process never imports the broker API and never submits an order.  It only
writes a session-scoped signal snapshot consumed by the live bot's separately
locked MICRO order bridge.
"""

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SELECTION_DB = ROOT / "data" / "ticker_selection_log.db"
SECTOR_PAIRS = {
    "SOXX": "091160",
    "XLV": "227550",
    "XLF": "139220",
    "ITA": "309230",
    "LIT": "305720",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def consensus_signals(db_path: Path, *, session_date: str) -> list[dict[str, Any]]:
    """Return latest prior-session US signals whose two strategy labels agree."""

    if not db_path.exists():
        return []
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        latest = connection.execute(
            """
            SELECT MAX(date) FROM ticker_selection_log
            WHERE market='US' AND bot_mode='live' AND signal_fired=1 AND date<?
            """,
            (session_date,),
        ).fetchone()[0]
        if not latest:
            return []
        if (pd.Timestamp(session_date) - pd.Timestamp(str(latest))).days > 4:
            return []
        rows = connection.execute(
            """
            SELECT id,date,ticker,strategy_name,recommended_strategy,
                   selection_rank,entry_priority_score,change_pct
            FROM ticker_selection_log
            WHERE market='US' AND bot_mode='live' AND signal_fired=1 AND date=?
              AND TRIM(COALESCE(strategy_name,''))<>''
              AND LOWER(TRIM(strategy_name))=LOWER(TRIM(recommended_strategy))
            ORDER BY COALESCE(selection_rank,999999),
                     COALESCE(entry_priority_score,-999999) DESC,id DESC
            """,
            (latest,),
        ).fetchall()
    finally:
        connection.close()
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row["ticker"] or "").upper().strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        output.append({
            "strategy_id": "US_CONSENSUS_3D_V1",
            "source_strategy": "us_consensus_3d",
            "market": "US",
            "ticker": ticker,
            "signal_date": str(latest),
            "entry_session_date": session_date,
            "known_at": f"{latest}T23:59:59Z",
            "rank": int(row["selection_rank"] or len(output) + 1),
            "priority": float(row["entry_priority_score"] or 0.0),
            "hold_sessions": 3,
            "weight": 1.0,
            "evidence": {
                "selection_row_id": int(row["id"]),
                "strategy_name": str(row["strategy_name"] or ""),
                "recommended_strategy": str(row["recommended_strategy"] or ""),
                "change_pct": row["change_pct"],
            },
        })
    return output


def _download_sector_closes(symbol: str) -> pd.Series:
    import yfinance as yf

    frame = yf.download(symbol, period="10d", interval="1d", auto_adjust=True, progress=False, threads=False)
    if frame.empty:
        return pd.Series(dtype=float)
    close = frame["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close = pd.to_numeric(close, errors="coerce").dropna()
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    return close[~close.index.duplicated(keep="last")].sort_index()


def sector_pulse_signals(
    *,
    session_date: str,
    close_loader: Callable[[str], pd.Series] = _download_sector_closes,
    threshold_pct: float = 2.0,
) -> list[dict[str, Any]]:
    """Select the strongest completed US sector pulse for the next KR session."""

    cutoff = pd.Timestamp(session_date)
    best: tuple[str, str, pd.Timestamp, float] | None = None
    for leader, target in SECTOR_PAIRS.items():
        closes = close_loader(leader)
        closes = closes[closes.index < cutoff]
        if len(closes) < 2:
            continue
        signal_date = pd.Timestamp(closes.index[-1])
        if (cutoff - signal_date).days > 4:
            continue
        move = (float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1.0) * 100.0
        if move < float(threshold_pct):
            continue
        if best is None or move > best[3]:
            best = (leader, target, signal_date, move)
    if best is None:
        return []
    leader, target, signal_date, move = best
    return [{
        "strategy_id": "KR_US_SECTOR_PULSE_3D_V0",
        "source_strategy": "kr_us_sector_pulse_3d",
        "market": "KR",
        "ticker": target,
        "signal_date": str(signal_date.date()),
        "entry_session_date": session_date,
        "known_at": f"{signal_date.date()}T21:00:00Z",
        "rank": 1,
        "priority": float(move),
        "hold_sessions": 3,
        "weight": 1.0,
        "evidence": {
            "us_leader": leader,
            "leader_return_pct": round(float(move), 6),
            "threshold_pct": float(threshold_pct),
            "signal_provider": "yfinance_close_observation",
            "execution_price_provider": "KIS_ONLY",
        },
    }]


def materialize(*, market: str, session_date: str, output_path: Path) -> dict[str, Any]:
    market_key = str(market or "").upper()
    signals: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        if market_key == "US":
            signals.extend(consensus_signals(SELECTION_DB, session_date=session_date))
        elif market_key == "KR":
            signals.extend(sector_pulse_signals(session_date=session_date))
    except Exception as exc:
        errors.append(str(exc)[:500])
    payload = {
        "schema_version": "profit_strategy_signals_v1",
        "authority": "SIGNAL_ONLY_NO_BROKER_AUTHORITY",
        "market": market_key,
        "session_date": session_date,
        "generated_at": _now(),
        "signals": signals,
        "errors": errors,
        "status": "healthy" if not errors else "degraded",
    }
    _atomic_json(output_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", required=True, choices=("KR", "US"))
    parser.add_argument("--session-date", default=str(date.today()))
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    output = Path(args.output) if args.output else ROOT / "state" / f"profit_strategy_signals_{args.market}.json"
    payload = materialize(market=args.market, session_date=args.session_date, output_path=output)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if not payload["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
