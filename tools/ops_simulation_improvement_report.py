from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.rehearsal.context import RehearsalGuardError

KST = ZoneInfo("Asia/Seoul")
DEFAULT_OUTPUT_ROOT = ROOT / ".runtime" / "ops_simulation_analysis"
WAIT_PATHS = {"wait_30m", "wait_60m"}
HIGH_PRICE_REASON = "HIGH_PRICE_BUDGET_BLOCK"


def _now_stamp() -> str:
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S")


def _now_text() -> str:
    return datetime.now(KST).replace(microsecond=0).isoformat()


def _as_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return float(default)
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return float(default)


def _load_report(path: Path) -> dict[str, Any]:
    source = Path(path).expanduser()
    if not source.exists():
        raise RehearsalGuardError(f"simulation report not found: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RehearsalGuardError(f"invalid simulation report JSON: {source}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise RehearsalGuardError(f"simulation report must contain a results list: {source}")
    payload["_source_report_path"] = str(source)
    return payload


def _iter_results(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        source = report.get("_source_report_path", "")
        for result in report.get("results") or []:
            if isinstance(result, dict):
                row = dict(result)
                row["_source_report_path"] = source
                rows.append(row)
    return rows


def _result_score(result: dict[str, Any]) -> float:
    return _as_float((result.get("metrics") or {}).get("score"))


def _entry_block_events(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        event
        for event in result.get("events") or []
        if isinstance(event, dict) and str(event.get("event_type") or "") == "ENTRY_BLOCKED"
    ]


def _entry_block_reasons(result: dict[str, Any]) -> set[str]:
    return {str(event.get("reason") or "") for event in _entry_block_events(result) if event.get("reason")}


def _params(result: dict[str, Any]) -> dict[str, Any]:
    params = result.get("params") or {}
    return params if isinstance(params, dict) else {}


def _coverage(result: dict[str, Any]) -> dict[str, Any]:
    coverage = _params(result).get("price_coverage") or {}
    return coverage if isinstance(coverage, dict) else {}


def _hints(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [hint for hint in result.get("improvement_hints") or [] if isinstance(hint, dict)]


def _native_to_krw(price: float, result: dict[str, Any]) -> float:
    params = _params(result)
    market = str(result.get("market") or params.get("market") or "").upper()
    if market == "US":
        return price * _as_float(params.get("usd_krw"), 1350.0)
    return price


def _required_budget_krw(result: dict[str, Any]) -> float:
    params = _params(result)
    event = next((item for item in _entry_block_events(result) if str(item.get("reason") or "") == HIGH_PRICE_REASON), {})
    price = _as_float(event.get("price") or (result.get("metrics") or {}).get("entry_price"))
    slippage = _as_float(params.get("slippage_cap"), 1.0)
    return round(_native_to_krw(price, result) * slippage, 2) if price > 0 else 0.0


def _stats(values: list[float]) -> dict[str, Any]:
    clean = [float(value) for value in values]
    if not clean:
        return {"count": 0, "avg": 0.0, "median": 0.0, "best": 0.0, "worst": 0.0}
    return {
        "count": len(clean),
        "avg": round(sum(clean) / len(clean), 4),
        "median": round(float(median(clean)), 4),
        "best": round(max(clean), 4),
        "worst": round(min(clean), 4),
    }


def _best_by_key(rows: list[dict[str, Any]], key_fields: list[str], *, score_field: str = "score") -> list[dict[str, Any]]:
    best: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(str(row.get(field, "")) for field in key_fields)
        current = best.get(key)
        if current is None or _as_float(row.get(score_field)) > _as_float(current.get(score_field)):
            best[key] = row
    return list(best.values())


def _hint_row(result: dict[str, Any], hint: dict[str, Any], *, category: str) -> dict[str, Any]:
    params = _params(result)
    metrics = result.get("metrics") or {}
    evidence = hint.get("evidence") if isinstance(hint.get("evidence"), dict) else {}
    row: dict[str, Any] = {
        "market": result.get("market", ""),
        "category": category,
        "scenario": result.get("scenario", ""),
        "ticker": result.get("ticker", ""),
        "source": params.get("source", ""),
        "counterfactual_path": params.get("counterfactual_path", ""),
        "score": round(_result_score(result), 4),
        "entered": bool(metrics.get("entered")),
        "closed": bool(metrics.get("closed")),
        "signal": hint.get("signal", ""),
        "priority": hint.get("priority", ""),
        "suggestion": hint.get("suggestion", ""),
        "source_report_path": result.get("_source_report_path", ""),
    }
    for key in (
        "missed_gain_pct",
        "buy_zone_high",
        "final_from_entry_pct",
        "stop_price",
        "post_exit_runup_pct",
        "target_price",
    ):
        if key in evidence:
            row[key] = evidence.get(key)
    return row


def _us_buy_zone_misses(results: list[dict[str, Any]], *, top: int) -> dict[str, Any]:
    raw_rows: list[dict[str, Any]] = []
    for result in results:
        market = str(result.get("market") or _params(result).get("market") or "").upper()
        if market != "US":
            continue
        for hint in _hints(result):
            if str(hint.get("signal") or "") == "price missed buy zone then rallied":
                raw_rows.append(_hint_row(result, hint, category="profitability"))

    rows = _best_by_key(raw_rows, ["scenario", "ticker", "source", "counterfactual_path", "signal"], score_field="missed_gain_pct")
    grouped: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    raw_grouped: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["ticker"]), str(row["source"]), str(row["counterfactual_path"]))].append(row)
    for row in raw_rows:
        raw_grouped[(str(row["ticker"]), str(row["source"]), str(row["counterfactual_path"]))].append(row)

    groups: list[dict[str, Any]] = []
    for (ticker, source, path_name), items in grouped.items():
        raw_count = len(raw_grouped.get((ticker, source, path_name), []))
        groups.append(
            {
                "ticker": ticker,
                "source": source,
                "counterfactual_path": path_name,
                "count": len(items),
                "raw_count": raw_count,
                "avg_missed_gain_pct": round(sum(_as_float(item.get("missed_gain_pct")) for item in items) / len(items), 4),
                "max_missed_gain_pct": round(max(_as_float(item.get("missed_gain_pct")) for item in items), 4),
                "max_buy_zone_high": round(max(_as_float(item.get("buy_zone_high")) for item in items), 4),
            }
        )
    groups.sort(key=lambda row: (int(row["count"]), _as_float(row["max_missed_gain_pct"])), reverse=True)
    rows.sort(key=lambda row: _as_float(row.get("missed_gain_pct")), reverse=True)
    return {
        "raw_candidate_count": len(raw_rows),
        "candidate_count": len(rows),
        "groups": groups[:top],
        "top_candidates": rows[:top],
        "decision": "validate_timing_before_live_buy_zone_change" if rows else "no_buy_zone_miss_signal",
        "improvement_direction": "Audit selection latency and buy-zone generation before considering narrow, gated live changes; do not widen US PathB buy zones globally.",
    }


def _us_exit_followups(results: list[dict[str, Any]], *, top: int) -> dict[str, Any]:
    signals = {"hard stop was followed by rebound", "target exit left material run-up"}
    raw_rows: list[dict[str, Any]] = []
    for result in results:
        market = str(result.get("market") or _params(result).get("market") or "").upper()
        if market != "US":
            continue
        for hint in _hints(result):
            if str(hint.get("signal") or "") in signals:
                raw_rows.append(_hint_row(result, hint, category="profitability"))

    rows = _best_by_key(raw_rows, ["scenario", "ticker", "signal"], score_field="score")
    rows.sort(
        key=lambda row: (
            _as_float(row.get("final_from_entry_pct")),
            _as_float(row.get("post_exit_runup_pct")),
            _as_float(row.get("score")),
        ),
        reverse=True,
    )
    by_signal: defaultdict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_signal[str(row.get("signal") or "")].append(_as_float(row.get("score")))
    return {
        "raw_candidate_count": len(raw_rows),
        "candidate_count": len(rows),
        "signal_stats": {signal: _stats(scores) for signal, scores in sorted(by_signal.items())},
        "top_candidates": rows[:top],
        "decision": "diagnostic_only_until_repeat_evidence" if rows else "no_exit_followup_signal",
        "improvement_direction": "Treat exit follow-up rows as diagnostics; do not change US PathB profit ladder, target, stop, or pre-close without repeated complete-coverage evidence.",
    }


def _kr_wait_candidates(results: list[dict[str, Any]], *, top: int) -> dict[str, Any]:
    raw_candidates: list[dict[str, Any]] = []
    for result in results:
        params = _params(result)
        market = str(result.get("market") or params.get("market") or "").upper()
        path_name = str(params.get("counterfactual_path") or "")
        if market != "KR" or path_name not in WAIT_PATHS:
            continue
        score = _result_score(result)
        metrics = result.get("metrics") or {}
        raw_candidates.append(
            {
                "market": "KR",
                "category": "profitability",
                "scenario": result.get("scenario", ""),
                "ticker": result.get("ticker", ""),
                "candidate_key": params.get("candidate_key", ""),
                "counterfactual_path": path_name,
                "score": round(score, 4),
                "entered": bool(metrics.get("entered")),
                "closed": bool(metrics.get("closed")),
                "observed_outcome_30m_pct": params.get("observed_outcome_30m_pct"),
                "observed_outcome_60m_pct": params.get("observed_outcome_60m_pct"),
                "observed_max_runup_60m_pct": params.get("observed_max_runup_60m_pct"),
                "observed_max_drawdown_60m_pct": params.get("observed_max_drawdown_60m_pct"),
                "source_report_path": result.get("_source_report_path", ""),
            }
        )
    candidates = _best_by_key(raw_candidates, ["candidate_key", "scenario", "ticker", "counterfactual_path"])
    by_path: defaultdict[str, list[float]] = defaultdict(list)
    for row in candidates:
        by_path[str(row.get("counterfactual_path") or "")].append(_as_float(row.get("score")))
    candidates.sort(key=lambda row: _as_float(row.get("score")), reverse=True)
    return {
        "raw_candidate_count": len(raw_candidates),
        "candidate_count": len(candidates),
        "path_stats": {path: _stats(scores) for path, scores in sorted(by_path.items())},
        "top_candidates": candidates[:top],
        "decision": "report_only_follow_up_queue" if candidates else "insufficient_evidence",
        "improvement_direction": "KR wait_30m/wait_60m candidates should be reviewed as follow-up analysis, not automatic orders.",
    }


def _us_high_price_blocks(results: list[dict[str, Any]], *, top: int) -> dict[str, Any]:
    raw_rows: list[dict[str, Any]] = []
    for result in results:
        params = _params(result)
        market = str(result.get("market") or params.get("market") or "").upper()
        if market != "US" or HIGH_PRICE_REASON not in _entry_block_reasons(result):
            continue
        fixed = _as_float(params.get("fixed_order_krw"))
        required = _required_budget_krw(result)
        metrics = result.get("metrics") or {}
        row = {
            "market": "US",
            "category": "operability_profitability",
            "scenario": result.get("scenario", ""),
            "ticker": result.get("ticker", ""),
            "source": params.get("source", ""),
            "counterfactual_path": params.get("counterfactual_path", ""),
            "score": round(_result_score(result), 4),
            "missed_gain_pct": metrics.get("missed_gain_pct", 0.0),
            "fixed_order_krw": fixed,
            "required_one_share_krw": required,
            "budget_gap_krw": round(max(0.0, required - fixed), 2),
            "source_report_path": result.get("_source_report_path", ""),
        }
        raw_rows.append(row)

    rows = _best_by_key(raw_rows, ["scenario", "ticker", "source", "counterfactual_path"], score_field="missed_gain_pct")
    grouped: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    raw_grouped: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["ticker"]), str(row["source"]), str(row["counterfactual_path"]))].append(row)
    for row in raw_rows:
        raw_grouped[(str(row["ticker"]), str(row["source"]), str(row["counterfactual_path"]))].append(row)

    group_rows: list[dict[str, Any]] = []
    for (ticker, source, path_name), items in grouped.items():
        raw_count = len(raw_grouped.get((ticker, source, path_name), []))
        group_rows.append(
            {
                "ticker": ticker,
                "source": source,
                "counterfactual_path": path_name,
                "count": len(items),
                "raw_count": raw_count,
                "avg_score": round(sum(_as_float(item.get("score")) for item in items) / len(items), 4),
                "max_missed_gain_pct": round(max(_as_float(item.get("missed_gain_pct")) for item in items), 4),
                "max_budget_gap_krw": round(max(_as_float(item.get("budget_gap_krw")) for item in items), 2),
            }
        )
    group_rows.sort(key=lambda row: (int(row["count"]), _as_float(row["max_budget_gap_krw"])), reverse=True)
    rows.sort(key=lambda row: (_as_float(row.get("budget_gap_krw")), _as_float(row.get("score"))), reverse=True)
    return {
        "raw_blocked_count": len(raw_rows),
        "blocked_count": len(rows),
        "groups": group_rows[:top],
        "top_blocks": rows[:top],
        "decision": "report_only_no_global_budget_change" if rows else "no_blocks_found",
        "improvement_direction": "Separate high-price blocked candidates from live budget policy; do not raise global fixed budget from this signal alone.",
    }


def _coverage_audit(results: list[dict[str, Any]], *, top: int) -> dict[str, Any]:
    by_status: Counter[str] = Counter()
    by_flag: Counter[str] = Counter()
    incomplete: list[dict[str, Any]] = []
    for result in results:
        coverage = _coverage(result)
        if not coverage:
            continue
        status = str(coverage.get("coverage_status") or "unknown")
        by_status[status] += 1
        for flag in coverage.get("coverage_flags") or []:
            by_flag[str(flag or "unknown")] += 1
        if status != "complete":
            incomplete.append(
                {
                    "market": result.get("market", ""),
                    "ticker": result.get("ticker", ""),
                    "scenario": result.get("scenario", ""),
                    "source": _params(result).get("source", ""),
                    "coverage_status": status,
                    "coverage_flags": coverage.get("coverage_flags") or [],
                    "requested_start_at": coverage.get("requested_start_at", ""),
                    "requested_end_at": coverage.get("requested_end_at", ""),
                    "actual_start_at": coverage.get("actual_start_at", ""),
                    "actual_end_at": coverage.get("actual_end_at", ""),
                    "matched_rows": coverage.get("matched_rows", 0),
                    "source_report_path": result.get("_source_report_path", ""),
                }
            )
    incomplete.sort(key=lambda row: str(row.get("coverage_status") or ""))
    unique_incomplete = _best_by_key(
        incomplete,
        [
            "market",
            "ticker",
            "scenario",
            "coverage_status",
            "requested_start_at",
            "requested_end_at",
            "actual_start_at",
            "actual_end_at",
        ],
        score_field="matched_rows",
    )
    unique_incomplete.sort(key=lambda row: str(row.get("coverage_status") or ""))
    return {
        "by_status": dict(sorted(by_status.items())),
        "by_flag": dict(sorted(by_flag.items())),
        "raw_incomplete_count": len(incomplete),
        "incomplete_count": len(unique_incomplete),
        "incomplete_examples": unique_incomplete[:top],
        "decision": "audit_before_profitability_judgment" if unique_incomplete else "coverage_clean_for_reported_cases",
        "improvement_direction": "Treat incomplete price windows as data-quality cases before using those rows for profitability changes.",
    }


def _output_dir(output_root: str | Path, output_dir: str) -> Path:
    root = Path(output_root).expanduser()
    target = Path(output_dir).expanduser() if output_dir else root / _now_stamp()
    if not target.is_absolute():
        target = root / target
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames or ["empty"])
        writer.writeheader()
        writer.writerows(rows)


def _md_table(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    if not rows:
        return ["_no rows_"]
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return lines


def _write_md(payload: dict[str, Any], path: Path) -> None:
    kr_wait = payload["categories"]["KR"]["profitability"]["wait_followup"]
    us_blocks = payload["categories"]["US"]["operability_profitability"]["high_price_blocks"]
    us_buy_zone = payload["categories"]["US"]["profitability"]["buy_zone_misses"]
    us_exit = payload["categories"]["US"]["profitability"]["exit_followup"]
    coverage = payload["categories"]["common"]["operability_bug"]["price_coverage"]
    lines: list[str] = [
        "# Ops Simulation Improvement Report",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- input_report_count: {len(payload['input_reports'])}",
        f"- result_count: {payload['result_count']}",
        f"- live_writes_performed: {payload['live_writes_performed']}",
        "",
        "## KR / profitability / wait follow-up",
        "",
        f"- decision: {kr_wait['decision']}",
        f"- candidate_count: {kr_wait['candidate_count']}",
        f"- raw_candidate_count: {kr_wait.get('raw_candidate_count', kr_wait['candidate_count'])}",
        f"- direction: {kr_wait['improvement_direction']}",
        "",
    ]
    path_rows = [{"path": path_name, **stats} for path_name, stats in kr_wait["path_stats"].items()]
    lines.extend(_md_table(path_rows, ["path", "count", "avg", "median", "best", "worst"]))
    lines.extend(["", "### Top KR Wait Candidates", ""])
    lines.extend(
        _md_table(
            kr_wait["top_candidates"],
            ["ticker", "counterfactual_path", "score", "observed_outcome_60m_pct", "observed_max_runup_60m_pct"],
        )
    )
    lines.extend(
        [
            "",
            "## US / operability_profitability / high-price blocks",
            "",
            f"- decision: {us_blocks['decision']}",
            f"- blocked_count: {us_blocks['blocked_count']}",
            f"- raw_blocked_count: {us_blocks.get('raw_blocked_count', us_blocks['blocked_count'])}",
            f"- direction: {us_blocks['improvement_direction']}",
            "",
        ]
    )
    lines.extend(
        _md_table(
            us_blocks["groups"],
            ["ticker", "source", "counterfactual_path", "count", "raw_count", "avg_score", "max_budget_gap_krw"],
        )
    )
    lines.extend(
        [
            "",
            "## US / profitability / buy-zone misses",
            "",
            f"- decision: {us_buy_zone['decision']}",
            f"- candidate_count: {us_buy_zone['candidate_count']}",
            f"- raw_candidate_count: {us_buy_zone.get('raw_candidate_count', us_buy_zone['candidate_count'])}",
            f"- direction: {us_buy_zone['improvement_direction']}",
            "",
        ]
    )
    lines.extend(
        _md_table(
            us_buy_zone["groups"],
            ["ticker", "source", "counterfactual_path", "count", "raw_count", "avg_missed_gain_pct", "max_missed_gain_pct"],
        )
    )
    lines.extend(["", "### Top US Buy-Zone Miss Candidates", ""])
    lines.extend(
        _md_table(
            us_buy_zone["top_candidates"],
            ["ticker", "score", "missed_gain_pct", "buy_zone_high", "scenario"],
        )
    )
    lines.extend(
        [
            "",
            "## US / profitability / exit follow-up",
            "",
            f"- decision: {us_exit['decision']}",
            f"- candidate_count: {us_exit['candidate_count']}",
            f"- raw_candidate_count: {us_exit.get('raw_candidate_count', us_exit['candidate_count'])}",
            f"- direction: {us_exit['improvement_direction']}",
            "",
        ]
    )
    signal_rows = [{"signal": signal, **stats} for signal, stats in us_exit["signal_stats"].items()]
    lines.extend(_md_table(signal_rows, ["signal", "count", "avg", "median", "best", "worst"]))
    lines.extend(["", "### Top US Exit Follow-Up Candidates", ""])
    lines.extend(
        _md_table(
            us_exit["top_candidates"],
            ["ticker", "signal", "score", "final_from_entry_pct", "post_exit_runup_pct", "scenario"],
        )
    )
    lines.extend(["", "## Common / operability_bug / price coverage", ""])
    lines.extend(
        [
            f"- decision: {coverage['decision']}",
            f"- by_status: {json.dumps(coverage['by_status'], ensure_ascii=False, sort_keys=True)}",
            f"- by_flag: {json.dumps(coverage['by_flag'], ensure_ascii=False, sort_keys=True)}",
            f"- incomplete_count: {coverage['incomplete_count']}",
            f"- raw_incomplete_count: {coverage.get('raw_incomplete_count', coverage['incomplete_count'])}",
            f"- direction: {coverage['improvement_direction']}",
            "",
        ]
    )
    lines.extend(_md_table(coverage["incomplete_examples"], ["market", "ticker", "coverage_status", "matched_rows", "actual_start_at", "actual_end_at"]))
    lines.extend(
        [
            "",
            "## Apply Prohibitions",
            "",
            "- Do not widen US PathB live buy zones from counterfactual-only evidence.",
            "- Do not change US PathB profit ladder, pre-close, target, or stop from this report.",
            "- Do not raise global fixed order budget from high-price block counts alone.",
            "- Do not connect counterfactual candidates to automatic order submission.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_improvement_report(
    report_paths: list[str | Path],
    *,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    output_dir: str = "",
    top: int = 25,
) -> dict[str, Any]:
    if not report_paths:
        raise RehearsalGuardError("at least one --report is required")
    reports = [_load_report(Path(path)) for path in report_paths]
    results = _iter_results(reports)
    out_dir = _output_dir(output_root, output_dir)
    kr_wait = _kr_wait_candidates(results, top=top)
    us_blocks = _us_high_price_blocks(results, top=top)
    us_buy_zone = _us_buy_zone_misses(results, top=top)
    us_exit = _us_exit_followups(results, top=top)
    coverage = _coverage_audit(results, top=top)
    payload: dict[str, Any] = {
        "ok": True,
        "generated_at": _now_text(),
        "input_reports": [str(Path(path).expanduser()) for path in report_paths],
        "result_count": len(results),
        "live_writes_performed": False,
        "categories": {
            "common": {"operability_bug": {"price_coverage": coverage}},
            "KR": {"profitability": {"wait_followup": kr_wait}},
            "US": {
                "operability_profitability": {"high_price_blocks": us_blocks},
                "profitability": {
                    "buy_zone_misses": us_buy_zone,
                    "exit_followup": us_exit,
                },
            },
        },
        "apply_prohibitions": [
            "US PathB live buy zone widening",
            "US PathB profit ladder/pre-close/target/stop changes",
            "global fixed order budget increase",
            "automatic order submission from counterfactual candidates",
        ],
    }
    json_path = out_dir / "ops_simulation_improvement_report.json"
    md_path = out_dir / "ops_simulation_improvement_report.md"
    kr_csv_path = out_dir / "kr_wait_followup_candidates.csv"
    us_csv_path = out_dir / "us_high_price_blocks.csv"
    us_buy_zone_csv_path = out_dir / "us_buy_zone_misses.csv"
    us_exit_csv_path = out_dir / "us_exit_followup_candidates.csv"
    payload["output_paths"] = {
        "json": str(json_path),
        "md": str(md_path),
        "kr_wait_csv": str(kr_csv_path),
        "us_high_price_csv": str(us_csv_path),
        "us_buy_zone_csv": str(us_buy_zone_csv_path),
        "us_exit_csv": str(us_exit_csv_path),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _write_md(payload, md_path)
    _write_csv(kr_csv_path, kr_wait["top_candidates"])
    _write_csv(us_csv_path, us_blocks["top_blocks"])
    _write_csv(us_buy_zone_csv_path, us_buy_zone["top_candidates"])
    _write_csv(us_exit_csv_path, us_exit["top_candidates"])
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build categorized improvement reports from ops simulation JSON reports.")
    parser.add_argument("--report", action="append", default=[], help="ops_simulate JSON report path; may be repeated")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--output-dir", default="", help="relative directory under output root; default timestamp")
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = build_improvement_report(
            args.report,
            output_root=args.output_root,
            output_dir=args.output_dir,
            top=args.top,
        )
    except RehearsalGuardError as exc:
        error = {"ok": False, "error": str(exc)}
        if args.json:
            print(json.dumps(error, ensure_ascii=False, indent=2))
        else:
            print(f"ops_simulation_improvement_report failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            "ok "
            f"results={payload['result_count']} "
            f"md={payload['output_paths']['md']} "
            f"json={payload['output_paths']['json']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
