"""Turn daily lessons into reviewable engineering actions.

This registry is deliberately outside the trading authority path.  It may
classify evidence and track forward-validation contracts, but it must never
change prompts, configuration, sizing, orders, or exit policy.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from risk_manager import ISOLATED_STRATEGY_SOURCES
from runtime_paths import get_runtime_path


SCHEMA_VERSION = "lesson_action_registry.v1"
AUTHORITY = "OPS_ONLY_NO_TRADE_AUTHORITY"
ROOT_CAUSES = frozenset(
    {
        "EXIT_OWNER",
        "EXIT_POLICY",
        "ENTRY_PIPELINE",
        "DATA_QUALITY",
        "EXECUTION",
        "MARKET_FORECAST",
        "REPORTING",
        "OBSERVATION",
    }
)

_LOCK = threading.Lock()
_GENERIC_EXIT_TOKENS = (
    "intraday_review",
    "post_session_review",
    "pre_session_review",
    "next_open",
    "hold_advisor",
    "claude_sell",
    "auto_sell_review",
)
_SAFETY_EXIT_TOKENS = (
    "hard_stop",
    "loss_cap",
    "system_guard",
    "broker_liquidation",
)
_OPS_METRIC_KEYS = (
    "consensus_directional_hit_rate",
    "trade_ready_signal_conversion",
    "trade_ready_signal_conversion_row_basis",
    "watch_only_missed_runup_ratio",
    "watch_only_missed_runup_ratio_row_basis",
    "trade_ready_forward_3d_average",
    "entry_blackout_ratio",
    "watch_only_blocked_ratio",
    "decision_id_fallback_count",
    "lifecycle_high_gap_count",
    "exit_lifecycle_bypass_ratio",
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _registry_path() -> Path:
    return get_runtime_path("state", "lesson_action_registry.json")


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _metric_snapshot(ops_review_snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metrics = (ops_review_snapshot or {}).get("metrics") or {}
    output: dict[str, dict[str, Any]] = {}
    for key in _OPS_METRIC_KEYS:
        cell = metrics.get(key)
        if not isinstance(cell, dict):
            continue
        output[key] = {
            "value": cell.get("value"),
            "sample": _as_int(cell.get("sample")),
            "threshold": cell.get("threshold"),
            "breached": bool(cell.get("breached")),
        }
    return output


def _strategies(trade_log: Iterable[dict[str, Any]]) -> list[str]:
    values: set[str] = set()
    for trade in trade_log or []:
        value = str(trade.get("source_strategy") or trade.get("strategy") or "").strip().lower()
        if value:
            values.add(value)
    return sorted(values)


def _exit_owners(trade_log: Iterable[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(trade.get("exit_owner") or "").strip().lower()
            for trade in trade_log or []
            if str(trade.get("exit_owner") or "").strip()
        }
    )


def _isolated_owner_violations(trade_log: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for trade in trade_log or []:
        source = str(trade.get("source_strategy") or "").strip().lower()
        if source not in ISOLATED_STRATEGY_SOURCES:
            continue
        reason = str(
            trade.get("reason")
            or trade.get("exit_reason")
            or trade.get("auto_sell_review_reason")
            or ""
        ).strip().lower()
        owner = str(trade.get("exit_owner") or "").strip().lower()
        if any(token in reason for token in _SAFETY_EXIT_TOKENS):
            continue
        generic_exit = any(token in reason for token in _GENERIC_EXIT_TOKENS)
        if generic_exit and owner != source:
            violations.append(
                {
                    "ticker": str(trade.get("ticker") or ""),
                    "source_strategy": source,
                    "exit_owner": owner,
                    "reason": reason,
                }
            )
    return violations


def _runtime_gap_count(runtime_safety_summary: dict[str, Any]) -> int:
    lifecycle = (runtime_safety_summary or {}).get("lifecycle_gap_qa") or {}
    return _as_int(lifecycle.get("gap_count"))


def _data_quality_evidence(
    actual_result: dict[str, Any],
    postmortem: dict[str, Any],
    runtime_safety_summary: dict[str, Any],
) -> bool:
    labels = " ".join(str(v) for v in (actual_result.get("execution_issue_labels") or [])).lower()
    issue_type = str(postmortem.get("issue_type") or "").lower()
    issue_desc = str(postmortem.get("issue_desc") or "").lower()
    fallback = _as_int(((runtime_safety_summary or {}).get("decision_id_fallback") or {}).get("count"))
    terms = ("data", "stale", "missing", "데이터", "누락", "시차")
    return fallback > 0 or any(term in f"{labels} {issue_type} {issue_desc}" for term in terms)


def _validation_contract(root_cause: str) -> dict[str, Any]:
    contracts = {
        "EXIT_OWNER": {
            "metric": "isolated_exit_owner_violation_count",
            "success_condition": "0 violations for 5 forward sessions with isolated positions",
            "min_forward_sessions": 5,
        },
        "EXECUTION": {
            "metric": "execution_contaminated_or_lifecycle_gap_count",
            "success_condition": "0 contaminated sessions and 0 lifecycle gaps for 5 forward sessions",
            "min_forward_sessions": 5,
        },
        "DATA_QUALITY": {
            "metric": "fresh_complete_input_session_ratio",
            "success_condition": "100% fresh and complete inputs for 5 forward sessions",
            "min_forward_sessions": 5,
        },
        "ENTRY_PIPELINE": {
            "metric": "bounded_rejudge_incremental_net_pct",
            "success_condition": "cost-adjusted paired forward net > 0 with no safety regression",
            "min_forward_samples": 15,
        },
        "EXIT_POLICY": {
            "metric": "realized_net_capture_delta_pct",
            "success_condition": "paired forward net-capture delta > 0",
            "min_forward_samples": 15,
        },
        "MARKET_FORECAST": {
            "metric": "consensus_directional_hit_rate",
            "success_condition": "directional hit rate >= 55% without lower realized net",
            "min_forward_sessions": 10,
        },
        "REPORTING": {
            "metric": "report_truth_mismatch_count",
            "success_condition": "0 mismatches for 5 forward sessions",
            "min_forward_sessions": 5,
        },
        "OBSERVATION": {
            "metric": "repeat_occurrence_count",
            "success_condition": "collect independent recurrence evidence before action",
            "min_forward_sessions": 3,
        },
    }
    return {
        **contracts[root_cause],
        "status": "COLLECTING",
        "authority": AUTHORITY,
        "automatic_enforcement": False,
    }


def classify_lesson_action(
    *,
    market: str,
    session_date: str,
    postmortem: dict[str, Any] | None,
    actual_result: dict[str, Any] | None,
    trade_log: Iterable[dict[str, Any]] | None,
    ops_review_snapshot: dict[str, Any] | None,
    runtime_safety_summary: dict[str, Any] | None = None,
    judgment_eval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile one daily record into a deterministic, non-trading action."""

    market_key = str(market or "").upper()
    day = str(session_date or "")[:10]
    pm = dict(postmortem or {})
    actual = dict(actual_result or {})
    trades = [dict(row) for row in (trade_log or []) if isinstance(row, dict)]
    ops = dict(ops_review_snapshot or {})
    safety = dict(runtime_safety_summary or {})
    evaluation = dict(judgment_eval or {})
    metrics = _metric_snapshot(ops)
    owner_violations = _isolated_owner_violations(trades)
    strategies = _strategies(trades)
    owners = _exit_owners(trades)
    execution_contaminated = bool(
        actual.get("execution_contaminated")
        or actual.get("execution_learning_excluded")
        or actual.get("execution_issues")
    )
    lifecycle_gaps = _runtime_gap_count(safety)
    analyst_results = [str(pm.get(f"{role}_result") or "").upper() for role in ("bull", "bear", "neutral")]
    # Older records may not have judgment_eval, so fall back to the
    # deterministic postmortem role results below.
    consensus_hit = (
        bool(evaluation.get("consensus_hit"))
        if "consensus_hit" in evaluation
        else any(value == "HIT" for value in analyst_results)
    )

    trade_ready = metrics.get("trade_ready_signal_conversion") or {}
    watch_miss = metrics.get("watch_only_missed_runup_ratio") or {}
    ready_forward = metrics.get("trade_ready_forward_3d_average") or {}
    entry_pipeline_breach = bool(
        (trade_ready.get("breached") and trade_ready.get("sample", 0) >= 20)
        or (watch_miss.get("breached") and watch_miss.get("sample", 0) >= 20)
    )

    if owner_violations:
        root_cause = "EXIT_OWNER"
        confidence = 0.99
        status = "ACTION_REQUIRED"
        action = "enforce isolated strategy exit ownership and audit existing owner metadata"
        action_ko = "고립 전략의 출구 소유권을 강제하고 기존 포지션 소유자 메타데이터를 감사"
        rationale = "An isolated sleeve was closed by a generic exit path without its declared owner."
    elif execution_contaminated or lifecycle_gaps > 0:
        root_cause = "EXECUTION"
        confidence = 0.95
        status = "ACTION_REQUIRED"
        action = "repair the broker/lifecycle path before learning from this session"
        action_ko = "이 세션을 학습에 쓰기 전에 브로커·라이프사이클 경로를 복구"
        rationale = "Structured execution health reports contamination or lifecycle gaps."
    elif _data_quality_evidence(actual, pm, safety):
        root_cause = "DATA_QUALITY"
        confidence = 0.85
        status = "NEEDS_REVIEW"
        action = "repair input freshness/completeness and replay the same decision contract"
        action_ko = "입력 신선도·완전성을 복구하고 같은 판단 계약으로 재실행"
        rationale = "The session contains structured or postmortem evidence of stale or missing inputs."
    elif entry_pipeline_breach and (_as_int(actual.get("trades")) == 0 or not trades):
        root_cause = "ENTRY_PIPELINE"
        confidence = 0.9 if trade_ready.get("sample", 0) >= 20 else 0.82
        status = "NEEDS_REVIEW"
        action = (
            "run bounded rejudge/funnel replay; do not loosen live gates unless the paired, "
            "cost-adjusted forward net is positive"
        )
        action_ko = "제한된 재심사·퍼널 리플레이를 수행하고 비용 후 paired forward 순익이 양수일 때만 게이트 변경 검토"
        rationale = "Selection opportunities are not converting, but direction-only runup is not permission to buy."
    elif trades and _as_float(actual.get("pnl_krw")) < 0:
        root_cause = "EXIT_POLICY"
        confidence = 0.78
        status = "NEEDS_REVIEW"
        action = "compare realized exit with the pre-registered paired exit/capture arms"
        action_ko = "실현 청산을 사전등록된 paired 출구·capture arm과 비교"
        rationale = "A clean executed session lost money without an ownership violation."
    elif not consensus_hit and any(value in {"MISS", "PARTIAL"} for value in analyst_results):
        root_cause = "MARKET_FORECAST"
        confidence = 0.72
        status = "NEEDS_REVIEW"
        action = "review regime inputs in shadow; do not change live sizing from one session"
        action_ko = "shadow에서 국면 입력을 검토하되 단일 세션만으로 라이브 비중은 변경하지 않음"
        rationale = "The directional market judgment did not match the observed session."
    else:
        root_cause = "OBSERVATION"
        confidence = 0.6
        status = "OBSERVED"
        action = "collect independent recurrence and forward evidence"
        action_ko = "독립적인 반복 관측과 forward 근거를 추가 수집"
        rationale = "No deterministic execution, data, entry, exit, or forecast defect was established."

    evidence_payload = {
        "market_change_pct": actual.get("market_change"),
        "pnl_pct": actual.get("pnl_pct"),
        "pnl_krw": actual.get("pnl_krw"),
        "closed_trades": actual.get("trades"),
        "execution_contaminated": execution_contaminated,
        "execution_issues": list(actual.get("execution_issues") or []),
        "lifecycle_gap_count": lifecycle_gaps,
        "isolated_exit_owner_violations": owner_violations,
        "ops_metrics": metrics,
        "analyst_results": analyst_results,
    }
    source_payload = {
        "postmortem": pm,
        "actual_result": actual,
        "trade_log": trades,
        "ops_review_snapshot": ops,
        "runtime_safety_summary": safety,
        "judgment_eval": evaluation,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "id": f"lesson_action:{day}:{market_key}",
        "market": market_key,
        "session_date": day,
        "root_cause": root_cause,
        "confidence": round(confidence, 3),
        "status": status,
        "authority": AUTHORITY,
        "automatic_enforcement": False,
        "affected_strategies": strategies,
        "exit_owners": owners,
        "evidence": evidence_payload,
        "rationale": rationale,
        "recommended_action": action,
        "recommended_action_ko": action_ko,
        "validation_contract": _validation_contract(root_cause),
        "postmortem_lesson": str(pm.get("key_lesson") or "").strip(),
        "postmortem_issue_type": str(pm.get("issue_type") or "").strip(),
        "source_hash": _stable_hash(source_payload),
        "updated_at": _now(),
    }


def load_lesson_action_registry(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else _registry_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("actions", {})
            data.setdefault("patterns", {})
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "actions": {},
        "patterns": {},
    }


def _pattern_key(action: dict[str, Any]) -> str:
    strategies = action.get("affected_strategies") or []
    strategy = strategies[0] if len(strategies) == 1 else ("multi" if strategies else "market")
    return f"{action.get('market')}:{action.get('root_cause')}:{strategy}"


def _build_patterns(actions: Iterable[dict[str, Any]]) -> dict[str, Any]:
    all_actions = list(actions)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for action in all_actions:
        grouped.setdefault(_pattern_key(action), []).append(action)
    output: dict[str, Any] = {}
    for key, rows in grouped.items():
        ordered = sorted(rows, key=lambda row: str(row.get("session_date") or ""))
        sessions = sorted({str(row.get("session_date") or "") for row in ordered if row.get("session_date")})
        latest = ordered[-1]
        later_market_sessions = sorted(
            {
                str(row.get("session_date") or "")
                for row in all_actions
                if str(row.get("market") or "") == str(latest.get("market") or "")
                and str(row.get("session_date") or "") > str(latest.get("session_date") or "")
            }
        )
        validation = latest.get("validation_contract") or {}
        required_sessions = _as_int(validation.get("min_forward_sessions"))
        if validation.get("min_forward_samples"):
            forward_state = "COLLECTING_SAMPLES"
        elif required_sessions and len(later_market_sessions) >= required_sessions:
            forward_state = "REVIEW_DUE"
        elif later_market_sessions:
            forward_state = "COLLECTING"
        else:
            forward_state = "NOT_STARTED"
        output[key] = {
            "market": latest.get("market"),
            "root_cause": latest.get("root_cause"),
            "affected_strategies": sorted(
                {strategy for row in ordered for strategy in (row.get("affected_strategies") or [])}
            ),
            "occurrences": len(sessions),
            "first_session": sessions[0] if sessions else "",
            "last_session": sessions[-1] if sessions else "",
            "latest_status": latest.get("status"),
            "latest_action": latest.get("recommended_action"),
            "validation_contract": validation,
            "forward_observation": {
                "status": forward_state,
                "market_sessions_since_last_occurrence": len(later_market_sessions),
                "sessions": later_market_sessions,
                "note": "REVIEW_DUE is not automatic approval or enforcement.",
            },
        }
    return output


def record_lesson_actions(
    actions: Iterable[dict[str, Any]],
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Idempotently persist actions and rebuild recurrence patterns."""

    target = Path(path) if path is not None else _registry_path()
    with _LOCK:
        registry = load_lesson_action_registry(target)
        stored = registry.setdefault("actions", {})
        for incoming in actions:
            if not isinstance(incoming, dict) or not incoming.get("id"):
                continue
            action = dict(incoming)
            existing = stored.get(action["id"]) or {}
            action["created_at"] = existing.get("created_at") or action.get("updated_at") or _now()
            for operator_field in ("operator_status", "operator_notes", "operator_decision_at"):
                if operator_field in existing and operator_field not in action:
                    action[operator_field] = existing[operator_field]
            stored[action["id"]] = action
        registry.update(
            {
                "schema_version": SCHEMA_VERSION,
                "authority": AUTHORITY,
                "automatic_enforcement": False,
                "updated_at": _now(),
                "patterns": _build_patterns(stored.values()),
            }
        )
        counts: dict[str, int] = {}
        for action in stored.values():
            root = str(action.get("root_cause") or "OBSERVATION")
            counts[root] = counts.get(root, 0) + 1
        registry["summary"] = {
            "actions": len(stored),
            "patterns": len(registry["patterns"]),
            "root_cause_counts": dict(sorted(counts.items())),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
        return registry


def record_lesson_action(action: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    registry = record_lesson_actions([action], path=path)
    return dict((registry.get("actions") or {}).get(action.get("id")) or action)


def find_lesson_action(market: str, session_date: str, *, path: Path | None = None) -> dict[str, Any]:
    key = f"lesson_action:{str(session_date or '')[:10]}:{str(market or '').upper()}"
    return dict((load_lesson_action_registry(path).get("actions") or {}).get(key) or {})
