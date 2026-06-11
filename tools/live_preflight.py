from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover
    dotenv_values = None  # type: ignore

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
KST = ZoneInfo("Asia/Seoul") if ZoneInfo is not None else None

from bot.session_date import is_known_market_holiday, resolve_session_date
from runtime.market_resolver import infer_ticker_market
from runtime.broker_truth_snapshot import age_seconds as _broker_truth_age_seconds
from runtime_paths import get_runtime_path
from tools.order_unknown_evidence import (
    attach_exposure_evidence as _shared_attach_order_unknown_exposure_evidence,
    mark_order_unknown_remediation_hint as _shared_mark_order_unknown_remediation_hint,
    pathb_local_exposure_index as _shared_pathb_local_exposure_index,
)

LIVE_CONFIG_KEYS = {
    "ENABLED_MARKETS",
    "KR_FIXED_ORDER_KRW",
    "US_FIXED_ORDER_KRW",
    "KR_MIN_ORDER_KRW",
    "US_MIN_ORDER_KRW",
    "KR_MAX_POSITIONS",
    "US_MAX_POSITIONS",
    "DAILY_LOSS_LIMIT_PCT",
    "V2_MAX_DAILY_ENTRIES",
    "KR_DAILY_ENTRY_CAP",
    "US_DAILY_ENTRY_CAP",
    "KR_CONFIRMATION_GATE_ENABLED",
    "KR_CONFIRMATION_GATE_SHADOW",
    "KR_CONFIRMATION_GATE_MODE",
    "ENABLE_CLAUDE_CANDIDATE_ACTIONS",
    "ENABLE_ACTION_ROUTING",
    "CANDIDATE_ACTIONS_V2_ENABLED",
    "ALLOW_LEGACY_SELECTION_AUTO_READY",
    "KR_FAST_TRIGGER_WINDOW_MIN",
    "WATCH_TRIGGER_INITIAL_THRESHOLD",
    "PATHA_ENTRY_SCAN_HOT_FAST_ENABLED",
    "KR_MICROSTRUCTURE_CONTEXT_ENABLED",
    "KR_ORDERBOOK_TIMEOUT_SEC",
    "KR_ORDERBOOK_MAX_RETRIES",
    "KR_ORDERBOOK_RETRY_BACKOFF_SEC",
    "KR_ORDERBOOK_CACHE_TTL_SEC",
    "KR_VI_TIMEOUT_SEC",
    "KR_VI_MAX_RETRIES",
    "KR_VI_RETRY_BACKOFF_SEC",
    "KR_VI_CACHE_TTL_SEC",
    "V2_LIFECYCLE_ENABLED",
    "PATHA_TIMING_LIFECYCLE_ENABLED",
    "V2_FIXED_SIZING_ENABLED",
    "V2_BRAIN_POLICY",
    "V2_FRESH_BRAIN_START",
    "PATHB_MODE",
    "PATHB_ENABLED",
    "PATHB_KR_LIVE_ENABLED",
    "KR_CLAUDE_PRICE_LIVE_ENABLED",
    "KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK",
    "KR_CONTINUATION_NEW_ENTRY_BLOCK",
    "PATHB_US_LIVE_ENABLED",
    "PATHB_TELEGRAM_CONTROL_ENABLED",
    "PATHB_FIXED_ORDER_KRW",
    "PATHB_MAX_POSITIONS",
    "PATHB_MAX_DAILY_ENTRIES",
    "PATHB_MIN_CONFIDENCE",
    "PATHB_INTRADAY_ONLY",
    "PATHB_ALLOW_STOP_LOSS_LOWERING",
    "PATHB_ALLOW_SAME_TICKER_WITH_PATHA",
    "PATHB_ORDER_UNKNOWN_HALTS_ENTRY",
    "PATHB_KR_SLIPPAGE_CAP",
    "PATHB_US_SLIPPAGE_CAP",
    "PATHB_SELL_PARTIAL_WAIT_SEC",
    "PATHB_PRE_CLOSE_MARKET_FALLBACK",
    "PATHB_PRE_CLOSE_TIMEOUT_MINUTES",
    "PATHB_EMERGENCY_DISABLE",
    "PATHB_PREOPEN_EXIT_POLICY_MODE",
    "US_PATHB_PREOPEN_EXIT_POLICY_MODE",
    "KR_PATHB_PREOPEN_EXIT_POLICY_MODE",
    "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US",
    "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR",
    "PATHB_SELECTION_RECONCILE_ENABLED",
    "PATHB_SELECTION_RECONCILE_MODE",
    "US_PATHB_SELECTION_RECONCILE_MODE",
    "KR_PATHB_SELECTION_RECONCILE_MODE",
    "PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER",
    "US_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER",
    "KR_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER",
    "PATHB_SELECTION_RECONCILE_ZONE_UPDATE_ENABLED",
    "PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE",
    "US_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE",
    "KR_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE",
    "PATHB_SELECTION_RECONCILE_CANCEL_INVALID",
    "PATHB_SELECTION_RECONCILE_CANCEL_SUSPENDED",
    "PATHB_SELECTION_RECONCILE_UPDATE_VALID_TARGETS",
    "PATHB_SELECTION_RECONCILE_HIT_SUSPEND_CANCEL",
    "PATHB_RECONCILE_FORCE_FRESH_AFTER_CANCEL",
    "KR_REENTRY_COOLDOWN_MINUTES",
    "US_REENTRY_COOLDOWN_MINUTES",
    "USD_KRW_RATE",
    "KIS_US_CREDENTIAL_FALLBACK_ACCEPTED",
    "KR_MAX_SINGLE_LOSS_PCT",
    "KR_LOSS_CAP_SHADOW_PCT",
    "AUTO_TRAIL_PCT_KR",
    "KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED",
    "KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED",
    "KR_PLAN_A_ORP_SIGNAL_ENABLED",
    "KR_PATHB_BULL_MODE_GATE_ENABLED",
    "KR_PATHB_BULL_MODE_GATE_SHADOW",
    "KR_PATHB_BULL_MODE_GATE_ALLOWED_MODES",
    "KR_PATHB_STRATEGY_FILTER_ENABLED",
    "KR_PATHB_STRATEGY_FILTER_SHADOW",
    "KR_PATHB_STRATEGY_ALLOWLIST",
    "PLANA_MFE_BREAKEVEN_ENABLED",
    "PATHB_MFE_BREAKEVEN_ENABLED",
    "ACTIVE_LESSONS_ENABLED",
    "ACTIVE_LESSONS_SHADOW",
    "ACTIVE_LESSONS_MAX_ITEMS",
    "ACTIVE_LESSONS_MAX_CHARS",
    "ACTIVE_LESSONS_ANALYST_MAX_CHARS",
    "ACTIVE_LESSONS_DEBATE_ENABLED",
    "ACTIVE_LESSONS_DEBATE_MAX_CHARS",
    "BULL_R1_MODEL",
    "BEAR_R1_MODEL",
    "NEUTRAL_R1_MODEL",
    "CLAUDE_ANALYST_R1_MAX_TOKENS",
    "CLAUDE_ANALYST_R2_MAX_TOKENS",
    "ENABLE_KR_CANDIDATE_QUALITY_PROMPT",
    "ENABLE_KR_CANDIDATE_QUALITY_SHADOW",
    "ENABLE_KR_CANDIDATE_RS_INDEX_CACHE",
    "KR_INDEX_CACHE_MAX_AGE_HOURS",
    "KR_INDEX_CACHE_REQUIRE_PREWARM",
    "ENABLE_KR_CANDIDATE_FLOW_SHADOW",
    "ENABLE_KR_CANDIDATE_FLOW_PREFETCH",
    "KR_CANDIDATE_FLOW_PREFETCH_MAX",
    "KR_CANDIDATE_FLOW_PREFETCH_SLEEP_SEC",
    "CANDIDATE_TRAINER_QUALITY_SCORE_ENABLED",
    "CANDIDATE_TRAINER_QUALITY_SCORE_WEIGHT",
    "US_QUALITY_SHADOW_ENABLED",
    "KR_CANDIDATE_POST_RANK_ENABLED",
    "KR_CANDIDATE_POST_RANK_ENFORCE",
    "US_DYNAMIC_LOSERS_QUOTA_ENABLED",
    "ENABLE_CANDIDATE_AUDIT_LIVE",
    "ENABLE_CANDIDATE_AUDIT_SHADOW",
    "CANDIDATE_AUDIT_DB_PATH",
    "US_EARLY_ENTRY_SOFT_GATE_ENABLED",
    "US_EARLY_ENTRY_SOFT_GATE_START_MIN",
    "US_EARLY_ENTRY_SOFT_GATE_END_MIN",
    "US_EARLY_ENTRY_SIZE_MULT",
}

RUNTIME_CONFIG_DRIFT_KEYS = {
    "ENABLED_MARKETS",
    "V2_MAX_DAILY_ENTRIES",
    "KR_DAILY_ENTRY_CAP",
    "US_DAILY_ENTRY_CAP",
    "KR_CONFIRMATION_GATE_ENABLED",
    "KR_CONFIRMATION_GATE_SHADOW",
    "KR_CONFIRMATION_GATE_MODE",
    "KR_FAST_TRIGGER_WINDOW_MIN",
    "WATCH_TRIGGER_INITIAL_THRESHOLD",
    "PATHA_ENTRY_SCAN_HOT_FAST_ENABLED",
    "KR_MICROSTRUCTURE_CONTEXT_ENABLED",
    "KR_ORDERBOOK_TIMEOUT_SEC",
    "KR_ORDERBOOK_MAX_RETRIES",
    "KR_ORDERBOOK_RETRY_BACKOFF_SEC",
    "KR_ORDERBOOK_CACHE_TTL_SEC",
    "KR_VI_TIMEOUT_SEC",
    "KR_VI_MAX_RETRIES",
    "KR_VI_RETRY_BACKOFF_SEC",
    "KR_VI_CACHE_TTL_SEC",
    "PATHB_ENABLED",
    "PATHB_KR_LIVE_ENABLED",
    "KR_CLAUDE_PRICE_LIVE_ENABLED",
    "KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK",
    "KR_CONTINUATION_NEW_ENTRY_BLOCK",
    "PATHB_US_LIVE_ENABLED",
    "KR_MAX_SINGLE_LOSS_PCT",
    "KR_LOSS_CAP_SHADOW_PCT",
    "AUTO_TRAIL_PCT_KR",
    "KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED",
    "KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED",
    "KR_PLAN_A_ORP_SIGNAL_ENABLED",
    "KR_PATHB_BULL_MODE_GATE_ENABLED",
    "KR_PATHB_BULL_MODE_GATE_SHADOW",
    "KR_PATHB_BULL_MODE_GATE_ALLOWED_MODES",
    "KR_PATHB_STRATEGY_FILTER_ENABLED",
    "KR_PATHB_STRATEGY_FILTER_SHADOW",
    "KR_PATHB_STRATEGY_ALLOWLIST",
    "PATHB_MAX_POSITIONS",
    "PATHB_MAX_DAILY_ENTRIES",
    "PATHB_FIXED_ORDER_KRW",
    "PATHB_PREOPEN_EXIT_POLICY_MODE",
    "US_PATHB_PREOPEN_EXIT_POLICY_MODE",
    "KR_PATHB_PREOPEN_EXIT_POLICY_MODE",
    "PATHB_SELECTION_RECONCILE_ENABLED",
    "PATHB_SELECTION_RECONCILE_MODE",
    "US_PATHB_SELECTION_RECONCILE_MODE",
    "KR_PATHB_SELECTION_RECONCILE_MODE",
    "PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER",
    "US_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER",
    "KR_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER",
    "PATHB_SELECTION_RECONCILE_ZONE_UPDATE_ENABLED",
    "PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE",
    "US_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE",
    "KR_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE",
    "PATHB_SELECTION_RECONCILE_HIT_SUSPEND_CANCEL",
    "PATHB_RECONCILE_FORCE_FRESH_AFTER_CANCEL",
    "ACTIVE_LESSONS_ENABLED",
    "ACTIVE_LESSONS_SHADOW",
    "ACTIVE_LESSONS_MAX_ITEMS",
    "ACTIVE_LESSONS_MAX_CHARS",
    "ACTIVE_LESSONS_ANALYST_MAX_CHARS",
    "ACTIVE_LESSONS_DEBATE_ENABLED",
    "ACTIVE_LESSONS_DEBATE_MAX_CHARS",
    "BULL_R1_MODEL",
    "BEAR_R1_MODEL",
    "NEUTRAL_R1_MODEL",
    "CLAUDE_ANALYST_R1_MAX_TOKENS",
    "CLAUDE_ANALYST_R2_MAX_TOKENS",
    "ENABLE_KR_CANDIDATE_QUALITY_PROMPT",
    "ENABLE_KR_CANDIDATE_QUALITY_SHADOW",
    "ENABLE_KR_CANDIDATE_RS_INDEX_CACHE",
    "KR_INDEX_CACHE_MAX_AGE_HOURS",
    "KR_INDEX_CACHE_REQUIRE_PREWARM",
    "ENABLE_KR_CANDIDATE_FLOW_SHADOW",
    "ENABLE_KR_CANDIDATE_FLOW_PREFETCH",
    "KR_CANDIDATE_FLOW_PREFETCH_MAX",
    "KR_CANDIDATE_FLOW_PREFETCH_SLEEP_SEC",
    "CANDIDATE_TRAINER_QUALITY_SCORE_ENABLED",
    "CANDIDATE_TRAINER_QUALITY_SCORE_WEIGHT",
    "US_QUALITY_SHADOW_ENABLED",
    "KR_CANDIDATE_POST_RANK_ENABLED",
    "KR_CANDIDATE_POST_RANK_ENFORCE",
    "US_DYNAMIC_LOSERS_QUOTA_ENABLED",
    "ENABLE_CANDIDATE_AUDIT_LIVE",
    "ENABLE_CANDIDATE_AUDIT_SHADOW",
    "CANDIDATE_AUDIT_DB_PATH",
    "US_EARLY_ENTRY_SOFT_GATE_ENABLED",
    "US_EARLY_ENTRY_SOFT_GATE_START_MIN",
    "US_EARLY_ENTRY_SOFT_GATE_END_MIN",
    "US_EARLY_ENTRY_SIZE_MULT",
}

CRITICAL_RUNTIME_CONFIG_DRIFT_KEYS = {
    "ENABLED_MARKETS",
    "PATHB_ENABLED",
    "PATHB_KR_LIVE_ENABLED",
    "PATHB_US_LIVE_ENABLED",
    "KR_CLAUDE_PRICE_LIVE_ENABLED",
    "KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK",
    "KR_CONTINUATION_NEW_ENTRY_BLOCK",
    "AUTO_TRAIL_PCT_KR",
    "KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED",
    "KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED",
    "KR_PLAN_A_ORP_SIGNAL_ENABLED",
    "KR_PATHB_BULL_MODE_GATE_ENABLED",
    "KR_PATHB_STRATEGY_FILTER_ENABLED",
    "PATHB_MAX_POSITIONS",
    "PATHB_MAX_DAILY_ENTRIES",
    "PATHB_FIXED_ORDER_KRW",
    "US_PATHB_PREOPEN_EXIT_POLICY_MODE",
    "KR_PATHB_PREOPEN_EXIT_POLICY_MODE",
    "PATHB_SELECTION_RECONCILE_ENABLED",
    "US_PATHB_SELECTION_RECONCILE_MODE",
    "KR_PATHB_SELECTION_RECONCILE_MODE",
    "PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER",
    "US_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER",
    "KR_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER",
    "PATHB_SELECTION_RECONCILE_ZONE_UPDATE_ENABLED",
    "US_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE",
    "KR_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE",
}

REQUIRED_TABLE_COLUMNS = {
    "v2_decisions": {
        "decision_id",
        "market",
        "runtime_mode",
        "session_date",
        "ticker",
        "prompt_version",
        "brain_snapshot_id",
        "status",
        "payload_json",
    },
    "lifecycle_events": {
        "event_id",
        "event_type",
        "market",
        "runtime_mode",
        "session_date",
        "ticker",
        "decision_id",
        "execution_id",
        "position_id",
        "reason_code",
        "prompt_version",
        "brain_snapshot_id",
        "payload_json",
        "occurred_at",
    },
    "v2_path_runs": {
        "path_run_id",
        "decision_id",
        "path_type",
        "market",
        "runtime_mode",
        "session_date",
        "ticker",
        "status",
        "plan_json",
        "created_at",
        "updated_at",
    },
    "phase_validation_runs": {"id", "phase", "ok", "qa", "simulation_report", "report_json", "created_at"},
}

PATHB_ACTIVE_ISH_STATUSES = {
    "ORDER_UNKNOWN",
    "ORDER_ACKED",
    "ORDER_SENT",
    "PARTIAL_FILLED",
    "FILLED",
    "SELL_SENT",
    "SELL_ACKED",
    "SELL_PARTIAL_FILLED",
}
PATHB_TERMINAL_EVENT_BY_STATUS = {
    "FILLED": "FILLED",
    "PARTIAL_FILLED": "FILLED",
    "CLOSED": "CLOSED",
}
PATHB_PRE_RUN_EVENT_TYPES = {"CLAUDE_PRICE_PLAN_GATE_WARNING", "SAFETY_BLOCKED"}


def _safe_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(str(value or "{}"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _first_nonempty(mapping: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _pathb_operator_context(item: dict[str, Any], plan: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = plan if isinstance(plan, dict) else {}
    order_no = _first_nonempty(
        item,
        ("order_no", "order_id", "execution_id", "entry_execution_id", "sell_order_no", "sell_order_id"),
    ) or _first_nonempty(
        plan,
        (
            "order_no",
            "order_id",
            "execution_id",
            "entry_execution_id",
            "sell_order_no",
            "sell_order_id",
            "pending_sell_order_no",
            "pathb_pending_sell_order_no",
        ),
    )
    last_event_at = _first_nonempty(item, ("last_event_at", "updated_at", "created_at")) or _first_nonempty(
        plan,
        ("last_event_at", "updated_at", "created_at", "filled_at", "sell_order_sent_at"),
    )
    action = (
        "read-only: verify broker positions/open orders/fills, then use audited remediation/backfill; "
        "do not close local PathB rows from DB state alone"
    )
    return {
        "order_no": order_no,
        "last_event_at": last_event_at,
        "operator_action": action,
        "remediation_requires_broker_truth": True,
        "read_only_check": True,
        "auto_apply_allowed": False,
    }


def _ticker_key(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "")))
    except Exception:
        return int(default)


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "[]")
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _path_run_id_from_payload(payload: dict[str, Any]) -> str:
    return str(payload.get("path_run_id") or payload.get("pathb_path_run_id") or "").strip()


def _pathb_like_missing_path_run_id_rows(
    event_rows: list[sqlite3.Row] | list[dict[str, Any]],
    decision_ids_with_runs: set[str],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    pre_run: list[dict[str, Any]] = []
    post_run: list[dict[str, Any]] = []
    linkable = 0
    unlinkable = 0
    for row in event_rows:
        payload = _safe_json_object(row["payload_json"] if "payload_json" in row.keys() else row.get("payload_json"))
        event_type = str(row["event_type"] if "event_type" in row.keys() else row.get("event_type") or "")
        path_run_id = _path_run_id_from_payload(payload)
        path_type = str(payload.get("path_type") or payload.get("buy_path") or "")
        if not ((event_type.startswith("CLAUDE_PRICE") or path_type in {"claude_price", "path_b"}) and not path_run_id):
            continue
        decision_id = str(row["decision_id"] if "decision_id" in row.keys() else row.get("decision_id") or "")
        item = {
            "event_type": event_type,
            "market": row["market"] if "market" in row.keys() else row.get("market"),
            "ticker": row["ticker"] if "ticker" in row.keys() else row.get("ticker"),
            "decision_id": decision_id,
        }
        rows.append(item)
        if decision_id and decision_id in decision_ids_with_runs:
            linkable += 1
        else:
            unlinkable += 1
        if event_type in PATHB_PRE_RUN_EVENT_TYPES:
            pre_run.append(item)
        else:
            post_run.append(item)
    return {
        "rows": rows,
        "pre_run": pre_run,
        "post_run": post_run,
        "decision_id_linkable_count": linkable,
        "decision_id_unlinkable_count": unlinkable,
    }


def _pathb_terminal_missing_events(
    run_rows: list[sqlite3.Row] | list[dict[str, Any]],
    event_rows: list[sqlite3.Row] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    event_by_path_and_type: set[tuple[str, str]] = set()
    for row in event_rows:
        payload = _safe_json_object(row["payload_json"] if "payload_json" in row.keys() else row.get("payload_json"))
        path_run_id = _path_run_id_from_payload(payload)
        if path_run_id:
            event_type = str(row["event_type"] if "event_type" in row.keys() else row.get("event_type") or "")
            event_by_path_and_type.add((path_run_id, event_type))
    missing: list[dict[str, Any]] = []
    for row in run_rows:
        status = str(row["status"] if "status" in row.keys() else row.get("status") or "")
        expected = PATHB_TERMINAL_EVENT_BY_STATUS.get(status)
        if not expected:
            continue
        path_run_id = str(row["path_run_id"] if "path_run_id" in row.keys() else row.get("path_run_id") or "")
        if (path_run_id, expected) in event_by_path_and_type:
            continue
        missing.append(
            {
                "path_run_id": path_run_id,
                "market": row["market"] if "market" in row.keys() else row.get("market"),
                "runtime_mode": row["runtime_mode"] if "runtime_mode" in row.keys() else row.get("runtime_mode"),
                "session_date": row["session_date"] if "session_date" in row.keys() else row.get("session_date"),
                "ticker": row["ticker"] if "ticker" in row.keys() else row.get("ticker"),
                "status": status,
                "missing_event": expected,
            }
        )
    return missing


def _pathb_cross_run_closed_lifecycle_evidence(
    run_rows: list[sqlite3.Row] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    inconsistent: list[dict[str, Any]] = []
    for row in run_rows:
        plan = _safe_json_object(row["plan_json"] if "plan_json" in row.keys() else row.get("plan_json"))
        evidence = plan.get("pathb_closed_lifecycle_evidence")
        if not isinstance(evidence, dict):
            continue
        path_run_id = str(row["path_run_id"] if "path_run_id" in row.keys() else row.get("path_run_id") or "")
        evidence_path_run_id = _path_run_id_from_payload(evidence)
        if not path_run_id or not evidence_path_run_id or evidence_path_run_id == path_run_id:
            continue
        inconsistent.append(
            {
                "path_run_id": path_run_id,
                "evidence_path_run_id": evidence_path_run_id,
                "market": row["market"] if "market" in row.keys() else row.get("market"),
                "runtime_mode": row["runtime_mode"] if "runtime_mode" in row.keys() else row.get("runtime_mode"),
                "session_date": row["session_date"] if "session_date" in row.keys() else row.get("session_date"),
                "ticker": row["ticker"] if "ticker" in row.keys() else row.get("ticker"),
                "status": row["status"] if "status" in row.keys() else row.get("status"),
                "evidence_event_id": evidence.get("event_id"),
                "evidence_execution_id": evidence.get("execution_id"),
                "exit_execution_id": plan.get("exit_execution_id"),
                "close_reason": plan.get("close_reason"),
                "pending_close_reason": plan.get("pending_close_reason"),
            }
        )
    return inconsistent


def _pathb_lifecycle_window_check_result(
    inconsistent_runs: list[dict[str, Any]],
    window_missing_path: dict[str, Any],
) -> CheckResult:
    post_run_missing_path = list(window_missing_path.get("post_run") or [])
    pre_run_missing_path = list(window_missing_path.get("pre_run") or [])
    remediation_required = bool(inconsistent_runs or post_run_missing_path)
    if remediation_required:
        status = "WARN"
        detail = (
            "recent-window Path B lifecycle diagnostic warnings: "
            f"recent_window_missing_events_count={len(inconsistent_runs)} "
            "recent_window_size_events=1000 recent_window_size_runs=500; "
            f"PathB post-run lifecycle events missing payload_json.path_run_id={len(post_run_missing_path)}"
        )
        operator_action = "review lifecycle diagnostics before mutating production records"
    else:
        status = "PASS"
        detail = (
            "recent-window Path B terminal/post-run lifecycle rows are internally consistent"
            if not pre_run_missing_path
            else (
                "recent-window Path B terminal/post-run lifecycle rows are internally consistent; "
                f"pre-run events without path_run_id={len(pre_run_missing_path)}"
            )
        )
        operator_action = "none; pre-run safety/gate events can occur before a path_run_id exists"
    return CheckResult(
        "db.pathb_lifecycle_window_consistency",
        status,
        detail,
        {
            "missing_events": inconsistent_runs[:30],
            "pathb_events_missing_path_run_id": list(window_missing_path.get("rows") or [])[:30],
            "pathb_pre_run_events_missing_path_run_id": pre_run_missing_path[:30],
            "pathb_post_run_events_missing_path_run_id": post_run_missing_path[:30],
            "missing_events_count": len(inconsistent_runs),
            "recent_window_missing_events_count": len(inconsistent_runs),
            "recent_window_size_events": 1000,
            "recent_window_size_runs": 500,
            "pathb_events_missing_path_run_id_count": len(window_missing_path.get("rows") or []),
            "pathb_pre_run_events_missing_path_run_id_count": len(pre_run_missing_path),
            "pathb_post_run_events_missing_path_run_id_count": len(post_run_missing_path),
            "decision_id_linkable_count": window_missing_path["decision_id_linkable_count"],
            "decision_id_unlinkable_count": window_missing_path["decision_id_unlinkable_count"],
            "accepted_exception": bool(pre_run_missing_path and not remediation_required),
            "remediation_required": remediation_required,
            "remediation_tool": "python tools/pathb_legacy_remediation.py --mode live --write-report",
            "operator_action": operator_action,
        },
    )


def _pathb_local_exposure_index(mode: str) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], list[dict[str, Any]]]]:
    return _shared_pathb_local_exposure_index(mode)


def _broker_evidence_for_ticker(
    broker_snapshot: dict[str, Any],
    market: str,
    ticker: str,
    *,
    local_sell_order_id: str = "",
) -> dict[str, Any]:
    markets = broker_snapshot.get("markets") if isinstance(broker_snapshot, dict) else {}
    market_data = markets.get(market) if isinstance(markets, dict) else {}
    snapshot_error = str(broker_snapshot.get("load_error") or "") if isinstance(broker_snapshot, dict) else ""
    market_present = isinstance(market_data, dict) and bool(market_data)
    stale = bool((market_data or {}).get("stale")) if isinstance(market_data, dict) else False
    missing = bool((market_data or {}).get("missing")) if isinstance(market_data, dict) else True
    error = str((market_data or {}).get("error") or snapshot_error or "") if isinstance(market_data, dict) else snapshot_error
    last_success_at = str((market_data or {}).get("last_success_at") or "") if isinstance(market_data, dict) else ""
    if not market_present or missing or stale or error:
        return {
            "broker_truth_unavailable": True,
            "broker_truth_market_present": bool(market_present),
            "broker_truth_stale": bool(stale),
            "broker_truth_error": error,
            "broker_position_qty": None,
            "broker_position_count": 0,
            "broker_open_order_count": 0,
            "broker_fill_count": 0,
            "broker_open_order_evidence": False,
            "broker_any_open_order_evidence": False,
            "broker_sell_fill_evidence": False,
            "broker_any_fill_evidence": False,
            "broker_truth_last_success_at": last_success_at,
        }
    positions = _broker_rows_for_ticker(list(market_data.get("positions") or []), market, ticker)
    open_orders = _broker_rows_for_ticker(list(market_data.get("open_orders") or []), market, ticker)
    fills = _broker_rows_for_ticker(list(market_data.get("today_fills") or []), market, ticker)
    active_open_orders = [
        row for row in open_orders
        if _safe_int(row.get("remaining_qty", row.get("order_qty", row.get("qty", 0)))) > 0
    ]
    filled_rows = [
        row for row in fills
        if _safe_int(row.get("filled_qty", row.get("qty", 0))) > 0
    ]
    open_sell = [
        row for row in active_open_orders
        if (not local_sell_order_id or _broker_order_id(row) == local_sell_order_id)
        and (not _row_side(row) or _row_side(row) == "sell")
    ]
    sell_fills = [
        row for row in filled_rows
        if (not local_sell_order_id or _broker_order_id(row) == local_sell_order_id)
        and (not _row_side(row) or _row_side(row) == "sell")
    ]
    return {
        "broker_truth_unavailable": False,
        "broker_truth_market_present": True,
        "broker_truth_stale": False,
        "broker_truth_error": "",
        "broker_position_qty": sum(_safe_int(row.get("qty")) for row in positions),
        "broker_position_count": len(positions),
        "broker_open_order_count": len(active_open_orders),
        "broker_fill_count": len(filled_rows),
        "broker_open_order_evidence": bool(open_sell),
        "broker_any_open_order_evidence": bool(active_open_orders),
        "broker_sell_fill_evidence": bool(sell_fills),
        "broker_any_fill_evidence": bool(filled_rows),
        "broker_truth_last_success_at": last_success_at,
    }


def _db_broker_truth_ttl_sec() -> int:
    try:
        return max(30, int(os.getenv("PREFLIGHT_DB_BROKER_TRUTH_TTL_SEC", "300") or 300))
    except Exception:
        return 300


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat(timespec="seconds").replace("+00:00", "Z")


def _broker_truth_stale_reason(
    *,
    missing: bool,
    stale: bool,
    error: str,
    last_success_at: str,
    age_sec: float | None,
    ttl_sec: int,
) -> str:
    if missing:
        return "missing"
    if error:
        return "error"
    if not last_success_at:
        return "last_success_missing"
    if age_sec is None:
        return "last_success_unparseable"
    if stale or age_sec > ttl_sec:
        return "age_gt_ttl"
    return ""


def _broker_truth_freshness_fields(item: dict[str, Any], *, evaluated_at: datetime) -> dict[str, Any]:
    ttl_sec = _safe_int(item.get("ttl_sec"), 60)
    last_success_at = str(item.get("last_success_at", "") or "")
    age = _broker_truth_age_seconds(last_success_at, now=evaluated_at)
    age_value = round(age, 3) if age is not None else None
    ttl_margin = round(float(ttl_sec) - age, 3) if age is not None else None
    missing = bool(item.get("missing", True))
    stale = bool(item.get("stale", True))
    error = str(item.get("error", "") or "")
    return {
        "evaluated_at": _utc_iso(evaluated_at),
        "age_sec": age_value,
        "ttl_sec": ttl_sec,
        "ttl_margin_sec": ttl_margin,
        "stale_reason": _broker_truth_stale_reason(
            missing=missing,
            stale=stale,
            error=error,
            last_success_at=last_success_at,
            age_sec=age,
            ttl_sec=ttl_sec,
        ),
    }


def _load_broker_truth_snapshot_for_db(mode: str) -> dict[str, Any]:
    from runtime.broker_truth_snapshot import BrokerTruthSnapshot

    ttl = _db_broker_truth_ttl_sec()
    return BrokerTruthSnapshot(runtime_mode=mode).load_snapshot(ttl_by_market={"KR": ttl, "US": ttl})


def _load_broker_truth_snapshot_for_state_check(
    mode: str,
    snapshot_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], bool, str]:
    raw_data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    raw_markets = (
        raw_data.get("markets")
        if isinstance(raw_data, dict) and isinstance(raw_data.get("markets"), dict)
        else {}
    )
    try:
        from runtime.broker_truth_snapshot import BrokerTruthSnapshot

        effective_data = BrokerTruthSnapshot(runtime_mode=mode, path=snapshot_path).load_snapshot()
        effective_markets = (
            effective_data.get("markets") if isinstance(effective_data.get("markets"), dict) else {}
        )
        return raw_data, effective_markets, True, ""
    except Exception as exc:
        return raw_data, raw_markets, False, f"{type(exc).__name__}: {exc}"


def _broker_truth_snapshot_file_checks(mode: str, snapshot_path: Path) -> list[CheckResult]:
    checks: list[CheckResult] = []
    if not snapshot_path.exists():
        startup_missing = {"path": str(snapshot_path), "snapshot_missing": True, "startup_expected": True}
        checks.append(
            CheckResult(
                "broker_truth.snapshot_missing_or_present",
                "WARN",
                "broker truth snapshot is missing; bot startup should create it",
                dict(startup_missing),
            )
        )
        checks.append(
            CheckResult(
                "broker_truth.snapshot_file_valid",
                "WARN",
                "snapshot file missing before bot startup",
                dict(startup_missing),
            )
        )
        return checks

    try:
        raw_data, markets, stale_recomputed, recompute_error = _load_broker_truth_snapshot_for_state_check(
            mode,
            snapshot_path,
        )
        raw_markets = raw_data.get("markets") if isinstance(raw_data.get("markets"), dict) else {}
        checks.append(
            CheckResult(
                "broker_truth.snapshot_missing_or_present",
                "PASS",
                "broker truth snapshot exists",
                {"path": str(snapshot_path)},
            )
        )
        checks.append(
            CheckResult(
                "broker_truth.snapshot_file_valid",
                "PASS",
                "broker truth snapshot JSON parses",
                {"path": str(snapshot_path)},
            )
        )
        evaluated_at = _utc_now()
        for market in ("KR", "US"):
            raw_item = raw_markets.get(market) if isinstance(raw_markets.get(market), dict) else {}
            item = markets.get(market) if isinstance(markets.get(market), dict) else {}
            freshness = _broker_truth_freshness_fields(item, evaluated_at=evaluated_at)
            status = "PASS"
            detail = f"{market} snapshot available"
            if not item or bool(item.get("missing", True)):
                status = "WARN"
                detail = f"{market} snapshot missing/account lookup pending"
            elif str(item.get("error") or ""):
                status = "WARN"
                detail = f"{market} snapshot error"
            elif bool(item.get("stale", False)):
                status = "WARN"
                detail = f"{market} snapshot stale"
            checks.append(
                CheckResult(
                    f"broker_truth.{market.lower()}_stale_state",
                    status,
                    detail,
                    {
                        "last_success_at": item.get("last_success_at", ""),
                        "last_attempt_at": item.get("last_attempt_at", ""),
                        "ttl_sec": freshness.get("ttl_sec"),
                        "age_sec": freshness.get("age_sec"),
                        "ttl_margin_sec": freshness.get("ttl_margin_sec"),
                        "evaluated_at": freshness.get("evaluated_at"),
                        "stale_reason": freshness.get("stale_reason"),
                        "error": item.get("error", ""),
                        "stale": bool(item.get("stale", False)),
                        "stored_stale": raw_item.get("stale", ""),
                        "stored_ttl_sec": raw_item.get("ttl_sec", ""),
                        "stale_recomputed": stale_recomputed,
                        "stale_recompute_error": recompute_error,
                    },
                )
            )
    except Exception as exc:
        checks.append(
            CheckResult(
                "broker_truth.snapshot_missing_or_present",
                "PASS",
                "snapshot file exists",
                {"path": str(snapshot_path)},
            )
        )
        checks.append(
            CheckResult(
                "broker_truth.snapshot_file_valid",
                "FAIL",
                f"broker truth snapshot JSON error: {exc}",
                {"path": str(snapshot_path)},
            )
        )
    return checks


def _attach_exposure_evidence(
    item: dict[str, Any],
    exposure_by_path: dict[str, dict[str, Any]],
    broker_snapshot: dict[str, Any],
) -> None:
    _shared_attach_order_unknown_exposure_evidence(item, exposure_by_path, broker_snapshot)


def _pathb_recoverable_still_held(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or "").upper()
    if status != "ORDER_UNKNOWN":
        return False
    if bool(item.get("broker_truth_unavailable")):
        return False
    local_qty = _safe_int(item.get("local_position_qty"))
    broker_qty = _safe_int(item.get("broker_position_qty"))
    if local_qty <= 0 or broker_qty <= 0 or local_qty != broker_qty:
        return False
    if bool(item.get("broker_any_open_order_evidence", item.get("broker_open_order_evidence"))) or bool(
        item.get("broker_any_fill_evidence", item.get("broker_sell_fill_evidence"))
    ):
        return False
    local_sell_order_id = str(item.get("local_sell_order_id") or item.get("local_pending_sell_order_id") or "").strip()
    return bool(local_sell_order_id)


def _pathb_recoverable_entry_holding(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or "").upper()
    if status not in {"ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED"}:
        return False
    if bool(item.get("broker_truth_unavailable")):
        return False
    local_qty = _safe_int(item.get("local_position_qty"))
    broker_qty = _safe_int(item.get("broker_position_qty"))
    return bool(local_qty > 0 and broker_qty > 0 and local_qty == broker_qty)


def _order_unknown_remediation_command(mode: str, market: str, session_before: str) -> str:
    market_arg = str(market or "").upper() or "KR"
    return (
        "python tools/order_unknown_remediation.py "
        f"--mode {mode} --market {market_arg} --session-before {session_before} --dry-run --json"
    )


def _order_unknown_remediation_blockers(
    item: dict[str, Any],
    *,
    previous_session: bool,
) -> list[str]:
    blockers: list[str] = []
    if str(item.get("status") or "").upper() != "ORDER_UNKNOWN":
        blockers.append("status_not_order_unknown")
    if str(item.get("path_type") or "") != "claude_price":
        blockers.append("not_pathb_claude_price")
    if not previous_session:
        blockers.append("current_session_order_unknown")
    if bool(item.get("broker_truth_unavailable")):
        blockers.append("broker_truth_unavailable")
    if not str(item.get("broker_truth_last_success_at") or "").strip():
        blockers.append("broker_truth_timestamp_missing")
    if bool(item.get("local_exposure")) or _safe_int(item.get("local_position_qty")) > 0:
        blockers.append("local_exposure_present")
    if str(item.get("local_pending_sell_order_id") or item.get("local_sell_order_id") or "").strip():
        blockers.append("local_sell_order_present")
    if _safe_int(item.get("broker_position_qty")) > 0:
        blockers.append("broker_position_present")
    if bool(item.get("broker_any_open_order_evidence", item.get("broker_open_order_evidence"))):
        blockers.append("broker_open_order_present")
    if bool(item.get("broker_any_fill_evidence", item.get("broker_sell_fill_evidence"))):
        blockers.append("broker_fill_present")
    if not str(item.get("path_run_id") or "").strip():
        blockers.append("path_run_id_missing")
    return blockers


def _mark_order_unknown_remediation_hint(
    item: dict[str, Any],
    *,
    mode: str,
    previous_session: bool,
    session_before: str,
) -> dict[str, Any]:
    return _shared_mark_order_unknown_remediation_hint(
        item,
        mode=mode,
        previous_session=previous_session,
        session_before=session_before,
    )


def _broker_rows_for_ticker(rows: list[Any], market: str, ticker: str) -> list[dict[str, Any]]:
    key = _ticker_key(market, ticker)
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if _ticker_key(market, row.get("ticker")) == key:
            out.append(row)
    return out


def _broker_order_id(row: dict[str, Any]) -> str:
    return str(row.get("order_no") or row.get("order_id") or row.get("execution_id") or "").strip()


def _row_side(row: dict[str, Any]) -> str:
    return str(row.get("side") or row.get("order_side") or "").strip().lower()


def _pathb_broker_truth_conflicts(
    mode: str,
    runs_by_id: dict[str, dict[str, Any]],
    *,
    broker_snapshot: dict[str, Any] | None = None,
    exposure_by_path: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if broker_snapshot is None:
        try:
            broker_snapshot = _load_broker_truth_snapshot_for_db(mode)
        except Exception:
            broker_snapshot = {}
    if exposure_by_path is None:
        exposure_by_path, _by_ticker = _pathb_local_exposure_index(mode)
    markets = broker_snapshot.get("markets") if isinstance(broker_snapshot, dict) else {}
    conflicts: list[dict[str, Any]] = []
    for path_run_id, exposure in sorted((exposure_by_path or {}).items()):
        run = runs_by_id.get(path_run_id)
        if not run:
            continue
        status = str(run.get("status") or "")
        if status not in PATHB_ACTIVE_ISH_STATUSES:
            continue
        market = str(run.get("market") or exposure.get("market") or "").upper()
        ticker = _ticker_key(market, run.get("ticker") or exposure.get("ticker"))
        market_data = markets.get(market) if isinstance(markets, dict) else {}
        if not isinstance(market_data, dict) or bool(market_data.get("missing")) or bool(market_data.get("stale")) or str(market_data.get("error") or ""):
            continue
        positions = _broker_rows_for_ticker(list(market_data.get("positions") or []), market, ticker)
        open_orders = _broker_rows_for_ticker(list(market_data.get("open_orders") or []), market, ticker)
        fills = _broker_rows_for_ticker(list(market_data.get("today_fills") or []), market, ticker)
        broker_qty = sum(_safe_int(row.get("qty")) for row in positions)
        local_qty = _safe_int(exposure.get("qty"))
        local_sell_order_id = str(exposure.get("local_sell_order_id") or "").strip()
        open_sell = [
            row for row in open_orders
            if (not local_sell_order_id or _broker_order_id(row) == local_sell_order_id)
            and (not _row_side(row) or _row_side(row) == "sell")
            and _safe_int(row.get("remaining_qty", row.get("order_qty", row.get("qty", 0)))) > 0
        ]
        sell_fills = [
            row for row in fills
            if (not local_sell_order_id or _broker_order_id(row) == local_sell_order_id)
            and (not _row_side(row) or _row_side(row) == "sell")
            and _safe_int(row.get("filled_qty", row.get("qty", 0))) > 0
        ]
        reasons: list[str] = []
        if local_sell_order_id and not open_sell and not sell_fills:
            reasons.append("local_sell_order_missing_from_broker_truth")
        if local_qty > 0 and broker_qty > 0 and local_qty != broker_qty:
            reasons.append("local_broker_qty_mismatch")
        if broker_qty > 0 and status in {"ORDER_UNKNOWN", "SELL_SENT", "SELL_ACKED", "SELL_PARTIAL_FILLED"} and not open_sell and not sell_fills:
            reasons.append("broker_position_without_sell_order_or_fill_evidence")
        if not reasons:
            continue
        plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
        recoverable_still_held = bool(
            status == "ORDER_UNKNOWN"
            and local_sell_order_id
            and local_qty > 0
            and broker_qty > 0
            and local_qty == broker_qty
            and not open_sell
            and not sell_fills
        )
        conflicts.append(
            {
                "market": market,
                "ticker": ticker,
                "path_run_id": path_run_id,
                "status": status,
                "session_date": str(run.get("session_date") or ""),
                "local_qty": local_qty,
                "broker_qty": broker_qty,
                "local_sell_order_id": local_sell_order_id,
                "broker_open_order_evidence": bool(open_sell),
                "broker_sell_fill_evidence": bool(sell_fills),
                "session_end_unresolved": bool(plan.get("session_end_unresolved")),
                "manual_reconciliation_required": not recoverable_still_held,
                "pathb_recoverable_still_held": recoverable_still_held,
                "suggested_action": (
                    "recover_still_held"
                    if recoverable_still_held
                    else "close_path_run"
                    if sell_fills
                    else "restore_pending_sell"
                    if open_sell
                    else "manual_review"
                ),
                "do_not_start": not recoverable_still_held,
                "reasons": reasons,
                "broker_truth_last_success_at": str(market_data.get("last_success_at") or ""),
            }
        )
    return conflicts


def _pathb_broker_conflict_remediation_tool(
    broker_conflict_blockers: list[dict[str, Any]],
    broker_conflicts: list[dict[str, Any]],
) -> str:
    conflict = (broker_conflict_blockers or broker_conflicts or [{}])[0]
    market = str(conflict.get("market") or "").upper()
    ticker = str(conflict.get("ticker") or "").strip()
    path_run_id = str(conflict.get("path_run_id") or "").strip()
    parts = ["python", "-m", "tools.reconcile_live_truth"]
    if market:
        parts.extend(["--market", market])
    if ticker:
        parts.extend(["--ticker", ticker])
    if path_run_id:
        parts.extend(["--path-run-id", path_run_id])
    parts.append("--dry-run")
    return subprocess.list2cmdline(parts)


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


def _warning_meta(kind: str, *, accepted: bool, action: str = "none", blocked_if_live_start: bool = False) -> dict[str, Any]:
    return {
        "warning_kind": kind,
        "accepted_exception": bool(accepted),
        "operator_action_required": bool(action and action != "none" and not accepted),
        "operator_action": action or "none",
        "blocked_if_live_start": bool(blocked_if_live_start),
    }


def _now_kst() -> datetime:
    return datetime.now(KST) if KST is not None else datetime.now()


def _read_env(path: Path) -> dict[str, str]:
    if dotenv_values is not None:
        raw = dotenv_values(path)
        return {str(k): str(v) for k, v in raw.items() if k and v is not None}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _duplicate_env_keys(path: Path) -> dict[str, list[int]]:
    seen: dict[str, list[int]] = {}
    pattern = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
    for idx, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        match = pattern.match(line)
        if match:
            seen.setdefault(match.group(1), []).append(idx)
    return {key: lines for key, lines in seen.items() if len(lines) > 1}


def _load_start_config(base_env: dict[str, str]) -> tuple[Path, dict[str, Any]]:
    raw = str(base_env.get("V2_START_CONFIG_PATH") or "config/v2_start_config.json")
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        return path, {}
    return path, json.loads(path.read_text(encoding="utf-8"))


def _norm_config_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def load_effective_config(mode: str) -> dict[str, Any]:
    env_path = ROOT / f".env.{mode}"
    if not env_path.exists():
        env_path = ROOT / ".env"
    base_env = _read_env(env_path) if env_path.exists() else {}
    start_path, start_config = _load_start_config(base_env)
    overrides: dict[str, str] = {}
    disabled = str(base_env.get("V2_START_CONFIG_DISABLED", "")).strip().lower() in {"1", "true", "yes", "y", "on"}
    if mode == "live" and not disabled:
        raw_overrides = start_config.get("env_overrides") or {}
        if isinstance(raw_overrides, dict):
            overrides = {
                str(k): str(v).lower() if isinstance(v, bool) else str(v)
                for k, v in raw_overrides.items()
                if v is not None
            }
    effective = dict(base_env)
    effective.update(overrides)
    return {
        "env_path": str(env_path),
        "start_config_path": str(start_path),
        "start_config_loaded": bool(start_config),
        "start_config_disabled": disabled,
        "base_env": base_env,
        "overrides": overrides,
        "effective": effective,
        "start_config": start_config,
    }


def _latest_runtime_config_snapshot(mode: str) -> tuple[Path | None, dict[str, Any]]:
    try:
        config_dir = get_runtime_path("logs", "config", "_probe", make_parents=False).parent
    except Exception:
        config_dir = ROOT / "logs" / "config"
    if not config_dir.exists():
        return None, {}
    pattern = f"effective_config_*_{mode}.redacted.json"
    candidates = sorted(
        config_dir.glob(pattern),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    for path in candidates:
        try:
            return path, json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None, {}


def _runtime_config_drift_payload(config: dict[str, Any], snapshot_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    effective = dict(config.get("effective") or {})
    runtime_effective = dict((snapshot_payload or {}).get("effective") or {})
    drift: dict[str, dict[str, Any]] = {}
    for key in sorted(RUNTIME_CONFIG_DRIFT_KEYS):
        if key not in effective and key not in runtime_effective:
            continue
        file_value = _norm_config_value(effective.get(key, ""))
        runtime_value = _norm_config_value(runtime_effective.get(key, ""))
        if runtime_value == "***" or "***" in runtime_value:
            continue
        if file_value != runtime_value:
            drift[key] = {"file_effective": file_value, "runtime_snapshot": runtime_value}
    return drift


def _parse_iso_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None and KST is not None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed


def _runtime_pid_state(mode: str) -> dict[str, Any]:
    path = get_runtime_path("state", f"{mode}_trading_bot.pid", make_parents=False)
    if not path.exists():
        return {"pid_path": str(path), "pid": 0, "pid_started_at": "", "pid_alive": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception as exc:
        return {"pid_path": str(path), "pid": 0, "pid_started_at": "", "pid_alive": False, "pid_error": str(exc)}
    pid = _int_value(data.get("pid"), 0)
    return {
        "pid_path": str(path),
        "pid": pid,
        "pid_started_at": str(data.get("started_at") or ""),
        "pid_alive": _pid_alive(pid),
    }


def _runtime_config_drift_check(config: dict[str, Any], mode: str) -> CheckResult:
    path, payload = _latest_runtime_config_snapshot(mode)
    pid_state = _runtime_pid_state(mode)
    if path is None:
        return CheckResult(
            "config.runtime_snapshot_drift",
            "WARN",
            "no runtime effective_config snapshot found",
            {"mode": mode, **pid_state, "operator_action": "wait for the bot to write a runtime config snapshot or restart after config changes"},
        )
    drift = _runtime_config_drift_payload(config, payload)
    critical_drift = {
        key: value for key, value in drift.items() if key in CRITICAL_RUNTIME_CONFIG_DRIFT_KEYS
    }
    if critical_drift:
        status = "FAIL"
        detail = "critical runtime config snapshot differs from files"
        operator_action = "restart or reload the live bot so runtime effective config matches files"
    elif drift:
        status = "WARN"
        detail = "latest runtime config snapshot differs from files"
        operator_action = "review drift; restart/reload if the difference is intentional and should be live"
    else:
        status = "PASS"
        detail = "runtime snapshot matches file effective config"
        operator_action = "none"
    written_dt = _parse_iso_datetime(payload.get("written_at", ""))
    pid_started_dt = _parse_iso_datetime(pid_state.get("pid_started_at", ""))
    snapshot_after_pid_start: bool | None = None
    snapshot_fresh_for_process: bool | None = None
    if written_dt is not None and pid_started_dt is not None:
        if written_dt.tzinfo is not None and pid_started_dt.tzinfo is not None:
            pid_started_dt = pid_started_dt.astimezone(written_dt.tzinfo)
        # EffectiveRuntimeConfig snapshots are written with second precision while
        # pid files keep fractional seconds. Treat same-second startup snapshots
        # as fresh instead of reporting a false post-restart drift warning.
        snapshot_after_pid_start = written_dt >= (pid_started_dt - timedelta(seconds=1))
        snapshot_fresh_for_process = bool(snapshot_after_pid_start)
        if not snapshot_fresh_for_process and status == "PASS":
            status = "WARN"
            detail = "runtime config snapshot predates current pid"
            operator_action = "restart/reload the bot or wait until it writes a fresh effective config snapshot"
    elif status == "PASS" and str(mode or "").lower() == "live" and bool(pid_state.get("pid_alive")):
        status = "WARN"
        detail = "runtime config snapshot freshness unverifiable"
        operator_action = "verify pid and snapshot timestamps or wait until the live bot writes a fresh effective config snapshot"
    return CheckResult(
        "config.runtime_snapshot_drift",
        status,
        detail,
        {
            "snapshot_path": str(path),
            "written_at": payload.get("written_at", ""),
            "runtime_mode": payload.get("runtime_mode", ""),
            "drift": drift,
            "critical_drift": critical_drift,
            "critical_drift_keys": sorted(critical_drift),
            "operator_action": operator_action,
            **pid_state,
            "snapshot_after_pid_start": snapshot_after_pid_start,
            "snapshot_fresh_for_process": snapshot_fresh_for_process,
        },
    )


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value or "").replace(",", ""))
    except Exception:
        return default


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "")))
    except Exception:
        return default


def _kr_cap40_confirmation_enforce_check(effective: dict[str, str]) -> CheckResult:
    cap = _int_value(effective.get("KR_DAILY_ENTRY_CAP"), 0)
    enabled = _truthy(effective.get("KR_CONFIRMATION_GATE_ENABLED"))
    shadow = _truthy(effective.get("KR_CONFIRMATION_GATE_SHADOW"))
    mode = str(effective.get("KR_CONFIRMATION_GATE_MODE") or "").strip().upper()
    data = {
        "KR_DAILY_ENTRY_CAP": cap,
        "KR_CONFIRMATION_GATE_ENABLED": enabled,
        "KR_CONFIRMATION_GATE_SHADOW": shadow,
        "KR_CONFIRMATION_GATE_MODE": mode,
        "required_mode": "FAST_TRIGGER_WITH_HARD_VETO",
    }
    if cap >= 40 and (not enabled or shadow or mode != "FAST_TRIGGER_WITH_HARD_VETO"):
        data["remediation_required"] = True
        data["operator_action"] = (
            "set KR_CONFIRMATION_GATE_ENABLED=true, "
            "KR_CONFIRMATION_GATE_SHADOW=false, "
            "KR_CONFIRMATION_GATE_MODE=FAST_TRIGGER_WITH_HARD_VETO before operating KR cap 40"
        )
        return CheckResult(
            "config.kr_cap40_confirmation_enforce",
            "FAIL",
            "KR cap 40 requires confirmation enforce mode",
            data,
        )
    return CheckResult(
        "config.kr_cap40_confirmation_enforce",
        "PASS",
        "KR cap 40 confirmation enforce is valid",
        data,
    )


def _pathb_market_live_gate_check(effective: dict[str, Any]) -> CheckResult:
    def gate_detail(market: str) -> dict[str, Any]:
        market_key = str(market or "").upper()
        primary = f"PATHB_{market_key}_LIVE_ENABLED"
        legacy = f"{market_key}_CLAUDE_PRICE_LIVE_ENABLED"
        if primary in effective:
            return {
                "effective": _truthy(effective.get(primary)),
                "source_key": primary,
                "source_value": str(effective.get(primary, "")),
                "legacy_key": legacy,
                "legacy_value": str(effective.get(legacy, "")),
                "legacy_shadowed": legacy in effective,
            }
        source_value = str(effective.get(legacy, "true"))
        return {
            "effective": _truthy(source_value),
            "source_key": legacy,
            "source_value": source_value,
            "legacy_key": legacy,
            "legacy_value": str(effective.get(legacy, "")),
            "legacy_shadowed": False,
        }

    gate_source = {market: gate_detail(market) for market in ("KR", "US")}
    pathb_market_gates = {
        market: str(gate_source[market].get("source_value", ""))
        for market in ("KR", "US")
    }
    # 2026-06-11 운영자 결정: KR PathB 재개 (신규 게이트 체계 하 재검증) — KR-on/US-on 복원.
    violations: list[str] = []
    kr_live = bool(gate_source["KR"].get("effective"))
    us_live = bool(gate_source["US"].get("effective"))
    if not kr_live:
        violations.append("KR Path B live must be enabled")
    if not us_live:
        violations.append("US Path B live must be enabled")
    policy_match = not violations
    if violations:
        status = "WARN"
        detail = "Path B market live gates violate KR-on/US-on policy: " + "; ".join(violations)
    else:
        status = "PASS"
        detail = "Path B market live gates match KR-on/US-on policy"
    return CheckResult(
        "config.pathb_market_live_gates",
        status,
        detail,
        {
            "values": pathb_market_gates,
            "policy": "KR-on/US-on",
            "policy_match": policy_match,
            "violations": violations,
            "remediation_required": bool(violations),
            "operator_action": "set PATHB_KR_LIVE_ENABLED=true and PATHB_US_LIVE_ENABLED=true",
            "market_live_gate_source": gate_source,
        },
    )


def _candidate_actions_live_config_check(effective: dict[str, str], mode: str) -> CheckResult:
    keys = (
        "ENABLE_CLAUDE_CANDIDATE_ACTIONS",
        "ENABLE_ACTION_ROUTING",
        "CANDIDATE_ACTIONS_V2_ENABLED",
    )
    values = {key: str(effective.get(key, "")) for key in keys}
    disabled = [key for key in keys if not _truthy(effective.get(key))]
    legacy_auto_ready = _truthy(effective.get("ALLOW_LEGACY_SELECTION_AUTO_READY"))
    data = {
        "values": values,
        "disabled": disabled,
        "allow_legacy_auto_ready": legacy_auto_ready,
        "mode": mode,
    }
    if str(mode or "").lower() == "live" and disabled:
        return CheckResult(
            "config.candidate_actions_live_contract",
            "FAIL",
            "live candidate action routing must be enabled; legacy watchlist auto-ready is blocked",
            data,
        )
    return CheckResult(
        "config.candidate_actions_live_contract",
        "PASS",
        "candidate action routing config checked",
        data,
    )


CONFIG_SOURCE_MEANING_KEYS: dict[str, dict[str, str]] = {
    "MAX_DAILY_LOSS_PCT": {
        "used_by": "legacy/global daily loss guard if referenced by runtime path",
        "meaning": "legacy daily loss threshold; compare with DAILY_LOSS_LIMIT_PCT before live operations",
    },
    "DAILY_LOSS_LIMIT_PCT": {
        "used_by": "Path A/Path B realized daily loss gate",
        "meaning": "canonical daily loss halt/block threshold",
    },
    "MAX_POSITIONS": {
        "used_by": "legacy/global position cap if referenced by runtime path",
        "meaning": "legacy global position limit; market/path caps may be more specific",
    },
    "KR_MAX_POSITIONS": {
        "used_by": "KR market position cap",
        "meaning": "KR market-scoped maximum positions",
    },
    "US_MAX_POSITIONS": {
        "used_by": "US market position cap",
        "meaning": "US market-scoped maximum positions",
    },
    "PATHB_MAX_POSITIONS": {
        "used_by": "PathB entry guard",
        "meaning": "PathB-specific maximum active positions",
    },
    "PATHB_PREOPEN_EXIT_POLICY_MODE": {
        "used_by": "US fallback for PathB preopen shallow-stop defer policy",
        "meaning": "legacy/global policy mode; KR does not inherit this value",
    },
    "US_PATHB_PREOPEN_EXIT_POLICY_MODE": {
        "used_by": "US PathB preopen shallow-stop defer policy",
        "meaning": "market-scoped US mode; overrides PATHB_PREOPEN_EXIT_POLICY_MODE",
    },
    "KR_PATHB_PREOPEN_EXIT_POLICY_MODE": {
        "used_by": "KR PathB preopen shallow-stop defer policy",
        "meaning": "market-scoped KR mode; current live policy is operator-approved enforce",
    },
    "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US": {
        "used_by": "preflight expectation for US PathB preopen exit policy",
        "meaning": "operator-approved expected US mode used by preflight only",
    },
    "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR": {
        "used_by": "preflight expectation for KR PathB preopen exit policy",
        "meaning": "operator-approved expected KR mode used by preflight only",
    },
    "PATHB_SELECTION_RECONCILE_ENABLED": {
        "used_by": "PathB selection-plan reconciliation",
        "meaning": "enables Fresh selection based WAITING/HIT plan reconciliation",
    },
    "US_PATHB_SELECTION_RECONCILE_MODE": {
        "used_by": "US PathB selection-plan reconciliation",
        "meaning": "market-scoped US mode; enforce can cancel stale WAITING plans",
    },
    "KR_PATHB_SELECTION_RECONCILE_MODE": {
        "used_by": "KR PathB selection-plan reconciliation",
        "meaning": "market-scoped KR mode; enforce can cancel stale WAITING plans when configured",
    },
    "PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER": {
        "used_by": "PathB Claude price plan parsing",
        "meaning": "global fallback multiplier for default cancel_if_open_above when Claude omits it",
    },
    "US_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER": {
        "used_by": "US PathB Claude price plan parsing",
        "meaning": "market-scoped US multiplier for default cancel_if_open_above",
    },
    "KR_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER": {
        "used_by": "KR PathB Claude price plan parsing",
        "meaning": "market-scoped KR multiplier for default cancel_if_open_above",
    },
    "PATHB_SELECTION_RECONCILE_ZONE_UPDATE_ENABLED": {
        "used_by": "PathB active WAITING plan zone-only update",
        "meaning": "enables Fresh selection zone patching when active WAITING plan blocks new plan registration",
    },
    "US_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE": {
        "used_by": "US PathB active WAITING plan zone-only update",
        "meaning": "market-scoped US mode; enforce updates buy_zone_low/high and cancel cap only",
    },
    "KR_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE": {
        "used_by": "KR PathB active WAITING plan zone-only update",
        "meaning": "market-scoped KR mode; enforce updates buy_zone_low/high and cancel cap only",
    },
}


def _config_key_source(config: dict[str, Any], key: str) -> dict[str, str]:
    base_env = dict(config.get("base_env") or {})
    overrides = dict(config.get("overrides") or {})
    start_config = dict(config.get("start_config") or {})
    env_path = str(config.get("env_path") or "")
    start_path = str(config.get("start_config_path") or "")
    if key in overrides:
        return {"source": "v2_start_config.env_overrides", "source_path": start_path}
    if key in base_env:
        return {"source": "env_file", "source_path": env_path}
    if key in start_config:
        return {"source": "v2_start_config.top_level", "source_path": start_path}
    return {"source": "missing", "source_path": ""}


def _config_source_meaning_check(config: dict[str, Any]) -> CheckResult:
    effective = dict(config.get("effective") or {})
    rows: dict[str, dict[str, Any]] = {}
    for key, meta in CONFIG_SOURCE_MEANING_KEYS.items():
        source_info = _config_key_source(config, key)
        rows[key] = {
            "effective_value": effective.get(key, ""),
            "source": source_info["source"],
            "source_path": source_info["source_path"],
            "used_by": meta["used_by"],
            "meaning": meta["meaning"],
        }
    return CheckResult(
        "config.source_meaning",
        "PASS",
        "critical config sources and gate meanings captured",
        {"keys": rows, "config_change_allowed": False},
    )


def _pathb_preopen_exit_policy_check(config: dict[str, Any]) -> CheckResult:
    effective = dict(config.get("effective") or {})

    def normalized_mode(raw: Any) -> str:
        mode = str(raw or "").strip().lower()
        return mode if mode in {"off", "shadow", "enforce"} else "off"

    global_mode = normalized_mode(effective.get("PATHB_PREOPEN_EXIT_POLICY_MODE", "off"))
    market_modes: dict[str, str] = {}
    source_by_market: dict[str, dict[str, str]] = {}
    configured_values = {
        "PATHB_PREOPEN_EXIT_POLICY_MODE": str(effective.get("PATHB_PREOPEN_EXIT_POLICY_MODE", "")),
        "US_PATHB_PREOPEN_EXIT_POLICY_MODE": str(effective.get("US_PATHB_PREOPEN_EXIT_POLICY_MODE", "")),
        "KR_PATHB_PREOPEN_EXIT_POLICY_MODE": str(effective.get("KR_PATHB_PREOPEN_EXIT_POLICY_MODE", "")),
        "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US": str(effective.get("PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US", "")),
        "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR": str(effective.get("PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR", "")),
    }
    for market in ("US", "KR"):
        market_key = f"{market}_PATHB_PREOPEN_EXIT_POLICY_MODE"
        if str(effective.get(market_key, "")).strip():
            market_modes[market] = normalized_mode(effective.get(market_key))
            source_by_market[market] = {"key": market_key, **_config_key_source(config, market_key)}
        elif market == "US":
            market_modes[market] = global_mode
            source_by_market[market] = {
                "key": "PATHB_PREOPEN_EXIT_POLICY_MODE",
                **_config_key_source(config, "PATHB_PREOPEN_EXIT_POLICY_MODE"),
                "fallback": "US only",
            }
        else:
            market_modes[market] = "off"
            source_by_market[market] = {"key": market_key, "source": "default", "source_path": "", "fallback": "KR defaults off"}

    expected = {
        "US": normalized_mode(effective.get("PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US", "enforce")),
        "KR": normalized_mode(effective.get("PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR", "off")),
    }
    violations = [
        f"{market} expected {expected_mode} but effective {market_modes.get(market)}"
        for market, expected_mode in expected.items()
        if market_modes.get(market) != expected_mode
    ]
    status = "WARN" if violations else "PASS"
    detail = (
        "PathB preopen exit policy market modes differ from current policy"
        if violations
        else "PathB preopen exit policy market modes match current policy"
    )
    return CheckResult(
        "config.pathb_preopen_exit_policy",
        status,
        detail,
        {
            "effective_modes": market_modes,
            "configured_values": configured_values,
            "source_by_market": source_by_market,
            "current_policy": expected,
            "expected_policy": expected,
            "expected_source_by_market": {
                "US": {
                    "key": "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US",
                    **_config_key_source(config, "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US"),
                },
                "KR": {
                    "key": "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR",
                    **_config_key_source(config, "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR"),
                },
            },
            "violations": violations,
            "source_of_truth": "config/v2_start_config.json env_overrides for live mode",
            "env_live_note": "do not duplicate market-scoped values in .env.live unless changing the source-of-truth policy",
            "operator_action": "KR preopen defer is explicitly approved; keep market-scoped expected policy aligned",
            "config_change_allowed": False,
        },
    )


def _pathb_selection_reconcile_check(config: dict[str, Any]) -> CheckResult:
    effective = dict(config.get("effective") or {})

    def normalized_mode(raw: Any) -> str:
        mode = str(raw or "").strip().lower()
        return mode if mode in {"off", "shadow", "enforce"} else "invalid"

    enabled = _truthy(effective.get("PATHB_SELECTION_RECONCILE_ENABLED"))
    global_mode = normalized_mode(effective.get("PATHB_SELECTION_RECONCILE_MODE", "shadow"))
    market_modes: dict[str, str] = {}
    source_by_market: dict[str, dict[str, str]] = {}
    for market in ("US", "KR"):
        market_key = f"{market}_PATHB_SELECTION_RECONCILE_MODE"
        if str(effective.get(market_key, "")).strip():
            market_modes[market] = normalized_mode(effective.get(market_key))
            source_by_market[market] = {"key": market_key, **_config_key_source(config, market_key)}
        else:
            market_modes[market] = global_mode
            source_by_market[market] = {
                "key": "PATHB_SELECTION_RECONCILE_MODE",
                **_config_key_source(config, "PATHB_SELECTION_RECONCILE_MODE"),
                "fallback": "global",
            }

    zone_update_enabled = _truthy(effective.get("PATHB_SELECTION_RECONCILE_ZONE_UPDATE_ENABLED"))
    zone_global_mode = normalized_mode(effective.get("PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE", "enforce"))
    zone_update_modes: dict[str, str] = {}
    zone_source_by_market: dict[str, dict[str, str]] = {}
    cancel_above_multipliers: dict[str, float] = {}
    global_multiplier = _float_value(effective.get("PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER", "1.05"), 1.05)
    for market in ("US", "KR"):
        zone_mode_key = f"{market}_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE"
        if str(effective.get(zone_mode_key, "")).strip():
            zone_update_modes[market] = normalized_mode(effective.get(zone_mode_key))
            zone_source_by_market[market] = {"key": zone_mode_key, **_config_key_source(config, zone_mode_key)}
        else:
            zone_update_modes[market] = zone_global_mode
            zone_source_by_market[market] = {
                "key": "PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE",
                **_config_key_source(config, "PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE"),
                "fallback": "global",
            }
        multiplier_key = f"{market}_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER"
        cancel_above_multipliers[market] = _float_value(
            effective.get(multiplier_key, effective.get("PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER", global_multiplier)),
            global_multiplier,
        )

    risky_flags = {
        "PATHB_SELECTION_RECONCILE_UPDATE_VALID_TARGETS": _truthy(effective.get("PATHB_SELECTION_RECONCILE_UPDATE_VALID_TARGETS")),
        "PATHB_SELECTION_RECONCILE_HIT_SUSPEND_CANCEL": _truthy(effective.get("PATHB_SELECTION_RECONCILE_HIT_SUSPEND_CANCEL")),
        "PATHB_RECONCILE_FORCE_FRESH_AFTER_CANCEL": _truthy(effective.get("PATHB_RECONCILE_FORCE_FRESH_AFTER_CANCEL")),
    }
    invalid_modes = {
        market: mode
        for market, mode in market_modes.items()
        if mode == "invalid"
    }
    warnings: list[str] = []
    if not enabled:
        warnings.append("PATHB_SELECTION_RECONCILE_ENABLED is false")
    if not zone_update_enabled:
        warnings.append("PATHB_SELECTION_RECONCILE_ZONE_UPDATE_ENABLED is false")
    for key, value in risky_flags.items():
        if value:
            warnings.append(f"{key} is true")
    for market, mode in invalid_modes.items():
        warnings.append(f"{market} mode is invalid: {mode}")
    for market, mode in zone_update_modes.items():
        if mode != "enforce":
            warnings.append(f"{market} zone update mode is {mode}, expected enforce")
    for market, multiplier in cancel_above_multipliers.items():
        if multiplier <= 0:
            warnings.append(f"{market} cancel-above multiplier is nonpositive")

    status = "WARN" if warnings else "PASS"
    return CheckResult(
        "config.pathb_selection_reconcile",
        status,
        "PathB selection reconcile config checked",
        {
            "enabled": enabled,
            "effective_modes": market_modes,
            "configured_values": {
                "PATHB_SELECTION_RECONCILE_ENABLED": str(effective.get("PATHB_SELECTION_RECONCILE_ENABLED", "")),
                "PATHB_SELECTION_RECONCILE_MODE": str(effective.get("PATHB_SELECTION_RECONCILE_MODE", "")),
                "US_PATHB_SELECTION_RECONCILE_MODE": str(effective.get("US_PATHB_SELECTION_RECONCILE_MODE", "")),
                "KR_PATHB_SELECTION_RECONCILE_MODE": str(effective.get("KR_PATHB_SELECTION_RECONCILE_MODE", "")),
                "PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER": str(effective.get("PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER", "")),
                "US_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER": str(effective.get("US_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER", "")),
                "KR_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER": str(effective.get("KR_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER", "")),
                "PATHB_SELECTION_RECONCILE_ZONE_UPDATE_ENABLED": str(effective.get("PATHB_SELECTION_RECONCILE_ZONE_UPDATE_ENABLED", "")),
                "PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE": str(effective.get("PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE", "")),
                "US_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE": str(effective.get("US_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE", "")),
                "KR_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE": str(effective.get("KR_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE", "")),
                "PATHB_SELECTION_RECONCILE_CANCEL_INVALID": str(effective.get("PATHB_SELECTION_RECONCILE_CANCEL_INVALID", "")),
                "PATHB_SELECTION_RECONCILE_CANCEL_SUSPENDED": str(effective.get("PATHB_SELECTION_RECONCILE_CANCEL_SUSPENDED", "")),
                "PATHB_SELECTION_RECONCILE_UPDATE_VALID_TARGETS": str(effective.get("PATHB_SELECTION_RECONCILE_UPDATE_VALID_TARGETS", "")),
                "PATHB_SELECTION_RECONCILE_HIT_SUSPEND_CANCEL": str(effective.get("PATHB_SELECTION_RECONCILE_HIT_SUSPEND_CANCEL", "")),
                "PATHB_RECONCILE_FORCE_FRESH_AFTER_CANCEL": str(effective.get("PATHB_RECONCILE_FORCE_FRESH_AFTER_CANCEL", "")),
            },
            "source_by_market": source_by_market,
            "zone_update": {
                "enabled": zone_update_enabled,
                "effective_modes": zone_update_modes,
                "source_by_market": zone_source_by_market,
                "cancel_above_multipliers": cancel_above_multipliers,
            },
            "risky_flags": risky_flags,
            "warnings": warnings,
            "operator_action": "review risky flags before enabling target updates, HIT cancel, or force-fresh behavior",
            "config_change_allowed": True,
        },
    )


def _kr_live_expansion_guard_check(effective: dict[str, str]) -> CheckResult:
    strategy_flags = {
        "KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED": _truthy(effective.get("KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED")),
        "KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED": _truthy(effective.get("KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED")),
        "KR_PLAN_A_ORP_SIGNAL_ENABLED": _truthy(effective.get("KR_PLAN_A_ORP_SIGNAL_ENABLED")),
    }
    enabled = [key for key, value in strategy_flags.items() if value]
    status = "WARN" if enabled else "PASS"
    detail = (
        f"KR Plan A live expansion flags enabled: {', '.join(enabled)}"
        if enabled
        else "KR Plan A live expansion flags remain off; shadow/probe evidence required before expansion"
    )
    return CheckResult(
        "kr.live_expansion_guard",
        status,
        detail,
        {
            "strategy_live_flags": strategy_flags,
            "enabled_strategy_flags": enabled,
            "pathb_kr_live_enabled": _truthy(effective.get("PATHB_KR_LIVE_ENABLED")),
            "kr_claude_price_new_entry_block": _truthy(effective.get("KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK")),
            "minimum_shadow_or_probe": {"fills": 30, "calendar_weeks": 4},
            "live_expansion_allowed": False,
            "config_change_allowed": False,
        },
    )


def _repo_text(*parts: str) -> str:
    path = ROOT.joinpath(*parts)
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _session_date_guess(market: str) -> str:
    """Preflight session date using the same boundary rules as TradingBot."""
    now = _now_kst()
    return resolve_session_date(market, now).isoformat()


def _market_session_calendar_check() -> CheckResult:
    now_kst = _now_kst()
    weekday = now_kst.weekday()
    weekend = weekday >= 5
    data: dict[str, Any] = {
        "now_kst": now_kst.isoformat(timespec="seconds"),
        "weekday": weekday,
        "weekend": weekend,
        "KR_session_date_guess": _session_date_guess("KR"),
        "US_session_date_guess": _session_date_guess("US"),
    }
    try:
        import exchange_calendars as ec

        exchange_by_market = {"KR": "XKRX", "US": "XNYS"}
        sessions: dict[str, dict[str, Any]] = {}
        non_sessions: list[str] = []
        for market, exchange in exchange_by_market.items():
            session_date = str(data[f"{market}_session_date_guess"])
            calendar = ec.get_calendar(exchange)
            raw_is_session = bool(calendar.is_session(session_date))
            known_holiday = is_known_market_holiday(market, session_date)
            is_session = bool(raw_is_session and not known_holiday)
            item: dict[str, Any] = {
                "exchange": exchange,
                "session_date": session_date,
                "raw_is_session": raw_is_session,
                "known_holiday_override": known_holiday,
                "is_session": is_session,
            }
            if is_session:
                item["open_utc"] = str(calendar.session_open(session_date))
                item["close_utc"] = str(calendar.session_close(session_date))
            else:
                non_sessions.append(market)
            sessions[market] = item
        data["sessions"] = sessions
        data["calendar_source"] = "exchange_calendars"
        data["accepted_exception"] = bool(non_sessions and weekend)
        data["remediation_required"] = bool(non_sessions and not weekend)
        data["operator_action"] = (
            "verify holiday and early-close calendar before operation"
            if non_sessions and not weekend
            else "none"
        )
        if non_sessions:
            return CheckResult(
                "market.session_calendar",
                "WARN",
                "exchange calendars report non-trading session for: " + ",".join(non_sessions),
                data,
            )
        return CheckResult(
            "market.session_calendar",
            "PASS",
            "exchange calendars verified KR/US session dates",
            data,
        )
    except Exception as exc:
        data["calendar_source"] = "unavailable"
        data["error"] = str(exc)
        data["accepted_exception"] = weekend
        data["remediation_required"] = not weekend
        data["operator_action"] = (
            "none; weekend/non-trading-day warning is expected"
            if weekend
            else "verify holiday and early-close calendar before operation"
        )
        return CheckResult(
            "market.session_calendar",
            "WARN",
            (
                "today is a weekend; verify next trading session before operation"
                if weekend
                else "calendar API unavailable; verify holidays/early-close operationally"
            ),
            data,
        )


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if psutil is not None:
        try:
            return bool(psutil.pid_exists(pid))
        except Exception:
            pass
    try:
        if sys.platform.startswith("win"):
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return str(pid) in (result.stdout or "")
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _pid_lock_check(name: str, path: Path, *, expected_mode: str = "") -> CheckResult:
    data: dict[str, Any] = {"path": str(path), "category": "runtime_pid_lock"}
    if not path.exists():
        return CheckResult(name, "PASS", "pid lock file is absent", data)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            data["state"] = "invalid_json_root"
            return CheckResult(name, "WARN", "pid lock file root is not an object", data)
    except Exception as exc:
        data["state"] = "unreadable"
        data["error"] = str(exc)
        return CheckResult(name, "WARN", f"pid lock file unreadable: {exc}", data)

    pid = _int_value(raw.get("pid"), 0)
    alive = _pid_alive(pid)
    data.update({"pid": pid, "alive": alive, "state": raw, "auto_fix": not alive})
    if expected_mode:
        actual_mode = str(raw.get("mode", "") or "")
        data["expected_mode"] = expected_mode
        if actual_mode and actual_mode != expected_mode:
            data["mode_mismatch"] = actual_mode
    if alive:
        if expected_mode and data.get("mode_mismatch"):
            data["accepted_exception"] = False
            data["remediation_required"] = True
            data["operator_action"] = "verify the active process before operating or starting another process"
            data["operational_interpretation"] = "active process exists, but lock mode differs from expected mode"
            return CheckResult(name, "WARN", "pid lock is active with a mode mismatch; verify before operation", data)
        data["accepted_exception"] = True
        data["remediation_required"] = False
        data["operator_action"] = "no action while the expected process is intentionally running"
        data["operational_interpretation"] = "expected process appears alive; treat as healthy unless starting a duplicate"
        return CheckResult(name, "WARN", "pid lock is active; expected process appears alive", data)
    return CheckResult(name, "WARN", "stale pid lock is present; guardian may remove it after process check", data)


def _repo_python_processes() -> tuple[list[dict[str, Any]], str, str]:
    known_scripts = (
        "trading_bot.py",
        "run_bot.py",
        "dashboard_server.py",
        "live_guardian.py",
        "preopen_scheduler.py",
        "broker_truth_scheduler.py",
        "run_counterfactual_pipeline.py",
    )
    rows: list[dict[str, Any]] = []
    if psutil is not None:
        try:
            root_text = str(ROOT).lower()
            for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
                try:
                    info = proc.info
                    cmdline = [str(item) for item in (info.get("cmdline") or [])]
                    command = " ".join(cmdline)
                    lowered = command.lower().replace("\\", "/")
                    name = str(info.get("name") or "")
                    is_python = "python" in name.lower() or any("python" in item.lower() for item in cmdline[:2])
                    if not is_python:
                        continue
                    if root_text not in command.lower() and not any(script in lowered for script in known_scripts):
                        continue
                    created_at = ""
                    try:
                        created_at = datetime.fromtimestamp(float(info.get("create_time") or 0), tz=KST).isoformat(timespec="seconds")
                    except Exception:
                        pass
                    rows.append(
                        {
                            "pid": int(info.get("pid") or 0),
                            "name": name,
                            "cmdline": cmdline,
                            "command": command,
                            "created_at": created_at,
                            "role": _classify_repo_process_role(cmdline),
                        }
                    )
                except Exception:
                    continue
            return rows, "psutil", ""
        except Exception as exc:
            return rows, "psutil", str(exc)
    return rows, "unavailable", "psutil is not available"


def _classify_repo_process_role(cmdline: list[str]) -> str:
    command = " ".join(str(item) for item in cmdline).lower().replace("\\", "/")
    if "trading_bot.py" in command:
        if "--live" in command:
            return "live_bot"
        return "paper_bot"
    if "run_bot.py" in command and "paper" in command:
        return "paper_bot"
    if "dashboard/dashboard_server.py" in command or "dashboard_server.py" in command:
        return "dashboard"
    if "tools/live_guardian.py" in command or "live_guardian.py" in command:
        return "guardian"
    if "tools/preopen_scheduler.py" in command or "preopen_scheduler.py" in command:
        return "preopen_scheduler"
    if "tools/broker_truth_scheduler.py" in command or "broker_truth_scheduler.py" in command:
        return "broker_truth_scheduler"
    if "tools/run_counterfactual_pipeline.py" in command or "run_counterfactual_pipeline.py" in command:
        return "counterfactual_pipeline"
    return "repo_python"


def _listening_ports_by_pid(ports: set[int]) -> tuple[dict[int, list[int]], str]:
    out: dict[int, list[int]] = {}
    if psutil is None:
        return out, "psutil is not available"
    try:
        for conn in psutil.net_connections(kind="inet"):
            try:
                port = int(getattr(conn.laddr, "port", 0) or 0)
                pid = int(conn.pid or 0)
            except Exception:
                continue
            if port in ports and pid > 0:
                out.setdefault(pid, []).append(port)
    except Exception as exc:
        return out, str(exc)
    return {pid: sorted(set(values)) for pid, values in out.items()}, ""


def _pid_lock_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "pid": 0, "alive": False, "exists": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception as exc:
        return {"path": str(path), "pid": 0, "alive": False, "exists": True, "error": str(exc)}
    pid = _int_value(data.get("pid"), 0)
    return {"path": str(path), "pid": pid, "alive": _pid_alive(pid), "exists": True, "state": data}


def _classify_live_process_inventory(
    rows: list[dict[str, Any]],
    ports_by_pid: dict[int, list[int]],
    *,
    mode: str,
    bot_lock: dict[str, Any] | None = None,
    dashboard_lock: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    by_role: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_role.setdefault(str(row.get("role") or ""), []).append(row)
    live_bots = by_role.get("live_bot", [])
    paper_bots = by_role.get("paper_bot", [])
    dashboards = by_role.get("dashboard", [])
    mode_key = str(mode or "").lower()
    if mode_key == "live" and len(live_bots) > 1:
        findings.append({"severity": "FAIL", "code": "duplicate_live_bot", "pids": [row.get("pid") for row in live_bots]})
    if mode_key == "paper" and len(paper_bots) > 1:
        findings.append({"severity": "FAIL", "code": "duplicate_paper_bot", "pids": [row.get("pid") for row in paper_bots]})
    if mode_key == "live" and paper_bots:
        findings.append(
            {
                "severity": "WARN",
                "code": "paper_bot_concurrent_with_live",
                "pids": [row.get("pid") for row in paper_bots],
                "accepted_exception": True,
                "operator_action": "confirm paper process is intentional",
            }
        )
    legacy_dashboard_pids = [
        pid
        for pid, ports in ports_by_pid.items()
        if 5001 in ports and any(int(row.get("pid") or 0) == pid for row in dashboards)
    ]
    if legacy_dashboard_pids:
        findings.append(
            {
                "severity": "WARN",
                "code": "legacy_dashboard_port_5001",
                "pids": legacy_dashboard_pids,
                "accepted_exception": True,
                "operator_action": "close legacy dashboard or ignore only if intentionally used",
            }
        )
    bot_lock = bot_lock or {}
    if bot_lock.get("alive") and live_bots:
        lock_pid = int(bot_lock.get("pid") or 0)
        if lock_pid and lock_pid not in {int(row.get("pid") or 0) for row in live_bots}:
            findings.append({"severity": "WARN", "code": "bot_pid_lock_mismatch", "lock_pid": lock_pid, "pids": [row.get("pid") for row in live_bots]})
    dashboard_lock = dashboard_lock or {}
    if dashboard_lock.get("alive") and dashboards:
        lock_pid = int(dashboard_lock.get("pid") or 0)
        if lock_pid and lock_pid not in {int(row.get("pid") or 0) for row in dashboards}:
            findings.append({"severity": "WARN", "code": "dashboard_pid_lock_mismatch", "lock_pid": lock_pid, "pids": [row.get("pid") for row in dashboards]})
    return findings


def _process_inventory_check(mode: str) -> CheckResult:
    rows, source, error = _repo_python_processes()
    ports_by_pid, port_error = _listening_ports_by_pid({5000, 5001})
    bot_lock = _pid_lock_state(get_runtime_path("state", f"{mode}_trading_bot.pid", make_parents=False))
    dashboard_lock = _pid_lock_state(get_runtime_path("state", "dashboard_server.pid", make_parents=False))
    findings = _classify_live_process_inventory(
        rows,
        ports_by_pid,
        mode=mode,
        bot_lock=bot_lock,
        dashboard_lock=dashboard_lock,
    )
    fail = any(str(item.get("severity")) == "FAIL" for item in findings)
    warn = bool(findings) or bool(error and not rows)
    status = "FAIL" if fail else ("WARN" if warn else "PASS")
    detail = (
        f"process inventory findings={len(findings)}"
        if findings
        else "repo process inventory has no duplicate live runtime findings"
    )
    data = {
        "source": source,
        "error": error,
        "port_error": port_error,
        "rows": rows,
        "ports_by_pid": {str(pid): ports for pid, ports in ports_by_pid.items()},
        "findings": findings,
        "bot_lock": bot_lock,
        "dashboard_lock": dashboard_lock,
        "operator_action": "review process inventory findings before interpreting live state" if findings else "none",
    }
    if status == "WARN" and findings and all(bool(item.get("accepted_exception")) for item in findings):
        data.update(_warning_meta("accepted_concurrent_process", accepted=True))
    if status == "WARN" and error and not rows:
        data.update(_warning_meta("process_inventory_unavailable", accepted=False, action="install/enable psutil or inspect processes manually"))
    return CheckResult("runtime.process_inventory", status, detail, data)


def _heartbeat_age_sec(value: Any) -> float | None:
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return None
    now = _now_kst()
    if parsed.tzinfo is not None and now.tzinfo is not None:
        parsed = parsed.astimezone(now.tzinfo)
    try:
        return round((now - parsed).total_seconds(), 2)
    except Exception:
        return None


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _heartbeat_check(name: str, path: Path, *, max_age_sec: int, process: str) -> CheckResult:
    data = {"path": str(path), "process": process, "max_age_sec": int(max_age_sec)}
    if not path.exists():
        data.update(_warning_meta("heartbeat_missing", accepted=False, action=f"start or inspect {process} heartbeat"))
        return CheckResult(name, "WARN", f"{process} heartbeat missing", data)
    payload = _read_json_object(path)
    data.update(payload)
    last_tick = payload.get("last_tick_at") or payload.get("updated_at") or payload.get("last_success_at")
    age = _heartbeat_age_sec(last_tick)
    data["heartbeat_age_sec"] = age
    pid = _int_value(payload.get("pid"), 0)
    data["pid_alive"] = _pid_alive(pid) if pid else False
    last_error = str(payload.get("last_error") or payload.get("error") or "")
    if pid and not data["pid_alive"]:
        data.update(_warning_meta("heartbeat_pid_dead", accepted=False, action=f"restart or inspect {process}"))
        return CheckResult(name, "WARN", f"{process} heartbeat pid is not alive", data)
    if last_error:
        data.update(_warning_meta("heartbeat_error", accepted=False, action=f"inspect {process} last_error"))
        return CheckResult(name, "WARN", f"{process} heartbeat reports last_error", data)
    if age is None:
        data.update(_warning_meta("heartbeat_unparseable", accepted=False, action=f"inspect {process} heartbeat timestamp"))
        return CheckResult(name, "WARN", f"{process} heartbeat timestamp is unavailable", data)
    if age > max_age_sec:
        data.update(_warning_meta("heartbeat_stale", accepted=False, action=f"restart or inspect {process}"))
        return CheckResult(name, "WARN", f"{process} heartbeat stale age_sec={age}", data)
    return CheckResult(name, "PASS", f"{process} heartbeat fresh age_sec={age}", data)


def _heartbeat_checks(mode: str) -> list[CheckResult]:
    runtime_mode = "live" if str(mode or "").lower() == "live" else "paper"
    guardian_name = "live_guardian_heartbeat.json" if runtime_mode == "live" else f"{runtime_mode}_guardian_heartbeat.json"
    preopen_name = "preopen_scheduler_heartbeat.json" if runtime_mode == "live" else f"{runtime_mode}_preopen_scheduler_heartbeat.json"
    broker_truth_name = (
        "broker_truth_scheduler_heartbeat.json"
        if runtime_mode == "live"
        else f"{runtime_mode}_broker_truth_scheduler_heartbeat.json"
    )
    guardian_process = "live_guardian" if runtime_mode == "live" else f"{runtime_mode}_guardian"
    preopen_process = "preopen_scheduler" if runtime_mode == "live" else f"{runtime_mode}_preopen_scheduler"
    broker_truth_process = (
        "broker_truth_scheduler"
        if runtime_mode == "live"
        else f"{runtime_mode}_broker_truth_scheduler"
    )
    return [
        _heartbeat_check(
            f"runtime.{runtime_mode}_guardian_heartbeat",
            get_runtime_path("state", guardian_name, make_parents=False),
            max_age_sec=300,
            process=guardian_process,
        ),
        _heartbeat_check(
            f"runtime.{runtime_mode}_preopen_scheduler_heartbeat",
            get_runtime_path("state", preopen_name, make_parents=False),
            max_age_sec=900,
            process=preopen_process,
        ),
        _heartbeat_check(
            f"runtime.{runtime_mode}_broker_truth_scheduler_heartbeat",
            get_runtime_path("state", broker_truth_name, make_parents=False),
            max_age_sec=300,
            process=broker_truth_process,
        ),
    ]


def _default_kis_base_url(mode: str) -> str:
    return (
        "https://openapivts.koreainvestment.com:29443"
        if str(mode or "").lower() == "paper"
        else "https://openapi.koreainvestment.com:9443"
    )


def _host_port_from_url(raw_url: str, *, default_url: str) -> tuple[str, int, str]:
    raw = str(raw_url or "").strip() or default_url
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    host = str(parsed.hostname or "").strip()
    if not host:
        fallback = urlparse(default_url)
        host = str(fallback.hostname or "").strip()
    port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
    return host, port, raw


def _kis_socket_check(name: str, raw_url: str, *, mode: str, timeout_sec: float) -> CheckResult:
    host, port, normalized_url = _host_port_from_url(raw_url, default_url=_default_kis_base_url(mode))
    data = {
        "url": normalized_url,
        "host": host,
        "port": port,
        "timeout_sec": timeout_sec,
        "python_executable": sys.executable,
        "powershell_check": f"Test-NetConnection {host} -Port {port}",
        "firewall_allow_rule": (
            f'New-NetFirewallRule -DisplayName "Allow KIS API Python {port}" '
            f'-Direction Outbound -Program "{sys.executable}" '
            f"-Action Allow -Protocol TCP -RemotePort {port}"
        ),
    }
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            pass
        return CheckResult(name, "PASS", f"Python can open TCP connection to {host}:{port}", data)
    except Exception as exc:
        data["error"] = f"{type(exc).__name__}: {exc}"
        return CheckResult(name, "FAIL", f"Python cannot open TCP connection to {host}:{port}", data)


def _kis_network_checks(config: dict[str, Any], mode: str) -> list[CheckResult]:
    effective: dict[str, str] = config.get("effective", {})
    timeout_sec = _float_value(effective.get("KIS_NETWORK_CHECK_TIMEOUT_SEC", "3"), 3.0)
    default_url = _default_kis_base_url(mode)
    kr_url = str(effective.get("KIS_BASE_URL") or default_url)
    us_url = str(effective.get("KIS_BASE_URL_US") or kr_url)
    targets = [("KR", kr_url)]
    if us_url != kr_url:
        targets.append(("US", us_url))
    return [
        _kis_socket_check(f"network.kis_rest_python_socket.{market.lower()}", url, mode=mode, timeout_sec=timeout_sec)
        for market, url in targets
    ]


def _config_checks(mode: str, allow_config_conflicts: bool) -> tuple[list[CheckResult], dict[str, Any]]:
    checks: list[CheckResult] = []
    config = load_effective_config(mode)
    env_path = Path(config["env_path"])
    start_config = config["start_config"]
    base_env: dict[str, str] = config["base_env"]
    overrides: dict[str, str] = config["overrides"]
    effective: dict[str, str] = config["effective"]

    if env_path.exists():
        checks.append(CheckResult("config.env_file", "PASS", f"loaded {env_path}"))
    else:
        checks.append(CheckResult("config.env_file", "FAIL", f"missing env file for mode={mode}", {"path": str(env_path)}))

    duplicates = _duplicate_env_keys(env_path) if env_path.exists() else {}
    checks.append(
        CheckResult(
            "config.duplicate_env_keys",
            "FAIL" if duplicates else "PASS",
            "duplicate keys in env file" if duplicates else "no duplicate env keys",
            {"duplicates": duplicates},
        )
    )

    conflicts = {
        key: {"env": base_env[key], "start_config": overrides[key]}
        for key in sorted(set(base_env) & set(overrides) & LIVE_CONFIG_KEYS)
        if str(base_env[key]) != str(overrides[key])
    }
    checks.append(
        CheckResult(
            "config.env_vs_start_config",
            "WARN" if conflicts and allow_config_conflicts else ("FAIL" if conflicts else "PASS"),
            "start config overrides env values" if conflicts else "no live config conflicts",
            {"conflicts": conflicts},
        )
    )

    internal_conflicts = {}
    for key, value in overrides.items():
        if key in start_config and _norm_config_value(start_config.get(key)) != _norm_config_value(value):
            internal_conflicts[key] = {
                "top_level": _norm_config_value(start_config.get(key)),
                "env_overrides": _norm_config_value(value),
            }
    checks.append(
        CheckResult(
            "config.start_config_internal",
            "WARN" if internal_conflicts and allow_config_conflicts else ("FAIL" if internal_conflicts else "PASS"),
            "v2_start_config top-level values differ from env_overrides" if internal_conflicts else "start config is internally consistent",
            {"conflicts": internal_conflicts},
        )
    )

    important = {key: effective.get(key, "") for key in sorted(LIVE_CONFIG_KEYS) if key in effective}
    checks.append(CheckResult("config.effective_values", "PASS", "effective live values captured", {"values": important}))
    checks.append(_config_source_meaning_check(config))
    checks.append(_pathb_preopen_exit_policy_check(config))
    checks.append(_pathb_selection_reconcile_check(config))
    checks.append(_kr_live_expansion_guard_check(effective))
    checks.append(_kr_cap40_confirmation_enforce_check(effective))
    checks.append(_runtime_config_drift_check(config, mode))
    checks.append(_candidate_actions_live_config_check(effective, mode))
    if _truthy(effective.get("PATHB_INTRADAY_ONLY")):
        checks.append(
            CheckResult(
                "config.pathb_intraday_only",
                "WARN",
                "Path B is forced intraday but current live policy allows hold-days",
                {
                    "value": effective.get("PATHB_INTRADAY_ONLY"),
                    "expected": "false",
                    **_warning_meta("policy_mismatch", accepted=False, action="set PATHB_INTRADAY_ONLY=false if hold-days policy is approved"),
                },
            )
        )
    else:
        checks.append(
            CheckResult(
                "config.pathb_intraday_only",
                "PASS",
                "Path B hold-days policy is enabled",
                {"value": effective.get("PATHB_INTRADAY_ONLY"), "expected": "false"},
            )
        )

    checks.append(_pathb_market_live_gate_check(effective))

    enabled_markets = {
        item.strip().upper()
        for item in str(effective.get("ENABLED_MARKETS", "") or "").split(",")
        if item.strip()
    }
    checks.append(
        CheckResult(
            "config.kr_us_enabled",
            "PASS" if {"KR", "US"}.issubset(enabled_markets) else "FAIL",
            f"ENABLED_MARKETS={','.join(sorted(enabled_markets)) or '-'}",
            {"enabled_markets": sorted(enabled_markets)},
        )
    )

    pathb_values = {
        key: effective.get(key, "")
        for key in (
            "PATHB_ENABLED",
            "PATHB_MODE",
            "PATHB_KR_LIVE_ENABLED",
            "PATHB_US_LIVE_ENABLED",
            "PATHB_MAX_POSITIONS",
            "PATHB_MAX_DAILY_ENTRIES",
            "PATHB_INTRADAY_ONLY",
            "PATHB_EMERGENCY_DISABLE",
            "PATHB_FIXED_ORDER_KRW",
            "PATHB_MIN_CONFIDENCE",
        )
    }
    pathb_failures = []
    if not _truthy(effective.get("PATHB_ENABLED")):
        pathb_failures.append("PATHB_ENABLED is not true")
    if _truthy(effective.get("PATHB_EMERGENCY_DISABLE")):
        pathb_failures.append("PATHB_EMERGENCY_DISABLE is true")
    if _int_value(effective.get("PATHB_MAX_POSITIONS")) <= 0:
        pathb_failures.append("PATHB_MAX_POSITIONS <= 0")
    if _int_value(effective.get("PATHB_MAX_DAILY_ENTRIES")) <= 0:
        pathb_failures.append("PATHB_MAX_DAILY_ENTRIES <= 0")
    checks.append(
        CheckResult(
            "config.pathb_limits",
            "FAIL" if pathb_failures else "PASS",
            "; ".join(pathb_failures) if pathb_failures else "Path B live limits are usable",
            {"values": pathb_values},
        )
    )

    us_budget = _float_value(effective.get("US_FIXED_ORDER_KRW"))
    us_min = _float_value(effective.get("US_MIN_ORDER_KRW"))
    fx = _float_value(effective.get("USD_KRW_RATE"))
    fx_failures = []
    if us_budget <= 0:
        fx_failures.append("US_FIXED_ORDER_KRW <= 0")
    if us_min <= 0:
        fx_failures.append("US_MIN_ORDER_KRW <= 0")
    if fx <= 0:
        fx_failures.append("USD_KRW_RATE <= 0")
    checks.append(
        CheckResult(
            "config.us_sizing_fx",
            "FAIL" if fx_failures else "PASS",
            "; ".join(fx_failures) if fx_failures else "US KRW sizing and FX fallback are present",
            {"US_FIXED_ORDER_KRW": us_budget, "US_MIN_ORDER_KRW": us_min, "USD_KRW_RATE": fx},
        )
    )
    primary_tokens = _int_value(effective.get("CLAUDE_SELECTION_MAX_TOKENS"), 0)
    retry_tokens = _int_value(effective.get("CLAUDE_SELECTION_RETRY_MAX_TOKENS"), 0)
    token_failures = []
    if primary_tokens < 3200:
        token_failures.append("CLAUDE_SELECTION_MAX_TOKENS < 3200")
    if retry_tokens < 1800:
        token_failures.append("CLAUDE_SELECTION_RETRY_MAX_TOKENS < 1800")
    checks.append(
        CheckResult(
            "claude.max_tokens_sufficient",
            "FAIL" if token_failures else "PASS",
            "; ".join(token_failures) if token_failures else "Claude selection token limits are sufficient for price_targets",
            {"CLAUDE_SELECTION_MAX_TOKENS": primary_tokens, "CLAUDE_SELECTION_RETRY_MAX_TOKENS": retry_tokens},
        )
    )
    checks.append(
        CheckResult(
            "us.cash_sizing",
            "PASS" if us_budget > 0 and fx > 0 else "FAIL",
            "US KRW budget can be converted to USD sizing" if us_budget > 0 and fx > 0 else "US cash sizing cannot be computed",
            {"budget_krw": us_budget, "fx": fx, "approx_budget_usd": round(us_budget / fx, 2) if fx > 0 else 0},
        )
    )
    checks.append(
        CheckResult(
            "us.pathb_intraday_only",
            "WARN" if _truthy(effective.get("PATHB_INTRADAY_ONLY")) else "PASS",
            "US Path B is forced intraday despite hold-days policy"
            if _truthy(effective.get("PATHB_INTRADAY_ONLY"))
            else "US Path B follows hold-days policy via shared config",
            {"PATHB_INTRADAY_ONLY": effective.get("PATHB_INTRADAY_ONLY")},
        )
    )

    us_account = str(effective.get("KIS_ACCOUNT_NO_US") or "").strip()
    kr_account = str(effective.get("KIS_ACCOUNT_NO") or "").strip()
    us_app = str(effective.get("KIS_APP_KEY_US") or "").strip()
    kr_app = str(effective.get("KIS_APP_KEY") or "").strip()
    us_secret = str(effective.get("KIS_APP_SECRET_US") or "").strip()
    kr_secret = str(effective.get("KIS_APP_SECRET") or "").strip()
    is_paper_us = str(effective.get("KIS_IS_PAPER_US") or "").strip().lower()
    fallback_accepted = _truthy(effective.get("KIS_US_CREDENTIAL_FALLBACK_ACCEPTED"))
    cred_status = "PASS"
    cred_notes = []
    if not us_account and not kr_account:
        cred_status = "FAIL"
        cred_notes.append("US account missing and no KR account fallback")
    elif not us_account:
        cred_status = "WARN"
        cred_notes.append("KIS_ACCOUNT_NO_US missing; KR account fallback will be used")
    if (not us_app or not us_secret) and (not kr_app or not kr_secret):
        cred_status = "FAIL"
        cred_notes.append("US app key/secret missing and no KR app fallback")
    elif not us_app or not us_secret:
        cred_status = "WARN" if cred_status != "FAIL" else cred_status
        app_state = "present" if us_app else "missing"
        secret_state = "present" if us_secret else "missing"
        cred_notes.append(
            f"US-specific credential incomplete: app_key={app_state}, "
            f"app_secret={secret_state}; common key fallback will be used"
        )
    if is_paper_us == "true" and mode == "live":
        cred_status = "FAIL"
        cred_notes.append("KIS_IS_PAPER_US=true in live mode")
    us_credential_mode = (
        "separate_us"
        if us_account and us_app and us_secret
        else "fallback_shared_kr"
        if kr_account and kr_app and kr_secret
        else "missing"
    )
    accepted_exception = cred_status == "WARN" and us_credential_mode == "fallback_shared_kr" and fallback_accepted
    display_status = "PASS" if accepted_exception else cred_status
    checks.append(
        CheckResult(
            "kis.us_credentials",
            display_status,
            (
                "shared KR/US KIS credential fallback is explicitly accepted by policy"
                if accepted_exception
                else "; ".join(cred_notes)
                if cred_notes
                else "US live credentials/fallbacks are explicit"
            ),
            {
                "KIS_ACCOUNT_NO_US_present": bool(us_account),
                "KIS_APP_KEY_US_present": bool(us_app),
                "KIS_APP_SECRET_US_present": bool(us_secret),
                "KIS_IS_PAPER_US": is_paper_us,
                "credential_mode": us_credential_mode,
                "fallback_to_kr_allowed": us_credential_mode == "fallback_shared_kr",
                "fallback_accepted_by_policy": fallback_accepted,
                "accepted_exception": accepted_exception,
                "remediation_required": display_status != "PASS",
                "operator_action": (
                    "shared KR/US KIS credential fallback is explicitly accepted by policy"
                    if accepted_exception
                    else "set KIS_APP_KEY_US/KIS_APP_SECRET_US or KIS_US_CREDENTIAL_FALLBACK_ACCEPTED=true after policy approval"
                    if cred_status == "WARN"
                    else "fix missing or invalid US KIS credential configuration"
                ),
            },
        )
    )
    return checks, config


def _db_checks(mode: str = "live") -> list[CheckResult]:
    checks: list[CheckResult] = []
    from lifecycle.event_store import EventStore
    from lifecycle.models import LifecycleEvent

    store = EventStore(read_only=True, initialize=False)
    if not store.path.exists():
        detail = f"event store DB missing: {store.path}"
        data = {
            "path": str(store.path),
            "missing": True,
            "operator_action": "initialize event store before live start",
            "blocked_if_live_start": True,
        }
        return [
            CheckResult("db.live_path", "FAIL", detail, data),
            CheckResult("db.event_store_open", "FAIL", "event store DB is absent", data),
            CheckResult("db.event_store_schema", "FAIL", "event-store schema unavailable because DB is absent", data),
        ]
    kr_session = _session_date_guess("KR")
    us_session = _session_date_guess("US")
    current_sessions = {"KR": kr_session, "US": us_session}
    active_statuses = {
        "WAITING",
        "HIT",
        "ORDER_SENT",
        "ORDER_ACKED",
        "PARTIAL_FILLED",
        "FILLED",
        "SELL_SENT",
        "SELL_ACKED",
        "SELL_PARTIAL_FILLED",
        "ORDER_UNKNOWN",
    }
    checks.append(CheckResult("db.live_path", "PASS", "live event store path resolved", {"path": str(store.path)}))
    try:
        conn_context = store.connect()
    except sqlite3.OperationalError as exc:
        checks.append(
            CheckResult(
                "db.event_store_open",
                "FAIL",
                f"event store read-only open failed: {exc}",
                {
                    "path": str(store.path),
                    "operator_action": "verify event store path and SQLite file accessibility before live start",
                    "blocked_if_live_start": True,
                },
            )
        )
        checks.append(
            CheckResult(
                "db.event_store_schema",
                "FAIL",
                "event-store schema unavailable because DB could not be opened",
                {"path": str(store.path), "open_error": str(exc)},
            )
        )
        return checks
    with conn_context as conn:
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        checks.append(CheckResult("db.wal_mode", "PASS" if str(journal).lower() == "wal" else "FAIL", f"journal_mode={journal}"))
        schema_missing: dict[str, list[str]] = {}
        for table, required in REQUIRED_TABLE_COLUMNS.items():
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            found = {str(row[1]) for row in rows}
            missing = sorted(required - found)
            if missing:
                schema_missing[table] = missing
            checks.append(
                CheckResult(
                    f"db.schema.{table}",
                    "FAIL" if missing else "PASS",
                    f"missing columns: {missing}" if missing else "required columns present",
                    {"path": str(store.path), "missing": missing},
                )
            )
        checks.append(
            CheckResult(
                "db.event_store_schema",
                "FAIL" if schema_missing else "PASS",
                "schema mismatch found" if schema_missing else "required event-store tables are present",
                {
                    "missing": schema_missing,
                    "path": str(store.path),
                    "operator_action": "initialize or migrate event store schema before live start" if schema_missing else "none",
                    "blocked_if_live_start": bool(schema_missing),
                },
            )
        )
        if schema_missing:
            return checks
        invalid_json = 0
        for row in conn.execute("SELECT path_run_id, plan_json FROM v2_path_runs ORDER BY updated_at DESC LIMIT 200").fetchall():
            try:
                json.loads(row[1] or "{}")
            except Exception:
                invalid_json += 1
        checks.append(CheckResult("db.path_run_json", "FAIL" if invalid_json else "PASS", "recent plan_json decode check", {"invalid_recent_rows": invalid_json}))
        checks.append(CheckResult("db.path_run_plan_json_valid", "FAIL" if invalid_json else "PASS", "recent Path B plan_json rows parse cleanly", {"invalid_recent_rows": invalid_json}))

        exposure_by_path, _exposure_by_ticker = _pathb_local_exposure_index(mode)
        try:
            broker_snapshot = _load_broker_truth_snapshot_for_db(mode)
        except Exception:
            broker_snapshot = {}
        unknown_rows = conn.execute(
            """
            SELECT market, runtime_mode, session_date, ticker, path_run_id, path_type, status, updated_at, plan_json
            FROM v2_path_runs
            WHERE runtime_mode=? AND status='ORDER_UNKNOWN'
            ORDER BY updated_at DESC
            LIMIT 50
            """,
            (mode,),
        ).fetchall()
        current_unknown: list[dict[str, Any]] = []
        previous_unknown: list[dict[str, Any]] = []
        for row in unknown_rows:
            item = dict(row)
            try:
                plan = json.loads(str(item.pop("plan_json", "") or "{}"))
            except Exception:
                plan = {}
            item.update(_pathb_operator_context(item, plan))
            item["order_unknown_phase"] = str(plan.get("order_unknown_phase") or "")
            item["order_unknown_age_sec"] = plan.get("order_unknown_age_sec")
            item["order_unknown_reconcile_attempts"] = plan.get("order_unknown_reconcile_attempts")
            _attach_exposure_evidence(item, exposure_by_path, broker_snapshot)
            market_key = str(item.get("market") or "").upper()
            session_for_market = current_sessions.get(market_key, "")
            current_session_row = item.get("session_date") == session_for_market
            _mark_order_unknown_remediation_hint(
                item,
                mode=mode,
                previous_session=not current_session_row,
                session_before=session_for_market,
            )
            if current_session_row:
                current_unknown.append(item)
            else:
                previous_unknown.append(item)
        previous_recoverable_still_held = [
            item for item in previous_unknown if bool(item.get("pathb_recoverable_still_held"))
        ]
        previous_with_local_exposure = [
            item
            for item in previous_unknown
            if bool(item.get("local_exposure")) and not bool(item.get("pathb_recoverable_still_held"))
        ]
        previous_no_local_exposure = [item for item in previous_unknown if not bool(item.get("local_exposure"))]
        previous_audited_remediation_available = [
            item for item in previous_no_local_exposure if bool(item.get("remediation_allowed"))
        ]
        previous_no_local_exposure_blocked = [
            item for item in previous_no_local_exposure if not bool(item.get("remediation_allowed"))
        ]
        blocking_unknown_count = (
            len(current_unknown)
            + len(previous_with_local_exposure)
            + len(previous_recoverable_still_held)
            + len(previous_no_local_exposure_blocked)
        )
        accepted_historical_only = bool(unknown_rows) and blocking_unknown_count == 0
        order_unknown_warning = _warning_meta(
            "pathb_order_unknown_historical_no_exposure",
            accepted=accepted_historical_only,
            action=(
                "resolve current/local-exposure ORDER_UNKNOWN rows before live start"
                if blocking_unknown_count
                else (
                    "review audited remediation dry-run output before applying historical no-exposure cleanup"
                    if unknown_rows
                    else "none"
                )
            ),
            blocked_if_live_start=bool(blocking_unknown_count),
        )
        checks.append(
            CheckResult(
                "db.order_unknown_unresolved",
                "WARN" if unknown_rows else "PASS",
                f"unresolved ORDER_UNKNOWN rows={len(unknown_rows)}" if unknown_rows else "no unresolved Path B ORDER_UNKNOWN rows",
                {
                    "current_session": current_unknown,
                    "current_session_blocking": current_unknown,
                    "previous_session": previous_unknown,
                    "previous_session_with_local_exposure": previous_with_local_exposure,
                    "previous_session_no_local_exposure": previous_no_local_exposure,
                    "previous_session_no_local_exposure_blocked": previous_no_local_exposure_blocked,
                    "previous_session_recoverable_still_held": previous_recoverable_still_held,
                    "previous_session_audited_remediation_available": previous_audited_remediation_available,
                    "current_session_count": len(current_unknown),
                    "current_session_blocking_count": len(current_unknown),
                    "previous_session_count": len(previous_unknown),
                    "previous_session_with_local_exposure_count": len(previous_with_local_exposure),
                    "previous_session_no_local_exposure_count": len(previous_no_local_exposure),
                    "previous_session_no_local_exposure_blocked_count": len(previous_no_local_exposure_blocked),
                    "previous_session_recoverable_still_held_count": len(previous_recoverable_still_held),
                    "previous_session_audited_remediation_available_count": len(previous_audited_remediation_available),
                    "audited_remediation_available_count": len(previous_audited_remediation_available),
                    "blocking_unknown_count": blocking_unknown_count,
                    **order_unknown_warning,
                    "remediation_required": bool(unknown_rows) and not accepted_historical_only,
                    "auto_remediation": False,
                    "auto_remediation_allowed": False,
                    "recoverable_hint_count": len(previous_recoverable_still_held),
                    "remediation_tools": {
                        market: _order_unknown_remediation_command(mode, market, session)
                        for market, session in current_sessions.items()
                    },
                    "remediation_tool": _order_unknown_remediation_command(mode, "US", current_sessions.get("US", "")),
                    "operator_action": (
                        order_unknown_warning.get("operator_action")
                        if unknown_rows
                        else "none"
                    ),
                },
            )
        )

        stale_rows = conn.execute(
            f"""
            SELECT market, runtime_mode, session_date, ticker, path_run_id, status, updated_at, plan_json
            FROM v2_path_runs
            WHERE runtime_mode=? AND status IN ({','.join('?' for _ in active_statuses)})
            ORDER BY updated_at DESC
            LIMIT 100
            """,
            (mode, *sorted(active_statuses)),
        ).fetchall()
        stale_active: list[dict[str, Any]] = []
        active_runs_by_id: dict[str, dict[str, Any]] = {}
        for row in stale_rows:
            item = dict(row)
            item["plan"] = _safe_json_object(item.pop("plan_json", ""))
            item.update(_pathb_operator_context(item, item.get("plan")))
            _attach_exposure_evidence(item, exposure_by_path, broker_snapshot)
            if item.get("path_run_id"):
                active_runs_by_id[str(item.get("path_run_id"))] = item
            session_for_market = current_sessions.get(str(item.get("market") or ""))
            if item.get("session_date") != session_for_market:
                stale_active.append(item)
        stale_recoverable_still_held = [
            item for item in stale_active if bool(item.get("pathb_recoverable_still_held"))
        ]
        stale_recoverable_entry_holding = [
            item for item in stale_active if bool(item.get("pathb_recoverable_entry_holding"))
        ]
        stale_with_local_exposure = [
            item
            for item in stale_active
            if bool(item.get("local_exposure"))
            and not bool(item.get("pathb_recoverable_still_held"))
            and not bool(item.get("pathb_recoverable_entry_holding"))
        ]
        stale_no_local_exposure = [item for item in stale_active if not bool(item.get("local_exposure"))]
        checks.append(
            CheckResult(
                "db.pathb_stale_active_runs",
                "WARN" if stale_active else "PASS",
                f"previous-session active Path B rows={len(stale_active)}" if stale_active else "no previous-session active Path B rows",
                {
                    "rows": stale_active[:30],
                    "previous_session_with_local_exposure": stale_with_local_exposure[:30],
                    "previous_session_no_local_exposure": stale_no_local_exposure[:30],
                    "previous_session_recoverable_still_held": stale_recoverable_still_held[:30],
                    "previous_session_recoverable_entry_holding": stale_recoverable_entry_holding[:30],
                    "current_sessions": current_sessions,
                    "stale_active_count": len(stale_active),
                    "previous_session_with_local_exposure_count": len(stale_with_local_exposure),
                    "previous_session_no_local_exposure_count": len(stale_no_local_exposure),
                    "previous_session_recoverable_still_held_count": len(stale_recoverable_still_held),
                    "previous_session_recoverable_entry_holding_count": len(stale_recoverable_entry_holding),
                    "accepted_exception": False,
                    "remediation_required": bool(stale_active),
                    "auto_remediation": False,
                    "auto_remediation_allowed": False,
                    "recoverable_hint_count": len(stale_recoverable_still_held) + len(stale_recoverable_entry_holding),
                    "remediation_tool": "python tools/pathb_legacy_remediation.py --mode live --write-report",
                    "operator_action": (
                        "read-only: verify broker positions/open orders/fills before closing, expiring, or backfilling any prior-session active row"
                        if stale_active
                        else "none"
                    ),
                },
            )
        )

        broker_conflicts = _pathb_broker_truth_conflicts(
            mode,
            active_runs_by_id,
            broker_snapshot=broker_snapshot,
            exposure_by_path=exposure_by_path,
        )
        broker_conflict_blockers = [
            item for item in broker_conflicts if bool(item.get("do_not_start", True))
        ]
        broker_conflict_recoverable = [
            item for item in broker_conflicts if bool(item.get("pathb_recoverable_still_held"))
        ]
        broker_conflict_status = (
            "FAIL"
            if broker_conflict_blockers
            else "WARN"
            if broker_conflicts
            else "PASS"
        )
        checks.append(
            CheckResult(
                "db.pathb_broker_truth_conflict",
                broker_conflict_status,
                (
                    f"Path B broker truth conflicts={len(broker_conflicts)} blockers={len(broker_conflict_blockers)} "
                    f"recoverable_still_held={len(broker_conflict_recoverable)}"
                    if broker_conflicts
                    else "no Path B broker truth conflicts"
                ),
                {
                    "conflicts": broker_conflicts[:30],
                    "conflict_count": len(broker_conflicts),
                    "blocking_conflict_count": len(broker_conflict_blockers),
                    "recoverable_still_held_count": len(broker_conflict_recoverable),
                    "accepted_exception": False,
                    "remediation_required": bool(broker_conflicts),
                    "auto_remediation": False,
                    "auto_remediation_allowed": False,
                    "recoverable_hint_count": len(broker_conflict_recoverable),
                    "remediation_tool": _pathb_broker_conflict_remediation_tool(
                        broker_conflict_blockers,
                        broker_conflicts,
                    ),
                    "operator_action": (
                        "read-only: verify broker positions/open orders/today fills and reconcile local Path B state before live start"
                        if broker_conflicts
                        else "none"
                    ),
                },
            )
        )

        recent_events = conn.execute(
            """
            SELECT event_type, market, runtime_mode, session_date, ticker, decision_id, payload_json
            FROM lifecycle_events
            WHERE runtime_mode=?
            ORDER BY event_id DESC
            LIMIT 1000
            """,
            (mode,),
        ).fetchall()
        invalid_event_market_runtime = []
        for row in recent_events:
            event_type = str(row["event_type"] or "")
            if row["market"] not in {"KR", "US"} or row["runtime_mode"] not in {"live", "paper"}:
                invalid_event_market_runtime.append(
                    {
                        "event_type": event_type,
                        "market": row["market"],
                        "runtime_mode": row["runtime_mode"],
                        "ticker": row["ticker"],
                    }
                )

        recent_runs = conn.execute(
            """
            SELECT path_run_id, market, runtime_mode, session_date, ticker, status
            FROM v2_path_runs
            WHERE runtime_mode=? AND path_type='claude_price'
            ORDER BY updated_at DESC
            LIMIT 500
            """,
            (mode,),
        ).fetchall()
        terminal_events = conn.execute(
            """
            SELECT event_type, market, runtime_mode, session_date, ticker, decision_id, payload_json
            FROM lifecycle_events
            WHERE runtime_mode=? AND event_type IN ('FILLED','PARTIAL_FILLED','CLOSED')
            ORDER BY event_id
            """,
            (mode,),
        ).fetchall()
        decision_ids_with_runs = {
            str(row["decision_id"] or "")
            for row in conn.execute(
                """
                SELECT DISTINCT decision_id
                FROM v2_path_runs
                WHERE runtime_mode=? AND path_type='claude_price' AND decision_id IS NOT NULL AND decision_id<>''
                """,
                (mode,),
            ).fetchall()
        }
        inconsistent_runs = _pathb_terminal_missing_events(list(recent_runs), list(terminal_events))
        window_missing_path = _pathb_like_missing_path_run_id_rows(list(recent_events), decision_ids_with_runs)
        checks.append(_pathb_lifecycle_window_check_result(inconsistent_runs, window_missing_path))
        full_events = conn.execute(
            """
            SELECT event_type, market, runtime_mode, session_date, ticker, decision_id, payload_json
            FROM lifecycle_events
            WHERE runtime_mode=?
            ORDER BY event_id
            """,
            (mode,),
        ).fetchall()
        full_runs = conn.execute(
            """
            SELECT path_run_id, market, runtime_mode, session_date, ticker, status, plan_json
            FROM v2_path_runs
            WHERE runtime_mode=? AND path_type='claude_price'
            ORDER BY updated_at DESC
            """,
            (mode,),
        ).fetchall()
        full_missing = _pathb_terminal_missing_events(list(full_runs), list(full_events))
        full_detail = (
            "full terminal lifecycle event check passed; recent window has diagnostic warnings"
            if not full_missing and (inconsistent_runs or window_missing_path["rows"])
            else "full terminal lifecycle event check passed"
            if not full_missing
            else f"full terminal lifecycle missing events={len(full_missing)}"
        )
        checks.append(
            CheckResult(
                "db.pathb_lifecycle_full_consistency",
                "WARN" if full_missing else "PASS",
                full_detail,
                {
                    "missing_events": full_missing[:30],
                    "missing_events_count": len(full_missing),
                    "full_terminal_missing_events_count": len(full_missing),
                    "checked_runs": len(full_runs),
                    "checked_events": len(full_events),
                    "accepted_exception": False,
                    "remediation_required": bool(full_missing),
                    "remediation_tool": "python tools/pathb_legacy_remediation.py --mode live --write-report",
                    "operator_action": (
                        "generate an audited lifecycle backfill plan before mutating production records"
                        if full_missing
                        else "none"
                    ),
                },
            )
        )
        cross_run_closed_evidence = _pathb_cross_run_closed_lifecycle_evidence(list(full_runs))
        checks.append(
            CheckResult(
                "db.pathb_closed_lifecycle_evidence_consistency",
                "WARN" if cross_run_closed_evidence else "PASS",
                (
                    f"PathB CLOSED lifecycle evidence references another run={len(cross_run_closed_evidence)}"
                    if cross_run_closed_evidence
                    else "PathB CLOSED lifecycle evidence is scoped to the same path_run_id"
                ),
                {
                    "cross_run_evidence": cross_run_closed_evidence[:30],
                    "cross_run_evidence_count": len(cross_run_closed_evidence),
                    "accepted_exception": False,
                    "remediation_required": bool(cross_run_closed_evidence),
                    "operator_action": (
                        "audit affected historical rows before using path_run plan_json as realized performance"
                        if cross_run_closed_evidence
                        else "none"
                    ),
                    "blocked_if_live_start": False,
                },
            )
        )
        checks.append(
            CheckResult(
                "db.market_runtime_isolation",
                "FAIL" if invalid_event_market_runtime else "PASS",
                "invalid market/runtime values found" if invalid_event_market_runtime else "recent events use valid market/runtime values",
                {"invalid": invalid_event_market_runtime[:30]},
            )
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_store = EventStore(Path(tmp) / "events.db")
        tmp_store.create_decision(
            decision_id="preflight_decision",
            market="KR",
            runtime_mode="live",
            session_date="2026-04-27",
            ticker="005930",
            prompt_version="preflight",
            brain_snapshot_id="preflight_brain",
        )
        tmp_store.append(
            LifecycleEvent(
                event_type="CLAUDE_TRADE_READY",
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                decision_id="preflight_decision",
                prompt_version="preflight",
                brain_snapshot_id="preflight_brain",
            )
        )
        tmp_store.create_path_run(
            path_run_id="preflight_path",
            decision_id="preflight_decision",
            path_type="claude_price",
            market="KR",
            runtime_mode="live",
            session_date="2026-04-27",
            ticker="005930",
            status="WAITING",
            plan={"buy_zone_low": 52000, "sell_target": 54500},
        )
        tmp_store.update_path_run("preflight_path", status="FILLED", plan={"filled_qty": 1}, merge_plan=True)
        reopened = EventStore(Path(tmp) / "events.db")
        run = reopened.find_path_run("preflight_path")
        ok = bool(run and run["status"] == "FILLED" and run["plan"].get("buy_zone_low") == 52000 and run["plan"].get("filled_qty") == 1)
        checks.append(CheckResult("db.roundtrip_temp", "PASS" if ok else "FAIL", "temp DB decision/event/path_run round trip"))
    checks.extend(_agent_call_event_store_contamination_checks(mode))
    return checks


def _agent_call_event_store_contamination_checks(mode: str) -> list[CheckResult]:
    try:
        from tools.clean_agent_call_event_store import scan_agent_call_event_store

        summary = scan_agent_call_event_store(get_runtime_path("data", "audit", "agent_call_events.db"))
        matched = int(summary.get("matched_event_count") or 0)
        if matched:
            status = "FAIL" if str(mode or "").lower() == "live" else "WARN"
            detail = f"test-contaminated agent_call_events rows={matched}"
        else:
            status = "PASS"
            detail = "agent_call_events has no known test contamination signatures"
        summary["remediation_tool"] = "python tools/clean_agent_call_event_store.py --json"
        summary["apply_command"] = "python tools/clean_agent_call_event_store.py --apply --json"
        return [CheckResult("db.agent_call_event_store_contamination", status, detail, summary)]
    except Exception as exc:
        return [
            CheckResult(
                "db.agent_call_event_store_contamination",
                "WARN",
                f"agent_call_events contamination check failed: {exc}",
            )
        ]


def _token_checks(mode: str) -> list[CheckResult]:
    checks: list[CheckResult] = []
    rate_limit_check = _token_rate_limit_marker_check(mode)
    token_path = ROOT / "state" / f"{mode}_kis_token.json"
    if not token_path.exists():
        return [
            CheckResult("kis.token_file", "FAIL", "token file missing", {"path": str(token_path)}),
            CheckResult("kis.kr_token_refresh", "FAIL", "token file missing", {"path": str(token_path)}),
            CheckResult("kis.us_token_refresh", "FAIL", "token file missing", {"path": str(token_path)}),
            rate_limit_check,
        ]
    try:
        data = json.loads(token_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [
            CheckResult("kis.token_file", "FAIL", f"token file unreadable: {exc}", {"path": str(token_path)}),
            CheckResult("kis.kr_token_refresh", "FAIL", f"token file unreadable: {exc}", {"path": str(token_path)}),
            CheckResult("kis.us_token_refresh", "FAIL", f"token file unreadable: {exc}", {"path": str(token_path)}),
            rate_limit_check,
        ]
    expires_raw = str(data.get("expires_at", "") or "")
    issued_raw = str(data.get("issued_at", "") or "")
    try:
        expires_at = datetime.fromisoformat(expires_raw)
        now = datetime.now(tz=expires_at.tzinfo) if expires_at.tzinfo else datetime.now()
        minutes_left = (expires_at - now).total_seconds() / 60.0
        status = "FAIL" if minutes_left <= 0 else ("WARN" if minutes_left < 180 else "PASS")
        detail = f"token expires_at={expires_raw}, minutes_left={minutes_left:.1f}"
        checks.append(
            CheckResult(
                "kis.token_expiry",
                status,
                detail,
                {
                    "path": str(token_path),
                    "issued_at": issued_raw,
                    "expires_at": expires_raw,
                    "minutes_left": round(minutes_left, 1),
                    "context": data.get("context", {}),
                },
            )
        )
        trading_text = _repo_text("trading_bot.py")
        helper_ok = "_get_balance_with_token_refresh" in trading_text and bool(
            re.search(r"get_access_token\(\s*force_refresh\s*=\s*True", trading_text)
        )
        refresh_status = "FAIL" if status == "FAIL" or not helper_ok else ("WARN" if status == "WARN" else "PASS")
        refresh_detail = (
            "token expired or refresh helper missing"
            if refresh_status == "FAIL"
            else ("token is near expiry; forced refresh helper is present" if refresh_status == "WARN" else "token valid and forced refresh helper is present")
        )
        checks.append(CheckResult("kis.kr_token_refresh", refresh_status, refresh_detail, {"minutes_left": round(minutes_left, 1), "helper": helper_ok}))
        checks.append(CheckResult("kis.us_token_refresh", refresh_status, refresh_detail, {"minutes_left": round(minutes_left, 1), "helper": helper_ok}))
    except Exception as exc:
        checks.append(CheckResult("kis.token_expiry", "FAIL", f"cannot parse token expiry: {exc}", {"expires_at": expires_raw}))
        checks.append(CheckResult("kis.kr_token_refresh", "FAIL", f"cannot parse token expiry: {exc}", {"expires_at": expires_raw}))
        checks.append(CheckResult("kis.us_token_refresh", "FAIL", f"cannot parse token expiry: {exc}", {"expires_at": expires_raw}))
    checks.append(rate_limit_check)
    checks.append(
        CheckResult(
            "kis.balance_probe",
            "WARN",
            "default preflight avoids direct balance APIs; broker-truth snapshot and live smoke cover read-only balance checks",
            {
                "reason": "network/order side effects intentionally avoided in default preflight",
                "read_only_check": "run live guardian/smoke or KIS read-only precheck before live trading",
                "accepted_exception": True,
                "remediation_required": False,
                "operator_action": "none if live guardian smoke and broker-truth snapshots are passing",
            },
        )
    )
    return checks


def _token_rate_limit_marker_check(mode: str) -> CheckResult:
    state_dir = get_runtime_path("state", make_parents=False)
    markers: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    now_ts = datetime.now().timestamp()
    for path in sorted(state_dir.glob(f"{mode}_kis_token_rate_limit_*.json")):
        try:
            marker = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(marker, dict):
                marker = {}
        except Exception as exc:
            markers.append({"path": str(path), "active": False, "error": str(exc)})
            continue
        try:
            until_ts = float(marker.get("cooldown_until_ts", 0) or 0)
        except Exception:
            until_ts = 0.0
        payload = marker.get("payload") if isinstance(marker.get("payload"), dict) else {}
        item = {
            "path": str(path),
            "market": marker.get("market", ""),
            "cooldown_until": marker.get("cooldown_until", ""),
            "retry_after_sec": max(0, int(until_ts - now_ts)) if until_ts > 0 else 0,
            "msg_cd": payload.get("msg_cd", ""),
            "active": until_ts > now_ts,
        }
        markers.append(item)
        if item["active"]:
            active.append(item)
    if not active:
        return CheckResult(
            "kis.token_rate_limit_cooldown",
            "PASS",
            "no active KIS token issue rate-limit cooldown marker",
            {"marker_count": len(markers), "active_count": 0, "markers": markers[:10]},
        )
    return CheckResult(
        "kis.token_rate_limit_cooldown",
        "WARN",
        f"active KIS token issue rate-limit cooldown markers={len(active)}",
        {
            "marker_count": len(markers),
            "active_count": len(active),
            "active_markers": active[:10],
            **_warning_meta(
                "kis_token_rate_limit_cooldown",
                accepted=False,
                action="wait for cooldown expiry or use valid cached token; do not force-refresh repeatedly",
                blocked_if_live_start=False,
            ),
        },
    )


def _position_market_from_ticker(ticker: str) -> str:
    return infer_ticker_market(ticker, unknown="")


def _open_positions_market_metadata_check(mode: str) -> CheckResult:
    path = get_runtime_path("state", f"{mode}_open_positions.json", make_parents=False)
    data = {
        "path": str(path),
        "missing_market": [],
        "conflicts": [],
        "count": 0,
    }
    if not path.exists():
        return CheckResult(
            "state.open_positions_market_metadata",
            "PASS",
            "open positions file absent",
            data,
        )
    try:
        items = json.loads(path.read_text(encoding="utf-8")) or []
    except Exception as exc:
        return CheckResult(
            "state.open_positions_market_metadata",
            "FAIL",
            f"open positions JSON unreadable: {exc}",
            data,
        )
    if not isinstance(items, list):
        data["root_type"] = type(items).__name__
        return CheckResult(
            "state.open_positions_market_metadata",
            "FAIL",
            "open positions root is not a list",
            data,
        )
    data["count"] = len(items)
    for idx, pos in enumerate(items):
        if not isinstance(pos, dict):
            data["conflicts"].append({"index": idx, "reason": "position_not_object"})
            continue
        ticker = str(pos.get("ticker") or "").strip()
        declared = str(pos.get("market") or "").strip().upper()
        inferred = _position_market_from_ticker(ticker)
        currency = str(pos.get("display_currency") or "").strip().upper()
        pathb_plan = pos.get("pathb_plan") if isinstance(pos.get("pathb_plan"), dict) else {}
        pathb_market = str((pathb_plan or {}).get("market") or "").strip().upper()
        row = {
            "index": idx,
            "ticker": ticker,
            "market": declared,
            "inferred_market": inferred,
            "display_currency": currency,
            "pathb_market": pathb_market,
        }
        if declared not in {"KR", "US"}:
            data["missing_market"].append(row)
            continue
        if inferred and declared != inferred:
            data["conflicts"].append({**row, "reason": "ticker_market_mismatch"})
        if pathb_market in {"KR", "US"} and declared != pathb_market:
            data["conflicts"].append({**row, "reason": "pathb_plan_market_mismatch"})
        if currency == "USD" and declared != "US":
            data["conflicts"].append({**row, "reason": "usd_currency_market_mismatch"})
        if currency == "KRW" and declared != "KR":
            data["conflicts"].append({**row, "reason": "krw_currency_market_mismatch"})
    if data["conflicts"]:
        return CheckResult(
            "state.open_positions_market_metadata",
            "FAIL",
            f"open position market conflicts={len(data['conflicts'])}",
            data,
        )
    if data["missing_market"]:
        return CheckResult(
            "state.open_positions_market_metadata",
            "WARN",
            f"open positions missing explicit market={len(data['missing_market'])}",
            data,
        )
    return CheckResult(
        "state.open_positions_market_metadata",
        "PASS",
        f"open positions market metadata valid rows={len(items)}",
        data,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_porcelain_for_path(path: Path) -> tuple[list[str], str]:
    try:
        rel = str(path.resolve().relative_to(ROOT.resolve()))
    except Exception:
        rel = str(path)
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--", rel],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return [], (proc.stderr or proc.stdout or f"git status exited {proc.returncode}").strip()
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return lines, ""


def _jsonl_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            continue
        count += 1
    return count


def _brain_memory_change_check(
    mode: str,
    *,
    brain_path: Path | None = None,
    approval_queue_path: Path | None = None,
) -> CheckResult:
    path = brain_path or (ROOT / "state" / "brain.json")
    data: dict[str, Any] = {
        "mode": mode,
        "path": str(path),
        "exists": path.exists(),
        "pending_approval_count": 0,
    }
    parse_error = ""
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                parse_error = "root is not object"
            else:
                data["sha256"] = _file_sha256(path)
                data["version"] = parsed.get("version") or parsed.get("schema_version") or ""
                data["last_updated"] = (
                    parsed.get("last_updated")
                    or parsed.get("updated_at")
                    or parsed.get("generated_at")
                    or ""
                )
        except Exception as exc:
            parse_error = f"{type(exc).__name__}: {exc}"
    git_status, git_error = _git_porcelain_for_path(path)
    data["git_status"] = git_status
    data["git_dirty"] = bool(git_status)
    if git_error:
        data["git_error"] = git_error
    try:
        queue_path = approval_queue_path or (ROOT / "state" / "brain_approval_queue.jsonl")
        data["approval_queue_path"] = str(queue_path)
        data["pending_approval_count"] = _jsonl_row_count(queue_path)
    except Exception as exc:
        data["approval_queue_error"] = f"{type(exc).__name__}: {exc}"

    if parse_error:
        return CheckResult(
            "state.brain_memory_change_guard",
            "FAIL",
            f"brain.json unreadable: {parse_error}",
            data,
        )
    if data["git_dirty"]:
        data.update(
            _warning_meta(
                "brain_memory_dirty",
                accepted=False,
                action="review and explicitly commit or revert state/brain.json before treating policy memory as approved",
                blocked_if_live_start=True,
            )
        )
        return CheckResult(
            "state.brain_memory_change_guard",
            "WARN",
            "state/brain.json has uncommitted changes",
            data,
        )
    data.update(_warning_meta("brain_memory_clean", accepted=True))
    return CheckResult(
        "state.brain_memory_change_guard",
        "PASS",
        "state/brain.json has no git-visible changes",
        data,
    )


def _state_checks(config: dict[str, Any], mode: str) -> list[CheckResult]:
    checks: list[CheckResult] = []
    effective: dict[str, str] = config.get("effective", {})
    checks.append(
        _pid_lock_check(
            "runtime.bot_pid_lock",
            get_runtime_path("state", f"{mode}_trading_bot.pid"),
            expected_mode=mode,
        )
    )
    checks.append(
        _pid_lock_check(
            "runtime.dashboard_pid_lock",
            get_runtime_path("state", "dashboard_server.pid"),
            expected_mode="dashboard_server",
        )
    )
    checks.append(_open_positions_market_metadata_check(mode))
    brain_candidates = [
        ROOT / "state" / "brain.json",
        ROOT / "claude_memory" / "brain.json",
        ROOT / "brain.json",
    ]
    parsed_brains = []
    brain_errors = []
    for path in brain_candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                brain_errors.append({"path": str(path), "error": "root is not object"})
            else:
                parsed_brains.append({"path": str(path), "keys": sorted(data.keys())[:20]})
        except Exception as exc:
            brain_errors.append({"path": str(path), "error": str(exc)})
    fresh_brain = _truthy(effective.get("V2_FRESH_BRAIN_START"))
    if brain_errors:
        status = "FAIL"
        detail = "brain JSON parse/root errors found"
    elif parsed_brains:
        status = "PASS"
        detail = "brain JSON candidates parse cleanly"
    elif fresh_brain:
        status = "PASS"
        detail = "brain JSON missing but fresh V2 brain start is enabled"
    else:
        status = "WARN"
        detail = "brain JSON missing and fresh V2 brain start is not enabled"
    checks.append(
        CheckResult(
            "state.brain_json_valid",
            status,
            detail,
            {"parsed": parsed_brains, "errors": brain_errors, "fresh_brain_start": fresh_brain},
        )
    )
    checks.append(_brain_memory_change_check(mode))

    control_path = ROOT / "state" / f"{mode}_pathb_control.json"
    if not control_path.exists():
        checks.append(
            CheckResult(
                "runtime.pathb_control_state",
                "PASS",
                "Path B runtime control file missing; default operator state is enabled",
                {"path": str(control_path), "default_enabled": True},
            )
        )
    else:
        try:
            data = json.loads(control_path.read_text(encoding="utf-8"))
            enabled = bool(data.get("enabled", True))
            emergency = bool(data.get("emergency_disabled", False))
            status = "FAIL" if emergency else ("WARN" if not enabled else "PASS")
            detail = "Path B emergency-disabled" if emergency else ("Path B operator-disabled" if not enabled else "Path B operator control allows live operation")
            checks.append(CheckResult("runtime.pathb_control_state", status, detail, {"path": str(control_path), "state": data}))
        except Exception as exc:
            checks.append(CheckResult("runtime.pathb_control_state", "FAIL", f"Path B control state unreadable: {exc}", {"path": str(control_path)}))
    snapshot_path = ROOT / "state" / f"{mode}_broker_truth_snapshot.json"
    checks.extend(_broker_truth_snapshot_file_checks(mode, snapshot_path))
    return checks


def _static_code_checks(effective: dict[str, str] | None = None) -> list[CheckResult]:
    checks: list[CheckResult] = []
    trading = _repo_text("trading_bot.py")
    kis = _repo_text("kis_api.py")
    pathb = _repo_text("runtime", "pathb_runtime.py")
    v2_runtime = _repo_text("runtime", "v2_lifecycle_runtime.py")
    arbiter = _repo_text("execution", "path_arbiter.py")
    sell = _repo_text("execution", "claude_price_sell_manager.py")
    adapter = _repo_text("execution", "claude_price_adapter.py")
    analysts = _repo_text("minority_report", "analysts.py")
    dashboard = _repo_text("dashboard", "dashboard_server.py")
    ops_summary = _repo_text("interface", "v2_ops_summary.py")
    broker_truth = _repo_text("runtime", "broker_truth_snapshot.py")
    v2_telegram = _repo_text("interface", "v2_telegram.py")
    telegram_reporter = _repo_text("telegram_reporter.py")
    telegram_commander = _repo_text("telegram_commander.py")

    def _func_block(source: str, name: str) -> str:
        match = re.search(rf"^def\s+{re.escape(name)}\b.*?(?=^def\s+|\Z)", source, re.M | re.S)
        return match.group(0) if match else ""

    selection_retry_prompt = _func_block(analysts, "_build_selection_retry_prompt")

    atomic_write_present = (
        ("tmp.replace(self.path)" in broker_truth or "os.replace(tmp, self.path)" in broker_truth)
        and "mask_sensitive" in broker_truth
    )
    token_refresh_helper_present = "_get_balance_with_token_refresh" in trading and bool(
        re.search(r"get_access_token\(\s*force_refresh\s*=\s*True", trading)
    )
    markers = {
        "code.token_refresh_helper": token_refresh_helper_present,
        "code.pathb_startup_recovery": "self.pathb.recover_on_startup()" in trading,
        "code.session_active_attribute": "self.session_active = False" in trading and "self.session_active = True" in trading,
        "code.current_market_attribute": "self.current_market = market" in trading,
        "code.price_cache_clear_on_session_open": "price cache cleared at session_open" in trading,
        "code.partial_buy_exit_runtime": '{"FILLED", "PARTIAL_FILLED"}' in pathb,
        "code.partial_buy_exit_manager": 'status not in {"FILLED", "PARTIAL_FILLED"}' in sell,
        "code.cold_start_brain_fallback": "pathb_cold_start_" in pathb,
        "code.cancel_if_open_above": 'signal.reason == "cancel_if_open_above"' in pathb,
        "code.pathb_runtime_ready": "PathBRuntime" in trading and "self.pathb.recover_on_startup()" in trading,
        "code.pathb_plan_registration": "register_from_selection_meta" in pathb and "price_targets" in pathb and "create_path_run" in adapter,
        "code.pathb_buy_scan": "scan_waiting_entries" in pathb and "cancel_if_open_above" in pathb and "ZONE_EDGE_NO_VALID_LIMIT" in adapter,
        "code.pathb_sell_scan": "scan_exits" in pathb and '{"FILLED", "PARTIAL_FILLED"}' in pathb and "CLOSED_CLAUDE_PRICE_TARGET" in sell,
        "code.pathb_preclose_fallback": "_pre_close_force_exit" in pathb and "PATHB_PRE_CLOSE_MARKET_FALLBACK" in _repo_text("config", "v2.py"),
        "code.pathb_kill_switch": "PATHB_EMERGENCY_DISABLE" in _repo_text("config", "v2.py") and "def emergency_disable" in pathb and "/pathb_kill" in telegram_commander,
        "code.buy_partial_fill_exit_protected": '{"FILLED", "PARTIAL_FILLED"}' in pathb and "mark_partial_filled" in adapter,
        "code.sell_partial_fallback": "mark_sell_partial" in sell and "market_fallback_wait_sec" in sell,
        "code.order_acked_stuck_recovery": "recover_on_startup" in pathb and "ORDER_ACKED" in pathb and "broker" in pathb.lower(),
        "code.pending_order_session_close": "_clear_pending_orders_for_market" in trading and "pending order remained at session_close" in trading,
        "code.path_a_entry_flow_present": "_v2_arbitrate_path_a_entry" in trading and "_v2_safety_decision" in trading and "place_order" in trading,
        "code.path_arbiter_wired": "PathExecutionArbiter" in v2_runtime and "arbitrate_path_a_entry" in trading and "PATHB_ORDER_UNKNOWN_SAME_TICKER" in arbiter,
        "code.same_day_reentry_guard_wired": "SameDayReentryGuard" in v2_runtime and "_v2_same_day_reentry_decision" in trading and "KR_REENTRY_COOLDOWN_MINUTES" in _repo_text("config", "v2.py"),
        "claude.price_targets_required": "price_targets is required for every trade_ready ticker" in analysts,
        "claude.retry_prompt_omits_price_targets": (
            "DO NOT include price_targets in this response" in selection_retry_prompt
            and '"price_targets"' not in selection_retry_prompt
            and "entry_rationale" not in selection_retry_prompt
        ),
        "claude.no_same_session_watch_chase": "Do not promote a ticker to trade_ready solely because it moved after watch_only earlier in the same session" in analysts,
        "dashboard.path_comparison": "pathbCompareChart" in dashboard and "path_comparison" in ops_summary,
        "dashboard.pathb_state_truth": "path_runs_for_session" in ops_summary and "path_b_live" in ops_summary,
        "dashboard.broker_truth_uses_snapshot": "broker_truth" in ops_summary and "load_broker_truth_snapshot" in ops_summary and "pathb-broker-truth" in dashboard,
        "telegram.broker_truth_uses_snapshot": "broker_truth" in v2_telegram and "_cmd_positions_from_broker_truth" in telegram_commander,
        "broker_truth.atomic_write_marker": atomic_write_present,
        "broker_truth.positions_from_broker": "positions" in broker_truth and "normalize_position" in broker_truth,
        "broker_truth.open_orders_from_broker": "open_orders" in broker_truth and "remaining_qty" in broker_truth,
        "broker_truth.today_fills_from_broker": "today_fills" in broker_truth and "filled_qty" in broker_truth,
        "order_unknown.path_a_b_disambiguation": "path_a_origin_possible" in pathb and "_path_a_lifecycle_evidence" in pathb,
        "order_unknown.in_memory_pending_checked": "_path_a_pending_evidence" in pathb and "pending_orders" in pathb,
        "order_unknown.session_recheck_wired": "next_broker_truth_recheck_at" in pathb and "reconcile_order_unknowns" in pathb,
        "order_unknown.session_end_unresolved_marking": "session_end_unresolved" in pathb and "finalize_order_unknowns_at_session_close" in trading,
        "dashboard.order_unknown_visibility": "ORDER_UNKNOWN" in ops_summary and "path_run_id" in ops_summary,
        "dashboard.candidate_funnel": "missing_price_targets" in ops_summary and "registered_plans" in ops_summary and "Claude 입력 후보" in dashboard,
        "telegram.path_label_alerts": "_path_label" in telegram_reporter and "buy_path" in telegram_reporter and "B플랜" in telegram_reporter,
        "telegram.timeout_nonblocking": "getUpdates" in telegram_commander and "timeout=35" in telegram_commander and "폴링 오류" in telegram_commander,
        "session.us_session_date_logic_present": "US는 KST 자정을 넘어도 ET 기준 날짜" in trading and "market == \"US\"" in trading,
        "us.market_session_cutoff": "NEW_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE" in _repo_text("config", "v2.py") and "_pre_close_force_exit" in pathb,
        "us.order_unknown_scope": "block_state(market=market" in v2_runtime and "paused_markets" in _repo_text("execution", "order_state.py"),
    }
    for name, ok in markers.items():
        checks.append(
            CheckResult(
                name,
                "PASS" if ok else "FAIL",
                "marker found" if ok else "marker missing",
                {"category": "code_marker", "guardian_severity": "soft_fail"},
            )
        )

    effective = effective or {}
    patha_timing_enabled = _truthy(effective.get("PATHA_TIMING_LIFECYCLE_ENABLED", "true"))
    timing_runtime_present = "WAIT_TIMING" in trading and "TIMING_EXPIRED" in trading
    timing_enum_present = "WAIT_TIMING" in _repo_text("lifecycle", "models.py") and "TIMING_EXPIRED" in _repo_text("lifecycle", "models.py")
    timing_status = "PASS" if timing_runtime_present or not patha_timing_enabled else ("WARN" if timing_enum_present else "FAIL")
    timing_detail = (
        "Path A WAIT_TIMING/TIMING_EXPIRED runtime markers found"
        if timing_runtime_present
        else "Path A timing lifecycle is explicitly disabled"
        if not patha_timing_enabled
        else ("lifecycle enum supports WAIT_TIMING/TIMING_EXPIRED, but Path A runtime wiring is not proven" if timing_enum_present else "timing lifecycle markers missing")
    )
    checks.append(
        CheckResult(
            "code.wait_timing_recorded",
            timing_status,
            timing_detail,
            {
                "runtime_markers": timing_runtime_present,
                "enum_markers": timing_enum_present,
                "PATHA_TIMING_LIFECYCLE_ENABLED": patha_timing_enabled,
                "category": "code_marker",
                "guardian_severity": "soft_fail",
                "accepted_exception": not timing_runtime_present and not patha_timing_enabled,
                "remediation_required": patha_timing_enabled and not timing_runtime_present,
                "operator_action": (
                    "none; Path A timing lifecycle is explicitly disabled"
                    if not timing_runtime_present and not patha_timing_enabled
                    else
                    "add Path A WAIT_TIMING/TIMING_EXPIRED runtime wiring evidence or explicitly disable this lifecycle state"
                    if not timing_runtime_present
                    else "none"
                ),
            },
        )
    )

    kr_order_block = _func_block(kis, "_build_order_body_kr")
    kr_payload_ok = all(
        marker in kr_order_block
        for marker in (
            "ORD_QTY",
            "str(qty_i)",
            '"ORD_UNPR": "0" if price_i == 0 else str(price_i)',
            '"EXCG_ID_DVSN_CD": "KRX"',
            '"CNDT_PRIC": ""',
        )
    )
    checks.append(
        CheckResult(
            "code.kr_order_payload_normalized",
            "PASS" if kr_payload_ok else "FAIL",
            "KR order qty/price payload is normalized" if kr_payload_ok else "KR order payload normalization marker missing",
        )
    )

    kr_place_block = _func_block(kis, "_place_order_kr")
    kr_recovery_ok = (
        "_find_recent_order_truth_kr" in kr_place_block
        and "retry_skipped=state_unknown" in kr_place_block
        and "retrying once" in kr_place_block
        and "_submit_order_kr_once" in kr_place_block
    )
    blind_kr_retry = "_retry_kis" in kr_place_block
    checks.append(
        CheckResult(
            "code.kr_order_500_recovery_wired",
            "PASS" if kr_recovery_ok and not blind_kr_retry else "FAIL",
            "KR HTTP 500 uses broker truth then one retry" if kr_recovery_ok and not blind_kr_retry else "KR HTTP 500 recovery wiring incomplete or blind retry present",
            {"blind_retry_in_place_order_kr": blind_kr_retry},
        )
    )

    us_place_block = _func_block(kis, "_place_order_us")
    us_recovery_ok = (
        "_find_recent_order_truth_us" in us_place_block
        and "retry_skipped=state_unknown" in us_place_block
        and "retrying once" in us_place_block
        and "_submit_order_us_once" in us_place_block
    )
    blind_us_retry = "_retry_kis" in us_place_block
    checks.append(
        CheckResult(
            "code.us_order_500_recovery_wired",
            "PASS" if us_recovery_ok and not blind_us_retry else "FAIL",
            "US HTTP 500 uses broker truth then one retry" if us_recovery_ok and not blind_us_retry else "US HTTP 500 recovery wiring incomplete or blind retry present",
            {"blind_retry_in_place_order_us": blind_us_retry},
        )
    )

    raw_error_ok = all(
        marker in kis
        for marker in ("_raise_order_http_error", "_response_text", "_mask_order_body", "status_code")
    )
    checks.append(
        CheckResult(
            "code.order_error_raw_response_logged",
            "PASS" if raw_error_ok else "FAIL",
            "order HTTP errors preserve raw response and masked payload" if raw_error_ok else "order HTTP error logging marker missing",
        )
    )

    truth_ok = all(
        marker in kis
        for marker in ("inquire_daily_ccld_kr", "inquire_ccnl_us", "_find_recent_order_truth_kr", "_find_recent_order_truth_us")
    )
    checks.append(
        CheckResult(
            "broker_truth_query_available",
            "PASS" if truth_ok else "FAIL",
            "KR/US broker-truth query helpers are present" if truth_ok else "broker-truth query helper missing",
        )
    )

    us_price_block = _func_block(kis, "_submit_order_us_once")
    checks.append(
        CheckResult(
            "us.order_price_format",
            "PASS" if 'f"{price_f:.2f}"' in us_price_block and "OVRS_ORD_UNPR" in us_price_block else "FAIL",
            "US order price uses two decimal places" if 'f"{price_f:.2f}"' in us_price_block else "US order price format marker missing",
        )
    )

    sell_partial_refs = len(re.findall(r"\bmark_sell_partial\s*\(", pathb + trading + sell))
    status = "WARN" if sell_partial_refs <= 1 else "PASS"
    checks.append(
        CheckResult(
            "code.sell_partial_runtime_wiring",
            status,
            "SELL_PARTIAL_FILLED helper exists but runtime callback wiring is not proven" if status == "WARN" else "sell partial marker wired",
            {"mark_sell_partial_references": sell_partial_refs},
        )
    )

    try:
        import kis_api

        fallback = {str(t).upper() for t in getattr(kis_api, "_US_FALLBACK_UNIVERSE", [])}
        mapped = set()
        for values in getattr(kis_api, "_US_EXCHANGE_MAP", {}).values():
            mapped.update(str(t).upper() for t in values)
        missing = sorted(fallback - mapped)
        checks.append(
            CheckResult(
                "code.us_exchange_map_coverage",
                "FAIL" if missing else "PASS",
                f"missing US exchange mappings: {missing}" if missing else "US fallback universe exchange mappings are complete",
                {"missing": missing, "fallback_count": len(fallback)},
            )
        )
        checks.append(
            CheckResult(
                "us.exchange_map_coverage",
                "FAIL" if missing else "PASS",
                f"missing US exchange mappings: {missing}" if missing else "US fallback universe exchange mappings are complete",
                {"missing": missing, "fallback_count": len(fallback)},
            )
        )
    except Exception as exc:
        checks.append(CheckResult("code.us_exchange_map_coverage", "FAIL", f"cannot inspect US exchange map: {exc}"))
        checks.append(CheckResult("us.exchange_map_coverage", "FAIL", f"cannot inspect US exchange map: {exc}"))

    checks.append(_market_session_calendar_check())
    return checks


def _pathb_feature_checks() -> list[CheckResult]:
    checks: list[CheckResult] = []
    from config.v2 import V2Config
    from decision.claude_price_plan import make_price_plan
    from execution.claude_price_adapter import ClaudePriceAdapter
    from execution.claude_price_sell_manager import ClaudePriceSellManager
    from execution.safety_gate import PathBSafetyGate, SafetyContext
    from lifecycle.event_store import EventStore

    with tempfile.TemporaryDirectory() as tmp:
        cfg = V2Config(pathb_fixed_order_krw=100_000, pathb_max_positions=1, pathb_max_daily_entries=1)
        store = EventStore(Path(tmp) / "events.db")
        adapter = ClaudePriceAdapter(store, cfg)
        plan = make_price_plan(
            decision_id="dec1",
            ticker="005930",
            market="KR",
            session_date="2026-04-27",
            buy_zone_low=52_000,
            buy_zone_high=52_500,
            sell_target=54_500,
            stop_loss=51_000,
            hold_days=1,
            confidence=0.7,
            cancel_if_open_above=53_000,
        )
        path_run_id = adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
        cancel = adapter.check_entry(path_run_id, 53_100)
        checks.append(CheckResult("pathb.cancel_if_open_above", "PASS" if cancel.reason == "cancel_if_open_above" else "FAIL", cancel.reason))

        edge = adapter.compute_buy_limit("KR", 51_200, 51_000)
        checks.append(CheckResult("pathb.limit_edge_guard", "PASS" if edge < 51_200 else "FAIL", "limit below current should be blocked by check_entry", {"computed_limit": edge}))

        ctx = SafetyContext(
            market="KR",
            runtime_mode="live",
            ticker="005930",
            price_krw=200_000,
            qty=0,
            order_cost_krw=0,
            cash_krw=100_000,
            min_order_krw=100_000,
            market_open=True,
            broker_trust_level="trusted",
        )
        decision = PathBSafetyGate(cfg).evaluate(ctx, plan=plan)
        checks.append(CheckResult("pathb.qty_zero_blocks", "PASS" if not decision.passed and decision.reason_code == "INVALID_QTY" else "FAIL", decision.reason_code))

        adapter.mark_partial_filled(path_run_id, price=52_200, qty=1, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
        exit_signal = ClaudePriceSellManager(adapter, cfg).check_exit(path_run_id, 50_900)
        checks.append(
            CheckResult(
                "pathb.partial_buy_exit_protected",
                "PASS" if exit_signal.signal and exit_signal.close_reason == "CLOSED_CLAUDE_PRICE_STOP" else "FAIL",
                exit_signal.close_reason or exit_signal.reason,
            )
        )
    return checks


def _dashboard_checks() -> list[CheckResult]:
    try:
        from dashboard.dashboard_server import app
    except Exception as exc:
        return [CheckResult("dashboard.import", "FAIL", f"dashboard import failed: {exc}")]
    client = app.test_client()
    checks: list[CheckResult] = []
    try:
        page = client.get("/pathb")
        body = page.get_data(as_text=True)
        ok = page.status_code == 200 and all(
            marker in body
            for marker in ("pathbPnlChart", "pathbOutcomeChart", "pathbStatusChart", "pathbCompareChart")
        )
        checks.append(CheckResult("dashboard.pathb_page", "PASS" if ok else "FAIL", f"status={page.status_code}"))
    except Exception as exc:
        checks.append(CheckResult("dashboard.pathb_page", "FAIL", f"/pathb crashed: {exc}"))
    try:
        api = client.get("/api/v2/ops?market=KR&mode=live")
        data = api.get_json(silent=True) or {}
        ok = api.status_code == 200 and data.get("ok") is True and "path_b_live" in data and "broker_truth" in data
        checks.append(CheckResult("dashboard.pathb_api", "PASS" if ok else "FAIL", f"status={api.status_code}", {"keys": sorted(data.keys())}))
        pathb_live = data.get("path_b_live") or {}
        comparison = pathb_live.get("path_comparison") or {}
        cmp_ok = bool(comparison.get("path_a")) and bool(comparison.get("path_b"))
        checks.append(
            CheckResult(
                "dashboard.path_comparison",
                "PASS" if cmp_ok else "FAIL",
                "A/B path comparison is exposed" if cmp_ok else "path comparison missing from ops API",
                {"comparison_keys": sorted(comparison.keys())},
            )
        )
        selection = pathb_live.get("selection") or {}
        counts = selection.get("counts") or {}
        funnel_ok = all(key in counts for key in ("universe", "watchlist", "raw_trade_ready", "applied_trade_ready", "price_targets", "registered_plans"))
        checks.append(
            CheckResult(
                "dashboard.candidate_funnel_runtime",
                "PASS" if funnel_ok else "WARN",
                "candidate funnel is exposed" if funnel_ok else "candidate funnel counts are incomplete",
                {"counts": counts},
            )
        )
        broker_truth = data.get("broker_truth") or {}
        broker_markets = broker_truth.get("markets") if isinstance(broker_truth.get("markets"), dict) else {}
        checks.append(
            CheckResult(
                "dashboard.broker_truth_api",
                "PASS" if all(m in broker_markets for m in ("KR", "US")) else "FAIL",
                "broker truth snapshot is exposed through ops API" if all(m in broker_markets for m in ("KR", "US")) else "broker truth missing from ops API",
                {"markets": sorted(broker_markets.keys()) if isinstance(broker_markets, dict) else []},
            )
        )
    except Exception as exc:
        checks.append(CheckResult("dashboard.pathb_api", "FAIL", f"/api/v2/ops crashed: {exc}"))
    return checks


def _telegram_checks() -> list[CheckResult]:
    checks: list[CheckResult] = []
    commander_text = _repo_text("telegram_commander.py")
    core_commands = ["/status", "/health", "/positions", "/errors"]
    core_missing = [cmd for cmd in core_commands if cmd not in commander_text and (cmd != "/positions" or "/pos" not in commander_text)]
    checks.append(
        CheckResult(
            "telegram.core_commands",
            "FAIL" if core_missing else "PASS",
            f"missing core command markers: {core_missing}" if core_missing else "core Telegram command markers are present",
            {"missing": core_missing},
        )
    )
    try:
        from telegram_reporter import _path_label

        label_ok = _path_label("path_a").startswith("A플랜") and _path_label("path_b").startswith("B플랜")
        checks.append(
            CheckResult(
                "telegram.path_label_alerts",
                "PASS" if label_ok else "FAIL",
                "A/B path labels render in alerts" if label_ok else "A/B path labels are wrong",
                {"path_a": _path_label("path_a"), "path_b": _path_label("path_b")},
            )
        )
    except Exception as exc:
        checks.append(CheckResult("telegram.path_label_alerts", "FAIL", f"path label import/check failed: {exc}"))

    try:
        from interface.v2_telegram import handle_v2_command
    except Exception as exc:
        checks.append(CheckResult("telegram.import", "FAIL", f"telegram command import failed: {exc}"))
        return checks

    class _Risk:
        halted = False
        halt_reason = ""
        cash = 0
        positions: list[dict[str, Any]] = []

        def equity(self) -> float:
            return 0.0

        def daily_return(self) -> float:
            return 0.0

    class _PathB:
        def status(self) -> dict[str, Any]:
            return {
                "enabled": True,
                "operator_enabled": True,
                "emergency_disabled": False,
                "mode": "min_size_live",
                "runtime_mode": "live",
                "fixed_order_krw": 100000,
                "max_positions": 10,
                "max_daily_entries": 10,
                "min_confidence": 0.5,
            }

        def set_enabled(self, enabled: bool, *, updated_by: str, reason: str) -> None:
            return None

        def emergency_disable(self, *, updated_by: str, reason: str) -> None:
            return None

        def close_all_open(self, market: str, *, reason: str) -> int:
            return 0

    class _Bot:
        risk = _Risk()
        pathb = _PathB()
        pending_orders: list[dict[str, Any]] = []
        current_market = "KR"

    commands = ["/health", "/errors", "/pathb_status", "/pathb_on", "/pathb_off", "/pathb_kill", "/pathb_closeall"]
    failures: dict[str, str] = {}
    for command in commands:
        try:
            response = handle_v2_command(command, _Bot())
            if not response:
                failures[command] = "empty response"
            if command == "/health" and "broker_truth" not in response:
                failures[command] = "broker_truth status missing"
        except Exception as exc:
            failures[command] = str(exc)
    checks.append(
        CheckResult(
            "telegram.pathb_commands",
            "FAIL" if failures else "PASS",
            "V2/Path B Telegram commands responded" if not failures else "V2/Path B Telegram command failures",
            {"failures": failures},
        )
    )
    checks.append(
        CheckResult(
            "telegram.positions_uses_broker_snapshot",
            "PASS" if "_cmd_positions_from_broker_truth" in commander_text and "build_v2_ops_summary" in commander_text else "FAIL",
            "/positions uses broker truth snapshot first" if "_cmd_positions_from_broker_truth" in commander_text else "/positions broker truth helper missing",
        )
    )
    return checks


def _ops_summary_checks(mode: str) -> list[CheckResult]:
    checks: list[CheckResult] = []
    try:
        from interface.v2_ops_summary import build_v2_ops_summary
    except Exception as exc:
        return [CheckResult("ops_summary.import", "FAIL", f"ops summary import failed: {exc}")]

    for market in ("KR", "US"):
        try:
            summary = build_v2_ops_summary(market=market, runtime_mode=mode, session_date=_session_date_guess(market))
            pathb = summary.get("path_b_live") or {}
            comparison = pathb.get("path_comparison") or {}
            lifecycle = summary.get("lifecycle") or {}
            broker_truth = summary.get("broker_truth") or {}
            readiness = pathb.get("readiness") if isinstance(pathb.get("readiness"), dict) else {}
            live_truth = pathb.get("live_truth_verdict") if isinstance(pathb.get("live_truth_verdict"), dict) else {}
            capacity = pathb.get("execution_capacity") if isinstance(pathb.get("execution_capacity"), dict) else {}
            # lifecycle.order_unknown is a raw event history; recovered rows can
            # still have an old ORDER_UNKNOWN event. For preflight review, use
            # unresolved Path B rows from the Path B summary.
            unknown = pathb.get("order_unknown") or []
            closed = lifecycle.get("closed") or []
            checks.append(
                CheckResult(
                    f"{market.lower()}.today_order_unknown_review",
                    "WARN" if unknown else "PASS",
                    f"{market} ORDER_UNKNOWN rows={len(unknown)}" if unknown else f"{market} has no ORDER_UNKNOWN rows in ops summary",
                    {"rows": unknown[-20:]},
                )
            )
            checks.append(
                CheckResult(
                    f"{market.lower()}.broker_truth_summary",
                    "PASS" if market in (broker_truth.get("markets") or {}) else "FAIL",
                    f"{market} broker truth summary exposed" if market in (broker_truth.get("markets") or {}) else f"{market} broker truth summary missing",
                    {"market_data": (broker_truth.get("markets") or {}).get(market, {})},
                )
            )
            readiness_state = str(readiness.get("state") or "")
            readiness_status = "WARN" if readiness_state.startswith("BLOCKED_") else "PASS"
            checks.append(
                CheckResult(
                    f"{market.lower()}.pathb_execution_readiness",
                    readiness_status,
                    f"{market} Path B readiness={readiness_state or 'missing'}",
                    {
                        "readiness": readiness,
                        "live_truth_verdict": live_truth.get(market, {}),
                        "execution_capacity": capacity.get(market, {}),
                        "operator_action": (
                            "review blocked Path B readiness before expecting live orders"
                            if readiness_status == "WARN"
                            else "none"
                        ),
                    },
                )
            )
            checks.append(
                CheckResult(
                    f"{market.lower()}.closed_positions_review",
                    "PASS",
                    f"{market} closed rows={len(closed)}",
                    {"rows": closed[-20:]},
                )
            )
            if market == "KR":
                pathb_closed = int(((comparison.get("path_b") or {}).get("closed") or 0))
                checks.append(
                    CheckResult(
                        "kr.pathb_no_closed_explained",
                        "PASS",
                        "Path B closed count is derived from v2_path_runs/path_comparison",
                        {"path_b_closed": pathb_closed, "comparison": comparison},
                    )
                )
        except Exception as exc:
            checks.append(CheckResult(f"{market.lower()}.ops_summary", "FAIL", f"{market} ops summary failed: {exc}"))
    return checks


def _price_csv_active_universe(market: str, mode: str = "live") -> dict[str, Any]:
    market_key = str(market or "").upper()
    tickers: set[str] = set()
    sources: dict[str, list[str]] = {"trade_ready": [], "open_positions": [], "pending_orders": []}
    selection_date = ""
    db_path = ROOT / "data" / "ticker_selection_log.db"
    if db_path.exists():
        conn = None
        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            row = conn.execute(
                "SELECT MAX(date) FROM ticker_selection_log WHERE market=?",
                (market_key,),
            ).fetchone()
            selection_date = str((row or [""])[0] or "")
            if selection_date:
                rows = conn.execute(
                    """
                    SELECT ticker FROM ticker_selection_log
                    WHERE market=? AND date=? AND COALESCE(trade_ready, 0)=1
                    """,
                    (market_key, selection_date),
                ).fetchall()
                for row in rows:
                    ticker = _ticker_key(market_key, row[0])
                    if ticker:
                        tickers.add(ticker)
                        sources["trade_ready"].append(ticker)
        except Exception:
            pass
        finally:
            if conn is not None:
                conn.close()
    for source_name, filename in (
        ("open_positions", f"{mode}_open_positions.json"),
        ("pending_orders", f"{mode}_pending_orders.json"),
    ):
        for item in _read_json_list(get_runtime_path("state", filename, make_parents=False)):
            item_market = str(item.get("market") or "").upper()
            if item_market and item_market != market_key:
                continue
            ticker = _ticker_key(market_key, item.get("ticker"))
            if ticker:
                tickers.add(ticker)
                sources[source_name].append(ticker)
    return {
        "market": market_key,
        "selection_date": selection_date,
        "tickers": sorted(tickers),
        "sources": {key: sorted(set(value)) for key, value in sources.items()},
    }


def _price_csv_checks() -> list[CheckResult]:
    checks: list[CheckResult] = []
    try:
        import exchange_calendars as ec

        ec.get_calendar("XKRX")
        ec.get_calendar("XNYS")
        checks.append(
            CheckResult(
                "calendar.exchange_calendars",
                "PASS",
                "ok XKRX/XNYS",
                {"calendars": ["XKRX", "XNYS"]},
            )
        )
    except Exception as exc:
        checks.append(
            CheckResult(
                "calendar.exchange_calendars",
                "WARN",
                "weekday_fallback active; KR holiday freshness threshold relaxed",
                {"error": str(exc)},
            )
        )
    try:
        from runtime.price_csv_health import price_csv_health_summary
    except Exception as exc:
        return [CheckResult("price_csv.import", "FAIL", f"price CSV health import failed: {exc}")]

    def _sample_tickers(summary: dict[str, Any], key: str) -> set[str]:
        samples = summary.get("samples") if isinstance(summary.get("samples"), dict) else {}
        rows = samples.get(key) if isinstance(samples.get(key), list) else []
        return {str(item.get("ticker") or "").strip() for item in rows if isinstance(item, dict) and item.get("ticker")}

    def _status_tickers(summary: dict[str, Any], key: str) -> set[str]:
        status_tickers = summary.get("status_tickers") if isinstance(summary.get("status_tickers"), dict) else {}
        rows = status_tickers.get(key)
        if isinstance(rows, list):
            return {str(item or "").strip() for item in rows if str(item or "").strip()}
        return _sample_tickers(summary, key)

    for market in ("KR", "US"):
        active_universe = _price_csv_active_universe(market)
        active_tickers = set(active_universe.get("tickers") or [])
        try:
            summary = price_csv_health_summary(ROOT, market, include_tickers=active_tickers)
        except Exception as exc:
            checks.append(CheckResult(f"price_csv.{market.lower()}", "FAIL", f"price CSV health failed: {exc}"))
            continue
        counts = summary.get("counts", {})
        malformed = int(counts.get("malformed_csv", 0))
        missing = int(counts.get("missing_csv", 0))
        stale = int(counts.get("stale_csv", 0))
        ohlc_error_csv = int(counts.get("ohlc_logic_error_csv", 0))
        ohlc_error_rows = int(counts.get("ohlc_logic_error_rows", 0))
        latest_ohlc_error_csv = int(counts.get("latest_ohlc_logic_error_csv", 0))
        flat_zero_csv = int(counts.get("flat_ohlc_zero_volume_csv", 0))
        flat_zero_rows = int(counts.get("flat_ohlc_zero_volume_rows", 0))
        latest_flat_zero_csv = int(counts.get("latest_flat_ohlc_zero_volume_csv", 0))
        too_few_rows_csv = int(counts.get("too_few_rows_csv", 0))
        quality_tickers = summary.get("quality_tickers") if isinstance(summary.get("quality_tickers"), dict) else {}
        active_latest_flat_zero = sorted(
            active_tickers & {str(item) for item in (quality_tickers.get("latest_flat_ohlc_zero_volume") or [])}
        )
        active_too_few_rows = sorted(
            active_tickers & {str(item) for item in (quality_tickers.get("too_few_rows") or [])}
        )
        active_missing = sorted(active_tickers & _status_tickers(summary, "missing_csv"))
        active_malformed = sorted(active_tickers & _status_tickers(summary, "malformed_csv"))
        active_stale = sorted(active_tickers & _status_tickers(summary, "stale_csv"))
        active_ohlc_error = sorted(
            active_tickers & {str(item) for item in (quality_tickers.get("ohlc_logic_error") or [])}
        )
        active_latest_ohlc_error = sorted(
            active_tickers & {str(item) for item in (quality_tickers.get("latest_ohlc_logic_error") or [])}
        )
        summary["active_universe"] = active_universe
        summary["active_quality_issues"] = {
            "missing_csv": active_missing,
            "malformed_csv": active_malformed,
            "stale_csv": active_stale,
            "ohlc_logic_error": active_ohlc_error,
            "latest_ohlc_logic_error": active_latest_ohlc_error,
            "latest_flat_ohlc_zero_volume": active_latest_flat_zero,
            "too_few_rows": active_too_few_rows,
        }
        active_blocking_issues = {
            key: value
            for key, value in summary["active_quality_issues"].items()
            if value
        }
        summary["active_blocking_issues"] = active_blocking_issues
        summary["execution_blocking"] = False
        summary["impact_area"] = "price_history_quality"
        summary["operator_action"] = "none"
        total = int(summary.get("total", 0))
        fresh_ratio = float(summary.get("fresh_ratio", 0.0))
        freshness_detail = (
            f"total={total} fresh={summary.get('fresh_count', 0)} "
            f"fresh_ratio={fresh_ratio:.1%} stale={stale} "
            f"expected_last={summary.get('expected_last_date', '')} "
            f"last_range={summary.get('oldest_last_date', '')}..{summary.get('newest_last_date', '')}"
        )
        freshness_status = "PASS" if total and fresh_ratio >= 0.95 else "WARN"
        checks.append(CheckResult(f"data.price_csv_freshness.{market.lower()}", freshness_status, freshness_detail, summary))
        integrity_detail = (
            f"total={total} malformed={malformed} missing={missing} "
            f"ohlc_error_csv={ohlc_error_csv} ohlc_error_rows={ohlc_error_rows} "
            f"latest_ohlc_error_csv={latest_ohlc_error_csv} "
            f"flat_ohlc_zero_volume_csv={flat_zero_csv} flat_ohlc_zero_volume_rows={flat_zero_rows} "
            f"latest_flat_ohlc_zero_volume_csv={latest_flat_zero_csv} too_few_rows_csv={too_few_rows_csv} "
            f"active_blocking_issues={sum(len(value) for value in active_blocking_issues.values())} "
            f"active_latest_flat_zero={len(active_latest_flat_zero)} active_too_few_rows={len(active_too_few_rows)}"
        )
        integrity_status = (
            "FAIL"
            if total == 0 or active_blocking_issues
            else "WARN"
            if malformed or missing or ohlc_error_csv or flat_zero_csv or too_few_rows_csv
            else "PASS"
        )
        summary["execution_blocking"] = bool(total == 0 or active_blocking_issues)
        summary["impact_area"] = (
            "execution_blocking_active_universe"
            if active_blocking_issues
            else "execution_blocking_no_price_files"
            if total == 0
            else "data_quality_nonblocking"
            if integrity_status == "WARN"
            else "healthy"
        )
        summary["data_quality_warning"] = integrity_status == "WARN"
        summary["blocked_if_live_start"] = bool(total == 0 or active_blocking_issues)
        summary["operator_action"] = (
            "backfill or repair active-universe price CSV before trusting live execution checks"
            if active_blocking_issues
            else "create price CSV history before live execution checks"
            if total == 0
            else "schedule data cleanup/backfill; not blocking active execution"
            if integrity_status == "WARN"
            else "none"
        )
        checks.append(CheckResult(f"data.price_csv_integrity.{market.lower()}", integrity_status, integrity_detail, summary))
    return checks


def _candidate_audit_outcome_checks(mode: str) -> list[CheckResult]:
    path = get_runtime_path("data", "audit", "candidate_audit.db", make_parents=False)
    if not path.exists():
        return [CheckResult("candidate_audit.outcome_update", "WARN", f"candidate audit DB missing: {path}")]
    conn = None
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "audit_candidate_outcomes" not in tables:
            return [CheckResult("candidate_audit.outcome_update", "WARN", "audit_candidate_outcomes table missing")]
        total, latest = conn.execute(
            "SELECT COUNT(*), MAX(label_generated_at) FROM audit_candidate_outcomes"
        ).fetchone()
        rows = conn.execute(
            """
            SELECT horizon_min, COALESCE(NULLIF(status, ''), 'unknown') AS status,
                   COALESCE(NULLIF(source, ''), 'unknown') AS source,
                   COUNT(*) AS rows,
                   MAX(label_generated_at) AS latest_label_generated_at
            FROM audit_candidate_outcomes
            GROUP BY horizon_min, status, source
            ORDER BY horizon_min, status, source
            """
        ).fetchall()
    except Exception as exc:
        return [CheckResult("candidate_audit.outcome_update", "WARN", f"candidate outcome check failed: {exc}")]
    finally:
        if conn is not None:
            conn.close()

    horizon_status: list[dict[str, Any]] = []
    daily_pending_rows = 0
    insufficient_rows = 0
    for row in rows:
        item = {
            "horizon_min": int(row[0] or 0),
            "status": str(row[1] or "unknown"),
            "source": str(row[2] or "unknown"),
            "rows": int(row[3] or 0),
            "latest_label_generated_at": row[4] or "",
        }
        horizon_status.append(item)
        if item["status"] == "daily_pending":
            daily_pending_rows += item["rows"]
        if item["status"] == "insufficient_samples":
            insufficient_rows += item["rows"]

    has_rows = int(total or 0) > 0 and bool(latest)
    status = "PASS" if has_rows and daily_pending_rows == 0 else "WARN"
    status_parts = [
        f"{item['horizon_min']}m:{item['status']}={item['rows']}"
        for item in horizon_status[:8]
    ]
    detail = (
        f"outcome_rows={int(total or 0)} latest_label_generated_at={latest or ''} "
        f"daily_pending_rows={daily_pending_rows} insufficient_rows={insufficient_rows}"
    )
    if status_parts:
        detail = f"{detail}; status_counts={', '.join(status_parts)}"
    data = {
        "path": str(path),
        "rows": int(total or 0),
        "latest": latest or "",
        "horizon_status": horizon_status,
        "daily_pending_rows": daily_pending_rows,
        "insufficient_sample_rows": insufficient_rows,
    }
    if status == "WARN":
        data.update(
            _warning_meta(
                "candidate_audit_outcome_freshness",
                accepted=True,
                action="refresh candidate audit outcomes before using daily forward audit as learning evidence",
            )
        )
    return [CheckResult("candidate_audit.outcome_update", status, detail, data)]


def _ticker_selection_attribution_checks(mode: str) -> list[CheckResult]:
    path = get_runtime_path("data", "ticker_selection_log.db", make_parents=False)
    if not path.exists():
        return [CheckResult("ticker_selection.execution_attribution", "WARN", f"ticker selection DB missing: {path}")]
    conn = None
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "ticker_selection_log" not in tables:
            return [CheckResult("ticker_selection.execution_attribution", "WARN", "ticker_selection_log table missing")]
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(ticker_selection_log)")}
        if "execution_decision_id" not in columns:
            return [CheckResult("ticker_selection.execution_attribution", "WARN", "execution_decision_id column missing")]
        rows = conn.execute(
            """
            SELECT market,
                   COUNT(*) AS traded_rows,
                   SUM(CASE WHEN COALESCE(trade_ready, 0)=0 THEN 1 ELSE 0 END) AS watch_only_traded_rows,
                   SUM(CASE WHEN execution_decision_id IS NULL OR TRIM(execution_decision_id)='' THEN 1 ELSE 0 END) AS missing_execution_decision_id_rows,
                   SUM(CASE WHEN execution_decision_id IS NOT NULL AND TRIM(execution_decision_id)!='' THEN 1 ELSE 0 END) AS linked_execution_decision_id_rows,
                   MIN(date) AS min_date,
                   MAX(date) AS max_date
            FROM ticker_selection_log
            WHERE bot_mode=? AND traded=1
            GROUP BY market
            ORDER BY market
            """,
            (mode,),
        ).fetchall()
    except Exception as exc:
        return [CheckResult("ticker_selection.execution_attribution", "WARN", f"selection attribution check failed: {exc}")]
    finally:
        if conn is not None:
            conn.close()

    markets = []
    traded_total = 0
    missing_total = 0
    watch_only_traded_total = 0
    for row in rows:
        traded_rows = int(row[1] or 0)
        watch_only_rows = int(row[2] or 0)
        missing_rows = int(row[3] or 0)
        linked_rows = int(row[4] or 0)
        traded_total += traded_rows
        missing_total += missing_rows
        watch_only_traded_total += watch_only_rows
        markets.append(
            {
                "market": str(row[0] or ""),
                "traded_rows": traded_rows,
                "watch_only_traded_rows": watch_only_rows,
                "missing_execution_decision_id_rows": missing_rows,
                "linked_execution_decision_id_rows": linked_rows,
                "min_date": row[5] or "",
                "max_date": row[6] or "",
            }
        )

    status = "PASS" if missing_total == 0 and watch_only_traded_total == 0 else "WARN"
    detail = (
        f"traded_rows={traded_total} missing_execution_decision_id={missing_total} "
        f"watch_only_traded_rows={watch_only_traded_total}"
    )
    data = {
        "path": str(path),
        "mode": mode,
        "markets": markets,
        "traded_rows": traded_total,
        "missing_execution_decision_id_rows": missing_total,
        "watch_only_traded_rows": watch_only_traded_total,
    }
    if status == "WARN":
        data.update(
            _warning_meta(
                "ticker_selection_execution_attribution_gap",
                accepted=True,
                action="repair selection-to-v2 decision linkage before using traded selection rows for learning attribution",
            )
        )
    return [CheckResult("ticker_selection.execution_attribution", status, detail, data)]


def _ml_db_health_checks(mode: str) -> list[CheckResult]:
    path = ROOT / "data" / "ml" / "decisions.db"
    try:
        from ml import db_health

        result = db_health.check_db_health(path, read_only=True)
    except Exception as exc:
        return [CheckResult("ml.decisions_db_health", "FAIL", f"ML DB health check failed: {exc}", {"path": str(path)})]
    if not result.get("exists"):
        return [CheckResult("ml.decisions_db_health", "WARN", "decisions.db missing", {"path": str(path)})]
    gaps = result.get("gaps", {}) if isinstance(result.get("gaps"), dict) else {}
    warnings = [str(item) for item in result.get("warnings", [])]
    errors = [str(item) for item in result.get("errors", [])]
    try:
        from ml.db_writer import schema_missing_columns

        schema_missing = schema_missing_columns(path)
    except Exception as exc:
        schema_missing = {"schema_check": [f"{type(exc).__name__}: {exc}"]}
    status = "FAIL" if errors else ("WARN" if warnings or schema_missing else "PASS")
    detail = (
        f"rows={result.get('total_rows')} live={result.get('live_rows')} "
        f"known_gaps={len(gaps.get('known_unrecoverable_ranges') or [])} "
        f"rows_inside_known_gap={gaps.get('rows_inside_known_gap', 0)}"
    )
    if errors:
        detail = f"{detail}; errors={', '.join(errors)}"
    elif schema_missing:
        detail = f"{detail}; missing_schema_columns={schema_missing}"
    elif warnings:
        detail = f"{detail}; warnings={', '.join(warnings)}"
    result = dict(result)
    result["schema_missing_columns"] = schema_missing
    if schema_missing:
        result.update(
            _warning_meta(
                "ml_schema_missing",
                accepted=False,
                action="run python -c \"from ml.db_writer import init_db; init_db()\" before live start",
                blocked_if_live_start=(mode == "live"),
            )
        )
    elif status == "WARN":
        result.update(_warning_meta("known_data_gap", accepted=True))
    return [CheckResult("ml.decisions_db_health", status, detail, result)]


def _external_data_readiness_checks(config: dict[str, Any]) -> list[CheckResult]:
    effective = config.get("effective", {}) if isinstance(config, dict) else {}
    required = _truthy(effective.get("EXTERNAL_DATA_REQUIRED") or os.getenv("EXTERNAL_DATA_REQUIRED", ""))
    try:
        from phase1_trainer.external_data_store import DEFAULT_DB_PATH, ExternalDataStore

        summary = ExternalDataStore(DEFAULT_DB_PATH).readiness_summary(initialize=False)
    except Exception as exc:
        status = "FAIL" if required else "WARN"
        return [CheckResult("external_data.readiness", status, f"external data readiness check failed: {exc}", {"required": required})]
    ready = bool(summary.get("production_ready"))
    status = "PASS" if ready else ("FAIL" if required else "WARN")
    detail = (
        f"production_ready={ready} total_data_rows={summary.get('total_data_rows', 0)} "
        f"latest_api_run_at={summary.get('latest_api_run_at', '')}"
    )
    data = dict(summary)
    data["required"] = required
    data["data_quality_flags"] = [] if ready else ["external_data_empty"]
    if not ready and not required:
        data.update(_warning_meta("accepted_missing_optional_data", accepted=True))
    return [CheckResult("external_data.readiness", status, detail, data)]


def _warning_classification_counts(checks: list[CheckResult]) -> dict[str, int]:
    warnings = [check for check in checks if check.status == "WARN"]
    accepted = 0
    action_required = 0
    data_quality = 0
    blocked_if_live_start = 0
    for check in warnings:
        data = check.data if isinstance(check.data, dict) else {}
        if bool(data.get("accepted_exception")):
            accepted += 1
        if bool(data.get("operator_action_required") or data.get("remediation_required")) and not bool(data.get("accepted_exception")):
            action_required += 1
        if bool(data.get("data_quality_warning")) or str(data.get("warning_kind") or "").startswith("data"):
            data_quality += 1
        if bool(data.get("blocked_if_live_start")):
            blocked_if_live_start += 1
    return {
        "accepted_warn_count": accepted,
        "action_required_warn_count": action_required,
        "data_quality_warn_count": data_quality,
        "blocked_if_live_start_warn_count": blocked_if_live_start,
    }


def run_preflight(mode: str = "live", *, allow_config_conflicts: bool = False, include_dashboard: bool = True) -> dict[str, Any]:
    checks: list[CheckResult] = []
    config_checks, config = _config_checks(mode, allow_config_conflicts)
    checks.extend(config_checks)
    checks.extend(_kis_network_checks(config, mode))
    checks.extend(_db_checks(mode))
    checks.extend(_token_checks(mode))
    checks.extend(_state_checks(config, mode))
    checks.append(_process_inventory_check(mode))
    checks.extend(_heartbeat_checks(mode))
    checks.extend(_static_code_checks(config.get("effective", {})))
    checks.extend(_pathb_feature_checks())
    if include_dashboard:
        checks.extend(_dashboard_checks())
    checks.extend(_telegram_checks())
    checks.extend(_price_csv_checks())
    checks.extend(_ml_db_health_checks(mode))
    checks.extend(_external_data_readiness_checks(config))
    checks.extend(_candidate_audit_outcome_checks(mode))
    checks.extend(_ticker_selection_attribution_checks(mode))
    checks.extend(_ops_summary_checks(mode))
    fail_count = sum(1 for check in checks if check.status == "FAIL")
    warn_count = sum(1 for check in checks if check.status == "WARN")
    warning_counts = _warning_classification_counts(checks)
    report = {
        "ok": fail_count == 0,
        "mode": mode,
        "generated_at": _now_kst().isoformat(timespec="seconds"),
        "fail_count": fail_count,
        "warn_count": warn_count,
        **warning_counts,
        "checks": [asdict(check) for check in checks],
        "effective_config": {
            key: config["effective"].get(key, "")
            for key in sorted(LIVE_CONFIG_KEYS)
            if key in config["effective"]
        },
    }
    return report


def _write_report(report: dict[str, Any]) -> tuple[Path, Path]:
    out_dir = ROOT / "data" / "v2_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now_kst().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"live_preflight_{stamp}.json"
    md_path = out_dir / f"live_preflight_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# Live Preflight Report {stamp}",
        "",
        f"- ok: {report['ok']}",
        f"- mode: {report['mode']}",
        f"- fail_count: {report['fail_count']}",
        f"- warn_count: {report['warn_count']}",
        f"- accepted_warn_count: {report.get('accepted_warn_count', 0)}",
        f"- action_required_warn_count: {report.get('action_required_warn_count', 0)}",
        "",
        "## Checks",
        "",
    ]
    for check in report["checks"]:
        lines.append(f"- {check['status']} `{check['name']}` - {check['detail']}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Live preflight for V2/Path B production operation.")
    parser.add_argument("--mode", default="live", choices=["live", "paper"])
    parser.add_argument("--allow-config-conflicts", action="store_true")
    parser.add_argument("--skip-dashboard", action="store_true")
    parser.add_argument("--json", action="store_true", dest="print_json")
    args = parser.parse_args()

    report = run_preflight(
        args.mode,
        allow_config_conflicts=args.allow_config_conflicts,
        include_dashboard=not args.skip_dashboard,
    )
    json_path, md_path = _write_report(report)
    if args.print_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"ok={report['ok']} fail={report['fail_count']} warn={report['warn_count']}")
        print(f"json={json_path}")
        print(f"md={md_path}")
        for check in report["checks"]:
            if check["status"] != "PASS":
                print(f"{check['status']} {check['name']}: {check['detail']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
