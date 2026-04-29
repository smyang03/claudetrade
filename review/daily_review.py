from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
import json

from runtime_paths import get_runtime_path
from lifecycle.event_store import EventStore
from lifecycle.quality import evaluate_decision_quality
from performance.decomposition import PerformanceDecomposer


class DailyReviewWriter:
    def __init__(self, store: EventStore | None = None, output_dir: str | Path | None = None):
        self.store = store or EventStore()
        self.output_dir = Path(output_dir) if output_dir else get_runtime_path("logs", "daily_review", make_parents=False)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build_summary(self, *, session_date: str, runtime_mode: str, market: str | None = None) -> dict[str, Any]:
        events = self.store.events_for_session(market=market, runtime_mode=runtime_mode, session_date=session_date)
        counts = Counter(str(event.get("event_type") or "") for event in events)
        by_decision: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            by_decision[str(event.get("decision_id") or "")].append(event)

        quality_counts: Counter[str] = Counter()
        learning_allowed = 0
        quality_reasons: Counter[str] = Counter()
        for decision_events in by_decision.values():
            result = evaluate_decision_quality(decision_events)
            quality_counts[result.grade.value] += 1
            learning_allowed += 1 if result.learning_allowed else 0
            quality_reasons.update(result.reasons)

        closed_by_reason: Counter[str] = Counter()
        order_unknown: list[dict[str, Any]] = []
        for event in events:
            event_type = str(event.get("event_type") or "")
            payload = event.get("payload") or {}
            if event_type == "CLOSED":
                closed_by_reason[str(payload.get("close_reason") or event.get("reason_code") or "UNKNOWN")] += 1
            if event_type == "ORDER_UNKNOWN":
                order_unknown.append(event)

        performance = PerformanceDecomposer(self.store).session_performance(
            session_date=session_date,
            runtime_mode=runtime_mode,
            market=market,
        )
        pathb = _pathb_summary(self.store, session_date=session_date, runtime_mode=runtime_mode, market=market)
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "session_date": session_date,
            "runtime_mode": runtime_mode,
            "market": market or "ALL",
            "event_count": len(events),
            "decision_count": len(by_decision),
            "event_counts": dict(sorted(counts.items())),
            "order_unknown": {
                "count": len(order_unknown),
                "items": [
                    {
                        "market": event.get("market"),
                        "ticker": event.get("ticker"),
                        "decision_id": event.get("decision_id"),
                        "execution_id": event.get("execution_id"),
                        "reason_code": event.get("reason_code"),
                    }
                    for event in order_unknown
                ],
            },
            "safety_blocked": counts.get("SAFETY_BLOCKED", 0),
            "timing_unsupported": counts.get("TIMING_UNSUPPORTED", 0),
            "timing_expired": counts.get("TIMING_EXPIRED", 0),
            "closed_by_reason": dict(sorted(closed_by_reason.items())),
            "data_quality": {
                "counts": dict(sorted(quality_counts.items())),
                "learning_allowed_decisions": learning_allowed,
                "reasons": dict(sorted(quality_reasons.items())),
            },
            "performance": performance,
            "path_comparison": {
                "path_a": _path_a_summary(events),
                "path_b": pathb,
            },
            "tomorrow_checks": _tomorrow_checks(counts, quality_reasons),
        }

    def write(self, *, session_date: str, runtime_mode: str, market: str | None = None) -> dict[str, str]:
        summary = self.build_summary(session_date=session_date, runtime_mode=runtime_mode, market=market)
        prefix_market = f"{market}_" if market else ""
        base = f"{runtime_mode}_{prefix_market}{session_date.replace('-', '')}_summary"
        json_path = self.output_dir / f"{base}.json"
        md_path = self.output_dir / f"{base}.md"
        json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(_to_markdown(summary), encoding="utf-8")
        return {"json": str(json_path), "markdown": str(md_path)}


def _tomorrow_checks(counts: Counter[str], quality_reasons: Counter[str]) -> list[str]:
    checks: list[str] = []
    if counts.get("ORDER_UNKNOWN", 0):
        checks.append("Resolve ORDER_UNKNOWN before allowing new entries.")
    if counts.get("SAFETY_BLOCKED", 0):
        checks.append("Review SAFETY_BLOCKED reason distribution.")
    if counts.get("TIMING_EXPIRED", 0):
        checks.append("Forward-measure TIMING_EXPIRED decisions.")
    if quality_reasons:
        checks.append("Inspect data quality reasons before learning.")
    return checks


def _path_a_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    entries = [
        event for event in events
        if event.get("event_type") in {"ORDER_SENT", "FILLED", "CLOSED"}
        and ((event.get("payload") or {}).get("path_type") or "") != "claude_price"
    ]
    closes = [event for event in entries if event.get("event_type") == "CLOSED"]
    return {
        "path_type": "timing_adapter",
        "events": len(entries),
        "closed": len(closes),
        "closed_by_reason": dict(Counter(str((event.get("payload") or {}).get("close_reason") or "UNKNOWN") for event in closes)),
    }


def _pathb_summary(
    store: EventStore,
    *,
    session_date: str,
    runtime_mode: str,
    market: str | None,
) -> dict[str, Any]:
    runs = []
    markets = [market] if market else ["KR", "US"]
    for market_key in markets:
        runs.extend(
            store.path_runs_for_session(
                market=market_key,
                runtime_mode=runtime_mode,
                session_date=session_date,
                path_type="claude_price",
            )
        )
    status_counts = Counter(str(run.get("status") or "UNKNOWN") for run in runs)
    filled = [run for run in runs if str(run.get("status") or "") in {"FILLED", "SELL_SENT", "SELL_PARTIAL_FILLED", "CLOSED"}]
    closed = [run for run in runs if str(run.get("status") or "") == "CLOSED"]
    target_hits = 0
    stop_hits = 0
    missed_buy = 0
    for run in runs:
        plan = run.get("plan") or run.get("plan_json") or {}
        close_reason = str(plan.get("close_reason") or "")
        if close_reason == "CLOSED_CLAUDE_PRICE_TARGET":
            target_hits += 1
        elif close_reason in {"CLOSED_CLAUDE_PRICE_STOP", "CLOSED_HARD_STOP"}:
            stop_hits += 1
        if str(run.get("status") or "") == "EXPIRED":
            missed_buy += 1
    return {
        "path_type": "claude_price",
        "runs": len(runs),
        "status_counts": dict(sorted(status_counts.items())),
        "filled": len(filled),
        "closed": len(closed),
        "missed_buy": missed_buy,
        "target_hits": target_hits,
        "stop_hits": stop_hits,
    }


def _to_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# V2 Daily Review",
        "",
        f"- generated_at: {summary['generated_at']}",
        f"- session_date: {summary['session_date']}",
        f"- runtime_mode: {summary['runtime_mode']}",
        f"- market: {summary['market']}",
        f"- decisions: {summary['decision_count']}",
        f"- events: {summary['event_count']}",
        "",
        "## Lifecycle Counts",
        "",
    ]
    for key, value in summary["event_counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Exceptions",
            "",
            f"- ORDER_UNKNOWN: {summary['order_unknown']['count']}",
            f"- SAFETY_BLOCKED: {summary['safety_blocked']}",
            f"- TIMING_UNSUPPORTED: {summary['timing_unsupported']}",
            f"- TIMING_EXPIRED: {summary['timing_expired']}",
            "",
            "## Data Quality",
            "",
        ]
    )
    for key, value in summary["data_quality"]["counts"].items():
        lines.append(f"- {key}: {value}")
    if summary["data_quality"]["reasons"]:
        lines.append("")
        lines.append("## Quality Reasons")
        lines.append("")
        for key, value in summary["data_quality"]["reasons"].items():
            lines.append(f"- {key}: {value}")
    perf = summary.get("performance") or {}
    lines.extend(["", "## Performance", ""])
    lines.append(f"- actual_trade_count: {perf.get('actual_trade_count', 0)}")
    lines.append(f"- total_pnl_krw: {(perf.get('actual_trade_result') or {}).get('total_pnl_krw')}")
    lines.append(f"- net_pnl_after_claude_krw: {(perf.get('net_pnl_after_claude') or {}).get('total_krw')}")
    lines.append(f"- selection_alpha_3d_avg: {(perf.get('selection_alpha') or {}).get('avg_3d')}")
    lines.append(f"- entry_delay_avg_minutes: {(perf.get('entry_delay') or {}).get('avg_minutes')}")
    lines.append(f"- exit_efficiency_avg: {(perf.get('exit_efficiency') or {}).get('avg')}")
    comparison = summary.get("path_comparison") or {}
    path_a = comparison.get("path_a") or {}
    path_b = comparison.get("path_b") or {}
    lines.extend(["", "## Path Comparison", ""])
    lines.append(f"- Path A events: {path_a.get('events', 0)} closed: {path_a.get('closed', 0)}")
    lines.append(f"- Path B runs: {path_b.get('runs', 0)} filled: {path_b.get('filled', 0)} closed: {path_b.get('closed', 0)}")
    lines.append(f"- Path B missed_buy: {path_b.get('missed_buy', 0)} target_hits: {path_b.get('target_hits', 0)} stop_hits: {path_b.get('stop_hits', 0)}")
    if path_b.get("status_counts"):
        for key, value in path_b.get("status_counts", {}).items():
            lines.append(f"- Path B {key}: {value}")
    lines.extend(["", "## Tomorrow Checks", ""])
    for item in summary["tomorrow_checks"] or ["No blocking checks."]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)
