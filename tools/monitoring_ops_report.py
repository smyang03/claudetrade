from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path
from tools.analyze_candidate_audit import analyze_candidate_audit
from tools.analyze_hold_advisor_latency import analyze_hold_advisor_latency

KST = timezone(timedelta(hours=9))


def _read_json(path: Path, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return default


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _candidate_bucket_source_score_coverage(
    *,
    db_path: Path,
    session_date: str,
    market: str,
    runtime_mode: str,
) -> dict[str, Any]:
    if not db_path.exists():
        return {"available": False, "reason": "db_missing", "db_path": str(db_path)}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "audit_candidate_rows"):
            return {"available": False, "reason": "table_missing", "db_path": str(db_path)}
        columns = _columns(conn, "audit_candidate_rows")
        where = ["runtime_mode=?"]
        params: list[Any] = [runtime_mode]
        if session_date:
            where.append("session_date=?")
            params.append(session_date)
        if market:
            where.append("market=?")
            params.append(market.upper())
        pieces = ["COUNT(*) AS rows"]
        checks = {
            "blank_primary_bucket": "primary_bucket",
            "empty_source_tags": "source_tags_json",
            "null_raw_score_current": "raw_score_current",
            "null_trainer_score_rank": "trainer_score_rank",
            "empty_data_quality_flags": "data_quality_flags_json",
        }
        for label, column in checks.items():
            if column in columns:
                pieces.append(
                    f"SUM(CASE WHEN {column} IS NULL OR {column}='' OR {column}='[]' THEN 1 ELSE 0 END) AS {label}"
                )
            else:
                pieces.append(f"0 AS {label}")
        row = conn.execute(
            f"SELECT {', '.join(pieces)} FROM audit_candidate_rows WHERE {' AND '.join(where)}",
            params,
        ).fetchone()
        invalid_price_reason_counts: dict[str, int] = {}
        if "invalid_price_reason" in columns:
            invalid_price_reason_counts = {
                str(item["reason"] or "unknown"): int(item["rows"] or 0)
                for item in conn.execute(
                    f"""
                    SELECT COALESCE(NULLIF(invalid_price_reason,''), 'unknown') AS reason, COUNT(*) AS rows
                    FROM audit_candidate_rows
                    WHERE {' AND '.join(where)}
                      AND invalid_price_reason IS NOT NULL
                      AND invalid_price_reason<>''
                    GROUP BY COALESCE(NULLIF(invalid_price_reason,''), 'unknown')
                    ORDER BY rows DESC
                    """,
                    params,
                )
            }
        return {
            "available": True,
            "db_path": str(db_path),
            "rows": int(row["rows"] or 0) if row else 0,
            "blank_primary_bucket": int(row["blank_primary_bucket"] or 0) if row else 0,
            "empty_source_tags": int(row["empty_source_tags"] or 0) if row else 0,
            "null_raw_score_current": int(row["null_raw_score_current"] or 0) if row else 0,
            "null_trainer_score_rank": int(row["null_trainer_score_rank"] or 0) if row else 0,
            "empty_data_quality_flags": int(row["empty_data_quality_flags"] or 0) if row else 0,
            "invalid_price_reason_counts": invalid_price_reason_counts,
            "performance_conclusion_allowed": False,
        }
    finally:
        conn.close()


def _kis_token_status(mode: str) -> dict[str, Any]:
    try:
        from tools.live_preflight import _token_checks

        checks = _token_checks(mode)
        rows = []
        for item in checks:
            payload = asdict(item) if is_dataclass(item) else dict(item)
            details = dict(payload.get("data") or payload.get("details") or {})
            details.pop("access_token", None)
            details.pop("app_key", None)
            details.pop("app_secret", None)
            details.pop("account_no", None)
            payload["data"] = details
            rows.append(payload)
        return {"available": True, "checks": rows}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def _v2_learning_gate_report(db_path: Path, *, market: str, runtime_mode: str) -> dict[str, Any]:
    if not db_path.exists():
        return {"available": False, "reason": "db_missing", "db_path": str(db_path)}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "v2_canonical_performance"):
            return {"available": False, "reason": "table_missing", "db_path": str(db_path)}
        columns = _columns(conn, "v2_canonical_performance")
        where = ["runtime_mode=?"]
        params: list[Any] = [runtime_mode]
        if market:
            where.append("market=?")
            params.append(market.upper())
        grade_col = "quality_grade" if "quality_grade" in columns else "''"
        rows_by_grade = {
            str(row["quality_grade"] or "unknown"): int(row["rows"] or 0)
            for row in conn.execute(
                f"""
                SELECT COALESCE(NULLIF({grade_col},''), 'unknown') AS quality_grade, COUNT(*) AS rows
                FROM v2_canonical_performance
                WHERE {' AND '.join(where)}
                GROUP BY COALESCE(NULLIF({grade_col},''), 'unknown')
                ORDER BY rows DESC
                """,
                params,
            )
        }
        learning_allowed = 0
        learning_excluded = 0
        if "learning_allowed" in columns:
            row = conn.execute(
                f"""
                SELECT
                  SUM(CASE WHEN COALESCE(learning_allowed, 0)=1 THEN 1 ELSE 0 END) AS allowed,
                  SUM(CASE WHEN COALESCE(learning_allowed, 0)=0 THEN 1 ELSE 0 END) AS excluded
                FROM v2_canonical_performance
                WHERE {' AND '.join(where)}
                """,
                params,
            ).fetchone()
            learning_allowed = int(row["allowed"] or 0)
            learning_excluded = int(row["excluded"] or 0)
        reason_counts: Counter[str] = Counter()
        reason_table = ""
        reason_columns: set[str] = set()
        if _table_exists(conn, "v2_learning_performance"):
            learning_columns = _columns(conn, "v2_learning_performance")
            if "quality_reasons_json" in learning_columns:
                reason_table = "v2_learning_performance"
                reason_columns = learning_columns
        if not reason_table and "quality_reasons_json" in columns:
            reason_table = "v2_canonical_performance"
            reason_columns = columns
        if reason_table:
            reason_where = ["runtime_mode=?"]
            reason_params: list[Any] = [runtime_mode]
            if market and "market" in reason_columns:
                reason_where.append("market=?")
                reason_params.append(market.upper())
            for row in conn.execute(
                f"SELECT quality_reasons_json FROM {reason_table} WHERE {' AND '.join(reason_where)}",
                reason_params,
            ):
                try:
                    reasons = json.loads(str(row["quality_reasons_json"] or "[]"))
                except Exception:
                    reasons = []
                if isinstance(reasons, list):
                    reason_counts.update(str(item) for item in reasons if str(item).strip())
        synced_at = ""
        if "synced_at" in columns:
            row = conn.execute(
                f"SELECT MAX(synced_at) AS synced_at FROM v2_canonical_performance WHERE {' AND '.join(where)}",
                params,
            ).fetchone()
            synced_at = str(row["synced_at"] or "") if row else ""
        return {
            "available": True,
            "db_path": str(db_path),
            "rows_by_quality_grade": rows_by_grade,
            "learning_allowed": learning_allowed,
            "learning_excluded": learning_excluded,
            "top_quality_reasons": dict(reason_counts.most_common(20)),
            "focus_exclusion_reasons": {
                key: int(reason_counts.get(key, 0))
                for key in (
                    "FORWARD_NOT_MEASURED",
                    "ORDER_UNKNOWN_UNRESOLVED",
                    "FORWARD_PENDING_DATA",
                    "DIRTY_BROKER_TRUTH",
                )
            },
            "last_synced_at": synced_at,
            "policy_change_allowed": False,
        }
    finally:
        conn.close()


def _blank_expr(column: str) -> str:
    return f"SUM(CASE WHEN {column} IS NULL OR TRIM(CAST({column} AS TEXT))='' THEN 1 ELSE 0 END)"


def _candidate_metadata_coverage_report(
    *,
    db_path: Path,
    session_date: str,
    market: str,
    runtime_mode: str,
) -> dict[str, Any]:
    if not db_path.exists():
        return {"available": False, "reason": "db_missing", "db_path": str(db_path)}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "audit_candidate_rows"):
            return {"available": False, "reason": "table_missing", "db_path": str(db_path)}
        columns = _columns(conn, "audit_candidate_rows")
        where = ["runtime_mode=?"]
        params: list[Any] = [runtime_mode]
        if session_date:
            where.append("session_date=?")
            params.append(session_date)
        if market:
            where.append("market=?")
            params.append(market.upper())
        watched_columns = [
            "candidate_pool_role",
            "discovery_signal_family",
            "discovery_reason",
            "discovery_action_ceiling",
            "freshness_verdict",
            "trainer_tier",
            "lifecycle_state",
            "evidence_data_state",
        ]
        pieces = ["COUNT(*) AS rows"]
        for column in watched_columns:
            if column in columns:
                pieces.append(f"{_blank_expr(column)} AS blank_{column}")
        row = conn.execute(
            f"SELECT {', '.join(pieces)} FROM audit_candidate_rows WHERE {' AND '.join(where)}",
            params,
        ).fetchone()
        total = int(row["rows"] or 0) if row else 0
        blank_counts: dict[str, int] = {}
        blank_rates: dict[str, float | None] = {}
        for column in watched_columns:
            key = f"blank_{column}"
            if row is not None and key in row.keys():
                count = int(row[key] or 0)
                blank_counts[column] = count
                blank_rates[column] = round(count / total, 4) if total else None
        discovery_where = list(where)
        discovery_params = list(params)
        discovery_predicates = []
        for column in ("candidate_pool_role", "discovery_signal_family", "discovery_reason", "discovery_action_ceiling"):
            if column in columns:
                discovery_predicates.append(f"({column} IS NOT NULL AND TRIM(CAST({column} AS TEXT))<>'')")
        discovery_rows = 0
        expansion_role_rows = 0
        if discovery_predicates:
            row = conn.execute(
                f"""
                SELECT
                  COUNT(*) AS rows,
                  SUM(CASE WHEN UPPER(TRIM(COALESCE(candidate_pool_role,'')))='EXPANSION' THEN 1 ELSE 0 END) AS expansion_rows
                FROM audit_candidate_rows
                WHERE {' AND '.join(discovery_where)}
                  AND ({' OR '.join(discovery_predicates)})
                """,
                discovery_params,
            ).fetchone()
            discovery_rows = int(row["rows"] or 0) if row else 0
            expansion_role_rows = int(row["expansion_rows"] or 0) if row else 0
        discovery_prompt_metrics = {
            "available": False,
            "reason": "audit_claude_calls_missing",
            "calls": 0,
            "enabled_calls": 0,
            "eligible_total": 0,
            "added_total": 0,
            "prompt_pool_discovery_total": 0,
            "reject_counts": {},
        }
        if _table_exists(conn, "audit_claude_calls"):
            call_where = ["runtime_mode=?"]
            call_params: list[Any] = [runtime_mode]
            if session_date:
                call_where.append("session_date=?")
                call_params.append(session_date)
            if market:
                call_where.append("market=?")
                call_params.append(market.upper())
            rows = conn.execute(
                f"""
                SELECT label, payload_json
                FROM audit_claude_calls
                WHERE {' AND '.join(call_where)}
                """,
                call_params,
            ).fetchall()
            reject_counter: Counter[str] = Counter()
            calls = 0
            enabled_calls = 0
            eligible_total = 0
            added_total = 0
            prompt_pool_discovery_total = 0
            for call_row in rows:
                payload = _decode_json_dict(call_row["payload_json"])
                if "discovery_enabled" not in payload:
                    continue
                calls += 1
                if bool(payload.get("discovery_enabled")):
                    enabled_calls += 1
                try:
                    eligible_total += int(payload.get("discovery_eligible_count") or 0)
                except Exception:
                    pass
                try:
                    added_total += int(payload.get("discovery_added") or 0)
                except Exception:
                    pass
                try:
                    prompt_pool_discovery_total += int(payload.get("prompt_pool_discovery_count") or 0)
                except Exception:
                    pass
                reject_counts = payload.get("discovery_reject_counts")
                if isinstance(reject_counts, dict):
                    for reason, count in reject_counts.items():
                        try:
                            reject_counter[str(reason)] += int(count or 0)
                        except Exception:
                            pass
            discovery_prompt_metrics = {
                "available": True,
                "calls": calls,
                "enabled_calls": enabled_calls,
                "eligible_total": eligible_total,
                "added_total": added_total,
                "prompt_pool_discovery_total": prompt_pool_discovery_total,
                "reject_counts": dict(reject_counter.most_common()),
                "eligible_added_zero": bool(enabled_calls > 0 and eligible_total == 0),
                "audit_write_blank_suspected": bool(added_total > 0 and discovery_rows == 0),
            }
        pullback_gate_count = 0
        pullback_live_count = 0
        pullback_shadow_count = 0
        pullback_gate_tickers: list[str] = []
        pullback_gate_reasons: Counter[str] = Counter()
        pullback_legacy_shadow_count = 0
        for candidate_row in conn.execute(
            f"""
            SELECT ticker, payload_json
            FROM audit_candidate_rows
            WHERE {' AND '.join(where)}
            """,
            params,
        ).fetchall():
            payload = _decode_json_dict(candidate_row["payload_json"])
            runtime_gate = payload.get("runtime_gate")
            if not isinstance(runtime_gate, dict):
                continue
            gate = runtime_gate.get("pullback_wait_evidence_gate")
            if not isinstance(gate, dict):
                legacy_shadow = runtime_gate.get("pullback_wait_evidence_shadow")
                if isinstance(legacy_shadow, dict) and bool(legacy_shadow.get("would_demote_to_watch")):
                    pullback_legacy_shadow_count += 1
                continue
            pullback_gate_count += 1
            if bool(gate.get("demoted_to_watch")):
                pullback_live_count += 1
            else:
                pullback_shadow_count += 1
            ticker = str(candidate_row["ticker"] or "").strip().upper()
            if ticker and ticker not in pullback_gate_tickers:
                pullback_gate_tickers.append(ticker)
            for reason in list(gate.get("reasons") or []):
                pullback_gate_reasons[str(reason)] += 1
        return {
            "available": True,
            "db_path": str(db_path),
            "rows": total,
            "blank_counts": blank_counts,
            "blank_rates": blank_rates,
            "discovery_metadata_rows": discovery_rows,
            "discovery_prompt_metrics": discovery_prompt_metrics,
            "pullback_wait_evidence_gate": {
                "count": pullback_gate_count,
                "live_demotion_count": pullback_live_count,
                "shadow_count": pullback_shadow_count,
                "tickers": pullback_gate_tickers[:50],
                "reason_counts": dict(pullback_gate_reasons.most_common()),
                "legacy_shadow_count": pullback_legacy_shadow_count,
                "trade_behavior_change_allowed": bool(pullback_live_count > 0),
            },
            "expansion_role_rows": expansion_role_rows,
            "role_contract": "candidate_pool_role must remain DISCOVERY for discovery/expansion audit rows",
            "trade_behavior_change_allowed": False,
        }
    finally:
        conn.close()


def _selection_source_bucket(source: str) -> str:
    text = str(source or "").strip().lower()
    if not text:
        return "unknown"
    if "sub_screener" in text:
        return "sub_screener"
    if "manual" in text or "telegram" in text:
        return "manual"
    if "reinvoke" in text:
        return "analyst_reinvoke"
    if "session_open" in text or "preopen" in text or text == "initial":
        return "scheduled"
    if "session_reuse" in text or "resume" in text or "startup" in text:
        return "resume"
    return "other"


def _selection_call_breakdown_report(
    *,
    db_path: Path,
    session_date: str,
    market: str,
    runtime_mode: str,
) -> dict[str, Any]:
    state_payload: dict[str, Any] = {}
    try:
        from runtime import selection_smart_skip

        if market and session_date:
            state_payload = selection_smart_skip.load_state(market, session_date)
    except Exception:
        state_payload = {}
    if not db_path.exists():
        return {
            "available": False,
            "reason": "db_missing",
            "db_path": str(db_path),
            "smart_skip_state": state_payload,
        }
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "audit_claude_calls"):
            return {
                "available": False,
                "reason": "audit_claude_calls_missing",
                "db_path": str(db_path),
                "smart_skip_state": state_payload,
            }
        where = ["runtime_mode=?"]
        params: list[Any] = [runtime_mode]
        if session_date:
            where.append("session_date=?")
            params.append(session_date)
        if market:
            where.append("market=?")
            params.append(market.upper())
        rows = conn.execute(
            f"""
            SELECT label, input_tokens, output_tokens, payload_json
            FROM audit_claude_calls
            WHERE {' AND '.join(where)}
            """,
            params,
        ).fetchall()
        application_count = 0
        smart_reuse_count = 0
        sub_triage_count = 0
        by_source: Counter[str] = Counter()
        by_bucket: Counter[str] = Counter()
        by_label: Counter[str] = Counter()
        token_input = 0
        token_output = 0
        for row in rows:
            label = str(row["label"] or "unknown")
            by_label[label] += 1
            token_input += int(row["input_tokens"] or 0)
            token_output += int(row["output_tokens"] or 0)
            payload = _decode_json_dict(row["payload_json"])
            if label != "selection_meta_live":
                continue
            application_count += 1
            source = str(payload.get("selection_source_type") or "unknown")
            by_source[source] += 1
            by_bucket[_selection_source_bucket(source)] += 1
            if bool(payload.get("smart_skip_reused")):
                smart_reuse_count += 1
            triage = payload.get("sub_screener_triage")
            if isinstance(triage, dict) and bool(triage.get("enabled")):
                sub_triage_count += 1
        full_call_estimate = max(0, application_count - smart_reuse_count - sub_triage_count)
        return {
            "available": True,
            "db_path": str(db_path),
            "selection_application_count": application_count,
            "full_select_tickers_estimate": full_call_estimate,
            "smart_skip_reuse_count": smart_reuse_count,
            "sub_screener_triage_count": sub_triage_count,
            "by_source": dict(by_source.most_common()),
            "by_bucket": dict(by_bucket.most_common()),
            "by_label": dict(by_label.most_common()),
            "audit_token_sums": {"input": token_input, "output": token_output},
            "smart_skip_state": {
                "full_call_count": int(state_payload.get("full_call_count") or 0) if state_payload else 0,
                "reuse_count": int(state_payload.get("reuse_count") or 0) if state_payload else 0,
                "observe_hit_count": int(state_payload.get("observe_hit_count") or 0) if state_payload else 0,
                "mode": str(state_payload.get("mode") or "") if state_payload else "",
                "fail_open_count": int(state_payload.get("fail_open_count") or 0) if state_payload else 0,
                "fail_open_reasons": dict(state_payload.get("fail_open_reasons") or {}) if state_payload else {},
                "last_fail_open": dict(state_payload.get("last_fail_open") or {}) if state_payload else {},
                "last_observe_hit": dict(state_payload.get("last_observe_hit") or {}) if state_payload else {},
            },
            "policy_change_allowed": False,
        }
    finally:
        conn.close()


def _decode_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _resolved_candidate_reason(row: sqlite3.Row) -> tuple[str, str]:
    payload = _decode_json_dict(row["payload_json"] if "payload_json" in row.keys() else "")
    runtime_gate = _decode_json_dict(payload.get("runtime_gate"))
    ordered = [
        ("no_submit_reason_code", row["no_submit_reason_code"] if "no_submit_reason_code" in row.keys() else ""),
        ("route_runtime_gate_reason", row["route_runtime_gate_reason"] if "route_runtime_gate_reason" in row.keys() else ""),
        ("route_reason", row["route_reason"] if "route_reason" in row.keys() else ""),
        ("payload.runtime_gate.reason", runtime_gate.get("reason")),
        ("classification", row["classification"] if "classification" in row.keys() else ""),
    ]
    for source, value in ordered:
        text = str(value or "").strip()
        if text:
            return text, source
    return "unknown", "fallback"


def _candidate_resolved_reason_report(
    *,
    db_path: Path,
    session_date: str,
    market: str,
    runtime_mode: str,
    limit: int = 20,
) -> dict[str, Any]:
    if not db_path.exists():
        return {"available": False, "reason": "db_missing", "db_path": str(db_path)}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "audit_candidate_rows"):
            return {"available": False, "reason": "table_missing", "db_path": str(db_path)}
        columns = _columns(conn, "audit_candidate_rows")
        selected = [
            "ticker",
            "classification",
            "route_reason",
            "route_runtime_gate_reason",
            "payload_json",
        ]
        if "no_submit_reason_code" in columns:
            selected.append("no_submit_reason_code")
        where = ["runtime_mode=?"]
        params: list[Any] = [runtime_mode]
        if session_date:
            where.append("session_date=?")
            params.append(session_date)
        if market:
            where.append("market=?")
            params.append(market.upper())
        rows = list(
            conn.execute(
                f"SELECT {', '.join(selected)} FROM audit_candidate_rows WHERE {' AND '.join(where)}",
                params,
            )
        )
        reason_counts: Counter[str] = Counter()
        source_counts: Counter[str] = Counter()
        examples: list[dict[str, Any]] = []
        for row in rows:
            reason, source = _resolved_candidate_reason(row)
            reason_counts[reason] += 1
            source_counts[source] += 1
            if len(examples) < max(int(limit or 1), 1):
                examples.append(
                    {
                        "ticker": row["ticker"],
                        "resolved_reason": reason,
                        "source": source,
                        "classification": row["classification"] if "classification" in row.keys() else "",
                    }
                )
        return {
            "available": True,
            "db_path": str(db_path),
            "rows": len(rows),
            "reason_counts": dict(reason_counts.most_common(limit)),
            "source_counts": dict(source_counts.most_common()),
            "examples": examples,
            "trade_behavior_change_allowed": False,
        }
    finally:
        conn.close()


def _pathb_missed_opportunity_report(
    *,
    db_path: Path,
    session_date: str,
    market: str,
    runtime_mode: str,
    limit: int = 10,
) -> dict[str, Any]:
    if not db_path.exists():
        return {"available": False, "reason": "db_missing", "db_path": str(db_path)}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "pathb_miss_quality"):
            return {"available": False, "reason": "table_missing", "db_path": str(db_path)}
        where = ["runtime_mode=?"]
        params: list[Any] = [runtime_mode]
        if session_date:
            where.append("session_date=?")
            params.append(session_date)
        if market:
            where.append("market=?")
            params.append(market.upper())
        where_sql = " AND ".join(where)
        reason_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT
                  COALESCE(NULLIF(cancel_reason,''), 'unknown') AS cancel_reason,
                  COUNT(*) AS rows,
                  SUM(CASE WHEN COALESCE(zone_reentered_after_cancel,0)=1 THEN 1 ELSE 0 END) AS zone_reentered,
                  SUM(CASE WHEN COALESCE(mfe_30m_pct,0)>0 THEN 1 ELSE 0 END) AS positive_mfe_rows,
                  AVG(mfe_30m_pct) AS avg_mfe_30m_pct,
                  AVG(mae_30m_pct) AS avg_mae_30m_pct
                FROM pathb_miss_quality
                WHERE {where_sql}
                GROUP BY COALESCE(NULLIF(cancel_reason,''), 'unknown')
                ORDER BY rows DESC
                """,
                params,
            )
        ]
        examples = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT market, session_date, ticker, cancel_reason,
                       zone_reentered_after_cancel, mfe_30m_pct, mae_30m_pct,
                       followup_status, quote_sample_count
                FROM pathb_miss_quality
                WHERE {where_sql}
                ORDER BY COALESCE(mfe_30m_pct, -999999.0) DESC
                LIMIT ?
                """,
                [*params, max(int(limit or 1), 1)],
            )
        ]
        return {
            "available": True,
            "db_path": str(db_path),
            "rows": sum(int(row.get("rows") or 0) for row in reason_rows),
            "by_cancel_reason": reason_rows,
            "top_positive_mfe_examples": examples,
            "trade_behavior_change_allowed": False,
            "reason_split_change_allowed": False,
        }
    finally:
        conn.close()


def _kr_live_expansion_guard_report(mode: str) -> dict[str, Any]:
    try:
        from tools.live_preflight import load_effective_config

        config = load_effective_config(mode)
        effective = dict(config.get("effective") or {})
    except Exception as exc:
        return {"available": False, "reason": "config_unavailable", "error": str(exc)}

    def truthy(value: Any) -> bool:
        return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}

    strategy_flags = {
        "KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED": truthy(effective.get("KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED")),
        "KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED": truthy(effective.get("KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED")),
        "KR_PLAN_A_ORP_SIGNAL_ENABLED": truthy(effective.get("KR_PLAN_A_ORP_SIGNAL_ENABLED")),
    }
    enabled_strategy_flags = [key for key, value in strategy_flags.items() if value]
    return {
        "available": True,
        "status": "warn" if enabled_strategy_flags else "pass",
        "strategy_live_flags": strategy_flags,
        "enabled_strategy_flags": enabled_strategy_flags,
        "pathb_kr_live_enabled": truthy(effective.get("PATHB_KR_LIVE_ENABLED")),
        "kr_claude_price_new_entry_block": truthy(effective.get("KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK")),
        "minimum_shadow_or_probe": {"fills": 30, "calendar_weeks": 4},
        "live_expansion_allowed": False,
        "config_change_allowed": False,
    }


def _pead_manual_review_report(
    *,
    state_path: Path,
    log_dir: Path,
    required_trading_days: int = 5,
) -> dict[str, Any]:
    state = _read_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    rows: list[dict[str, Any]] = []
    if log_dir.exists():
        for path in sorted([*log_dir.glob("*_shadow.json"), *log_dir.glob("*_shadow.jsonl")]):
            try:
                if path.suffix == ".jsonl":
                    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                        if line.strip():
                            item = json.loads(line)
                            if isinstance(item, dict):
                                rows.append(item)
                else:
                    item = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(item, list):
                        rows.extend(row for row in item if isinstance(row, dict))
                    elif isinstance(item, dict):
                        rows.append(item)
            except Exception:
                continue
    trading_days_observed = int(state.get("trading_days_observed") or state.get("observed_trading_days") or 0)
    prompt_enabled = bool(state.get("prompt_surprise_enabled") or state.get("pead_prompt_surprise_enabled"))
    checklist = state.get("manual_review_checklist") if isinstance(state.get("manual_review_checklist"), dict) else {}
    prompt_leaks = [
        {
            "ticker": row.get("ticker"),
            "market": row.get("market"),
            "session_date": row.get("session_date") or row.get("date"),
        }
        for row in rows
        if bool(row.get("prompt_applied")) and not prompt_enabled
    ][:20]
    surprise_known = sum(1 for row in rows if str(row.get("surprise_sign") or "unknown") != "unknown")
    prompt_applied = sum(1 for row in rows if bool(row.get("prompt_applied")))
    checklist_complete = bool(checklist) and all(bool(value) for value in checklist.values())
    return {
        "state_path": str(state_path),
        "log_dir": str(log_dir),
        "state_found": state_path.exists(),
        "shadow_rows": len(rows),
        "trading_days_observed": trading_days_observed,
        "required_trading_days": required_trading_days,
        "prompt_surprise_enabled": prompt_enabled,
        "manual_review_checklist": checklist,
        "surprise_known_count": surprise_known,
        "prompt_applied_count": prompt_applied,
        "prompt_leak_candidates": prompt_leaks,
        "promotion_gate_state": "pass"
        if prompt_enabled and trading_days_observed >= required_trading_days and not prompt_leaks and checklist_complete
        else "blocked_manual_review",
        "policy_change_allowed": False,
    }


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        try:
            parsed = datetime.fromisoformat(raw[:19])
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _hold_advisor_cache_shadow(
    *,
    decision_dir: Path,
    start_date: str,
    end_date: str,
    market: str,
    ttl_minutes: int = 10,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if decision_dir.exists():
        for path in sorted(decision_dir.glob("decisions_*.jsonl")):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if not isinstance(item, dict):
                    continue
                ts = _parse_dt(item.get("ts") or item.get("timestamp"))
                day = ts.date().isoformat() if ts else str(item.get("date") or "")[:10]
                if start_date and day and day < start_date:
                    continue
                if end_date and day and day > end_date:
                    continue
                market_key = str(market or "").upper()
                if market_key and market_key != "ALL" and str(item.get("market") or "").upper() != market_key:
                    continue
                rows.append({**item, "_parsed_ts": ts})
    rows.sort(key=lambda row: row.get("_parsed_ts") or datetime.min.replace(tzinfo=KST))
    ttl = timedelta(minutes=max(int(ttl_minutes or 0), 0))
    last_by_key: dict[tuple[str, str, str, str], datetime] = {}
    would_hit = 0
    would_expire = 0
    missing_time = 0
    for row in rows:
        ts = row.get("_parsed_ts")
        if not isinstance(ts, datetime):
            missing_time += 1
            continue
        key = (
            str(row.get("market") or "").upper(),
            str(row.get("ticker") or "").upper(),
            str(row.get("decision_stage") or "unknown"),
            str(row.get("review_reason") or row.get("reason") or "unknown"),
        )
        previous = last_by_key.get(key)
        if previous is not None and ts - previous <= ttl:
            would_hit += 1
        else:
            if previous is not None:
                would_expire += 1
            last_by_key[key] = ts
    return {
        "decision_dir": str(decision_dir),
        "ttl_minutes": max(int(ttl_minutes or 0), 0),
        "requests": len(rows),
        "would_hit": would_hit,
        "would_expire": would_expire,
        "missing_time": missing_time,
        "estimated_request_reduction": would_hit,
        "cache_enable_allowed": False,
        "policy_change_allowed": False,
    }


def _write_report(payload: dict[str, Any], report_dir: str | Path | None) -> dict[str, str]:
    out_dir = Path(report_dir) if report_dir else get_runtime_path("data", "v2_reports", "monitoring_ops")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"monitoring_ops_report_{stamp}.json"
    md_path = out_dir / f"monitoring_ops_report_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# Monitoring Ops Report",
        "",
        f"- generated_at: {payload.get('generated_at', '')}",
        f"- mode: {payload.get('mode', '')}",
        f"- market: {payload.get('market', '') or '*'}",
        f"- session_date: {payload.get('session_date', '') or '*'}",
        "",
        "## Gates",
        "",
    ]
    for name, row in (payload.get("gate_summary") or {}).items():
        lines.append(f"- {name}: {row}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def build_monitoring_ops_report(
    *,
    candidate_db: str | Path | None = None,
    learning_db: str | Path | None = None,
    event_db: str | Path | None = None,
    mode: str = "live",
    session_date: str = "",
    market: str = "",
    horizon_min: int = 60,
    pead_state: str | Path | None = None,
    pead_log_dir: str | Path | None = None,
    hold_decision_dir: str | Path | None = None,
    hold_start_date: str = "",
    hold_end_date: str = "",
    hold_cache_ttl_minutes: int = 10,
    report_dir: str | Path | None = None,
    write_report: bool = False,
) -> dict[str, Any]:
    runtime_mode = str(mode or "live").lower()
    market_key = str(market or "").upper()
    candidate_path = Path(candidate_db) if candidate_db else get_runtime_path("data", "audit", "candidate_audit.db")
    learning_path = Path(learning_db) if learning_db else get_runtime_path("data", "ml", "decisions.db")
    event_path = Path(event_db) if event_db else get_runtime_path("data", "v2_event_store.db")
    candidate_analysis: dict[str, Any]
    if candidate_path.exists():
        try:
            candidate_analysis = analyze_candidate_audit(
                db_path=candidate_path,
                session_date=session_date,
                market=market_key,
                runtime_mode=runtime_mode,
                horizon_min=int(horizon_min),
            )
        except Exception as exc:
            candidate_analysis = {"available": False, "error": str(exc)}
    else:
        candidate_analysis = {"available": False, "reason": "db_missing", "db_path": str(candidate_path)}
    hold_decisions = Path(hold_decision_dir) if hold_decision_dir else get_runtime_path("logs", "hold_advisor")
    hold_latency = analyze_hold_advisor_latency(
        decision_dir=hold_decisions,
        start_date=hold_start_date or session_date,
        end_date=hold_end_date or session_date,
        market=market_key or "ALL",
        source="auto",
    )
    payload = {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "mode": runtime_mode,
        "market": market_key,
        "session_date": session_date,
        "horizon_min": int(horizon_min),
        "candidate_analysis": candidate_analysis,
        "candidate_bucket_source_score_coverage": _candidate_bucket_source_score_coverage(
            db_path=candidate_path,
            session_date=session_date,
            market=market_key,
            runtime_mode=runtime_mode,
        ),
        "candidate_metadata_coverage": _candidate_metadata_coverage_report(
            db_path=candidate_path,
            session_date=session_date,
            market=market_key,
            runtime_mode=runtime_mode,
        ),
        "selection_call_breakdown": _selection_call_breakdown_report(
            db_path=candidate_path,
            session_date=session_date,
            market=market_key,
            runtime_mode=runtime_mode,
        ),
        "candidate_resolved_reason": _candidate_resolved_reason_report(
            db_path=candidate_path,
            session_date=session_date,
            market=market_key,
            runtime_mode=runtime_mode,
        ),
        "pathb_missed_opportunity": _pathb_missed_opportunity_report(
            db_path=event_path,
            session_date=session_date,
            market=market_key,
            runtime_mode=runtime_mode,
        ),
        "kis_token_status": _kis_token_status(runtime_mode),
        "v2_learning_gate": _v2_learning_gate_report(learning_path, market=market_key, runtime_mode=runtime_mode),
        "kr_live_expansion_guard": _kr_live_expansion_guard_report(runtime_mode),
        "hold_advisor_latency": hold_latency,
        "hold_advisor_cache_shadow": _hold_advisor_cache_shadow(
            decision_dir=hold_decisions,
            start_date=hold_start_date or session_date,
            end_date=hold_end_date or session_date,
            market=market_key or "ALL",
            ttl_minutes=hold_cache_ttl_minutes,
        ),
        "pead_manual_review": _pead_manual_review_report(
            state_path=Path(pead_state) if pead_state else get_runtime_path("state", "pead_shadow_state.json"),
            log_dir=Path(pead_log_dir) if pead_log_dir else get_runtime_path("logs", "pead"),
        ),
    }
    if isinstance(payload.get("pathb_missed_opportunity"), dict):
        payload["pathb_missed_opportunity"]["source_overlay"] = {
            "event_store": "pathb_miss_quality",
            "candidate_audit": "candidate_resolved_reason",
            "funnel": "candidate_analysis.routing_delta",
        }
        payload["pathb_missed_opportunity"]["candidate_resolved_reason_counts"] = dict(
            (payload.get("candidate_resolved_reason") or {}).get("reason_counts") or {}
        )
        payload["pathb_missed_opportunity"]["routing_delta_reason_counts"] = dict(
            (((payload.get("candidate_analysis") or {}).get("routing_delta") or {}).get("route_reason_counts") or {})
        )
    payload["gate_summary"] = {
        "actual_prompt_visibility": (
            candidate_analysis.get("consistency") or candidate_analysis.get("candidate_consistency") or {}
        )
        if isinstance(candidate_analysis, dict)
        else {},
        "bucket_source_score_performance_allowed": False,
        "watch_trigger_policy_change_allowed": False,
        "learning_policy_change_allowed": False,
        "kr_live_expansion_allowed": False,
        "pathb_missed_opportunity_policy_change_allowed": False,
        "pead_policy_change_allowed": False,
    }
    if write_report:
        payload["report_paths"] = _write_report(payload, report_dir)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build read-only monitoring operations report.")
    parser.add_argument("--candidate-db", default="")
    parser.add_argument("--learning-db", default="")
    parser.add_argument("--event-db", default="")
    parser.add_argument("--mode", default="live", choices=["live", "paper"])
    parser.add_argument("--date", default="")
    parser.add_argument("--market", default="")
    parser.add_argument("--horizon-min", type=int, default=60)
    parser.add_argument("--pead-state", default="")
    parser.add_argument("--pead-log-dir", default="")
    parser.add_argument("--hold-decision-dir", default="")
    parser.add_argument("--hold-start-date", default="")
    parser.add_argument("--hold-end-date", default="")
    parser.add_argument("--hold-cache-ttl-minutes", type=int, default=10)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-dir", default="")
    args = parser.parse_args(argv)
    payload = build_monitoring_ops_report(
        candidate_db=args.candidate_db or None,
        learning_db=args.learning_db or None,
        event_db=args.event_db or None,
        mode=args.mode,
        session_date=args.date,
        market=args.market,
        horizon_min=args.horizon_min,
        pead_state=args.pead_state or None,
        pead_log_dir=args.pead_log_dir or None,
        hold_decision_dir=args.hold_decision_dir or None,
        hold_start_date=args.hold_start_date,
        hold_end_date=args.hold_end_date,
        hold_cache_ttl_minutes=args.hold_cache_ttl_minutes,
        write_report=args.write_report,
        report_dir=args.report_dir or None,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
