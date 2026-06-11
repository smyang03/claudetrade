from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.candidate_audit_store import CandidateAuditStore
from runtime_paths import get_runtime_path
import ticker_selection_db as _selection_price_db


DEFAULT_HORIZONS = (30, 60)
MIN_SAMPLES_BY_HORIZON = {30: 2, 60: 3}
DAILY_FORWARD_HORIZONS = {1440: 1, 2880: 2, 4320: 3}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect_read_only(path: Path) -> sqlite3.Connection:
    sidecars = (Path(f"{path}-wal"), Path(f"{path}-shm"))
    options = "mode=ro" if any(sidecar.exists() for sidecar in sidecars) else "mode=ro&immutable=1"
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?{options}", timeout=5.0, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='table' AND name=?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _select_expr(columns: set[str], column_name: str, default_sql: str = "NULL") -> str:
    return column_name if column_name in columns else f"{default_sql} AS {column_name}"


def _empty_update_summary(
    *,
    target: Path,
    session_date: str,
    market: str,
    runtime_mode: str,
    dry_run: bool,
    force_recompute: bool,
    horizons: tuple[int, ...],
    label_generated_at: str,
    next_due_at: str,
    status: str,
    reason: str = "",
    missing_columns: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "status": status,
        "db_path": str(target),
        "session_date": session_date,
        "market": str(market or "").upper(),
        "runtime_mode": str(runtime_mode or "live").lower(),
        "dry_run": bool(dry_run),
        "force_recompute": bool(force_recompute),
        "candidate_rows": 0,
        "outcome_rows": 0,
        "planned_outcome_rows": 0,
        "write_rows": 0,
        "written_rows": 0,
        "planned_insert_count": 0,
        "planned_update_count": 0,
        "planned_overwrite_existing_non_null_count": 0,
        "skipped_existing_non_null_rows": 0,
        "skipped_existing_non_null_examples": [],
        "non_null_return_rows": 0,
        "horizons": list(horizons),
        "status_counts": {},
        "coverage_by_horizon": {},
        "promotion_gate_state": "not_applicable",
        "label_generated_at": label_generated_at,
        "last_success_at": "",
        "next_due_at": next_due_at,
        "outcome_health": status,
    }
    if reason:
        summary["reason"] = reason
    if missing_columns:
        summary["missing_columns"] = missing_columns
    return summary


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        try:
            parsed = datetime.fromisoformat(normalized[:19])
        except Exception:
            return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def _iso(value: datetime | None) -> str:
    return value.replace(microsecond=0).isoformat() if value else ""


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        out = float(text)
    except Exception:
        return None
    return out if out > 0 else None


def _max_gap_sec(points: list[datetime]) -> int:
    if len(points) < 2:
        return 0
    ordered = sorted(points)
    return int(max((b - a).total_seconds() for a, b in zip(ordered, ordered[1:])))


def _candidate_filters(
    *,
    session_date: str = "",
    market: str = "",
    runtime_mode: str = "live",
) -> tuple[str, list[Any]]:
    where = ["runtime_mode=?"]
    params: list[Any] = [str(runtime_mode or "live").lower()]
    if session_date:
        where.append("session_date=?")
        params.append(session_date)
    if market:
        where.append("market=?")
        params.append(str(market).upper())
    return " AND ".join(where), params


def _chunked(values: list[Any], size: int = 400) -> list[list[Any]]:
    return [values[idx : idx + size] for idx in range(0, len(values), size)]


def _load_candidate_rows(
    conn: sqlite3.Connection,
    *,
    session_date: str = "",
    market: str = "",
    runtime_mode: str = "live",
) -> list[dict[str, Any]]:
    where, params = _candidate_filters(session_date=session_date, market=market, runtime_mode=runtime_mode)
    columns = _table_columns(conn, "audit_candidate_rows")
    classification = _select_expr(columns, "classification")
    consensus_mode = _select_expr(columns, "consensus_mode")
    strength_capture_shadow = _select_expr(columns, "strength_capture_shadow", "0")
    strength_capture_rules = _select_expr(columns, "strength_capture_rules")
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT candidate_key, call_id, runtime_mode, market, session_date,
                   known_at, ticker, price, {classification},
                   {consensus_mode}, {strength_capture_shadow}, {strength_capture_rules}
            FROM audit_candidate_rows
            WHERE {where}
            ORDER BY session_date, market, ticker, known_at
            """,
            params,
        )
    ]


def _load_price_observations(
    conn: sqlite3.Connection,
    *,
    session_date: str = "",
    market: str = "",
    runtime_mode: str = "live",
) -> dict[tuple[str, str, str, str], list[tuple[datetime, float]]]:
    where = ["runtime_mode=?", "known_at IS NOT NULL", "known_at!=''", "price IS NOT NULL", "price>0"]
    params: list[Any] = [str(runtime_mode or "live").lower()]
    if session_date:
        where.append("session_date=?")
        params.append(session_date)
    if market:
        where.append("market=?")
        params.append(str(market).upper())
    observations: dict[tuple[str, str, str, str], list[tuple[datetime, float]]] = {}
    for row in conn.execute(
        f"""
        SELECT runtime_mode, market, session_date, ticker, known_at, price
        FROM audit_candidate_rows
        WHERE {' AND '.join(where)}
        ORDER BY session_date, market, ticker, known_at
        """,
        params,
    ):
        ts = _parse_dt(row["known_at"])
        price = _to_float(row["price"])
        if ts is None or price is None:
            continue
        key = (
            str(row["runtime_mode"] or "live").lower(),
            str(row["market"] or "").upper(),
            str(row["session_date"] or ""),
            str(row["ticker"] or "").upper(),
        )
        observations.setdefault(key, []).append((ts, price))
    for values in observations.values():
        values.sort(key=lambda item: item[0])
    return observations


def _load_existing_outcomes(
    conn: sqlite3.Connection,
    *,
    candidate_keys: list[str],
    horizons: tuple[int, ...],
) -> dict[tuple[str, int], dict[str, Any]]:
    if not candidate_keys or not horizons:
        return {}
    if not _table_exists(conn, "audit_candidate_outcomes"):
        return {}
    horizon_values = tuple(sorted({int(value) for value in horizons}))
    horizon_placeholders = ",".join("?" for _ in horizon_values)
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for chunk in _chunked(sorted({str(key) for key in candidate_keys if str(key or "").strip()})):
        key_placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT candidate_key, horizon_min, return_pct, status, updated_at
            FROM audit_candidate_outcomes
            WHERE candidate_key IN ({key_placeholders})
              AND horizon_min IN ({horizon_placeholders})
            """,
            (*chunk, *horizon_values),
        ).fetchall()
        for row in rows:
            out[(str(row["candidate_key"] or ""), int(row["horizon_min"] or 0))] = dict(row)
    return out


def _horizon_summary(
    rows: list[dict[str, Any]],
    *,
    rows_to_write: list[dict[str, Any]],
    skipped_existing_non_null: list[dict[str, Any]],
    written: int,
) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}

    def _entry(horizon: int) -> dict[str, Any]:
        key = str(int(horizon))
        if key not in summary:
            summary[key] = {
                "planned_rows": 0,
                "write_rows": 0,
                "skipped_existing_non_null_rows": 0,
                "written_rows": 0,
                "non_null_return_rows": 0,
                "status_counts": {},
            }
        return summary[key]

    for row in rows:
        entry = _entry(int(row.get("horizon_min") or 0))
        entry["planned_rows"] += 1
        if row.get("return_pct") is not None:
            entry["non_null_return_rows"] += 1
        status = str(row.get("status") or "unknown")
        entry["status_counts"][status] = int(entry["status_counts"].get(status, 0)) + 1
    for row in rows_to_write:
        _entry(int(row.get("horizon_min") or 0))["write_rows"] += 1
    for row in skipped_existing_non_null:
        _entry(int(row.get("horizon_min") or 0))["skipped_existing_non_null_rows"] += 1
    if written:
        for row in rows_to_write:
            _entry(int(row.get("horizon_min") or 0))["written_rows"] += 1
    return dict(sorted(summary.items(), key=lambda item: int(item[0])))


def _write_outcome_report(summary: dict[str, Any], *, report_dir: str | Path | None = None) -> dict[str, str]:
    out_dir = Path(report_dir) if report_dir else get_runtime_path(
        "data",
        "v2_reports",
        "candidate_audit_outcomes",
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"candidate_audit_outcomes_{summary.get('session_date') or 'all'}_{summary.get('market') or 'ALL'}_{stamp}"
    json_path = out_dir / f"{base}.json"
    md_path = out_dir / f"{base}.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# Candidate Audit Outcome Backfill Report",
        "",
        f"- generated_at: {summary.get('label_generated_at', '')}",
        f"- db_path: {summary.get('db_path', '')}",
        f"- session_date: {summary.get('session_date', '') or '*'}",
        f"- market: {summary.get('market', '') or '*'}",
        f"- runtime_mode: {summary.get('runtime_mode', '')}",
        f"- dry_run: {summary.get('dry_run')}",
        f"- force_recompute: {summary.get('force_recompute')}",
        f"- candidate_rows: {summary.get('candidate_rows')}",
        f"- planned_outcome_rows: {summary.get('planned_outcome_rows')}",
        f"- write_rows: {summary.get('write_rows')}",
        f"- skipped_existing_non_null_rows: {summary.get('skipped_existing_non_null_rows')}",
        f"- written_rows: {summary.get('written_rows')}",
        f"- promotion_gate_state: {summary.get('promotion_gate_state', '')}",
        "",
        "## Coverage By Horizon",
        "",
        "| horizon_min | planned | write | skipped_existing_non_null | written | non_null_return | statuses |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for horizon, row in (summary.get("coverage_by_horizon") or {}).items():
        statuses = ", ".join(f"{key}={value}" for key, value in sorted((row.get("status_counts") or {}).items()))
        lines.append(
            f"| {horizon} | {row.get('planned_rows', 0)} | {row.get('write_rows', 0)} | "
            f"{row.get('skipped_existing_non_null_rows', 0)} | {row.get('written_rows', 0)} | "
            f"{row.get('non_null_return_rows', 0)} | {statuses} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def _build_outcome_row(
    *,
    candidate: dict[str, Any],
    horizon_min: int,
    observations: list[tuple[datetime, float]],
    label_generated_at: str,
    min_samples: int,
) -> dict[str, Any]:
    if int(horizon_min) in DAILY_FORWARD_HORIZONS:
        raise ValueError("daily forward horizons must use _build_daily_forward_outcome_row")
    base_at = _parse_dt(candidate.get("known_at"))
    base_price = _to_float(candidate.get("price"))
    target_at = base_at + timedelta(minutes=horizon_min) if base_at else None
    payload: dict[str, Any] = {
        "base_at": _iso(base_at),
        "base_price": base_price,
        "known_at": _iso(target_at),
        "sample_count": 0,
        "outcome_quality": "insufficient_samples",
    }
    base = {
        "candidate_key": candidate.get("candidate_key"),
        "horizon_min": horizon_min,
        "target_at": _iso(target_at),
        "observed_at": "",
        "observed_price": None,
        "return_pct": None,
        "max_runup_pct": None,
        "max_drawdown_pct": None,
        "status": "insufficient_samples",
        "source": "audit_candidate_rows",
        "label_generated_at": label_generated_at,
        "payload": payload,
    }
    if base_at is None or base_price is None or target_at is None:
        payload["reason"] = "missing_base"
        return base

    future = [(ts, price) for ts, price in observations if base_at < ts <= target_at]
    payload["sample_count"] = len(future)
    payload["min_samples"] = min_samples
    if future:
        payload["first_sample_at"] = _iso(future[0][0])
        payload["last_sample_at"] = _iso(future[-1][0])
        payload["max_gap_sec"] = _max_gap_sec([base_at] + [ts for ts, _ in future])
    if len(future) < min_samples:
        payload["reason"] = "too_few_future_samples"
        return base

    observed_at, observed_price = future[-1]
    prices = [price for _, price in future]
    max_price = max(prices)
    min_price = min(prices)
    payload.update(
        {
            "outcome_quality": "audit_sparse",
            "max_price": max_price,
            "min_price": min_price,
            "reason": "",
        }
    )
    return {
        **base,
        "observed_at": _iso(observed_at),
        "observed_price": observed_price,
        "return_pct": ((observed_price / base_price) - 1.0) * 100.0,
        "max_runup_pct": ((max_price / base_price) - 1.0) * 100.0,
        "max_drawdown_pct": ((min_price / base_price) - 1.0) * 100.0,
        "status": "audit_sparse",
        "payload": payload,
    }


def _build_daily_forward_outcome_row(
    *,
    candidate: dict[str, Any],
    horizon_min: int,
    label_generated_at: str,
) -> dict[str, Any]:
    horizon_key = int(horizon_min)
    offset = DAILY_FORWARD_HORIZONS.get(horizon_key)
    session_date = str(candidate.get("session_date") or "")
    market = str(candidate.get("market") or "").upper()
    ticker = str(candidate.get("ticker") or "").upper()
    payload: dict[str, Any] = {
        "horizon_kind": "trading_day_close",
        "trading_day_offset": offset,
        "base_session_date": session_date,
        "base_price_source": "price_csv_close",
        "target_price_source": "price_csv_close",
        "strength_capture_shadow": bool(candidate.get("strength_capture_shadow")),
        "strength_capture_rules": candidate.get("strength_capture_rules") or "[]",
    }
    base = {
        "candidate_key": candidate.get("candidate_key"),
        "horizon_min": horizon_key,
        "target_at": "",
        "observed_at": "",
        "observed_price": None,
        "return_pct": None,
        "max_runup_pct": None,
        "max_drawdown_pct": None,
        "status": "daily_pending",
        "source": "audit_candidate_rows_daily_forward",
        "label_generated_at": label_generated_at,
        "payload": payload,
    }
    if offset is None:
        payload["reason"] = "unsupported_daily_horizon"
        return {**base, "status": "daily_unsupported_horizon"}

    price_data = _selection_price_db._load_price(market, ticker)
    if price_data is None:
        payload["reason"] = "missing_price_csv"
        return {**base, "status": "daily_missing_csv"}

    base_idx = (price_data.get("index") or {}).get(session_date)
    if base_idx is None:
        payload["reason"] = "session_date_not_in_price_csv"
        return {**base, "status": "daily_missing_base"}

    closes = list(price_data.get("closes") or [])
    dates = list(price_data.get("dates") or [])
    target_idx = int(base_idx) + int(offset)
    if base_idx >= len(closes) or base_idx >= len(dates):
        payload["reason"] = "invalid_base_index"
        return {**base, "status": "daily_missing_base"}

    try:
        base_close = float(closes[base_idx])
    except Exception:
        base_close = 0.0
    if base_close <= 0:
        payload["reason"] = "invalid_base_close"
        return {**base, "status": "daily_missing_base"}

    payload["base_close"] = base_close
    if target_idx >= len(closes) or target_idx >= len(dates):
        payload["reason"] = "target_session_not_available"
        return {**base, "status": "daily_pending"}

    try:
        target_close = float(closes[target_idx])
    except Exception:
        target_close = 0.0
    if target_close <= 0:
        payload["reason"] = "invalid_target_close"
        return {**base, "status": "daily_pending"}

    target_session_date = str(dates[target_idx])
    return_pct = _selection_price_db._calc_forward_return(price_data, session_date, int(offset))
    max_runup, max_drawdown = _selection_price_db._calc_window_excursion(price_data, session_date, int(offset))
    payload.update(
        {
            "reason": "",
            "target_session_date": target_session_date,
            "target_close": target_close,
        }
    )
    return {
        **base,
        "target_at": target_session_date,
        "observed_at": target_session_date,
        "observed_price": target_close,
        "return_pct": return_pct,
        "max_runup_pct": max_runup,
        "max_drawdown_pct": max_drawdown,
        "status": "daily_forward",
        "payload": payload,
    }


def update_candidate_audit_daily_backlog(
    *,
    db_path: str | Path | None = None,
    market: str = "",
    runtime_mode: str = "live",
    lookback_days: int = 10,
    max_dates: int = 12,
) -> dict[str, Any]:
    """일 단위 지평(1440/2880/4320) 라벨 백로그 일괄 갱신.

    봇 intraday 갱신은 30/60분 지평만 처리해 일 단위 라벨이 daily_pending으로
    영구 방치됐다 (2026-06-11 발견: 5/19 세션 517후보×3지평). 최근 세션 +
    pending 잔존 세션을 모아 일 지평으로 재계산한다.
    """
    target = Path(db_path) if db_path else get_runtime_path("data", "audit", "candidate_audit.db")
    if not target.exists():
        return {"dates": [], "summaries": [], "reason": "missing_db"}
    dates: set[str] = set()
    try:
        con = sqlite3.connect(str(target))
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=int(lookback_days))).date().isoformat()
            params: list[Any] = [cutoff]
            market_clause = ""
            if market:
                market_clause = " AND market=?"
                params.append(str(market).upper())
            for (d,) in con.execute(
                "SELECT DISTINCT session_date FROM audit_candidate_rows "
                f"WHERE session_date >= ?{market_clause}",
                params,
            ):
                if d:
                    dates.add(str(d)[:10])
            for (d,) in con.execute(
                "SELECT DISTINCT c.session_date FROM audit_candidate_outcomes o "
                "JOIN audit_candidate_rows c ON c.candidate_key = o.candidate_key "
                "WHERE o.status = 'daily_pending'"
            ):
                if d:
                    dates.add(str(d)[:10])
        finally:
            con.close()
    except Exception as exc:
        return {"dates": [], "summaries": [], "reason": f"scan_failed:{exc}"}
    picked = sorted(dates)[-max(1, int(max_dates)):]
    daily_horizons = tuple(sorted(DAILY_FORWARD_HORIZONS.keys()))
    summaries: list[dict[str, Any]] = []
    for session_date in picked:
        try:
            summary = update_candidate_audit_outcomes(
                db_path=target,
                session_date=session_date,
                market=market,
                runtime_mode=runtime_mode,
                horizons=daily_horizons,
            )
            summaries.append({"session_date": session_date, "updated": summary.get("updated_rows", summary.get("updated", 0))})
        except Exception as exc:
            summaries.append({"session_date": session_date, "error": str(exc)})
    return {"dates": picked, "summaries": summaries, "reason": ""}


def update_candidate_audit_outcomes(
    *,
    db_path: str | Path | None = None,
    session_date: str = "",
    market: str = "",
    runtime_mode: str = "live",
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    min_samples_by_horizon: dict[int, int] | None = None,
    dry_run: bool = False,
    write_report: bool = False,
    report_dir: str | Path | None = None,
    force_recompute: bool = False,
) -> dict[str, Any]:
    target = Path(db_path) if db_path else get_runtime_path("data", "audit", "candidate_audit.db")
    label_generated_at = _utc_now()
    next_due_at = (datetime.now(timezone.utc) + timedelta(minutes=max(horizons or DEFAULT_HORIZONS))).isoformat(timespec="seconds")
    min_samples = dict(MIN_SAMPLES_BY_HORIZON)
    if min_samples_by_horizon:
        min_samples.update({int(k): int(v) for k, v in min_samples_by_horizon.items()})

    if dry_run and not target.exists():
        summary = _empty_update_summary(
            target=target,
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
            dry_run=dry_run,
            force_recompute=force_recompute,
            horizons=horizons,
            label_generated_at=label_generated_at,
            next_due_at=next_due_at,
            status="db_not_found",
            reason="dry_run_read_only_target_missing",
        )
        if write_report:
            summary["report_paths"] = _write_outcome_report(summary, report_dir=report_dir)
        return summary

    store = None if dry_run else CandidateAuditStore(target)
    conn = _connect_read_only(target) if dry_run else store.connect()
    try:
        required_candidate_columns = {
            "candidate_key",
            "call_id",
            "runtime_mode",
            "market",
            "session_date",
            "known_at",
            "ticker",
            "price",
        }
        candidate_columns = _table_columns(conn, "audit_candidate_rows")
        missing_candidate_columns = sorted(required_candidate_columns - candidate_columns)
        if missing_candidate_columns:
            summary = _empty_update_summary(
                target=target,
                session_date=session_date,
                market=market,
                runtime_mode=runtime_mode,
                dry_run=dry_run,
                force_recompute=force_recompute,
                horizons=horizons,
                label_generated_at=label_generated_at,
                next_due_at=next_due_at,
                status="schema_missing",
                reason="audit_candidate_rows_missing_or_incomplete",
                missing_columns={"audit_candidate_rows": missing_candidate_columns},
            )
            if write_report:
                summary["report_paths"] = _write_outcome_report(summary, report_dir=report_dir)
            return summary
        candidates = _load_candidate_rows(
            conn,
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
        )
        observations = _load_price_observations(
            conn,
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
        )
        existing_outcomes = _load_existing_outcomes(
            conn,
            candidate_keys=[str(row.get("candidate_key") or "") for row in candidates],
            horizons=horizons,
        )
    finally:
        conn.close()

    outcome_rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    for candidate in candidates:
        key = (
            str(candidate.get("runtime_mode") or "live").lower(),
            str(candidate.get("market") or "").upper(),
            str(candidate.get("session_date") or ""),
            str(candidate.get("ticker") or "").upper(),
        )
        ticker_observations = observations.get(key, [])
        for horizon in horizons:
            horizon_key = int(horizon)
            if horizon_key in DAILY_FORWARD_HORIZONS:
                row = _build_daily_forward_outcome_row(
                    candidate=candidate,
                    horizon_min=horizon_key,
                    label_generated_at=label_generated_at,
                )
            else:
                row = _build_outcome_row(
                    candidate=candidate,
                    horizon_min=horizon_key,
                    observations=ticker_observations,
                    label_generated_at=label_generated_at,
                    min_samples=int(min_samples.get(horizon_key, 1)),
                )
            outcome_rows.append(row)
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1

    rows_to_write: list[dict[str, Any]] = []
    skipped_existing_non_null: list[dict[str, Any]] = []
    planned_insert_count = 0
    planned_update_count = 0
    planned_overwrite_existing_non_null_count = 0
    for row in outcome_rows:
        key = (str(row.get("candidate_key") or ""), int(row.get("horizon_min") or 0))
        existing = existing_outcomes.get(key)
        if existing is not None and existing.get("return_pct") is not None and not force_recompute:
            skipped_existing_non_null.append(
                {
                    "candidate_key": key[0],
                    "horizon_min": key[1],
                    "existing_return_pct": existing.get("return_pct"),
                    "existing_status": existing.get("status"),
                    "planned_status": row.get("status"),
                }
            )
            continue
        if existing is None:
            planned_insert_count += 1
        else:
            planned_update_count += 1
            if existing.get("return_pct") is not None:
                planned_overwrite_existing_non_null_count += 1
        rows_to_write.append(row)

    written = 0 if dry_run else store.upsert_outcomes(rows_to_write)
    coverage_by_horizon = _horizon_summary(
        outcome_rows,
        rows_to_write=rows_to_write,
        skipped_existing_non_null=skipped_existing_non_null,
        written=written,
    )
    daily_horizons = [int(value) for value in horizons if int(value) in DAILY_FORWARD_HORIZONS]
    daily_non_null = sum(
        int(row.get("non_null_return_rows") or 0)
        for horizon, row in coverage_by_horizon.items()
        if int(horizon) in DAILY_FORWARD_HORIZONS
    )
    promotion_gate_state = "not_applicable"
    if daily_horizons:
        promotion_gate_state = "pass" if daily_non_null > 0 else "blocked_label_coverage"

    summary = {
        "status": "ok",
        "db_path": str(target),
        "session_date": session_date,
        "market": str(market or "").upper(),
        "runtime_mode": str(runtime_mode or "live").lower(),
        "dry_run": bool(dry_run),
        "force_recompute": bool(force_recompute),
        "candidate_rows": len(candidates),
        "outcome_rows": written,
        "planned_outcome_rows": len(outcome_rows),
        "write_rows": len(rows_to_write),
        "written_rows": written,
        "planned_insert_count": planned_insert_count,
        "planned_update_count": planned_update_count,
        "planned_overwrite_existing_non_null_count": planned_overwrite_existing_non_null_count,
        "skipped_existing_non_null_rows": len(skipped_existing_non_null),
        "skipped_existing_non_null_examples": skipped_existing_non_null[:20],
        "non_null_return_rows": sum(1 for row in outcome_rows if row.get("return_pct") is not None),
        "horizons": list(horizons),
        "status_counts": status_counts,
        "coverage_by_horizon": coverage_by_horizon,
        "promotion_gate_state": promotion_gate_state,
        "label_generated_at": label_generated_at,
        "last_success_at": label_generated_at if written or not candidates else "",
        "next_due_at": next_due_at,
        "outcome_health": "ok" if written or dry_run or not candidates else "no_outcomes_written",
    }
    if write_report:
        summary["report_paths"] = _write_outcome_report(summary, report_dir=report_dir)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Update candidate audit 30m/60m outcome labels.")
    parser.add_argument("--db", default="", help="candidate audit DB path")
    parser.add_argument("--date", default="", help="session date YYYY-MM-DD")
    parser.add_argument("--market", default="", help="KR or US; empty means all markets")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--horizons", default="30,60", help="comma-separated minute horizons")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-dir", default="")
    parser.add_argument("--force-recompute", action="store_true")
    args = parser.parse_args()
    horizons = tuple(int(part.strip()) for part in str(args.horizons).split(",") if part.strip())
    summary = update_candidate_audit_outcomes(
        db_path=args.db or None,
        session_date=args.date,
        market=args.market,
        runtime_mode=args.runtime_mode,
        horizons=horizons or DEFAULT_HORIZONS,
        dry_run=args.dry_run,
        write_report=args.write_report,
        report_dir=args.report_dir or None,
        force_recompute=args.force_recompute,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
