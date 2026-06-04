from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path


DEFAULT_SELECTION_DB = ROOT / "data" / "ticker_selection_log.db"
DEFAULT_ML_DB = ROOT / "data" / "ml" / "decisions.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _readonly_uri(path: Path) -> str:
    return f"file:{path.resolve().as_posix()}?mode=ro"


def _connect_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_readonly_uri(path), uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_write(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


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


def _norm_mode(value: Any) -> str:
    return str(value or "").strip().lower()


def _norm_market(value: Any) -> str:
    return str(value or "").strip().upper()


def _ticker_key(market: Any, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if _norm_market(market) == "US" else text


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _delta_minutes(left: Any, right: Any) -> float | None:
    left_dt = _parse_dt(left)
    right_dt = _parse_dt(right)
    if left_dt is None or right_dt is None:
        return None
    return abs((left_dt.astimezone(timezone.utc) - right_dt.astimezone(timezone.utc)).total_seconds()) / 60.0


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _date_filters(
    *,
    date_field: str,
    mode_field: str | None,
    mode: str,
    market: str,
    start_date: str,
    end_date: str,
) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if mode_field and mode != "all":
        where.append(f"{mode_field}=?")
        params.append(mode)
    if market != "ALL":
        where.append("market=?")
        params.append(market)
    if start_date:
        where.append(f"{date_field}>=?")
        params.append(start_date)
    if end_date:
        where.append(f"{date_field}<=?")
        params.append(end_date)
    return where, params


def _load_selection_rows(
    conn: sqlite3.Connection,
    *,
    mode: str,
    market: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "ticker_selection_log"):
        return []
    columns = _columns(conn, "ticker_selection_log")
    select_cols = [
        _expr(columns, "id", "0"),
        _expr(columns, "bot_mode", "''"),
        _expr(columns, "date", "''"),
        _expr(columns, "market", "''"),
        _expr(columns, "ticker", "''"),
        _expr(columns, "trade_ready", "0"),
        _expr(columns, "source_type", "''"),
        _expr(columns, "signal_fired", "0"),
        _expr(columns, "strategy_name", "''"),
        _expr(columns, "traded", "0"),
        _expr(columns, "traded_at", "''"),
        _expr(columns, "execution_source_type", "''"),
        _expr(columns, "execution_decision_id", "''"),
        _expr(columns, "execution_strategy", "''"),
        _expr(columns, "execution_reason", "''"),
        _expr(columns, "pnl_pct"),
        _expr(columns, "exit_reason", "''"),
        _expr(columns, "created_at", "''"),
    ]
    where, params = _date_filters(
        date_field="date",
        mode_field="bot_mode",
        mode=mode,
        market=market,
        start_date=start_date,
        end_date=end_date,
    )
    where.append("COALESCE(traded, 0) != 0")
    sql = f"""
        SELECT {", ".join(select_cols)}
        FROM ticker_selection_log
        WHERE {" AND ".join(where)}
        ORDER BY date, market, ticker, id
    """
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _load_duplicate_groups(
    conn: sqlite3.Connection,
    *,
    mode: str,
    market: str,
    start_date: str,
    end_date: str,
    traded_only: bool,
    limit: int,
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "ticker_selection_log"):
        return []
    where, params = _date_filters(
        date_field="date",
        mode_field="bot_mode",
        mode=mode,
        market=market,
        start_date=start_date,
        end_date=end_date,
    )
    if traded_only:
        where.append("COALESCE(traded, 0) != 0")
    sql = f"""
        SELECT bot_mode, market, date, ticker, COUNT(*) AS row_count,
               SUM(CASE WHEN COALESCE(traded, 0) != 0 THEN 1 ELSE 0 END) AS traded_count,
               SUM(CASE WHEN COALESCE(trade_ready, 0) != 0 THEN 1 ELSE 0 END) AS trade_ready_count
        FROM ticker_selection_log
        WHERE {" AND ".join(where) if where else "1=1"}
        GROUP BY bot_mode, market, date, ticker
        HAVING COUNT(*) > 1
        ORDER BY row_count DESC, date DESC, market, ticker
        LIMIT ?
    """
    rows = [dict(row) for row in conn.execute(sql, [*params, int(limit)]).fetchall()]
    return rows


def _load_v2_rows(
    conn: sqlite3.Connection,
    *,
    mode: str,
    market: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "v2_learning_performance"):
        return []
    columns = _columns(conn, "v2_learning_performance")
    select_cols = [
        _expr(columns, "v2_decision_id", "''"),
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
        _expr(columns, "candidate_pool_role", "''"),
        _expr(columns, "filled", "0"),
        _expr(columns, "closed", "0"),
        _expr(columns, "filled_at", "''"),
        _expr(columns, "closed_at", "''"),
        _expr(columns, "pnl_pct"),
        _expr(columns, "quality_grade", "''"),
        _expr(columns, "learning_allowed", "0"),
        _expr(columns, "synced_at", "''"),
    ]
    where, params = _date_filters(
        date_field="session_date",
        mode_field="runtime_mode",
        mode=mode,
        market=market,
        start_date=start_date,
        end_date=end_date,
    )
    sql = f"""
        SELECT {", ".join(select_cols)}
        FROM v2_learning_performance
        WHERE {" AND ".join(where) if where else "1=1"}
        ORDER BY session_date, market, ticker, filled_at, v2_decision_id
    """
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _load_legacy_decision_rows(
    conn: sqlite3.Connection,
    *,
    market: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "decisions"):
        return []
    columns = _columns(conn, "decisions")
    select_cols = [
        _expr(columns, "id", "0"),
        _expr(columns, "ts", "''"),
        _expr(columns, "market", "''"),
        _expr(columns, "ticker", "''"),
        _expr(columns, "session_date", "''"),
        _expr(columns, "decision", "''"),
        _expr(columns, "strategy_used", "''"),
        _expr(columns, "filled", "0"),
        _expr(columns, "order_status", "''"),
        _expr(columns, "pnl_pct"),
    ]
    where, params = _date_filters(
        date_field="session_date",
        mode_field=None,
        mode="all",
        market=market,
        start_date=start_date,
        end_date=end_date,
    )
    where.append("COALESCE(filled, 0) != 0")
    sql = f"""
        SELECT {", ".join(select_cols)}
        FROM decisions
        WHERE {" AND ".join(where)}
        ORDER BY session_date, market, ticker, id
    """
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _load_fill_link_rows(
    conn: sqlite3.Connection,
    *,
    mode: str,
    market: str,
    start_date: str,
    end_date: str,
) -> dict[str, dict[str, Any]]:
    if not _table_exists(conn, "v2_decision_fill_links"):
        return {}
    columns = _columns(conn, "v2_decision_fill_links")
    select_cols = [
        _expr(columns, "v2_decision_id", "''"),
        _expr(columns, "link_status", "''"),
        _expr(columns, "matched_by", "''"),
        _expr(columns, "legacy_decision_id"),
        _expr(columns, "filled_from_canonical", "0"),
        _expr(columns, "legacy_filled_after"),
        _expr(columns, "unmatched_reason", "''"),
    ]
    where, params = _date_filters(
        date_field="session_date",
        mode_field="runtime_mode",
        mode=mode,
        market=market,
        start_date=start_date,
        end_date=end_date,
    )
    sql = f"""
        SELECT {", ".join(select_cols)}
        FROM v2_decision_fill_links
        WHERE {" AND ".join(where) if where else "1=1"}
    """
    return {str(row["v2_decision_id"] or ""): dict(row) for row in conn.execute(sql, params).fetchall()}


def _index_rows(rows: list[dict[str, Any]], *, mode_field: str, date_field: str) -> dict[tuple[str, str, str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        market = _norm_market(row.get("market"))
        key = (
            _norm_mode(row.get(mode_field)),
            market,
            str(row.get(date_field) or ""),
            _ticker_key(market, row.get("ticker")),
        )
        index[key].append(row)
    return index


def _index_legacy_rows(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        market = _norm_market(row.get("market"))
        key = (market, str(row.get("session_date") or ""), _ticker_key(market, row.get("ticker")))
        index[key].append(row)
    return index


def _v2_brief(row: dict[str, Any], traded_at: str) -> dict[str, Any]:
    return {
        "v2_decision_id": row.get("v2_decision_id") or "",
        "status": row.get("status") or "",
        "route": row.get("route") or "",
        "path_type": row.get("path_type") or "",
        "strategy": row.get("strategy") or "",
        "filled": _int(row.get("filled")),
        "closed": _int(row.get("closed")),
        "filled_at": row.get("filled_at") or "",
        "time_delta_min": _delta_minutes(traded_at, row.get("filled_at")),
        "pnl_pct": row.get("pnl_pct"),
    }


def _classify_row(
    row: dict[str, Any],
    *,
    v2_index: dict[tuple[str, str, str, str], list[dict[str, Any]]],
    legacy_index: dict[tuple[str, str, str], list[dict[str, Any]]],
    fill_links: dict[str, dict[str, Any]],
    max_time_delta_min: float,
) -> dict[str, Any]:
    mode = _norm_mode(row.get("bot_mode"))
    market = _norm_market(row.get("market"))
    date = str(row.get("date") or "")
    ticker = _ticker_key(market, row.get("ticker"))
    trade_ready = _int(row.get("trade_ready"))
    execution_decision_id = str(row.get("execution_decision_id") or "").strip()
    issues: list[str] = []
    if not execution_decision_id:
        issues.append("missing_execution_decision_id")
    if trade_ready == 0:
        issues.append("watch_only_traded")

    key = (mode, market, date, ticker)
    v2_candidates = [item for item in v2_index.get(key, []) if _int(item.get("filled")) != 0]
    direct = [item for item in v2_candidates if str(item.get("v2_decision_id") or "") == execution_decision_id]
    time_matches = [
        item
        for item in v2_candidates
        if (delta := _delta_minutes(row.get("traded_at"), item.get("filled_at"))) is not None
        and delta <= max_time_delta_min
    ]
    legacy_key = (market, date, ticker)
    legacy_candidates = legacy_index.get(legacy_key, [])

    classification = "clean_linked"
    recommendation = "no_action"
    matched_v2: dict[str, Any] | None = None
    manual_review_reason = ""

    if direct:
        matched_v2 = direct[0]
        if "watch_only_traded" in issues:
            classification = "linked_watch_only_traded"
            recommendation = "manual_review_split_watch_only_execution_row"
            manual_review_reason = "selection row is watch_only even though an execution id is linked"
        else:
            classification = "clean_linked"
    elif len(time_matches) == 1:
        matched_v2 = time_matches[0]
        classification = "exact_v2_time_match"
        recommendation = (
            "manual_review_split_watch_only_execution_row"
            if "watch_only_traded" in issues
            else "backfill_execution_decision_id"
        )
    elif len(time_matches) > 1:
        classification = "ambiguous_v2_time_match"
        recommendation = "manual_review"
        manual_review_reason = "multiple filled v2 decisions matched the ticker/date/time window"
    elif len(v2_candidates) == 1:
        matched_v2 = v2_candidates[0]
        classification = "same_key_single_v2_match"
        recommendation = "manual_review_time_delta"
        manual_review_reason = "single filled v2 decision has the same ticker/date but does not pass the time window"
    elif len(v2_candidates) > 1:
        classification = "ambiguous_v2_same_key"
        recommendation = "manual_review"
        manual_review_reason = "multiple filled v2 decisions share the same ticker/date"
    elif len(legacy_candidates) == 1:
        classification = "legacy_decisions_single_match"
        recommendation = "manual_review_legacy_decision"
        manual_review_reason = "no v2 match; one legacy decisions row exists"
    elif len(legacy_candidates) > 1:
        classification = "legacy_decisions_ambiguous"
        recommendation = "manual_review"
        manual_review_reason = "no v2 match; multiple legacy decisions rows exist"
    elif issues:
        classification = "no_execution_truth_match"
        recommendation = "leave_as_contaminated_no_touch"
        manual_review_reason = "no causal execution truth found"

    matched_id = str((matched_v2 or {}).get("v2_decision_id") or "")
    link = fill_links.get(matched_id, {}) if matched_id else {}
    v2_delta = _delta_minutes(row.get("traded_at"), (matched_v2 or {}).get("filled_at"))
    return {
        "selection_log_id": _int(row.get("id")),
        "mode": mode,
        "market": market,
        "date": date,
        "ticker": ticker,
        "trade_ready": trade_ready,
        "source_type": row.get("source_type") or "",
        "signal_fired": _int(row.get("signal_fired")),
        "strategy_name": row.get("strategy_name") or "",
        "traded_at": row.get("traded_at") or "",
        "execution_decision_id": execution_decision_id,
        "issues": issues,
        "classification": classification,
        "recommendation": recommendation,
        "manual_review_reason": manual_review_reason,
        "matched_v2_decision_id": matched_id,
        "matched_v2_status": (matched_v2 or {}).get("status") or "",
        "matched_v2_route": (matched_v2 or {}).get("route") or "",
        "matched_v2_path_type": (matched_v2 or {}).get("path_type") or "",
        "matched_v2_strategy": (matched_v2 or {}).get("strategy") or "",
        "matched_v2_filled_at": (matched_v2 or {}).get("filled_at") or "",
        "time_delta_min": v2_delta,
        "fill_link_status": link.get("link_status") or "",
        "fill_link_matched_by": link.get("matched_by") or "",
        "v2_candidate_count": len(v2_candidates),
        "legacy_candidate_count": len(legacy_candidates),
        "v2_candidates": [_v2_brief(item, str(row.get("traded_at") or "")) for item in v2_candidates[:5]],
    }


def _market_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("mode") or ""), str(row.get("market") or ""))].append(row)
    summary: list[dict[str, Any]] = []
    for (mode, market), items in sorted(grouped.items()):
        missing = sum(1 for item in items if "missing_execution_decision_id" in item["issues"])
        watch = sum(1 for item in items if "watch_only_traded" in item["issues"])
        exact = sum(1 for item in items if item["classification"] == "exact_v2_time_match")
        summary.append(
            {
                "mode": mode,
                "market": market,
                "traded_rows": len(items),
                "missing_execution_decision_id_rows": missing,
                "watch_only_traded_rows": watch,
                "exact_v2_time_match_rows": exact,
            }
        )
    return summary


def _top_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    priority = {
        "exact_v2_time_match": 0,
        "same_key_single_v2_match": 1,
        "ambiguous_v2_time_match": 2,
        "ambiguous_v2_same_key": 3,
        "legacy_decisions_single_match": 4,
        "no_execution_truth_match": 5,
        "clean_linked": 9,
    }
    return sorted(
        rows,
        key=lambda row: (
            priority.get(str(row.get("classification") or ""), 8),
            str(row.get("date") or ""),
            str(row.get("market") or ""),
            str(row.get("ticker") or ""),
            int(row.get("selection_log_id") or 0),
        ),
    )[: max(0, int(limit))]


def audit_ticker_selection_attribution(
    *,
    selection_db: Path = DEFAULT_SELECTION_DB,
    ml_db: Path = DEFAULT_ML_DB,
    mode: str = "live",
    market: str = "ALL",
    start_date: str = "",
    end_date: str = "",
    max_time_delta_min: float = 10.0,
    sample_limit: int = 50,
) -> dict[str, Any]:
    mode_key = _norm_mode(mode or "live")
    if mode_key not in {"live", "paper", "all"}:
        raise ValueError("mode must be live, paper, or all")
    market_key = _norm_market(market or "ALL")
    if market_key not in {"KR", "US", "ALL"}:
        raise ValueError("market must be KR, US, or ALL")

    selection_rows: list[dict[str, Any]] = []
    duplicate_groups: list[dict[str, Any]] = []
    traded_duplicate_groups: list[dict[str, Any]] = []
    v2_rows: list[dict[str, Any]] = []
    legacy_rows: list[dict[str, Any]] = []
    fill_links: dict[str, dict[str, Any]] = {}
    missing_sources: list[str] = []

    if not Path(selection_db).exists():
        missing_sources.append("selection_db")
    else:
        with _connect_readonly(Path(selection_db)) as conn:
            selection_rows = _load_selection_rows(
                conn,
                mode=mode_key,
                market=market_key,
                start_date=start_date,
                end_date=end_date,
            )
            duplicate_groups = _load_duplicate_groups(
                conn,
                mode=mode_key,
                market=market_key,
                start_date=start_date,
                end_date=end_date,
                traded_only=False,
                limit=sample_limit,
            )
            traded_duplicate_groups = _load_duplicate_groups(
                conn,
                mode=mode_key,
                market=market_key,
                start_date=start_date,
                end_date=end_date,
                traded_only=True,
                limit=sample_limit,
            )

    if not Path(ml_db).exists():
        missing_sources.append("ml_db")
    else:
        with _connect_readonly(Path(ml_db)) as conn:
            v2_rows = _load_v2_rows(
                conn,
                mode=mode_key,
                market=market_key,
                start_date=start_date,
                end_date=end_date,
            )
            legacy_rows = _load_legacy_decision_rows(
                conn,
                market=market_key,
                start_date=start_date,
                end_date=end_date,
            )
            fill_links = _load_fill_link_rows(
                conn,
                mode=mode_key,
                market=market_key,
                start_date=start_date,
                end_date=end_date,
            )

    v2_index = _index_rows(v2_rows, mode_field="runtime_mode", date_field="session_date")
    legacy_index = _index_legacy_rows(legacy_rows)
    rows = [
        _classify_row(
            row,
            v2_index=v2_index,
            legacy_index=legacy_index,
            fill_links=fill_links,
            max_time_delta_min=float(max_time_delta_min),
        )
        for row in selection_rows
    ]
    contaminated = [row for row in rows if row["issues"]]
    classification_counts = Counter(str(row["classification"]) for row in rows)
    recommendation_counts = Counter(str(row["recommendation"]) for row in rows)
    issue_counts = Counter(issue for row in rows for issue in row["issues"])
    exact_backfill = [
        row
        for row in rows
        if row["recommendation"] == "backfill_execution_decision_id"
        and row.get("matched_v2_decision_id")
        and row.get("trade_ready") == 1
    ]
    watch_split = [
        row
        for row in rows
        if row["recommendation"] == "manual_review_split_watch_only_execution_row"
    ]
    report = {
        "generated_at": _utc_now(),
        "dry_run": True,
        "selection_db": str(Path(selection_db)),
        "ml_db": str(Path(ml_db)),
        "missing_sources": missing_sources,
        "mode": mode_key,
        "market": market_key,
        "start_date": start_date,
        "end_date": end_date,
        "max_time_delta_min": float(max_time_delta_min),
        "summary": {
            "traded_rows": len(rows),
            "contaminated_rows": len(contaminated),
            "missing_execution_decision_id_rows": int(issue_counts["missing_execution_decision_id"]),
            "watch_only_traded_rows": int(issue_counts["watch_only_traded"]),
            "exact_backfill_candidate_rows": len(exact_backfill),
            "watch_only_split_review_rows": len(watch_split),
            "manual_review_rows": sum(
                1
                for row in rows
                if str(row.get("recommendation") or "").startswith("manual_review")
            ),
            "no_touch_rows": int(recommendation_counts["leave_as_contaminated_no_touch"]),
            "selection_duplicate_groups_sampled": len(duplicate_groups),
            "traded_duplicate_groups_sampled": len(traded_duplicate_groups),
        },
        "by_market": _market_summary(rows),
        "classification_counts": dict(sorted(classification_counts.items())),
        "recommendation_counts": dict(sorted(recommendation_counts.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
        "duplicate_groups_sample": duplicate_groups,
        "traded_duplicate_groups_sample": traded_duplicate_groups,
        "exact_backfill_rows": exact_backfill,
        "watch_only_split_review_rows_sample": watch_split[: max(0, int(sample_limit))],
        "rows_sample": _top_rows(rows, limit=sample_limit),
    }
    return report


def _backup_sqlite_db(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{db_path.stem}_before_selection_attr_backfill_{stamp}{db_path.suffix}"
    with _connect_readonly(db_path) as source, sqlite3.connect(str(backup_path)) as target:
        source.backup(target)
    try:
        shutil.copystat(db_path, backup_path)
    except OSError:
        pass
    return backup_path


def apply_exact_backfill(
    *,
    selection_db: Path,
    report: dict[str, Any],
    backup_dir: Path,
    expected_exact_count: int | None = None,
) -> dict[str, Any]:
    exact_rows = list(report.get("exact_backfill_rows") or [])
    if expected_exact_count is not None and len(exact_rows) != expected_exact_count:
        raise RuntimeError(
            f"exact backfill count mismatch: expected={expected_exact_count} actual={len(exact_rows)}"
        )
    backup_path = _backup_sqlite_db(selection_db, backup_dir)
    now = _utc_now()
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    with _connect_write(selection_db) as conn:
        columns = _columns(conn, "ticker_selection_log")
        for item in exact_rows:
            row_id = _int(item.get("selection_log_id"))
            matched_id = str(item.get("matched_v2_decision_id") or "").strip()
            if not row_id or not matched_id:
                skipped.append({"selection_log_id": row_id, "reason": "missing row id or matched v2 id"})
                continue
            current = conn.execute(
                """
                SELECT id, bot_mode, date, market, ticker, trade_ready, traded, execution_decision_id
                FROM ticker_selection_log
                WHERE id=?
                """,
                (row_id,),
            ).fetchone()
            if current is None:
                skipped.append({"selection_log_id": row_id, "reason": "selection row missing"})
                continue
            current_market = _norm_market(current["market"])
            if (
                _norm_mode(current["bot_mode"]) != str(item.get("mode") or "")
                or current_market != str(item.get("market") or "")
                or str(current["date"] or "") != str(item.get("date") or "")
                or _ticker_key(current_market, current["ticker"]) != str(item.get("ticker") or "")
                or _int(current["trade_ready"]) != 1
                or _int(current["traded"]) == 0
                or str(current["execution_decision_id"] or "").strip()
            ):
                skipped.append({"selection_log_id": row_id, "reason": "selection row no longer matches exact criteria"})
                continue

            assignments = ["execution_decision_id=?"]
            params: list[Any] = [matched_id]
            if "execution_source_type" in columns:
                source_type = (
                    str(item.get("matched_v2_path_type") or "").strip()
                    or str(item.get("matched_v2_route") or "").strip()
                    or "v2_learning_performance"
                )
                assignments.append("execution_source_type=COALESCE(NULLIF(execution_source_type, ''), ?)")
                params.append(source_type)
            if "execution_strategy" in columns:
                assignments.append("execution_strategy=COALESCE(NULLIF(execution_strategy, ''), ?)")
                params.append(str(item.get("matched_v2_strategy") or "").strip())
            if "execution_reason" in columns:
                assignments.append("execution_reason=COALESCE(NULLIF(execution_reason, ''), ?)")
                params.append("audited_backfill:v2_exact_time_match")
            params.append(row_id)
            cursor = conn.execute(
                f"""
                UPDATE ticker_selection_log
                SET {", ".join(assignments)}
                WHERE id=?
                  AND COALESCE(traded, 0) != 0
                  AND COALESCE(trade_ready, 0) != 0
                  AND (execution_decision_id IS NULL OR TRIM(execution_decision_id)='')
                """,
                params,
            )
            if cursor.rowcount:
                applied.append(
                    {
                        "selection_log_id": row_id,
                        "market": item.get("market"),
                        "date": item.get("date"),
                        "ticker": item.get("ticker"),
                        "execution_decision_id": matched_id,
                        "matched_v2_strategy": item.get("matched_v2_strategy"),
                        "time_delta_min": item.get("time_delta_min"),
                    }
                )
            else:
                skipped.append({"selection_log_id": row_id, "reason": "update affected no rows"})
        conn.commit()
    return {
        "applied_at": now,
        "backup_path": str(backup_path),
        "candidate_count": len(exact_rows),
        "applied_count": len(applied),
        "skipped_count": len(skipped),
        "applied": applied,
        "skipped": skipped,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run audit for ticker_selection_log execution attribution contamination."
    )
    parser.add_argument("--selection-db", default=str(DEFAULT_SELECTION_DB))
    parser.add_argument("--ml-db", default=str(DEFAULT_ML_DB))
    parser.add_argument("--mode", choices=["live", "paper", "all"], default="live")
    parser.add_argument("--market", choices=["KR", "US", "ALL"], default="ALL")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--max-time-delta-min", type=float, default=10.0)
    parser.add_argument("--sample-limit", type=int, default=50)
    parser.add_argument("--report-path", default="")
    parser.add_argument("--apply-exact-backfill", action="store_true")
    parser.add_argument("--expected-exact-count", type=int, default=-1)
    parser.add_argument("--backup-dir", default="")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.apply_exact_backfill and int(args.expected_exact_count) < 0:
        message = "--apply-exact-backfill requires --expected-exact-count"
        if args.json:
            print(json.dumps({"ok": False, "error": message}, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"ERROR: {message}", file=sys.stderr)
        return 2
    report = audit_ticker_selection_attribution(
        selection_db=Path(args.selection_db).expanduser(),
        ml_db=Path(args.ml_db).expanduser(),
        mode=str(args.mode),
        market=str(args.market),
        start_date=str(args.start_date or ""),
        end_date=str(args.end_date or ""),
        max_time_delta_min=float(args.max_time_delta_min),
        sample_limit=int(args.sample_limit),
    )
    if args.apply_exact_backfill:
        backup_dir = (
            Path(args.backup_dir).expanduser()
            if args.backup_dir
            else get_runtime_path("data", "remediation_backups")
        )
        apply_result = apply_exact_backfill(
            selection_db=Path(args.selection_db).expanduser(),
            report=report,
            backup_dir=backup_dir,
            expected_exact_count=(
                int(args.expected_exact_count) if int(args.expected_exact_count) >= 0 else None
            ),
        )
        report["dry_run"] = False
        report["apply_exact_backfill"] = apply_result
    if args.report_path:
        path = Path(args.report_path).expanduser()
        if not path.is_absolute():
            path = get_runtime_path("docs", "reports", path.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        report["report_path"] = str(path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        summary = report["summary"]
        print(
            "ticker_selection attribution audit "
            f"dry_run={str(bool(report.get('dry_run', True))).lower()} "
            f"mode={report['mode']} market={report['market']} "
            f"traded={summary['traded_rows']} contaminated={summary['contaminated_rows']} "
            f"missing_execution_id={summary['missing_execution_decision_id_rows']} "
            f"watch_only_traded={summary['watch_only_traded_rows']} "
            f"exact_backfill_candidates={summary['exact_backfill_candidate_rows']} "
            f"watch_split_reviews={summary['watch_only_split_review_rows']} "
            f"no_touch={summary['no_touch_rows']}"
        )
        if report.get("report_path"):
            print(f"report_path={report['report_path']}")
        if report.get("apply_exact_backfill"):
            apply_result = report["apply_exact_backfill"]
            print(
                "apply_exact_backfill "
                f"candidates={apply_result['candidate_count']} "
                f"applied={apply_result['applied_count']} "
                f"skipped={apply_result['skipped_count']}"
            )
            print(f"backup_path={apply_result['backup_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
