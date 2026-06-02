from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[3]


def now_kst() -> datetime:
    return datetime.now(KST)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_dt(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        try:
            parsed = datetime.fromisoformat(raw[:19])
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def run_capture(args: list[str], *, timeout_sec: int = 600) -> dict[str, Any]:
    started = now_kst().isoformat(timespec="seconds")
    try:
        proc = subprocess.run(
            [sys.executable, *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
    except Exception as exc:
        return {
            "ok": False,
            "started_at": started,
            "ended_at": now_kst().isoformat(timespec="seconds"),
            "args": [sys.executable, *args],
            "error": str(exc),
        }
    payload: dict[str, Any] = {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "started_at": started,
        "ended_at": now_kst().isoformat(timespec="seconds"),
        "args": [sys.executable, *args],
        "stderr_tail": proc.stderr[-4000:],
    }
    try:
        payload["json"] = json.loads(proc.stdout)
    except Exception:
        payload["stdout_tail"] = proc.stdout[-8000:]
    return payload


def run_to_file(args: list[str], output: Path, *, timeout_sec: int = 600) -> dict[str, Any]:
    result = run_capture([*args, "--output", str(output)], timeout_sec=timeout_sec)
    result["output"] = str(output)
    return result


def wait_for_monitor(final_json: Path, *, wait_until: datetime, timeout_sec: int) -> dict[str, Any]:
    deadline = max(wait_until, now_kst()) + timedelta(seconds=max(timeout_sec, 0))
    while now_kst() <= deadline:
        if final_json.exists():
            payload = read_json(final_json, {})
            if isinstance(payload, dict) and payload.get("status") == "completed":
                return {"ready": True, "path": str(final_json), "observed_at": now_kst().isoformat(timespec="seconds")}
        time.sleep(30)
    return {"ready": False, "path": str(final_json), "observed_at": now_kst().isoformat(timespec="seconds")}


def summarize_preflight(payload: dict[str, Any]) -> dict[str, Any]:
    checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    warns = [row for row in checks if row.get("status") == "WARN"]
    fails = [row for row in checks if row.get("status") == "FAIL"]
    def compact(row: dict[str, Any]) -> dict[str, Any]:
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        return {
            "name": row.get("name"),
            "detail": row.get("detail"),
            "operator_action_required": data.get("operator_action_required"),
            "blocked_if_live_start": data.get("blocked_if_live_start"),
        }
    return {
        "ok": payload.get("ok"),
        "generated_at": payload.get("generated_at"),
        "fail_count": payload.get("fail_count"),
        "warn_count": payload.get("warn_count"),
        "action_required_warn_count": payload.get("action_required_warn_count"),
        "blocked_if_live_start_warn_count": payload.get("blocked_if_live_start_warn_count"),
        "warns": [compact(row) for row in warns],
        "fails": [compact(row) for row in fails],
    }


def pct(value: Any) -> str:
    try:
        return f"{float(value):.2f}%"
    except Exception:
        return "NA"


def count_by_type(events: list[dict[str, Any]], values: set[str]) -> int:
    return sum(1 for row in events if str(row.get("type") or row.get("action") or "").upper() in values)


def top_items(rows: list[dict[str, Any]], key: str, *, limit: int = 5) -> list[dict[str, Any]]:
    def value(row: dict[str, Any]) -> float:
        try:
            return float(row.get(key) or -9999.0)
        except Exception:
            return -9999.0
    return sorted(rows, key=value, reverse=True)[:limit]


def table(lines: list[str], rows: list[dict[str, Any]], columns: list[tuple[str, str]], *, empty: str) -> None:
    if not rows:
        lines.append(f"- {empty}")
        lines.append("")
        return
    lines.append("| " + " | ".join(title for title, _key in columns) + " |")
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in rows:
        values: list[str] = []
        for _title, key in columns:
            value = row.get(key)
            if key in {"pnl_pct", "peak_pnl_pct", "position_mfe_pct", "position_mae_pct"}:
                value = pct(value)
            values.append(str(value if value not in (None, "") else ""))
        lines.append("| " + " | ".join(value.replace("|", "\\|") for value in values) + " |")
    lines.append("")


def append_map(lines: list[str], title: str, values: dict[str, Any], *, limit: int = 12) -> None:
    lines.append(f"## {title}")
    lines.append("")
    if not values:
        lines.append("- none")
        lines.append("")
        return
    for key, value in list(values.items())[:limit]:
        lines.append(f"- {key}: {value}")
    lines.append("")


def synthesize(
    *,
    out_dir: Path,
    monitor: dict[str, Any],
    candidate60: dict[str, Any],
    candidate30: dict[str, Any],
    ops: dict[str, Any],
    quality: dict[str, Any],
    misjudgments: dict[str, Any],
    adaptive: dict[str, Any],
    preflight: dict[str, Any],
    command_results: dict[str, Any],
) -> str:
    monitor_source = str(monitor.get("_source") or "final_report")
    monitor_ready = bool(monitor.get("_monitor_final_ready", True))
    latest = monitor.get("latest_snapshot") if isinstance(monitor.get("latest_snapshot"), dict) else {}
    broker = latest.get("broker_truth") if isinstance(latest.get("broker_truth"), dict) else {}
    broker_positions = broker.get("positions") if isinstance(broker.get("positions"), list) else []
    broker_fills = broker.get("today_fills") if isinstance(broker.get("today_fills"), list) else []
    guardian = latest.get("guardian") if isinstance(latest.get("guardian"), dict) else {}
    risk = monitor.get("risk_axes") if isinstance(monitor.get("risk_axes"), dict) else {}
    usage = monitor.get("claude_usage_since_start") if isinstance(monitor.get("claude_usage_since_start"), dict) else {}
    usage_labels = usage.get("by_label") if isinstance(usage.get("by_label"), dict) else {}
    issues = monitor.get("log_issue_counts_since_start") if isinstance(monitor.get("log_issue_counts_since_start"), dict) else {}
    decisions = monitor.get("decision_events_since_start") if isinstance(monitor.get("decision_events_since_start"), list) else []
    entries = count_by_type(decisions, {"ENTRY", "BUY"})
    exits = count_by_type(decisions, {"CLOSED", "SELL"})
    hold_reviews = count_by_type(decisions, {"HOLD_REVIEW"})

    route = candidate60.get("routing_delta") if isinstance(candidate60.get("routing_delta"), dict) else {}
    consistency = candidate60.get("consistency") if isinstance(candidate60.get("consistency"), dict) else {}
    watch = candidate60.get("watch_only_bucket_decomposition") if isinstance(candidate60.get("watch_only_bucket_decomposition"), dict) else {}
    watch_shadow = candidate60.get("watch_trigger_shadow_summary") if isinstance(candidate60.get("watch_trigger_shadow_summary"), dict) else {}
    coverage = candidate60.get("outcome_coverage") if isinstance(candidate60.get("outcome_coverage"), dict) else {}
    latency = candidate60.get("latency_sla") if isinstance(candidate60.get("latency_sla"), dict) else {}
    missed = candidate60.get("missed_winners") if isinstance(candidate60.get("missed_winners"), list) else []
    mfe_candidates = top_items(missed, "max_runup_pct", limit=8)
    pathb_remediation = latest.get("pathb_remediation") if isinstance(latest.get("pathb_remediation"), dict) else {}
    ops_learning = ops.get("v2_learning_gate") if isinstance(ops.get("v2_learning_gate"), dict) else {}
    ops_hold = ops.get("hold_advisor_latency") if isinstance(ops.get("hold_advisor_latency"), dict) else {}
    quality_lifecycle = quality.get("lifecycle_reconciliation") if isinstance(quality.get("lifecycle_reconciliation"), dict) else {}
    mis_summary = misjudgments.get("summary") if isinstance(misjudgments.get("summary"), dict) else {}
    adaptive_suggestions = adaptive.get("by_suggestion") if isinstance(adaptive.get("by_suggestion"), dict) else {}
    candidate_rows = int(candidate60.get("candidate_rows") or 0)
    observed_claude_calls = int(usage.get("calls_since_start_observed_from_raw_files") or 0)
    runtime_filtered_reasons = route.get("runtime_filtered_reason_counts") if isinstance(route.get("runtime_filtered_reason_counts"), dict) else {}
    watch_block_reasons = watch_shadow.get("blocked_reason_counts") if isinstance(watch_shadow.get("blocked_reason_counts"), dict) else {}

    buy_causality: list[str] = []
    if entries == 0:
        if candidate_rows <= 0:
            buy_causality.append("No US candidate rows were present in candidate audit for the session, so missed buys cannot be attributed to selection quality yet.")
        if observed_claude_calls <= 0:
            buy_causality.append("No US Claude calls were observed by the monitor after start, so no Claude-side buy decision was visible in this window.")
        if broker.get("stale") or broker.get("missing") or broker.get("error"):
            buy_causality.append("US broker truth was stale, missing, or errored; live entry gates should be treated as fail-closed until fresh broker truth returns.")
        if guardian.get("gate") == "BLOCK_START":
            buy_causality.append("Guardian gate was BLOCK_START; this can block startup/entry independent of candidate quality.")
        if pathb_remediation.get("stale_active_count"):
            buy_causality.append("Previous-session PathB active rows remain, so entry capacity and reconciliation state need broker-truth review before policy changes.")
    else:
        buy_causality.append(f"{entries} buy/entry events were observed; evaluate ticker-level fills and route reasons rather than treating the session as no-buy.")
    if runtime_filtered_reasons:
        buy_causality.append(f"Runtime filters removed candidates after raw trade_ready: {runtime_filtered_reasons}.")
    if watch_block_reasons:
        buy_causality.append(f"Watch-trigger shadow blocks were observed: {watch_block_reasons}.")
    if not buy_causality:
        buy_causality.append("No dominant buy-side block was visible from the collected artifacts.")

    sell_causality: list[str] = []
    if exits == 0:
        sell_causality.append("No sell/closed decision events were observed during the monitor window.")
        if broker_positions:
            sell_causality.append(f"Latest broker snapshot still had {len(broker_positions)} US positions; absence of sells should be judged against hold advisor and stop/target triggers.")
        if not broker_fills:
            sell_causality.append("Latest broker snapshot had no same-day fills, so broker evidence does not show a missed submitted sell in the snapshot.")
    else:
        sell_causality.append(f"{exits} sell/closed events were observed; inspect exit reasons and broker fill confirmation per ticker.")
    if latest.get("pending_sells"):
        sell_causality.append(f"{len(latest.get('pending_sells') or [])} local pending-sell rows require broker reconciliation.")
    if latest.get("protected_positions"):
        sell_causality.append(f"{len(latest.get('protected_positions') or [])} protected positions were present; protective hold/reconcile status can suppress automatic cleanup.")
    if broker.get("stale") or broker.get("missing") or broker.get("error"):
        sell_causality.append("Stale or untrusted broker truth also weakens sell forensic certainty; use broker positions/open orders/fills as the final truth.")
    if guardian.get("gate") == "BLOCK_START":
        sell_causality.append("Guardian BLOCK_START was present; separate runtime safety blocks from sell-advisor quality.")

    lines: list[str] = [
        "# US Session Profitability Review",
        "",
        f"- generated_at: {now_kst().isoformat(timespec='seconds')}",
        f"- session_date: {monitor.get('session_date') or candidate60.get('session_date')}",
        f"- monitor_window: {monitor.get('start_at')} ~ {monitor.get('end_at')}",
        f"- source_dir: {out_dir}",
        "- requested_regular_window: 2026-06-01T22:30:00+09:00 ~ 2026-06-02T07:00:00+09:00",
        f"- monitor_source: {monitor_source}",
        f"- monitor_final_ready: {monitor_ready}",
        f"- read_only: True",
        "",
        "## Executive Summary",
        "",
        f"- decisions observed: entries={entries}, exits={exits}, hold_reviews={hold_reviews}",
        f"- broker truth: missing={broker.get('missing')} stale={broker.get('stale')} error={broker.get('error')} positions={broker.get('positions_count')} open_orders={broker.get('open_orders_count')} fills={broker.get('today_fills_count')}",
        f"- guardian: gate={guardian.get('gate')} ok={guardian.get('ok')} heartbeat_status={(guardian.get('heartbeat') or {}).get('status')}",
        f"- unresolved state: protected={risk.get('protected_positions')} pending_sells={risk.get('pending_sells')} order_unknown_events={risk.get('order_unknown_events')} manual_action_required={risk.get('manual_action_required')}",
        f"- Claude calls observed by monitor: {usage.get('calls_since_start_observed_from_raw_files')} labels={usage_labels}",
        "",
        "## Broker Performance Snapshot",
        "",
    ]
    table(
        lines,
        broker_positions,
        [
            ("ticker", "ticker"),
            ("qty", "qty"),
            ("pnl", "pnl_pct"),
            ("mfe", "position_mfe_pct"),
            ("mae", "position_mae_pct"),
            ("strategy", "strategy"),
            ("path", "path_type"),
        ],
        empty="no broker positions in latest snapshot",
    )
    table(
        lines,
        broker_fills[-20:],
        [
            ("time", "fill_time"),
            ("ticker", "ticker"),
            ("side", "side"),
            ("qty", "filled_qty"),
            ("remaining", "remaining_qty"),
            ("status", "order_status"),
            ("order", "order_no"),
        ],
        empty="no broker fills in latest snapshot",
    )
    table(
        lines,
        decisions[-30:],
        [
            ("time", "timestamp"),
            ("type", "type"),
            ("ticker", "ticker"),
            ("qty", "qty"),
            ("order", "order_no"),
            ("exit", "exit_reason"),
            ("pnl", "pnl_pct"),
        ],
        empty="no decision events observed during monitor window",
    )
    lines.extend(["## Buy Non-Execution Causality", ""])
    for item in buy_causality:
        lines.append(f"- {item}")
    lines.append("")
    lines.extend(["## Sell Non-Execution Causality", ""])
    for item in sell_causality:
        lines.append(f"- {item}")
    lines.append("")
    lines.extend(
        [
            "## Buy Path Review",
            "",
            f"- candidate rows: {candidate_rows} latest_checked={consistency.get('latest_rows_checked')}",
            f"- prompt/watchlist pool: full={route.get('full_pool_count')} prompt={route.get('prompt_pool_count')} watchlist={route.get('watchlist_count')}",
            f"- raw trade_ready={route.get('raw_trade_ready_count')} normalized={route.get('normalized_trade_ready_count')} applied={route.get('applied_trade_ready_count')} execution_pool={route.get('execution_pool_count')}",
            f"- dropped_after_raw={route.get('dropped_after_raw') or []}",
            f"- runtime_filtered_count={route.get('runtime_filtered_count')} reasons={route.get('runtime_filtered_reason_counts') or {}}",
            f"- PathB wait tickers={route.get('pathb_wait_tickers') or []}",
            f"- missed winners found={len(mfe_candidates)} at 60m horizon",
            "",
        ]
    )
    if mfe_candidates:
        lines.append("| ticker | miss_stage | mfe60 | drawdown60 | route_reason |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in mfe_candidates:
            lines.append(
                f"| {row.get('ticker')} | {row.get('miss_stage')} | {pct(row.get('max_runup_pct'))} | "
                f"{pct(row.get('max_drawdown_pct'))} | {row.get('route_reason') or ''} |"
            )
        lines.append("")
    else:
        lines.append("- no high-confidence missed-winner rows were mature enough in the 60m outcome table.")
        lines.append("")

    append_map(lines, "Watch And Block Reasons", watch_shadow.get("blocked_reason_counts") or {})
    append_map(lines, "Watch Bucket Decomposition", {row.get("bucket"): row.get("rows") for row in watch.get("buckets") or []})

    lines.extend(
        [
            "## Sell Path Review",
            "",
            f"- exits observed during monitor window: {exits}",
            f"- pending sell local rows: {len(latest.get('pending_sells') or [])}",
            f"- protected positions: {len(latest.get('protected_positions') or [])}",
            f"- hold advisor latency/status: {ops_hold}",
            f"- lifecycle unique fills without close: {quality_lifecycle.get('unique_fill_without_close')}",
            f"- lifecycle closed events: {quality_lifecycle.get('closed_event_count')}",
            "",
            "## Quality And Contamination",
            "",
            f"- candidate consistency: prompt_mismatch={consistency.get('actual_prompt_mismatch_count')} trace_missing={consistency.get('trace_join_missing_count')} trade_ready_family_mismatch={consistency.get('trade_ready_family_mismatch_count')}",
            f"- invalid price observations={consistency.get('invalid_price_count')} reasons={consistency.get('invalid_price_reason_counts') or {}}",
            f"- outcome coverage 30m={coverage.get('30')} 60m={coverage.get('60')}",
            f"- latency SLA: status={latency.get('status')} avg_ms={latency.get('avg_ms')} p95_ms={latency.get('p95_ms')} max_ms={latency.get('max_ms')}",
            f"- v2 learning gate: rows_by_grade={ops_learning.get('rows_by_quality_grade')} excluded={ops_learning.get('learning_excluded')} reasons={ops_learning.get('top_quality_reasons')}",
            f"- preflight: ok={preflight.get('ok')} fails={preflight.get('fail_count')} warns={preflight.get('warn_count')} action_required_warns={preflight.get('action_required_warn_count')}",
            f"- PathB remediation: current_unknown={pathb_remediation.get('current_order_unknown_count')} stale_active={pathb_remediation.get('stale_active_count')} apply_eligible={pathb_remediation.get('apply_eligible_items')}",
            "",
        ]
    )
    append_map(lines, "Issue Counts", issues)
    append_map(lines, "Adaptive Live Suggestions", adaptive_suggestions)
    append_map(lines, "Misjudgment Label Distribution", mis_summary.get("by_market_label") or {})

    recommendations: list[str] = []
    if broker.get("stale") or broker.get("missing") or broker.get("error"):
        recommendations.append("Restore fresh US broker truth before judging missed buys or sells; stale truth can make entry fail-closed and contaminates exposure/capacity analysis.")
    if guardian.get("gate") == "BLOCK_START":
        recommendations.append("Clear the guardian BLOCK_START causes after verifying they are current-session relevant; stale guardian state can explain why otherwise valid candidates did not enter.")
    if pathb_remediation.get("stale_active_count"):
        recommendations.append("Review previous-session PathB stale active rows against broker holdings; do not auto-close them without fresh broker evidence.")
    if consistency.get("invalid_price_count"):
        recommendations.append("Fix invalid or unmeasured quote evidence before expanding candidate gates; price-quality contamination can create false blocked or false ready rows.")
    if route.get("runtime_filtered_reason_counts"):
        recommendations.append("Use runtime_filtered reason counts for narrow improvements; avoid broad selection-policy changes from raw trade_ready counts alone.")
    if coverage.get("60", {}).get("maturity") != "ready":
        recommendations.append("Treat same-session 60m performance as reference-only until outcome coverage matures; do not promote a policy from sparse rows.")
    if not recommendations:
        recommendations.append("No major contamination signal dominated the report; next step is ticker-level review of missed winners and realized exits.")

    lines.extend(["## Profitability Improvement Actions", ""])
    for item in recommendations:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- monitor_final_json: {out_dir / 'final_report.json'}",
            f"- monitor_final_md: {out_dir / 'final_report.md'}",
            f"- candidate_60m_json: {out_dir / 'candidate_audit_60m.json'}",
            f"- candidate_30m_json: {out_dir / 'candidate_audit_30m.json'}",
            f"- monitoring_ops_json: {out_dir / 'monitoring_ops_report.json'}",
            f"- v2_quality_json: {out_dir / 'v2_quality_audit.json'}",
            f"- preflight_summary_json: {out_dir / 'live_preflight_summary.json'}",
            f"- command_results_json: {out_dir / 'post_session_command_results.json'}",
            "",
            "## Command Results",
            "",
        ]
    )
    for name, result in command_results.items():
        lines.append(f"- {name}: ok={result.get('ok')} returncode={result.get('returncode')} output={result.get('output', '')}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build post-session US profitability synthesis.")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--session-date", required=True)
    parser.add_argument("--wait-until", required=True)
    parser.add_argument("--timeout-sec", type=int, default=1800)
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    wait_until = parse_dt(args.wait_until) or now_kst()
    final_json = out_dir / "final_report.json"
    wait_state = wait_for_monitor(final_json, wait_until=wait_until, timeout_sec=args.timeout_sec)
    write_json(out_dir / "post_session_wait_state.json", wait_state)

    command_results: dict[str, Any] = {}
    progress_json = out_dir / "progress.json"
    monitor = read_json(final_json, {})
    monitor_source = "final_report.json"
    if not isinstance(monitor, dict) or not monitor:
        monitor = read_json(progress_json, {})
        monitor_source = "progress.json_fallback"
    if isinstance(monitor, dict):
        monitor["_source"] = monitor_source
        monitor["_monitor_final_ready"] = bool(wait_state.get("ready"))
        monitor["_monitor_final_path"] = str(final_json)
        monitor["_monitor_progress_path"] = str(progress_json)

    preflight_raw = run_capture(["tools/live_preflight.py", "--mode", "live", "--skip-dashboard", "--json"], timeout_sec=180)
    preflight = summarize_preflight(preflight_raw.get("json") if isinstance(preflight_raw.get("json"), dict) else {})
    write_json(out_dir / "live_preflight_summary.json", preflight)
    command_results["live_preflight_summary"] = preflight_raw

    candidate60_raw = run_capture(
        [
            "tools/analyze_candidate_audit.py",
            "--date",
            args.session_date,
            "--market",
            "US",
            "--runtime-mode",
            "live",
            "--horizon-min",
            "60",
            "--limit",
            "20",
        ],
        timeout_sec=300,
    )
    candidate60 = candidate60_raw.get("json") if isinstance(candidate60_raw.get("json"), dict) else {}
    write_json(out_dir / "candidate_audit_60m.json", candidate60)
    command_results["candidate_audit_60m"] = candidate60_raw

    candidate30_raw = run_capture(
        [
            "tools/analyze_candidate_audit.py",
            "--date",
            args.session_date,
            "--market",
            "US",
            "--runtime-mode",
            "live",
            "--horizon-min",
            "30",
            "--limit",
            "20",
        ],
        timeout_sec=300,
    )
    candidate30 = candidate30_raw.get("json") if isinstance(candidate30_raw.get("json"), dict) else {}
    write_json(out_dir / "candidate_audit_30m.json", candidate30)
    command_results["candidate_audit_30m"] = candidate30_raw

    ops_raw = run_capture(
        [
            "tools/monitoring_ops_report.py",
            "--mode",
            "live",
            "--date",
            args.session_date,
            "--market",
            "US",
            "--horizon-min",
            "60",
            "--write-report",
            "--report-dir",
            str(out_dir / "ops_report"),
        ],
        timeout_sec=300,
    )
    ops = ops_raw.get("json") if isinstance(ops_raw.get("json"), dict) else {}
    write_json(out_dir / "monitoring_ops_report.json", ops)
    command_results["monitoring_ops_report"] = ops_raw

    quality_raw = run_to_file(
        ["tools/v2_quality_audit.py", "--session-date", args.session_date, "--runtime-mode", "live", "--market", "US"],
        out_dir / "v2_quality_audit.json",
        timeout_sec=300,
    )
    quality = read_json(out_dir / "v2_quality_audit.json", {})
    command_results["v2_quality_audit"] = quality_raw

    mis_raw = run_to_file(
        [
            "tools/report_claude_misjudgments.py",
            "--date",
            args.session_date,
            "--market",
            "US",
            "--runtime-mode",
            "live",
            "--format",
            "json",
        ],
        out_dir / "claude_misjudgments.json",
        timeout_sec=300,
    )
    misjudgments = read_json(out_dir / "claude_misjudgments.json", {})
    command_results["claude_misjudgments"] = mis_raw

    adaptive_raw = run_capture(
        [
            "tools/adaptive_live_condition_accuracy_report.py",
            "--session-date",
            args.session_date,
            "--market",
            "US",
            "--output-json",
        ],
        timeout_sec=300,
    )
    adaptive = adaptive_raw.get("json") if isinstance(adaptive_raw.get("json"), dict) else {}
    write_json(out_dir / "adaptive_live_condition_accuracy.json", adaptive)
    command_results["adaptive_live_condition_accuracy"] = adaptive_raw

    write_json(out_dir / "post_session_command_results.json", command_results)
    report_md = synthesize(
        out_dir=out_dir,
        monitor=monitor if isinstance(monitor, dict) else {},
        candidate60=candidate60,
        candidate30=candidate30,
        ops=ops,
        quality=quality if isinstance(quality, dict) else {},
        misjudgments=misjudgments if isinstance(misjudgments, dict) else {},
        adaptive=adaptive,
        preflight=preflight,
        command_results=command_results,
    )
    report_path = out_dir / "us_session_profitability_report_20260602_0700.md"
    report_path.write_text(report_md + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "wait_state": wait_state}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
