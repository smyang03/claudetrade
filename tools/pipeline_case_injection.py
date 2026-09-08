from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.action_routing import route_candidate_action
from runtime.live_evidence_pack import build_live_evidence_pack
from runtime_paths import get_runtime_path


DEFAULT_DB = get_runtime_path("data", "audit", "candidate_audit.db")


CASE_QUERIES: list[tuple[str, str, str]] = [
    (
        "KR_partial_missing_live_data",
        "KR",
        """
        market='KR'
        AND claude_action IN ('BUY_READY','PROBE_READY','PULLBACK_WAIT')
        AND COALESCE(route_final_action,'')='WATCH'
        AND COALESCE(evidence_data_state,'')='partial'
        """,
    ),
    (
        "KR_buy_ready_price_cap_exceeded",
        "KR",
        """
        market='KR'
        AND claude_action='BUY_READY'
        AND COALESCE(route_final_action,'')='WATCH'
        AND COALESCE(route_runtime_gate_reason,'')='entry_price_cap_exceeded'
        """,
    ),
    (
        "KR_confirmation_not_confirmed",
        "KR",
        """
        market='KR'
        AND claude_action IN ('BUY_READY','PROBE_READY')
        AND COALESCE(route_final_action,'')='WATCH'
        AND COALESCE(route_runtime_gate_reason,'') LIKE 'kr_%'
        """,
    ),
    (
        "KR_pullback_evidence_gate",
        "KR",
        """
        market='KR'
        AND claude_action='PULLBACK_WAIT'
        AND COALESCE(route_final_action,'')='WATCH'
        AND COALESCE(route_runtime_gate_reason,'')='pullback_wait_evidence_gate'
        """,
    ),
    (
        "KR_pathb_claude_price_invalid",
        "KR",
        """
        market='KR'
        AND claude_action='PULLBACK_WAIT'
        AND COALESCE(route_route,'')='PathB.wait'
        AND COALESCE(no_submit_reason_code,'')='CLAUDE_PRICE_INVALID'
        """,
    ),
    (
        "KR_blank_watch_after_actionable",
        "KR",
        """
        market='KR'
        AND claude_action IN ('BUY_READY','PROBE_READY','PULLBACK_WAIT')
        AND COALESCE(route_final_action,'')='WATCH'
        AND COALESCE(route_runtime_gate_reason,'')=''
        """,
    ),
    (
        "US_buy_ready_no_signal",
        "US",
        """
        market='US'
        AND claude_action='BUY_READY'
        AND COALESCE(route_route,'')='PlanA.buy'
        AND COALESCE(no_submit_reason_code,'')='NO_SIGNAL'
        """,
    ),
    (
        "US_probe_no_signal",
        "US",
        """
        market='US'
        AND claude_action='PROBE_READY'
        AND COALESCE(route_route,'')='PlanA.probe'
        AND COALESCE(no_submit_reason_code,'')='NO_SIGNAL'
        """,
    ),
    (
        "US_pullback_evidence_gate",
        "US",
        """
        market='US'
        AND claude_action='PULLBACK_WAIT'
        AND COALESCE(route_final_action,'')='WATCH'
        AND COALESCE(route_runtime_gate_reason,'')='pullback_wait_evidence_gate'
        """,
    ),
    (
        "US_late_mover_soft_block",
        "US",
        """
        market='US'
        AND claude_action='PULLBACK_WAIT'
        AND COALESCE(route_final_action,'')='WATCH'
        AND COALESCE(route_runtime_gate_reason,'')='late_mover'
        """,
    ),
    (
        "US_soft_gate_override_failed",
        "US",
        """
        market='US'
        AND claude_action IN ('BUY_READY','PROBE_READY')
        AND COALESCE(route_final_action,'')='WATCH'
        AND COALESCE(route_runtime_gate_reason,'')='soft_gate_override_failed'
        """,
    ),
    (
        "US_pathb_claude_price_invalid",
        "US",
        """
        market='US'
        AND claude_action='PULLBACK_WAIT'
        AND COALESCE(route_route,'')='PathB.wait'
        AND COALESCE(no_submit_reason_code,'')='CLAUDE_PRICE_INVALID'
        """,
    ),
    (
        "US_evidence_ceiling",
        "US",
        """
        market='US'
        AND claude_action IN ('BUY_READY','PROBE_READY')
        AND COALESCE(route_final_action,'') IN ('WATCH','PROBE_READY')
        AND COALESCE(route_runtime_gate_reason,'')='evidence_action_ceiling'
        """,
    ),
]


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if value in (None, ""):
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    return list(parsed) if isinstance(parsed, list) else []


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def load_cases(db_path: Path, since: str, per_category: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    base = """
        SELECT *
        FROM audit_candidate_rows
        WHERE session_date >= ?
          AND actual_prompt_included = 1
          AND claude_action IN ('BUY_READY','PROBE_READY','PULLBACK_WAIT')
          AND filled_count = 0
          AND post_open_features_json IS NOT NULL
          AND post_open_features_json != ''
          AND payload_json IS NOT NULL
          AND payload_json != ''
          AND ({where})
        ORDER BY session_date, known_at, ticker
        LIMIT ?
    """
    for case_type, market, where in CASE_QUERIES:
        rows = conn.execute(base.format(where=where), (since, per_category)).fetchall()
        for row in rows:
            item = _row_dict(row)
            key = str(item.get("candidate_key") or "")
            if key in seen:
                continue
            seen.add(key)
            item["_case_type"] = case_type
            item["_case_market"] = market
            selected.append(item)
    conn.close()
    return selected


def _price_targets(row: dict[str, Any], ctx: dict[str, Any], features: dict[str, Any]) -> dict[str, Any]:
    payload = _json_dict(row.get("payload_json"))
    action_payload = _json_dict(payload.get("action"))
    targets = _json_dict(action_payload.get("price_targets"))
    if targets:
        return targets
    current = _num(ctx.get("current_price") or features.get("current_price") or row.get("price")) or 0.0
    high = _num(ctx.get("buy_zone_high") or row.get("max_entry_price")) or 0.0
    low = _num(ctx.get("buy_zone_low")) or 0.0
    if high <= 0 and current > 0:
        high = current * 1.01
    if low <= 0 and high > 0:
        low = high * 0.985
    if current <= 0:
        current = high
    return {
        "current_price": current or None,
        "reference_price": current or None,
        "buy_zone_low": low or None,
        "buy_zone_high": high or None,
        "sell_target": _num(ctx.get("sell_target")) or (current * 1.04 if current > 0 else None),
        "stop_loss": _num(ctx.get("stop_loss")) or (current * 0.97 if current > 0 else None),
        "hold_days": int(_num(ctx.get("hold_days")) or 3),
        "confidence": _num(ctx.get("confidence") or row.get("max_position_pct")) or 0.8,
    }


def _action_from_row(row: dict[str, Any], ctx: dict[str, Any], features: dict[str, Any]) -> dict[str, Any]:
    confidence = (
        _num(ctx.get("confidence"))
        or _num(row.get("cohort_reliability"))
        or _num(row.get("trainer_plan_a_score"))
        or 0.8
    )
    return {
        "ticker": row.get("ticker"),
        "market": row.get("market"),
        "action": row.get("claude_action") or row.get("route_original_action") or "WATCH",
        "confidence": confidence,
        "current_price": _num(ctx.get("current_price") or features.get("current_price") or row.get("price")),
        "max_entry_price": _num(ctx.get("max_entry_price") or row.get("max_entry_price")),
        "price_targets": _price_targets(row, ctx, features),
        "risk_tags": _json_list(row.get("risk_tags_json")),
        "reason": row.get("claude_reason") or "",
    }


def _sync_evidence(ctx: dict[str, Any], pack: dict[str, Any]) -> None:
    ctx["evidence_pack_ceiling_enabled"] = True
    ctx["evidence_data_state"] = pack.get("data_state")
    ctx["evidence_action_ceiling"] = pack.get("action_ceiling")
    ctx["evidence_missing_fields"] = pack.get("missing_fields") or []
    ctx["evidence_complete"] = pack.get("data_state") == "confirmed"
    ctx["evidence_coverage_ratio"] = 1.0 if pack.get("data_state") == "confirmed" else 0.5
    ctx["evidence_pack"] = pack
    ctx["evidence_fail_closed"] = pack.get("action_ceiling") == "WATCH"
    if pack.get("action_ceiling") == "WATCH":
        ctx["evidence_fail_closed_reason"] = "evidence_action_ceiling"
    else:
        ctx.pop("evidence_fail_closed_reason", None)
    ctx["data_quality"] = pack.get("data_quality") or ctx.get("data_quality")
    ctx["data_quality_missing"] = pack.get("data_state") == "missing"


def _base_inputs(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = _json_dict(row.get("payload_json"))
    features = _json_dict(row.get("post_open_features_json"))
    ctx = _json_dict(payload.get("runtime_gate"))
    if not ctx and isinstance(payload.get("route"), dict):
        ctx = _json_dict(payload["route"].get("runtime_gate"))
    for key in (
        "current_price",
        "ret_3m_pct",
        "ret_5m_pct",
        "opening_range_break",
        "vwap_distance_pct",
        "volume_ratio_open",
        "time_normalized_rvol",
        "momentum_state",
        "pullback_from_high_pct",
        "spread_bps",
        "vi_active",
        "vi_state",
    ):
        if key not in ctx and features.get(key) is not None:
            ctx[key] = features.get(key)
    if row.get("max_entry_price") not in (None, ""):
        ctx.setdefault("max_entry_price", row.get("max_entry_price"))
        ctx.setdefault("entry_price_cap", row.get("max_entry_price"))
        ctx.setdefault("buy_zone_high", row.get("max_entry_price"))
    ctx.setdefault("route_requested_action", row.get("route_original_action") or row.get("claude_action"))
    return payload, features, ctx


def _inject_confirmed(features: dict[str, Any], ctx: dict[str, Any], *, orb: bool = True) -> None:
    current = _num(ctx.get("current_price") or features.get("current_price")) or 100.0
    updates = {
        "current_price": current,
        "data_quality": "minute_complete",
        "ret_3m_pct": _num(features.get("ret_3m_pct")) if _num(features.get("ret_3m_pct")) is not None else 0.8,
        "ret_5m_pct": _num(features.get("ret_5m_pct")) if _num(features.get("ret_5m_pct")) is not None else 1.2,
        "opening_range_break": orb,
        "vwap_distance_pct": _num(features.get("vwap_distance_pct")) if _num(features.get("vwap_distance_pct")) is not None else 0.4,
        "volume_ratio_open": max(_num(features.get("volume_ratio_open")) or 0.0, 1.5),
        "momentum_state": "sustained",
        "spread_bps": _num(features.get("spread_bps")) if _num(features.get("spread_bps")) is not None else 12.0,
        "vi_active": False,
        "fail_closed": False,
    }
    features.update(updates)
    ctx.update(updates)
    ctx["data_quality_missing"] = False
    ctx["evidence_partial_grace_active"] = False


def _inject_inside_cap(features: dict[str, Any], ctx: dict[str, Any]) -> None:
    cap = _num(ctx.get("entry_price_cap") or ctx.get("buy_zone_high") or ctx.get("max_entry_price"))
    if cap is None:
        cap = _num(features.get("current_price")) or 100.0
    current = cap * 0.995
    features["current_price"] = current
    ctx["current_price"] = current
    ctx["entry_price_cap"] = cap
    ctx["buy_zone_high"] = cap
    ctx["max_entry_price"] = cap


def _inject_complete_pathb(action: dict[str, Any], features: dict[str, Any], ctx: dict[str, Any]) -> None:
    current = _num(ctx.get("current_price") or features.get("current_price") or action.get("current_price")) or 100.0
    high = _num(ctx.get("buy_zone_high") or ctx.get("entry_price_cap") or action.get("max_entry_price")) or current * 1.01
    if current > high:
        current = high * 0.995
        features["current_price"] = current
        ctx["current_price"] = current
    low = _num(ctx.get("buy_zone_low")) or high * 0.985
    targets = {
        "current_price": current,
        "reference_price": current,
        "buy_zone_low": low,
        "buy_zone_high": high,
        "sell_target": current * 1.04,
        "stop_loss": current * 0.97,
        "hold_days": 3,
        "confidence": 0.8,
    }
    action["price_targets"] = targets
    ctx.update(
        {
            "current_price": current,
            "buy_zone_low": low,
            "buy_zone_high": high,
            "entry_price_cap": high,
            "max_entry_price": high,
        }
    )


def _inject_clear_soft_blocks(ctx: dict[str, Any]) -> None:
    ctx["soft_gates"] = []
    ctx["repeated_failed_ready_count"] = 0
    ctx["freshness_verdict"] = "FRESH"
    ctx["momentum_state"] = "sustained"
    ctx["overextended"] = False
    ctx["pathb_wait_negative_watch_count"] = 0


def _inject_kr_confirmation(ctx: dict[str, Any]) -> None:
    ctx["kr_confirmation_gate_active"] = True
    ctx["kr_confirmation_gate_enabled"] = True
    ctx["kr_confirmation_gate_shadow"] = False
    ctx["kr_confirmation_confirmed"] = True
    ctx["kr_confirmation_state"] = "confirmed"
    ctx["kr_confirmation_reason"] = "injected_confirmed"
    ctx["kr_confirmation_score"] = max(_num(ctx.get("kr_confirmation_score")) or 0.0, _num(ctx.get("kr_confirmation_threshold")) or 1.0)
    ctx["kr_confirmation_fast_window_ok"] = True


def run_scenario(row: dict[str, Any], scenario: str) -> dict[str, Any]:
    _, features, ctx = _base_inputs(row)
    action = _action_from_row(row, ctx, features)
    notes: list[str] = []
    if scenario == "original":
        pass
    elif scenario == "complete_post_open_data":
        _inject_confirmed(features, ctx, orb=True)
        notes.append("ret_3m/ret_5m/opening_range_break/vwap/volume/data_quality 직접 보강")
    elif scenario == "complete_post_open_no_orb":
        _inject_confirmed(features, ctx, orb=False)
        notes.append("ORB만 false로 두고 나머지 분봉 증거 보강")
    elif scenario == "inside_entry_cap":
        _inject_confirmed(features, ctx, orb=True)
        _inject_inside_cap(features, ctx)
        notes.append("현재가를 entry_price_cap/buy_zone_high 안쪽으로 강제")
    elif scenario == "complete_pathb_plan":
        _inject_confirmed(features, ctx, orb=True)
        _inject_complete_pathb(action, features, ctx)
        notes.append("PathB 필수 가격계획 buy_zone/sell/stop/hold/confidence 직접 주입")
    elif scenario == "clear_soft_blocks":
        _inject_confirmed(features, ctx, orb=True)
        _inject_clear_soft_blocks(ctx)
        notes.append("late/repeated/fade/soft gate 맥락 제거")
    elif scenario == "kr_confirmation_confirmed":
        _inject_confirmed(features, ctx, orb=True)
        _inject_kr_confirmation(ctx)
        notes.append("KR confirmation snapshot을 confirmed로 강제")
    else:
        raise ValueError(f"unknown scenario: {scenario}")

    pack = build_live_evidence_pack(
        market=str(row.get("market") or ""),
        ticker=str(row.get("ticker") or ""),
        features=features,
        action=action,
        route={
            "final_action": row.get("route_final_action"),
            "route": row.get("route_route"),
            "runtime_gate_reason": row.get("route_runtime_gate_reason"),
        },
    )
    _sync_evidence(ctx, pack)
    if scenario == "clear_soft_blocks":
        _inject_clear_soft_blocks(ctx)
    if scenario == "kr_confirmation_confirmed":
        _inject_kr_confirmation(ctx)
    if scenario == "complete_pathb_plan":
        _inject_complete_pathb(action, features, ctx)

    route = route_candidate_action(
        action,
        market=str(row.get("market") or ""),
        pathb_waiting=_bool(ctx.get("pathb_waiting")),
        pathb_active_order=_bool(ctx.get("pathb_active_order")),
        overextended=_bool(ctx.get("overextended")),
        data_quality=str(ctx.get("data_quality") or "missing"),
        execution_context=ctx,
    ).to_dict()
    return {
        "scenario": scenario,
        "notes": notes,
        "evidence": {
            "data_state": pack.get("data_state"),
            "action_ceiling": pack.get("action_ceiling"),
            "missing_fields": pack.get("missing_fields") or [],
            "momentum_state": (pack.get("post_open_confirmation") or {}).get("momentum_state"),
        },
        "route": {
            "final_action": route.get("final_action"),
            "route": route.get("route"),
            "reason": route.get("reason"),
            "runtime_gate_reason": route.get("runtime_gate_reason"),
            "warnings": route.get("warnings") or [],
        },
        "field_presence": {
            "feature_keys": sorted(features.keys()),
            "ctx_keys": sorted(ctx.keys()),
            "action_price_targets_complete": all(
                action.get("price_targets", {}).get(key) not in (None, "")
                for key in ("buy_zone_low", "buy_zone_high", "sell_target", "stop_loss", "hold_days", "confidence")
            ),
        },
    }


def classify_case(row: dict[str, Any], scenarios: list[dict[str, Any]]) -> tuple[str, list[str]]:
    original = scenarios[0]
    recorded_route_open = row.get("route_final_action") in {"BUY_READY", "PROBE_READY", "PULLBACK_WAIT"} and row.get(
        "route_route"
    ) in {"PlanA.buy", "PlanA.probe", "PathB.wait"}
    if row.get("no_submit_reason_code") and recorded_route_open:
        return (
            "submit_layer_block_after_route_open",
            [
                f"route는 기록상 이미 열렸지만 submit 단계에서 {row.get('no_submit_reason_code')} 발생",
                "evidence/route 함수 주입 문제가 아니라 주문 신호 생성·가격계획 파싱·submit 조건 계층을 별도 재생해야 함",
            ],
        )
    recovered = [
        item
        for item in scenarios[1:]
        if item["route"]["final_action"] in {"BUY_READY", "PROBE_READY", "PULLBACK_WAIT"}
        and item["route"]["route"] in {"PlanA.buy", "PlanA.probe", "PathB.wait"}
    ]
    findings: list[str] = []
    if row.get("route_runtime_gate_reason") == "entry_price_cap_exceeded":
        if any(item["scenario"] == "inside_entry_cap" for item in recovered):
            return "normal_price_cap_block", ["상한 안쪽으로 넣으면 매수 경로가 열림: 가격 차단 자체는 정상"]
        return "price_cap_block_unresolved", ["상한 안쪽 주입 후에도 경로가 열리지 않음"]
    if row.get("no_submit_reason_code"):
        findings.append(f"route는 열렸지만 submit 단계에서 {row.get('no_submit_reason_code')} 발생")
    if recovered:
        findings.append("주입 시나리오에서 실행 가능 route 회복: 원본 입력/배선 결손 가능성")
        names = ", ".join(item["scenario"] for item in recovered[:5])
        findings.append(f"회복 시나리오: {names}")
        return "data_or_wiring_gap_recovers", findings
    if original["evidence"]["data_state"] != "confirmed":
        findings.append("원본 evidence가 confirmed가 아니며 주입 후에도 route 회복 없음")
        return "data_gap_but_strategy_still_blocks", findings
    route_reason = str(row.get("route_runtime_gate_reason") or row.get("route_reason") or "")
    if route_reason:
        findings.append(f"명시 차단 사유 유지: {route_reason}")
        return "strategy_gate_holds", findings
    findings.append("원본 route 차단 사유가 비어 있고 주입 후에도 회복 없음: 로깅/필드 사용 추적 필요")
    return "blank_or_unused_route_reason", findings


def field_gap_notes(row: dict[str, Any]) -> list[str]:
    _, features, ctx = _base_inputs(row)
    notes: list[str] = []
    pairs = [
        ("vwap_distance_pct", "vwap_reclaim"),
        ("time_normalized_rvol", "volume_ratio_open"),
        ("opening_range_break", "opening_range_break"),
        ("ret_3m_pct", "ret_3m_pct"),
        ("ret_5m_pct", "ret_5m_pct"),
    ]
    for feature_key, ctx_key in pairs:
        if features.get(feature_key) not in (None, "") and ctx.get(ctx_key) in (None, ""):
            notes.append(f"features.{feature_key} 존재하지만 runtime_gate.{ctx_key} 없음")
    requested = str(ctx.get("route_requested_action") or "")
    claude = str(row.get("claude_action") or "")
    if requested and claude and requested != claude:
        notes.append(f"claude_action={claude}, runtime_gate.route_requested_action={requested} 불일치")
    return notes


def analyze(db_path: Path, since: str, per_category: int) -> dict[str, Any]:
    rows = load_cases(db_path, since, per_category)
    scenario_names = [
        "original",
        "complete_post_open_data",
        "complete_post_open_no_orb",
        "inside_entry_cap",
        "complete_pathb_plan",
        "clear_soft_blocks",
        "kr_confirmation_confirmed",
    ]
    cases: list[dict[str, Any]] = []
    class_counts: Counter[str] = Counter()
    for row in rows:
        scenarios = [run_scenario(row, name) for name in scenario_names]
        classification, findings = classify_case(row, scenarios)
        class_counts[classification] += 1
        cases.append(
            {
                "case_type": row["_case_type"],
                "candidate_key": row.get("candidate_key"),
                "market": row.get("market"),
                "session_date": row.get("session_date"),
                "known_at": row.get("known_at"),
                "ticker": row.get("ticker"),
                "claude_action": row.get("claude_action"),
                "recorded_route": {
                    "final_action": row.get("route_final_action"),
                    "route": row.get("route_route"),
                    "reason": row.get("route_reason"),
                    "runtime_gate_reason": row.get("route_runtime_gate_reason"),
                    "no_submit_reason_code": row.get("no_submit_reason_code"),
                },
                "classification": classification,
                "findings": findings,
                "field_gap_notes": field_gap_notes(row),
                "scenarios": scenarios,
            }
        )
    return {
        "db_path": str(db_path),
        "since": since,
        "per_category": per_category,
        "case_count": len(cases),
        "classification_counts": dict(class_counts),
        "cases": cases,
    }


def write_md(result: dict[str, Any], path: Path) -> None:
    lines: list[str] = []
    lines.append("# Pipeline Case Injection Review 2026-07-23")
    lines.append("")
    lines.append("목적: DB의 실제 미매수 후보를 대표 유형별로 뽑아 `build_live_evidence_pack()`과 `route_candidate_action()`에 원본/주입 데이터를 직접 넣고, 미매수가 데이터 누락인지 전략상 정상 차단인지 검증한다.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- DB: `{result['db_path']}`")
    lines.append(f"- 기간: `{result['since']}` 이후")
    lines.append(f"- 케이스 수: {result['case_count']}")
    for key, count in sorted(result["classification_counts"].items()):
        lines.append(f"- {key}: {count}")
    lines.append("")
    lines.append("## Case Results")
    lines.append("")
    for case in result["cases"]:
        lines.append(f"### {case['case_type']} / {case['market']} {case['ticker']} / {case['candidate_key']}")
        lines.append("")
        lines.append(f"- 시각: {case['session_date']} {case['known_at']}")
        lines.append(f"- Claude action: `{case['claude_action']}`")
        rec = case["recorded_route"]
        lines.append(
            "- 기록 route: "
            f"`{rec.get('final_action')}` / `{rec.get('route')}` / "
            f"reason=`{rec.get('reason')}` / gate=`{rec.get('runtime_gate_reason')}` / "
            f"no_submit=`{rec.get('no_submit_reason_code')}`"
        )
        lines.append(f"- 분류: `{case['classification']}`")
        for finding in case["findings"]:
            lines.append(f"  - {finding}")
        if case["field_gap_notes"]:
            lines.append("- 필드/배선 의심:")
            for note in case["field_gap_notes"]:
                lines.append(f"  - {note}")
        lines.append("")
        lines.append("| scenario | evidence | route | gate reason | missing |")
        lines.append("|---|---|---|---|---|")
        for scenario in case["scenarios"]:
            ev = scenario["evidence"]
            rt = scenario["route"]
            missing = ",".join(ev.get("missing_fields") or [])
            lines.append(
                f"| {scenario['scenario']} | {ev.get('data_state')}/{ev.get('action_ceiling')} | "
                f"{rt.get('final_action')}/{rt.get('route')} | {rt.get('runtime_gate_reason') or rt.get('reason') or ''} | {missing} |"
            )
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay candidate audit cases with injected evidence/route data.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--since", default="2026-07-01")
    parser.add_argument("--per-category", type=int, default=2)
    parser.add_argument("--json-out", default=str(ROOT / "docs" / "reports" / "pipeline_case_injection_20260723.json"))
    parser.add_argument("--md-out", default=str(ROOT / "docs" / "reports" / "pipeline_case_injection_20260723.md"))
    args = parser.parse_args()
    result = analyze(Path(args.db), args.since, args.per_category)
    json_path = Path(args.json_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(result, Path(args.md_out))
    print(json.dumps({"case_count": result["case_count"], "classification_counts": result["classification_counts"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
