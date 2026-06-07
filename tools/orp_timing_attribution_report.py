from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path


DEFAULT_SELECTION_DB = get_runtime_path("data", "ticker_selection_log.db", make_parents=False)
DEFAULT_INTRADAY_DB = get_runtime_path("data", "intraday_strategy_log.db", make_parents=False)
ORP_STRATEGY = "opening_range_pullback"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        [table],
    ).fetchone()
    return bool(row)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _col(columns: set[str], name: str, default: str = "NULL") -> str:
    return name if name in columns else f"{default} AS {name}"


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def _minutes_between(start: Any, end: Any) -> float | None:
    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end)
    if start_dt is None or end_dt is None:
        return None
    try:
        if start_dt.tzinfo is None and end_dt.tzinfo is not None:
            start_dt = start_dt.replace(tzinfo=end_dt.tzinfo)
        elif start_dt.tzinfo is not None and end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=start_dt.tzinfo)
        return round((end_dt - start_dt).total_seconds() / 60.0, 4)
    except Exception:
        return None


def _is_after_or_equal(start: datetime | None, end: datetime | None) -> bool:
    if start is None or end is None:
        return False
    try:
        if start.tzinfo is None and end.tzinfo is not None:
            start = start.replace(tzinfo=end.tzinfo)
        elif start.tzinfo is not None and end.tzinfo is None:
            end = end.replace(tzinfo=start.tzinfo)
        return end >= start
    except Exception:
        return False


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _percentile(values: list[float], q: float) -> float | None:
    clean = sorted(float(value) for value in values if value is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return round(clean[0], 4)
    pos = (len(clean) - 1) * max(0.0, min(1.0, float(q)))
    low = int(pos)
    high = min(low + 1, len(clean) - 1)
    weight = pos - low
    return round(clean[low] * (1.0 - weight) + clean[high] * weight, 4)


def _market_key(market: str) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


def _ticker_key(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if _market_key(market) == "US" else text.zfill(6) if text.isdigit() else text


def _load_selection_rows(
    db_path: Path,
    *,
    session_date: str,
    market: str,
    runtime_mode: str,
) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = _columns(conn, "ticker_selection_log")
        if not columns:
            return []
        where: list[str] = []
        params: list[Any] = []
        if session_date and "date" in columns:
            where.append("date=?")
            params.append(session_date)
        if market and "market" in columns:
            where.append("market=?")
            params.append(_market_key(market))
        if runtime_mode and "bot_mode" in columns:
            where.append("LOWER(COALESCE(bot_mode, ''))=?")
            params.append(str(runtime_mode or "live").lower())
        strategy_cols = [name for name in ("strategy_name", "recommended_strategy") if name in columns]
        if not strategy_cols and "execution_strategy" in columns:
            strategy_cols = ["execution_strategy"]
        if strategy_cols:
            where.append(
                "("
                + " OR ".join(f"LOWER(COALESCE({name}, ''))=?" for name in strategy_cols)
                + ")"
            )
            params.extend([ORP_STRATEGY] * len(strategy_cols))
        else:
            return []
        where_sql = " AND ".join(where) if where else "1=1"
        order_col = "selected_at" if "selected_at" in columns else "created_at" if "created_at" in columns else "ticker"
        order_sql = f"{order_col}, id" if "id" in columns else order_col
        rows = conn.execute(
            f"""
            SELECT {_col(columns, 'id')},
                   {_col(columns, 'date')},
                   {_col(columns, 'market')},
                   {_col(columns, 'ticker')},
                   {_col(columns, 'source_type', "''")},
                   {_col(columns, 'selected_at', "''")},
                   {_col(columns, 'created_at', "''")},
                   {_col(columns, 'signal_at', "''")},
                   {_col(columns, 'signal_fired', '0')},
                   {_col(columns, 'traded', '0')},
                   {_col(columns, 'trade_ready', '0')},
                   {_col(columns, 'strategy_name', "''")},
                   {_col(columns, 'recommended_strategy', "''")},
                   {_col(columns, 'execution_strategy', "''")},
                   {_col(columns, 'blocked_reason', "''")},
                   {_col(columns, 'bot_mode', "''")}
            FROM ticker_selection_log
            WHERE {where_sql}
            ORDER BY {order_sql}
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _load_intraday_rows(
    db_path: Path,
    *,
    session_date: str,
    market: str,
    runtime_mode: str,
) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = _columns(conn, "intraday_strategy_log")
        if not columns:
            return []
        where: list[str] = []
        params: list[Any] = []
        if session_date and "session_date" in columns:
            where.append("session_date=?")
            params.append(session_date)
        if market and "market" in columns:
            where.append("market=?")
            params.append(_market_key(market))
        if runtime_mode and "bot_mode" in columns:
            where.append("LOWER(COALESCE(bot_mode, ''))=?")
            params.append(str(runtime_mode or "live").lower())
        if "strategy_name" in columns:
            where.append("LOWER(COALESCE(strategy_name, ''))=?")
            params.append(ORP_STRATEGY)
        where_sql = " AND ".join(where) if where else "1=1"
        order_col = "ts" if "ts" in columns else "created_at" if "created_at" in columns else "ticker"
        order_sql = f"{order_col}, id" if "id" in columns else order_col
        rows = conn.execute(
            f"""
            SELECT {_col(columns, 'id')},
                   {_col(columns, 'ts', "''")},
                   {_col(columns, 'created_at', "''")},
                   {_col(columns, 'session_date', "''")},
                   {_col(columns, 'market')},
                   {_col(columns, 'ticker')},
                   {_col(columns, 'strategy_name', "''")},
                   {_col(columns, 'entry_window_elapsed_min')},
                   {_col(columns, 'signal_fired', '0')},
                   {_col(columns, 'traded', '0')},
                   {_col(columns, 'blocked_reason', "''")},
                   {_col(columns, 'note', "''")},
                   {_col(columns, 'bot_mode', "''")}
            FROM intraday_strategy_log
            WHERE {where_sql}
            ORDER BY {order_sql}
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _is_orp_expired(row: dict[str, Any], *, entry_window_min: float) -> bool:
    reason_text = f"{row.get('blocked_reason') or ''} {row.get('note') or ''}".lower()
    if "orp_entry_window_expired" in reason_text:
        return True
    elapsed = _to_float(row.get("entry_window_elapsed_min"))
    return elapsed is not None and elapsed > float(entry_window_min)


def build_report(
    *,
    selection_db: str | Path = DEFAULT_SELECTION_DB,
    intraday_db: str | Path = DEFAULT_INTRADAY_DB,
    session_date: str = "",
    market: str = "KR",
    runtime_mode: str = "live",
    limit: int = 20,
    or_minutes: float | None = None,
    entry_window_min: float = 60.0,
) -> dict[str, Any]:
    market_key = _market_key(market)
    or_min = float(or_minutes if or_minutes is not None else (15.0 if market_key == "US" else 10.0))
    entry_window = float(entry_window_min or 60.0)
    expires_at = or_min + entry_window
    selection_rows = _load_selection_rows(
        Path(selection_db),
        session_date=session_date,
        market=market_key,
        runtime_mode=runtime_mode,
    )
    intraday_rows = _load_intraday_rows(
        Path(intraday_db),
        session_date=session_date,
        market=market_key,
        runtime_mode=runtime_mode,
    )
    intraday_by_ticker: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in intraday_rows:
        ticker = _ticker_key(market_key, row.get("ticker"))
        row_session = str(row.get("session_date") or "").strip()
        if ticker and row_session:
            intraday_by_ticker.setdefault((row_session, ticker), []).append(row)

    joined: list[dict[str, Any]] = []
    signal_delays: list[float] = []
    expired_delays: list[float] = []
    expired_elapsed_values: list[float] = []
    expired_join_count = 0
    expired_after_selected_count = 0
    expired_before_selected_count = 0
    for row in selection_rows:
        ticker = _ticker_key(market_key, row.get("ticker"))
        row_session = str(row.get("date") or session_date or "").strip()
        selected_at = row.get("selected_at") or row.get("created_at")
        signal_at = row.get("signal_at")
        signal_delay = _minutes_between(selected_at, signal_at)
        if signal_delay is not None:
            signal_delays.append(signal_delay)
        expired_rows = [
            intraday
            for intraday in intraday_by_ticker.get((row_session, ticker), [])
            if _is_orp_expired(intraday, entry_window_min=entry_window)
        ]
        selected_dt = _parse_dt(selected_at)
        after_rows: list[dict[str, Any]] = []
        before_rows: list[dict[str, Any]] = []
        for intraday in expired_rows:
            event_at = intraday.get("ts") or intraday.get("created_at")
            event_dt = _parse_dt(event_at)
            if _is_after_or_equal(selected_dt, event_dt):
                after_rows.append(intraday)
            else:
                before_rows.append(intraday)
        expired_row = after_rows[0] if after_rows else expired_rows[0] if expired_rows else {}
        expired_at = expired_row.get("ts") or expired_row.get("created_at") if expired_row else ""
        selected_to_expired = _minutes_between(selected_at, expired_at)
        relation = "no_expired_log"
        if expired_row:
            expired_join_count += 1
            if selected_to_expired is not None:
                expired_delays.append(selected_to_expired)
                if selected_to_expired >= 0:
                    expired_after_selected_count += 1
                    relation = "expired_after_selected"
                else:
                    expired_before_selected_count += 1
                    relation = "expired_before_selected"
            else:
                relation = "expired_joined_time_unknown"
            elapsed = _to_float(expired_row.get("entry_window_elapsed_min"))
            if elapsed is not None:
                expired_elapsed_values.append(elapsed)
        joined.append(
            {
                "ticker": ticker,
                "selection_id": row.get("id"),
                "selected_at": selected_at,
                "signal_at": signal_at,
                "selected_to_signal_delay_min": signal_delay,
                "trade_ready": row.get("trade_ready"),
                "signal_fired": row.get("signal_fired"),
                "traded": row.get("traded"),
                "source_type": row.get("source_type"),
                "selection_recommended_strategy": row.get("recommended_strategy"),
                "signal_strategy": row.get("strategy_name"),
                "selection_strategy": row.get("recommended_strategy") or row.get("strategy_name") or row.get("execution_strategy"),
                "execution_strategy": row.get("execution_strategy"),
                "orp_entry_window_expires_at_min": expires_at,
                "orp_expired_at": expired_at,
                "selected_to_orp_expired_delay_min": selected_to_expired,
                "expired_entry_window_elapsed_min": _to_float(expired_row.get("entry_window_elapsed_min")) if expired_row else None,
                "expired_reason": expired_row.get("blocked_reason") or expired_row.get("note") if expired_row else "",
                "expiry_relation": relation,
            }
        )

    return {
        "schema": "orp_timing_attribution.v1",
        "selection_db": str(selection_db),
        "intraday_db": str(intraday_db),
        "session_date": session_date,
        "market": market_key,
        "runtime_mode": str(runtime_mode or "live").lower(),
        "or_minutes": or_min,
        "entry_window_min": entry_window,
        "entry_window_expires_at_min": expires_at,
        "selection_rows": len(selection_rows),
        "intraday_orp_rows": len(intraday_rows),
        "expired_join_rows": expired_join_count,
        "expired_after_selected_count": expired_after_selected_count,
        "expired_before_selected_count": expired_before_selected_count,
        "selected_to_signal_delay_min": {
            "count": len(signal_delays),
            "p50": _percentile(signal_delays, 0.50),
            "p90": _percentile(signal_delays, 0.90),
            "max": round(max(signal_delays), 4) if signal_delays else None,
        },
        "selected_to_orp_expired_delay_min": {
            "count": len(expired_delays),
            "p50": _percentile(expired_delays, 0.50),
            "p90": _percentile(expired_delays, 0.90),
            "max": round(max(expired_delays), 4) if expired_delays else None,
        },
        "expired_entry_window_elapsed_min": {
            "count": len(expired_elapsed_values),
            "p50": _percentile(expired_elapsed_values, 0.50),
            "p90": _percentile(expired_elapsed_values, 0.90),
            "max": round(max(expired_elapsed_values), 4) if expired_elapsed_values else None,
        },
        "interpretation": (
            "orp_window_timing_directly_relevant"
            if expired_join_count and (_percentile(signal_delays, 0.90) or 0.0) >= expires_at
            else "orp_window_timing_needs_more_joined_samples"
        ),
        "samples": joined[: max(0, int(limit or 0))],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only ORP timing attribution report.")
    parser.add_argument("--selection-db", default=str(DEFAULT_SELECTION_DB))
    parser.add_argument("--intraday-db", default=str(DEFAULT_INTRADAY_DB))
    parser.add_argument("--date", default="")
    parser.add_argument("--market", default="KR")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--or-minutes", type=float, default=None)
    parser.add_argument("--entry-window-min", type=float, default=60.0)
    args = parser.parse_args()
    report = build_report(
        selection_db=args.selection_db,
        intraday_db=args.intraday_db,
        session_date=args.date,
        market=args.market,
        runtime_mode=args.runtime_mode,
        limit=args.limit,
        or_minutes=args.or_minutes,
        entry_window_min=args.entry_window_min,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
