from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lifecycle.event_store import EventStore


@dataclass(frozen=True)
class DecisionPerformance:
    decision_id: str
    market: str
    ticker: str
    forward_from_decision: dict[str, float | None]
    benchmark_forward: dict[str, float | None]
    selection_alpha: dict[str, float | None]
    actual_trade_result: dict[str, float | None]
    entry_delay_minutes: float | None
    mfe_pct: float | None
    mae_pct: float | None
    exit_efficiency: float | None
    claude_cost_krw: float
    net_pnl_after_claude_krw: float | None


def decompose_decision_events(events: list[dict[str, Any]]) -> DecisionPerformance:
    if not events:
        raise ValueError("events are required")
    first = events[0]
    decision_id = str(first.get("decision_id") or "")
    market = str(first.get("market") or "")
    ticker = str(first.get("ticker") or "")
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_type[str(event.get("event_type") or "")].append(event)

    ready = _first(by_type, "CLAUDE_TRADE_READY")
    filled = _first(by_type, "FILLED") or _first(by_type, "PARTIAL_FILLED")
    closed = _last(by_type, "CLOSED")
    forward = _last(by_type, "FORWARD_MEASURED")

    forward_payload = forward.get("payload", {}) if forward else {}
    closed_payload = closed.get("payload", {}) if closed else {}

    fwd = {
        "1d": _num(forward_payload, "forward_1d"),
        "3d": _num(forward_payload, "forward_3d"),
        "5d": _num(forward_payload, "forward_5d"),
    }
    bench = {
        "1d": _num(forward_payload, "benchmark_1d"),
        "3d": _num(forward_payload, "benchmark_3d"),
        "5d": _num(forward_payload, "benchmark_5d"),
    }
    alpha = {
        horizon: (fwd[horizon] - bench[horizon] if fwd[horizon] is not None and bench[horizon] is not None else None)
        for horizon in ("1d", "3d", "5d")
    }

    actual_pnl_pct = _num(closed_payload, "pnl_pct")
    actual_pnl_krw = _num(closed_payload, "pnl_krw")
    mfe_pct = _num(closed_payload, "mfe_pct")
    mae_pct = _num(closed_payload, "mae_pct")
    if mfe_pct is None:
        mfe_pct = _max_payload(events, "mfe_pct")
    if mae_pct is None:
        mae_pct = _min_payload(events, "mae_pct")
    exit_efficiency = None
    if actual_pnl_pct is not None and mfe_pct is not None and mfe_pct > 0:
        exit_efficiency = round(actual_pnl_pct / mfe_pct, 6)

    entry_delay = None
    if ready and filled:
        entry_delay = _minutes_between(ready.get("occurred_at"), filled.get("occurred_at"))

    claude_cost_krw = sum(_num(event.get("payload") or {}, "claude_cost_krw") or 0.0 for event in events)
    net_after_claude = actual_pnl_krw - claude_cost_krw if actual_pnl_krw is not None else None

    return DecisionPerformance(
        decision_id=decision_id,
        market=market,
        ticker=ticker,
        forward_from_decision=fwd,
        benchmark_forward=bench,
        selection_alpha=alpha,
        actual_trade_result={
            "pnl_pct": actual_pnl_pct,
            "pnl_krw": actual_pnl_krw,
        },
        entry_delay_minutes=entry_delay,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        exit_efficiency=exit_efficiency,
        claude_cost_krw=round(claude_cost_krw, 3),
        net_pnl_after_claude_krw=round(net_after_claude, 3) if net_after_claude is not None else None,
    )


class PerformanceDecomposer:
    def __init__(self, store: EventStore | None = None):
        self.store = store or EventStore()

    def session_performance(self, *, session_date: str, runtime_mode: str, market: str | None = None) -> dict[str, Any]:
        events = self.store.events_for_session(market=market, runtime_mode=runtime_mode, session_date=session_date)
        by_decision: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            by_decision[str(event.get("decision_id") or "")].append(event)
        rows = []
        for decision_id, decision_events in by_decision.items():
            if not decision_id:
                continue
            try:
                rows.append(_to_dict(decompose_decision_events(decision_events)))
            except ValueError:
                continue
        return summarize_performance(rows)


def summarize_performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actual_pnls = [_nested(row, "actual_trade_result", "pnl_pct") for row in rows]
    actual_pnls = [value for value in actual_pnls if value is not None]
    pnl_krw = [_nested(row, "actual_trade_result", "pnl_krw") for row in rows]
    pnl_krw = [value for value in pnl_krw if value is not None]
    net_after = [row.get("net_pnl_after_claude_krw") for row in rows if row.get("net_pnl_after_claude_krw") is not None]
    alpha_3d = [_nested(row, "selection_alpha", "3d") for row in rows]
    alpha_3d = [value for value in alpha_3d if value is not None]
    delays = [row.get("entry_delay_minutes") for row in rows if row.get("entry_delay_minutes") is not None]
    efficiencies = [row.get("exit_efficiency") for row in rows if row.get("exit_efficiency") is not None]
    return {
        "decision_count": len(rows),
        "actual_trade_count": len(actual_pnls),
        "actual_trade_result": {
            "avg_pnl_pct": _avg(actual_pnls),
            "total_pnl_krw": round(sum(pnl_krw), 3) if pnl_krw else None,
        },
        "selection_alpha": {
            "avg_3d": _avg(alpha_3d),
            "measured_count": len(alpha_3d),
        },
        "entry_delay": {
            "avg_minutes": _avg(delays),
            "measured_count": len(delays),
        },
        "exit_efficiency": {
            "avg": _avg(efficiencies),
            "measured_count": len(efficiencies),
        },
        "net_pnl_after_claude": {
            "total_krw": round(sum(net_after), 3) if net_after else None,
        },
        "decision_rows": rows,
    }


def _first(by_type: dict[str, list[dict[str, Any]]], event_type: str) -> dict[str, Any] | None:
    rows = by_type.get(event_type) or []
    return rows[0] if rows else None


def _last(by_type: dict[str, list[dict[str, Any]]], event_type: str) -> dict[str, Any] | None:
    rows = by_type.get(event_type) or []
    return rows[-1] if rows else None


def _num(payload: dict[str, Any], key: str) -> float | None:
    try:
        raw = payload.get(key)
        if raw is None or raw == "":
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def _max_payload(events: list[dict[str, Any]], key: str) -> float | None:
    values = [_num(event.get("payload") or {}, key) for event in events]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _min_payload(events: list[dict[str, Any]], key: str) -> float | None:
    values = [_num(event.get("payload") or {}, key) for event in events]
    values = [value for value in values if value is not None]
    return min(values) if values else None


def _minutes_between(start: Any, end: Any) -> float | None:
    try:
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return round((e - s).total_seconds() / 60.0, 3)
    except Exception:
        return None


def _to_dict(perf: DecisionPerformance) -> dict[str, Any]:
    return {
        "decision_id": perf.decision_id,
        "market": perf.market,
        "ticker": perf.ticker,
        "forward_from_decision": perf.forward_from_decision,
        "benchmark_forward": perf.benchmark_forward,
        "selection_alpha": perf.selection_alpha,
        "actual_trade_result": perf.actual_trade_result,
        "entry_delay_minutes": perf.entry_delay_minutes,
        "mfe_pct": perf.mfe_pct,
        "mae_pct": perf.mae_pct,
        "exit_efficiency": perf.exit_efficiency,
        "claude_cost_krw": perf.claude_cost_krw,
        "net_pnl_after_claude_krw": perf.net_pnl_after_claude_krw,
    }


def _nested(row: dict[str, Any], key: str, subkey: str) -> float | None:
    value = (row.get(key) or {}).get(subkey)
    return value if value is not None else None


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None

