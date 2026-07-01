from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from bot.session_date import KST, resolve_session_date_str
from preopen.storage import load_preopen_state, log_path, read_jsonl_tail, state_path
from runtime_paths import get_runtime_path
from minority_report.claude_utils import response_text, thinking_extra_body


SCHEMA_VERSION = "preopen_continuation_shadow.v1"
PROMPT_VERSION = "preopen_continuation_eval.v1"
DEFAULT_DB_FILENAME = "preopen_continuation.db"
SUPPORTED_MARKET = "US"
SUPPORTED_MARKETS = {"KR", "US"}
EVAL_LABEL = "preopen_continuation_eval"
DEFAULT_MAX_CANDIDATES = 15
DEFAULT_SOURCE_LIMIT = 60
DENSE_OFFSET_ALIASES = {"all", "dense", "full", "range", "5m", "5min", "5-min", "every5", "every_5m"}
PROTECTED_SOURCE_DB_NAMES = {
    "ticker_selection_log.db",
    "candidate_audit.db",
    "decisions.db",
    "v2_event_store.db",
    "intraday_strategy_log.db",
    "agent_call_events.db",
}


class ContinuationShadowError(RuntimeError):
    pass


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    candidate_id: int
    ticker: str
    rank_in_prompt: int
    payload: dict[str, Any]
    visible_feature_hash: str


def default_db_path() -> Path:
    return get_runtime_path("data", DEFAULT_DB_FILENAME)


def _resolve_db_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path).expanduser() if db_path else default_db_path()


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _protected_source_db_paths() -> set[Path]:
    candidates = [
        get_runtime_path("data", "ticker_selection_log.db", make_parents=False),
        get_runtime_path("data", "audit", "candidate_audit.db", make_parents=False),
        get_runtime_path("data", "ml", "decisions.db", make_parents=False),
        get_runtime_path("data", "v2_event_store.db", make_parents=False),
        get_runtime_path("data", "intraday_strategy_log.db", make_parents=False),
        get_runtime_path("data", "audit", "agent_call_events.db", make_parents=False),
    ]
    return {_resolved(path) for path in candidates}


def _ensure_shadow_write_db_path(db_path: str | Path | None = None) -> Path:
    path = _resolve_db_path(db_path)
    resolved = _resolved(path)
    name = resolved.name.lower()
    if name in PROTECTED_SOURCE_DB_NAMES or resolved in _protected_source_db_paths():
        raise ContinuationShadowError(f"preopen_shadow_refuses_protected_db_path:{resolved}")
    if not (name == DEFAULT_DB_FILENAME or (name.startswith("preopen_continuation") and name.endswith(".db"))):
        raise ContinuationShadowError(f"preopen_shadow_db_path_must_be_isolated:{resolved}")
    return path


def normalize_market(market: str) -> str:
    market_key = str(market or "").strip().upper()
    if market_key not in SUPPORTED_MARKETS:
        raise ContinuationShadowError("preopen_continuation_shadow_supports_kr_us_only")
    return market_key


def normalize_mode(mode: str) -> str:
    return "live" if str(mode or "").strip().lower() == "live" else "paper"


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = _ensure_shadow_write_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_readonly(path: str | Path) -> sqlite3.Connection:
    db = Path(path)
    uri = f"{db.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _db(db_path: str | Path | None = None):
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def _readonly_db(path: str | Path):
    conn = _connect_readonly(path)
    try:
        yield conn
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _unique_index_column_sets(conn: sqlite3.Connection, table: str) -> list[tuple[str, ...]]:
    result: list[tuple[str, ...]] = []
    try:
        indexes = conn.execute(f"PRAGMA index_list({_quote_ident(table)})").fetchall()
    except sqlite3.Error:
        return result
    for index in indexes:
        try:
            is_unique = bool(index["unique"])
            index_name = str(index["name"])
        except Exception:
            is_unique = bool(index[2])
            index_name = str(index[1])
        if not is_unique:
            continue
        try:
            cols = tuple(
                str(row["name"] if isinstance(row, sqlite3.Row) else row[2])
                for row in conn.execute(f"PRAGMA index_info({_quote_ident(index_name)})").fetchall()
            )
        except sqlite3.Error:
            cols = ()
        if cols:
            result.append(cols)
    return result


def _runtime_mode_table_sql(table: str, sql: str, columns: set[str]) -> str:
    start = sql.find("(")
    if start < 0:
        raise ContinuationShadowError(f"preopen_shadow_schema_migration_invalid_sql:{table}")
    new_sql = f"CREATE TABLE {table} " + sql[start:]
    if table in {"preopen_feature_snapshots", "preopen_outcomes"} and "runtime_mode" not in columns:
        new_sql = new_sql.replace(
            "market TEXT NOT NULL,",
            "market TEXT NOT NULL,\n                runtime_mode TEXT NOT NULL DEFAULT 'live',",
            1,
        )
    replacements = {
        "preopen_candidates": (
            "UNIQUE(session_date, market, ticker)",
            "UNIQUE(session_date, market, runtime_mode, ticker)",
        ),
        "preopen_feature_snapshots": (
            "UNIQUE(session_date, market, ticker, offset_min)",
            "UNIQUE(session_date, market, runtime_mode, ticker, offset_min)",
        ),
        "preopen_claude_checks": (
            "UNIQUE(session_date, market, eval_offset_min, fingerprint, attempt_no)",
            "UNIQUE(session_date, market, runtime_mode, eval_offset_min, fingerprint, attempt_no)",
        ),
        "preopen_outcomes": (
            "UNIQUE(session_date, market, ticker)",
            "UNIQUE(session_date, market, runtime_mode, ticker)",
        ),
    }
    old, new = replacements[table]
    new_sql = new_sql.replace(old, new)
    return new_sql


def _rebuild_runtime_mode_identity_table(conn: sqlite3.Connection, table: str) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not row or not row["sql"]:
        return
    columns = _table_columns(conn, table)
    create_sql = _runtime_mode_table_sql(table, str(row["sql"]), columns)
    legacy = f"{table}_legacy_{uuid.uuid4().hex[:8]}"
    conn.execute(f"ALTER TABLE {_quote_ident(table)} RENAME TO {_quote_ident(legacy)}")
    conn.execute(create_sql)
    old_columns = _table_columns(conn, legacy)
    new_columns = _table_columns(conn, table)
    copy_columns: list[str] = []
    select_exprs: list[str] = []
    for column in new_columns:
        if column == "runtime_mode":
            copy_columns.append(column)
            if column in old_columns:
                select_exprs.append("COALESCE(runtime_mode, 'live')")
            else:
                select_exprs.append("'live'")
            continue
        if column in old_columns:
            copy_columns.append(column)
            select_exprs.append(_quote_ident(column))
    conn.execute(
        f"""
        INSERT OR IGNORE INTO {_quote_ident(table)} ({', '.join(_quote_ident(col) for col in copy_columns)})
        SELECT {', '.join(select_exprs)} FROM {_quote_ident(legacy)}
        """
    )
    conn.execute(f"DROP TABLE {_quote_ident(legacy)}")


def _migrate_runtime_mode_identity_keys(conn: sqlite3.Connection) -> None:
    desired = {
        "preopen_candidates": ("session_date", "market", "runtime_mode", "ticker"),
        "preopen_feature_snapshots": ("session_date", "market", "runtime_mode", "ticker", "offset_min"),
        "preopen_claude_checks": ("session_date", "market", "runtime_mode", "eval_offset_min", "fingerprint", "attempt_no"),
        "preopen_outcomes": ("session_date", "market", "runtime_mode", "ticker"),
    }
    stale = {
        "preopen_candidates": ("session_date", "market", "ticker"),
        "preopen_feature_snapshots": ("session_date", "market", "ticker", "offset_min"),
        "preopen_claude_checks": ("session_date", "market", "eval_offset_min", "fingerprint", "attempt_no"),
        "preopen_outcomes": ("session_date", "market", "ticker"),
    }
    for table, desired_columns in desired.items():
        if not _table_columns(conn, table):
            continue
        unique_sets = _unique_index_column_sets(conn, table)
        columns = _table_columns(conn, table)
        if desired_columns not in unique_sets or stale[table] in unique_sets or "runtime_mode" not in columns:
            _rebuild_runtime_mode_identity_table(conn, table)


def _ensure_secondary_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS ix_preopen_candidates_session_eligible_score
        ON preopen_candidates(session_date, market, runtime_mode, eligible, deterministic_score DESC);

        CREATE INDEX IF NOT EXISTS ix_preopen_candidates_source_hash
        ON preopen_candidates(source_row_hash);

        CREATE INDEX IF NOT EXISTS ix_preopen_feature_snapshots_candidate_offset
        ON preopen_feature_snapshots(candidate_id, offset_min);

        CREATE INDEX IF NOT EXISTS ix_preopen_claude_decisions_case
        ON preopen_claude_decisions(check_id, case_id);

        CREATE INDEX IF NOT EXISTS ix_preopen_outcomes_decision_join
        ON preopen_outcomes(session_date, market, runtime_mode, ticker, actual_selected, actual_trade_ready, actual_ordered);
        """
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_text(_json(value))


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(str(value).replace(",", ""))
        return parsed
    except Exception:
        return None


def _int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(str(value).replace(",", "")))
    except Exception:
        return None


def _bool_int(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    return 1 if str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"} else 0


def _pct(current: Any, base: Any) -> float | None:
    current_f = _num(current)
    base_f = _num(base)
    if current_f is None or base_f is None or base_f <= 0:
        return None
    return round(((current_f - base_f) / base_f) * 100.0, 4)


def _ticker(row: dict[str, Any], market: str = SUPPORTED_MARKET) -> str:
    raw = str((row or {}).get("ticker") or "").strip()
    return raw.upper() if normalize_market(market) == "US" else raw


def _first_num(row: dict[str, Any], keys: Iterable[str]) -> float | None:
    for key in keys:
        value = _num((row or {}).get(key))
        if value is not None:
            return value
    return None


def _candidate_gap_pct(row: dict[str, Any]) -> float | None:
    return _first_num(row, ("extended_change_pct", "gap_pct", "change_rate"))


def _candidate_rank(row: dict[str, Any]) -> int | None:
    return _int(
        (row or {}).get("shadow_preopen_rank")
        or (row or {}).get("preopen_rank")
        or (row or {}).get("provider_rank")
        or (row or {}).get("actual_selection_rank")
    )


def _candidate_dollar_volume(row: dict[str, Any]) -> float | None:
    explicit = _first_num(row, ("extended_dollar_volume", "prior_day_traded_value"))
    if explicit is not None:
        return explicit
    price = _first_num(row, ("extended_price", "price", "last_price"))
    volume = _first_num(row, ("extended_volume", "volume"))
    if price is None or volume is None:
        return None
    return round(price * volume, 2)


def _liquidity_bucket(market: str, value: float | None) -> str:
    if value is None:
        return "missing"
    market_key = normalize_market(market)
    if market_key == "KR":
        if value >= 30_000_000_000:
            return "KRW_30B_PLUS"
        if value >= 10_000_000_000:
            return "KRW_10B_30B"
        if value >= 3_000_000_000:
            return "KRW_3B_10B"
        return "KRW_LT_3B"
    return bucket_dollar_volume(value)


def _regular_open_price(row: dict[str, Any]) -> float | None:
    return _first_num(row, ("regular_open_price", "open_price"))


def _news_flag(row: dict[str, Any]) -> bool:
    if _bool_int((row or {}).get("news_or_earnings_flag")):
        return True
    count = _int((row or {}).get("news_or_earnings_count"))
    return bool(count and count > 0)


def deterministic_candidate_result(row: dict[str, Any], *, require_regular_open: bool = True) -> dict[str, Any]:
    market = normalize_market(str((row or {}).get("market") or SUPPORTED_MARKET))
    gap = _candidate_gap_pct(row)
    dollar_volume = _candidate_dollar_volume(row)
    rank = _candidate_rank(row)
    regular_open = _regular_open_price(row)
    components = {"market_supported": market in SUPPORTED_MARKETS}
    if market == "KR":
        components.update(
            {
                "indicative_positive": gap is not None and gap > 0,
                "indicative_3_20": gap is not None and 3.0 <= gap <= 20.0,
                "kr_traded_value_3b": dollar_volume is not None and dollar_volume >= 3_000_000_000,
                "rank_le_40": rank is not None and rank <= 40,
                "regular_open_price_available": regular_open is not None and regular_open > 0,
            }
        )
    else:
        components.update(
            {
                "gap_positive": gap is not None and gap > 0,
                "gap_2_20": gap is not None and 2.0 <= gap <= 20.0,
                "extended_dollar_volume_50m": dollar_volume is not None and dollar_volume >= 50_000_000,
                "rank_le_40": rank is not None and rank <= 40,
                "regular_open_price_available": regular_open is not None and regular_open > 0,
            }
        )
    if not require_regular_open:
        components["regular_open_price_available"] = True

    exclusion = [key for key, ok in components.items() if not ok]
    score = 0.0
    if gap is not None:
        lower_gap = 3.0 if market == "KR" else 2.0
        if lower_gap <= gap <= 10.0:
            score += 30.0
        elif 10.0 < gap <= 20.0:
            score += 18.0
        elif gap > 20.0:
            score -= 30.0
    if dollar_volume is not None:
        if market == "KR":
            if dollar_volume >= 30_000_000_000:
                score += 30.0
            elif dollar_volume >= 10_000_000_000:
                score += 20.0
            elif dollar_volume >= 3_000_000_000:
                score += 10.0
        else:
            if dollar_volume >= 300_000_000:
                score += 30.0
            elif dollar_volume >= 100_000_000:
                score += 20.0
            elif dollar_volume >= 50_000_000:
                score += 10.0
    if rank is not None:
        if rank <= 10:
            score += 25.0
        elif rank <= 20:
            score += 15.0
        elif rank <= 40:
            score += 5.0
    if _news_flag(row):
        score += 10.0
    return {
        "eligible": 1 if not exclusion else 0,
        "deterministic_score": round(score, 4),
        "exclusion_reason": ",".join(exclusion),
        "components": components,
        "gap_pct": gap,
        "extended_dollar_volume": dollar_volume,
        "preopen_rank": rank,
        "regular_open_price": regular_open,
    }


def bucket_pct(value: float | None, cuts: tuple[float, ...] = (-5, -3, -1, 0, 1, 3, 5, 10)) -> str:
    if value is None:
        return "missing"
    prev = None
    for cut in cuts:
        if value <= cut:
            if prev is None:
                return f"le_{cut:g}"
            return f"{prev:g}_{cut:g}"
        prev = cut
    return f"gt_{cuts[-1]:g}"


def bucket_dollar_volume(value: float | None) -> str:
    if value is None:
        return "missing"
    if value >= 300_000_000:
        return "300M_PLUS"
    if value >= 100_000_000:
        return "100M_300M"
    if value >= 50_000_000:
        return "50M_100M"
    return "LT_50M"


def bucket_rank(value: int | None) -> str:
    if value is None:
        return "missing"
    if value <= 10:
        return "1_10"
    if value <= 20:
        return "11_20"
    if value <= 40:
        return "21_40"
    return "GT_40"


def init_schema(db_path: str | Path | None = None) -> None:
    with _db(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS preopen_shadow_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                session_date TEXT NOT NULL,
                market TEXT NOT NULL,
                runtime_mode TEXT NOT NULL,
                step TEXT NOT NULL,
                offset_min INTEGER,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                source_state_path TEXT,
                source_candidate_log_path TEXT,
                source_outcome_log_path TEXT,
                source_state_captured_at TEXT,
                source_state_age_min REAL,
                source_candidate_count INTEGER DEFAULT 0,
                eligible_count INTEGER DEFAULT 0,
                evaluated_count INTEGER DEFAULT 0,
                error_type TEXT,
                error_message TEXT,
                config_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS preopen_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_date TEXT NOT NULL,
                market TEXT NOT NULL,
                runtime_mode TEXT NOT NULL,
                schema_version TEXT,
                run_id TEXT,
                ticker TEXT NOT NULL,
                name TEXT,
                source TEXT,
                preopen_rank INTEGER,
                provider_rank INTEGER,
                gap_pct REAL,
                extended_price REAL,
                extended_volume REAL,
                extended_dollar_volume REAL,
                news_or_earnings_flag INTEGER DEFAULT 0,
                news_sample_title TEXT,
                preopen_reason_json TEXT,
                quality_tags_json TEXT,
                risk_tags_json TEXT,
                eligible INTEGER DEFAULT 0,
                deterministic_score REAL,
                exclusion_reason TEXT,
                captured_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                source_status TEXT,
                provider TEXT,
                data_quality TEXT,
                stale INTEGER DEFAULT 0,
                source_file TEXT,
                source_row_hash TEXT,
                source_row_json TEXT,
                first_detected_at TEXT,
                last_detected_at TEXT,
                detected_at TEXT,
                preopen_score REAL,
                preopen_grade TEXT,
                screen_score REAL,
                change_rate REAL,
                volume_ratio REAL,
                spread_pct REAL,
                bid REAL,
                ask REAL,
                regular_prev_close REAL,
                regular_open_price REAL,
                anchor_price REAL,
                anchor_price_source TEXT,
                anchor_price_at TEXT,
                category TEXT,
                sector TEXT,
                market_type TEXT,
                liquidity_bucket TEXT,
                from_high_pct REAL,
                from_high_bucket TEXT,
                above_ma60 INTEGER,
                source_overlap_count INTEGER,
                pattern_tags_json TEXT,
                news_or_earnings_count INTEGER,
                news_or_earnings_sources_json TEXT,
                eligible_rule_version TEXT,
                eligible_components_json TEXT,
                evaluation_case_id TEXT,
                UNIQUE(session_date, market, runtime_mode, ticker)
            );

            CREATE TABLE IF NOT EXISTS preopen_feature_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_date TEXT NOT NULL,
                market TEXT NOT NULL,
                runtime_mode TEXT NOT NULL DEFAULT 'live',
                ticker TEXT NOT NULL,
                offset_min INTEGER NOT NULL,
                regular_open_price REAL,
                observed_price REAL,
                return_from_open_pct REAL,
                mfe_from_open_pct REAL,
                mae_from_open_pct REAL,
                volume REAL,
                price_source TEXT,
                captured_at TEXT,
                run_id TEXT,
                candidate_id INTEGER,
                snapshot_status TEXT,
                token_status TEXT,
                source_outcome_path TEXT,
                source_outcome_ts TEXT,
                source_offset_min INTEGER,
                anchor_price REAL,
                anchor_return_pct REAL,
                high_price REAL,
                low_price REAL,
                high_return_from_open_pct REAL,
                low_return_from_open_pct REAL,
                high_return_from_anchor_pct REAL,
                low_return_from_anchor_pct REAL,
                price_basis TEXT,
                price_basis_missing INTEGER DEFAULT 0,
                sample_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                UNIQUE(candidate_id, offset_min),
                UNIQUE(session_date, market, runtime_mode, ticker, offset_min)
            );

            CREATE TABLE IF NOT EXISTS preopen_claude_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_date TEXT NOT NULL,
                market TEXT NOT NULL,
                eval_offset_min INTEGER NOT NULL,
                prompt_version TEXT,
                model TEXT,
                candidate_count INTEGER DEFAULT 0,
                fingerprint TEXT,
                smart_skip INTEGER DEFAULT 0,
                skip_reason TEXT,
                raw_call_path TEXT,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                parse_ok INTEGER DEFAULT 0,
                parse_error TEXT,
                created_at TEXT NOT NULL,
                run_id TEXT,
                runtime_mode TEXT NOT NULL,
                status TEXT,
                attempt_no INTEGER DEFAULT 1,
                retry_of_check_id INTEGER,
                max_tokens INTEGER,
                duration_ms INTEGER,
                prompt_hash TEXT,
                response_hash TEXT,
                prompt_chars INTEGER,
                response_chars INTEGER,
                prompt_case_count INTEGER,
                case_map_json TEXT,
                throttle_enabled INTEGER DEFAULT 0,
                throttle_allowed INTEGER DEFAULT 1,
                throttle_tier TEXT,
                daily_call_count_before INTEGER DEFAULT 0,
                daily_call_count_after INTEGER DEFAULT 0,
                api_error_type TEXT,
                api_error_message TEXT,
                UNIQUE(session_date, market, runtime_mode, eval_offset_min, fingerprint, attempt_no)
            );

            CREATE TABLE IF NOT EXISTS preopen_claude_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                check_id INTEGER NOT NULL,
                session_date TEXT NOT NULL,
                market TEXT NOT NULL,
                ticker TEXT NOT NULL,
                visible_offset_min INTEGER NOT NULL,
                decision TEXT NOT NULL,
                confidence REAL,
                reason_code TEXT,
                action_ceiling TEXT NOT NULL DEFAULT 'WATCH',
                would_inject_candidate_pool INTEGER DEFAULT 0,
                actually_injected INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                candidate_id INTEGER,
                case_id TEXT,
                rank_in_prompt INTEGER,
                raw_decision TEXT,
                raw_confidence TEXT,
                parse_warning TEXT,
                visible_feature_hash TEXT,
                decision_payload_json TEXT,
                candidate_pool_role TEXT DEFAULT 'SHADOW_ONLY',
                discovery_signal_family TEXT DEFAULT 'preopen_continuation',
                discovery_action_ceiling TEXT DEFAULT 'WATCH',
                injection_eligible_after_shadow INTEGER DEFAULT 0,
                UNIQUE(check_id, case_id)
            );

            CREATE TABLE IF NOT EXISTS preopen_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_date TEXT NOT NULL,
                market TEXT NOT NULL,
                runtime_mode TEXT NOT NULL DEFAULT 'live',
                ticker TEXT NOT NULL,
                ret_5m REAL,
                ret_30m REAL,
                ret_60m REAL,
                ret_120m REAL,
                ret_close REAL,
                mfe REAL,
                mae REAL,
                selected_by_live_claude INTEGER DEFAULT 0,
                live_trade_ready INTEGER DEFAULT 0,
                ordered INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL,
                candidate_id INTEGER,
                latest_decision_id INTEGER,
                outcome_status TEXT,
                open_price REAL,
                close_price REAL,
                ret_90m REAL,
                ret_150m REAL,
                ret_180m REAL,
                anchor_ret_5m REAL,
                anchor_ret_30m REAL,
                anchor_ret_60m REAL,
                anchor_ret_120m REAL,
                anchor_ret_close REAL,
                mfe_open_basis REAL,
                mae_open_basis REAL,
                mfe_anchor_basis REAL,
                mae_anchor_basis REAL,
                outcome_samples_json TEXT,
                actual_selected INTEGER DEFAULT 0,
                actual_selection_rank INTEGER,
                actual_trade_ready INTEGER DEFAULT 0,
                actual_ordered INTEGER DEFAULT 0,
                ticker_selection_log_id INTEGER,
                audit_candidate_key TEXT,
                v2_decision_id TEXT,
                path_run_id TEXT,
                route_final_action TEXT,
                route_route TEXT,
                entry_price REAL,
                pnl_pct REAL,
                UNIQUE(session_date, market, runtime_mode, ticker)
            );

            CREATE INDEX IF NOT EXISTS ix_preopen_candidates_session_eligible_score
            ON preopen_candidates(session_date, market, runtime_mode, eligible, deterministic_score DESC);

            CREATE INDEX IF NOT EXISTS ix_preopen_candidates_source_hash
            ON preopen_candidates(source_row_hash);

            CREATE INDEX IF NOT EXISTS ix_preopen_feature_snapshots_candidate_offset
            ON preopen_feature_snapshots(candidate_id, offset_min);

            CREATE INDEX IF NOT EXISTS ix_preopen_claude_decisions_case
            ON preopen_claude_decisions(check_id, case_id);

            CREATE INDEX IF NOT EXISTS ix_preopen_outcomes_decision_join
            ON preopen_outcomes(session_date, market, runtime_mode, ticker, actual_selected, actual_trade_ready, actual_ordered);
            """
        )
        _migrate_runtime_mode_identity_keys(conn)
        _ensure_secondary_indexes(conn)


def _start_run(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    market: str,
    runtime_mode: str,
    step: str,
    offset_min: int | None,
    config: dict[str, Any],
) -> str:
    run_id = f"{session_date}_{market}_{runtime_mode}_{step}_{offset_min if offset_min is not None else 'na'}_{uuid.uuid4().hex[:10]}"
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO preopen_shadow_runs
        (run_id, session_date, market, runtime_mode, step, offset_min, status, started_at, created_at, config_json)
        VALUES (?, ?, ?, ?, ?, ?, 'started', ?, ?, ?)
        """,
        (run_id, session_date, market, runtime_mode, step, offset_min, ts, ts, _json(config)),
    )
    return run_id


def _finish_run(conn: sqlite3.Connection, run_id: str, *, status: str, **updates: Any) -> None:
    fields = ["status=?", "finished_at=?"]
    values: list[Any] = [status, now_iso()]
    for key, value in updates.items():
        fields.append(f"{key}=?")
        values.append(value)
    values.append(run_id)
    conn.execute(f"UPDATE preopen_shadow_runs SET {', '.join(fields)} WHERE run_id=?", values)


SOURCE_ROW_KEYS = (
    "ticker",
    "name",
    "market",
    "session_date",
    "source",
    "provider",
    "captured_at",
    "detected_at",
    "first_detected_at",
    "last_detected_at",
    "shadow_preopen_rank",
    "provider_rank",
    "preopen_score",
    "preopen_grade",
    "screen_score",
    "price",
    "change_rate",
    "gap_pct",
    "extended_price",
    "extended_change_pct",
    "extended_volume",
    "extended_dollar_volume",
    "volume",
    "volume_ratio",
    "prior_day_traded_value",
    "regular_prev_close",
    "regular_open_price",
    "bid",
    "ask",
    "spread_pct",
    "anchor_price",
    "anchor_price_source",
    "anchor_price_at",
    "news_or_earnings_flag",
    "news_or_earnings_count",
    "news_or_earnings_sample_title",
    "news_or_earnings_sources",
    "news_quality",
    "news_date_quality",
    "news_quality_tags",
    "news_prompt_eligible",
    "news_signal_type",
    "news_score",
    "news_prompt_summary",
    "risk_news_summary",
    "prompt_news_ids",
    "excluded_news_counts",
    "scored_news_count",
    "preopen_reason",
    "quality_tags",
    "risk_tags",
    "pattern_tags",
    "source_overlap_count",
    "market_type",
    "category",
    "sector",
    "from_high_pct",
    "from_high_bucket",
    "liquidity_bucket",
    "above_ma60",
    "actual_selected",
    "actual_selection_rank",
    "actual_trade_ready",
    "actual_ordered",
    "outcome_samples",
)


def _compact_source_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in SOURCE_ROW_KEYS if key in row}


def _candidate_db_payload(
    row: dict[str, Any],
    *,
    state: dict[str, Any],
    run_id: str,
    runtime_mode: str,
    source_file: str,
    require_regular_open: bool,
) -> dict[str, Any]:
    market = normalize_market(str(row.get("market") or state.get("market") or SUPPORTED_MARKET))
    ticker = _ticker(row, market)
    merged = dict(row)
    merged.setdefault("market", market)
    merged.setdefault("regular_open_price", row.get("regular_open_price"))
    result = deterministic_candidate_result(merged, require_regular_open=require_regular_open)
    compact = _compact_source_row(row)
    news_sources = row.get("news_or_earnings_sources")
    news_title = row.get("news_or_earnings_sample_title") or row.get("news_sample_title")
    payload = {
        "session_date": str(row.get("session_date") or state.get("session_date") or ""),
        "market": market,
        "runtime_mode": runtime_mode,
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "ticker": ticker,
        "name": str(row.get("name") or ""),
        "source": str(row.get("source") or row.get("category") or ""),
        "preopen_rank": result["preopen_rank"],
        "provider_rank": _int(row.get("provider_rank")),
        "gap_pct": result["gap_pct"],
        "extended_price": _num(row.get("extended_price") or row.get("price")),
        "extended_volume": _num(row.get("extended_volume") or row.get("volume")),
        "extended_dollar_volume": result["extended_dollar_volume"],
        "news_or_earnings_flag": 1 if _news_flag(row) else 0,
        "news_sample_title": str(news_title or ""),
        "preopen_reason_json": _json(row.get("preopen_reason") or []),
        "quality_tags_json": _json(row.get("quality_tags") or []),
        "risk_tags_json": _json(row.get("risk_tags") or []),
        "eligible": result["eligible"],
        "deterministic_score": result["deterministic_score"],
        "exclusion_reason": result["exclusion_reason"],
        "captured_at": str(row.get("captured_at") or state.get("captured_at") or ""),
        "updated_at": now_iso(),
        "source_status": str(state.get("source_status") or state.get("collector_status") or ""),
        "provider": str(row.get("provider") or state.get("provider") or ""),
        "data_quality": str(row.get("data_quality") or state.get("data_quality") or ""),
        "stale": _bool_int(row.get("stale") or state.get("stale")),
        "source_file": source_file,
        "source_row_hash": _sha256_json(compact),
        "source_row_json": _json(compact),
        "first_detected_at": str(row.get("first_detected_at") or ""),
        "last_detected_at": str(row.get("last_detected_at") or ""),
        "detected_at": str(row.get("detected_at") or ""),
        "preopen_score": _num(row.get("preopen_score")),
        "preopen_grade": str(row.get("preopen_grade") or ""),
        "screen_score": _num(row.get("screen_score")),
        "change_rate": _num(row.get("change_rate")),
        "volume_ratio": _num(row.get("volume_ratio")),
        "spread_pct": _num(row.get("spread_pct")),
        "bid": _num(row.get("bid")),
        "ask": _num(row.get("ask")),
        "regular_prev_close": _num(row.get("regular_prev_close")),
        "regular_open_price": result["regular_open_price"],
        "anchor_price": _num(row.get("anchor_price")),
        "anchor_price_source": str(row.get("anchor_price_source") or ""),
        "anchor_price_at": str(row.get("anchor_price_at") or ""),
        "category": str(row.get("category") or row.get("source") or ""),
        "sector": str(row.get("sector") or ""),
        "market_type": str(row.get("market_type") or ""),
        "liquidity_bucket": str(row.get("liquidity_bucket") or ""),
        "from_high_pct": _num(row.get("from_high_pct")),
        "from_high_bucket": str(row.get("from_high_bucket") or ""),
        "above_ma60": _bool_int(row.get("above_ma60")),
        "source_overlap_count": _int(row.get("source_overlap_count")),
        "pattern_tags_json": _json(row.get("pattern_tags") or []),
        "news_or_earnings_count": _int(row.get("news_or_earnings_count")),
        "news_or_earnings_sources_json": _json(news_sources or []),
        "eligible_rule_version": "preopen_continuation.eligible.v2",
        "eligible_components_json": _json(result["components"]),
    }
    return payload


def _upsert_candidate(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    now = now_iso()
    payload = dict(payload)
    payload.setdefault("created_at", now)
    columns = list(payload.keys())
    placeholders = ", ".join("?" for _ in columns)
    update_columns = [col for col in columns if col not in {"id", "session_date", "market", "runtime_mode", "ticker", "created_at"}]
    update_sql = ", ".join(f"{col}=excluded.{col}" for col in update_columns)
    conn.execute(
        f"""
        INSERT INTO preopen_candidates ({', '.join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(session_date, market, runtime_mode, ticker) DO UPDATE SET {update_sql}
        """,
        [payload[col] for col in columns],
    )
    row = conn.execute(
        "SELECT id FROM preopen_candidates WHERE session_date=? AND market=? AND runtime_mode=? AND ticker=?",
        (payload["session_date"], payload["market"], payload["runtime_mode"], payload["ticker"]),
    ).fetchone()
    return int(row["id"])


def collect_candidates(
    market: str,
    *,
    session_date: str | None = None,
    mode: str = "live",
    db_path: str | Path | None = None,
    source_limit: int = DEFAULT_SOURCE_LIMIT,
    dry_run: bool = False,
) -> dict[str, Any]:
    market_key = normalize_market(market)
    runtime_mode = normalize_mode(mode)
    explicit_session_date = session_date is not None
    session_date = session_date or resolve_session_date_str(market_key)
    state_max_age = 0 if explicit_session_date else 24 * 60
    state = load_preopen_state(market_key, session_date=session_date, max_age_min=state_max_age, mode=runtime_mode) or {}
    candidates = [row for row in (state.get("candidates") or []) if isinstance(row, dict)]
    if source_limit and source_limit > 0:
        candidates = candidates[: int(source_limit)]
    source = str(state_path(market_key, session_date, mode=runtime_mode))
    result = {
        "market": market_key,
        "mode": runtime_mode,
        "session_date": session_date,
        "source_state_path": source,
        "source_candidate_count": len(candidates),
        "eligible_count": 0,
        "written": 0,
        "dry_run": bool(dry_run),
    }
    if dry_run:
        rows = [
            _candidate_db_payload(
                row,
                state=state,
                run_id="dry_run",
                runtime_mode=runtime_mode,
                source_file=source,
                require_regular_open=True,
            )
            for row in candidates
        ]
        result["eligible_count"] = sum(int(row.get("eligible", 0) or 0) for row in rows)
        result["candidates"] = rows
        return result

    init_schema(db_path)
    with _db(db_path) as conn:
        run_id = _start_run(
            conn,
            session_date=session_date,
            market=market_key,
            runtime_mode=runtime_mode,
            step="collect",
            offset_min=None,
            config={"source_limit": source_limit},
        )
        written = 0
        eligible = 0
        for raw in candidates:
            payload = _candidate_db_payload(
                raw,
                state=state,
                run_id=run_id,
                runtime_mode=runtime_mode,
                source_file=source,
                require_regular_open=True,
            )
            if not payload["ticker"]:
                continue
            _upsert_candidate(conn, payload)
            written += 1
            eligible += int(payload.get("eligible", 0) or 0)
        _finish_run(
            conn,
            run_id,
            status="success",
            source_state_path=source,
            source_candidate_log_path=str(log_path("candidates", market_key, session_date, mode=runtime_mode)),
            source_state_captured_at=str(state.get("captured_at") or ""),
            source_state_age_min=state.get("state_age_min"),
            source_candidate_count=len(candidates),
            eligible_count=eligible,
        )
    result.update({"run_id": run_id, "eligible_count": eligible, "written": written})
    return result


def _latest_outcome_by_ticker(market: str, session_date: str, mode: str, offset_min: int) -> dict[str, dict[str, Any]]:
    path = log_path("outcome", market, session_date, mode=mode)
    rows = read_jsonl_tail(path, limit=20000)
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = _ticker(row, market)
        if not ticker:
            continue
        row_offset = _int(row.get("offset_min"))
        if row_offset == int(offset_min):
            latest[ticker] = dict(row)
        for sample in row.get("outcome_samples") or []:
            if not isinstance(sample, dict):
                continue
            if _int(sample.get("offset_min")) != int(offset_min):
                continue
            merged = dict(row)
            merged.update({
                "offset_min": offset_min,
                "price": sample.get("price"),
                "captured_at": sample.get("captured_at") or row.get("captured_at") or row.get("ts"),
                "post_open_return_pct": sample.get("return_pct"),
                f"post_open_{int(offset_min)}m_return_pct": sample.get("return_pct"),
                "high": sample.get("high"),
                "low": sample.get("low"),
                "volume": sample.get("volume"),
                "price_source": sample.get("price_source") or row.get("price_source"),
                "sample_json": sample,
            })
            latest[ticker] = merged
    return latest


def _source_candidates_by_ticker(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_ticker: dict[str, dict[str, Any]] = {}
    for row in state.get("candidates") or []:
        if not isinstance(row, dict):
            continue
        ticker = _ticker(row, str(state.get("market") or SUPPORTED_MARKET))
        if ticker:
            by_ticker[ticker] = dict(row)
    return by_ticker


def _feature_payload(
    candidate: sqlite3.Row,
    *,
    source_row: dict[str, Any] | None,
    outcome_row: dict[str, Any] | None,
    offset_min: int,
    run_id: str,
    source_outcome_path: str,
) -> dict[str, Any]:
    source = dict(source_row or {})
    outcome = dict(outcome_row or {})
    ticker = str(candidate["ticker"])
    observed = _first_num(outcome, ("price", "observed_price", f"outcome_{int(offset_min)}m_price"))
    open_price = _first_num(outcome, ("regular_open_price", "open_price")) or _num(candidate["regular_open_price"])
    anchor = _first_num(outcome, ("anchor_price",)) or _num(candidate["anchor_price"])
    high = _first_num(outcome, ("high", "high_price"))
    low = _first_num(outcome, ("low", "low_price"))
    volume = _first_num(outcome, ("volume",))
    status_raw = str(outcome.get("outcome_status") or "")
    if "error" in status_raw.lower():
        status = "provider_error"
    elif observed is None:
        status = "missing"
    elif open_price is None or open_price <= 0:
        status = "basis_missing"
    else:
        status = "sampled"
    high_open = _pct(high, open_price)
    low_open = _pct(low, open_price)
    high_anchor = _pct(high, anchor)
    low_anchor = _pct(low, anchor)
    anchor_return = _first_num(
        outcome,
        ("post_open_return_pct", f"post_open_{int(offset_min)}m_return_pct", "anchor_return_pct"),
    )
    if anchor_return is None:
        anchor_return = _pct(observed, anchor)
    sample = outcome.get("sample_json") if isinstance(outcome.get("sample_json"), dict) else _compact_source_row(outcome)
    return {
        "session_date": str(candidate["session_date"]),
        "market": str(candidate["market"]),
        "runtime_mode": str(candidate["runtime_mode"] or "live"),
        "ticker": ticker,
        "offset_min": int(offset_min),
        "regular_open_price": open_price,
        "observed_price": observed,
        "return_from_open_pct": _pct(observed, open_price),
        "mfe_from_open_pct": high_open,
        "mae_from_open_pct": low_open,
        "volume": volume,
        "price_source": str(outcome.get("price_source") or ""),
        "captured_at": str(outcome.get("captured_at") or outcome.get("ts") or source.get("captured_at") or ""),
        "run_id": run_id,
        "candidate_id": int(candidate["id"]),
        "snapshot_status": status,
        "token_status": str(outcome.get("token_status") or ""),
        "source_outcome_path": source_outcome_path,
        "source_outcome_ts": str(outcome.get("ts") or ""),
        "source_offset_min": _int(outcome.get("offset_min")),
        "anchor_price": anchor,
        "anchor_return_pct": anchor_return,
        "high_price": high,
        "low_price": low,
        "high_return_from_open_pct": high_open,
        "low_return_from_open_pct": low_open,
        "high_return_from_anchor_pct": high_anchor,
        "low_return_from_anchor_pct": low_anchor,
        "price_basis": "regular_open_price" if open_price else "anchor_price",
        "price_basis_missing": 1 if open_price is None or open_price <= 0 else 0,
        "sample_json": _json(sample),
        "updated_at": now_iso(),
    }


def _upsert_feature_snapshot(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    payload = dict(payload)
    payload.setdefault("created_at", now_iso())
    columns = list(payload.keys())
    update_columns = [col for col in columns if col not in {"id", "created_at"}]
    conn.execute(
        f"""
        INSERT INTO preopen_feature_snapshots ({', '.join(columns)})
        VALUES ({', '.join('?' for _ in columns)})
        ON CONFLICT(candidate_id, offset_min) DO UPDATE SET
        {', '.join(f'{col}=excluded.{col}' for col in update_columns)}
        """,
        [payload[col] for col in columns],
    )
    if payload.get("regular_open_price"):
        row = conn.execute("SELECT source_row_json FROM preopen_candidates WHERE id=?", (payload["candidate_id"],)).fetchone()
        source = {}
        if row and row["source_row_json"]:
            try:
                source = json.loads(row["source_row_json"])
            except Exception:
                source = {}
        source["regular_open_price"] = payload.get("regular_open_price")
        result = deterministic_candidate_result(source, require_regular_open=True)
        conn.execute(
            """
            UPDATE preopen_candidates
            SET regular_open_price=?, eligible=?, deterministic_score=?, exclusion_reason=?,
                eligible_components_json=?, updated_at=?
            WHERE id=?
            """,
            (
                payload.get("regular_open_price"),
                result["eligible"],
                result["deterministic_score"],
                result["exclusion_reason"],
                _json(result["components"]),
                now_iso(),
                payload["candidate_id"],
            ),
        )


def record_feature_snapshots(
    market: str,
    *,
    offset_min: int,
    session_date: str | None = None,
    mode: str = "live",
    db_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    market_key = normalize_market(market)
    runtime_mode = normalize_mode(mode)
    explicit_session_date = session_date is not None
    session_date = session_date or resolve_session_date_str(market_key)
    state_max_age = 0 if explicit_session_date else 24 * 60
    source_state = load_preopen_state(market_key, session_date=session_date, max_age_min=state_max_age, mode=runtime_mode) or {}
    source_by_ticker = _source_candidates_by_ticker(source_state)
    outcome_by_ticker = _latest_outcome_by_ticker(market_key, session_date, runtime_mode, int(offset_min))
    outcome_path = str(log_path("outcome", market_key, session_date, mode=runtime_mode))
    if dry_run:
        return {
            "market": market_key,
            "mode": runtime_mode,
            "session_date": session_date,
            "offset_min": int(offset_min),
            "source_outcome_path": outcome_path,
            "source_outcome_count": len(outcome_by_ticker),
            "dry_run": True,
        }
    init_schema(db_path)
    with _db(db_path) as conn:
        if not conn.execute(
            "SELECT 1 FROM preopen_candidates WHERE session_date=? AND market=? AND runtime_mode=? LIMIT 1",
            (session_date, market_key, runtime_mode),
        ).fetchone():
            collect_candidates(market_key, session_date=session_date, mode=runtime_mode, db_path=db_path)
        run_id = _start_run(
            conn,
            session_date=session_date,
            market=market_key,
            runtime_mode=runtime_mode,
            step="feature",
            offset_min=int(offset_min),
            config={"offset_min": int(offset_min)},
        )
        rows = conn.execute(
            "SELECT * FROM preopen_candidates WHERE session_date=? AND market=? AND runtime_mode=? ORDER BY preopen_rank, ticker",
            (session_date, market_key, runtime_mode),
        ).fetchall()
        sampled = 0
        missing = 0
        for candidate in rows:
            payload = _feature_payload(
                candidate,
                source_row=source_by_ticker.get(str(candidate["ticker"])),
                outcome_row=outcome_by_ticker.get(str(candidate["ticker"])),
                offset_min=int(offset_min),
                run_id=run_id,
                source_outcome_path=outcome_path,
            )
            _upsert_feature_snapshot(conn, payload)
            if payload["snapshot_status"] == "sampled":
                sampled += 1
            else:
                missing += 1
        _finish_run(
            conn,
            run_id,
            status="success",
            source_state_path=str(state_path(market_key, session_date, mode=runtime_mode)),
            source_outcome_log_path=outcome_path,
            source_candidate_count=len(rows),
            eligible_count=_eligible_count(conn, session_date, market_key, runtime_mode),
        )
        _refresh_outcomes(conn, session_date=session_date, market=market_key, runtime_mode=runtime_mode)
    return {
        "market": market_key,
        "mode": runtime_mode,
        "session_date": session_date,
        "offset_min": int(offset_min),
        "run_id": run_id,
        "snapshots": len(rows),
        "sampled": sampled,
        "missing": missing,
    }


def is_dense_offset_request(offset: str | int | None) -> bool:
    return str(offset if offset is not None else "").strip().lower() in DENSE_OFFSET_ALIASES


def dense_feature_offsets_min(
    market: str,
    session_date: str | None = None,
    *,
    interval_min: int = 5,
) -> tuple[int, ...]:
    market_key = normalize_market(market)
    resolved_date = session_date or resolve_session_date_str(market_key)
    interval = _int(interval_min)
    if interval is None or interval <= 0:
        raise ContinuationShadowError(f"preopen_continuation_invalid_interval:{interval_min}")
    close_offset = _close_offset_min(market_key, resolved_date)
    if close_offset is None:
        raise ContinuationShadowError("preopen_continuation_close_offset_unavailable")
    offsets: list[int] = [5]
    offset = int(interval)
    while offset <= int(close_offset):
        offsets.append(offset)
        offset += int(interval)
    if int(close_offset) not in offsets:
        offsets.append(int(close_offset))
    return tuple(dict.fromkeys(offset for offset in offsets if offset > 0))


def record_feature_snapshot_range(
    market: str,
    *,
    session_date: str | None = None,
    mode: str = "live",
    db_path: str | Path | None = None,
    interval_min: int = 5,
    offsets: Iterable[int | str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    market_key = normalize_market(market)
    runtime_mode = normalize_mode(mode)
    resolved_session_date = session_date or resolve_session_date_str(market_key)
    if offsets is None:
        offset_values = dense_feature_offsets_min(market_key, resolved_session_date, interval_min=interval_min)
    else:
        offset_values = tuple(
            dict.fromkeys(
                resolve_offset_min(value, market=market_key, session_date=resolved_session_date)
                for value in offsets
            )
        )
    if not offset_values:
        raise ContinuationShadowError("preopen_continuation_no_offsets")

    results: list[dict[str, Any]] = []
    for offset in offset_values:
        results.append(
            record_feature_snapshots(
                market_key,
                session_date=resolved_session_date,
                mode=runtime_mode,
                db_path=db_path,
                offset_min=int(offset),
                dry_run=dry_run,
            )
        )
    return {
        "market": market_key,
        "mode": runtime_mode,
        "session_date": resolved_session_date,
        "interval_min": int(_int(interval_min) or 0),
        "offsets": [int(value) for value in offset_values],
        "offset_count": len(offset_values),
        "snapshots": sum(int(item.get("snapshots") or 0) for item in results),
        "sampled": sum(int(item.get("sampled") or 0) for item in results),
        "missing": sum(int(item.get("missing") or 0) for item in results),
        "dry_run": bool(dry_run),
        "results": results,
    }


def _eligible_count(conn: sqlite3.Connection, session_date: str, market: str, runtime_mode: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM preopen_candidates WHERE session_date=? AND market=? AND runtime_mode=? AND eligible=1",
        (session_date, market, runtime_mode),
    ).fetchone()
    return int(row["c"] if row else 0)


def _latest_decisions(conn: sqlite3.Connection, session_date: str, market: str, runtime_mode: str) -> dict[int, sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT d.*
        FROM preopen_claude_decisions d
        JOIN preopen_claude_checks ch ON ch.id=d.check_id
        JOIN (
            SELECT d2.candidate_id, MAX(d2.id) AS id
            FROM preopen_claude_decisions d2
            JOIN preopen_claude_checks ch2 ON ch2.id=d2.check_id
            WHERE d2.session_date=? AND d2.market=? AND ch2.runtime_mode=?
            GROUP BY d2.candidate_id
        ) latest ON latest.id=d.id
        """,
        (session_date, market, runtime_mode),
    ).fetchall()
    return {int(row["candidate_id"]): row for row in rows if row["candidate_id"] is not None}


def _row_value(row: sqlite3.Row | None, key: str) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        return None


def _close_offset_min(market: str, session_date: str) -> int | None:
    try:
        from preopen.scheduler import default_outcome_offsets_min

        offsets = [int(value) for value in default_outcome_offsets_min(market, session_date)]
        return max(offsets) if offsets else None
    except Exception:
        return None


def _refresh_outcomes(conn: sqlite3.Connection, *, session_date: str, market: str, runtime_mode: str) -> None:
    decisions = _latest_decisions(conn, session_date, market, runtime_mode)
    candidates = conn.execute(
        "SELECT * FROM preopen_candidates WHERE session_date=? AND market=? AND runtime_mode=?",
        (session_date, market, runtime_mode),
    ).fetchall()
    for candidate in candidates:
        cid = int(candidate["id"])
        snapshots = conn.execute(
            "SELECT * FROM preopen_feature_snapshots WHERE candidate_id=?",
            (cid,),
        ).fetchall()
        by_offset = {int(row["offset_min"]): row for row in snapshots}
        ret = {offset: _row_value(row, "return_from_open_pct") for offset, row in by_offset.items()}
        anchor_ret = {offset: _row_value(row, "anchor_return_pct") for offset, row in by_offset.items()}
        mfe_values = [row["mfe_from_open_pct"] for row in snapshots if row["mfe_from_open_pct"] is not None]
        mae_values = [row["mae_from_open_pct"] for row in snapshots if row["mae_from_open_pct"] is not None]
        anchor_mfe_values = [row["high_return_from_anchor_pct"] for row in snapshots if row["high_return_from_anchor_pct"] is not None]
        anchor_mae_values = [row["low_return_from_anchor_pct"] for row in snapshots if row["low_return_from_anchor_pct"] is not None]
        open_price = next((row["regular_open_price"] for row in snapshots if row["regular_open_price"]), None)
        close_offset = _close_offset_min(market, session_date)
        close_row = by_offset.get(close_offset) if close_offset is not None else None
        close_price = _row_value(close_row, "observed_price")
        ret_close = _row_value(close_row, "return_from_open_pct")
        anchor_ret_close = _row_value(close_row, "anchor_return_pct")
        decision = decisions.get(cid)
        source = {}
        try:
            source = json.loads(candidate["source_row_json"] or "{}")
        except Exception:
            source = {}
        actual_selected = _bool_int(source.get("actual_selected"))
        actual_trade_ready = _bool_int(source.get("actual_trade_ready"))
        actual_ordered = _bool_int(source.get("actual_ordered"))
        status = "complete" if ret_close is not None else ("partial" if snapshots else "missing")
        payload = {
            "session_date": session_date,
            "market": market,
            "runtime_mode": str(candidate["runtime_mode"] or runtime_mode),
            "ticker": str(candidate["ticker"]),
            "ret_5m": ret.get(5),
            "ret_30m": ret.get(30),
            "ret_60m": ret.get(60),
            "ret_120m": ret.get(120),
            "ret_close": ret_close,
            "mfe": max(mfe_values) if mfe_values else None,
            "mae": min(mae_values) if mae_values else None,
            "selected_by_live_claude": actual_selected,
            "live_trade_ready": actual_trade_ready,
            "ordered": actual_ordered,
            "updated_at": now_iso(),
            "candidate_id": cid,
            "latest_decision_id": int(decision["id"]) if decision else None,
            "outcome_status": status,
            "open_price": open_price,
            "close_price": close_price,
            "ret_90m": ret.get(90),
            "ret_150m": ret.get(150),
            "ret_180m": ret.get(180),
            "anchor_ret_5m": anchor_ret.get(5),
            "anchor_ret_30m": anchor_ret.get(30),
            "anchor_ret_60m": anchor_ret.get(60),
            "anchor_ret_120m": anchor_ret.get(120),
            "anchor_ret_close": anchor_ret_close,
            "mfe_open_basis": max(mfe_values) if mfe_values else None,
            "mae_open_basis": min(mae_values) if mae_values else None,
            "mfe_anchor_basis": max(anchor_mfe_values) if anchor_mfe_values else None,
            "mae_anchor_basis": min(anchor_mae_values) if anchor_mae_values else None,
            "outcome_samples_json": _json([dict(row) for row in snapshots]),
            "actual_selected": actual_selected,
            "actual_selection_rank": _int(source.get("actual_selection_rank")),
            "actual_trade_ready": actual_trade_ready,
            "actual_ordered": actual_ordered,
        }
        _upsert_outcome(conn, payload)


def _upsert_outcome(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    columns = list(payload.keys())
    update_columns = [col for col in columns if col not in {"id", "session_date", "market", "runtime_mode", "ticker"}]
    conn.execute(
        f"""
        INSERT INTO preopen_outcomes ({', '.join(columns)})
        VALUES ({', '.join('?' for _ in columns)})
        ON CONFLICT(session_date, market, runtime_mode, ticker) DO UPDATE SET
        {', '.join(f'{col}=excluded.{col}' for col in update_columns)}
        """,
        [payload[col] for col in columns],
    )


def _daily_called_count(conn: sqlite3.Connection, session_date: str, market: str, runtime_mode: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM preopen_claude_checks
        WHERE session_date=? AND market=? AND runtime_mode=? AND status IN ('called', 'parse_failed') AND smart_skip=0
        """,
        (session_date, market, runtime_mode),
    ).fetchone()
    return int(row["c"] if row else 0)


def _next_check_attempt_no(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    market: str,
    runtime_mode: str,
    offset_min: int,
    fingerprint: str,
) -> int:
    row = conn.execute(
        """
        SELECT MAX(attempt_no) AS max_attempt
        FROM preopen_claude_checks
        WHERE session_date=? AND market=? AND runtime_mode=? AND eval_offset_min=? AND fingerprint=?
        """,
        (session_date, market, runtime_mode, int(offset_min), fingerprint),
    ).fetchone()
    return int(row["max_attempt"] or 0) + 1 if row else 1


def _feature_hash(row: sqlite3.Row) -> str:
    payload = {
        "offset": _row_value(row, "offset_min"),
        "ret": bucket_pct(_row_value(row, "return_from_open_pct")),
        "mfe": bucket_pct(_row_value(row, "mfe_from_open_pct")),
        "mae": bucket_pct(_row_value(row, "mae_from_open_pct")),
        "volume": bucket_dollar_volume(_row_value(row, "feature_volume")),
        "status": _row_value(row, "snapshot_status"),
    }
    return _sha256_json(payload)


def build_eval_cases(
    db_path: str | Path | None,
    *,
    session_date: str,
    market: str,
    mode: str = "live",
    offset_min: int,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> tuple[list[EvalCase], str]:
    market_key = normalize_market(market)
    runtime_mode = normalize_mode(mode)
    path = _resolve_db_path(db_path)
    if not path.exists():
        return [], "db_missing"
    try:
        with _readonly_db(path) as conn:
            rows = conn.execute(
                """
                SELECT c.*, fs.offset_min, fs.return_from_open_pct, fs.mfe_from_open_pct,
                       fs.mae_from_open_pct, fs.volume AS feature_volume,
                       fs.snapshot_status
                FROM preopen_candidates c
                LEFT JOIN preopen_feature_snapshots fs
                  ON fs.candidate_id=c.id AND fs.offset_min=?
                WHERE c.session_date=? AND c.market=? AND c.runtime_mode=? AND c.eligible=1
                ORDER BY c.deterministic_score DESC, c.preopen_rank ASC, c.ticker ASC
                """,
                (int(offset_min), session_date, market_key, runtime_mode),
            ).fetchall()
    except sqlite3.Error:
        return [], "db_schema_unavailable"
    cases: list[EvalCase] = []
    for row in rows:
        if row["snapshot_status"] != "sampled":
            continue
        idx = len(cases) + 1
        if idx > int(max_candidates):
            break
        case_id = f"C{idx:02d}"
        risk_tags = []
        try:
            risk_tags = json.loads(row["risk_tags_json"] or "[]")
        except Exception:
            risk_tags = []
        liquidity_bucket = _liquidity_bucket(market_key, row["extended_dollar_volume"])
        payload = {
            "id": case_id,
            "market": market_key,
            "gap": row["gap_pct"],
            "rank": row["preopen_rank"],
            "dv_bucket": bucket_dollar_volume(row["extended_dollar_volume"]) if market_key == "US" else None,
            "liquidity_bucket": liquidity_bucket,
            "news": bool(row["news_or_earnings_flag"]),
            "r30": row["return_from_open_pct"],
            "mfe30": row["mfe_from_open_pct"],
            "mae30": row["mae_from_open_pct"],
            "risk": risk_tags,
        }
        cases.append(
            EvalCase(
                case_id=case_id,
                candidate_id=int(row["id"]),
                ticker=str(row["ticker"]),
                rank_in_prompt=idx,
                payload=payload,
                visible_feature_hash=_feature_hash(row),
            )
        )
    if not rows:
        readiness = "no_eligible_candidates"
    elif not cases:
        readiness = "feature_not_ready"
    else:
        readiness = "ready"
    return cases, readiness


def fingerprint_cases(*, session_date: str, market: str, offset_min: int, cases: list[EvalCase]) -> str:
    payload = {
        "schema": "preopen_continuation_shadow.fp.v1",
        "session_date": session_date,
        "market": normalize_market(market),
        "offset_min": int(offset_min),
        "cases": [
            {
                "t": case.ticker,
                "gap": bucket_pct(_num(case.payload.get("gap")), (2, 5, 10, 20)),
                "dv": case.payload.get("dv_bucket"),
                "liquidity": case.payload.get("liquidity_bucket"),
                "rank": bucket_rank(_int(case.payload.get("rank"))),
                "news": 1 if case.payload.get("news") else 0,
                "r30": bucket_pct(_num(case.payload.get("r30"))),
                "mfe30": bucket_pct(_num(case.payload.get("mfe30"))),
                "mae30": bucket_pct(_num(case.payload.get("mae30"))),
                "risk": sorted(str(x) for x in (case.payload.get("risk") or [])),
            }
            for case in cases
        ],
    }
    return _sha256_json(payload)


def build_prompt(cases: list[EvalCase]) -> str:
    markets = sorted({str(case.payload.get("market") or "") for case in cases if case.payload.get("market")})
    market_label = "/".join(markets) if markets else "KR/US"
    payload = {
        "task": f"Judge {market_label} preopen continuation candidates using only visible early-session features.",
        "schema": {"cases": [["case_id", "PROMOTE|KEEP|DROP", "confidence_0_to_1", "REASON_CODE"]]},
        "rules": [
            "Return strict JSON only.",
            "No markdown.",
            "No explanation outside JSON.",
            "Do not infer from future outcomes.",
            "Action ceiling is WATCH.",
            "For US cases, dv_bucket is an extended dollar-volume bucket.",
            "For KR cases, liquidity_bucket is a KRW traded-value bucket.",
        ],
        "cases": [case.payload for case in cases],
    }
    return _json(payload)


def parse_claude_response(raw: str, expected_case_ids: list[str]) -> list[dict[str, Any]]:
    text = str(raw or "").strip()
    if not text or not text.startswith("{") or not text.endswith("}"):
        raise ParseError("strict_json_required")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"json_decode_error:{exc.msg}") from exc
    if set(payload.keys()) != {"cases"}:
        raise ParseError("top_level_cases_only")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ParseError("cases_must_be_list")
    expected = set(expected_case_ids)
    seen: set[str] = set()
    parsed: list[dict[str, Any]] = []
    for item in cases:
        if isinstance(item, list) and len(item) == 4:
            case_id, decision, confidence, reason_code = item
        elif isinstance(item, dict):
            case_id = item.get("id")
            decision = item.get("d") or item.get("decision")
            confidence = item.get("c") if "c" in item else item.get("confidence")
            reason_code = item.get("rc") or item.get("reason_code")
        else:
            raise ParseError("case_shape_invalid")
        case_id = str(case_id or "").strip()
        if case_id not in expected:
            raise ParseError(f"unknown_case_id:{case_id}")
        if case_id in seen:
            raise ParseError(f"duplicate_case_id:{case_id}")
        seen.add(case_id)
        decision = str(decision or "").strip().upper()
        if decision not in {"PROMOTE", "KEEP", "DROP"}:
            raise ParseError(f"invalid_decision:{decision}")
        conf = _num(confidence)
        if conf is None or conf < 0.0 or conf > 1.0:
            raise ParseError(f"invalid_confidence:{case_id}")
        parsed.append(
            {
                "case_id": case_id,
                "decision": decision,
                "confidence": conf,
                "reason_code": str(reason_code or "").strip()[:80],
                "raw_decision": str(decision),
                "raw_confidence": str(confidence),
                "payload": item,
            }
        )
    missing = expected - seen
    if missing:
        raise ParseError(f"missing_case_id:{','.join(sorted(missing))}")
    return parsed


def _insert_check(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    payload = dict(payload)
    payload.setdefault("created_at", now_iso())
    columns = list(payload.keys())
    conn.execute(
        f"INSERT INTO preopen_claude_checks ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        [payload[col] for col in columns],
    )
    return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])


def _insert_decisions(conn: sqlite3.Connection, check_id: int, cases: list[EvalCase], decisions: list[dict[str, Any]], *, session_date: str, market: str, offset_min: int) -> None:
    case_by_id = {case.case_id: case for case in cases}
    for decision in decisions:
        case = case_by_id[decision["case_id"]]
        conn.execute(
            """
            INSERT OR REPLACE INTO preopen_claude_decisions
            (check_id, session_date, market, ticker, visible_offset_min, decision, confidence,
             reason_code, action_ceiling, would_inject_candidate_pool, actually_injected,
             created_at, candidate_id, case_id, rank_in_prompt, raw_decision, raw_confidence,
             parse_warning, visible_feature_hash, decision_payload_json, candidate_pool_role,
             discovery_signal_family, discovery_action_ceiling, injection_eligible_after_shadow)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'WATCH', ?, 0, ?, ?, ?, ?, ?, ?, '', ?, ?, 'SHADOW_ONLY',
                    'preopen_continuation', 'WATCH', ?)
            """,
            (
                check_id,
                session_date,
                market,
                case.ticker,
                int(offset_min),
                decision["decision"],
                decision["confidence"],
                decision["reason_code"],
                1 if decision["decision"] in {"PROMOTE", "KEEP"} else 0,
                now_iso(),
                case.candidate_id,
                case.case_id,
                case.rank_in_prompt,
                decision["raw_decision"],
                decision["raw_confidence"],
                case.visible_feature_hash,
                _json(decision["payload"]),
                1 if decision["decision"] in {"PROMOTE", "KEEP"} else 0,
            ),
        )
        conn.execute(
            "UPDATE preopen_candidates SET evaluation_case_id=?, updated_at=? WHERE id=?",
            (case.case_id, now_iso(), case.candidate_id),
        )


def _fingerprint_seen(conn: sqlite3.Connection, session_date: str, market: str, runtime_mode: str, offset_min: int, fingerprint: str) -> bool:
    return bool(
        conn.execute(
            """
            SELECT 1 FROM preopen_claude_checks
            WHERE session_date=? AND market=? AND runtime_mode=? AND eval_offset_min=? AND fingerprint=?
              AND parse_ok=1 AND status='called'
            LIMIT 1
            """,
            (session_date, market, runtime_mode, int(offset_min), fingerprint),
        ).fetchone()
    )


def _call_claude(prompt: str, *, model: str, max_tokens: int) -> tuple[str, int, int, int]:
    start = datetime.now()
    import anthropic

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    resp = client.messages.create(
        model=model,
        max_tokens=int(max_tokens),
        messages=[{"role": "user", "content": prompt}],
        extra_body=thinking_extra_body("continuation_shadow"),
    )
    text_parts = []
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) != "text":
            continue
        text = getattr(block, "text", None)
        if text:
            text_parts.append(text)
    raw = "\n".join(text_parts)
    usage = getattr(resp, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    duration_ms = int((datetime.now() - start).total_seconds() * 1000)
    return raw, input_tokens, output_tokens, duration_ms


def resolve_offset_min(offset: str | int | None, *, market: str, session_date: str | None = None) -> int:
    raw = str(offset if offset is not None else "30").strip().lower()
    market_key = normalize_market(market)
    resolved_date = session_date or resolve_session_date_str(market_key)
    if raw in {"close", "closing", "end", "eod", "종가"}:
        close_offset = _close_offset_min(market_key, resolved_date)
        if close_offset is None:
            raise ContinuationShadowError("preopen_continuation_close_offset_unavailable")
        return int(close_offset)
    try:
        parsed = int(float(raw))
    except Exception as exc:
        raise ContinuationShadowError(f"preopen_continuation_invalid_offset:{offset}") from exc
    if parsed <= 0:
        raise ContinuationShadowError(f"preopen_continuation_invalid_offset:{offset}")
    return parsed


def _repair_prompt(raw_response: str, expected_case_ids: list[str]) -> str:
    return _json(
        {
            "task": "Repair the previous response into the strict JSON contract.",
            "schema": {"cases": [["case_id", "PROMOTE|KEEP|DROP", "confidence_0_to_1", "REASON_CODE"]]},
            "expected_case_ids": expected_case_ids,
            "rules": ["Return JSON only.", "Do not add explanation.", "Do not invent unknown case ids."],
            "previous_raw_response": str(raw_response or "")[:3000],
        }
    )


def _save_eval_raw_call(
    *,
    prompt: str,
    raw: str,
    parsed: list[dict[str, Any]],
    input_tokens: int,
    output_tokens: int,
    market: str,
    session_date: str,
    model: str,
    parse_ok: bool,
    duration_ms: int,
) -> str:
    try:
        from minority_report.raw_call_logger import save as save_raw_call

        path = save_raw_call(
            EVAL_LABEL,
            prompt,
            raw,
            {"cases": parsed},
            input_tokens,
            output_tokens,
            market=market,
            call_date=session_date,
            model=model,
            parse_error=not bool(parse_ok),
            parse_stage="preopen_continuation_eval",
            duration_ms=duration_ms,
            prompt_version=PROMPT_VERSION,
        )
        return str(path or "")
    except Exception:
        return ""


def _record_eval_credit(input_tokens: int, output_tokens: int, model: str) -> None:
    try:
        from credit_tracker import record as credit_record

        credit_record(input_tokens, output_tokens, EVAL_LABEL, model=model)
    except Exception:
        pass


def _parse_response_or_error(raw: str, cases: list[EvalCase]) -> tuple[list[dict[str, Any]], str, int]:
    try:
        return parse_claude_response(raw, [case.case_id for case in cases]), "", 1
    except ParseError as exc:
        return [], str(exc)[:240], 0


def _insert_eval_attempt_check(
    conn: sqlite3.Connection,
    *,
    base_check: dict[str, Any],
    raw_call_path: str,
    input_tokens: int,
    output_tokens: int,
    parse_ok: int,
    parse_error: str,
    raw: str,
    duration_ms: int,
    throttle: dict[str, Any],
    attempt_no: int,
    retry_of_check_id: int | None = None,
    daily_call_count_after: int | None = None,
) -> int:
    daily_after = (
        int(daily_call_count_after)
        if daily_call_count_after is not None
        else int(base_check.get("daily_call_count_before") or 0)
    )
    return _insert_check(
        conn,
        {
            **base_check,
            "attempt_no": int(attempt_no),
            "retry_of_check_id": retry_of_check_id,
            "smart_skip": 0,
            "skip_reason": "",
            "raw_call_path": raw_call_path,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "parse_ok": parse_ok,
            "parse_error": parse_error,
            "status": "called" if parse_ok else "parse_failed",
            "duration_ms": duration_ms,
            "response_hash": _sha256_text(raw),
            "response_chars": len(raw),
            "throttle_enabled": _bool_int(throttle.get("enabled")),
            "throttle_allowed": _bool_int(throttle.get("allowed")),
            "throttle_tier": str(throttle.get("tier") or ""),
            "daily_call_count_after": daily_after,
        },
    )


def run_eval(
    market: str,
    *,
    offset_min: int = 30,
    session_date: str | None = None,
    mode: str = "live",
    db_path: str | Path | None = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    no_claude: bool = False,
    dry_run: bool = False,
    model: str | None = None,
    max_tokens: int = 1200,
) -> dict[str, Any]:
    market_key = normalize_market(market)
    runtime_mode = normalize_mode(mode)
    session_date = session_date or resolve_session_date_str(market_key)
    if not dry_run:
        init_schema(db_path)
    cases, readiness = build_eval_cases(
        db_path,
        session_date=session_date,
        market=market_key,
        mode=runtime_mode,
        offset_min=int(offset_min),
        max_candidates=int(max_candidates),
    )
    fingerprint = fingerprint_cases(session_date=session_date, market=market_key, offset_min=int(offset_min), cases=cases)
    prompt = build_prompt(cases)
    case_map = {case.case_id: {"ticker": case.ticker, "candidate_id": case.candidate_id} for case in cases}
    if dry_run:
        return {
            "market": market_key,
            "mode": runtime_mode,
            "session_date": session_date,
            "offset_min": int(offset_min),
            "readiness": readiness,
            "candidate_count": len(cases),
            "fingerprint": fingerprint,
            "prompt": prompt,
            "dry_run": True,
        }
    with _db(db_path) as conn:
        run_id = _start_run(
            conn,
            session_date=session_date,
            market=market_key,
            runtime_mode=runtime_mode,
            step="eval",
            offset_min=int(offset_min),
            config={"max_candidates": max_candidates, "no_claude": no_claude},
        )
        daily_before = _daily_called_count(conn, session_date, market_key, runtime_mode)
        base_attempt_no = _next_check_attempt_no(
            conn,
            session_date=session_date,
            market=market_key,
            runtime_mode=runtime_mode,
            offset_min=int(offset_min),
            fingerprint=fingerprint,
        )
        base_check = {
            "session_date": session_date,
            "market": market_key,
            "eval_offset_min": int(offset_min),
            "prompt_version": PROMPT_VERSION,
            "model": model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            "candidate_count": len(cases),
            "fingerprint": fingerprint,
            "created_at": now_iso(),
            "run_id": run_id,
            "runtime_mode": runtime_mode,
            "attempt_no": base_attempt_no,
            "max_tokens": int(max_tokens),
            "prompt_hash": _sha256_text(prompt),
            "prompt_chars": len(prompt),
            "prompt_case_count": len(cases),
            "case_map_json": _json(case_map),
            "daily_call_count_before": daily_before,
            "daily_call_count_after": daily_before,
        }
        if readiness != "ready":
            check_id = _insert_check(conn, {**base_check, "smart_skip": 1, "skip_reason": readiness, "parse_ok": 0, "status": "skipped"})
            _finish_run(conn, run_id, status="skipped", evaluated_count=0)
            return {"status": "skipped", "skip_reason": readiness, "check_id": check_id, "candidate_count": len(cases)}
        if _fingerprint_seen(conn, session_date, market_key, runtime_mode, int(offset_min), fingerprint):
            check_id = _insert_check(conn, {**base_check, "smart_skip": 1, "skip_reason": "fingerprint_seen", "parse_ok": 1, "status": "skipped"})
            _finish_run(conn, run_id, status="skipped", evaluated_count=len(cases))
            return {"status": "skipped", "skip_reason": "fingerprint_seen", "check_id": check_id, "candidate_count": len(cases)}
        daily_cap = int(os.getenv("PREOPEN_CONTINUATION_CLAUDE_MAX_CALLS_PER_DAY", "1") or "1")
        if daily_before >= daily_cap:
            check_id = _insert_check(conn, {**base_check, "smart_skip": 1, "skip_reason": "daily_call_cap", "parse_ok": 0, "status": "skipped"})
            _finish_run(conn, run_id, status="skipped", evaluated_count=len(cases))
            return {"status": "skipped", "skip_reason": "daily_call_cap", "check_id": check_id, "candidate_count": len(cases)}
        if no_claude or str(os.getenv("PREOPEN_CONTINUATION_CLAUDE_EVAL_ENABLED", "true")).lower() in {"0", "false", "no", "off"}:
            reason = "no_claude" if no_claude else "claude_eval_disabled"
            check_id = _insert_check(conn, {**base_check, "smart_skip": 1, "skip_reason": reason, "parse_ok": 0, "status": "skipped"})
            _finish_run(conn, run_id, status="skipped", evaluated_count=len(cases))
            return {"status": "skipped", "skip_reason": reason, "check_id": check_id, "candidate_count": len(cases)}
        if not os.getenv("ANTHROPIC_API_KEY"):
            check_id = _insert_check(
                conn,
                {**base_check, "smart_skip": 1, "skip_reason": "api_error:missing_api_key", "parse_ok": 0, "status": "api_error", "api_error_type": "missing_api_key"},
            )
            _finish_run(conn, run_id, status="skipped", evaluated_count=len(cases))
            return {"status": "api_error", "skip_reason": "missing_api_key", "check_id": check_id, "candidate_count": len(cases)}
        throttle = {"enabled": False, "allowed": True, "tier": "off"}
        try:
            from credit_tracker import throttle_state

            throttle = throttle_state(label=EVAL_LABEL)
        except Exception:
            throttle = {"enabled": False, "allowed": True, "tier": "unavailable"}
        if not throttle.get("allowed", True):
            check_id = _insert_check(
                conn,
                {
                    **base_check,
                    "smart_skip": 1,
                    "skip_reason": "budget_throttle",
                    "parse_ok": 0,
                    "status": "skipped",
                    "throttle_enabled": _bool_int(throttle.get("enabled")),
                    "throttle_allowed": 0,
                    "throttle_tier": str(throttle.get("tier") or ""),
                },
            )
            _finish_run(conn, run_id, status="skipped", evaluated_count=len(cases))
            return {"status": "skipped", "skip_reason": "budget_throttle", "check_id": check_id, "candidate_count": len(cases)}
        try:
            raw, input_tokens, output_tokens, duration_ms = _call_claude(prompt, model=base_check["model"], max_tokens=int(max_tokens))
        except Exception as exc:
            check_id = _insert_check(
                conn,
                {
                    **base_check,
                    "smart_skip": 1,
                    "skip_reason": "api_error:unknown",
                    "parse_ok": 0,
                    "status": "api_error",
                    "api_error_type": "unknown",
                    "api_error_message": str(exc)[:240],
                },
            )
            _finish_run(conn, run_id, status="error", error_type="api_error", error_message=str(exc)[:240], evaluated_count=len(cases))
            return {"status": "api_error", "error": str(exc)[:240], "check_id": check_id, "candidate_count": len(cases)}
        parsed, parse_error, parse_ok = _parse_response_or_error(raw, cases)
        raw_path = _save_eval_raw_call(
            prompt=prompt,
            raw=raw,
            parsed=parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            market=market_key,
            session_date=session_date,
            model=base_check["model"],
            parse_ok=bool(parse_ok),
            duration_ms=duration_ms,
        )
        _record_eval_credit(input_tokens, output_tokens, base_check["model"])
        check_id = _insert_eval_attempt_check(
            conn,
            base_check=base_check,
            raw_call_path=raw_path,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            parse_ok=parse_ok,
            parse_error=parse_error,
            raw=raw,
            duration_ms=duration_ms,
            throttle=throttle,
            attempt_no=base_attempt_no,
            daily_call_count_after=daily_before + 1,
        )
        if parse_ok:
            _insert_decisions(conn, check_id, cases, parsed, session_date=session_date, market=market_key, offset_min=int(offset_min))
            _refresh_outcomes(conn, session_date=session_date, market=market_key, runtime_mode=runtime_mode)
            _finish_run(conn, run_id, status="success", evaluated_count=len(cases))
            return {"status": "called", "check_id": check_id, "candidate_count": len(cases), "parse_ok": True}

        retry_max = int(os.getenv("PREOPEN_CONTINUATION_CLAUDE_RETRY_MAX", "1") or "1")
        if retry_max <= 0:
            _finish_run(conn, run_id, status="error", evaluated_count=len(cases), error_message=parse_error)
            return {"status": "parse_failed", "check_id": check_id, "candidate_count": len(cases), "parse_ok": False, "parse_error": parse_error}

        repair = _repair_prompt(raw, [case.case_id for case in cases])
        try:
            retry_raw, retry_input, retry_output, retry_duration = _call_claude(
                repair,
                model=base_check["model"],
                max_tokens=int(max_tokens),
            )
        except Exception as exc:
            retry_check_id = _insert_check(
                conn,
                {
                    **base_check,
                    "attempt_no": base_attempt_no + 1,
                    "retry_of_check_id": check_id,
                    "smart_skip": 1,
                    "skip_reason": "api_error:retry_unknown",
                    "parse_ok": 0,
                    "status": "api_error",
                    "api_error_type": "unknown",
                    "api_error_message": str(exc)[:240],
                },
            )
            _finish_run(conn, run_id, status="error", error_type="api_error", error_message=str(exc)[:240], evaluated_count=len(cases))
            return {
                "status": "api_error",
                "error": str(exc)[:240],
                "check_id": retry_check_id,
                "retry_of_check_id": check_id,
                "candidate_count": len(cases),
            }

        retry_parsed, retry_parse_error, retry_parse_ok = _parse_response_or_error(retry_raw, cases)
        retry_raw_path = _save_eval_raw_call(
            prompt=repair,
            raw=retry_raw,
            parsed=retry_parsed,
            input_tokens=retry_input,
            output_tokens=retry_output,
            market=market_key,
            session_date=session_date,
            model=base_check["model"],
            parse_ok=bool(retry_parse_ok),
            duration_ms=retry_duration,
        )
        _record_eval_credit(retry_input, retry_output, base_check["model"])
        retry_check_id = _insert_eval_attempt_check(
            conn,
            base_check={
                **base_check,
                "prompt_hash": _sha256_text(repair),
                "prompt_chars": len(repair),
            },
            raw_call_path=retry_raw_path,
            input_tokens=retry_input,
            output_tokens=retry_output,
            parse_ok=retry_parse_ok,
            parse_error=retry_parse_error,
            raw=retry_raw,
            duration_ms=retry_duration,
            throttle=throttle,
            attempt_no=base_attempt_no + 1,
            retry_of_check_id=check_id,
            daily_call_count_after=daily_before + 2,
        )
        if retry_parse_ok:
            _insert_decisions(conn, retry_check_id, cases, retry_parsed, session_date=session_date, market=market_key, offset_min=int(offset_min))
            _refresh_outcomes(conn, session_date=session_date, market=market_key, runtime_mode=runtime_mode)
            _finish_run(conn, run_id, status="success", evaluated_count=len(cases))
            return {
                "status": "called",
                "check_id": retry_check_id,
                "retry_of_check_id": check_id,
                "candidate_count": len(cases),
                "parse_ok": True,
            }
        _finish_run(conn, run_id, status="error", evaluated_count=len(cases), error_message=retry_parse_error)
        return {
            "status": "parse_failed",
            "check_id": retry_check_id,
            "retry_of_check_id": check_id,
            "candidate_count": len(cases),
            "parse_ok": False,
            "parse_error": retry_parse_error,
        }


def _safe_avg(values: Iterable[Any]) -> float | None:
    nums = [_num(v) for v in values]
    nums = [v for v in nums if v is not None]
    if not nums:
        return None
    return round(mean(nums), 4)


def _win_rate(values: Iterable[Any]) -> float | None:
    nums = [_num(v) for v in values]
    nums = [v for v in nums if v is not None]
    if not nums:
        return None
    return round(sum(1 for v in nums if v > 0) / len(nums) * 100.0, 2)


def _percentile(values: list[float], pct: float) -> float | None:
    nums = sorted(v for v in values if v is not None)
    if not nums:
        return None
    if len(nums) == 1:
        return round(nums[0], 4)
    idx = max(0, min(len(nums) - 1, int(round((len(nums) - 1) * pct))))
    return round(nums[idx], 4)


def _claude_called_checks(checks: list[sqlite3.Row]) -> list[sqlite3.Row]:
    return [
        row
        for row in checks
        if str(row["status"] or "") in {"called", "parse_failed"}
        and int(row["smart_skip"] or 0) == 0
    ]


def _sample_rows_from_outcome(row: sqlite3.Row) -> list[dict[str, Any]]:
    raw = _row_value(row, "outcome_samples_json")
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)]


def _dense_curve_stats(rows: list[sqlite3.Row]) -> dict[str, Any]:
    all_offsets: set[int] = set()
    sampled_offsets: set[int] = set()
    sampled_snapshot_count = 0
    missing_snapshot_count = 0
    outcome_rows_with_samples = 0
    peak_offsets: list[float] = []
    trough_offsets: list[float] = []
    early_pop_fade_count = 0
    early_pop_count = 0

    for row in rows:
        samples = _sample_rows_from_outcome(row)
        if samples:
            outcome_rows_with_samples += 1
        sampled_returns: list[tuple[int, float]] = []
        for sample in samples:
            offset = _int(sample.get("offset_min"))
            if offset is None:
                continue
            all_offsets.add(int(offset))
            ret = _num(sample.get("return_from_open_pct"))
            status = str(sample.get("snapshot_status") or "")
            if status == "sampled" and ret is not None:
                sampled_offsets.add(int(offset))
                sampled_snapshot_count += 1
                sampled_returns.append((int(offset), float(ret)))
            else:
                missing_snapshot_count += 1
        if sampled_returns:
            peak_offsets.append(float(max(sampled_returns, key=lambda item: item[1])[0]))
            trough_offsets.append(float(min(sampled_returns, key=lambda item: item[1])[0]))
        ret_5m = _num(row["ret_5m"])
        ret_close = _num(row["ret_close"])
        if ret_5m is not None and ret_5m > 0:
            early_pop_count += 1
            if ret_close is not None and ret_close < 0:
                early_pop_fade_count += 1

    offset_count = len(all_offsets)
    sampled_offset_count = len(sampled_offsets)
    return {
        "outcome_rows": len(rows),
        "outcome_rows_with_samples": outcome_rows_with_samples,
        "offset_count": offset_count,
        "sampled_offset_count": sampled_offset_count,
        "first_offset_min": min(all_offsets) if all_offsets else None,
        "last_offset_min": max(all_offsets) if all_offsets else None,
        "sampled_snapshot_count": sampled_snapshot_count,
        "missing_snapshot_count": missing_snapshot_count,
        "avg_sampled_snapshots_per_outcome": (
            round(sampled_snapshot_count / len(rows), 2) if rows else None
        ),
        "avg_time_to_peak_min": _safe_avg(peak_offsets),
        "avg_time_to_trough_min": _safe_avg(trough_offsets),
        "p50_time_to_peak_min": _percentile(peak_offsets, 0.5),
        "p50_time_to_trough_min": _percentile(trough_offsets, 0.5),
        "early_pop_count": early_pop_count,
        "early_pop_fade_count": early_pop_fade_count,
        "early_pop_fade_rate": (
            round(early_pop_fade_count / early_pop_count * 100.0, 2)
            if early_pop_count
            else None
        ),
    }


def build_report_payload(
    db_path: str | Path | None = None,
    *,
    market: str = "US",
    mode: str = "live",
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    market_key = normalize_market(market)
    runtime_mode = normalize_mode(mode)
    path = _resolve_db_path(db_path)
    if not path.exists():
        return _empty_report_payload(market_key, mode=runtime_mode, date_from=date_from, date_to=date_to, missing_db=str(path))
    try:
        with _readonly_db(path) as conn:
            candidate_params: list[Any] = [market_key, runtime_mode]
            check_params: list[Any] = [market_key, runtime_mode]
            outcome_params: list[Any] = [market_key, runtime_mode]
            candidate_where = " AND runtime_mode=?"
            check_where = " AND ch.runtime_mode=?"
            outcome_where = " AND o.runtime_mode=?"
            if date_from:
                candidate_where += " AND session_date>=?"
                check_where += " AND ch.session_date>=?"
                outcome_where += " AND o.session_date>=?"
                candidate_params.append(date_from)
                check_params.append(date_from)
                outcome_params.append(date_from)
            if date_to:
                candidate_where += " AND session_date<=?"
                check_where += " AND ch.session_date<=?"
                outcome_where += " AND o.session_date<=?"
                candidate_params.append(date_to)
                check_params.append(date_to)
                outcome_params.append(date_to)
            candidates = conn.execute(
                f"SELECT * FROM preopen_candidates WHERE market=?{candidate_where}",
                candidate_params,
            ).fetchall()
            checks = conn.execute(
                f"SELECT * FROM preopen_claude_checks ch WHERE ch.market=?{check_where}",
                check_params,
            ).fetchall()
            rows = conn.execute(
                f"""
                SELECT o.*, COALESCE(d.decision, 'UNDECIDED') AS decision
                FROM preopen_outcomes o
                LEFT JOIN preopen_claude_decisions d ON d.id=o.latest_decision_id
                WHERE o.market=?{outcome_where}
                """,
                outcome_params,
            ).fetchall()
    except sqlite3.Error as exc:
        return _empty_report_payload(
            market_key,
            mode=runtime_mode,
            date_from=date_from,
            date_to=date_to,
            schema_error=str(exc)[:240],
        )
    by_decision: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_decision.setdefault(str(row["decision"] or "UNDECIDED"), []).append(row)
    decision_stats = {}
    for decision, items in sorted(by_decision.items()):
        decision_stats[decision] = {
            "count": len(items),
            "avg_ret_30m": _safe_avg(row["ret_30m"] for row in items),
            "avg_ret_60m": _safe_avg(row["ret_60m"] for row in items),
            "avg_ret_120m": _safe_avg(row["ret_120m"] for row in items),
            "avg_ret_close": _safe_avg(row["ret_close"] for row in items),
            "win_rate_close": _win_rate(row["ret_close"] for row in items),
            "avg_mfe": _safe_avg(row["mfe"] for row in items),
            "avg_mae": _safe_avg(row["mae"] for row in items),
            "bad_rate_close": _win_rate((-_num(row["ret_close"]) if _num(row["ret_close"]) is not None else None) for row in items),
        }
    called = _claude_called_checks(checks)
    parse_success = [row for row in called if int(row["parse_ok"] or 0) == 1]
    skip_reasons: dict[str, int] = {}
    for row in checks:
        reason = str(row["skip_reason"] or "")
        if reason:
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
    five_up_mae = [
        _num(row["mae"])
        for row in rows
        if _num(row["ret_5m"]) is not None and _num(row["ret_5m"]) > 0 and _num(row["mae"]) is not None
    ]
    five_up_mae_nums = [float(v) for v in five_up_mae if v is not None]
    complete_count = sum(1 for row in rows if _num(row["ret_close"]) is not None)
    complete_rate = round((complete_count / len(rows) * 100.0), 2) if rows else None
    dense_curve = _dense_curve_stats(rows)
    recommendation = _recommendation(candidates=candidates, rows=rows, decision_stats=decision_stats, checks=checks)
    return {
        "market": market_key,
        "mode": runtime_mode,
        "date_from": date_from,
        "date_to": date_to,
        "candidate_count": len(candidates),
        "eligible_count": sum(1 for row in candidates if int(row["eligible"] or 0) == 1),
        "outcome_count": len(rows),
        "outcome_complete_rate": complete_rate,
        "decision_stats": decision_stats,
        "claude": {
            "checks": len(checks),
            "called": len(called),
            "skipped": sum(1 for row in checks if int(row["smart_skip"] or 0) == 1),
            "parse_success_rate": round((len(parse_success) / len(called) * 100.0), 2) if called else None,
            "input_tokens": sum(int(row["input_tokens"] or 0) for row in checks),
            "output_tokens": sum(int(row["output_tokens"] or 0) for row in checks),
            "skip_reasons": skip_reasons,
        },
        "five_min_up_mae": {
            "count": len(five_up_mae_nums),
            "avg": _safe_avg(five_up_mae_nums),
            "median": _percentile(five_up_mae_nums, 0.5),
            "p10": _percentile(five_up_mae_nums, 0.1),
            "p25": _percentile(five_up_mae_nums, 0.25),
        },
        "dense_curve": dense_curve,
        "recommendation": recommendation,
    }


def _empty_report_payload(
    market: str,
    *,
    mode: str = "live",
    date_from: str | None = None,
    date_to: str | None = None,
    missing_db: str = "",
    schema_error: str = "",
) -> dict[str, Any]:
    payload = {
        "market": market,
        "mode": normalize_mode(mode),
        "date_from": date_from,
        "date_to": date_to,
        "candidate_count": 0,
        "eligible_count": 0,
        "outcome_count": 0,
        "outcome_complete_rate": None,
        "decision_stats": {},
        "claude": {
            "checks": 0,
            "called": 0,
            "skipped": 0,
            "parse_success_rate": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "skip_reasons": {},
        },
        "five_min_up_mae": {"count": 0, "avg": None, "median": None, "p10": None, "p25": None},
        "dense_curve": _dense_curve_stats([]),
        "recommendation": "shadow_continue",
    }
    if missing_db:
        payload["missing_db"] = missing_db
    if schema_error:
        payload["schema_error"] = schema_error
    return payload


def _recommendation(*, candidates: list[sqlite3.Row], rows: list[sqlite3.Row], decision_stats: dict[str, dict[str, Any]], checks: list[sqlite3.Row]) -> str:
    eligible_count = sum(1 for row in candidates if int(row["eligible"] or 0) == 1)
    evaluated_count = sum(1 for row in rows if str(row["decision"] or "") in {"PROMOTE", "KEEP", "DROP"})
    complete_count = sum(1 for row in rows if _num(row["ret_close"]) is not None)
    promote_count = int(decision_stats.get("PROMOTE", {}).get("count") or 0)
    drop_count = int(decision_stats.get("DROP", {}).get("count") or 0)
    called = _claude_called_checks(checks)
    parse_rate = (sum(1 for row in called if int(row["parse_ok"] or 0) == 1) / len(called) * 100.0) if called else 100.0
    if eligible_count < 50 or evaluated_count < 35 or promote_count < 8 or drop_count < 8 or parse_rate < 95.0:
        return "shadow_continue"
    if not rows or (complete_count / len(rows) * 100.0) < 90.0:
        return "shadow_continue"
    pk = [row for row in rows if str(row["decision"] or "") in {"PROMOTE", "KEEP"}]
    drop = [row for row in rows if str(row["decision"] or "") == "DROP"]
    if not pk or not drop:
        return "shadow_continue"
    pk_avg = [_safe_avg(row[key] for row in pk) for key in ("ret_60m", "ret_120m", "ret_close")]
    drop_avg = [_safe_avg(row[key] for row in drop) for key in ("ret_60m", "ret_120m", "ret_close")]
    better_axes = sum(1 for left, right in zip(pk_avg, drop_avg) if left is not None and right is not None and left > right)
    drop_returns = [_num(row["ret_close"]) for row in drop]
    drop_returns = [v for v in drop_returns if v is not None]
    drop_bad_rate = (sum(1 for v in drop_returns if v < 0) / len(drop_returns) * 100.0) if drop_returns else 0.0
    if better_axes >= 2 and drop_bad_rate >= 60.0:
        return "consider_discovery_watch"
    if better_axes == 0:
        return "block_or_discard"
    return "shadow_continue"


def render_report_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Preopen Continuation Shadow Report",
        "",
        f"- market: {payload.get('market')}",
        f"- mode: {payload.get('mode')}",
        f"- range: {payload.get('date_from') or '-'} ~ {payload.get('date_to') or '-'}",
        f"- recommendation: `{payload.get('recommendation')}`",
        "",
        "## Summary",
        "",
        f"- candidates: {payload.get('candidate_count')}",
        f"- eligible: {payload.get('eligible_count')}",
        f"- outcomes: {payload.get('outcome_count')}",
        f"- outcome complete rate: {payload.get('outcome_complete_rate')}",
        f"- Claude checks: {payload.get('claude', {}).get('checks')}",
        f"- Claude called: {payload.get('claude', {}).get('called')}",
        f"- Claude skipped: {payload.get('claude', {}).get('skipped')}",
        f"- parse success rate: {payload.get('claude', {}).get('parse_success_rate')}",
        "",
        "## Decision Stats",
        "",
        "| decision | count | ret30 | ret60 | ret120 | close | win_close | mfe | mae |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for decision, stats in payload.get("decision_stats", {}).items():
        lines.append(
            "| {decision} | {count} | {avg_ret_30m} | {avg_ret_60m} | {avg_ret_120m} | {avg_ret_close} | {win_rate_close} | {avg_mfe} | {avg_mae} |".format(
                decision=decision,
                **stats,
            )
        )
    mae = payload.get("five_min_up_mae", {})
    dense = payload.get("dense_curve", {})
    lines.extend(
        [
            "",
            "## 5m Up MAE",
            "",
            f"- count: {mae.get('count')}",
            f"- avg: {mae.get('avg')}",
            f"- median: {mae.get('median')}",
            f"- p10: {mae.get('p10')}",
            f"- p25: {mae.get('p25')}",
            "",
            "## Dense Curve",
            "",
            f"- sampled snapshots: {dense.get('sampled_snapshot_count')}",
            f"- missing snapshots: {dense.get('missing_snapshot_count')}",
            f"- sampled offsets: {dense.get('sampled_offset_count')} / {dense.get('offset_count')}",
            f"- offset range: {dense.get('first_offset_min')} ~ {dense.get('last_offset_min')} min",
            f"- avg sampled snapshots per outcome: {dense.get('avg_sampled_snapshots_per_outcome')}",
            f"- avg time to peak: {dense.get('avg_time_to_peak_min')} min",
            f"- avg time to trough: {dense.get('avg_time_to_trough_min')} min",
            f"- 5m up then close fade: {dense.get('early_pop_fade_count')} / {dense.get('early_pop_count')} ({dense.get('early_pop_fade_rate')})",
            "",
            "## Skip Reasons",
            "",
        ]
    )
    skip_reasons = payload.get("claude", {}).get("skip_reasons") or {}
    if not skip_reasons:
        lines.append("- none")
    else:
        for reason, count in sorted(skip_reasons.items()):
            lines.append(f"- {reason}: {count}")
    lines.append("")
    return "\n".join(lines)


def write_report_markdown(payload: dict[str, Any], output_path: str | Path | None = None) -> Path:
    if output_path is None:
        today = datetime.now(KST).strftime("%Y%m%d")
        output_path = get_runtime_path("docs", "reports", f"preopen_continuation_shadow_report_{today}.md")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report_markdown(payload), encoding="utf-8")
    return path


def backfill_outcomes(
    market: str,
    *,
    session_date: str | None = None,
    mode: str = "live",
    db_path: str | Path | None = None,
    ticker_selection_db_path: str | Path | None = None,
    candidate_audit_db_path: str | Path | None = None,
    ml_decisions_db_path: str | Path | None = None,
) -> dict[str, Any]:
    market_key = normalize_market(market)
    runtime_mode = normalize_mode(mode)
    init_schema(db_path)
    with _db(db_path) as conn:
        if session_date:
            dates = [session_date]
        else:
            dates = [
                str(row["session_date"])
                for row in conn.execute(
                    "SELECT DISTINCT session_date FROM preopen_candidates WHERE market=? AND runtime_mode=? ORDER BY session_date",
                    (market_key, runtime_mode),
                ).fetchall()
            ]
        updated = 0
        for date_value in dates:
            _refresh_outcomes(conn, session_date=date_value, market=market_key, runtime_mode=runtime_mode)
            updated += _backfill_selection_links(
                conn,
                session_date=date_value,
                market=market_key,
                runtime_mode=runtime_mode,
                ticker_selection_db_path=ticker_selection_db_path,
                candidate_audit_db_path=candidate_audit_db_path,
                ml_decisions_db_path=ml_decisions_db_path,
            )
    return {"market": market_key, "mode": runtime_mode, "session_date": session_date or "ALL", "updated": updated}


def _backfill_selection_links(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    market: str,
    runtime_mode: str,
    ticker_selection_db_path: str | Path | None,
    candidate_audit_db_path: str | Path | None,
    ml_decisions_db_path: str | Path | None,
) -> int:
    selection = _load_ticker_selection_map(
        ticker_selection_db_path,
        session_date=session_date,
        market=market,
        runtime_mode=runtime_mode,
    )
    audit = _load_candidate_audit_map(
        candidate_audit_db_path,
        session_date=session_date,
        market=market,
        runtime_mode=runtime_mode,
    )
    ml_decisions = _load_ml_decision_map(
        ml_decisions_db_path,
        session_date=session_date,
        market=market,
        runtime_mode=runtime_mode,
    )
    outcomes = conn.execute(
        "SELECT * FROM preopen_outcomes WHERE session_date=? AND market=? AND runtime_mode=?",
        (session_date, market, runtime_mode),
    ).fetchall()
    updated = 0
    for row in outcomes:
        ticker = str(row["ticker"])
        sel = selection.get(ticker, {})
        aud = audit.get(ticker, {})
        ml = ml_decisions.get(ticker, {})
        if not sel and not aud and not ml:
            continue
        actual_selected = 1 if sel or aud.get("claude_watchlist") or ml else int(row["actual_selected"] or 0)
        actual_trade_ready = int(sel.get("trade_ready") or aud.get("claude_trade_ready") or row["actual_trade_ready"] or 0)
        actual_ordered = int(sel.get("traded") or ml.get("filled") or row["actual_ordered"] or 0)
        actual_selection_rank = _int(sel.get("selection_rank") or sel.get("watchlist_rank"))
        if actual_selection_rank is None:
            actual_selection_rank = _int(row["actual_selection_rank"])
        ticker_selection_log_id = _int(sel.get("id"))
        if ticker_selection_log_id is None:
            ticker_selection_log_id = _int(row["ticker_selection_log_id"])
        entry_price = _num(aud.get("entry_price"))
        if entry_price is None:
            entry_price = _num(ml.get("entry_price"))
        if entry_price is None:
            entry_price = _num(row["entry_price"])
        pnl_pct = _num(sel.get("pnl_pct") or aud.get("pnl_pct"))
        if pnl_pct is None:
            pnl_pct = _num(ml.get("pnl_pct"))
        if pnl_pct is None:
            pnl_pct = _num(row["pnl_pct"])
        conn.execute(
            """
            UPDATE preopen_outcomes
            SET actual_selected=?, selected_by_live_claude=?, actual_selection_rank=?,
                actual_trade_ready=?, live_trade_ready=?, actual_ordered=?, ordered=?,
                ticker_selection_log_id=?, audit_candidate_key=?, v2_decision_id=?,
                path_run_id=?, route_final_action=?, route_route=?, entry_price=?, pnl_pct=?,
                updated_at=?
            WHERE id=?
            """,
            (
                actual_selected,
                actual_selected,
                actual_selection_rank,
                actual_trade_ready,
                actual_trade_ready,
                actual_ordered,
                actual_ordered,
                ticker_selection_log_id,
                str(aud.get("candidate_key") or row["audit_candidate_key"] or ""),
                str(aud.get("v2_decision_id") or sel.get("execution_decision_id") or ml.get("v2_decision_id") or row["v2_decision_id"] or ""),
                str(aud.get("path_run_id") or ml.get("path_run_id") or row["path_run_id"] or ""),
                str(aud.get("route_final_action") or ml.get("route") or row["route_final_action"] or ""),
                str(aud.get("route_route") or ml.get("route") or row["route_route"] or ""),
                entry_price,
                pnl_pct,
                now_iso(),
                int(row["id"]),
            ),
        )
        updated += 1
    return updated


def _load_ticker_selection_map(
    path: str | Path | None,
    *,
    session_date: str,
    market: str,
    runtime_mode: str,
) -> dict[str, dict[str, Any]]:
    db = Path(path) if path else get_runtime_path("data", "ticker_selection_log.db", make_parents=False)
    if not db.exists():
        return {}
    try:
        with _readonly_db(db) as ro:
            columns = _table_columns(ro, "ticker_selection_log")
            where = ["date=?", "market=?"]
            params: list[Any] = [session_date, market]
            if "runtime_mode" in columns:
                where.append("runtime_mode=?")
                params.append(runtime_mode)
            elif "bot_mode" in columns:
                where.append("bot_mode=?")
                params.append(runtime_mode)
            rows = ro.execute(
                f"""
                SELECT *
                FROM ticker_selection_log
                WHERE {' AND '.join(where)}
                ORDER BY id DESC
                """,
                params,
            ).fetchall()
    except Exception:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row["ticker"] or "").upper()
        if ticker and ticker not in result:
            result[ticker] = dict(row)
    return result


def _load_ml_decision_map(
    path: str | Path | None,
    *,
    session_date: str,
    market: str,
    runtime_mode: str,
) -> dict[str, dict[str, Any]]:
    db = Path(path) if path else get_runtime_path("data", "ml", "decisions.db", make_parents=False)
    if not db.exists():
        return {}
    try:
        with _readonly_db(db) as ro:
            for table in ("v2_canonical_performance", "v2_learning_performance"):
                columns = _table_columns(ro, table)
                required = {"session_date", "market", "ticker"}
                if not required.issubset(columns):
                    continue
                select_cols = [
                    col
                    for col in (
                        "v2_decision_id",
                        "market",
                        "runtime_mode",
                        "session_date",
                        "ticker",
                        "status",
                        "route",
                        "path_type",
                        "path_run_id",
                        "strategy",
                        "origin_action",
                        "filled",
                        "closed",
                        "entry_price",
                        "pnl_pct",
                        "mfe_pct",
                        "mae_pct",
                    )
                    if col in columns
                ]
                if "ticker" not in select_cols:
                    continue
                where = ["session_date=?", "market=?"]
                params: list[Any] = [session_date, market]
                if "runtime_mode" in columns:
                    where.append("runtime_mode=?")
                    params.append(runtime_mode)
                order_cols = [col for col in ("closed", "filled", "synced_at", "v2_decision_id") if col in columns]
                order_sql = f" ORDER BY {', '.join(f'{col} DESC' for col in order_cols)}" if order_cols else ""
                rows = ro.execute(
                    f"SELECT {', '.join(select_cols)} FROM {table} WHERE {' AND '.join(where)}{order_sql}",
                    params,
                ).fetchall()
                result: dict[str, dict[str, Any]] = {}
                for row in rows:
                    ticker = str(row["ticker"] or "").upper()
                    if ticker and ticker not in result:
                        item = dict(row)
                        item["source_table"] = table
                        result[ticker] = item
                if result:
                    return result
    except Exception:
        return {}
    return {}


def _load_candidate_audit_map(
    path: str | Path | None,
    *,
    session_date: str,
    market: str,
    runtime_mode: str,
) -> dict[str, dict[str, Any]]:
    db = Path(path) if path else get_runtime_path("data", "audit", "candidate_audit.db", make_parents=False)
    if not db.exists():
        return {}
    try:
        with _readonly_db(db) as ro:
            columns = {row[1] for row in ro.execute("PRAGMA table_info(audit_candidate_rows)").fetchall()}
            if not columns:
                return {}
            select_cols = [
                col
                for col in (
                    "candidate_key",
                    "runtime_mode",
                    "ticker",
                    "market",
                    "session_date",
                    "claude_watchlist",
                    "claude_trade_ready",
                    "route_final_action",
                    "route_route",
                    "path_run_id",
                    "v2_decision_id",
                    "entry_price",
                    "pnl_pct",
                )
                if col in columns
            ]
            if "ticker" not in select_cols:
                return {}
            where = ["session_date=?", "market=?"]
            params: list[Any] = [session_date, market]
            if "runtime_mode" in columns:
                where.append("runtime_mode=?")
                params.append(runtime_mode)
            rows = ro.execute(
                f"SELECT {', '.join(select_cols)} FROM audit_candidate_rows WHERE {' AND '.join(where)}",
                params,
            ).fetchall()
    except Exception:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row["ticker"] or "").upper()
        if ticker and ticker not in result:
            result[ticker] = dict(row)
    return result
