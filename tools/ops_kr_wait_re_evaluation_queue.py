from __future__ import annotations

import argparse
import csv
import json
import sqlite3
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
DEFAULT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
DEFAULT_OUTPUT_ROOT = ROOT / ".runtime" / "ops_simulation_analysis"
WAIT_PATHS = {"wait_30m", "wait_60m"}
DEFAULT_ROUTE_SOURCES = {"analyst_reinvoke"}
DEFAULT_EVIDENCE_STATES = {"confirmed", "partial"}
DEFAULT_EXCLUDED_ENTRY_BUCKETS = {"LATE_AFTER_270"}
DEFAULT_EVIDENCE_ACTION_CEILINGS: set[str] = set()
DEFAULT_REQUIRED_ENTRY_BUCKETS: set[str] = set()
POSITIVE_FRESHNESS = {"LIVE", "FRESH", "RECENT", "CONFIRMED", "INTRADAY"}


def _now_stamp() -> str:
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S")


def _now_text() -> str:
    return datetime.now(KST).replace(microsecond=0).isoformat()


def _ro_uri(path: Path) -> str:
    return f"file:{path.resolve().as_posix()}?mode=ro"


def _connect_ro(path: Path) -> sqlite3.Connection:
    source = Path(path)
    if not source.exists():
        raise RehearsalGuardError(f"candidate audit DB not found: {source}")
    conn = sqlite3.connect(_ro_uri(source), uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _json_obj(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return float(default)
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return float(default)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return int(default)


def _stats(values: list[float]) -> dict[str, Any]:
    vals = [float(value) for value in values if value is not None]
    if not vals:
        return {"count": 0, "avg": 0.0, "median": 0.0, "best": 0.0, "worst": 0.0, "win_rate_pct": 0.0}
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 4),
        "median": round(float(median(vals)), 4),
        "best": round(max(vals), 4),
        "worst": round(min(vals), 4),
        "win_rate_pct": round(sum(1 for value in vals if value > 0) / len(vals) * 100.0, 2),
    }


def _output_dir(output_root: str | Path, output_dir: str) -> Path:
    root = Path(output_root).expanduser()
    target = Path(output_dir).expanduser() if output_dir else root / f"kr_wait_re_evaluation_{_now_stamp()}"
    if not target.is_absolute():
        target = root / target
    resolved_root = root.resolve()
    resolved_target = target.resolve()
    if resolved_target != resolved_root and resolved_root not in resolved_target.parents:
        raise RehearsalGuardError(f"output_dir must stay under output_root: {target}")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _split_set(raw: str | list[str] | tuple[str, ...] | set[str]) -> set[str]:
    if isinstance(raw, (list, tuple, set)):
        values: list[str] = []
        for item in raw:
            values.extend(str(item or "").split(","))
    else:
        values = str(raw or "").split(",")
    return {value.strip() for value in values if value.strip()}


def _format_policy_set(values: set[str]) -> str:
    return ", ".join(sorted(values)) if values else "(all)"


def _context(metadata_json: Any) -> dict[str, Any]:
    metadata = _json_obj(metadata_json)
    context = metadata.get("context")
    return context if isinstance(context, dict) else {}


def _row_route_source(row: sqlite3.Row, context: dict[str, Any]) -> str:
    return str(context.get("route_source") or row["route_source"] or "").strip()


def _row_evidence_state(row: sqlite3.Row, context: dict[str, Any]) -> str:
    return str(row["evidence_data_state"] or context.get("evidence_data_state") or "").strip().lower()


def _row_evidence_ceiling(row: sqlite3.Row, context: dict[str, Any]) -> str:
    return str(row["evidence_action_ceiling"] or context.get("evidence_action_ceiling") or "").strip().upper()


def _row_freshness(row: sqlite3.Row, context: dict[str, Any]) -> str:
    return str(row["freshness_verdict"] or context.get("freshness_verdict") or "").strip().upper()


def _live_visible_score(row: sqlite3.Row, context: dict[str, Any]) -> int:
    score = 0
    evidence_state = _row_evidence_state(row, context)
    ceiling = _row_evidence_ceiling(row, context)
    freshness = _row_freshness(row, context)
    bucket = str(context.get("entry_window_bucket") or "").strip().upper()
    if evidence_state == "confirmed":
        score += 30
    elif evidence_state == "partial":
        score += 18
    if ceiling == "BUY_READY":
        score += 20
    elif ceiling == "PROBE_READY":
        score += 14
    elif ceiling == "WATCH":
        score += 6
    if freshness in POSITIVE_FRESHNESS:
        score += 10
    elif freshness in {"PARTIAL", "MODERATE", "COMPACT"}:
        score += 4
    if bucket in {"OPEN_30_60", "OPEN_60_90", "OPEN_90_270"}:
        score += 6
    elif bucket == "OPEN_0_30":
        score += 3
    return score


def _fetch_rows(
    conn: sqlite3.Connection,
    *,
    start_date: str,
    end_date: str,
    limit: int,
    status: str,
) -> list[sqlite3.Row]:
    filters = [
        "p.runtime_mode='live'",
        "p.market='KR'",
        "p.path_name IN ('wait_30m', 'wait_60m')",
        "COALESCE(p.actual_path, '')='no_entry'",
        "p.trigger_time IS NOT NULL",
        "p.entry_price IS NOT NULL",
    ]
    params: list[Any] = []
    if status and status.upper() != "ANY":
        filters.append("p.status=?")
        params.append(status)
    if start_date:
        filters.append("p.session_date >= ?")
        params.append(start_date)
    if end_date:
        filters.append("p.session_date <= ?")
        params.append(end_date)
    params.append(int(limit))
    sql = f"""
        SELECT
            p.id,
            p.runtime_mode,
            p.session_date,
            p.market,
            p.ticker,
            p.candidate_key,
            p.call_id,
            p.signal_time,
            p.known_at,
            p.trade_ready_action,
            p.actual_path,
            p.path_name,
            p.trigger_time,
            p.trigger_price,
            p.trigger_reason,
            p.entry_price,
            p.entry_delay_min,
            p.outcome_30m_pct,
            p.outcome_60m_pct,
            p.outcome_close_pct,
            p.max_runup_60m_pct,
            p.max_drawdown_60m_pct,
            p.status,
            p.metadata_json,
            p.metadata_quality,
            p.label_source,
            COALESCE(r.freshness_verdict, '') AS freshness_verdict,
            COALESCE(r.evidence_data_state, '') AS evidence_data_state,
            COALESCE(r.evidence_action_ceiling, '') AS evidence_action_ceiling,
            COALESCE(r.route_final_action, '') AS route_final_action,
            COALESCE(r.route_reason, '') AS route_reason,
            COALESCE(r.route_runtime_gate_reason, '') AS route_runtime_gate_reason,
            COALESCE(r.trainer_candidate_state, '') AS trainer_candidate_state,
            COALESCE(r.action_ceiling, '') AS action_ceiling,
            COALESCE(r.why_not_watch, '') AS why_not_watch,
            COALESCE(r.payload_json, '') AS audit_payload_json,
            '' AS route_source
        FROM candidate_counterfactual_paths p
        LEFT JOIN audit_candidate_rows r ON r.candidate_key=p.candidate_key
        WHERE {' AND '.join(filters)}
        ORDER BY p.session_date DESC, p.trigger_time DESC, p.id DESC
        LIMIT ?
    """
    return list(conn.execute(sql, params))


def _candidate_payload(row: sqlite3.Row, context: dict[str, Any], *, status: str, reason: str) -> dict[str, Any]:
    label_outcome_60m = _as_float(row["outcome_60m_pct"])
    label_drawdown_60m = _as_float(row["max_drawdown_60m_pct"])
    return {
        "queue_status": status,
        "queue_reason": reason,
        "session_date": row["session_date"],
        "ticker": row["ticker"],
        "candidate_key": row["candidate_key"],
        "path_name": row["path_name"],
        "known_at": row["known_at"],
        "trigger_time": row["trigger_time"],
        "entry_price": row["entry_price"],
        "route_source": _row_route_source(row, context),
        "freshness_verdict": _row_freshness(row, context),
        "evidence_data_state": _row_evidence_state(row, context),
        "evidence_action_ceiling": _row_evidence_ceiling(row, context),
        "entry_window_bucket": str(context.get("entry_window_bucket") or ""),
        "live_visible_score": _live_visible_score(row, context),
        "route_final_action": row["route_final_action"],
        "route_reason": row["route_reason"],
        "route_runtime_gate_reason": row["route_runtime_gate_reason"],
        "trainer_candidate_state": row["trainer_candidate_state"],
        "why_not_watch": row["why_not_watch"],
        "historical_label": {
            "outcome_30m_pct": _as_float(row["outcome_30m_pct"]),
            "outcome_60m_pct": label_outcome_60m,
            "outcome_close_pct": _as_float(row["outcome_close_pct"]),
            "max_runup_60m_pct": _as_float(row["max_runup_60m_pct"]),
            "max_drawdown_60m_pct": label_drawdown_60m,
            "positive_60m": label_outcome_60m > 0,
        },
        "do_not_use_labels_for_live_gate": True,
        "must_recheck_live_route_and_risk": True,
        "order_send": False,
        "broker_call": False,
        "claude_call": False,
        "learning_excluded": True,
    }


def _rejection_reason(
    row: sqlite3.Row,
    context: dict[str, Any],
    *,
    path_names: set[str],
    route_sources: set[str],
    evidence_states: set[str],
    evidence_action_ceilings: set[str],
    required_entry_buckets: set[str],
    excluded_entry_buckets: set[str],
) -> str:
    path_name = str(row["path_name"] or "").strip()
    route_source = _row_route_source(row, context)
    evidence_state = _row_evidence_state(row, context)
    evidence_ceiling = _row_evidence_ceiling(row, context)
    bucket = str(context.get("entry_window_bucket") or "").strip().upper()
    if path_name not in path_names:
        return "path_name_not_allowed"
    if route_source not in route_sources:
        return "route_source_not_allowed"
    if evidence_state not in evidence_states:
        return "evidence_state_not_allowed"
    if evidence_action_ceilings and evidence_ceiling not in evidence_action_ceilings:
        return "evidence_action_ceiling_not_allowed"
    if required_entry_buckets and bucket not in required_entry_buckets:
        return "entry_window_bucket_not_required"
    if bucket in excluded_entry_buckets:
        return "entry_window_bucket_excluded"
    if not evidence_ceiling:
        return "missing_evidence_action_ceiling"
    return ""


def _apply_caps(
    candidates: list[dict[str, Any]],
    *,
    max_per_day: int,
    max_per_ticker_day: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    queued: list[dict[str, Any]] = []
    capped: list[dict[str, Any]] = []
    day_counts: Counter[str] = Counter()
    ticker_day_counts: Counter[tuple[str, str]] = Counter()
    for item in sorted(
        candidates,
        key=lambda row: (
            str(row.get("session_date") or ""),
            _as_int(row.get("live_visible_score")),
            str(row.get("trigger_time") or ""),
            str(row.get("ticker") or ""),
        ),
        reverse=True,
    ):
        day = str(item.get("session_date") or "")
        ticker_key = (day, str(item.get("ticker") or ""))
        if max_per_ticker_day > 0 and ticker_day_counts[ticker_key] >= max_per_ticker_day:
            capped_item = dict(item)
            capped_item["queue_status"] = "rejected"
            capped_item["queue_reason"] = "ticker_daily_quota_exceeded"
            capped.append(capped_item)
            continue
        if max_per_day > 0 and day_counts[day] >= max_per_day:
            capped_item = dict(item)
            capped_item["queue_status"] = "rejected"
            capped_item["queue_reason"] = "daily_quota_exceeded"
            capped.append(capped_item)
            continue
        day_counts[day] += 1
        ticker_day_counts[ticker_key] += 1
        queued.append(item)
    return queued, capped


def _summary_for(items: list[dict[str, Any]]) -> dict[str, Any]:
    labels = [(_as_float((row.get("historical_label") or {}).get("outcome_60m_pct"))) for row in items]
    drawdowns = [(_as_float((row.get("historical_label") or {}).get("max_drawdown_60m_pct"))) for row in items]
    return {
        "count": len(items),
        "outcome_60m": _stats(labels),
        "max_drawdown_60m": _stats(drawdowns),
        "by_path": dict(sorted(Counter(str(row.get("path_name") or "") for row in items).items())),
        "by_route_source": dict(sorted(Counter(str(row.get("route_source") or "") for row in items).items())),
        "by_evidence_state": dict(sorted(Counter(str(row.get("evidence_data_state") or "") for row in items).items())),
        "by_evidence_action_ceiling": dict(sorted(Counter(str(row.get("evidence_action_ceiling") or "") for row in items).items())),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "queue_status",
        "queue_reason",
        "session_date",
        "ticker",
        "path_name",
        "known_at",
        "trigger_time",
        "entry_price",
        "route_source",
        "freshness_verdict",
        "evidence_data_state",
        "evidence_action_ceiling",
        "entry_window_bucket",
        "live_visible_score",
        "outcome_60m_pct",
        "max_runup_60m_pct",
        "max_drawdown_60m_pct",
        "candidate_key",
    ]
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            label = row.get("historical_label") or {}
            flat = dict(row)
            flat["outcome_60m_pct"] = label.get("outcome_60m_pct")
            flat["max_runup_60m_pct"] = label.get("max_runup_60m_pct")
            flat["max_drawdown_60m_pct"] = label.get("max_drawdown_60m_pct")
            writer.writerow({key: flat.get(key, "") for key in fieldnames})


def _md_table(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    if not rows:
        return ["_no rows_"]
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        label = row.get("historical_label") or {}
        merged = {**row, **label}
        lines.append("| " + " | ".join(str(merged.get(column, "")) for column in columns) + " |")
    return lines


def _write_md(payload: dict[str, Any], path: Path) -> None:
    summary = payload["summary"]
    lines: list[str] = [
        "# KR Wait Re-evaluation Queue",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- live_writes_performed: {payload['live_writes_performed']}",
        f"- labels_used_for_queue_selection: {payload['leakage_contract']['labels_used_for_queue_selection']}",
        f"- queued_count: {summary['queued_count']}",
        f"- eligible_before_caps: {summary['eligible_before_caps']}",
        f"- rejected_count: {summary['rejected_count']}",
        "",
        "## Policy",
        "",
        f"- path_names: {_format_policy_set(set(payload['policy']['path_names']))}",
        f"- route_sources: {', '.join(payload['policy']['route_sources'])}",
        f"- evidence_states: {', '.join(payload['policy']['evidence_states'])}",
        f"- evidence_action_ceilings: {_format_policy_set(set(payload['policy']['evidence_action_ceilings']))}",
        f"- required_entry_buckets: {_format_policy_set(set(payload['policy']['required_entry_buckets']))}",
        f"- excluded_entry_buckets: {', '.join(payload['policy']['excluded_entry_buckets'])}",
        f"- max_per_day: {payload['policy']['max_per_day']}",
        f"- max_per_ticker_day: {payload['policy']['max_per_ticker_day']}",
        "",
        "## Queued Historical Metrics",
        "",
        f"- outcome_60m: {json.dumps(summary['queued']['outcome_60m'], ensure_ascii=False, sort_keys=True)}",
        f"- max_drawdown_60m: {json.dumps(summary['queued']['max_drawdown_60m'], ensure_ascii=False, sort_keys=True)}",
        "",
        "## Top Queued Candidates",
        "",
    ]
    lines.extend(
        _md_table(
            payload["queued_candidates"][:20],
            [
                "session_date",
                "ticker",
                "path_name",
                "route_source",
                "evidence_data_state",
                "evidence_action_ceiling",
                "live_visible_score",
                "outcome_60m_pct",
                "max_drawdown_60m_pct",
            ],
        )
    )
    lines.extend(
        [
            "",
            "## Rejection Counts",
            "",
            json.dumps(summary["rejection_counts"], ensure_ascii=False, sort_keys=True),
            "",
            "## Safety Notes",
            "",
            "- This report does not submit orders.",
            "- Historical labels are reported for evaluation only and are not used for queue eligibility or ranking.",
            "- Any live use must re-run RouteDecision, risk, affordability, broker truth, daily cap, and max position gates.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_kr_wait_re_evaluation_queue(
    *,
    db_path: str | Path = DEFAULT_DB,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    output_dir: str = "",
    start_date: str = "",
    end_date: str = "",
    limit: int = 5000,
    status: str = "CLOSE_OUTCOME_FILLED",
    path_names: set[str] | None = None,
    route_sources: set[str] | None = None,
    evidence_states: set[str] | None = None,
    evidence_action_ceilings: set[str] | None = None,
    required_entry_buckets: set[str] | None = None,
    excluded_entry_buckets: set[str] | None = None,
    max_per_day: int = 2,
    max_per_ticker_day: int = 1,
) -> dict[str, Any]:
    path_names = path_names or set(WAIT_PATHS)
    route_sources = route_sources or set(DEFAULT_ROUTE_SOURCES)
    evidence_states = {value.lower() for value in (evidence_states or DEFAULT_EVIDENCE_STATES)}
    evidence_action_ceilings = {
        value.upper() for value in (evidence_action_ceilings or DEFAULT_EVIDENCE_ACTION_CEILINGS)
    }
    required_entry_buckets = {value.upper() for value in (required_entry_buckets or DEFAULT_REQUIRED_ENTRY_BUCKETS)}
    excluded_entry_buckets = {value.upper() for value in (excluded_entry_buckets or DEFAULT_EXCLUDED_ENTRY_BUCKETS)}
    with _connect_ro(Path(db_path)) as conn:
        rows = _fetch_rows(conn, start_date=start_date, end_date=end_date, limit=limit, status=status)

    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        context = _context(row["metadata_json"])
        reason = _rejection_reason(
            row,
            context,
            path_names=path_names,
            route_sources=route_sources,
            evidence_states=evidence_states,
            evidence_action_ceilings=evidence_action_ceilings,
            required_entry_buckets=required_entry_buckets,
            excluded_entry_buckets=excluded_entry_buckets,
        )
        if reason:
            rejected.append(_candidate_payload(row, context, status="rejected", reason=reason))
            continue
        eligible.append(_candidate_payload(row, context, status="queued", reason="eligible_live_visible_policy"))

    queued, capped = _apply_caps(eligible, max_per_day=max_per_day, max_per_ticker_day=max_per_ticker_day)
    rejected.extend(capped)
    rejection_counts = dict(sorted(Counter(str(row.get("queue_reason") or "") for row in rejected).items()))
    out_dir = _output_dir(output_root, output_dir)
    json_path = out_dir / "kr_wait_re_evaluation_queue.json"
    md_path = out_dir / "kr_wait_re_evaluation_queue.md"
    csv_path = out_dir / "kr_wait_re_evaluation_queue.csv"
    rejected_csv_path = out_dir / "kr_wait_re_evaluation_rejected.csv"
    payload: dict[str, Any] = {
        "ok": True,
        "generated_at": _now_text(),
        "live_writes_performed": False,
        "inputs": {"db_path": str(Path(db_path))},
        "filters": {
            "start_date": start_date,
            "end_date": end_date,
            "limit": int(limit),
            "status": status,
        },
        "policy": {
            "path_names": sorted(path_names),
            "route_sources": sorted(route_sources),
            "evidence_states": sorted(evidence_states),
            "evidence_action_ceilings": sorted(evidence_action_ceilings),
            "required_entry_buckets": sorted(required_entry_buckets),
            "excluded_entry_buckets": sorted(excluded_entry_buckets),
            "max_per_day": int(max_per_day),
            "max_per_ticker_day": int(max_per_ticker_day),
        },
        "leakage_contract": {
            "labels_used_for_queue_selection": False,
            "labels_reported_for_historical_evaluation_only": True,
            "future_live_gate_inputs": [
                "path_name",
                "route_source",
                "evidence_data_state",
                "evidence_action_ceiling",
                "freshness_verdict",
                "entry_window_bucket",
            ],
        },
        "summary": {
            "source_count": len(rows),
            "eligible_before_caps": len(eligible),
            "queued_count": len(queued),
            "rejected_count": len(rejected),
            "rejection_counts": rejection_counts,
            "all_source": _summary_for([
                _candidate_payload(row, _context(row["metadata_json"]), status="source", reason="source")
                for row in rows
            ]),
            "eligible_before_caps_summary": _summary_for(eligible),
            "queued": _summary_for(queued),
            "rejected": _summary_for(rejected),
        },
        "queued_candidates": queued,
        "rejected_examples": rejected[:200],
        "apply_prohibitions": [
            "do not submit orders from this queue",
            "do not use outcome labels as live gate inputs",
            "do not bypass RouteDecision, risk, affordability, broker truth, daily cap, or max position gates",
        ],
        "output_paths": {
            "json": str(json_path),
            "md": str(md_path),
            "csv": str(csv_path),
            "rejected_csv": str(rejected_csv_path),
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _write_md(payload, md_path)
    _write_csv(csv_path, queued)
    _write_csv(rejected_csv_path, rejected)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a read-only KR wait re-evaluation queue report.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--status", default="CLOSE_OUTCOME_FILLED")
    parser.add_argument("--path-names", default=",".join(sorted(WAIT_PATHS)))
    parser.add_argument("--route-sources", default=",".join(sorted(DEFAULT_ROUTE_SOURCES)))
    parser.add_argument("--evidence-states", default=",".join(sorted(DEFAULT_EVIDENCE_STATES)))
    parser.add_argument("--evidence-action-ceilings", default="")
    parser.add_argument("--required-entry-buckets", default="")
    parser.add_argument("--excluded-entry-buckets", default=",".join(sorted(DEFAULT_EXCLUDED_ENTRY_BUCKETS)))
    parser.add_argument("--max-per-day", type=int, default=2)
    parser.add_argument("--max-per-ticker-day", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = build_kr_wait_re_evaluation_queue(
            db_path=args.db_path,
            output_root=args.output_root,
            output_dir=args.output_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            limit=args.limit,
            status=args.status,
            path_names=_split_set(args.path_names),
            route_sources=_split_set(args.route_sources),
            evidence_states=_split_set(args.evidence_states),
            evidence_action_ceilings=_split_set(args.evidence_action_ceilings),
            required_entry_buckets=_split_set(args.required_entry_buckets),
            excluded_entry_buckets=_split_set(args.excluded_entry_buckets),
            max_per_day=args.max_per_day,
            max_per_ticker_day=args.max_per_ticker_day,
        )
    except RehearsalGuardError as exc:
        error = {"ok": False, "error": str(exc)}
        if args.json:
            print(json.dumps(error, ensure_ascii=False, indent=2))
        else:
            print(f"ops_kr_wait_re_evaluation_queue failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            "ok "
            f"queued={payload['summary']['queued_count']} "
            f"eligible={payload['summary']['eligible_before_caps']} "
            f"md={payload['output_paths']['md']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
