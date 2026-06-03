from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(data: Any) -> str:
    if data is None:
        data = {}
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def _int_bool(value: Any) -> int:
    return 1 if bool(value) else 0


def candidate_key(*, session_date: str, market: str, call_id: str, ticker: str) -> str:
    raw = "|".join(
        [
            str(session_date or ""),
            str(market or "").upper(),
            str(call_id or ""),
            str(ticker or "").strip().upper(),
        ]
    )
    return "cand_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


EXTRA_CANDIDATE_COLUMNS: dict[str, str] = {
    "evidence_version": "TEXT",
    "schema_version": "TEXT",
    "candidate_age_min": "REAL",
    "candidate_source": "TEXT",
    "first_seen_price": "REAL",
    "first_ready_at": "TEXT",
    "first_ready_price": "REAL",
    "was_trade_ready_before": "INTEGER",
    "price_change_since_first_seen_pct": "REAL",
    "price_change_since_first_ready_pct": "REAL",
    "ready_attempt_count": "INTEGER",
    "freshness_verdict": "TEXT",
    "entry_type": "TEXT",
    "setup_maturity": "TEXT",
    "lifecycle_state": "TEXT",
    "lifecycle_rank": "INTEGER",
    "trainer_tier": "TEXT",
    "quarantine_reason": "TEXT",
    "cohort_reliability": "REAL",
    "action_ceiling": "TEXT",
    "action_ceiling_ack": "TEXT",
    "legacy_auto_ready_promoted": "INTEGER",
    "soft_gate_overrides": "TEXT",
    "override_validated": "INTEGER",
    "override_validation_reason": "TEXT",
    "why_not_watch": "TEXT",
    "max_entry_price": "REAL",
    "max_chase_pct": "REAL",
    "hard_blocks": "TEXT",
    "soft_gates": "TEXT",
    "exit_lifecycle_final_action": "TEXT",
    "claude_override_allowed": "INTEGER",
    "exit_owner": "TEXT",
    "bypass_advisor": "INTEGER",
    "metadata_contract_violation": "TEXT",
    "recovery_micro_invariant_restored": "TEXT",
    "max_entry_price_exceeded": "INTEGER",
    "sla_triggered_at": "TEXT",
    "review_called_at": "TEXT",
    "review_latency_sec": "REAL",
    "last_review_age_sec": "REAL",
    "tuning_rule_version": "TEXT",
    "tuning_feedback_applied": "INTEGER",
    "config_hash": "TEXT",
    "feature_flags_json": "TEXT",
    "selection_trace_id": "TEXT",
    "visibility_contract_version": "TEXT",
    "actual_prompt_call_id": "TEXT",
    "actual_prompt_included": "INTEGER",
    "actual_prompt_rank": "INTEGER",
    "reported_input_to_claude": "INTEGER",
    "prompt_join_delta_sec": "REAL",
    "final_prompt_included": "INTEGER",
    "prompt_rank_after_trim": "INTEGER",
    "raw_rank": "INTEGER",
    "raw_score_current": "REAL",
    "raw_score_components_json": "TEXT",
    "trainer_score_rank": "INTEGER",
    "prompt_excluded_reason": "TEXT",
    "trainer_prompt_score": "REAL",
    "trainer_plan_a_score": "REAL",
    "trainer_pathb_wait_score": "REAL",
    "trainer_risk_score": "REAL",
    "trainer_score_components_json": "TEXT",
    "trainer_candidate_state": "TEXT",
    "source_tags_json": "TEXT",
    "bucket_reasons_json": "TEXT",
    "bucket_data_gaps_json": "TEXT",
    "bucket_seen_count": "INTEGER",
    "first_bucket_detected_at": "TEXT",
    "last_bucket_detected_at": "TEXT",
    "earliest_bucket_detected_at": "TEXT",
    "data_quality_flags_json": "TEXT",
    "data_quality": "TEXT",
    "data_quality_missing": "INTEGER",
    "history_status": "TEXT",
    "history_usable_rows": "INTEGER",
    "history_required_rows": "INTEGER",
    "candidate_quality_score": "REAL",
    "quality_data_gaps_json": "TEXT",
    "scorer_input_snapshot_json": "TEXT",
    "scorer_config_hash": "TEXT",
    "stale_cycle": "INTEGER",
    "stale_cycle_count": "INTEGER",
    "repeated_failed_ready_count": "INTEGER",
    "no_fill_cycle_count": "INTEGER",
    "failed_ready_reasons_json": "TEXT",
    "candidate_pool_version": "TEXT",
    "prompt_pool_version": "TEXT",
    "execution_link_source": "TEXT",
    "execution_decision_id": "TEXT",
    "execution_event_id": "INTEGER",
    "no_submit_reason_code": "TEXT",
    "no_submit_reason_detail": "TEXT",
    "no_submit_signal_flags_json": "TEXT",
    "no_submit_block_meta_json": "TEXT",
    "entry_timing_snapshot_json": "TEXT",
    "post_open_features_json": "TEXT",
    "kr_confirmation_snapshot_json": "TEXT",
    "candidate_health_snapshot_json": "TEXT",
    "entry_delay_min": "REAL",
    "entry_price_vs_first_seen_pct": "REAL",
    "entry_price_vs_first_ready_pct": "REAL",
    "position_mfe_pct": "REAL",
    "position_mae_pct": "REAL",
    "us_early_entry_window": "TEXT",
    "us_early_entry_elapsed_min": "REAL",
    "us_early_entry_size_mult": "REAL",
    "us_early_entry_confirmation_reason": "TEXT",
    "us_early_entry_gate_json": "TEXT",
    "evidence_data_state": "TEXT",
    "evidence_missing_fields_json": "TEXT",
    "evidence_action_ceiling": "TEXT",
    "evidence_ceiling_applied": "INTEGER",
    "from_high_pct": "REAL",
    "consensus_mode": "TEXT",
    "strength_capture_shadow": "INTEGER",
    "strength_capture_rules": "TEXT",
    "candidate_pool_role": "TEXT",
    "discovery_signal_family": "TEXT",
    "discovery_reason": "TEXT",
    "discovery_action_ceiling": "TEXT",
    "discovery_baseline_trainer_rank": "INTEGER",
    "discovery_overlay_rank": "INTEGER",
    "discovery_action_ceiling_applied": "INTEGER",
    "discovery_demoted_from": "TEXT",
}

EXTRA_CALL_COLUMNS: dict[str, str] = {
    "actual_prompt_count": "INTEGER DEFAULT 0",
}

_MISSING = object()
_JSON_TEXT_COLUMNS = {"soft_gate_overrides", "hard_blocks", "soft_gates", "strength_capture_rules"}
_PROMPT_STAGE_SOURCE_FILES = {
    "trading_bot.prompt_pool_excluded",
    "trading_bot.prompt_pool",
    "trading_bot.selection_meta",
}
_RUNTIME_FILTER_SOURCE_FILE = "trading_bot.runtime_filter"
_PROMPT_PAYLOAD_KEYS_TO_PRESERVE = {
    "selection_stage",
    "prompt_pool_audit",
    "excluded_reason",
    "screener_quality",
}
_RUNTIME_EVIDENCE_PAYLOAD_KEYS_TO_PRESERVE = {
    "runtime_gate",
    "confirmation_state",
    "confirmation_reason",
    "confirmation_shadow",
    "post_open_features",
    "post_open_features_json",
    "kr_confirmation_snapshot",
    "kr_confirmation_snapshot_json",
}


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for name, column_type in columns.items():
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}")


def _decode_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _meaningful_payload_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _deep_merge_preserve_existing(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing or {})
    for key, value in dict(incoming or {}).items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_preserve_existing(current, value)
        elif _meaningful_payload_value(value) or key not in merged:
            merged[key] = value
    return merged


def _preserve_runtime_evidence_payload(merged: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    result = dict(merged or {})
    source_payload = dict(source or {})
    for key in _RUNTIME_EVIDENCE_PAYLOAD_KEYS_TO_PRESERVE:
        if key not in source_payload:
            continue
        source_value = source_payload.get(key)
        if key == "runtime_gate" and isinstance(source_value, dict):
            current_value = result.get(key) if isinstance(result.get(key), dict) else {}
            result[key] = _deep_merge_preserve_existing(source_value, current_value)
            continue
        if key not in result or not _meaningful_payload_value(result.get(key)):
            result[key] = source_value
    return result


def _row_payload(row: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    if "payload" in row and row.get("payload") is not None:
        return True, _decode_json_object(row.get("payload"))
    if "payload_json" in row and row.get("payload_json") is not None:
        return True, _decode_json_object(row.get("payload_json"))
    return False, {}


def _merge_source_file(existing: str, incoming: str) -> str:
    existing_text = str(existing or "")
    incoming_text = str(incoming or "")
    if not incoming_text:
        return existing_text
    if not existing_text:
        return incoming_text
    if (
        existing_text in _PROMPT_STAGE_SOURCE_FILES
        and incoming_text == _RUNTIME_FILTER_SOURCE_FILE
    ):
        return existing_text
    return incoming_text


def _merge_payload(
    *,
    existing_source_file: str,
    incoming_source_file: str,
    existing_payload_json: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    incoming_present, incoming_payload = _row_payload(row)
    existing_payload = _decode_json_object(existing_payload_json)
    if not incoming_present:
        return existing_payload
    existing_source = str(existing_source_file or "")
    incoming_source = str(incoming_source_file or "")
    existing_is_prompt = existing_source in _PROMPT_STAGE_SOURCE_FILES
    incoming_is_prompt = incoming_source in _PROMPT_STAGE_SOURCE_FILES
    existing_is_runtime = existing_source == _RUNTIME_FILTER_SOURCE_FILE
    incoming_is_runtime = incoming_source == _RUNTIME_FILTER_SOURCE_FILE
    if (existing_is_prompt and incoming_is_runtime) or (existing_is_runtime and incoming_is_prompt):
        prompt_payload = existing_payload if existing_is_prompt else incoming_payload
        runtime_payload = incoming_payload if incoming_is_runtime else existing_payload
        merged = dict(runtime_payload)
        for key in _PROMPT_PAYLOAD_KEYS_TO_PRESERVE:
            if key in prompt_payload:
                merged[key] = prompt_payload[key]
        return _preserve_runtime_evidence_payload(merged, prompt_payload)
    return _preserve_runtime_evidence_payload(incoming_payload, existing_payload)


def _candidate_extra_value(column: str, row: dict[str, Any]) -> Any:
    value = row[column] if column in row else _MISSING
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    if value is _MISSING and isinstance(payload, dict) and column in payload:
        value = payload.get(column)
    if value is _MISSING or value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if column.endswith("_json") or column in _JSON_TEXT_COLUMNS:
        if isinstance(value, str):
            return value
        return _json(value)
    if column in {
        "was_trade_ready_before",
        "legacy_auto_ready_promoted",
        "override_validated",
        "claude_override_allowed",
        "bypass_advisor",
        "tuning_feedback_applied",
        "actual_prompt_included",
        "reported_input_to_claude",
        "data_quality_missing",
        "final_prompt_included",
        "stale_cycle",
        "evidence_ceiling_applied",
        "strength_capture_shadow",
        "discovery_action_ceiling_applied",
    }:
        if value is None:
            return None
        return _int_bool(value)
    return value


class CandidateAuditStore:
    """SQLite store for candidate-level audit data.

    This DB is analysis-only. It intentionally duplicates selected data from
    operating DBs/logs instead of replacing them.
    """

    def __init__(self, path: str | Path, *, timeout: float = 5.0) -> None:
        self.path = Path(path)
        self.timeout = float(timeout or 5.0)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=self.timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def init(self) -> None:
        conn = sqlite3.connect(str(self.path), timeout=self.timeout)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_claude_calls (
                    call_id TEXT PRIMARY KEY,
                    runtime_mode TEXT NOT NULL,
                    market TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    called_at TEXT,
                    label TEXT,
                    model TEXT,
                    prompt_version TEXT,
                    source_file TEXT,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    prompt_candidate_count INTEGER DEFAULT 0,
                    actual_prompt_count INTEGER DEFAULT 0,
                    watchlist_count INTEGER DEFAULT 0,
                    trade_ready_count INTEGER DEFAULT 0,
                    candidate_action_count INTEGER DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_claude_calls_session
                    ON audit_claude_calls(runtime_mode, market, session_date, called_at);

                CREATE TABLE IF NOT EXISTS audit_candidate_rows (
                    candidate_key TEXT PRIMARY KEY,
                    call_id TEXT NOT NULL,
                    runtime_mode TEXT NOT NULL,
                    market TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    known_at TEXT,
                    ticker TEXT NOT NULL,
                    source_file TEXT,
                    prompt_rank INTEGER,
                    in_prompt INTEGER DEFAULT 0,
                    screener_seen INTEGER DEFAULT 0,
                    input_to_claude_reported INTEGER DEFAULT 0,
                    name TEXT,
                    price REAL,
                    change_pct REAL,
                    volume_ratio REAL,
                    turnover REAL,
                    market_type TEXT,
                    liquidity_bucket TEXT,
                    primary_bucket TEXT,
                    secondary_buckets_json TEXT NOT NULL DEFAULT '[]',
                    claude_action TEXT,
                    claude_reason TEXT,
                    claude_veto_reason TEXT,
                    claude_watchlist INTEGER DEFAULT 0,
                    claude_trade_ready INTEGER DEFAULT 0,
                    recommended_strategy TEXT,
                    risk_tags_json TEXT NOT NULL DEFAULT '[]',
                    max_position_pct REAL,
                    route_original_action TEXT,
                    route_final_action TEXT,
                    route_route TEXT,
                    route_reason TEXT,
                    route_demoted_to TEXT,
                    route_runtime_gate_reason TEXT,
                    route_overextended INTEGER DEFAULT 0,
                    route_cancel_pathb INTEGER DEFAULT 0,
                    route_suspend_pathb INTEGER DEFAULT 0,
                    route_warnings_json TEXT NOT NULL DEFAULT '[]',
                    decision_count INTEGER DEFAULT 0,
                    buy_signal_count INTEGER DEFAULT 0,
                    no_signal_count INTEGER DEFAULT 0,
                    watch_only_count INTEGER DEFAULT 0,
                    filled_count INTEGER DEFAULT 0,
                    first_signal_at TEXT,
                    first_fill_at TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    pnl_pct REAL,
                    exit_reason TEXT,
                    strategy_used TEXT,
                    lifecycle_event_count INTEGER DEFAULT 0,
                    last_lifecycle_event TEXT,
                    close_reason TEXT,
                    path_run_count INTEGER DEFAULT 0,
                    intraday_signal_count INTEGER DEFAULT 0,
                    intraday_traded_count INTEGER DEFAULT 0,
                    classification TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_candidate_rows_session
                    ON audit_candidate_rows(runtime_mode, market, session_date, ticker);
                CREATE INDEX IF NOT EXISTS idx_audit_candidate_rows_call
                    ON audit_candidate_rows(call_id, prompt_rank);
                CREATE INDEX IF NOT EXISTS idx_audit_candidate_rows_classification
                    ON audit_candidate_rows(runtime_mode, market, session_date, classification);

                CREATE TABLE IF NOT EXISTS audit_candidate_outcomes (
                    candidate_key TEXT NOT NULL,
                    horizon_min INTEGER NOT NULL,
                    target_at TEXT,
                    observed_at TEXT,
                    observed_price REAL,
                    return_pct REAL,
                    max_runup_pct REAL,
                    max_drawdown_pct REAL,
                    status TEXT NOT NULL,
                    source TEXT,
                    label_generated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    UNIQUE(candidate_key, horizon_min)
                );

                CREATE INDEX IF NOT EXISTS idx_audit_candidate_outcomes_key
                    ON audit_candidate_outcomes(candidate_key, horizon_min);

                CREATE VIEW IF NOT EXISTS audit_candidate_latest_rows AS
                    SELECT *
                    FROM (
                        SELECT
                            r.*,
                            ROW_NUMBER() OVER (
                                PARTITION BY r.runtime_mode, r.market, r.session_date, r.ticker
                                ORDER BY
                                    COALESCE(NULLIF(r.known_at, ''), r.updated_at, r.created_at) DESC,
                                    r.updated_at DESC,
                                    r.candidate_key DESC
                            ) AS latest_rank
                        FROM audit_candidate_rows r
                    )
                    WHERE latest_rank = 1;
                """
            )
            _ensure_columns(conn, "audit_claude_calls", EXTRA_CALL_COLUMNS)
            _ensure_columns(conn, "audit_candidate_rows", EXTRA_CANDIDATE_COLUMNS)
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_candidate_rows_strength_shadow
                    ON audit_candidate_rows(runtime_mode, market, session_date, strength_capture_shadow);
                """
            )
            conn.commit()
        finally:
            conn.close()

    def clear_session(self, *, session_date: str, market: str, runtime_mode: str = "live") -> None:
        market_key = str(market or "").upper()
        mode = str(runtime_mode or "live").lower()
        conn = self.connect()
        try:
            keys = [
                str(row["candidate_key"])
                for row in conn.execute(
                    """
                    SELECT candidate_key FROM audit_candidate_rows
                    WHERE session_date=? AND market=? AND runtime_mode=?
                    """,
                    (session_date, market_key, mode),
                )
            ]
            if keys:
                conn.executemany(
                    "DELETE FROM audit_candidate_outcomes WHERE candidate_key=?",
                    [(key,) for key in keys],
                )
            conn.execute(
                """
                DELETE FROM audit_candidate_rows
                WHERE session_date=? AND market=? AND runtime_mode=?
                """,
                (session_date, market_key, mode),
            )
            conn.execute(
                """
                DELETE FROM audit_claude_calls
                WHERE session_date=? AND market=? AND runtime_mode=?
                """,
                (session_date, market_key, mode),
            )
            conn.commit()
        finally:
            conn.close()

    def upsert_call(self, call: dict[str, Any]) -> None:
        now = _utc_now()
        payload = call.get("payload") if isinstance(call.get("payload"), dict) else {}
        actual_raw = call.get("actual_prompt_count")
        if actual_raw in (None, "") and "actual_prompt_count" in payload:
            actual_raw = payload.get("actual_prompt_count")
        actual_prompt_count = int(actual_raw or 0)
        has_actual_prompt_count = actual_raw not in (None, "")
        prompt_candidate_count = (
            actual_prompt_count
            if has_actual_prompt_count
            else int(call.get("prompt_candidate_count") or 0)
        )
        conn = self.connect()
        try:
            conn.execute(
                """
                INSERT INTO audit_claude_calls (
                    call_id, runtime_mode, market, session_date, called_at, label,
                    model, prompt_version, source_file, input_tokens, output_tokens,
                    prompt_candidate_count, actual_prompt_count, watchlist_count, trade_ready_count,
                    candidate_action_count, payload_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(call_id) DO UPDATE SET
                    called_at=excluded.called_at,
                    label=excluded.label,
                    model=excluded.model,
                    prompt_version=excluded.prompt_version,
                    source_file=excluded.source_file,
                    input_tokens=excluded.input_tokens,
                    output_tokens=excluded.output_tokens,
                    prompt_candidate_count=excluded.prompt_candidate_count,
                    actual_prompt_count=excluded.actual_prompt_count,
                    watchlist_count=excluded.watchlist_count,
                    trade_ready_count=excluded.trade_ready_count,
                    candidate_action_count=excluded.candidate_action_count,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    call.get("call_id", ""),
                    str(call.get("runtime_mode") or "live").lower(),
                    str(call.get("market") or "").upper(),
                    call.get("session_date", ""),
                    call.get("called_at", ""),
                    call.get("label", ""),
                    call.get("model", ""),
                    str(call.get("prompt_version") or ""),
                    call.get("source_file", ""),
                    int(call.get("input_tokens") or 0),
                    int(call.get("output_tokens") or 0),
                    prompt_candidate_count,
                    actual_prompt_count,
                    int(call.get("watchlist_count") or 0),
                    int(call.get("trade_ready_count") or 0),
                    int(call.get("candidate_action_count") or 0),
                    _json(payload),
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def upsert_candidate(self, row: dict[str, Any]) -> None:
        now = _utc_now()
        market = str(row.get("market") or "").upper()
        runtime_mode = str(row.get("runtime_mode") or "live").lower()
        session_date = str(row.get("session_date") or "")
        call_id = str(row.get("call_id") or "")
        ticker = str(row.get("ticker") or "").strip().upper()
        key = row.get("candidate_key") or candidate_key(
            session_date=session_date,
            market=market,
            call_id=call_id,
            ticker=ticker,
        )
        conn = self.connect()
        try:
            with conn:
                existing = conn.execute(
                    """
                    SELECT source_file, payload_json
                    FROM audit_candidate_rows
                    WHERE candidate_key=?
                    """,
                    (key,),
                ).fetchone()
                existing_source_file = str(existing["source_file"] or "") if existing is not None else ""
                existing_payload_json = str(existing["payload_json"] or "") if existing is not None else ""
                incoming_source_file = str(row.get("source_file") or "")
                effective_source_file = _merge_source_file(existing_source_file, incoming_source_file)
                effective_payload = _merge_payload(
                    existing_source_file=existing_source_file,
                    incoming_source_file=incoming_source_file,
                    existing_payload_json=existing_payload_json,
                    row=row,
                )
                conn.execute(
                    """
                    INSERT INTO audit_candidate_rows (
                        candidate_key, call_id, runtime_mode, market, session_date,
                        known_at, ticker, source_file, prompt_rank, in_prompt,
                        screener_seen, input_to_claude_reported, name, price,
                        change_pct, volume_ratio, turnover, market_type,
                        liquidity_bucket, primary_bucket, secondary_buckets_json,
                        claude_action, claude_reason, claude_veto_reason,
                        claude_watchlist, claude_trade_ready, recommended_strategy,
                        risk_tags_json, max_position_pct, route_original_action,
                        route_final_action, route_route, route_reason, route_demoted_to,
                        route_runtime_gate_reason, route_overextended, route_cancel_pathb,
                        route_suspend_pathb, route_warnings_json, classification,
                        payload_json, created_at, updated_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?
                    )
                    ON CONFLICT(candidate_key) DO UPDATE SET
                        known_at=COALESCE(NULLIF(excluded.known_at, ''), audit_candidate_rows.known_at),
                        source_file=COALESCE(NULLIF(excluded.source_file, ''), audit_candidate_rows.source_file),
                        prompt_rank=COALESCE(excluded.prompt_rank, audit_candidate_rows.prompt_rank),
                        in_prompt=MAX(audit_candidate_rows.in_prompt, excluded.in_prompt),
                        screener_seen=MAX(audit_candidate_rows.screener_seen, excluded.screener_seen),
                        input_to_claude_reported=MAX(audit_candidate_rows.input_to_claude_reported, excluded.input_to_claude_reported),
                        name=COALESCE(NULLIF(excluded.name, ''), audit_candidate_rows.name),
                        price=COALESCE(excluded.price, audit_candidate_rows.price),
                        change_pct=COALESCE(excluded.change_pct, audit_candidate_rows.change_pct),
                        volume_ratio=COALESCE(excluded.volume_ratio, audit_candidate_rows.volume_ratio),
                        turnover=COALESCE(excluded.turnover, audit_candidate_rows.turnover),
                        market_type=COALESCE(NULLIF(excluded.market_type, ''), audit_candidate_rows.market_type),
                        liquidity_bucket=COALESCE(NULLIF(excluded.liquidity_bucket, ''), audit_candidate_rows.liquidity_bucket),
                        primary_bucket=COALESCE(NULLIF(excluded.primary_bucket, ''), audit_candidate_rows.primary_bucket),
                        secondary_buckets_json=CASE WHEN excluded.secondary_buckets_json!='[]' THEN excluded.secondary_buckets_json ELSE audit_candidate_rows.secondary_buckets_json END,
                        claude_action=COALESCE(NULLIF(excluded.claude_action, ''), audit_candidate_rows.claude_action),
                        claude_reason=COALESCE(NULLIF(excluded.claude_reason, ''), audit_candidate_rows.claude_reason),
                        claude_veto_reason=COALESCE(NULLIF(excluded.claude_veto_reason, ''), audit_candidate_rows.claude_veto_reason),
                        claude_watchlist=MAX(audit_candidate_rows.claude_watchlist, excluded.claude_watchlist),
                        claude_trade_ready=MAX(audit_candidate_rows.claude_trade_ready, excluded.claude_trade_ready),
                        recommended_strategy=COALESCE(NULLIF(excluded.recommended_strategy, ''), audit_candidate_rows.recommended_strategy),
                        risk_tags_json=CASE WHEN excluded.risk_tags_json!='[]' THEN excluded.risk_tags_json ELSE audit_candidate_rows.risk_tags_json END,
                        max_position_pct=COALESCE(excluded.max_position_pct, audit_candidate_rows.max_position_pct),
                        route_original_action=COALESCE(NULLIF(excluded.route_original_action, ''), audit_candidate_rows.route_original_action),
                        route_final_action=COALESCE(NULLIF(excluded.route_final_action, ''), audit_candidate_rows.route_final_action),
                        route_route=COALESCE(NULLIF(excluded.route_route, ''), audit_candidate_rows.route_route),
                        route_reason=COALESCE(NULLIF(excluded.route_reason, ''), audit_candidate_rows.route_reason),
                        route_demoted_to=COALESCE(NULLIF(excluded.route_demoted_to, ''), audit_candidate_rows.route_demoted_to),
                        route_runtime_gate_reason=COALESCE(NULLIF(excluded.route_runtime_gate_reason, ''), audit_candidate_rows.route_runtime_gate_reason),
                        route_overextended=MAX(audit_candidate_rows.route_overextended, excluded.route_overextended),
                        route_cancel_pathb=MAX(audit_candidate_rows.route_cancel_pathb, excluded.route_cancel_pathb),
                        route_suspend_pathb=MAX(audit_candidate_rows.route_suspend_pathb, excluded.route_suspend_pathb),
                        route_warnings_json=CASE WHEN excluded.route_warnings_json!='[]' THEN excluded.route_warnings_json ELSE audit_candidate_rows.route_warnings_json END,
                        classification=COALESCE(NULLIF(excluded.classification, ''), audit_candidate_rows.classification),
                        payload_json=excluded.payload_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        key,
                        call_id,
                        runtime_mode,
                        market,
                        session_date,
                        row.get("known_at", ""),
                        ticker,
                        effective_source_file,
                        row.get("prompt_rank"),
                        _int_bool(row.get("in_prompt")),
                        _int_bool(row.get("screener_seen")),
                        _int_bool(row.get("input_to_claude_reported")),
                        row.get("name", ""),
                        row.get("price"),
                        row.get("change_pct"),
                        row.get("volume_ratio"),
                        row.get("turnover"),
                        row.get("market_type", ""),
                        row.get("liquidity_bucket", ""),
                        row.get("primary_bucket", ""),
                        _json(row.get("secondary_buckets") or []),
                        row.get("claude_action", ""),
                        row.get("claude_reason", ""),
                        row.get("claude_veto_reason", ""),
                        _int_bool(row.get("claude_watchlist")),
                        _int_bool(row.get("claude_trade_ready")),
                        row.get("recommended_strategy", ""),
                        _json(row.get("risk_tags") or []),
                        row.get("max_position_pct"),
                        row.get("route_original_action", ""),
                        row.get("route_final_action", ""),
                        row.get("route_route", ""),
                        row.get("route_reason", ""),
                        row.get("route_demoted_to", ""),
                        row.get("route_runtime_gate_reason", ""),
                        _int_bool(row.get("route_overextended")),
                        _int_bool(row.get("route_cancel_pathb")),
                        _int_bool(row.get("route_suspend_pathb")),
                        _json(row.get("route_warnings") or []),
                        row.get("classification", ""),
                        _json(effective_payload),
                        now,
                        now,
                    ),
                )
                extra_updates = {}
                for column in EXTRA_CANDIDATE_COLUMNS:
                    value = _candidate_extra_value(column, row)
                    if value is not None:
                        extra_updates[column] = value
                if extra_updates:
                    extra_updates["updated_at"] = now
                    set_clause = ", ".join(f"{column}=?" for column in extra_updates)
                    conn.execute(
                        f"UPDATE audit_candidate_rows SET {set_clause} WHERE candidate_key=?",
                        [*extra_updates.values(), key],
                    )
        finally:
            conn.close()

    def candidate_row_uniqueness(
        self,
        *,
        session_date: str = "",
        market: str = "",
        runtime_mode: str = "live",
    ) -> dict[str, int]:
        mode = str(runtime_mode or "live").lower()
        market_key = str(market or "").upper()
        where = ["runtime_mode=?"]
        params: list[Any] = [mode]
        if session_date:
            where.append("session_date=?")
            params.append(session_date)
        if market_key:
            where.append("market=?")
            params.append(market_key)
        where_sql = " AND ".join(where)
        conn = self.connect()
        try:
            call_level_rows = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM audit_candidate_rows WHERE {where_sql}",
                    params,
                ).fetchone()[0]
            )
            latest_rows = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM audit_candidate_latest_rows WHERE {where_sql}",
                    params,
                ).fetchone()[0]
            )
            duplicate_groups = int(
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
        finally:
            conn.close()
        return {
            "call_level_rows": call_level_rows,
            "latest_session_ticker_rows": latest_rows,
            "duplicate_group_count": duplicate_groups,
        }

    def update_execution_by_ticker(
        self,
        *,
        session_date: str,
        market: str,
        runtime_mode: str,
        ticker: str,
        values: dict[str, Any],
        latest_only: bool = False,
    ) -> int:
        allowed = {
            "decision_count",
            "buy_signal_count",
            "no_signal_count",
            "watch_only_count",
            "filled_count",
            "first_signal_at",
            "first_fill_at",
            "entry_price",
            "exit_price",
            "pnl_pct",
            "exit_reason",
            "strategy_used",
            "lifecycle_event_count",
            "last_lifecycle_event",
            "close_reason",
            "path_run_count",
            "intraday_signal_count",
            "intraday_traded_count",
            "execution_link_source",
            "execution_decision_id",
            "execution_event_id",
            "no_submit_reason_code",
            "no_submit_reason_detail",
            "no_submit_signal_flags_json",
            "no_submit_block_meta_json",
            "entry_timing_snapshot_json",
            "post_open_features_json",
            "kr_confirmation_snapshot_json",
            "candidate_health_snapshot_json",
            "entry_delay_min",
            "entry_price_vs_first_seen_pct",
            "entry_price_vs_first_ready_pct",
            "position_mfe_pct",
            "position_mae_pct",
            "us_early_entry_window",
            "us_early_entry_elapsed_min",
            "us_early_entry_size_mult",
            "us_early_entry_confirmation_reason",
            "us_early_entry_gate_json",
        }
        updates = {k: v for k, v in values.items() if k in allowed and v is not None}
        for key, value in list(updates.items()):
            if key.endswith("_json") and not isinstance(value, str):
                updates[key] = _json(value)
        if not updates:
            return 0
        updates["updated_at"] = _utc_now()
        set_clause = ", ".join(f"{key}=?" for key in updates)
        params = list(updates.values()) + [
            session_date,
            str(market or "").upper(),
            str(runtime_mode or "live").lower(),
            str(ticker or "").strip().upper(),
        ]
        conn = self.connect()
        try:
            if latest_only:
                cur = conn.execute(
                    f"""
                    UPDATE audit_candidate_rows
                    SET {set_clause}
                    WHERE candidate_key=(
                        SELECT candidate_key
                        FROM audit_candidate_rows
                        WHERE session_date=? AND market=? AND runtime_mode=? AND ticker=?
                        ORDER BY
                            COALESCE(NULLIF(known_at, ''), updated_at, created_at) DESC,
                            updated_at DESC,
                            candidate_key DESC
                        LIMIT 1
                    )
                    """,
                    params,
                )
            else:
                cur = conn.execute(
                    f"""
                    UPDATE audit_candidate_rows
                    SET {set_clause}
                    WHERE session_date=? AND market=? AND runtime_mode=? AND ticker=?
                    """,
                    params,
                )
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            conn.close()

    def upsert_outcome(self, row: dict[str, Any]) -> None:
        now = _utc_now()
        payload = row.get("payload")
        if payload is None:
            payload = row.get("payload_json") or {}
        conn = self.connect()
        try:
            conn.execute(
                """
                INSERT INTO audit_candidate_outcomes (
                    candidate_key, horizon_min, target_at, observed_at,
                    observed_price, return_pct, max_runup_pct, max_drawdown_pct,
                    status, source, label_generated_at, payload_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_key, horizon_min) DO UPDATE SET
                    target_at=excluded.target_at,
                    observed_at=excluded.observed_at,
                    observed_price=excluded.observed_price,
                    return_pct=excluded.return_pct,
                    max_runup_pct=excluded.max_runup_pct,
                    max_drawdown_pct=excluded.max_drawdown_pct,
                    status=excluded.status,
                    source=excluded.source,
                    label_generated_at=excluded.label_generated_at,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    str(row.get("candidate_key") or ""),
                    int(row.get("horizon_min") or 0),
                    row.get("target_at", ""),
                    row.get("observed_at", ""),
                    row.get("observed_price"),
                    row.get("return_pct"),
                    row.get("max_runup_pct"),
                    row.get("max_drawdown_pct"),
                    str(row.get("status") or "unknown"),
                    str(row.get("source") or ""),
                    str(row.get("label_generated_at") or now),
                    _json(payload),
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def upsert_outcomes(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        now = _utc_now()
        params = []
        for row in rows:
            payload = row.get("payload")
            if payload is None:
                payload = row.get("payload_json") or {}
            params.append(
                (
                    str(row.get("candidate_key") or ""),
                    int(row.get("horizon_min") or 0),
                    row.get("target_at", ""),
                    row.get("observed_at", ""),
                    row.get("observed_price"),
                    row.get("return_pct"),
                    row.get("max_runup_pct"),
                    row.get("max_drawdown_pct"),
                    str(row.get("status") or "unknown"),
                    str(row.get("source") or ""),
                    str(row.get("label_generated_at") or now),
                    _json(payload),
                    now,
                )
            )
        conn = self.connect()
        try:
            conn.executemany(
                """
                INSERT INTO audit_candidate_outcomes (
                    candidate_key, horizon_min, target_at, observed_at,
                    observed_price, return_pct, max_runup_pct, max_drawdown_pct,
                    status, source, label_generated_at, payload_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_key, horizon_min) DO UPDATE SET
                    target_at=excluded.target_at,
                    observed_at=excluded.observed_at,
                    observed_price=excluded.observed_price,
                    return_pct=excluded.return_pct,
                    max_runup_pct=excluded.max_runup_pct,
                    max_drawdown_pct=excluded.max_drawdown_pct,
                    status=excluded.status,
                    source=excluded.source,
                    label_generated_at=excluded.label_generated_at,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                params,
            )
            conn.commit()
            return len(params)
        finally:
            conn.close()

    def refresh_classifications(
        self, *, session_date: str, market: str, runtime_mode: str = "live"
    ) -> int:
        conn = self.connect()
        try:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM audit_candidate_rows
                    WHERE session_date=? AND market=? AND runtime_mode=?
                    """,
                    (session_date, str(market or "").upper(), str(runtime_mode or "live").lower()),
                )
            ]
            changed = 0
            for row in rows:
                label = classify_candidate(row)
                if label != row.get("classification"):
                    conn.execute(
                        """
                        UPDATE audit_candidate_rows
                        SET classification=?, updated_at=?
                        WHERE candidate_key=?
                        """,
                        (label, _utc_now(), row["candidate_key"]),
                    )
                    changed += 1
            conn.commit()
            return changed
        finally:
            conn.close()

    def summary(self, *, session_date: str, market: str, runtime_mode: str = "live") -> dict[str, Any]:
        conn = self.connect()
        params = (session_date, str(market or "").upper(), str(runtime_mode or "live").lower())
        try:
            calls = conn.execute(
                """
                SELECT COUNT(*) AS call_count,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(prompt_candidate_count), 0) AS prompt_candidate_rows,
                       COALESCE(SUM(
                           CASE
                               WHEN actual_prompt_count IS NOT NULL AND actual_prompt_count > 0
                               THEN actual_prompt_count
                               ELSE prompt_candidate_count
                           END
                       ), 0) AS actual_prompt_rows,
                       COALESCE(SUM(watchlist_count), 0) AS watchlist_rows
                FROM audit_claude_calls
                WHERE session_date=? AND market=? AND runtime_mode=?
                """,
                params,
            ).fetchone()
            class_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT COALESCE(classification, 'unknown') AS classification,
                           COUNT(*) AS rows,
                           COALESCE(SUM(filled_count), 0) AS filled_rows,
                           ROUND(AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END), 4) AS avg_pnl_pct
                    FROM audit_candidate_rows
                    WHERE session_date=? AND market=? AND runtime_mode=?
                    GROUP BY COALESCE(classification, 'unknown')
                    ORDER BY rows DESC
                    """,
                    params,
                )
            ]
            route_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT COALESCE(claude_action, '') AS claude_action,
                           COALESCE(route_final_action, '') AS route_final_action,
                           COUNT(*) AS rows
                    FROM audit_candidate_rows
                    WHERE session_date=? AND market=? AND runtime_mode=?
                    GROUP BY COALESCE(claude_action, ''), COALESCE(route_final_action, '')
                    ORDER BY rows DESC
                    LIMIT 30
                    """,
                    params,
                )
            ]
            return {
                "db_path": str(self.path),
                "session_date": session_date,
                "market": str(market or "").upper(),
                "runtime_mode": str(runtime_mode or "live").lower(),
                "calls": dict(calls or {}),
                "classifications": class_rows,
                "route_matrix": route_rows,
            }
        finally:
            conn.close()

    def rows(
        self,
        *,
        session_date: str,
        market: str,
        runtime_mode: str = "live",
        classification: str = "",
        ticker: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where = ["session_date=?", "market=?", "runtime_mode=?"]
        params: list[Any] = [session_date, str(market or "").upper(), str(runtime_mode or "live").lower()]
        if classification:
            where.append("classification=?")
            params.append(classification)
        if ticker:
            where.append("ticker=?")
            params.append(str(ticker).strip().upper())
        params.append(max(min(int(limit or 200), 1000), 1))
        conn = self.connect()
        try:
            return [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT candidate_key, call_id, known_at, ticker, prompt_rank,
                           in_prompt, screener_seen, input_to_claude_reported,
                           final_prompt_included, actual_prompt_included,
                           actual_prompt_rank, selection_trace_id,
                           price, change_pct, volume_ratio,
                           primary_bucket, claude_action, claude_reason,
                           route_final_action, route_reason, route_runtime_gate_reason,
                           buy_signal_count, filled_count, pnl_pct, exit_reason,
                           close_reason, classification
                    FROM audit_candidate_rows
                    WHERE {' AND '.join(where)}
                    ORDER BY known_at DESC, prompt_rank ASC, ticker ASC
                    LIMIT ?
                    """,
                    params,
                )
            ]
        finally:
            conn.close()


def classify_candidate(row: dict[str, Any]) -> str:
    filled = int(row.get("filled_count") or 0)
    if filled > 0:
        pnl = row.get("pnl_pct")
        if pnl is None:
            return "filled_unknown"
        try:
            return "filled_win" if float(pnl) > 0 else "filled_loss"
        except Exception:
            return "filled_unknown"

    if int(row.get("buy_signal_count") or 0) > 0:
        return "buy_signal_unfilled"

    claude_action = str(row.get("claude_action") or "").upper()
    route_final = str(row.get("route_final_action") or "").upper()
    route_reason = str(row.get("route_reason") or row.get("route_runtime_gate_reason") or "")
    ready_actions = {"BUY_READY", "PROBE_READY", "ADD_READY", "PULLBACK_WAIT"}
    blocked = route_final in {"HARD_BLOCK", "BLOCK", "REJECT"} or "block" in route_reason.lower()
    if claude_action in ready_actions and blocked:
        return "ready_route_blocked"
    if claude_action in ready_actions:
        return "ready_no_signal"
    if claude_action == "AVOID":
        return "avoid_watch"
    if int(row.get("claude_trade_ready") or 0) > 0 and blocked:
        return "ready_route_blocked"
    if int(row.get("claude_trade_ready") or 0) > 0:
        return "ready_no_signal"
    if int(row.get("claude_watchlist") or 0) > 0:
        return "watch_only"
    if int(row.get("in_prompt") or 0) > 0:
        return "in_prompt_not_selected"
    if int(row.get("screener_seen") or 0) > 0:
        return "not_in_prompt"
    return "unknown"
