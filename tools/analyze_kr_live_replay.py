from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path


DEFAULT_TICKERS = ("208710", "242040", "457370", "126730")


def _date_key(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}", text
    dashed = text[:10]
    return dashed, dashed.replace("-", "")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except Exception:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _counter_dict(counter: Counter) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _route_bucket(route: dict[str, Any]) -> str:
    route_name = str(route.get("route") or "").strip()
    if route_name:
        return route_name
    final_action = str(route.get("final_action") or "").strip()
    return final_action or "UNKNOWN"


def _error_reason_counts(payload: dict[str, Any]) -> Counter:
    counts: Counter[str] = Counter()
    for key in (
        "provider_timeout_count",
        "prefetch_timeout_count",
        "http_error_count",
        "kis_500_count",
        "other_error_count",
    ):
        value = payload.get(key)
        try:
            count = int(value or 0)
        except Exception:
            count = 0
        if count:
            counts[key] += count
    if counts:
        return counts
    for raw in payload.get("errors_sample") or []:
        text = str(raw or "").lower()
        if "provider_timeout" in text:
            counts["provider_timeout_count"] += 1
        elif "prefetch_timeout" in text or "timeout_or_cancelled" in text:
            counts["prefetch_timeout_count"] += 1
        elif "500" in text:
            counts["kis_500_count"] += 1
            counts["http_error_count"] += 1
        elif "http" in text or "status" in text:
            counts["http_error_count"] += 1
        elif text:
            counts["other_error_count"] += 1
    return counts


def _v2_counts(db_path: Path, *, session_date: str, market: str, runtime_mode: str) -> dict[str, Any]:
    if not db_path.exists():
        return {
            "db_path": str(db_path),
            "available": False,
            "v2_decisions": 0,
            "v2_path_runs": 0,
            "path_run_status_counts": {},
        }
    conn = sqlite3.connect(str(db_path))
    try:
        decisions = conn.execute(
            """
            SELECT COUNT(*)
            FROM v2_decisions
            WHERE session_date = ? AND market = ? AND runtime_mode = ?
            """,
            (session_date, market, runtime_mode),
        ).fetchone()[0]
        path_runs = conn.execute(
            """
            SELECT COUNT(*)
            FROM v2_path_runs
            WHERE session_date = ? AND market = ? AND runtime_mode = ?
            """,
            (session_date, market, runtime_mode),
        ).fetchone()[0]
        status_counts = {
            str(status): int(count)
            for status, count in conn.execute(
                """
                SELECT status, COUNT(*)
                FROM v2_path_runs
                WHERE session_date = ? AND market = ? AND runtime_mode = ?
                GROUP BY status
                """,
                (session_date, market, runtime_mode),
            ).fetchall()
        }
    except sqlite3.Error:
        return {
            "db_path": str(db_path),
            "available": False,
            "v2_decisions": 0,
            "v2_path_runs": 0,
            "path_run_status_counts": {},
        }
    finally:
        conn.close()
    return {
        "db_path": str(db_path),
        "available": True,
        "v2_decisions": int(decisions or 0),
        "v2_path_runs": int(path_runs or 0),
        "path_run_status_counts": dict(sorted(status_counts.items())),
    }


def _summarize_candidate_funnel(rows: list[dict[str, Any]]) -> dict[str, Any]:
    action_counts: Counter[str] = Counter()
    route_counts: Counter[str] = Counter()
    route_reason_counts: Counter[str] = Counter()
    pathb_wait_snapshots = 0
    max_pathb_wait = 0
    for row in rows:
        action_counts.update({str(key): int(value or 0) for key, value in (row.get("candidate_action_counts") or {}).items()})
        pathb_wait = list(row.get("pathb_wait_tickers") or [])
        if pathb_wait:
            pathb_wait_snapshots += 1
        max_pathb_wait = max(max_pathb_wait, len(pathb_wait))
        for route in row.get("candidate_action_routes") or []:
            if not isinstance(route, dict):
                continue
            route_counts[_route_bucket(route)] += 1
            reason = str(route.get("reason") or route.get("runtime_gate_reason") or "")
            route_reason_counts[reason] += 1
    return {
        "snapshot_count": len(rows),
        "candidate_action_counts": _counter_dict(action_counts),
        "candidate_action_route_counts": _counter_dict(route_counts),
        "candidate_action_route_reason_counts": _counter_dict(route_reason_counts),
        "pathb_wait_snapshots": pathb_wait_snapshots,
        "max_pathb_wait_tickers": max_pathb_wait,
    }


def _summarize_action_shadow(rows: list[dict[str, Any]]) -> dict[str, Any]:
    route_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    total_routes = 0
    for row in rows:
        for route in row.get("routes") or []:
            if not isinstance(route, dict):
                continue
            total_routes += 1
            route_counts[_route_bucket(route)] += 1
            reason_counts[str(route.get("reason") or "")] += 1
    return {
        "event_count": len(rows),
        "route_count": total_routes,
        "route_stage": "pre_gate_shadow",
        "pre_gate_shadow_route_counts": _counter_dict(route_counts),
        "pre_gate_shadow_reason_counts": _counter_dict(reason_counts),
        "pre_gate_pathb_wait_count": int(route_counts.get("PathB.wait", 0) + route_counts.get("PULLBACK_WAIT", 0)),
    }


def _summarize_gate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    final_counts: Counter[str] = Counter()
    route_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    claude_counts: Counter[str] = Counter()
    quality_counts: Counter[str] = Counter()
    evidence_state_counts: Counter[str] = Counter()
    evidence_ceiling_counts: Counter[str] = Counter()
    ticker_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        runtime_gate = row.get("runtime_gate") if isinstance(row.get("runtime_gate"), dict) else {}
        final = str(row.get("final_action") or "")
        route = str(row.get("route") or "")
        reason = str(row.get("reason") or row.get("runtime_gate_reason") or "")
        ticker = str(row.get("ticker") or "")
        final_counts[final] += 1
        route_counts[route or final or "UNKNOWN"] += 1
        reason_counts[reason] += 1
        claude_counts[str(row.get("claude_action") or row.get("requested_action") or "")] += 1
        quality_counts[str(runtime_gate.get("data_quality") or "")] += 1
        evidence_state_counts[str(runtime_gate.get("evidence_data_state") or "")] += 1
        evidence_ceiling_counts[str(runtime_gate.get("evidence_action_ceiling") or "")] += 1
        if ticker:
            ticker_counts[ticker][reason] += 1
    return {
        "row_count": len(rows),
        "final_action_counts": _counter_dict(final_counts),
        "final_route_counts": _counter_dict(route_counts),
        "reason_counts": _counter_dict(reason_counts),
        "claude_action_counts": _counter_dict(claude_counts),
        "data_quality_counts": _counter_dict(quality_counts),
        "evidence_data_state_counts": _counter_dict(evidence_state_counts),
        "evidence_action_ceiling_counts": _counter_dict(evidence_ceiling_counts),
        "final_pathb_wait_count": int(route_counts.get("PathB.wait", 0) + final_counts.get("PULLBACK_WAIT", 0)),
        "final_plan_a_buy_count": int(route_counts.get("PlanA.buy", 0)),
        "ticker_reason_counts": {ticker: _counter_dict(counts) for ticker, counts in sorted(ticker_counts.items())},
    }


def _summarize_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    error_counts: Counter[str] = Counter()
    timeline: list[dict[str, Any]] = []
    degradation_rows = 0
    for row in rows:
        counts = _error_reason_counts(row)
        error_counts.update(counts)
        requested = int(row.get("requested") or 0)
        complete = int(row.get("complete") or 0)
        missing = int(row.get("missing") or 0)
        if requested and complete < requested:
            degradation_rows += 1
        timeline.append(
            {
                "written_at": row.get("written_at"),
                "phase": row.get("phase"),
                "requested": requested,
                "fetched": int(row.get("fetched") or 0),
                "complete": complete,
                "partial": int(row.get("partial") or 0),
                "missing": missing,
                "coverage_ratio": row.get("coverage_ratio", row.get("complete_ratio")),
                "fail_closed_applied": bool(row.get("fail_closed_applied")),
                "error_count": int(row.get("error_count") or len(row.get("errors_sample") or [])),
                "provider_timeout_count": int(row.get("provider_timeout_count") or counts.get("provider_timeout_count") or 0),
                "prefetch_timeout_count": int(row.get("prefetch_timeout_count") or counts.get("prefetch_timeout_count") or 0),
                "kis_500_count": int(row.get("kis_500_count") or counts.get("kis_500_count") or 0),
                "worker_count": row.get("worker_count"),
                "timeout_seconds": row.get("timeout_seconds"),
                "min_call_interval_seconds": row.get("min_call_interval_seconds"),
                "elapsed_seconds": row.get("elapsed_seconds"),
                "reason_visibility": "full_counts" if row.get("provider_timeout_count") is not None else "sample_limited",
            }
        )
    return {
        "event_count": len(rows),
        "degradation_rows": degradation_rows,
        "error_reason_counts": _counter_dict(error_counts),
        "timeline": timeline,
    }


def _summarize_post_open(rows: list[dict[str, Any]]) -> dict[str, Any]:
    quality_counts: Counter[str] = Counter()
    missing_fields: Counter[str] = Counter()
    fields = ("volume_ratio_open", "spread_bps", "vwap_distance_pct", "opening_range_break")
    for row in rows:
        quality_counts[str(row.get("data_quality") or "")] += 1
        for field in fields:
            if row.get(field) in (None, ""):
                missing_fields[field] += 1
    return {
        "row_count": len(rows),
        "data_quality_counts": _counter_dict(quality_counts),
        "missing_field_counts": _counter_dict(missing_fields),
        "feature_surface": "post_open_feature_builder",
        "gate_evidence_preferred_for_replay": True,
    }


def _summarize_shadow_events(rows: list[dict[str, Any]], *, event_name: str) -> dict[str, Any]:
    if not rows:
        return {"available": False, "event": event_name, "count": 0, "decision_counts": {"not_available": 0}}
    decisions = Counter(str(row.get("shadow_decision") or row.get("reason") or "recorded") for row in rows)
    tickers = sorted({str(row.get("ticker") or "") for row in rows if row.get("ticker")})
    return {
        "available": True,
        "event": event_name,
        "count": len(rows),
        "decision_counts": _counter_dict(decisions),
        "tickers": tickers,
    }


def _outcome_by_ticker(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue
        item = out.setdefault(
            ticker,
            {
                "ticker": ticker,
                "post_open_5m_return_pct": None,
                "post_open_30m_return_pct": None,
                "post_open_60m_return_pct": None,
                "post_open_90m_return_pct": None,
                "post_open_120m_return_pct": None,
                "post_open_mfe_pct": None,
                "post_open_mae_pct": None,
            },
        )
        for key in list(item.keys()):
            if key == "ticker":
                continue
            if row.get(key) is not None:
                item[key] = row.get(key)
    return out


def analyze_kr_live_replay(
    *,
    date: str,
    market: str = "KR",
    runtime_mode: str = "live",
    log_dir: str | Path | None = None,
    v2_db_path: str | Path | None = None,
    tickers: list[str] | None = None,
) -> dict[str, Any]:
    session_date, compact = _date_key(date)
    market_key = str(market or "KR").upper()
    base = Path(log_dir) if log_dir is not None else get_runtime_path("logs", "funnel")
    preopen_dir = base.parent / "preopen" if base.name == "funnel" else base
    paths = {
        "candidate_funnel": base / f"candidate_funnel_snapshot_{compact}_{market_key}.jsonl",
        "action_shadow": base / f"action_routing_shadow_{compact}_{market_key}.jsonl",
        "gate": base / f"gate_evaluation_{compact}_{market_key}.jsonl",
        "coverage": base / f"selection_intraday_evidence_coverage_{compact}_{market_key}.jsonl",
        "post_open": base / f"post_open_features_{compact}_{market_key}.jsonl",
        "healthy_shadow": base / f"kr_healthy_pullback_shadow_{compact}_{market_key}.jsonl",
        "no_signal_shadow": base / f"kr_plan_a_no_signal_pathb_shadow_{compact}_{market_key}.jsonl",
        "outcome": preopen_dir / f"{compact}_{market_key}_outcome.jsonl",
    }
    candidate_rows = _read_jsonl(paths["candidate_funnel"])
    shadow_rows = _read_jsonl(paths["action_shadow"])
    gate_rows = _read_jsonl(paths["gate"])
    coverage_rows = _read_jsonl(paths["coverage"])
    post_open_rows = _read_jsonl(paths["post_open"])
    healthy_shadow_rows = _read_jsonl(paths["healthy_shadow"])
    no_signal_shadow_rows = _read_jsonl(paths["no_signal_shadow"])
    outcome_rows = _read_jsonl(paths["outcome"])
    outcomes = _outcome_by_ticker(outcome_rows)
    gate_summary = _summarize_gate(gate_rows)
    focus = tickers or list(DEFAULT_TICKERS)
    ticker_breakdown: dict[str, Any] = {}
    ticker_reason_counts = gate_summary.get("ticker_reason_counts") or {}
    for ticker in focus:
        key = str(ticker or "").strip()
        if not key:
            continue
        ticker_breakdown[key] = {
            "gate_reason_counts": ticker_reason_counts.get(key, {}),
            "outcome": outcomes.get(key, {}),
            "healthy_pullback_shadow": [
                row for row in healthy_shadow_rows if str(row.get("ticker") or "") == key
            ],
            "plan_a_no_signal_shadow": [
                row for row in no_signal_shadow_rows if str(row.get("ticker") or "") == key
            ],
        }
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "filters": {
            "date": session_date,
            "market": market_key,
            "runtime_mode": runtime_mode,
            "log_dir": str(base),
        },
        "paths": {key: str(value) for key, value in paths.items()},
        "truth_contract": {
            "action_routing_shadow_stage": "pre_gate_shadow",
            "final_route_sources": ["gate_evaluation", "candidate_funnel.pathb_wait_tickers", "v2_event_store"],
            "shadow_routes_are_not_final_executable_routes": True,
        },
        "candidate_funnel": _summarize_candidate_funnel(candidate_rows),
        "action_routing_shadow": _summarize_action_shadow(shadow_rows),
        "gate_evaluation": gate_summary,
        "selection_intraday_evidence_coverage": _summarize_coverage(coverage_rows),
        "post_open_features": _summarize_post_open(post_open_rows),
        "v2_event_store": _v2_counts(
            Path(v2_db_path or get_runtime_path("data", "v2_event_store.db")),
            session_date=session_date,
            market=market_key,
            runtime_mode=runtime_mode,
        ),
        "phase2_shadow_events": {
            "healthy_pullback": _summarize_shadow_events(
                healthy_shadow_rows,
                event_name="kr_healthy_pullback_shadow",
            ),
            "plan_a_no_signal": _summarize_shadow_events(
                no_signal_shadow_rows,
                event_name="kr_plan_a_no_signal_pathb_shadow",
            ),
        },
        "ticker_breakdown": ticker_breakdown,
    }


def to_markdown(payload: dict[str, Any]) -> str:
    filters = payload.get("filters") or {}
    action_shadow = payload.get("action_routing_shadow") or {}
    gate = payload.get("gate_evaluation") or {}
    v2 = payload.get("v2_event_store") or {}
    lines = [
        "# KR Live Replay Review",
        "",
        f"Generated: {payload.get('generated_at', '')}",
        f"Market/date/runtime: {filters.get('market', '')}/{filters.get('date', '')}/{filters.get('runtime_mode', '')}",
        "",
        "## Truth Contract",
        "",
        "- `action_routing_shadow` is pre-gate shadow only.",
        "- Final executable route is based on gate evaluation, candidate funnel PathB wait, and v2 event store.",
        "",
        "## Route Summary",
        "",
        f"- Pre-gate shadow PathB.wait: {action_shadow.get('pre_gate_pathb_wait_count', 0)}",
        f"- Final PlanA.buy: {gate.get('final_plan_a_buy_count', 0)}",
        f"- Final PathB.wait: {gate.get('final_pathb_wait_count', 0)}",
        f"- v2 decisions/path runs: {v2.get('v2_decisions', 0)} / {v2.get('v2_path_runs', 0)}",
        "",
        "## Gate Reasons",
        "",
    ]
    for reason, count in (gate.get("reason_counts") or {}).items():
        lines.append(f"- `{reason}`: {count}")
    lines.extend(["", "## Evidence Coverage", ""])
    coverage = payload.get("selection_intraday_evidence_coverage") or {}
    for row in coverage.get("timeline") or []:
        lines.append(
            "- "
            f"{row.get('written_at')} requested={row.get('requested')} complete={row.get('complete')} "
            f"missing={row.get('missing')} fail_closed={row.get('fail_closed_applied')} "
            f"provider_timeout={row.get('provider_timeout_count')} prefetch_timeout={row.get('prefetch_timeout_count')} "
            f"visibility={row.get('reason_visibility')}"
        )
    lines.extend(["", "## Phase2 Shadow Events", ""])
    for key, item in (payload.get("phase2_shadow_events") or {}).items():
        lines.append(f"- {key}: available={item.get('available')} count={item.get('count')}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only KR live replay analyzer.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--market", default="KR")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--log-dir", default=str(get_runtime_path("logs", "funnel")))
    parser.add_argument("--v2-db-path", default=str(get_runtime_path("data", "v2_event_store.db")))
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    tickers = [item.strip() for item in str(args.tickers or "").split(",") if item.strip()]
    payload = analyze_kr_live_replay(
        date=args.date,
        market=args.market,
        runtime_mode=args.runtime_mode,
        log_dir=args.log_dir,
        v2_db_path=args.v2_db_path,
        tickers=tickers,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(to_markdown(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
