from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
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
        result_reasons: set[str] = set()
        for event in result.get("events") or []:
            if str(event.get("event_type") or "") != "ENTRY_BLOCKED":
                continue
            reason = str(event.get("reason") or "")
            if reason:
                result_reasons.add(reason)
        counts.update(result_reasons)
    return dict(counts.most_common())


def _price_coverage(result: dict[str, Any]) -> dict[str, Any]:
    params = result.get("params") or {}
    coverage = params.get("price_coverage") or {}
    return coverage if isinstance(coverage, dict) else {}


def _price_coverage_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: Counter[str] = Counter()
    by_flag: Counter[str] = Counter()
    incomplete: list[dict[str, Any]] = []
    for result in results:
        coverage = _price_coverage(result)
        if not coverage:
            continue
        status = str(coverage.get("coverage_status") or "unknown")
        by_status[status] += 1
        for flag in coverage.get("coverage_flags") or []:
            by_flag[str(flag or "unknown")] += 1
        if status != "complete":
            incomplete.append(
                {
                    "scenario": result.get("scenario"),
                    "market": result.get("market"),
                    "ticker": result.get("ticker"),
                    "source": (result.get("params") or {}).get("source"),
                    "coverage_status": status,
                    "coverage_flags": coverage.get("coverage_flags") or [],
                    "requested_start_at": coverage.get("requested_start_at", ""),
                    "requested_end_at": coverage.get("requested_end_at", ""),
                    "actual_start_at": coverage.get("actual_start_at", ""),
                    "actual_end_at": coverage.get("actual_end_at", ""),
                    "matched_rows": coverage.get("matched_rows", 0),
                }
            )
    return {
        "by_status": dict(sorted(by_status.items())),
        "by_flag": dict(sorted(by_flag.items())),
        "incomplete_count": len(incomplete),
        "incomplete_examples": incomplete[:50],
    }


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("metrics") or {}
    coverage = _price_coverage(result)
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
        "price_coverage_status": coverage.get("coverage_status", ""),
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
        "price_coverage": _price_coverage_summary(results),
    }
    return {
        "ok": True,
        "summary": summary,
        "best": _compact_result(ranked[0]) if ranked else {},
        "worst": _compact_result(ranked[-1]) if ranked else {},
        "ranking": [_compact_result(result) for result in ranked],
        "results": results,
    }


def simulation_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in report.get("results") or []:
        metrics = result.get("metrics") or {}
        coverage = _price_coverage(result)
        entry_block_reasons = [
            str(event.get("reason") or "")
            for event in result.get("events") or []
            if str(event.get("event_type") or "") == "ENTRY_BLOCKED" and event.get("reason")
        ]
        rows.append(
            {
                "scenario": result.get("scenario", ""),
                "market": result.get("market", ""),
                "ticker": result.get("ticker", ""),
                "path_type": result.get("path_type", ""),
                "sweep": json.dumps(result.get("sweep") or {}, ensure_ascii=False, sort_keys=True),
                "entered": metrics.get("entered", False),
                "closed": metrics.get("closed", False),
                "score": metrics.get("score", 0.0),
                "entry_price": metrics.get("entry_price", ""),
                "exit_price": metrics.get("exit_price", ""),
                "qty": metrics.get("qty", 0),
                "realized_pnl_pct": metrics.get("realized_pnl_pct", 0.0),
                "realized_pnl_krw": metrics.get("realized_pnl_krw", 0.0),
                "unrealized_pnl_pct": metrics.get("unrealized_pnl_pct", 0.0),
                "unrealized_pnl_krw": metrics.get("unrealized_pnl_krw", 0.0),
                "missed_gain_pct": metrics.get("missed_gain_pct", 0.0),
                "post_exit_runup_pct": metrics.get("post_exit_runup_pct", 0.0),
                "block_reasons": ";".join(entry_block_reasons),
                "price_coverage_status": coverage.get("coverage_status", ""),
                "price_coverage_flags": ";".join(str(flag or "") for flag in coverage.get("coverage_flags") or []),
                "price_requested_start_at": coverage.get("requested_start_at", ""),
                "price_requested_end_at": coverage.get("requested_end_at", ""),
                "price_actual_start_at": coverage.get("actual_start_at", ""),
                "price_actual_end_at": coverage.get("actual_end_at", ""),
                "price_matched_rows": coverage.get("matched_rows", ""),
                "hint_signals": ";".join(str(hint.get("signal") or "") for hint in result.get("improvement_hints") or []),
            }
        )
    return rows


def write_simulation_csv(report: dict[str, Any], path: Path) -> None:
    rows = simulation_csv_rows(report)
    fieldnames = [
        "scenario",
        "market",
        "ticker",
        "path_type",
        "sweep",
        "entered",
        "closed",
        "score",
        "entry_price",
        "exit_price",
        "qty",
        "realized_pnl_pct",
        "realized_pnl_krw",
        "unrealized_pnl_pct",
        "unrealized_pnl_krw",
        "missed_gain_pct",
        "post_exit_runup_pct",
        "block_reasons",
        "price_coverage_status",
        "price_coverage_flags",
        "price_requested_start_at",
        "price_requested_end_at",
        "price_actual_start_at",
        "price_actual_end_at",
        "price_matched_rows",
        "hint_signals",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
