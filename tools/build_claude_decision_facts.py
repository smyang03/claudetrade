from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_FACT_DB = ROOT / "data" / "ml" / "claude_decision_facts.db"
DEFAULT_CANDIDATE_AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
DEFAULT_SELECTION_DB = ROOT / "data" / "ticker_selection_log.db"
DEFAULT_ML_DB = ROOT / "data" / "ml" / "decisions.db"
DEFAULT_EVENT_DB = ROOT / "data" / "v2_event_store.db"


FACT_SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_selection (
    selection_key TEXT PRIMARY KEY,
    runtime_mode TEXT NOT NULL,
    session_date TEXT NOT NULL,
    market TEXT NOT NULL,
    ticker TEXT NOT NULL,
    candidate_key TEXT,
    call_id TEXT,
    known_at TEXT,
    source TEXT NOT NULL,
    source_file TEXT,
    dedupe_key TEXT NOT NULL,
    latest_rank INTEGER,

    prompt_included INTEGER NOT NULL DEFAULT 0,
    final_prompt_included INTEGER,
    input_to_claude_reported INTEGER NOT NULL DEFAULT 0,
    prompt_rank INTEGER,
    raw_rank INTEGER,
    trainer_score_rank INTEGER,
    prompt_excluded_reason TEXT,
    classification TEXT,

    raw_action TEXT,
    normalized_action TEXT,
    final_action TEXT,
    route_route TEXT,
    route_reason TEXT,
    route_demoted_to TEXT,
    route_runtime_gate_reason TEXT,

    claude_watchlist INTEGER NOT NULL DEFAULT 0,
    claude_trade_ready INTEGER NOT NULL DEFAULT 0,
    trade_ready INTEGER NOT NULL DEFAULT 0,
    selected_reason TEXT,
    veto_reason TEXT,
    claude_reason TEXT,
    claude_veto_reason TEXT,

    recommended_strategy TEXT,
    risk_tags_json TEXT NOT NULL DEFAULT '[]',
    hard_blocks_json TEXT NOT NULL DEFAULT '[]',
    soft_gates_json TEXT NOT NULL DEFAULT '[]',
    data_quality_flags_json TEXT NOT NULL DEFAULT '[]',
    data_quality TEXT,
    evidence_data_state TEXT,
    trainer_candidate_state TEXT,
    liquidity_bucket TEXT,
    market_type TEXT,
    primary_bucket TEXT,
    change_pct REAL,
    gap_pct REAL,
    from_high_pct REAL,
    volume_ratio REAL,
    turnover REAL,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_quality TEXT NOT NULL DEFAULT 'unknown',
    source_refs_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_fact_selection_session
    ON fact_selection(runtime_mode, market, session_date, ticker);
CREATE INDEX IF NOT EXISTS idx_fact_selection_action
    ON fact_selection(market, session_date, final_action, classification);
CREATE INDEX IF NOT EXISTS idx_fact_selection_dedupe
    ON fact_selection(runtime_mode, market, session_date, ticker, dedupe_key);

CREATE TABLE IF NOT EXISTS fact_forward_outcome (
    selection_key TEXT PRIMARY KEY,
    runtime_mode TEXT NOT NULL,
    session_date TEXT NOT NULL,
    market TEXT NOT NULL,
    ticker TEXT NOT NULL,

    forward_30m_pct REAL,
    forward_60m_pct REAL,
    forward_1d_pct REAL,
    forward_3d_pct REAL,
    forward_5d_pct REAL,
    max_runup_3d_pct REAL,
    max_drawdown_3d_pct REAL,
    max_runup_5d_pct REAL,
    max_drawdown_5d_pct REAL,

    outcome_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    outcome_source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_quality TEXT NOT NULL DEFAULT 'unknown',
    source_refs_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_fact_forward_session
    ON fact_forward_outcome(runtime_mode, market, session_date, ticker);

CREATE TABLE IF NOT EXISTS fact_execution (
    execution_key TEXT PRIMARY KEY,
    selection_key TEXT,
    runtime_mode TEXT NOT NULL,
    session_date TEXT NOT NULL,
    market TEXT NOT NULL,
    ticker TEXT NOT NULL,

    v2_decision_id TEXT,
    execution_decision_id TEXT,
    legacy_decision_id INTEGER,
    canonical_key TEXT,
    path_type TEXT,
    path_run_id TEXT,
    strategy TEXT,
    origin_action TEXT,

    filled INTEGER NOT NULL DEFAULT 0,
    closed INTEGER NOT NULL DEFAULT 0,
    first_fill_event_id INTEGER,
    first_close_event_id INTEGER,
    last_close_event_id INTEGER,
    earliest_fill_at TEXT,
    first_closed_at TEXT,
    last_closed_at TEXT,
    entry_price REAL,
    exit_price REAL,
    pnl_pct REAL,
    mfe_pct REAL,
    mae_pct REAL,
    close_reason TEXT,
    quality_grade TEXT,
    learning_allowed INTEGER NOT NULL DEFAULT 0,

    match_quality TEXT NOT NULL DEFAULT 'unknown',
    execution_link_source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_quality TEXT NOT NULL DEFAULT 'unknown',
    source_refs_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_fact_execution_session
    ON fact_execution(runtime_mode, market, session_date, ticker);
CREATE INDEX IF NOT EXISTS idx_fact_execution_match
    ON fact_execution(match_quality, source_quality, session_date);

CREATE TABLE IF NOT EXISTS fact_build_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL,
    runtime_mode TEXT NOT NULL,
    market TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    dry_run INTEGER NOT NULL DEFAULT 0,
    params_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(data: Any) -> str:
    return json.dumps(data if data is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def _as_bool_int(value: Any) -> int:
    if isinstance(value, str):
        return 1 if value.strip().lower() in {"1", "true", "yes", "y"} else 0
    return 1 if bool(value) else 0


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _market(value: str) -> str:
    key = str(value or "").strip().upper()
    return key if key in {"KR", "US", "ALL"} else key


def _ticker_key(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


def _dedupe_key(runtime_mode: str, market: str, session_date: str, ticker: str) -> str:
    return ":".join([str(runtime_mode or ""), str(market or "").upper(), str(session_date or ""), _ticker_key(market, ticker)])


def _normalize_action(action: Any, *, trade_ready: Any = None, classification: Any = None) -> str:
    key = str(action or "").strip().upper()
    if key:
        return key
    if _as_bool_int(trade_ready):
        return "TRADE_READY"
    if str(classification or "").strip().lower() == "watch_only":
        return "WATCH"
    if trade_ready is not None:
        return "WATCH"
    return "UNKNOWN"


def _json_array_text(value: Any) -> str:
    if value in (None, ""):
        return "[]"
    if isinstance(value, (list, tuple, set)):
        return _json(list(value))
    raw = str(value).strip()
    if not raw:
        return "[]"
    try:
        decoded = json.loads(raw)
    except Exception:
        return _json([raw])
    if isinstance(decoded, list):
        return _json(decoded)
    if decoded in (None, ""):
        return "[]"
    return _json([decoded])


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _readonly_uri(path: Path) -> str:
    return f"file:{path.resolve().as_posix()}?mode=ro"


def _connect_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_readonly_uri(path), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_writable(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(db_path: str | Path = DEFAULT_FACT_DB) -> None:
    with closing(_connect_writable(Path(db_path))) as conn:
        conn.executescript(FACT_SCHEMA)
        _ensure_fact_schema(conn)
        conn.commit()


def _ensure_fact_schema(conn: sqlite3.Connection) -> None:
    extras = {
        "fact_execution": {
            "first_fill_event_id": "INTEGER",
            "first_close_event_id": "INTEGER",
            "last_close_event_id": "INTEGER",
        },
    }
    for table, columns in extras.items():
        existing = _columns(conn, table)
        if not existing:
            continue
        for name, sql_type in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _expr(columns: set[str], name: str, default_sql: str = "NULL") -> str:
    return name if name in columns else f"{default_sql} AS {name}"


def _date_where(alias: str, field: str, start_date: str, end_date: str, market: str, runtime_mode: str) -> tuple[str, list[Any]]:
    prefix = f"{alias}." if alias else ""
    where: list[str] = []
    params: list[Any] = []
    if start_date:
        where.append(f"{prefix}{field}>=?")
        params.append(start_date)
    if end_date:
        where.append(f"{prefix}{field}<=?")
        params.append(end_date)
    if market and market != "ALL":
        where.append(f"{prefix}market=?")
        params.append(market)
    if runtime_mode:
        mode_field = "runtime_mode" if field == "session_date" else "bot_mode"
        where.append(f"{prefix}{mode_field}=?")
        params.append(runtime_mode)
    return (" AND ".join(where) if where else "1=1"), params


def _source_conn(path: Path, source_name: str, summary: dict[str, Any]) -> sqlite3.Connection | None:
    if not path.exists():
        summary["missing_sources"].append(source_name)
        return None
    try:
        return _connect_readonly(path)
    except sqlite3.Error as exc:
        summary["source_errors"][source_name] = str(exc)
        return None


def _load_audit_rows(
    conn: sqlite3.Connection | None,
    *,
    start_date: str,
    end_date: str,
    market: str,
    runtime_mode: str,
) -> list[dict[str, Any]]:
    if conn is None or not _table_exists(conn, "audit_candidate_rows"):
        return []
    columns = _columns(conn, "audit_candidate_rows")
    select_cols = [
        _expr(columns, "candidate_key", "''"),
        _expr(columns, "call_id", "''"),
        _expr(columns, "runtime_mode", "''"),
        _expr(columns, "market", "''"),
        _expr(columns, "session_date", "''"),
        _expr(columns, "known_at", "''"),
        _expr(columns, "ticker", "''"),
        _expr(columns, "source_file", "''"),
        _expr(columns, "prompt_rank"),
        _expr(columns, "in_prompt", "0"),
        _expr(columns, "input_to_claude_reported", "0"),
        _expr(columns, "final_prompt_included"),
        _expr(columns, "raw_rank"),
        _expr(columns, "trainer_score_rank"),
        _expr(columns, "prompt_excluded_reason", "''"),
        _expr(columns, "classification", "''"),
        _expr(columns, "claude_action", "''"),
        _expr(columns, "claude_reason", "''"),
        _expr(columns, "claude_veto_reason", "''"),
        _expr(columns, "claude_watchlist", "0"),
        _expr(columns, "claude_trade_ready", "0"),
        _expr(columns, "recommended_strategy", "''"),
        _expr(columns, "risk_tags_json", "'[]'"),
        _expr(columns, "hard_blocks", "'[]'"),
        _expr(columns, "soft_gates", "'[]'"),
        _expr(columns, "data_quality_flags_json", "'[]'"),
        _expr(columns, "data_quality", "''"),
        _expr(columns, "evidence_data_state", "''"),
        _expr(columns, "trainer_candidate_state", "''"),
        _expr(columns, "route_original_action", "''"),
        _expr(columns, "route_final_action", "''"),
        _expr(columns, "route_route", "''"),
        _expr(columns, "route_reason", "''"),
        _expr(columns, "route_demoted_to", "''"),
        _expr(columns, "route_runtime_gate_reason", "''"),
        _expr(columns, "liquidity_bucket", "''"),
        _expr(columns, "market_type", "''"),
        _expr(columns, "primary_bucket", "''"),
        _expr(columns, "change_pct"),
        _expr(columns, "gap_pct"),
        _expr(columns, "from_high_pct"),
        _expr(columns, "volume_ratio"),
        _expr(columns, "turnover"),
        _expr(columns, "execution_decision_id", "''"),
        _expr(columns, "execution_link_source", "''"),
        _expr(columns, "payload_json", "'{}'"),
        _expr(columns, "created_at", "''"),
        _expr(columns, "updated_at", "''"),
    ]
    where, params = _date_where("", "session_date", start_date, end_date, market, runtime_mode)
    sql = f"""
        SELECT {", ".join(select_cols)}
        FROM audit_candidate_rows
        WHERE {where}
        ORDER BY session_date, market, ticker, COALESCE(NULLIF(known_at, ''), updated_at, created_at), candidate_key
    """
    rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    _assign_latest_rank(rows)
    return rows


def _assign_latest_rank(rows: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("runtime_mode") or ""),
            str(row.get("market") or "").upper(),
            str(row.get("session_date") or ""),
            _ticker_key(str(row.get("market") or ""), row.get("ticker")),
        )
        groups[key].append(row)
    for items in groups.values():
        sorted_items = sorted(
            items,
            key=lambda row: (
                str(row.get("known_at") or row.get("updated_at") or row.get("created_at") or ""),
                str(row.get("updated_at") or ""),
                str(row.get("candidate_key") or ""),
            ),
            reverse=True,
        )
        for index, row in enumerate(sorted_items, start=1):
            row["latest_rank"] = index


def _load_selection_rows(
    conn: sqlite3.Connection | None,
    *,
    start_date: str,
    end_date: str,
    market: str,
    runtime_mode: str,
) -> list[dict[str, Any]]:
    if conn is None or not _table_exists(conn, "ticker_selection_log"):
        return []
    columns = _columns(conn, "ticker_selection_log")
    select_cols = [
        _expr(columns, "id"),
        _expr(columns, "bot_mode", "''"),
        _expr(columns, "date", "''"),
        _expr(columns, "market", "''"),
        _expr(columns, "ticker", "''"),
        _expr(columns, "consensus_mode", "''"),
        _expr(columns, "selection_rank"),
        _expr(columns, "watchlist_rank"),
        _expr(columns, "source_type", "''"),
        _expr(columns, "selected_reason", "''"),
        _expr(columns, "veto_reason", "''"),
        _expr(columns, "selected_at", "''"),
        _expr(columns, "change_pct"),
        _expr(columns, "vol_ratio"),
        _expr(columns, "gap_pct"),
        _expr(columns, "from_high_pct"),
        _expr(columns, "market_type", "''"),
        _expr(columns, "liquidity_bucket", "''"),
        _expr(columns, "from_high_bucket", "''"),
        _expr(columns, "trade_ready", "0"),
        _expr(columns, "risk_tags", "'[]'"),
        _expr(columns, "recommended_strategy", "''"),
        _expr(columns, "execution_decision_id", "''"),
        _expr(columns, "execution_source_type", "''"),
        _expr(columns, "execution_strategy", "''"),
        _expr(columns, "execution_reason", "''"),
        _expr(columns, "pnl_pct"),
        _expr(columns, "exit_reason", "''"),
        _expr(columns, "forward_1d"),
        _expr(columns, "forward_3d"),
        _expr(columns, "forward_5d"),
        _expr(columns, "max_runup_3d"),
        _expr(columns, "max_drawdown_3d"),
        _expr(columns, "max_runup_5d"),
        _expr(columns, "max_drawdown_5d"),
        _expr(columns, "created_at", "''"),
    ]
    where, params = _date_where("", "date", start_date, end_date, market, runtime_mode)
    sql = f"""
        SELECT {", ".join(select_cols)}
        FROM ticker_selection_log
        WHERE {where}
        ORDER BY date, market, ticker, id
    """
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _load_audit_outcomes(conn: sqlite3.Connection | None, candidate_keys: set[str]) -> dict[str, dict[int, dict[str, Any]]]:
    if conn is None or not candidate_keys or not _table_exists(conn, "audit_candidate_outcomes"):
        return {}
    out: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    keys = sorted(candidate_keys)
    chunk_size = 900
    for index in range(0, len(keys), chunk_size):
        chunk = keys[index : index + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT candidate_key, horizon_min, return_pct, max_runup_pct, max_drawdown_pct,
                   status, source, observed_at, observed_price, payload_json
            FROM audit_candidate_outcomes
            WHERE candidate_key IN ({placeholders})
            """,
            chunk,
        ).fetchall()
        for row in rows:
            out[str(row["candidate_key"])][int(row["horizon_min"])] = dict(row)
    return out


def _load_canonical_rows(
    conn: sqlite3.Connection | None,
    *,
    start_date: str,
    end_date: str,
    market: str,
    runtime_mode: str,
) -> list[dict[str, Any]]:
    if conn is None or not _table_exists(conn, "v2_canonical_performance"):
        return []
    columns = _columns(conn, "v2_canonical_performance")
    select_cols = [
        _expr(columns, "v2_decision_id", "''"),
        _expr(columns, "canonical_key", "''"),
        _expr(columns, "market", "''"),
        _expr(columns, "runtime_mode", "''"),
        _expr(columns, "session_date", "''"),
        _expr(columns, "ticker", "''"),
        _expr(columns, "status", "''"),
        _expr(columns, "route", "''"),
        _expr(columns, "path_type", "''"),
        _expr(columns, "path_run_id", "''"),
        _expr(columns, "strategy", "''"),
        _expr(columns, "origin_action", "''"),
        _expr(columns, "filled", "0"),
        _expr(columns, "closed", "0"),
        _expr(columns, "first_fill_event_id"),
        _expr(columns, "first_close_event_id"),
        _expr(columns, "last_close_event_id"),
        _expr(columns, "earliest_fill_at", "''"),
        _expr(columns, "first_closed_at", "''"),
        _expr(columns, "last_closed_at", "''"),
        _expr(columns, "entry_price"),
        _expr(columns, "first_exit_price"),
        _expr(columns, "last_exit_price"),
        _expr(columns, "pnl_pct"),
        _expr(columns, "mfe_pct"),
        _expr(columns, "mae_pct"),
        _expr(columns, "quality_grade", "''"),
        _expr(columns, "learning_allowed", "0"),
    ]
    where, params = _date_where("", "session_date", start_date, end_date, market, runtime_mode)
    sql = f"""
        SELECT {", ".join(select_cols)}
        FROM v2_canonical_performance
        WHERE {where}
        ORDER BY session_date, market, ticker, v2_decision_id
    """
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _load_fill_links(conn: sqlite3.Connection | None) -> dict[int, str]:
    if conn is None or not _table_exists(conn, "v2_decision_fill_links"):
        return {}
    columns = _columns(conn, "v2_decision_fill_links")
    if "legacy_decision_id" not in columns or "v2_decision_id" not in columns:
        return {}
    rows = conn.execute(
        """
        SELECT legacy_decision_id, v2_decision_id
        FROM v2_decision_fill_links
        WHERE legacy_decision_id IS NOT NULL
        """
    ).fetchall()
    return {int(row["legacy_decision_id"]): str(row["v2_decision_id"]) for row in rows}


def _load_event_path_runs(
    conn: sqlite3.Connection | None,
    *,
    start_date: str,
    end_date: str,
    market: str,
    runtime_mode: str,
) -> dict[str, dict[str, Any]]:
    if conn is None or not _table_exists(conn, "v2_path_runs"):
        return {}
    where, params = _date_where("", "session_date", start_date, end_date, market, runtime_mode)
    rows = conn.execute(
        f"""
        SELECT path_run_id, decision_id, path_type, market, runtime_mode, session_date, ticker, status, plan_json
        FROM v2_path_runs
        WHERE {where}
        """,
        params,
    ).fetchall()
    return {str(row["path_run_id"]): dict(row) for row in rows if str(row["path_run_id"] or "")}


def _selection_log_lookup(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("bot_mode") or ""),
            str(row.get("market") or "").upper(),
            str(row.get("date") or ""),
            _ticker_key(str(row.get("market") or ""), row.get("ticker")),
        )
        grouped[key].append(row)
    return {key: sorted(items, key=lambda row: int(row.get("id") or 0), reverse=True)[0] for key, items in grouped.items()}


def _audit_selection_fact(row: dict[str, Any], now: str) -> dict[str, Any]:
    market = str(row.get("market") or "").upper()
    ticker = _ticker_key(market, row.get("ticker"))
    runtime_mode = str(row.get("runtime_mode") or "")
    session_date = str(row.get("session_date") or "")
    route_final = str(row.get("route_final_action") or "").strip().upper()
    raw_action = str(row.get("claude_action") or row.get("route_original_action") or "").strip().upper()
    final_action = _normalize_action(route_final or raw_action, trade_ready=row.get("claude_trade_ready"), classification=row.get("classification"))
    candidate_key = str(row.get("candidate_key") or "")
    source_refs = {
        "candidate_key": candidate_key,
        "call_id": row.get("call_id") or "",
        "source_db": "candidate_audit_db",
        "source_table": "audit_candidate_rows",
        "execution_decision_id": row.get("execution_decision_id") or "",
        "execution_link_source": row.get("execution_link_source") or "",
        "path_run_id": _extract_path_run_id(row),
    }
    return {
        "selection_key": f"audit:{candidate_key}" if candidate_key else f"fallback:{runtime_mode}:{market}:{session_date}:{ticker}:{row.get('known_at') or ''}",
        "runtime_mode": runtime_mode,
        "session_date": session_date,
        "market": market,
        "ticker": ticker,
        "candidate_key": candidate_key,
        "call_id": row.get("call_id") or "",
        "known_at": row.get("known_at") or "",
        "source": "audit_candidate_rows",
        "source_file": row.get("source_file") or "",
        "dedupe_key": _dedupe_key(runtime_mode, market, session_date, ticker),
        "latest_rank": _as_int(row.get("latest_rank")),
        "prompt_included": _as_bool_int(row.get("in_prompt")),
        "final_prompt_included": _as_int(row.get("final_prompt_included")),
        "input_to_claude_reported": _as_bool_int(row.get("input_to_claude_reported")),
        "prompt_rank": _as_int(row.get("prompt_rank")),
        "raw_rank": _as_int(row.get("raw_rank")),
        "trainer_score_rank": _as_int(row.get("trainer_score_rank")),
        "prompt_excluded_reason": row.get("prompt_excluded_reason") or "",
        "classification": row.get("classification") or "",
        "raw_action": raw_action,
        "normalized_action": _normalize_action(raw_action, trade_ready=row.get("claude_trade_ready"), classification=row.get("classification")),
        "final_action": final_action,
        "route_route": row.get("route_route") or "",
        "route_reason": row.get("route_reason") or "",
        "route_demoted_to": row.get("route_demoted_to") or "",
        "route_runtime_gate_reason": row.get("route_runtime_gate_reason") or "",
        "claude_watchlist": _as_bool_int(row.get("claude_watchlist")),
        "claude_trade_ready": _as_bool_int(row.get("claude_trade_ready")),
        "trade_ready": _as_bool_int(row.get("claude_trade_ready")),
        "selected_reason": "",
        "veto_reason": "",
        "claude_reason": row.get("claude_reason") or "",
        "claude_veto_reason": row.get("claude_veto_reason") or "",
        "recommended_strategy": row.get("recommended_strategy") or "",
        "risk_tags_json": _json_array_text(row.get("risk_tags_json")),
        "hard_blocks_json": _json_array_text(row.get("hard_blocks")),
        "soft_gates_json": _json_array_text(row.get("soft_gates")),
        "data_quality_flags_json": _json_array_text(row.get("data_quality_flags_json")),
        "data_quality": row.get("data_quality") or "",
        "evidence_data_state": row.get("evidence_data_state") or "",
        "trainer_candidate_state": row.get("trainer_candidate_state") or "",
        "liquidity_bucket": row.get("liquidity_bucket") or "",
        "market_type": row.get("market_type") or "",
        "primary_bucket": row.get("primary_bucket") or "",
        "change_pct": _as_float(row.get("change_pct")),
        "gap_pct": _as_float(row.get("gap_pct")),
        "from_high_pct": _as_float(row.get("from_high_pct")),
        "volume_ratio": _as_float(row.get("volume_ratio")),
        "turnover": _as_float(row.get("turnover")),
        "created_at": now,
        "updated_at": now,
        "source_quality": "partial",
        "source_refs_json": _json(source_refs),
    }


def _selection_log_fact(row: dict[str, Any], now: str) -> dict[str, Any]:
    market = str(row.get("market") or "").upper()
    ticker = _ticker_key(market, row.get("ticker"))
    runtime_mode = str(row.get("bot_mode") or "")
    session_date = str(row.get("date") or "")
    trade_ready = _as_bool_int(row.get("trade_ready"))
    action = "TRADE_READY" if trade_ready else "WATCH"
    source_refs = {
        "ticker_selection_log_id": row.get("id"),
        "source_db": "selection_db",
        "source_table": "ticker_selection_log",
        "execution_decision_id": row.get("execution_decision_id") or "",
    }
    return {
        "selection_key": f"selection_log:{int(row.get('id') or 0)}",
        "runtime_mode": runtime_mode,
        "session_date": session_date,
        "market": market,
        "ticker": ticker,
        "candidate_key": "",
        "call_id": "",
        "known_at": row.get("selected_at") or row.get("created_at") or "",
        "source": "ticker_selection_log",
        "source_file": "",
        "dedupe_key": _dedupe_key(runtime_mode, market, session_date, ticker),
        "latest_rank": 1,
        "prompt_included": 0,
        "final_prompt_included": None,
        "input_to_claude_reported": 0,
        "prompt_rank": _as_int(row.get("selection_rank")),
        "raw_rank": None,
        "trainer_score_rank": None,
        "prompt_excluded_reason": "",
        "classification": "trade_ready" if trade_ready else "watch_only",
        "raw_action": action,
        "normalized_action": action,
        "final_action": action,
        "route_route": "",
        "route_reason": "",
        "route_demoted_to": "",
        "route_runtime_gate_reason": "",
        "claude_watchlist": 1,
        "claude_trade_ready": trade_ready,
        "trade_ready": trade_ready,
        "selected_reason": row.get("selected_reason") or "",
        "veto_reason": row.get("veto_reason") or "",
        "claude_reason": "",
        "claude_veto_reason": "",
        "recommended_strategy": row.get("recommended_strategy") or "",
        "risk_tags_json": _json_array_text(row.get("risk_tags")),
        "hard_blocks_json": "[]",
        "soft_gates_json": "[]",
        "data_quality_flags_json": "[]",
        "data_quality": "",
        "evidence_data_state": "",
        "trainer_candidate_state": "",
        "liquidity_bucket": row.get("liquidity_bucket") or "",
        "market_type": row.get("market_type") or "",
        "primary_bucket": row.get("from_high_bucket") or "",
        "change_pct": _as_float(row.get("change_pct")),
        "gap_pct": _as_float(row.get("gap_pct")),
        "from_high_pct": _as_float(row.get("from_high_pct")),
        "volume_ratio": _as_float(row.get("vol_ratio")),
        "turnover": None,
        "created_at": now,
        "updated_at": now,
        "source_quality": "partial",
        "source_refs_json": _json(source_refs),
    }


def _extract_path_run_id(row: dict[str, Any]) -> str:
    payload = _json_object(row.get("payload_json"))
    for key in ("path_run_id", "pathb_path_run_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _source_refs(row: dict[str, Any]) -> dict[str, Any]:
    return _json_object(row.get("source_refs_json"))


def _build_outcome_fact(
    selection: dict[str, Any],
    audit_outcomes: dict[str, dict[int, dict[str, Any]]],
    selection_lookup: dict[tuple[str, str, str, str], dict[str, Any]],
    now: str,
) -> dict[str, Any]:
    refs = _source_refs(selection)
    candidate_key = str(selection.get("candidate_key") or "")
    horizons = audit_outcomes.get(candidate_key, {}) if candidate_key else {}
    source_refs: dict[str, Any] = {"selection_key": selection["selection_key"]}
    outcome = {
        "selection_key": selection["selection_key"],
        "runtime_mode": selection["runtime_mode"],
        "session_date": selection["session_date"],
        "market": selection["market"],
        "ticker": selection["ticker"],
        "forward_30m_pct": None,
        "forward_60m_pct": None,
        "forward_1d_pct": None,
        "forward_3d_pct": None,
        "forward_5d_pct": None,
        "max_runup_3d_pct": None,
        "max_drawdown_3d_pct": None,
        "max_runup_5d_pct": None,
        "max_drawdown_5d_pct": None,
        "outcome_status": "MISSING",
        "outcome_source": "",
        "created_at": now,
        "updated_at": now,
        "source_quality": "missing_outcome",
        "source_refs_json": "{}",
    }
    if horizons:
        outcome.update(
            {
                "forward_30m_pct": _as_float((horizons.get(30) or {}).get("return_pct")),
                "forward_60m_pct": _as_float((horizons.get(60) or {}).get("return_pct")),
                "forward_1d_pct": _as_float((horizons.get(1440) or {}).get("return_pct")),
                "forward_3d_pct": _as_float((horizons.get(4320) or {}).get("return_pct")),
                "forward_5d_pct": _as_float((horizons.get(7200) or {}).get("return_pct")),
                "max_runup_3d_pct": _as_float((horizons.get(4320) or {}).get("max_runup_pct")),
                "max_drawdown_3d_pct": _as_float((horizons.get(4320) or {}).get("max_drawdown_pct")),
                "max_runup_5d_pct": _as_float((horizons.get(7200) or {}).get("max_runup_pct")),
                "max_drawdown_5d_pct": _as_float((horizons.get(7200) or {}).get("max_drawdown_pct")),
                "outcome_status": "OK",
                "outcome_source": "audit_candidate_outcomes",
                "source_quality": "partial",
            }
        )
        source_refs.update({"candidate_key": candidate_key, "horizons": sorted(horizons)})
        outcome["source_refs_json"] = _json(source_refs)
        return outcome

    selection_log_id = refs.get("ticker_selection_log_id")
    selection_row = None
    if selection_log_id:
        for row in selection_lookup.values():
            if int(row.get("id") or 0) == int(selection_log_id or 0):
                selection_row = row
                break
    if selection_row is None:
        lookup_key = (
            str(selection.get("runtime_mode") or ""),
            str(selection.get("market") or "").upper(),
            str(selection.get("session_date") or ""),
            _ticker_key(str(selection.get("market") or ""), selection.get("ticker")),
        )
        selection_row = selection_lookup.get(lookup_key)
    if selection_row is not None:
        values = {
            "forward_1d_pct": _as_float(selection_row.get("forward_1d")),
            "forward_3d_pct": _as_float(selection_row.get("forward_3d")),
            "forward_5d_pct": _as_float(selection_row.get("forward_5d")),
            "max_runup_3d_pct": _as_float(selection_row.get("max_runup_3d")),
            "max_drawdown_3d_pct": _as_float(selection_row.get("max_drawdown_3d")),
            "max_runup_5d_pct": _as_float(selection_row.get("max_runup_5d")),
            "max_drawdown_5d_pct": _as_float(selection_row.get("max_drawdown_5d")),
        }
        if any(value is not None for value in values.values()):
            outcome.update(values)
            outcome.update(
                {
                    "outcome_status": "OK",
                    "outcome_source": "ticker_selection_log",
                    "source_quality": "partial",
                }
            )
            source_refs.update({"ticker_selection_log_id": selection_row.get("id")})
    outcome["source_refs_json"] = _json(source_refs)
    return outcome


def _canonical_indexes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_decision = {str(row.get("v2_decision_id") or ""): row for row in rows if str(row.get("v2_decision_id") or "")}
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_session: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        path_run_id = str(row.get("path_run_id") or "")
        if path_run_id:
            by_path[path_run_id].append(row)
        key = (
            str(row.get("runtime_mode") or ""),
            str(row.get("market") or "").upper(),
            str(row.get("session_date") or ""),
            _ticker_key(str(row.get("market") or ""), row.get("ticker")),
        )
        by_session[key].append(row)
    return {"by_decision": by_decision, "by_path": by_path, "by_session": by_session}


def _build_execution_fact(
    selection: dict[str, Any],
    selection_lookup: dict[tuple[str, str, str, str], dict[str, Any]],
    canonical_index: dict[str, Any],
    fill_links: dict[int, str],
    path_runs: dict[str, dict[str, Any]],
    now: str,
) -> dict[str, Any]:
    refs = _source_refs(selection)
    direct_decision = str(refs.get("execution_decision_id") or "").strip()
    if not direct_decision:
        lookup_key = (
            str(selection.get("runtime_mode") or ""),
            str(selection.get("market") or "").upper(),
            str(selection.get("session_date") or ""),
            _ticker_key(str(selection.get("market") or ""), selection.get("ticker")),
        )
        direct_decision = str((selection_lookup.get(lookup_key) or {}).get("execution_decision_id") or "").strip()
    path_run_id = str(refs.get("path_run_id") or "").strip()
    legacy_id = _as_int(refs.get("legacy_decision_id"))
    has_explicit_execution_key = bool(direct_decision or path_run_id or legacy_id is not None)

    canonical = None
    match_quality = "missing_execution"
    source_quality = "missing_execution"
    if direct_decision:
        canonical = canonical_index["by_decision"].get(direct_decision)
        match_quality = "direct_decision_id" if canonical else "decision_id_not_found"
    if canonical is None and path_run_id:
        path_matches = list(canonical_index["by_path"].get(path_run_id) or [])
        if not path_matches and path_run_id in path_runs:
            decision_from_path = str(path_runs[path_run_id].get("decision_id") or "")
            if decision_from_path:
                found = canonical_index["by_decision"].get(decision_from_path)
                path_matches = [found] if found else []
        if len(path_matches) == 1:
            canonical = path_matches[0]
            match_quality = "path_run_id"
        elif len(path_matches) > 1:
            match_quality = "ambiguous_path_run_id"
            source_quality = "ambiguous_match"
        elif not direct_decision:
            match_quality = "path_run_id_not_found"
    if canonical is None and legacy_id is not None and legacy_id in fill_links:
        canonical = canonical_index["by_decision"].get(fill_links[legacy_id])
        match_quality = "legacy_decision_id" if canonical else "legacy_decision_id_not_found"
    if canonical is None and not has_explicit_execution_key and match_quality == "missing_execution":
        session_key = (
            str(selection.get("runtime_mode") or ""),
            str(selection.get("market") or "").upper(),
            str(selection.get("session_date") or ""),
            _ticker_key(str(selection.get("market") or ""), selection.get("ticker")),
        )
        matches = list(canonical_index["by_session"].get(session_key) or [])
        if len(matches) == 1:
            canonical = matches[0]
            match_quality = "session_ticker_unique"
        elif len(matches) > 1:
            match_quality = "ambiguous_session_ticker"
            source_quality = "ambiguous_match"

    if canonical is not None:
        source_quality = "complete"
    source_refs = {
        "selection_key": selection["selection_key"],
        "match_quality": match_quality,
        "candidate_key": selection.get("candidate_key") or "",
        "execution_decision_id": direct_decision,
        "path_run_id": path_run_id,
        "legacy_decision_id": legacy_id,
    }
    return {
        "execution_key": selection["selection_key"],
        "selection_key": selection["selection_key"],
        "runtime_mode": selection["runtime_mode"],
        "session_date": selection["session_date"],
        "market": selection["market"],
        "ticker": selection["ticker"],
        "v2_decision_id": (canonical or {}).get("v2_decision_id"),
        "execution_decision_id": direct_decision,
        "legacy_decision_id": legacy_id,
        "canonical_key": (canonical or {}).get("canonical_key"),
        "path_type": (canonical or {}).get("path_type"),
        "path_run_id": (canonical or {}).get("path_run_id") or path_run_id,
        "strategy": (canonical or {}).get("strategy"),
        "origin_action": (canonical or {}).get("origin_action"),
        "filled": _as_bool_int((canonical or {}).get("filled")),
        "closed": _as_bool_int((canonical or {}).get("closed")),
        "first_fill_event_id": _as_int((canonical or {}).get("first_fill_event_id")),
        "first_close_event_id": _as_int((canonical or {}).get("first_close_event_id")),
        "last_close_event_id": _as_int((canonical or {}).get("last_close_event_id")),
        "earliest_fill_at": (canonical or {}).get("earliest_fill_at"),
        "first_closed_at": (canonical or {}).get("first_closed_at"),
        "last_closed_at": (canonical or {}).get("last_closed_at"),
        "entry_price": _as_float((canonical or {}).get("entry_price")),
        "exit_price": _as_float((canonical or {}).get("last_exit_price") if (canonical or {}).get("last_exit_price") is not None else (canonical or {}).get("first_exit_price")),
        "pnl_pct": _as_float((canonical or {}).get("pnl_pct")),
        "mfe_pct": _as_float((canonical or {}).get("mfe_pct")),
        "mae_pct": _as_float((canonical or {}).get("mae_pct")),
        "close_reason": "",
        "quality_grade": (canonical or {}).get("quality_grade"),
        "learning_allowed": _as_bool_int((canonical or {}).get("learning_allowed")),
        "match_quality": match_quality,
        "execution_link_source": refs.get("execution_link_source") or "",
        "created_at": now,
        "updated_at": now,
        "source_quality": source_quality,
        "source_refs_json": _json(source_refs),
    }


def _upsert(conn: sqlite3.Connection, table: str, row: dict[str, Any], pk: str) -> None:
    columns = list(row.keys())
    placeholders = ", ".join(f":{column}" for column in columns)
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns if column != pk)
    sql = f"""
        INSERT INTO {table} ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT({pk}) DO UPDATE SET {updates}
    """
    conn.execute(sql, row)


def _delete_fact_scope(
    conn: sqlite3.Connection,
    *,
    runtime_mode: str,
    market: str,
    start_date: str,
    end_date: str,
) -> None:
    where = ["runtime_mode=?"]
    params: list[Any] = [runtime_mode]
    if start_date:
        where.append("session_date>=?")
        params.append(start_date)
    if end_date:
        where.append("session_date<=?")
        params.append(end_date)
    if market and market != "ALL":
        where.append("market=?")
        params.append(market)
    where_sql = " AND ".join(where)
    for table in ("fact_selection", "fact_forward_outcome", "fact_execution"):
        conn.execute(f"DELETE FROM {table} WHERE {where_sql}", params)


def _resolve_dates(date: str = "", start_date: str = "", end_date: str = "") -> tuple[str, str]:
    if date:
        return date, date
    return start_date, end_date


def build_claude_decision_facts(
    *,
    db_path: str | Path = DEFAULT_FACT_DB,
    candidate_audit_db: str | Path = DEFAULT_CANDIDATE_AUDIT_DB,
    selection_db: str | Path = DEFAULT_SELECTION_DB,
    ml_db: str | Path = DEFAULT_ML_DB,
    event_db: str | Path = DEFAULT_EVENT_DB,
    date: str = "",
    start_date: str = "",
    end_date: str = "",
    market: str = "ALL",
    runtime_mode: str = "live",
    dry_run: bool = False,
) -> dict[str, Any]:
    started_at = _utc_now()
    start_date, end_date = _resolve_dates(date=date, start_date=start_date, end_date=end_date)
    market_key = _market(market or "ALL")
    runtime_key = str(runtime_mode or "live").strip().lower()
    summary: dict[str, Any] = {
        "started_at": started_at,
        "finished_at": "",
        "status": "OK",
        "dry_run": bool(dry_run),
        "db_path": str(db_path),
        "start_date": start_date,
        "end_date": end_date,
        "market": market_key,
        "runtime_mode": runtime_key,
        "missing_sources": [],
        "source_errors": {},
        "source_counts": {},
        "fact_selection_rows": 0,
        "fact_forward_outcome_rows": 0,
        "fact_execution_rows": 0,
        "ambiguous_execution_matches": 0,
        "missing_execution_matches": 0,
    }

    audit_conn = _source_conn(Path(candidate_audit_db), "candidate_audit_db", summary)
    selection_conn = _source_conn(Path(selection_db), "selection_db", summary)
    ml_conn = _source_conn(Path(ml_db), "ml_db", summary)
    event_conn = _source_conn(Path(event_db), "event_db", summary)
    try:
        audit_rows = _load_audit_rows(
            audit_conn,
            start_date=start_date,
            end_date=end_date,
            market=market_key,
            runtime_mode=runtime_key,
        )
        selection_rows = _load_selection_rows(
            selection_conn,
            start_date=start_date,
            end_date=end_date,
            market=market_key,
            runtime_mode=runtime_key,
        )
        canonical_rows = _load_canonical_rows(
            ml_conn,
            start_date=start_date,
            end_date=end_date,
            market=market_key,
            runtime_mode=runtime_key,
        )
        fill_links = _load_fill_links(ml_conn)
        path_runs = _load_event_path_runs(
            event_conn,
            start_date=start_date,
            end_date=end_date,
            market=market_key,
            runtime_mode=runtime_key,
        )
        summary["source_counts"] = {
            "audit_candidate_rows": len(audit_rows),
            "ticker_selection_log": len(selection_rows),
            "v2_canonical_performance": len(canonical_rows),
            "v2_path_runs": len(path_runs),
        }

        now = _utc_now()
        selection_lookup = _selection_log_lookup(selection_rows)
        audit_dedupe_keys = {
            _dedupe_key(row.get("runtime_mode") or "", row.get("market") or "", row.get("session_date") or "", row.get("ticker") or "")
            for row in audit_rows
        }
        selection_facts = [_audit_selection_fact(row, now) for row in audit_rows]
        for row in selection_rows:
            key = _dedupe_key(row.get("bot_mode") or "", row.get("market") or "", row.get("date") or "", row.get("ticker") or "")
            if key not in audit_dedupe_keys:
                selection_facts.append(_selection_log_fact(row, now))

        candidate_keys = {str(row.get("candidate_key") or "") for row in audit_rows if str(row.get("candidate_key") or "")}
        audit_outcomes = _load_audit_outcomes(audit_conn, candidate_keys)
        outcome_facts = [
            _build_outcome_fact(selection, audit_outcomes, selection_lookup, now)
            for selection in selection_facts
        ]
        canonical_index = _canonical_indexes(canonical_rows)
        execution_facts = [
            _build_execution_fact(selection, selection_lookup, canonical_index, fill_links, path_runs, now)
            for selection in selection_facts
        ]

        summary["fact_selection_rows"] = len(selection_facts)
        summary["fact_forward_outcome_rows"] = len(outcome_facts)
        summary["fact_execution_rows"] = len(execution_facts)
        summary["ambiguous_execution_matches"] = sum(1 for row in execution_facts if str(row.get("source_quality")) == "ambiguous_match")
        summary["missing_execution_matches"] = sum(1 for row in execution_facts if str(row.get("source_quality")) == "missing_execution")

        if not dry_run:
            init_schema(db_path)
            with closing(_connect_writable(Path(db_path))) as fact_conn:
                with fact_conn:
                    if audit_conn is not None or selection_conn is not None:
                        _delete_fact_scope(
                            fact_conn,
                            runtime_mode=runtime_key,
                            market=market_key,
                            start_date=start_date,
                            end_date=end_date,
                        )
                    for row in selection_facts:
                        _upsert(fact_conn, "fact_selection", row, "selection_key")
                    for row in outcome_facts:
                        _upsert(fact_conn, "fact_forward_outcome", row, "selection_key")
                    for row in execution_facts:
                        _upsert(fact_conn, "fact_execution", row, "execution_key")
                    finished_at = _utc_now()
                    summary["finished_at"] = finished_at
                    fact_conn.execute(
                        """
                        INSERT INTO fact_build_runs (
                            started_at, finished_at, status, runtime_mode, market,
                            start_date, end_date, dry_run, params_json, summary_json, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            started_at,
                            finished_at,
                            "OK",
                            runtime_key,
                            market_key,
                            start_date,
                            end_date,
                            0,
                            _json(
                                {
                                    "candidate_audit_db": str(candidate_audit_db),
                                    "selection_db": str(selection_db),
                                    "ml_db": str(ml_db),
                                    "event_db": str(event_db),
                                }
                            ),
                            _json(summary),
                            finished_at,
                        ),
                    )
        else:
            summary["finished_at"] = _utc_now()
        return summary
    finally:
        for conn in (audit_conn, selection_conn, ml_conn, event_conn):
            if conn is not None:
                conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build read-only Claude decision fact mart.")
    parser.add_argument("--db", default=str(DEFAULT_FACT_DB))
    parser.add_argument("--candidate-audit-db", default=str(DEFAULT_CANDIDATE_AUDIT_DB))
    parser.add_argument("--selection-db", default=str(DEFAULT_SELECTION_DB))
    parser.add_argument("--ml-db", default=str(DEFAULT_ML_DB))
    parser.add_argument("--event-db", default=str(DEFAULT_EVENT_DB))
    parser.add_argument("--date", default="")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--market", default="ALL")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    summary = build_claude_decision_facts(
        db_path=args.db,
        candidate_audit_db=args.candidate_audit_db,
        selection_db=args.selection_db,
        ml_db=args.ml_db,
        event_db=args.event_db,
        date=args.date,
        start_date=args.start_date,
        end_date=args.end_date,
        market=args.market,
        runtime_mode=args.runtime_mode,
        dry_run=bool(args.dry_run),
    )
    if args.json:
        print(_json(summary))
    else:
        print(
            "claude decision facts: "
            f"selection={summary['fact_selection_rows']} "
            f"outcome={summary['fact_forward_outcome_rows']} "
            f"execution={summary['fact_execution_rows']} "
            f"ambiguous={summary['ambiguous_execution_matches']} "
            f"missing_execution={summary['missing_execution_matches']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
