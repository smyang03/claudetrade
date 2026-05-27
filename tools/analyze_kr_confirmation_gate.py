from __future__ import annotations

import argparse
import ast
import json
import math
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


KR_CONFIRMATION_ACTIONS = {"PROBE_READY", "BUY_READY", "PULLBACK_WAIT"}
KR_CONFIRMATION_REASONS = {
    "kr_confirmation_required",
    "kr_fast_trigger_not_confirmed",
    "kr_fast_window_elapsed_missing",
    "kr_data_quality_not_confirmed",
    "kr_overextended_not_confirmed",
    "kr_spread_not_confirmed",
    "kr_vi_active_not_confirmed",
    "kr_momentum_not_confirmed",
    "kr_vwap_not_confirmed",
    "kr_or_not_confirmed",
    "kr_volume_not_confirmed",
}


def _decode_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    if not isinstance(value, str):
        return {}
    for loader in (json.loads, ast.literal_eval):
        try:
            decoded = loader(value)
        except Exception:
            continue
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _finite_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _profit_factor(values: list[float]) -> float | str | None:
    gains = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    if losses:
        return round(sum(gains) / abs(sum(losses)), 6) if gains else 0.0
    if gains:
        return "INF"
    return None


def _metrics(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [_finite_float(row.get(field)) for row in rows]
    values = [value for value in values if value is not None]
    return {
        "n": len(values),
        "avg_pct": round(_avg(values), 6) if values else None,
        "median_pct": round(_median(values), 6) if values else None,
        "win_rate_pct": round((sum(1 for value in values if value > 0) / len(values)) * 100.0, 4)
        if values
        else None,
        "profit_factor": _profit_factor(values),
    }


def _markdown_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|")


def _confirmation_reason(row: dict[str, Any], payload: dict[str, Any]) -> str:
    runtime_gate = payload.get("runtime_gate") if isinstance(payload.get("runtime_gate"), dict) else {}
    return str(
        payload.get("confirmation_reason")
        or runtime_gate.get("kr_confirmation_reason")
        or row.get("route_runtime_gate_reason")
        or row.get("route_reason")
        or ""
    )


def _route_group(row: dict[str, Any], payload: dict[str, Any]) -> str:
    original = str(row.get("route_original_action") or "").upper()
    final = str(row.get("route_final_action") or "").upper()
    reason = str(row.get("route_reason") or "")
    gate_reason = str(row.get("route_runtime_gate_reason") or "")
    confirmation_reason = _confirmation_reason(row, payload)

    if original not in KR_CONFIRMATION_ACTIONS:
        return "other"
    if final in KR_CONFIRMATION_ACTIONS:
        return "kept_executable"
    if final == "WATCH" and (gate_reason in KR_CONFIRMATION_REASONS or reason in KR_CONFIRMATION_REASONS):
        return "demoted_by_confirmation"
    if final == "WATCH" and reason == "evidence_ceiling_watch":
        if confirmation_reason in KR_CONFIRMATION_REASONS:
            return "demoted_by_evidence_ceiling_with_confirmation_pending"
        return "demoted_by_evidence_ceiling_confirmed_or_unknown"
    if final == "WATCH" and reason == "pullback_wait_blocked_negative_context":
        return "demoted_by_negative_pullback_context"
    if final == "WATCH":
        return "demoted_other_watch"
    if final == "HARD_BLOCK":
        return "hard_block_after_ready_action"
    return "other"


def _from_high_proxy(row: dict[str, Any]) -> str:
    value = _finite_float(row.get("from_high_pct"))
    if value is None:
        return "unknown"
    # Audit sign convention has changed over time. This proxy only separates
    # rows close to the recorded high from rows clearly below that high.
    return "near_or_at_high_proxy" if value >= -1.5 else "below_high_proxy"


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "ret30": _metrics(rows, "ret30"),
        "ret60": _metrics(rows, "ret60"),
        "mfe60": _metrics(rows, "mfe60"),
        "mae60": _metrics(rows, "mae60"),
        "route_reasons": dict(
            sorted(
                Counter(str(row.get("route_runtime_gate_reason") or row.get("route_reason") or "") for row in rows).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "confirmation_reasons": dict(
            sorted(
                Counter(str(row.get("confirmation_reason") or "") for row in rows).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "from_high_proxy": dict(
            sorted(
                Counter(str(row.get("from_high_proxy") or "") for row in rows).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
    }


def _decision_note(groups: dict[str, dict[str, Any]]) -> str:
    kept = groups.get("kept_executable", {})
    demoted = groups.get("demoted_by_confirmation", {})
    kept_n = int(((kept.get("ret60") or {}).get("n") or 0))
    demoted_n = int(((demoted.get("ret60") or {}).get("n") or 0))
    if kept_n < 30 or demoted_n < 30:
        return (
            "hold_parameter_change: 60m labels are too sparse to justify WATCH_TRIGGER/KR confirmation "
            "demotion changes."
        )
    kept_avg = (kept.get("ret60") or {}).get("avg_pct")
    demoted_avg = (demoted.get("ret60") or {}).get("avg_pct")
    if demoted_avg is not None and kept_avg is not None and demoted_avg > kept_avg:
        return "hold_or_relax_review: demoted confirmation rows outperformed kept rows."
    return "no_relaxation_signal: confirmation-demoted rows did not outperform kept executable rows."


def analyze_kr_confirmation_gate(
    *,
    db_path: str | Path | None = None,
    start_date: str = "",
    end_date: str = "",
    market: str = "KR",
    runtime_mode: str = "live",
) -> dict[str, Any]:
    path = Path(db_path or get_runtime_path("data", "audit", "candidate_audit.db"))
    market_key = str(market or "KR").upper()
    where = ["r.market = ?", "r.runtime_mode = ?"]
    params: list[Any] = [market_key, runtime_mode]
    if start_date:
        where.append("r.session_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("r.session_date <= ?")
        params.append(end_date)
    action_placeholders = ",".join("?" for _ in KR_CONFIRMATION_ACTIONS)
    where.append(f"upper(coalesce(r.route_original_action, '')) IN ({action_placeholders})")
    params.extend(sorted(KR_CONFIRMATION_ACTIONS))

    sql = f"""
        SELECT
          r.candidate_key,
          r.session_date,
          r.known_at,
          r.ticker,
          r.route_original_action,
          r.route_final_action,
          r.route_reason,
          r.route_runtime_gate_reason,
          r.route_demoted_to,
          r.from_high_pct,
          r.payload_json,
          o30.return_pct AS ret30,
          o60.return_pct AS ret60,
          o60.max_runup_pct AS mfe60,
          o60.max_drawdown_pct AS mae60
        FROM audit_candidate_rows r
        LEFT JOIN audit_candidate_outcomes o30
          ON o30.candidate_key = r.candidate_key
         AND o30.horizon_min = 30
        LEFT JOIN audit_candidate_outcomes o60
          ON o60.candidate_key = r.candidate_key
         AND o60.horizon_min = 60
        WHERE {" AND ".join(where)}
        ORDER BY r.session_date, r.known_at, r.ticker
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        fetched = [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()

    rows: list[dict[str, Any]] = []
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_group_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_group_high: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_date: dict[str, Counter[str]] = defaultdict(Counter)
    for row in fetched:
        payload = _decode_payload(row.get("payload_json"))
        group = _route_group(row, payload)
        confirmation_reason = _confirmation_reason(row, payload)
        enriched = {
            **row,
            "route_group": group,
            "confirmation_reason": confirmation_reason,
            "from_high_proxy": _from_high_proxy(row),
        }
        rows.append(enriched)
        by_group[group].append(enriched)
        by_group_reason[f"{group}|{row.get('route_runtime_gate_reason') or row.get('route_reason') or ''}"].append(enriched)
        by_group_high[f"{group}|{enriched['from_high_proxy']}"].append(enriched)
        by_date[str(row.get("session_date") or "")][group] += 1

    groups = {key: _summarize(items) for key, items in sorted(by_group.items())}
    reason_groups = {key: _summarize(items) for key, items in sorted(by_group_reason.items())}
    high_groups = {key: _summarize(items) for key, items in sorted(by_group_high.items())}
    warnings: list[str] = []
    for key in ("kept_executable", "demoted_by_confirmation"):
        label_n = int(((groups.get(key, {}).get("ret60") or {}).get("n") or 0))
        if label_n < 30:
            warnings.append(f"{key}_ret60_label_n_below_30")

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "filters": {
            "db_path": str(path),
            "market": market_key,
            "runtime_mode": runtime_mode,
            "start_date": start_date,
            "end_date": end_date,
        },
        "row_count": len(rows),
        "decision_note": _decision_note(groups),
        "warnings": warnings,
        "groups": groups,
        "reason_groups": reason_groups,
        "from_high_groups": high_groups,
        "by_date": {key: dict(value) for key, value in sorted(by_date.items())},
    }


def to_markdown(payload: dict[str, Any]) -> str:
    filters = payload.get("filters") or {}
    lines = [
        "# KR Confirmation Gate Outcome Review",
        "",
        f"Generated: {payload.get('generated_at', '')}",
        f"Market/runtime: {filters.get('market', '')}/{filters.get('runtime_mode', '')}",
        f"Window: {filters.get('start_date', '') or '(all)'} ~ {filters.get('end_date', '') or '(all)'}",
        f"Rows: {payload.get('row_count', 0)}",
        f"Decision note: `{payload.get('decision_note', '')}`",
        "",
        "## Interpretation",
        "",
        "- This is a read-only audit DB review. It does not change live config, PathB gates, order sizing, or state files.",
        "- `demoted_by_confirmation` means the route itself demoted a KR executable candidate to WATCH for a KR confirmation reason.",
        "- `demoted_by_evidence_ceiling_with_confirmation_pending` means evidence ceiling blocked first, while confirmation was also pending.",
        "- `from_high_proxy` uses `from_high_pct >= -1.5` because historical audit sign conventions are mixed; it is not an exact `at_high` bucket.",
        "",
        "## Warnings",
        "",
    ]
    warnings = payload.get("warnings") or []
    if warnings:
        for warning in warnings:
            lines.append(f"- `{_markdown_cell(warning)}`")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Group Summary",
            "",
            "| Group | Rows | 30m N | 30m Avg | 60m N | 60m Avg | 60m Median | 60m Win% | MFE60 Avg | MAE60 Avg |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for group, item in (payload.get("groups") or {}).items():
        ret30 = item.get("ret30") or {}
        ret60 = item.get("ret60") or {}
        mfe60 = item.get("mfe60") or {}
        mae60 = item.get("mae60") or {}
        lines.append(
            f"| {_markdown_cell(group)} | {item.get('rows', 0)} | "
            f"{ret30.get('n', 0)} | {ret30.get('avg_pct', '')} | "
            f"{ret60.get('n', 0)} | {ret60.get('avg_pct', '')} | {ret60.get('median_pct', '')} | "
            f"{ret60.get('win_rate_pct', '')} | {mfe60.get('avg_pct', '')} | {mae60.get('avg_pct', '')} |"
        )

    lines.extend(
        [
            "",
            "## Reason Summary",
            "",
            "| Group / Reason | Rows | 60m N | 60m Avg | MFE60 Avg | MAE60 Avg |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for key, item in (payload.get("reason_groups") or {}).items():
        ret60 = item.get("ret60") or {}
        mfe60 = item.get("mfe60") or {}
        mae60 = item.get("mae60") or {}
        lines.append(
            f"| {_markdown_cell(key)} | {item.get('rows', 0)} | {ret60.get('n', 0)} | "
            f"{ret60.get('avg_pct', '')} | {mfe60.get('avg_pct', '')} | {mae60.get('avg_pct', '')} |"
        )

    lines.extend(
        [
            "",
            "## From High Proxy",
            "",
            "| Group / Proxy | Rows | 60m N | 60m Avg | MFE60 Avg | MAE60 Avg |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for key, item in (payload.get("from_high_groups") or {}).items():
        ret60 = item.get("ret60") or {}
        mfe60 = item.get("mfe60") or {}
        mae60 = item.get("mae60") or {}
        lines.append(
            f"| {_markdown_cell(key)} | {item.get('rows', 0)} | {ret60.get('n', 0)} | "
            f"{ret60.get('avg_pct', '')} | {mfe60.get('avg_pct', '')} | {mae60.get('avg_pct', '')} |"
        )

    lines.extend(
        [
            "",
            "## By Date",
            "",
            "| Date | Group counts |",
            "|---|---|",
        ]
    )
    for session_date, counts in (payload.get("by_date") or {}).items():
        text = ", ".join(f"{key}={value}" for key, value in sorted((counts or {}).items()))
        lines.append(f"| {_markdown_cell(session_date)} | {_markdown_cell(text)} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze KR confirmation gate demotion outcomes.")
    parser.add_argument("--db-path", default=str(get_runtime_path("data", "audit", "candidate_audit.db")))
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--market", default="KR")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--stamp", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--output-dir", default=str(ROOT / "docs" / "reports"))
    args = parser.parse_args(argv)

    payload = analyze_kr_confirmation_gate(
        db_path=args.db_path,
        start_date=args.start_date,
        end_date=args.end_date,
        market=args.market,
        runtime_mode=args.runtime_mode,
    )
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"kr_confirmation_gate_review_{args.stamp}.json"
    md_path = out / f"kr_confirmation_gate_review_{args.stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(to_markdown(payload), encoding="utf-8")
    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
