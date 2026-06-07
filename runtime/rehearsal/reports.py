from __future__ import annotations

from collections import Counter
from typing import Any


def _result_key(result: dict[str, Any]) -> tuple[float, float, str]:
    metrics = result.get("metrics") or {}
    return (
        float(metrics.get("score") or 0.0),
        float(metrics.get("realized_pnl_pct") or metrics.get("unrealized_pnl_pct") or 0.0),
        str(result.get("scenario") or ""),
    )


def rank_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(results, key=_result_key, reverse=True)


def _hint_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_category: Counter[str] = Counter()
    by_priority: Counter[str] = Counter()
    by_signal: Counter[str] = Counter()
    for result in results:
        for hint in result.get("improvement_hints") or []:
            by_category[str(hint.get("category") or "unknown")] += 1
            by_priority[str(hint.get("priority") or "unknown")] += 1
            by_signal[str(hint.get("signal") or "unknown")] += 1
    return {
        "by_category": dict(sorted(by_category.items())),
        "by_priority": dict(sorted(by_priority.items())),
        "top_signals": [{"signal": key, "count": value} for key, value in by_signal.most_common(10)],
    }


def _block_summary(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for result in results:
        for event in result.get("events") or []:
            reason = str(event.get("reason") or "")
            if reason:
                counts[reason] += 1
    return dict(counts.most_common())


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("metrics") or {}
    return {
        "scenario": result.get("scenario"),
        "market": result.get("market"),
        "ticker": result.get("ticker"),
        "sweep": result.get("sweep") or {},
        "entered": metrics.get("entered"),
        "closed": metrics.get("closed"),
        "score": metrics.get("score"),
        "realized_pnl_pct": metrics.get("realized_pnl_pct"),
        "realized_pnl_krw": metrics.get("realized_pnl_krw"),
        "unrealized_pnl_pct": metrics.get("unrealized_pnl_pct"),
        "missed_gain_pct": metrics.get("missed_gain_pct"),
        "hint_signals": [hint.get("signal") for hint in result.get("improvement_hints") or []],
    }


def build_simulation_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = rank_results(results)
    entered_count = sum(1 for result in results if (result.get("metrics") or {}).get("entered"))
    closed_count = sum(1 for result in results if (result.get("metrics") or {}).get("closed"))
    blocked_count = sum(
        1
        for result in results
        if any(str(event.get("event_type") or "") == "ENTRY_BLOCKED" for event in result.get("events") or [])
    )
    summary = {
        "case_count": len(results),
        "entered_count": entered_count,
        "closed_count": closed_count,
        "blocked_count": blocked_count,
        "avg_score": round(sum(float((result.get("metrics") or {}).get("score") or 0.0) for result in results) / len(results), 4)
        if results
        else 0.0,
        "block_reasons": _block_summary(results),
        "hint_summary": _hint_summary(results),
    }
    return {
        "ok": True,
        "summary": summary,
        "best": _compact_result(ranked[0]) if ranked else {},
        "worst": _compact_result(ranked[-1]) if ranked else {},
        "ranking": [_compact_result(result) for result in ranked],
        "results": results,
    }
