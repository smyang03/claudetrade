from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KST = timezone(timedelta(hours=9))


def _now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def _tail_path(path: str | Path) -> str:
    raw = str(path or "")
    return raw.replace(str(ROOT), "").lstrip("\\/")


def _top_counts(value: Any, *, limit: int = 10) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    rows = sorted(
        ((str(key), _safe_int(count)) for key, count in value.items()),
        key=lambda item: (-item[1], item[0]),
    )
    return {key: count for key, count in rows[:limit] if count}


def _sample_rows(value: Any, *, fields: tuple[str, ...], limit: int = 10, tail: bool = False) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows = value[-limit:] if tail else value[:limit]
    samples: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item: dict[str, str] = {}
        for field in fields:
            raw = row.get(field)
            if isinstance(raw, (dict, list)):
                item[field] = json.dumps(raw, ensure_ascii=False, sort_keys=True)
            else:
                item[field] = str(raw or "")
        samples.append(item)
    return samples


def _usage_consistency(usage_delta: dict[str, Any], raw_usage: dict[str, Any], raw_tokens: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    api_calls = _safe_int(usage_delta.get("calls"))
    raw_calls = _safe_int(raw_usage.get("calls_since_start_observed_from_raw_files"))
    quality_calls = _safe_int(quality.get("calls"))
    api_input = _safe_int(usage_delta.get("input_tokens"))
    raw_input = _safe_int(raw_tokens.get("input_tokens"))
    quality_input = _safe_int(quality.get("input_tokens"))
    api_output = _safe_int(usage_delta.get("output_tokens"))
    raw_output = _safe_int(raw_tokens.get("output_tokens"))
    quality_output = _safe_int(quality.get("output_tokens"))
    api_negative_fields = [
        field
        for field, value in (
            ("calls", api_calls),
            ("input_tokens", api_input),
            ("output_tokens", api_output),
        )
        if value < 0
    ]
    raw_quality_match = (
        raw_calls == quality_calls
        and raw_input == quality_input
        and raw_output == quality_output
    )
    api_raw_quality_match = (
        api_calls == raw_calls == quality_calls
        and api_input == raw_input == quality_input
        and api_output == raw_output == quality_output
    )
    quality_available = quality_calls > 0 or quality_input > 0 or quality_output > 0
    if api_negative_fields:
        usage_source = "quality_report_raw_call_scan" if quality_available else "raw_call_logs"
    elif api_raw_quality_match:
        usage_source = "api_raw_quality_consensus"
    elif raw_quality_match:
        usage_source = "raw_quality_consensus"
    elif quality_available:
        usage_source = "quality_report_raw_call_scan"
    else:
        usage_source = "raw_call_logs"
    return {
        "calls": {"api": api_calls, "raw": raw_calls, "quality": quality_calls},
        "input_tokens": {"api": api_input, "raw": raw_input, "quality": quality_input},
        "output_tokens": {"api": api_output, "raw": raw_output, "quality": quality_output},
        "calls_match": api_calls == raw_calls == quality_calls,
        "input_tokens_match": api_input == raw_input == quality_input,
        "output_tokens_match": api_output == raw_output == quality_output,
        "raw_quality_calls_match": raw_calls == quality_calls,
        "raw_quality_input_tokens_match": raw_input == quality_input,
        "raw_quality_output_tokens_match": raw_output == quality_output,
        "api_negative_delta_detected": bool(api_negative_fields),
        "api_negative_fields": api_negative_fields,
        "usage_source_for_final_review": usage_source,
    }


def _quality_by_label(value: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in value[:limit]:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "label": str(row.get("label") or ""),
                "calls": _safe_int(row.get("calls")),
                "input_tokens": _safe_int(row.get("input_tokens")),
                "output_tokens": _safe_int(row.get("output_tokens")),
                "total_tokens": _safe_int(row.get("total_tokens"))
                or _safe_int(row.get("input_tokens")) + _safe_int(row.get("output_tokens")),
                "avg_input_tokens": row.get("avg_input_tokens"),
                "avg_output_tokens": row.get("avg_output_tokens"),
                "avg_duration_ms": row.get("avg_duration_ms"),
                "duration_observed_calls": _safe_int(row.get("duration_observed_calls")),
                "duration_missing_calls": _safe_int(row.get("duration_missing_calls")),
                "parse_errors": _safe_int(row.get("parse_errors")),
                "input_issues": _top_counts(row.get("input_issues") or {}, limit=8),
                "output_issues": _top_counts(row.get("output_issues") or {}, limit=8),
            }
        )
    return rows


def _quality_timeline(value: Any, *, limit: int = 40) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in value[:limit]:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "bucket_start": str(row.get("bucket_start") or ""),
                "calls": _safe_int(row.get("calls")),
                "input_tokens": _safe_int(row.get("input_tokens")),
                "output_tokens": _safe_int(row.get("output_tokens")),
                "total_tokens": _safe_int(row.get("total_tokens"))
                or _safe_int(row.get("input_tokens")) + _safe_int(row.get("output_tokens")),
                "avg_input_tokens": row.get("avg_input_tokens"),
                "avg_output_tokens": row.get("avg_output_tokens"),
                "parse_errors": _safe_int(row.get("parse_errors")),
                "top_labels": _top_counts(row.get("top_labels") or {}, limit=5),
                "input_issues": _top_counts(row.get("input_issues") or {}, limit=8),
                "output_issues": _top_counts(row.get("output_issues") or {}, limit=8),
            }
        )
    return rows


def _usage_timeline_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "status": "no_data",
            "total_calls": 0,
            "total_tokens": 0,
            "peak_bucket_start": "",
            "peak_bucket_total_tokens": 0,
            "peak_bucket_share_pct": 0.0,
            "first_bucket_start": "",
            "first_bucket_total_tokens": 0,
            "first_bucket_share_pct": 0.0,
            "latest_bucket_start": "",
            "latest_bucket_total_tokens": 0,
            "high_input_bucket_count": 0,
            "non_strict_json_bucket_count": 0,
            "conclusion_ko": "시간대별 Claude 사용량 데이터가 없습니다.",
        }
    total_calls = sum(_safe_int(row.get("calls")) for row in rows)
    total_tokens = sum(_safe_int(row.get("total_tokens")) for row in rows)
    peak = max(rows, key=lambda row: _safe_int(row.get("total_tokens")))
    first = rows[0]
    latest = rows[-1]
    first_share = _pct(_safe_int(first.get("total_tokens")), total_tokens)
    peak_share = _pct(_safe_int(peak.get("total_tokens")), total_tokens)
    high_input_bucket_count = 0
    non_strict_json_bucket_count = 0
    for row in rows:
        input_issues = row.get("input_issues") if isinstance(row.get("input_issues"), dict) else {}
        output_issues = row.get("output_issues") if isinstance(row.get("output_issues"), dict) else {}
        if _safe_int(input_issues.get("prompt_input_tokens_ge_8000")) or _safe_int(input_issues.get("prompt_input_tokens_ge_12000")):
            high_input_bucket_count += 1
        if _safe_int(output_issues.get("response_not_strict_json")):
            non_strict_json_bucket_count += 1

    if len(rows) == 1:
        status = "single_bucket"
        conclusion = "관측 호출이 하나의 시간 버킷에만 있어 시간대별 분산을 판단하기 어렵습니다."
    elif str(peak.get("bucket_start") or "") == str(first.get("bucket_start") or "") and first_share >= 40.0:
        status = "front_loaded"
        conclusion = "Claude 사용량이 초반 버킷에 집중됐습니다. 장 초반 selection/R1 호출량을 우선 줄이는 것이 효과적입니다."
    else:
        status = "distributed"
        conclusion = "Claude 사용량이 여러 시간 버킷에 분산됐습니다. peak bucket과 고입력 버킷을 중심으로 조정합니다."

    return {
        "status": status,
        "total_calls": total_calls,
        "total_tokens": total_tokens,
        "peak_bucket_start": peak.get("bucket_start") or "",
        "peak_bucket_total_tokens": _safe_int(peak.get("total_tokens")),
        "peak_bucket_share_pct": peak_share,
        "first_bucket_start": first.get("bucket_start") or "",
        "first_bucket_total_tokens": _safe_int(first.get("total_tokens")),
        "first_bucket_share_pct": first_share,
        "latest_bucket_start": latest.get("bucket_start") or "",
        "latest_bucket_total_tokens": _safe_int(latest.get("total_tokens")),
        "high_input_bucket_count": high_input_bucket_count,
        "non_strict_json_bucket_count": non_strict_json_bucket_count,
        "conclusion_ko": conclusion,
    }


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100.0, 1)


def _label_group(
    rows: list[dict[str, Any]],
    *,
    name: str,
    labels: set[str] | None = None,
    prefix: str = "",
    suffix: str = "",
    total_tokens: int,
) -> dict[str, Any]:
    matched: list[dict[str, Any]] = []
    for row in rows:
        label = str(row.get("label") or "")
        if labels is not None and label in labels:
            matched.append(row)
        elif prefix and suffix and label.startswith(prefix) and label.endswith(suffix):
            matched.append(row)
        elif prefix and not suffix and label.startswith(prefix):
            matched.append(row)
        elif suffix and not prefix and label.endswith(suffix):
            matched.append(row)
    calls = sum(_safe_int(row.get("calls")) for row in matched)
    input_tokens = sum(_safe_int(row.get("input_tokens")) for row in matched)
    output_tokens = sum(_safe_int(row.get("output_tokens")) for row in matched)
    group_total = sum(_safe_int(row.get("total_tokens")) for row in matched)
    return {
        "group": name,
        "calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": group_total,
        "share_pct": _pct(group_total, total_tokens),
        "avg_total_tokens": round(float(group_total) / float(calls), 1) if calls else 0.0,
    }


def _claude_lightweighting_assessment(quality: dict[str, Any], label_rows: list[dict[str, Any]]) -> dict[str, Any]:
    label_total = sum(_safe_int(row.get("total_tokens")) for row in label_rows)
    reported_total = _safe_int(quality.get("total_tokens")) or _safe_int(quality.get("input_tokens")) + _safe_int(quality.get("output_tokens"))
    total_tokens = label_total or reported_total
    groups = [
        _label_group(label_rows, name="selection", labels={"select_tickers"}, total_tokens=total_tokens),
        _label_group(label_rows, name="analyst_r1", prefix="analyst_", suffix="_r1", total_tokens=total_tokens),
        _label_group(label_rows, name="analyst_r2", prefix="analyst_", suffix="_r2", total_tokens=total_tokens),
        _label_group(label_rows, name="hold_advisor", prefix="hold_advisor", total_tokens=total_tokens),
        _label_group(label_rows, name="tuning", labels={"param_tuner", "tune_30min", "tune_60min"}, total_tokens=total_tokens),
    ]
    grouped_total = sum(_safe_int(row.get("total_tokens")) for row in groups)
    if total_tokens > grouped_total:
        groups.append(
            {
                "group": "other",
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": total_tokens - grouped_total,
                "share_pct": _pct(total_tokens - grouped_total, total_tokens),
                "avg_total_tokens": 0.0,
            }
        )

    by_group = {str(row.get("group")): row for row in groups}
    selection_share = float(by_group.get("selection", {}).get("share_pct") or 0.0)
    analyst_r1_share = float(by_group.get("analyst_r1", {}).get("share_pct") or 0.0)
    front_load_share = round(selection_share + analyst_r1_share, 1)
    hold_share = float(by_group.get("hold_advisor", {}).get("share_pct") or 0.0)
    r1_avg = float(by_group.get("analyst_r1", {}).get("avg_total_tokens") or 0.0)
    r2_avg = float(by_group.get("analyst_r2", {}).get("avg_total_tokens") or 0.0)
    input_issues = quality.get("input_issue_counts") if isinstance(quality.get("input_issue_counts"), dict) else {}
    high_input_issue_count = _safe_int(input_issues.get("prompt_input_tokens_ge_8000")) + _safe_int(
        input_issues.get("prompt_input_tokens_ge_12000")
    )
    max_avg_input = 0.0
    for row in label_rows:
        try:
            max_avg_input = max(max_avg_input, float(row.get("avg_input_tokens") or 0.0))
        except Exception:
            continue

    concerns: list[str] = []
    positive_signals: list[str] = []
    if selection_share >= 30.0:
        concerns.append("selection_token_share_high")
    if analyst_r1_share >= 30.0:
        concerns.append("analyst_r1_token_share_high")
    if front_load_share >= 65.0:
        concerns.append("selection_and_r1_front_load_high")
    if high_input_issue_count > 0 or max_avg_input >= 8000.0:
        concerns.append("large_prompt_input_observed")
    if 0.0 < hold_share <= 10.0:
        positive_signals.append("hold_advisor_cost_control_ok")
    if r1_avg > 0.0 and r2_avg > 0.0 and r2_avg <= r1_avg * 0.6:
        positive_signals.append("analyst_r2_reduced_vs_r1")

    if concerns and positive_signals:
        status = "mixed"
        conclusion = "후단 hold/r2 경량화는 대체로 작동하지만 selection/R1 입력 집중이 높아 전단 경량화는 미흡합니다."
    elif concerns:
        status = "needs_attention"
        conclusion = "Claude 사용량 경량화가 충분하지 않습니다. 고비용 라벨의 입력 pack 축소가 우선입니다."
    else:
        status = "ok"
        conclusion = "현재 관측 범위에서는 Claude 사용량 경량화가 대체로 적절하게 운영 중입니다."

    actions: list[str] = []
    if "selection_token_share_high" in concerns or "large_prompt_input_observed" in concerns:
        actions.append("select_tickers 후보/evidence pack 행 수와 반복 calibration 블록을 줄이고 8k+ 입력 프롬프트를 별도 경고 기준으로 관리합니다.")
    if "analyst_r1_token_share_high" in concerns or "selection_and_r1_front_load_high" in concerns:
        actions.append("analyst_r1 3개 persona가 같은 대형 context를 반복 수신하는지 확인하고 shortlist 이후에만 무거운 분석을 실행합니다.")
    if "hold_advisor_cost_control_ok" in positive_signals:
        actions.append("hold_advisor triage/challenge 경량화는 유지하되 JSON-only 출력 계약 위반만 별도 보강합니다.")
    if not actions:
        actions.append("현재 token share와 high-input 경고 기준을 유지하며 다음 운영 구간에서도 같은 지표로 감시합니다.")

    return {
        "status": status,
        "conclusion_ko": conclusion,
        "total_tokens": total_tokens,
        "front_load_share_pct": front_load_share,
        "selection_share_pct": selection_share,
        "analyst_r1_share_pct": analyst_r1_share,
        "hold_advisor_share_pct": hold_share,
        "high_input_issue_count": high_input_issue_count,
        "max_avg_input_tokens_by_label": round(max_avg_input, 1),
        "positive_signals": positive_signals,
        "concerns": concerns,
        "groups": groups,
        "top_labels": sorted(label_rows, key=lambda row: _safe_int(row.get("total_tokens")), reverse=True)[:8],
        "actions_ko": actions,
    }


def _lightweighting_recommendations(assessment: dict[str, Any]) -> list[dict[str, str]]:
    status = str(assessment.get("status") or "")
    if status not in {"mixed", "needs_attention"}:
        return []
    priority = "P1" if status == "needs_attention" else "P2"
    actions = list(assessment.get("actions_ko") or [])
    recommendation = actions[0] if actions else "Reduce high-token Claude prompt paths before adding new model calls."
    return [
        {
            "priority": priority,
            "area": "token_lightweighting",
            "recommendation": recommendation,
        }
    ]


def _consistency_recommendations(consistency: dict[str, Any]) -> list[dict[str, str]]:
    if bool(consistency.get("api_negative_delta_detected")):
        fields = ", ".join(str(field) for field in consistency.get("api_negative_fields") or [])
        return [
            {
                "priority": "P1",
                "area": "observability",
                "recommendation": f"API usage delta is negative for {fields}; treat the quality report's raw-call scan as the Claude usage source of truth for this review.",
            }
        ]
    if (
        bool(consistency.get("calls_match"))
        and bool(consistency.get("input_tokens_match"))
        and bool(consistency.get("output_tokens_match"))
    ):
        return []
    return [
        {
            "priority": "P2",
            "area": "observability",
            "recommendation": "Reconcile API usage, raw-call logs, and quality-report counts before drawing final cost or quality conclusions.",
        }
    ]


def _review_usage(
    *,
    usage_delta: dict[str, Any],
    raw_usage: dict[str, Any],
    raw_tokens: dict[str, Any],
    quality: dict[str, Any],
    consistency: dict[str, Any],
) -> dict[str, Any]:
    source = str(consistency.get("usage_source_for_final_review") or "api_raw_quality_consensus")
    if source == "quality_report_raw_call_scan":
        calls = _safe_int(quality.get("calls"))
        input_tokens = _safe_int(quality.get("input_tokens"))
        output_tokens = _safe_int(quality.get("output_tokens"))
        return {
            "source": source,
            "calls": calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "api_cost_usd": usage_delta.get("cost_usd"),
            "api_cost_trusted": False,
        }
    if source == "raw_call_logs":
        calls = _safe_int(raw_usage.get("calls_since_start_observed_from_raw_files"))
        input_tokens = _safe_int(raw_tokens.get("input_tokens"))
        output_tokens = _safe_int(raw_tokens.get("output_tokens"))
        return {
            "source": source,
            "calls": calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "api_cost_usd": usage_delta.get("cost_usd"),
            "api_cost_trusted": False,
        }

    calls = _safe_int(quality.get("calls")) or _safe_int(raw_usage.get("calls_since_start_observed_from_raw_files")) or _safe_int(usage_delta.get("calls"))
    input_tokens = _safe_int(quality.get("input_tokens")) or _safe_int(raw_tokens.get("input_tokens")) or _safe_int(usage_delta.get("input_tokens"))
    output_tokens = _safe_int(quality.get("output_tokens")) or _safe_int(raw_tokens.get("output_tokens")) or _safe_int(usage_delta.get("output_tokens"))
    return {
        "source": source,
        "calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "api_cost_usd": usage_delta.get("cost_usd"),
        "api_cost_trusted": source == "api_raw_quality_consensus",
    }


def _ops_recommendations(monitor: dict[str, Any]) -> list[dict[str, str]]:
    latest = monitor.get("latest_snapshot") if isinstance(monitor.get("latest_snapshot"), dict) else {}
    broker = latest.get("broker_truth") if isinstance(latest.get("broker_truth"), dict) else {}
    guardian = latest.get("guardian") if isinstance(latest.get("guardian"), dict) else {}
    risk_axes = monitor.get("risk_axes") if isinstance(monitor.get("risk_axes"), dict) else {}
    issue_counts = monitor.get("log_issue_counts_since_start") if isinstance(monitor.get("log_issue_counts_since_start"), dict) else {}
    recs: list[dict[str, str]] = []

    if _guardian_gate(guardian) == "BLOCK_START" or _safe_int(issue_counts.get("guardian_block_start")) > 0:
        recs.append(
            {
                "priority": "P1",
                "area": "operations",
                "recommendation": "Resolve guardian BLOCK_START causes before treating the session as operationally clean; include any repeated block events observed during the window, not just the final snapshot.",
            }
        )
    if (
        bool(broker.get("missing"))
        or bool(broker.get("stale"))
        or str(broker.get("error") or "")
        or _safe_int(issue_counts.get("broker_truth_untrusted")) > 0
    ):
        recs.append(
            {
                "priority": "P1",
                "area": "broker_truth",
                "recommendation": "Refresh broker truth and keep new entries fail-closed while snapshot freshness is untrusted; review repeated stale/untrusted snapshots from the full window.",
            }
        )
    if _safe_int(risk_axes.get("manual_action_required")) > 0:
        recs.append(
            {
                "priority": "P1",
                "area": "reconciliation",
                "recommendation": "Review manual-action-required local state before the next live start window.",
            }
        )
    if _safe_int(issue_counts.get("traceback")) > 0 or _safe_int(issue_counts.get("log_error")) > 0:
        recs.append(
            {
                "priority": "P1",
                "area": "runtime_errors",
                "recommendation": "Inspect error samples and confirm they did not affect broker truth, order routing, or Claude fallback behavior.",
            }
        )
    if _safe_int(issue_counts.get("order_unknown")) > 0:
        recs.append(
            {
                "priority": "P2",
                "area": "order_state",
                "recommendation": "Separate current-session unresolved ORDER_UNKNOWN from historical event noise in the morning review.",
            }
        )
    return recs


def _guardian_gate(guardian: dict[str, Any]) -> str:
    gate = guardian.get("gate")
    if gate:
        return str(gate)
    alert = guardian.get("alert") if isinstance(guardian.get("alert"), dict) else {}
    return str(alert.get("gate") or "")


def build_morning_report(*, out_dir: str | Path) -> dict[str, Any]:
    base = Path(out_dir)
    monitor_path = base / "final_report.json"
    quality_path = base / "claude_io_quality.json"
    monitor = _read_json(monitor_path, {})
    quality = _read_json(quality_path, {})
    if not isinstance(monitor, dict):
        monitor = {}
    if not isinstance(quality, dict):
        quality = {}

    latest = monitor.get("latest_snapshot") if isinstance(monitor.get("latest_snapshot"), dict) else {}
    usage_delta = latest.get("api_usage_delta_since_start") if isinstance(latest.get("api_usage_delta_since_start"), dict) else {}
    raw_usage = monitor.get("claude_usage_since_start") if isinstance(monitor.get("claude_usage_since_start"), dict) else {}
    raw_tokens = raw_usage.get("tokens_observed_from_raw_files") if isinstance(raw_usage.get("tokens_observed_from_raw_files"), dict) else {}
    hold_cost = monitor.get("hold_advisor_cost_observation") if isinstance(monitor.get("hold_advisor_cost_observation"), dict) else {}
    guardian = latest.get("guardian") if isinstance(latest.get("guardian"), dict) else {}
    broker = latest.get("broker_truth") if isinstance(latest.get("broker_truth"), dict) else {}

    quality_recs = []
    for row in list(quality.get("recommendations") or []):
        if isinstance(row, dict):
            quality_recs.append(
                {
                    "priority": str(row.get("priority") or "P2"),
                    "area": str(row.get("area") or "claude_io"),
                    "recommendation": str(row.get("recommendation") or ""),
                }
            )
    consistency = _usage_consistency(usage_delta, raw_usage, raw_tokens, quality)
    review_usage = _review_usage(
        usage_delta=usage_delta,
        raw_usage=raw_usage,
        raw_tokens=raw_tokens,
        quality=quality,
        consistency=consistency,
    )
    quality_by_label = _quality_by_label(quality.get("by_label"), limit=1000)
    lightweighting = _claude_lightweighting_assessment(quality, quality_by_label)
    recommendations = (
        quality_recs
        + _lightweighting_recommendations(lightweighting)
        + _ops_recommendations(monitor)
        + _consistency_recommendations(consistency)
    )
    guardian_causes = guardian.get("block_start_causes")
    if not isinstance(guardian_causes, list):
        guardian_causes = []
    usage_timeline = _quality_timeline(quality.get("by_time_bucket"), limit=40)

    return {
        "generated_at": _now_kst(),
        "out_dir": str(base),
        "sources": {
            "monitor_json": str(monitor_path),
            "quality_json": str(quality_path),
        },
        "monitor_window": {
            "start_at": monitor.get("start_at") or quality.get("start_at") or "",
            "end_at": monitor.get("end_at") or quality.get("end_at") or "",
            "mode": monitor.get("mode") or "",
            "market": monitor.get("market") or quality.get("market") or "",
            "session_date": monitor.get("session_date") or "",
        },
        "operations": {
            "monitor_status": monitor.get("status") or "missing",
            "guardian_gate": _guardian_gate(guardian),
            "guardian_ok": guardian.get("ok"),
            "broker_truth_missing": bool(broker.get("missing")),
            "broker_truth_stale": bool(broker.get("stale")),
            "broker_truth_error": str(broker.get("error") or ""),
            "broker_positions": _safe_int(broker.get("positions_count")),
            "broker_open_orders": _safe_int(broker.get("open_orders_count")),
            "broker_fills": _safe_int(broker.get("today_fills_count")),
            "decision_events": len(monitor.get("decision_events_since_start") or []),
            "log_issue_counts": _top_counts(monitor.get("log_issue_counts_since_start") or {}),
        },
        "claude_usage": {
            "review_usage": review_usage,
            "api_usage_delta": usage_delta,
            "raw_call_files": _safe_int(raw_usage.get("calls_since_start_observed_from_raw_files")),
            "raw_input_tokens": _safe_int(raw_tokens.get("input_tokens")),
            "raw_output_tokens": _safe_int(raw_tokens.get("output_tokens")),
            "raw_duration_ms": _safe_int(raw_tokens.get("duration_ms")),
            "raw_by_label": _top_counts(raw_usage.get("by_label") or {}, limit=20),
            "raw_by_model": _top_counts(raw_usage.get("by_model") or {}, limit=10),
            "hold_advisor_calls": _safe_int(hold_cost.get("observed_calls")),
            "hold_advisor_by_label": _top_counts(hold_cost.get("by_label") or {}, limit=10),
        },
        "claude_io_quality": {
            "quality_calls": _safe_int(quality.get("calls")),
            "quality_input_tokens": _safe_int(quality.get("input_tokens")),
            "quality_output_tokens": _safe_int(quality.get("output_tokens")),
            "quality_parse_errors": _safe_int(quality.get("parse_errors")),
            "avg_input_tokens": quality.get("avg_input_tokens"),
            "avg_output_tokens": quality.get("avg_output_tokens"),
            "avg_duration_ms": quality.get("avg_duration_ms"),
            "duration_observed_calls": _safe_int(quality.get("duration_observed_calls")),
            "duration_missing_calls": _safe_int(quality.get("duration_missing_calls")),
            "input_issue_counts": _top_counts(quality.get("input_issue_counts") or {}, limit=20),
            "output_issue_counts": _top_counts(quality.get("output_issue_counts") or {}, limit=20),
            "by_label": quality_by_label[:12],
            "usage_timeline": usage_timeline,
            "usage_timeline_summary": _usage_timeline_summary(usage_timeline),
        },
        "claude_lightweighting": lightweighting,
        "consistency_checks": consistency,
        "recommendations": recommendations,
        "evidence_samples": {
            "quality_issue_samples": _sample_rows(
                quality.get("issue_samples"),
                fields=("timestamp", "label", "input_issues", "output_issues", "path"),
                limit=12,
            ),
            "prompt_warning_samples": _sample_rows(
                quality.get("prompt_warning_samples"),
                fields=(
                    "timestamp",
                    "label",
                    "input_tokens",
                    "prompt_chars",
                    "candidate_lines",
                    "evidence_requested_count",
                    "evidence_pack_count",
                    "active_lesson_chars",
                    "top_prompt_sections",
                    "path",
                ),
                limit=12,
            ),
            "quality_slow_call_samples": _sample_rows(
                quality.get("slow_call_samples"),
                fields=("timestamp", "label", "duration_ms", "path"),
                limit=8,
            ),
            "log_issue_samples": _sample_rows(
                monitor.get("log_issue_samples"),
                fields=("at", "kind", "level", "message", "path"),
                limit=12,
                tail=True,
            ),
            "guardian_block_causes": _sample_rows(
                guardian_causes,
                fields=("code", "risk_level", "message"),
                limit=10,
            ),
        },
        "sample_paths": {
            "monitor_report": _tail_path(base / "final_report.md"),
            "quality_report": _tail_path(base / "claude_io_quality.md"),
        },
    }


def write_json(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")
    tmp.replace(path)


def write_markdown(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    usage = report.get("claude_usage") or {}
    review_usage = usage.get("review_usage") if isinstance(usage.get("review_usage"), dict) else {}
    api = usage.get("api_usage_delta") if isinstance(usage.get("api_usage_delta"), dict) else {}
    quality = report.get("claude_io_quality") or {}
    lightweighting = report.get("claude_lightweighting") if isinstance(report.get("claude_lightweighting"), dict) else {}
    ops = report.get("operations") or {}
    evidence = report.get("evidence_samples") if isinstance(report.get("evidence_samples"), dict) else {}
    consistency = report.get("consistency_checks") if isinstance(report.get("consistency_checks"), dict) else {}
    window = report.get("monitor_window") or {}
    lines = [
        "# US Claude Morning Review / 미국장 Claude 아침 리뷰",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- window: {window.get('start_at')} ~ {window.get('end_at')}",
        f"- scope: {window.get('mode')} / {window.get('market')} / {window.get('session_date')}",
        f"- source_reports: {report.get('sample_paths', {}).get('monitor_report')} / {report.get('sample_paths', {}).get('quality_report')}",
        "",
        "## 한국어 요약",
        "",
        f"- 운영 상태: monitor={ops.get('monitor_status')} guardian={ops.get('guardian_gate')} ok={ops.get('guardian_ok')}",
        f"- 브로커 truth: missing={ops.get('broker_truth_missing')} stale={ops.get('broker_truth_stale')} error={ops.get('broker_truth_error')}",
        f"- Claude 사용량(검토 기준): source={review_usage.get('source')} calls={review_usage.get('calls')} input_tokens={review_usage.get('input_tokens')} output_tokens={review_usage.get('output_tokens')} total_tokens={review_usage.get('total_tokens')}",
        f"- API usage delta: calls={api.get('calls')} input_tokens={api.get('input_tokens')} output_tokens={api.get('output_tokens')} cost_usd={api.get('cost_usd')} trusted={review_usage.get('api_cost_trusted')}",
        f"- raw call 관측: files={usage.get('raw_call_files')} input={usage.get('raw_input_tokens')} output={usage.get('raw_output_tokens')} duration_ms={usage.get('raw_duration_ms')}",
        f"- Claude I/O 품질: calls={quality.get('quality_calls')} parse_errors={quality.get('quality_parse_errors')} avg_input={quality.get('avg_input_tokens')} avg_output={quality.get('avg_output_tokens')}",
        f"- 지연시간 커버리지: observed={quality.get('duration_observed_calls')} missing={quality.get('duration_missing_calls')} avg_duration_ms={quality.get('avg_duration_ms')}",
        f"- 사용량 일관성: calls_match={consistency.get('calls_match')} input_match={consistency.get('input_tokens_match')} output_match={consistency.get('output_tokens_match')}",
        f"- 입력 이슈: {json.dumps(quality.get('input_issue_counts') or {}, ensure_ascii=False, sort_keys=True)}",
        f"- 출력 이슈: {json.dumps(quality.get('output_issue_counts') or {}, ensure_ascii=False, sort_keys=True)}",
        "",
        "## Operations",
        "",
        f"- monitor_status: {ops.get('monitor_status')}",
        f"- guardian_gate: {ops.get('guardian_gate')} ok={ops.get('guardian_ok')}",
        f"- broker_truth: missing={ops.get('broker_truth_missing')} stale={ops.get('broker_truth_stale')} error={ops.get('broker_truth_error')}",
        f"- broker_positions/open_orders/fills: {ops.get('broker_positions')} / {ops.get('broker_open_orders')} / {ops.get('broker_fills')}",
        f"- decision_events: {ops.get('decision_events')}",
        f"- log_issue_counts: {json.dumps(ops.get('log_issue_counts') or {}, ensure_ascii=False, sort_keys=True)}",
        "",
        "## Claude Usage",
        "",
        f"- review_usage: source={review_usage.get('source')} calls={review_usage.get('calls')} input={review_usage.get('input_tokens')} output={review_usage.get('output_tokens')} total={review_usage.get('total_tokens')} api_cost_trusted={review_usage.get('api_cost_trusted')}",
        f"- api_usage_delta: calls={api.get('calls')} input={api.get('input_tokens')} output={api.get('output_tokens')} cost_usd={api.get('cost_usd')}",
        f"- raw_call_files: {usage.get('raw_call_files')}",
        f"- raw_tokens: input={usage.get('raw_input_tokens')} output={usage.get('raw_output_tokens')} duration_ms={usage.get('raw_duration_ms')}",
        f"- raw_by_label: {json.dumps(usage.get('raw_by_label') or {}, ensure_ascii=False, sort_keys=True)}",
        f"- raw_by_model: {json.dumps(usage.get('raw_by_model') or {}, ensure_ascii=False, sort_keys=True)}",
        f"- hold_advisor_calls: {usage.get('hold_advisor_calls')} by_label={json.dumps(usage.get('hold_advisor_by_label') or {}, ensure_ascii=False, sort_keys=True)}",
        "",
        "## Claude Lightweighting",
        "",
        f"- status: {lightweighting.get('status')}",
        f"- conclusion: {lightweighting.get('conclusion_ko')}",
        f"- total_tokens: {lightweighting.get('total_tokens')}",
        f"- front_load_share_pct: {lightweighting.get('front_load_share_pct')} selection={lightweighting.get('selection_share_pct')} analyst_r1={lightweighting.get('analyst_r1_share_pct')}",
        f"- hold_advisor_share_pct: {lightweighting.get('hold_advisor_share_pct')}",
        f"- high_input_issue_count: {lightweighting.get('high_input_issue_count')} max_avg_input_tokens_by_label={lightweighting.get('max_avg_input_tokens_by_label')}",
        f"- positive_signals: {json.dumps(lightweighting.get('positive_signals') or [], ensure_ascii=False)}",
        f"- concerns: {json.dumps(lightweighting.get('concerns') or [], ensure_ascii=False)}",
        "",
        "### Lightweighting Groups",
        "",
    ]
    groups = list(lightweighting.get("groups") or [])
    if groups:
        lines.append("| group | calls | input | output | total | share % | avg total |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        for row in groups:
            lines.append(
                f"| {row.get('group')} | {row.get('calls')} | {row.get('input_tokens')} | {row.get('output_tokens')} | "
                f"{row.get('total_tokens')} | {row.get('share_pct')} | {row.get('avg_total_tokens')} |"
            )
    else:
        lines.append("- no lightweighting groups captured")
    actions = list(lightweighting.get("actions_ko") or [])
    if actions:
        lines.extend(["", "### Lightweighting Actions"])
        for action in actions:
            lines.append(f"- {action}")
    lines.extend(
        [
        "",
        "## Claude I/O Quality",
        "",
        f"- quality_calls: {quality.get('quality_calls')}",
        f"- quality_tokens: input={quality.get('quality_input_tokens')} output={quality.get('quality_output_tokens')}",
        f"- parse_errors: {quality.get('quality_parse_errors')}",
        f"- averages: input={quality.get('avg_input_tokens')} output={quality.get('avg_output_tokens')} duration_ms={quality.get('avg_duration_ms')}",
        f"- duration_coverage: observed={quality.get('duration_observed_calls')} missing={quality.get('duration_missing_calls')}",
        f"- input_issues: {json.dumps(quality.get('input_issue_counts') or {}, ensure_ascii=False, sort_keys=True)}",
        f"- output_issues: {json.dumps(quality.get('output_issue_counts') or {}, ensure_ascii=False, sort_keys=True)}",
        "",
        "## Claude I/O By Label",
        "",
        ]
    )
    quality_labels = list(quality.get("by_label") or [])
    if quality_labels:
        lines.append("| label | calls | input | output | total | avg input | avg output | avg ms | ms observed | ms missing | input issues | output issues |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |")
        for row in quality_labels:
            lines.append(
                f"| {row.get('label')} | {row.get('calls')} | {row.get('input_tokens')} | {row.get('output_tokens')} | "
                f"{row.get('total_tokens')} | {row.get('avg_input_tokens')} | {row.get('avg_output_tokens')} | {row.get('avg_duration_ms')} | "
                f"{row.get('duration_observed_calls')} | {row.get('duration_missing_calls')} | "
                f"{json.dumps(row.get('input_issues') or {}, ensure_ascii=False, sort_keys=True)} | "
                f"{json.dumps(row.get('output_issues') or {}, ensure_ascii=False, sort_keys=True)} |"
            )
    else:
        lines.append("- no label-level quality rows captured")
    timeline = list(quality.get("usage_timeline") or [])
    lines.extend(["", "## Claude Usage Timeline", ""])
    timeline_summary = quality.get("usage_timeline_summary") if isinstance(quality.get("usage_timeline_summary"), dict) else {}
    if timeline_summary:
        lines.extend(
            [
                f"- timeline_summary: status={timeline_summary.get('status')} total_calls={timeline_summary.get('total_calls')} total_tokens={timeline_summary.get('total_tokens')}",
                f"- peak_bucket: {timeline_summary.get('peak_bucket_start')} tokens={timeline_summary.get('peak_bucket_total_tokens')} share_pct={timeline_summary.get('peak_bucket_share_pct')}",
                f"- first_bucket: {timeline_summary.get('first_bucket_start')} tokens={timeline_summary.get('first_bucket_total_tokens')} share_pct={timeline_summary.get('first_bucket_share_pct')}",
                f"- issue_buckets: high_input={timeline_summary.get('high_input_bucket_count')} non_strict_json={timeline_summary.get('non_strict_json_bucket_count')}",
                f"- conclusion: {timeline_summary.get('conclusion_ko')}",
                "",
            ]
        )
    if timeline:
        lines.append("| bucket start | calls | input | output | total | avg input | top labels | input issues | output issues |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |")
        for row in timeline:
            lines.append(
                f"| {row.get('bucket_start')} | {row.get('calls')} | {row.get('input_tokens')} | {row.get('output_tokens')} | "
                f"{row.get('total_tokens')} | {row.get('avg_input_tokens')} | "
                f"{json.dumps(row.get('top_labels') or {}, ensure_ascii=False, sort_keys=True)} | "
                f"{json.dumps(row.get('input_issues') or {}, ensure_ascii=False, sort_keys=True)} | "
                f"{json.dumps(row.get('output_issues') or {}, ensure_ascii=False, sort_keys=True)} |"
            )
    else:
        lines.append("- no usage timeline captured")
    lines.extend(
        [
            "",
        "## Consistency Checks",
        "",
        f"- calls: {json.dumps(consistency.get('calls') or {}, ensure_ascii=False, sort_keys=True)} match={consistency.get('calls_match')}",
        f"- input_tokens: {json.dumps(consistency.get('input_tokens') or {}, ensure_ascii=False, sort_keys=True)} match={consistency.get('input_tokens_match')}",
        f"- output_tokens: {json.dumps(consistency.get('output_tokens') or {}, ensure_ascii=False, sort_keys=True)} match={consistency.get('output_tokens_match')}",
        f"- raw_quality_match: calls={consistency.get('raw_quality_calls_match')} input={consistency.get('raw_quality_input_tokens_match')} output={consistency.get('raw_quality_output_tokens_match')}",
        f"- api_negative_delta_detected: {consistency.get('api_negative_delta_detected')} fields={json.dumps(consistency.get('api_negative_fields') or [], ensure_ascii=False)}",
        f"- usage_source_for_final_review: {consistency.get('usage_source_for_final_review')}",
        "",
        "## Evidence Samples / 근거 샘플",
        "",
        ]
    )
    quality_samples = list(evidence.get("quality_issue_samples") or [])
    if quality_samples:
        lines.append("### Claude I/O Issue Samples")
        for row in quality_samples:
            lines.append(
                f"- {row.get('timestamp')} {row.get('label')} input={row.get('input_issues')} "
                f"output={row.get('output_issues')} path={row.get('path')}"
            )
    prompt_warnings = list(evidence.get("prompt_warning_samples") or [])
    if prompt_warnings:
        lines.extend(["", "### Prompt Warning Samples"])
        lines.append("| time | label | input | chars | candidates | evidence requested | evidence pack | lessons chars | top sections | path |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |")
        for row in prompt_warnings:
            lines.append(
                f"| {row.get('timestamp')} | {row.get('label')} | {row.get('input_tokens')} | {row.get('prompt_chars')} | "
                f"{row.get('candidate_lines')} | {row.get('evidence_requested_count')} | {row.get('evidence_pack_count')} | "
                f"{row.get('active_lesson_chars')} | {row.get('top_prompt_sections')} | {row.get('path')} |"
            )
    slow_samples = list(evidence.get("quality_slow_call_samples") or [])
    if slow_samples:
        lines.extend(["", "### Slow Call Samples"])
        for row in slow_samples:
            lines.append(f"- {row.get('timestamp')} {row.get('label')} duration_ms={row.get('duration_ms')} path={row.get('path')}")
    log_samples = list(evidence.get("log_issue_samples") or [])
    if log_samples:
        lines.extend(["", "### Operational Issue Samples"])
        for row in log_samples:
            lines.append(f"- {row.get('at')} {row.get('kind')} {row.get('level')}: {row.get('message')} path={row.get('path')}")
    guardian_causes = list(evidence.get("guardian_block_causes") or [])
    if guardian_causes:
        lines.extend(["", "### Guardian Block Causes"])
        for row in guardian_causes:
            lines.append(f"- {row.get('risk_level')} {row.get('code')}: {row.get('message')}")
    if not (quality_samples or prompt_warnings or slow_samples or log_samples or guardian_causes):
        lines.append("- no samples captured")
    lines.extend(
        [
            "",
        "## Improvement Actions / 개선 액션",
        "",
        ]
    )
    recommendations = list(report.get("recommendations") or [])
    if recommendations:
        for row in recommendations:
            if not isinstance(row, dict):
                continue
            lines.append(f"- {row.get('priority')} {row.get('area')}: {row.get('recommendation')}")
    else:
        lines.append("- No immediate improvement action was generated from the observed data.")
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Combine US overnight monitor and Claude I/O quality reports.")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--out-json", default="")
    parser.add_argument("--out-md", default="")
    args = parser.parse_args(argv)

    report = build_morning_report(out_dir=args.out_dir)
    base = Path(args.out_dir)
    out_json = Path(args.out_json) if args.out_json else base / "morning_review.json"
    out_md = Path(args.out_md) if args.out_md else base / "morning_review.md"
    write_json(report, out_json)
    write_markdown(report, out_md)
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
