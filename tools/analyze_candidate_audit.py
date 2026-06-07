from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path
from tools.update_candidate_audit_outcomes import MIN_SAMPLES_BY_HORIZON

KST = timezone(timedelta(hours=9))
FRESHNESS_WARN_SEC = 120
MISSED_WINNER_MFE_PCT = 2.0
MISSED_WINNER_MIN_DRAWDOWN_PCT = -2.0
PRICE_STALE_TERMS = (
    "stale_quote",
    "quote_stale",
    "expired_quote",
    "expired_price",
    "old_quote",
    "quote_age",
    "price_stale",
)
PRICE_UNIT_TERMS = ("unit", "normaliz", "scale", "price_unit", "currency_mismatch")
PRICE_PROVIDER_TERMS = (
    "exception",
    "provider_timeout",
    "provider_failure",
    "api_error",
    "api_timeout",
    "http_error",
    "http_timeout",
    "quote_provider_error",
    "quote_provider_failure",
    "quote_fetch_failed",
    "quote_fetch_failure",
    "quote_fetch_timeout",
    "quote_timeout",
)
MISSING_QUOTE_TERMS = (
    "missing_quote",
    "quote_missing",
    "no_quote",
    "empty_quote",
    "absent_quote",
    "quote_absent",
)
BROAD_INVALID_PRICE_TERMS = (
    "invalid_price",
    "invalid price",
    "claude_price_invalid",
    "price_invalid",
    "quote_invalid",
)


def normalize_candidate_action(action: str) -> str:
    key = str(action or "").strip().upper()
    if key in {"BUY_READY", "PROBE_READY", "ADD_READY"}:
        return "trade_ready_family"
    if key in {"WATCH", "PULLBACK_WAIT"}:
        return "watch_family"
    if key in {"AVOID", "SKIP", "DO_NOT_TRADE"}:
        return "avoid_family"
    if key in {"HARD_BLOCK", "BLOCKED"}:
        return "blocked_family"
    return "unknown_family" if not key else "other_family"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _text_contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(term in lowered for term in terms)


def _invalid_price_evidence_text(row: dict[str, Any], payload: dict[str, Any]) -> str:
    evidence_parts: list[str] = []
    for key in (
        "invalid_price_reason",
        "price_invalid_reason",
        "quote_invalid_reason",
        "price_data_reason",
        "claude_reason",
        "claude_veto_reason",
        "runtime_filter_reason",
        "route_final_action",
        "route_reason",
        "route_runtime_gate_reason",
        "evidence_data_state",
        "data_quality",
        "classification",
    ):
        value = row.get(key)
        if value not in (None, ""):
            evidence_parts.append(str(value))
        payload_value = payload.get(key)
        if payload_value not in (None, ""):
            evidence_parts.append(str(payload_value))
    for key in (
        "evidence_missing_fields_json",
        "data_quality_flags_json",
        "quality_data_gaps_json",
        "bucket_data_gaps_json",
    ):
        value = row.get(key)
        if value not in (None, ""):
            evidence_parts.append(str(value))
        payload_value = payload.get(key)
        if payload_value not in (None, ""):
            evidence_parts.append(str(payload_value))
    return " ".join(evidence_parts)


def _price_provider_evidence_text(row: dict[str, Any], payload: dict[str, Any]) -> str:
    evidence_parts: list[str] = []
    for key in (
        "invalid_price_reason",
        "price_invalid_reason",
        "quote_invalid_reason",
        "price_data_reason",
    ):
        value = row.get(key)
        if value not in (None, ""):
            evidence_parts.append(str(value))
        payload_value = payload.get(key)
        if payload_value not in (None, ""):
            evidence_parts.append(str(payload_value))
    return " ".join(evidence_parts)


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _is_non_executable_selection_meta_row(row: dict[str, Any], payload: dict[str, Any]) -> bool:
    source_file = str(row.get("source_file") or payload.get("source_file") or "").strip()
    if source_file != "trading_bot.selection_meta":
        return False
    if _truthy_flag(row.get("in_prompt")) or _truthy_flag(row.get("input_to_claude_reported")):
        return False
    if _truthy_flag(row.get("final_prompt_included")) or _truthy_flag(row.get("actual_prompt_included")):
        return False
    if _truthy_flag(payload.get("actual_prompt_included")):
        return False
    if _truthy_flag(row.get("claude_trade_ready")):
        return False
    action = str(row.get("route_final_action") or row.get("claude_action") or "").strip()
    if action:
        return False
    evidence_text = f"{_invalid_price_evidence_text(row, payload)} {_price_provider_evidence_text(row, payload)}"
    invalid_terms = PRICE_STALE_TERMS + PRICE_UNIT_TERMS + PRICE_PROVIDER_TERMS + MISSING_QUOTE_TERMS + BROAD_INVALID_PRICE_TERMS
    if _text_contains_any(evidence_text, invalid_terms):
        return False
    return True


def _has_invalid_price_observation(row: dict[str, Any], payload: dict[str, Any]) -> bool:
    price = _to_float(row.get("price"))
    if (price is None or price <= 0) and _is_non_executable_selection_meta_row(row, payload):
        return False
    if price is None or price <= 0:
        return True
    text = _invalid_price_evidence_text(row, payload)
    return _text_contains_any(text, BROAD_INVALID_PRICE_TERMS)


def _invalid_price_reason(row: dict[str, Any], payload: dict[str, Any]) -> tuple[str, str]:
    price = _to_float(row.get("price"))
    text = _invalid_price_evidence_text(row, payload)
    provider_text = _price_provider_evidence_text(row, payload)
    if _text_contains_any(text, PRICE_STALE_TERMS):
        return "stale_quote", "quote evidence was stale or expired"
    if _text_contains_any(text, PRICE_UNIT_TERMS):
        return "unit_normalization_issue", "price unit or normalization evidence was invalid"
    if _text_contains_any(provider_text, PRICE_PROVIDER_TERMS):
        return "provider_failure", "quote provider returned an error or timed out"
    if _text_contains_any(text, MISSING_QUOTE_TERMS):
        return "missing_quote", "quote evidence was missing"
    if price is None:
        return "legacy_price_unmeasured", "price field was empty and no quote-failure evidence was present"
    if price <= 0:
        return "non_positive_price", "price field was zero or negative"
    return "unknown_price_issue", "price was invalid but no specific evidence was available"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _row_expr(columns: set[str], name: str, default_sql: str = "NULL") -> str:
    return f"r.{name}" if name in columns else f"{default_sql} AS {name}"


def _percentile(values: list[float], pct: float) -> float | None:
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    rank = (len(clean) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(clean) - 1)
    weight = rank - lower
    return clean[lower] + (clean[upper] - clean[lower]) * weight


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


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
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str:
    return value.astimezone(KST).replace(microsecond=0).isoformat() if value else ""


def _latest_dt(values: list[Any]) -> datetime | None:
    parsed = [dt for dt in (_parse_dt(value) for value in values) if dt is not None]
    return max(parsed) if parsed else None


def _rate(values: list[float], predicate) -> float | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return sum(1 for value in clean if predicate(value)) / len(clean)


def _metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [_to_float(row.get("return_pct")) for row in rows]
    runups = [_to_float(row.get("max_runup_pct")) for row in rows]
    drawdowns = [_to_float(row.get("max_drawdown_pct")) for row in rows]
    returns_clean = [v for v in returns if v is not None]
    runups_clean = [v for v in runups if v is not None]
    drawdowns_clean = [v for v in drawdowns if v is not None]
    return {
        "rows": len(rows),
        "unique_tickers": len({str(row.get("ticker") or "").upper() for row in rows if row.get("ticker")}),
        "labeled_rows": len(returns_clean),
        "mean_return_pct": _round(_mean(returns_clean)),
        "median_return_pct": _round(_percentile(returns_clean, 0.50)),
        "p75_return_pct": _round(_percentile(returns_clean, 0.75)),
        "p90_return_pct": _round(_percentile(returns_clean, 0.90)),
        "p95_return_pct": _round(_percentile(returns_clean, 0.95)),
        "mean_mfe_pct": _round(_mean(runups_clean)),
        "median_mfe_pct": _round(_percentile(runups_clean, 0.50)),
        "p90_mfe_pct": _round(_percentile(runups_clean, 0.90)),
        "positive_return_rate": _round(_rate(returns_clean, lambda value: value > 0)),
        "mfe_2pct_rate": _round(_rate(runups_clean, lambda value: value >= 2.0)),
        "mfe_3pct_rate": _round(_rate(runups_clean, lambda value: value >= 3.0)),
        "mfe_5pct_rate": _round(_rate(runups_clean, lambda value: value >= 5.0)),
        "mae_minus2pct_rate": _round(_rate(drawdowns_clean, lambda value: value <= -2.0)),
        "small_bucket_sample": len(returns_clean) < 5,
    }


def _where_clause(*, session_date: str, market: str, runtime_mode: str) -> tuple[str, list[Any]]:
    where = ["r.runtime_mode=?"]
    params: list[Any] = [str(runtime_mode or "live").lower()]
    if session_date:
        where.append("r.session_date=?")
        params.append(session_date)
    if market:
        where.append("r.market=?")
        params.append(str(market).upper())
    return " AND ".join(where), params


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _optional_col(columns: set[str], name: str, *, alias: str = "r", default_sql: str = "NULL") -> str:
    if name in columns:
        return f"{alias}.{name} AS {name}"
    return f"{default_sql} AS {name}"


def _row_uniqueness_summary(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    market: str,
    runtime_mode: str,
) -> dict[str, int]:
    where = ["runtime_mode=?"]
    params: list[Any] = [str(runtime_mode or "live").lower()]
    if session_date:
        where.append("session_date=?")
        params.append(session_date)
    if market:
        where.append("market=?")
        params.append(str(market).upper())
    where_sql = " AND ".join(where)
    call_level_rows = int(
        conn.execute(
            f"SELECT COUNT(*) FROM audit_candidate_rows WHERE {where_sql}",
            params,
        ).fetchone()[0]
    )
    latest_session_ticker_rows = int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM (
                SELECT runtime_mode, market, session_date, ticker
                FROM audit_candidate_rows
                WHERE {where_sql}
                GROUP BY runtime_mode, market, session_date, ticker
            )
            """,
            params,
        ).fetchone()[0]
    )
    duplicate_group_count = int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM (
                SELECT runtime_mode, market, session_date, ticker
                FROM audit_candidate_rows
                WHERE {where_sql}
                GROUP BY runtime_mode, market, session_date, ticker
                HAVING COUNT(*) > 1
            )
            """,
            params,
        ).fetchone()[0]
    )
    return {
        "call_level_rows": call_level_rows,
        "latest_session_ticker_rows": latest_session_ticker_rows,
        "duplicate_group_count": duplicate_group_count,
    }


def _load_outcome_rows(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    market: str,
    runtime_mode: str,
    horizon_min: int,
    latest_only: bool = True,
) -> list[dict[str, Any]]:
    where, params = _where_clause(session_date=session_date, market=market, runtime_mode=runtime_mode)
    params.append(int(horizon_min))
    row_source = "audit_candidate_latest_rows" if latest_only else "audit_candidate_rows"
    columns = _table_columns(conn, "audit_candidate_rows")
    col = lambda name, default="NULL": _optional_col(columns, name, default_sql=default)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT r.candidate_key, r.session_date, r.market, r.call_id, r.known_at,
                   r.ticker, COALESCE(r.classification, 'unknown') AS classification,
                   {col('claude_action')}, {col('claude_reason')}, {col('claude_veto_reason')},
                   {col('claude_watchlist', '0')}, {col('claude_trade_ready', '0')},
                   r.recommended_strategy, r.strategy_used, r.filled_count,
                   {col('no_signal_count', '0')}, {col('watch_only_count', '0')}, {col('buy_signal_count', '0')},
                   {col('entry_price')}, {col('first_signal_at')}, {col('first_fill_at')},
                   {col('execution_decision_id')}, {col('config_hash')},
                   {col('source_file', "''")}, {col('candidate_source', "''")},
                   {col('liquidity_bucket', "''")}, {col('primary_bucket', "''")},
                   {col('secondary_buckets_json', "'[]'")},
                   {col('selection_trace_id', "''")}, {col('visibility_contract_version', "''")},
                   {col('actual_prompt_call_id', "''")}, {col('actual_prompt_included')},
                   {col('actual_prompt_rank')}, {col('raw_rank')}, {col('prompt_rank_after_trim')},
                   {col('raw_score_current')}, {col('raw_score_components_json', "'{}'")},
                   {col('trainer_prompt_score')}, {col('trainer_score_rank')},
                   {col('source_tags_json', "'[]'")}, {col('bucket_reasons_json', "'{}'")},
                   {col('bucket_data_gaps_json', "'[]'")},
                   r.pnl_pct, r.close_reason, r.route_original_action,
                   r.route_final_action, r.route_route, r.route_reason,
                   {col('route_demoted_to')}, r.route_runtime_gate_reason,
                   r.route_cancel_pathb,
                   r.route_suspend_pathb, r.route_warnings_json,
                   {col('action_ceiling')}, {col('evidence_data_state')},
                   {col('evidence_missing_fields_json', "'[]'")}, {col('evidence_action_ceiling')},
                   {col('evidence_ceiling_applied', '0')}, {col('entry_timing_snapshot_json', "'{}'")},
                   {col('post_open_features_json', "'{}'")}, {col('failed_ready_reasons_json', "'[]'")},
                   {col('entry_delay_min')}, {col('position_mfe_pct')}, {col('position_mae_pct')},
                   {col('path_run_count', '0')}, {col('intraday_signal_count', '0')}, {col('intraday_traded_count', '0')},
                   o.horizon_min, o.status,
                   o.return_pct, o.max_runup_pct, o.max_drawdown_pct,
                   o.observed_at, o.observed_price, o.payload_json
            FROM {row_source} r
            LEFT JOIN audit_candidate_outcomes o
              ON o.candidate_key = r.candidate_key
             AND o.horizon_min = ?
            WHERE {where}
            """,
            [params[-1], *params[:-1]],
        )
    ]


def _top_by_mfe(
    rows: list[dict[str, Any]],
    classification: str,
    *,
    limit: int,
    horizon_min: int,
) -> list[dict[str, Any]]:
    subset = [
        row
        for row in rows
        if row.get("classification") == classification and _to_float(row.get("max_runup_pct")) is not None
    ]
    subset.sort(key=lambda row: _to_float(row.get("max_runup_pct")) or -9999.0, reverse=True)
    out: list[dict[str, Any]] = []
    for row in subset[:limit]:
        sample_count = None
        try:
            payload = json.loads(str(row.get("payload_json") or "{}"))
            sample_count = payload.get("sample_count")
        except Exception:
            sample_count = None
        min_samples = int(MIN_SAMPLES_BY_HORIZON.get(int(horizon_min), 1))
        out.append(
            {
                "candidate_key": row.get("candidate_key"),
                "session_date": row.get("session_date"),
                "market": row.get("market"),
                "call_id": row.get("call_id"),
                "known_at": row.get("known_at"),
                "ticker": row.get("ticker"),
                "classification": row.get("classification"),
                "return_pct": _round(_to_float(row.get("return_pct"))),
                "max_runup_pct": _round(_to_float(row.get("max_runup_pct"))),
                "max_drawdown_pct": _round(_to_float(row.get("max_drawdown_pct"))),
                "sample_count": sample_count,
                "thin_price_sample": sample_count is None or int(sample_count or 0) < min_samples,
            }
        )
    return out


def _miss_stage(classification: str) -> str:
    mapping = {
        "not_in_prompt": "selection_not_in_prompt",
        "in_prompt_not_selected": "claude_not_selected",
        "watch_only": "claude_not_selected",
        "ready_no_signal": "strategy_no_signal",
    }
    return mapping.get(classification, "unknown")


def _miss_type(classification: str) -> str:
    mapping = {
        "not_in_prompt": "selection_not_in_prompt",
        "in_prompt_not_selected": "claude_not_selected",
        "watch_only": "claude_not_selected",
        "ready_no_signal": "strategy_no_signal",
    }
    return mapping.get(classification, classification or "unknown")


def missed_winners(
    rows: list[dict[str, Any]],
    *,
    limit: int,
    horizon_min: int,
    min_mfe_pct: float = MISSED_WINNER_MFE_PCT,
    min_drawdown_pct: float = MISSED_WINNER_MIN_DRAWDOWN_PCT,
) -> list[dict[str, Any]]:
    allowed = {"not_in_prompt", "in_prompt_not_selected", "watch_only", "ready_no_signal"}
    items: list[dict[str, Any]] = []
    for row in rows:
        classification = str(row.get("classification") or "unknown")
        if classification not in allowed:
            continue
        mfe = _to_float(row.get("max_runup_pct"))
        mae = _to_float(row.get("max_drawdown_pct"))
        if mfe is None or mfe < min_mfe_pct:
            continue
        if mae is not None and mae <= min_drawdown_pct:
            continue
        sample_count = None
        try:
            payload = json.loads(str(row.get("payload_json") or "{}"))
            sample_count = payload.get("sample_count")
        except Exception:
            sample_count = None
        miss_stage = _miss_stage(classification)
        miss_type = _miss_type(classification)
        if classification in {"watch_only", "ready_no_signal"}:
            try:
                bucket, _reason = _watch_only_primary_bucket(row)
                bucket_map = {
                    "not_in_prompt": "selection_not_in_prompt",
                    "claude_not_selected": "claude_not_selected",
                    "risk_or_affordability": "risk_or_affordability_block",
                    "strategy_no_signal": "strategy_no_signal",
                    "pathb_zone_or_plan": "pathb_zone_or_plan",
                    "routing_demotion": "risk_or_affordability_block",
                    "data_insufficient": "prompt_trim_miss",
                    "evidence_ceiling": "prompt_trim_miss",
                }
                miss_stage = bucket_map.get(bucket, miss_stage)
                miss_type = bucket
            except Exception:
                pass
        items.append(
            {
                "candidate_key": row.get("candidate_key"),
                "session_date": row.get("session_date"),
                "market": row.get("market"),
                "call_id": row.get("call_id"),
                "known_at": row.get("known_at"),
                "ticker": row.get("ticker"),
                "classification": classification,
                "miss_type": miss_type,
                "miss_stage": miss_stage,
                "return_pct": _round(_to_float(row.get("return_pct"))),
                "max_runup_pct": _round(mfe),
                "max_drawdown_pct": _round(mae),
                "horizon_min": int(horizon_min),
                "sample_count": sample_count,
                "route_reason": row.get("route_reason") or "",
            }
        )
    best_by_ticker: dict[str, dict[str, Any]] = {}
    for item in items:
        ticker = str(item.get("ticker") or "").upper()
        if not ticker:
            continue
        current = best_by_ticker.get(ticker)
        if current is None or (_to_float(item.get("max_runup_pct")) or -9999.0) > (
            _to_float(current.get("max_runup_pct")) or -9999.0
        ):
            best_by_ticker[ticker] = item
    deduped = list(best_by_ticker.values())
    deduped.sort(key=lambda row: (_to_float(row.get("max_runup_pct")) or -9999.0), reverse=True)
    return deduped[: max(int(limit or 10), 1)]


def _strategy_tokens(value: Any) -> set[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return set()
    return {part for part in re.split(r"[^a-z0-9_]+", raw) if part}


def classify_strategy_match(recommended_strategy: Any, strategy_used: Any) -> str:
    used = str(strategy_used or "").strip().lower()
    if not used:
        return "not_applicable"
    recommended = _strategy_tokens(recommended_strategy)
    if not recommended:
        return "unclassified"
    return "match" if used in recommended else "mismatch"


def _strategy_mismatch(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [
        row
        for row in rows
        if int(row.get("filled_count") or 0) > 0 and str(row.get("strategy_used") or "").strip()
    ]
    buckets: dict[str, list[dict[str, Any]]] = {"match": [], "mismatch": [], "unclassified": []}
    pairs: dict[str, dict[str, Any]] = {}
    for row in filled:
        status = classify_strategy_match(row.get("recommended_strategy"), row.get("strategy_used"))
        if status == "not_applicable":
            continue
        buckets.setdefault(status, []).append(row)
        pair_key = f"{row.get('recommended_strategy') or ''} -> {row.get('strategy_used') or ''}"
        item = pairs.setdefault(
            pair_key,
            {
                "recommended_strategy": row.get("recommended_strategy") or "",
                "strategy_used": row.get("strategy_used") or "",
                "status": status,
                "count": 0,
                "pnl_values": [],
                "close_reasons": {},
            },
        )
        item["count"] += 1
        pnl = _to_float(row.get("pnl_pct"))
        if pnl is not None:
            item["pnl_values"].append(pnl)
        close_reason = str(row.get("close_reason") or "").strip() or "unknown"
        item["close_reasons"][close_reason] = item["close_reasons"].get(close_reason, 0) + 1

    summary: dict[str, Any] = {
        "filled_strategy_rows": len(filled),
        "match_count": len(buckets.get("match", [])),
        "mismatch_count": len(buckets.get("mismatch", [])),
        "unclassified_count": len(buckets.get("unclassified", [])),
    }
    summary["mismatch_rate"] = _round(
        len(buckets.get("mismatch", [])) / len(filled) if filled else None
    )
    pair_rows = []
    for item in pairs.values():
        pnl_values = item.pop("pnl_values")
        item["avg_pnl_pct"] = _round(_mean(pnl_values))
        item["median_pnl_pct"] = _round(_percentile(pnl_values, 0.50))
        pair_rows.append(item)
    pair_rows.sort(key=lambda item: (item["status"] != "mismatch", -int(item["count"])))
    summary["pairs"] = pair_rows[:30]
    return summary


def _outcome_coverage(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    market: str,
    runtime_mode: str,
) -> dict[str, dict[str, Any]]:
    where, params = _where_clause(session_date=session_date, market=market, runtime_mode=runtime_mode)
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT o.horizon_min,
                   COALESCE(o.status, 'unknown') AS status,
                   COALESCE(r.classification, 'unknown') AS classification,
                   o.payload_json,
                   COUNT(*) AS rows
            FROM audit_candidate_rows r
            JOIN audit_candidate_outcomes o ON o.candidate_key = r.candidate_key
            WHERE {where}
            GROUP BY o.horizon_min, COALESCE(o.status, 'unknown'), COALESCE(r.classification, 'unknown'), o.payload_json
            ORDER BY o.horizon_min ASC, rows DESC
            """,
            params,
        )
    ]
    by_horizon: dict[str, dict[str, Any]] = {}
    for row in rows:
        horizon = str(int(row.get("horizon_min") or 0))
        item = by_horizon.setdefault(
            horizon,
            {
                "total": 0,
                "audit_sparse": 0,
                "insufficient_samples": 0,
                "status_counts": {},
                "classification_counts": {},
                "coverage_gap_reasons": {},
            },
        )
        status = str(row.get("status") or "unknown")
        classification = str(row.get("classification") or "unknown")
        count = int(row.get("rows") or 0)
        item["total"] += count
        item["status_counts"][status] = item["status_counts"].get(status, 0) + count
        item["classification_counts"][classification] = item["classification_counts"].get(classification, 0) + count
        if status == "audit_sparse":
            item["audit_sparse"] = item.get("audit_sparse", 0) + count
        elif status == "insufficient_samples":
            item["insufficient_samples"] = item.get("insufficient_samples", 0) + count
        if status != "audit_sparse":
            reason = status
            try:
                payload = json.loads(str(row.get("payload_json") or "{}"))
                reason = str(payload.get("reason") or payload.get("quote_invalid_reason") or status)
            except Exception:
                reason = status
            reason_map = item["coverage_gap_reasons"]
            reason_map[reason] = reason_map.get(reason, 0) + count
    for item in by_horizon.values():
        total = int(item.get("total") or 0)
        audit_sparse = int(item.get("audit_sparse") or 0)
        coverage_rate = audit_sparse / total if total else None
        item["coverage_rate"] = _round(coverage_rate)
        item["maturity"] = _coverage_maturity(coverage_rate)
        item["interpretation"] = _coverage_interpretation(coverage_rate)
    return by_horizon


def _candidate_consistency_summary(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    market: str,
    runtime_mode: str,
) -> dict[str, Any]:
    where, params = _where_clause(session_date=session_date, market=market, runtime_mode=runtime_mode)
    columns = _columns(conn, "audit_candidate_rows")
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT candidate_key, session_date, market, ticker, price,
                   input_to_claude_reported, in_prompt, claude_trade_ready,
                   final_prompt_included, claude_action, claude_reason, claude_veto_reason,
                   route_final_action,
                   route_reason, route_runtime_gate_reason, source_file,
                   {_row_expr(columns, "evidence_data_state", "''")},
                   {_row_expr(columns, "evidence_missing_fields_json", "'[]'")},
                   {_row_expr(columns, "data_quality", "''")},
                   {_row_expr(columns, "data_quality_flags_json", "'[]'")},
                   {_row_expr(columns, "quality_data_gaps_json", "'[]'")},
                   {_row_expr(columns, "bucket_data_gaps_json", "'[]'")},
                   {_row_expr(columns, "selection_trace_id", "''")},
                   {_row_expr(columns, "visibility_contract_version", "''")},
                   {_row_expr(columns, "actual_prompt_call_id", "''")},
                   {_row_expr(columns, "actual_prompt_included")},
                   {_row_expr(columns, "actual_prompt_rank")},
                   payload_json
            FROM audit_candidate_latest_rows r
            WHERE {where}
            """,
            params,
        )
    ]
    input_reported_not_in_prompt: list[dict[str, Any]] = []
    actual_prompt_mismatch: list[dict[str, Any]] = []
    actual_prompt_unmeasured: list[dict[str, Any]] = []
    legacy_input_reported_mismatch: list[dict[str, Any]] = []
    trace_join_missing: list[dict[str, Any]] = []
    trade_ready_family_mismatch: list[dict[str, Any]] = []
    invalid_price: list[dict[str, Any]] = []
    invalid_price_reason_counts: Counter[str] = Counter()
    action_family_counts: Counter[str] = Counter()
    for row in rows:
        try:
            payload = json.loads(str(row.get("payload_json") or "{}"))
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        visibility_version = str(row.get("visibility_contract_version") or payload.get("visibility_contract_version") or "").strip()
        has_actual_contract = visibility_version == "actual_prompt_v1"
        trace_id = str(row.get("selection_trace_id") or payload.get("selection_trace_id") or "").strip()
        action = str(row.get("route_final_action") or row.get("claude_action") or "")
        family = normalize_candidate_action(action)
        action_family_counts[family] += 1
        item = {
            "candidate_key": row.get("candidate_key"),
            "session_date": row.get("session_date"),
            "market": row.get("market"),
            "ticker": row.get("ticker"),
            "action": action,
            "action_family": family,
        }
        if int(row.get("input_to_claude_reported") or 0) == 1 and int(row.get("in_prompt") or 0) == 0:
            input_reported_not_in_prompt.append(item)
            if has_actual_contract:
                actual_prompt_mismatch.append({**item, "visibility_contract_version": visibility_version})
            else:
                legacy_input_reported_mismatch.append(item)
        actual_prompt_included = row.get("actual_prompt_included")
        if actual_prompt_included is None and "actual_prompt_included" in payload:
            actual_prompt_included = payload.get("actual_prompt_included")
        if has_actual_contract and actual_prompt_included is None:
            actual_prompt_unmeasured.append({**item, "visibility_contract_version": visibility_version})
        if has_actual_contract and actual_prompt_included is not None:
            actual_flag = 1 if bool(actual_prompt_included) else 0
            reported_flag = int(row.get("input_to_claude_reported") or 0)
            if actual_flag != reported_flag:
                actual_prompt_mismatch.append({**item, "visibility_contract_version": visibility_version})
        if (
            has_actual_contract
            and row.get("final_prompt_included") is not None
            and int(row.get("final_prompt_included") or 0) != int(row.get("in_prompt") or 0)
        ):
            actual_prompt_mismatch.append({**item, "visibility_contract_version": visibility_version})
        if has_actual_contract and not trace_id:
            trace_join_missing.append({**item, "visibility_contract_version": visibility_version})
        if int(row.get("claude_trade_ready") or 0) == 1 and family != "trade_ready_family":
            trade_ready_family_mismatch.append(item)
        if _has_invalid_price_observation(row, payload):
            reason_code, reason_detail = _invalid_price_reason(row, payload)
            invalid_price_reason_counts[reason_code] += 1
            invalid_price.append(
                {
                    **item,
                    "price": row.get("price"),
                    "invalid_price_reason_code": reason_code,
                    "invalid_price_reason_detail": reason_detail,
                }
            )
    return {
        "latest_rows_checked": len(rows),
        "action_family_counts": dict(action_family_counts),
        "input_reported_not_in_prompt": input_reported_not_in_prompt[:30],
        "input_reported_not_in_prompt_count": len(input_reported_not_in_prompt),
        "actual_prompt_mismatch": actual_prompt_mismatch[:30],
        "actual_prompt_mismatch_count": len(actual_prompt_mismatch),
        "actual_prompt_unmeasured": actual_prompt_unmeasured[:30],
        "actual_prompt_unmeasured_count": len(actual_prompt_unmeasured),
        "legacy_input_reported_mismatch": legacy_input_reported_mismatch[:30],
        "legacy_input_reported_mismatch_count": len(legacy_input_reported_mismatch),
        "trace_join_missing": trace_join_missing[:30],
        "trace_join_missing_count": len(trace_join_missing),
        "trade_ready_family_mismatch": trade_ready_family_mismatch[:30],
        "trade_ready_family_mismatch_count": len(trade_ready_family_mismatch),
        "invalid_price": invalid_price[:30],
        "invalid_price_count": len(invalid_price),
        "invalid_price_reason_counts": dict(invalid_price_reason_counts.most_common()),
    }


def _actual_prompt_flag(row: dict[str, Any]) -> tuple[bool, bool]:
    value = row.get("actual_prompt_included")
    if value is None:
        return False, False
    return True, _truthy_flag(value)


def actual_prompt_profit_visibility(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {
        "included": [],
        "not_included": [],
        "unmeasured": [],
    }
    contract_rows = 0
    trace_missing = 0
    for row in rows:
        if str(row.get("visibility_contract_version") or "").strip() == "actual_prompt_v1":
            contract_rows += 1
        if not str(row.get("selection_trace_id") or "").strip():
            trace_missing += 1
        measured, included = _actual_prompt_flag(row)
        if not measured:
            groups["unmeasured"].append(row)
        elif included:
            groups["included"].append(row)
        else:
            groups["not_included"].append(row)

    included_summary = _metric_summary(groups["included"])
    not_included_summary = _metric_summary(groups["not_included"])
    included_labeled = int(included_summary.get("labeled_rows") or 0)
    not_included_labeled = int(not_included_summary.get("labeled_rows") or 0)
    measured_rows = len(groups["included"]) + len(groups["not_included"])
    sample_ready = included_labeled >= 5 and not_included_labeled >= 5
    status = (
        "ready"
        if sample_ready
        else ("partial" if included_labeled or not_included_labeled else ("awaiting_outcomes" if measured_rows else "missing"))
    )
    return {
        "contract": "actual_prompt_v1",
        "status": status,
        "rows": len(rows),
        "contract_rows": contract_rows,
        "measured_rows": measured_rows,
        "unmeasured_rows": len(groups["unmeasured"]),
        "trace_missing_count": trace_missing,
        "groups": {
            "included": included_summary,
            "not_included": not_included_summary,
            "unmeasured": _metric_summary(groups["unmeasured"]),
        },
        "delta_included_minus_not_included_mean_return_pct": _round(
            (included_summary.get("mean_return_pct") or 0.0) - (not_included_summary.get("mean_return_pct") or 0.0)
            if included_summary.get("mean_return_pct") is not None and not_included_summary.get("mean_return_pct") is not None
            else None
        ),
        "delta_included_minus_not_included_mean_mfe_pct": _round(
            (included_summary.get("mean_mfe_pct") or 0.0) - (not_included_summary.get("mean_mfe_pct") or 0.0)
            if included_summary.get("mean_mfe_pct") is not None and not_included_summary.get("mean_mfe_pct") is not None
            else None
        ),
        "interpretation": (
            "actual_prompt_comparison_ready"
            if sample_ready
            else (
                "actual_prompt_measured_waiting_for_outcomes"
                if measured_rows and not (included_labeled or not_included_labeled)
                else "collect_more_outcomes_before_strategy_judgment"
            )
        ),
    }


def bucket_source_score_quality(rows: list[dict[str, Any]], *, limit: int = 10) -> dict[str, Any]:
    total = len(rows)
    blank_bucket: list[dict[str, Any]] = []
    blank_source: list[dict[str, Any]] = []
    raw_score_missing: list[dict[str, Any]] = []
    trainer_score_missing: list[dict[str, Any]] = []
    source_tag_missing: list[dict[str, Any]] = []
    bucket_gap_rows: list[dict[str, Any]] = []
    bucket_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    for row in rows:
        primary_bucket = str(row.get("primary_bucket") or "").strip()
        source = str(row.get("candidate_source") or row.get("source_file") or "").strip()
        source_tags = _json_list(row.get("source_tags_json"))
        bucket_gaps = _json_list(row.get("bucket_data_gaps_json"))
        bucket_counts[primary_bucket or "blank"] += 1
        source_counts[source or "blank"] += 1
        item = {
            "candidate_key": row.get("candidate_key"),
            "session_date": row.get("session_date"),
            "market": row.get("market"),
            "ticker": row.get("ticker"),
            "classification": row.get("classification"),
            "primary_bucket": primary_bucket,
            "candidate_source": source,
            "route_final_action": row.get("route_final_action") or "",
        }
        if not primary_bucket:
            blank_bucket.append(item)
        if not source:
            blank_source.append(item)
        if _to_float(row.get("raw_score_current")) is None:
            raw_score_missing.append(item)
        if _to_float(row.get("trainer_prompt_score")) is None:
            trainer_score_missing.append(item)
        if not source_tags:
            source_tag_missing.append(item)
        if bucket_gaps:
            bucket_gap_rows.append({**item, "bucket_data_gaps": bucket_gaps})

    return {
        "rows": total,
        "blank_primary_bucket_count": len(blank_bucket),
        "blank_primary_bucket_rate": _round(len(blank_bucket) / total if total else None),
        "blank_source_count": len(blank_source),
        "blank_source_rate": _round(len(blank_source) / total if total else None),
        "raw_score_missing_count": len(raw_score_missing),
        "raw_score_missing_rate": _round(len(raw_score_missing) / total if total else None),
        "trainer_score_missing_count": len(trainer_score_missing),
        "trainer_score_missing_rate": _round(len(trainer_score_missing) / total if total else None),
        "source_tag_missing_count": len(source_tag_missing),
        "source_tag_missing_rate": _round(len(source_tag_missing) / total if total else None),
        "bucket_data_gap_count": len(bucket_gap_rows),
        "bucket_counts": dict(bucket_counts.most_common(20)),
        "source_counts": dict(source_counts.most_common(20)),
        "examples": {
            "blank_primary_bucket": blank_bucket[:limit],
            "blank_source": blank_source[:limit],
            "raw_score_missing": raw_score_missing[:limit],
            "bucket_data_gaps": bucket_gap_rows[:limit],
        },
        "status": "ok" if total and len(blank_bucket) / total < 0.10 else ("missing" if not total else "needs_review"),
        "interpretation": "bucket_source_score_queryable" if total else "no_candidate_rows",
    }


def entry_exit_shadow_readiness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [
        row
        for row in rows
        if int(row.get("filled_count") or 0) > 0
        or str(row.get("first_fill_at") or "").strip()
        or _to_float(row.get("return_pct")) is not None
        or _to_float(row.get("max_runup_pct")) is not None
    ]
    timing_rows = [
        row
        for row in rows
        if _json_dict(row.get("entry_timing_snapshot_json")) or _to_float(row.get("entry_delay_min")) is not None
    ]
    post_open_rows = [row for row in rows if _json_dict(row.get("post_open_features_json"))]
    mfe_rows = [
        row
        for row in rows
        if _to_float(row.get("max_runup_pct")) is not None or _to_float(row.get("position_mfe_pct")) is not None
    ]
    mae_rows = [
        row
        for row in rows
        if _to_float(row.get("max_drawdown_pct")) is not None or _to_float(row.get("position_mae_pct")) is not None
    ]
    session_dates = sorted({str(row.get("session_date") or "") for row in filled if row.get("session_date")})
    span_days = 0
    if len(session_dates) >= 2:
        try:
            span_days = (datetime.fromisoformat(session_dates[-1]) - datetime.fromisoformat(session_dates[0])).days + 1
        except Exception:
            span_days = 0
    by_session = Counter(str(row.get("session_date") or "unknown") for row in filled)
    top_day_count = max(by_session.values()) if by_session else 0
    top_day_contribution = top_day_count / len(filled) if filled else None
    sample_gate_passed = len(filled) >= 30 or span_days >= 28
    concentration_ok = top_day_contribution is not None and top_day_contribution < 0.40
    blockers: list[str] = []
    if not sample_gate_passed:
        blockers.append("sample_gate_not_met")
    if top_day_contribution is None or not concentration_ok:
        blockers.append("top_day_concentration_high_or_unknown")
    if not timing_rows:
        blockers.append("entry_timing_snapshot_missing")
    if not post_open_rows:
        blockers.append("post_open_features_missing")
    if not mfe_rows or not mae_rows:
        blockers.append("mfe_mae_observation_missing")
    return {
        "status": "promotion_ready" if not blockers else "observe_only",
        "rows": len(rows),
        "filled_rows": len(filled),
        "unique_filled_sessions": len(session_dates),
        "filled_session_span_days": span_days,
        "top_day_contribution": _round(top_day_contribution),
        "entry_timing_snapshot_rows": len(timing_rows),
        "post_open_feature_rows": len(post_open_rows),
        "mfe_observed_rows": len(mfe_rows),
        "mae_observed_rows": len(mae_rows),
        "sample_gate_passed": sample_gate_passed,
        "blockers": blockers,
        "interpretation": (
            "safe_to_compare_entry_exit_shadow"
            if not blockers
            else "keep_shadow_until_sample_and_feature_gates_pass"
        ),
    }


def timing_snapshot_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    snapshot_rows = 0
    entry_delay_rows = 0
    first_signal_check_rows = 0
    candidate_to_signal_rows = 0
    candidate_to_order_rows = 0
    snapshot_source_counts: Counter[str] = Counter()
    examples_missing: list[dict[str, Any]] = []
    for row in rows:
        snapshot = _json_dict(row.get("entry_timing_snapshot_json"))
        has_snapshot = bool(snapshot)
        if has_snapshot:
            snapshot_rows += 1
            source = str(snapshot.get("snapshot_source") or snapshot.get("candidate_source") or "entry_timing").strip()
            snapshot_source_counts[source or "unknown"] += 1
            if _to_float(snapshot.get("candidate_to_first_signal_check_delay_min")) is not None:
                first_signal_check_rows += 1
            if _to_float(snapshot.get("candidate_to_signal_delay_min")) is not None:
                candidate_to_signal_rows += 1
            if _to_float(snapshot.get("candidate_to_order_delay_min")) is not None:
                candidate_to_order_rows += 1
        if _to_float(row.get("entry_delay_min")) is not None:
            entry_delay_rows += 1
        if not has_snapshot and len(examples_missing) < 10:
            examples_missing.append(
                {
                    "market": row.get("market"),
                    "session_date": row.get("session_date"),
                    "ticker": row.get("ticker"),
                    "classification": row.get("classification"),
                    "call_id": row.get("call_id"),
                    "known_at": row.get("known_at"),
                }
            )
    return {
        "rows": total,
        "entry_timing_snapshot_rows": snapshot_rows,
        "entry_timing_snapshot_rate": _round(snapshot_rows / total if total else None),
        "entry_delay_rows": entry_delay_rows,
        "entry_delay_rate": _round(entry_delay_rows / total if total else None),
        "candidate_to_first_signal_check_rows": first_signal_check_rows,
        "candidate_to_first_signal_check_rate": _round(first_signal_check_rows / total if total else None),
        "candidate_to_signal_rows": candidate_to_signal_rows,
        "candidate_to_signal_rate": _round(candidate_to_signal_rows / total if total else None),
        "candidate_to_order_rows": candidate_to_order_rows,
        "candidate_to_order_rate": _round(candidate_to_order_rows / total if total else None),
        "snapshot_source_counts": dict(snapshot_source_counts.most_common()),
        "missing_examples": examples_missing,
        "interpretation": "queryable" if total else "no_candidate_rows",
    }


def _coverage_maturity(coverage_rate: float | None) -> str:
    if coverage_rate is None:
        return "missing"
    if coverage_rate < 0.20:
        return "immature"
    if coverage_rate < 0.60:
        return "partial"
    return "ready"


def _coverage_interpretation(coverage_rate: float | None) -> str:
    maturity = _coverage_maturity(coverage_rate)
    if maturity == "ready":
        return "comparison_ready"
    if maturity == "partial":
        return "reference_only"
    if maturity == "immature":
        return "do_not_interpret_yet"
    return "no_outcome_rows"


def _route_shadow_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    watched_reasons = {
        "probe_blocked_above_pathb_zone",
        "probe_ready_cancels_pathb_above_zone",
        "watch_suspends_stale_pathb",
        "claude_avoid",
    }
    summary: dict[str, Any] = {
        "route_suspend_pathb_rows": 0,
        "route_cancel_pathb_rows": 0,
        "reason_counts": {},
        "tickers_by_reason": {},
    }
    for row in rows:
        reason = str(row.get("route_reason") or "").strip()
        suspend = int(row.get("route_suspend_pathb") or 0) > 0
        cancel = int(row.get("route_cancel_pathb") or 0) > 0
        if suspend:
            summary["route_suspend_pathb_rows"] += 1
        if cancel:
            summary["route_cancel_pathb_rows"] += 1
        if reason not in watched_reasons and not suspend and not cancel:
            continue
        key = reason or ("route_suspend_pathb" if suspend else "route_cancel_pathb")
        summary["reason_counts"][key] = int(summary["reason_counts"].get(key, 0) or 0) + 1
        tickers = summary["tickers_by_reason"].setdefault(key, set())
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker:
            tickers.add(ticker)
    summary["tickers_by_reason"] = {
        reason: sorted(tickers)[:30] for reason, tickers in summary["tickers_by_reason"].items()
    }
    return summary


def _iter_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except Exception:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _session_day(session_date: str) -> str:
    return str(session_date or "").replace("-", "") or "*"


def _market_part(market: str) -> str:
    return str(market or "").upper() or "*"


def _latest_json_file_timestamp(paths: list[Path], key: str) -> tuple[datetime | None, str]:
    latest: datetime | None = None
    latest_path = ""
    for path in paths:
        row = _read_json(path)
        candidate = _parse_dt(row.get(key))
        if candidate is None:
            try:
                candidate = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except Exception:
                candidate = None
        if candidate is not None and (latest is None or candidate > latest):
            latest = candidate
            latest_path = str(path)
    return latest, latest_path


def _latest_jsonl_timestamp(paths: list[Path], key: str) -> tuple[datetime | None, str]:
    latest: datetime | None = None
    latest_path = ""
    for path in paths:
        for row in _iter_jsonl_rows(path):
            candidate = _parse_dt(row.get(key))
            if candidate is not None and (latest is None or candidate > latest):
                latest = candidate
                latest_path = str(path)
    return latest, latest_path


def _latest_audit_db_call(conn: sqlite3.Connection, *, session_date: str, market: str, runtime_mode: str) -> datetime | None:
    params: list[Any] = [str(runtime_mode or "live").lower()]
    where = ["runtime_mode=?"]
    if session_date:
        where.append("session_date=?")
        params.append(session_date)
    if market:
        where.append("market=?")
        params.append(str(market).upper())
    row = conn.execute(
        f"""
        SELECT MAX(called_at) AS latest_at
        FROM audit_claude_calls
        WHERE {' AND '.join(where)}
        """,
        params,
    ).fetchone()
    return _parse_dt(row["latest_at"] if row else "")


def audit_freshness_summary(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    market: str,
    runtime_mode: str = "live",
) -> dict[str, Any]:
    day = _session_day(session_date)
    market_key = _market_part(market)
    db_latest = _latest_audit_db_call(
        conn,
        session_date=session_date,
        market=market_key,
        runtime_mode=runtime_mode,
    )
    raw_latest, raw_path = _latest_json_file_timestamp(
        sorted(get_runtime_path("logs", "raw_calls").glob(f"{day}_{market_key}_select_tickers*.json")),
        "timestamp",
    )
    source_specs = {
        "raw_calls": (raw_latest, raw_path),
        "candidate_funnel_snapshot": _latest_jsonl_timestamp(
            sorted(get_runtime_path("logs", "funnel").glob(f"candidate_funnel_snapshot_{day}_{market_key}.jsonl")),
            "written_at",
        ),
        "screener_quality": _latest_jsonl_timestamp(
            sorted(get_runtime_path("logs", "screener_quality").glob(f"{day}_{market_key}_candidates.jsonl")),
            "timestamp",
        ),
        "watch_trigger_shadow": _latest_jsonl_timestamp(
            sorted(get_runtime_path("logs", "funnel").glob(f"watch_trigger_shadow_{day}_{market_key}.jsonl")),
            "written_at",
        ),
        "watch_trigger_not_evaluated": _latest_jsonl_timestamp(
            sorted(get_runtime_path("logs", "funnel").glob(f"watch_trigger_not_evaluated_{day}_{market_key}.jsonl")),
            "written_at",
        ),
    }
    sources: dict[str, dict[str, Any]] = {}
    max_lag_sec = 0
    stale_sources: list[str] = []
    for name, (latest, path) in source_specs.items():
        lag_sec = None
        if latest is not None and db_latest is not None:
            lag_sec = max(int((latest - db_latest).total_seconds()), 0)
            max_lag_sec = max(max_lag_sec, lag_sec)
            if lag_sec > FRESHNESS_WARN_SEC:
                stale_sources.append(name)
        sources[name] = {
            "latest_at": _iso(latest),
            "path": path,
            "lag_sec": lag_sec,
            "stale": lag_sec is not None and lag_sec > FRESHNESS_WARN_SEC,
        }
    status = "missing_db" if db_latest is None else ("stale" if stale_sources else "ok")
    return {
        "db_latest_at": _iso(db_latest),
        "max_lag_sec": max_lag_sec if db_latest is not None else None,
        "warn_threshold_sec": FRESHNESS_WARN_SEC,
        "status": status,
        "stale_sources": stale_sources,
        "sources": sources,
    }


def _latest_candidate_funnel_snapshot(*, session_date: str, market: str) -> dict[str, Any]:
    day = _session_day(session_date)
    market_key = _market_part(market)
    rows: list[dict[str, Any]] = []
    for path in sorted(get_runtime_path("logs", "funnel").glob(f"candidate_funnel_snapshot_{day}_{market_key}.jsonl")):
        rows.extend(_iter_jsonl_rows(path))
    rows.sort(key=lambda row: _parse_dt(row.get("written_at")) or datetime.min.replace(tzinfo=timezone.utc))
    return rows[-1] if rows else {}


def routing_delta_summary(*, session_date: str = "", market: str = "") -> dict[str, Any]:
    latest = _latest_candidate_funnel_snapshot(session_date=session_date, market=market)
    if not latest:
        return {"exists": False, "status": "missing"}
    stages = latest.get("selection_stages") if isinstance(latest.get("selection_stages"), dict) else {}
    raw = stages.get("raw") if isinstance(stages.get("raw"), dict) else {}
    normalized = stages.get("normalized") if isinstance(stages.get("normalized"), dict) else {}
    applied = stages.get("applied") if isinstance(stages.get("applied"), dict) else {}
    selection_delta = latest.get("selection_delta") if isinstance(latest.get("selection_delta"), dict) else {}
    routes = latest.get("candidate_action_routes") if isinstance(latest.get("candidate_action_routes"), list) else []
    reason_counts: Counter[str] = Counter()
    final_action_counts: Counter[str] = Counter()
    demoted: list[dict[str, Any]] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        final_action = str(route.get("final_action") or "unknown")
        reason = str(route.get("reason") or route.get("runtime_gate_reason") or "unknown")
        final_action_counts[final_action] += 1
        reason_counts[reason] += 1
        original = str(route.get("original_action") or route.get("requested_action") or "")
        if original and final_action and original != final_action:
            demoted.append(
                {
                    "ticker": str(route.get("ticker") or "").upper(),
                    "original_action": original,
                    "final_action": final_action,
                    "reason": reason,
                }
            )
    raw_ready = [
        str(t).upper()
        for t in (
            selection_delta.get("raw_trade_ready")
            or latest.get("raw_trade_ready")
            or raw.get("trade_ready")
            or []
        )
    ]
    normalized_ready = [
        str(t).upper()
        for t in (
            selection_delta.get("normalized_trade_ready")
            or latest.get("normalized_trade_ready")
            or normalized.get("trade_ready")
            or []
        )
    ]
    applied_ready = [
        str(t).upper()
        for t in (
            selection_delta.get("applied_trade_ready")
            or latest.get("applied_trade_ready")
            or applied.get("trade_ready")
            or []
        )
    ]
    runtime_filtered = latest.get("runtime_filtered") or selection_delta.get("runtime_filtered") or {}
    runtime_filtered_reason_counts = (
        latest.get("runtime_filtered_reason_counts")
        or selection_delta.get("runtime_filtered_reason_counts")
        or dict(Counter(str(value) for value in runtime_filtered.values()).most_common())
    )
    return {
        "exists": True,
        "status": "ok",
        "latest_at": str(latest.get("written_at") or ""),
        "full_pool_count": int(latest.get("full_pool_count") or 0),
        "prompt_pool_count": int(latest.get("prompt_pool_count") or 0),
        "execution_pool_count": int(latest.get("execution_pool_count") or 0),
        "watchlist_count": int(latest.get("watchlist_count") or 0),
        "trade_ready_count": int(latest.get("trade_ready_count") or 0),
        "raw_trade_ready_count": len(raw_ready),
        "normalized_trade_ready_count": len(normalized_ready),
        "applied_trade_ready_count": len(applied_ready),
        "raw_trade_ready": raw_ready,
        "normalized_trade_ready": normalized_ready,
        "applied_trade_ready": applied_ready,
        "dropped_after_raw": list(selection_delta.get("dropped_after_raw") or latest.get("dropped_after_raw") or sorted(set(raw_ready) - set(applied_ready))),
        "runtime_filtered": runtime_filtered,
        "runtime_filtered_reason_counts": runtime_filtered_reason_counts,
        "runtime_filtered_count": int(latest.get("runtime_filtered_count") or 0),
        "selection_delta": selection_delta,
        "universe_filter_bypass": latest.get("universe_filter_bypass") or {},
        "pathb_wait_tickers": latest.get("pathb_wait_tickers") or [],
        "route_reason_counts": dict(reason_counts.most_common()),
        "final_action_counts": dict(final_action_counts.most_common()),
        "demoted_routes": demoted[:20],
    }


def latency_sla_summary(*, session_date: str = "", market: str = "") -> dict[str, Any]:
    day = _session_day(session_date)
    market_key = _market_part(market)
    rows: list[dict[str, Any]] = []
    for path in sorted(get_runtime_path("logs", "funnel").glob(f"candidate_cycle_latency_{day}_{market_key}.jsonl")):
        rows.extend(_iter_jsonl_rows(path))
    values = [_to_float(row.get("elapsed_ms")) for row in rows]
    clean = [value for value in values if value is not None]
    alert_count = sum(1 for row in rows if bool(row.get("alert")))
    max_ms = max(clean) if clean else None
    status = "missing"
    if clean:
        status = "critical" if (max_ms or 0.0) > 60000 else ("warn" if alert_count > 0 or (max_ms or 0.0) > 25000 else "ok")
    return {
        "exists": bool(clean),
        "status": status,
        "rows": len(rows),
        "alert_count": alert_count,
        "avg_ms": _round(_mean(clean), 3),
        "max_ms": _round(max_ms, 3),
        "p95_ms": _round(_percentile(clean, 0.95), 3),
        "warn_threshold_ms": 25000,
        "critical_threshold_ms": 60000,
    }


def _watch_trigger_funnel_paths(event_type: str, *, session_date: str, market: str) -> list[Path]:
    log_dir = get_runtime_path("logs", "funnel")
    day = str(session_date or "").replace("-", "") or "*"
    market_part = str(market or "").upper() or "*"
    return sorted(log_dir.glob(f"{event_type}_{day}_{market_part}.jsonl"))


def watch_trigger_funnel_summary(*, session_date: str = "", market: str = "") -> dict[str, Any]:
    not_evaluated_rows: list[dict[str, Any]] = []
    shadow_rows: list[dict[str, Any]] = []
    for path in _watch_trigger_funnel_paths(
        "watch_trigger_not_evaluated",
        session_date=session_date,
        market=market,
    ):
        not_evaluated_rows.extend(_iter_jsonl_rows(path))
    for path in _watch_trigger_funnel_paths(
        "watch_trigger_shadow",
        session_date=session_date,
        market=market,
    ):
        shadow_rows.extend(_iter_jsonl_rows(path))

    result_counts: Counter[str] = Counter()
    blocked_reason_counts: Counter[str] = Counter()
    not_eval_reason_counts: Counter[str] = Counter()
    strategy_counts: Counter[str] = Counter()
    strategy_source_counts: Counter[str] = Counter()
    tickers_by_result: dict[str, set[str]] = defaultdict(set)
    for row in not_evaluated_rows:
        reason = str(row.get("reason") or "unknown")
        not_eval_reason_counts[reason] += 1
    for row in shadow_rows:
        result = str(row.get("result") or "unknown")
        result_counts[result] += 1
        strategy = str(row.get("strategy") or "unassigned")
        strategy_counts[strategy] += 1
        strategy_source = str(row.get("strategy_source") or "unassigned")
        strategy_source_counts[strategy_source] += 1
        blocked = str(row.get("blocked_reason") or "").strip()
        if blocked:
            blocked_reason_counts[blocked] += 1
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker:
            tickers_by_result[result].add(ticker)

    missing_strategy_count = int(blocked_reason_counts.get("missing_strategy", 0))
    blocked_count = int(result_counts.get("blocked", 0))
    return {
        "watch_trigger_not_evaluated_count": len(not_evaluated_rows),
        "watch_trigger_shadow_count": len(shadow_rows),
        "watch_trigger_would_promote_count": int(result_counts.get("would_promote", 0)),
        "watch_trigger_no_signal_count": int(result_counts.get("no_signal", 0)),
        "watch_trigger_blocked_count": blocked_count,
        "missing_strategy_count": missing_strategy_count,
        "missing_strategy_rate": _round(missing_strategy_count / blocked_count if blocked_count else None),
        "data_gap_dominant": blocked_count > 0 and missing_strategy_count / blocked_count >= 0.5,
        "not_evaluated_reason_counts": dict(not_eval_reason_counts.most_common()),
        "shadow_result_counts": dict(result_counts.most_common()),
        "blocked_reason_counts": dict(blocked_reason_counts.most_common()),
        "strategy_counts": dict(strategy_counts.most_common()),
        "strategy_source_counts": dict(strategy_source_counts.most_common()),
        "tickers_by_result": {
            result: sorted(tickers)[:30]
            for result, tickers in sorted(tickers_by_result.items())
        },
    }


def _watch_trigger_shadow_outcomes(
    rows: list[dict[str, Any]],
    *,
    session_date: str = "",
    market: str = "",
) -> dict[str, Any]:
    shadow_rows: list[dict[str, Any]] = []
    for path in _watch_trigger_funnel_paths(
        "watch_trigger_shadow",
        session_date=session_date,
        market=market,
    ):
        shadow_rows.extend(_iter_jsonl_rows(path))

    audit_by_ticker: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        market_key = str(row.get("market") or "").upper()
        ticker = str(row.get("ticker") or "").strip().upper()
        if market_key and ticker:
            audit_by_ticker[(market_key, ticker)].append(row)

    events_by_result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    matched_by_result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in shadow_rows:
        result = str(event.get("result") or "unknown")
        events_by_result[result].append(event)
        market_key = str(event.get("market") or "").upper()
        ticker = str(event.get("ticker") or "").strip().upper()
        matches = audit_by_ticker.get((market_key, ticker), [])
        if matches:
            matched_by_result[result].append(matches[0])

    out: dict[str, Any] = {}
    for result, events in sorted(events_by_result.items()):
        matched = matched_by_result.get(result, [])
        out[result] = {
            "shadow_events": len(events),
            "matched_outcomes": len(matched),
            **_metric_summary(matched),
        }
    return out


WATCH_ONLY_BUCKET_CLASSES = {
    "watch_only",
    "in_prompt_not_selected",
    "not_in_prompt",
    "ready_no_signal",
    "avoid_watch",
    "data_insufficient",
}


def _json_list(value: Any) -> list[Any]:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = []
    return list(parsed) if isinstance(parsed, list) else []


def _json_dict(value: Any) -> dict[str, Any]:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _row_text_blob(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "classification",
        "claude_action",
        "claude_reason",
        "claude_veto_reason",
        "route_original_action",
        "route_final_action",
        "route_route",
        "route_reason",
        "route_demoted_to",
        "route_runtime_gate_reason",
        "action_ceiling",
        "evidence_data_state",
        "evidence_action_ceiling",
        "close_reason",
    ):
        value = row.get(key)
        if value not in (None, ""):
            parts.append(str(value))
    for key in ("route_warnings_json", "evidence_missing_fields_json", "failed_ready_reasons_json"):
        for item in _json_list(row.get(key)):
            parts.append(str(item))
    return " ".join(parts).lower()


def _is_route_demoted(row: dict[str, Any]) -> bool:
    demoted_to = str(row.get("route_demoted_to") or "").strip()
    if demoted_to:
        return True
    original = str(row.get("route_original_action") or "").strip().upper()
    final = str(row.get("route_final_action") or "").strip().upper()
    if original and final and original != final:
        return True
    return False


def _watch_only_primary_bucket(row: dict[str, Any]) -> tuple[str, str]:
    classification = str(row.get("classification") or "unknown")
    text = _row_text_blob(row)
    if classification == "not_in_prompt":
        return "not_in_prompt", "candidate never reached Claude prompt"
    if classification == "in_prompt_not_selected":
        return "claude_not_selected", "candidate was in prompt but not selected"
    if bool(row.get("evidence_ceiling_applied")) or "evidence" in text or "data_fail" in text:
        return "evidence_ceiling", "evidence/data ceiling blocked readiness"
    if "pathb" in text or "path_b" in text or "zone" in text or int(row.get("path_run_count") or 0) > 0:
        return "pathb_zone_or_plan", "PathB zone/plan state prevented immediate entry"
    risk_terms = (
        "risk",
        "afford",
        "cash",
        "broker",
        "quarantine",
        "late",
        "blackout",
        "same_day",
        "reentry",
        "hard_block",
    )
    if any(term in text for term in risk_terms):
        return "risk_or_affordability", "risk, broker, cash, late-session, or reentry block"
    if _is_route_demoted(row):
        return "routing_demotion", "route changed Claude action before execution"
    if classification == "ready_no_signal" or int(row.get("no_signal_count") or 0) > 0:
        return "strategy_no_signal", "ready candidate did not trigger a strategy signal"
    final_action = str(row.get("route_final_action") or row.get("claude_action") or "").upper()
    if final_action in {"WATCH", "PULLBACK_WAIT", "WAIT"}:
        return "claude_watch_conservative", "Claude or route left candidate in watch state"
    if classification == "data_insufficient":
        return "data_insufficient", "candidate data was insufficient before prompt/execution"
    return "unknown", "not enough audit detail to classify"


def watch_only_bucket_decomposition(
    rows: list[dict[str, Any]],
    *,
    limit: int = 10,
    missed_runup_pct: float = MISSED_WINNER_MFE_PCT,
) -> dict[str, Any]:
    candidates = [
        row
        for row in rows
        if str(row.get("classification") or "unknown") in WATCH_ONLY_BUCKET_CLASSES
        or str(row.get("route_final_action") or "").upper() in {"WATCH", "PULLBACK_WAIT"}
    ]
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reasons: dict[str, str] = {}
    for row in candidates:
        bucket, reason = _watch_only_primary_bucket(row)
        buckets[bucket].append(row)
        reasons.setdefault(bucket, reason)

    bucket_rows: list[dict[str, Any]] = []
    for bucket, items in buckets.items():
        missed = [
            row
            for row in items
            if (_to_float(row.get("max_runup_pct")) or float("-inf")) >= float(missed_runup_pct)
        ]
        bucket_rows.append(
            {
                "bucket": bucket,
                "reason": reasons.get(bucket, ""),
                "rows": len(items),
                "missed_runup_rows": len(missed),
                "missed_runup_rate": _round(len(missed) / len(items) if items else None),
                **_metric_summary(items),
            }
        )
    bucket_rows.sort(key=lambda row: (int(row.get("missed_runup_rows") or 0), int(row.get("rows") or 0)), reverse=True)

    examples: dict[str, list[dict[str, Any]]] = {}
    for bucket, items in buckets.items():
        ranked = sorted(
            items,
            key=lambda row: _to_float(row.get("max_runup_pct")) or float("-inf"),
            reverse=True,
        )
        examples[bucket] = [
            {
                "session_date": row.get("session_date"),
                "market": row.get("market"),
                "ticker": row.get("ticker"),
                "classification": row.get("classification"),
                "claude_action": row.get("claude_action") or "",
                "route_final_action": row.get("route_final_action") or "",
                "route_reason": row.get("route_reason") or row.get("route_runtime_gate_reason") or "",
                "return_pct": _round(_to_float(row.get("return_pct"))),
                "max_runup_pct": _round(_to_float(row.get("max_runup_pct"))),
                "max_drawdown_pct": _round(_to_float(row.get("max_drawdown_pct"))),
                "execution_decision_id": row.get("execution_decision_id") or "",
            }
            for row in ranked[: max(int(limit or 1), 1)]
        ]

    return {
        "rows_considered": len(candidates),
        "missed_runup_threshold_pct": float(missed_runup_pct),
        "bucket_count": len(bucket_rows),
        "buckets": bucket_rows,
        "examples": examples,
        "interpretation": (
            "Use this to choose the next narrow fix. Do not open global gates from a high blocked ratio alone."
        ),
    }


def analyze_candidate_audit(
    *,
    db_path: str | Path | None = None,
    session_date: str = "",
    market: str = "",
    runtime_mode: str = "live",
    horizon_min: int = 60,
    limit: int = 10,
    latest_only: bool = True,
) -> dict[str, Any]:
    target = Path(db_path) if db_path else get_runtime_path("data", "audit", "candidate_audit.db")
    conn = sqlite3.connect(str(target), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = _load_outcome_rows(
            conn,
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
            horizon_min=horizon_min,
            latest_only=latest_only,
        )
        coverage = _outcome_coverage(
            conn,
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
        )
        freshness = audit_freshness_summary(
            conn,
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
        )
        row_uniqueness = _row_uniqueness_summary(
            conn,
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
        )
        consistency = _candidate_consistency_summary(
            conn,
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
        )
    finally:
        conn.close()

    by_class: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_class.setdefault(str(row.get("classification") or "unknown"), []).append(row)
    buckets = [
        {"classification": name, **_metric_summary(items)}
        for name, items in sorted(by_class.items(), key=lambda item: len(item[1]), reverse=True)
    ]
    top_classes = ["not_in_prompt", "in_prompt_not_selected", "ready_no_signal", "avoid_watch"]
    return {
        "db_path": str(target),
        "session_date": session_date,
        "market": str(market or "").upper(),
        "runtime_mode": str(runtime_mode or "live").lower(),
        "horizon_min": int(horizon_min),
        "candidate_rows": len(rows),
        "latest_only": bool(latest_only),
        "row_uniqueness": row_uniqueness,
        "consistency": consistency,
        "freshness": freshness,
        "outcome_coverage": coverage,
        "actual_prompt_profit_visibility": actual_prompt_profit_visibility(rows),
        "bucket_source_score_quality": bucket_source_score_quality(rows, limit=limit),
        "entry_exit_shadow_readiness": entry_exit_shadow_readiness(rows),
        "timing_snapshot_coverage": timing_snapshot_coverage(rows),
        "buckets": buckets,
        "missed_winners": missed_winners(
            rows,
            limit=limit,
            horizon_min=int(horizon_min),
        ),
        "top_mfe": {
            name: _top_by_mfe(rows, name, limit=limit, horizon_min=int(horizon_min))
            for name in top_classes
        },
        "routing_delta": routing_delta_summary(
            session_date=session_date,
            market=market,
        ),
        "latency_sla": latency_sla_summary(
            session_date=session_date,
            market=market,
        ),
        "route_shadow_summary": _route_shadow_summary(rows),
        "strategy_mismatch": _strategy_mismatch(rows),
        "watch_trigger_shadow_summary": watch_trigger_funnel_summary(
            session_date=session_date,
            market=market,
        ),
        "watch_trigger_shadow_outcomes": _watch_trigger_shadow_outcomes(
            rows,
            session_date=session_date,
            market=market,
        ),
        "watch_only_bucket_decomposition": watch_only_bucket_decomposition(
            rows,
            limit=limit,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze candidate audit outcomes.")
    parser.add_argument("--db", default="", help="candidate audit DB path")
    parser.add_argument("--date", default="", help="session date YYYY-MM-DD")
    parser.add_argument("--market", default="", help="KR or US; empty means all markets")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--horizon-min", type=int, default=60)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--call-level", action="store_true", help="use raw call-level rows instead of latest session/ticker rows")
    args = parser.parse_args()
    result = analyze_candidate_audit(
        db_path=args.db or None,
        session_date=args.date,
        market=args.market,
        runtime_mode=args.runtime_mode,
        horizon_min=args.horizon_min,
        limit=args.limit,
        latest_only=not bool(args.call_level),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
