from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lifecycle.event_store import EventStore


def build_quality_audit(
    *,
    store: EventStore | None = None,
    session_date: str | None = None,
    runtime_mode: str | None = "live",
    markets: list[str] | None = None,
    live_decisions_path: str | Path = ROOT / "state" / "live_decisions.jsonl",
    hold_advisor_dir: str | Path = ROOT / "logs" / "hold_advisor",
    raw_calls_dir: str | Path = ROOT / "logs" / "raw_calls",
    brain_path: str | Path = ROOT / "state" / "brain.json",
) -> dict[str, Any]:
    event_store = store or EventStore()
    return {
        "lifecycle_reconciliation": build_lifecycle_reconciliation(
            event_store,
            session_date=session_date,
            runtime_mode=runtime_mode,
            markets=markets,
        ),
        "stop_loss_forensics": build_stop_loss_forensics(
            event_store,
            live_decisions_path=live_decisions_path,
            runtime_mode=runtime_mode,
        ),
        "hold_advisor_linkage": build_hold_advisor_linkage_audit(
            live_decisions_path=live_decisions_path,
            hold_advisor_dir=hold_advisor_dir,
        ),
        "raw_call_breakdown": build_raw_call_breakdown(raw_calls_dir=raw_calls_dir),
        "brain_metric_provenance": build_brain_metric_provenance_audit(brain_path=brain_path),
    }


def build_lifecycle_reconciliation(
    store: EventStore,
    *,
    session_date: str | None = None,
    runtime_mode: str | None = "live",
    markets: list[str] | None = None,
) -> dict[str, Any]:
    events = _load_events(store, session_date=session_date, runtime_mode=runtime_mode, markets=markets)
    fills_raw = [event for event in events if event.get("event_type") == "FILLED"]
    closes = [event for event in events if event.get("event_type") == "CLOSED"]
    path_runs = _load_path_runs(store, session_date=session_date, runtime_mode=runtime_mode, markets=markets)
    path_runs_by_decision: dict[str, list[dict[str, Any]]] = {}
    for run in path_runs:
        path_runs_by_decision.setdefault(str(run.get("decision_id") or ""), []).append(run)
    fill_groups: dict[str, list[dict[str, Any]]] = {}
    for event in fills_raw:
        fill_groups.setdefault(_unique_fill_key(event), []).append(event)

    closes_by_decision: dict[str, list[dict[str, Any]]] = {}
    closes_by_ticker_session: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for event in closes:
        closes_by_decision.setdefault(str(event.get("decision_id") or ""), []).append(event)
        closes_by_ticker_session.setdefault(
            (
                str(event.get("market") or ""),
                str(event.get("session_date") or ""),
                str(event.get("ticker") or ""),
            ),
            [],
        ).append(event)

    unique_fills: list[dict[str, Any]] = []
    no_close: list[dict[str, Any]] = []
    with_close = 0
    for key, group in sorted(fill_groups.items()):
        first = group[0]
        matched = closes_by_decision.get(str(first.get("decision_id") or "")) or closes_by_ticker_session.get(
            (
                str(first.get("market") or ""),
                str(first.get("session_date") or ""),
                str(first.get("ticker") or ""),
            ),
            [],
        )
        item = {
            "fill_key": key,
            "market": first.get("market"),
            "session_date": first.get("session_date"),
            "ticker": first.get("ticker"),
            "decision_id": first.get("decision_id"),
            "path_type": _path_type_for_decision(path_runs_by_decision, str(first.get("decision_id") or "")),
            "execution_id": first.get("execution_id"),
            "position_id": first.get("position_id"),
            "occurred_at": first.get("occurred_at"),
            "duplicate_fill_events": len(group),
            "has_close": bool(matched),
            "close_event_ids": [event.get("event_id") for event in matched[:5]],
        }
        unique_fills.append(item)
        if matched:
            with_close += 1
        else:
            no_close.append(item)

    same_day_blocks = [
        event
        for event in events
        if event.get("event_type") == "SAFETY_BLOCKED"
        and "SAME_DAY" in str(event.get("reason_code") or "").upper()
    ]
    same_day_missing_evidence = []
    for event in same_day_blocks:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if not payload.get("same_day_reentry_closed_event_id"):
            same_day_missing_evidence.append(_event_ref(event))

    return {
        "raw_fill_events": len(fills_raw),
        "unique_fill_count": len(fill_groups),
        "closed_event_count": len(closes),
        "unique_fill_with_close": with_close,
        "unique_fill_without_close": len(no_close),
        "unique_fill_without_close_examples": no_close[:50],
        "path_run_count": len(path_runs),
        "path_run_counts": _path_run_counts(path_runs),
        "unique_fill_path_coverage": _unique_fill_path_coverage(unique_fills),
        "same_day_block_count": len(same_day_blocks),
        "same_day_missing_closed_evidence_count": len(same_day_missing_evidence),
        "same_day_missing_closed_evidence_examples": same_day_missing_evidence[:50],
    }


def build_stop_loss_forensics(
    store: EventStore,
    *,
    live_decisions_path: str | Path,
    runtime_mode: str | None = "live",
) -> dict[str, Any]:
    close_rows = [
        row
        for row in _read_jsonl(Path(live_decisions_path))
        if str(row.get("exit_reason") or "") == "stop_loss"
    ]
    items: list[dict[str, Any]] = []
    for row in close_rows:
        market = str(row.get("market") or "")
        ticker = str(row.get("ticker") or "")
        session_date = str(row.get("session_date") or str(row.get("timestamp") or "")[:10])
        evidence = _v2_evidence_for_ticker(
            store,
            market=market,
            ticker=ticker,
            session_date=session_date,
            runtime_mode=runtime_mode,
        )
        planned_stop = _first_float(
            [
                evidence.get("planned_stop_loss"),
                row.get("strategy_stop_price"),
                row.get("effective_stop_price"),
            ]
        )
        exit_price = _safe_float(row.get("exit_price") or row.get("exit_price_native"))
        slippage_pct = None
        if planned_stop and exit_price:
            slippage_pct = round((exit_price - planned_stop) / planned_stop * 100.0, 4)
        entry_price = _safe_float(row.get("entry_price") or row.get("actual_fill_price"))
        items.append(
            {
                "market": market,
                "ticker": ticker,
                "session_date": session_date,
                "timestamp": row.get("timestamp"),
                "strategy": row.get("strategy") or row.get("source_strategy"),
                "mode": row.get("mode"),
                "qty": row.get("qty"),
                "order_no": row.get("order_no"),
                "path": evidence.get("path") or "legacy_or_patha_unresolved",
                "decision_id": evidence.get("decision_id"),
                "path_run_id": evidence.get("path_run_id"),
                "planned_stop_loss": planned_stop,
                "exit_price": exit_price,
                "entry_price": entry_price,
                "pnl_pct": _safe_float(row.get("pnl_pct")),
                "stop_slippage_pct": slippage_pct,
                "missing_fields": [
                    name
                    for name, value in {
                        "entry_price": entry_price,
                        "planned_stop_loss": planned_stop,
                        "path_evidence": evidence.get("path"),
                    }.items()
                    if value in (None, "", 0)
                ],
            }
        )
    return {
        "count": len(items),
        "items": items,
    }


def build_hold_advisor_linkage_audit(
    *,
    live_decisions_path: str | Path,
    hold_advisor_dir: str | Path,
) -> dict[str, Any]:
    live_rows = _read_jsonl(Path(live_decisions_path))
    close_rows = [row for row in live_rows if row.get("exit_reason")]
    advisor_logs: list[dict[str, Any]] = []
    base = Path(hold_advisor_dir)
    if base.exists():
        for path in sorted(base.glob("decisions_*.jsonl")):
            date_key = path.stem.replace("decisions_", "")
            for row in _read_jsonl(path):
                row["_date"] = date_key
                advisor_logs.append(row)
    close_key_set = {
        (str(row.get("session_date") or str(row.get("timestamp") or "")[:10]), str(row.get("ticker") or ""))
        for row in close_rows
    }
    advisor_close_matches = sum(1 for row in advisor_logs if (row.get("_date"), str(row.get("ticker") or "")) in close_key_set)
    advisor_like_closes = [
        row
        for row in close_rows
        if str(row.get("exit_reason") or "")
        in {"intraday_review_sell", "tp_analyst_sell", "pre_session_sell", "max_hold"}
    ]
    close_rows_with_votes = sum(1 for row in close_rows if row.get("votes"))
    return {
        "advisor_log_rows": len(advisor_logs),
        "advisor_log_votes_rows": sum(1 for row in advisor_logs if row.get("votes")),
        "advisor_log_outcome_rows": sum(1 for row in advisor_logs if row.get("outcome") is not None),
        "live_close_rows": len(close_rows),
        "live_close_rows_with_votes": close_rows_with_votes,
        "advisor_like_close_rows": len(advisor_like_closes),
        "advisor_like_close_rows_with_votes": sum(1 for row in advisor_like_closes if row.get("votes")),
        "advisor_log_same_day_ticker_close_matches": advisor_close_matches,
    }


def build_raw_call_breakdown(*, raw_calls_dir: str | Path) -> dict[str, Any]:
    base = Path(raw_calls_dir)
    by_day_label: dict[tuple[str, str], dict[str, Any]] = {}
    if not base.exists():
        return {"exists": False, "by_day_label": []}
    for path in sorted(base.glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        day = str(row.get("timestamp") or row.get("date") or path.name[:8])[:10]
        if len(day) == 8 and day.isdigit():
            day = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
        label = str(row.get("label") or "unknown")
        tokens = row.get("tokens") if isinstance(row.get("tokens"), dict) else {}
        item = by_day_label.setdefault(
            (day, label),
            {"day": day, "label": label, "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )
        input_tokens = int(tokens.get("input") or tokens.get("input_tokens") or row.get("input_tokens") or 0)
        output_tokens = int(tokens.get("output") or tokens.get("output_tokens") or row.get("output_tokens") or 0)
        item["calls"] += 1
        item["input_tokens"] += input_tokens
        item["output_tokens"] += output_tokens
        item["total_tokens"] += input_tokens + output_tokens
    return {
        "exists": True,
        "by_day_label": sorted(by_day_label.values(), key=lambda row: (row["day"], -row["total_tokens"], row["label"])),
    }


def build_brain_metric_provenance_audit(*, brain_path: str | Path) -> dict[str, Any]:
    path = Path(brain_path)
    if not path.exists():
        return {"exists": False, "metrics": []}
    try:
        brain = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"exists": True, "parse_error": str(exc), "metrics": []}
    metrics: list[dict[str, Any]] = []
    markets = brain.get("markets") if isinstance(brain.get("markets"), dict) else {}
    for market, data in markets.items():
        if not isinstance(data, dict):
            continue
        for section in ("analyst_performance", "mode_performance", "strategy_performance"):
            section_data = data.get(section) if isinstance(data.get(section), dict) else {}
            for key, value in section_data.items():
                if not isinstance(value, dict):
                    continue
                metrics.append(
                    {
                        "market": market,
                        "section": section,
                        "key": key,
                        "has_rate": "rate" in value or "win_rate" in value,
                        "has_source": "source" in value,
                        "has_denominator": "total" in value or "count" in value,
                        "has_horizon": "horizon" in value,
                        "has_coverage": "coverage" in value,
                        "has_last_updated": "last_updated" in value,
                    }
                )
    return {
        "exists": True,
        "metric_count": len(metrics),
        "missing_source_count": sum(1 for item in metrics if not item["has_source"]),
        "missing_horizon_count": sum(1 for item in metrics if not item["has_horizon"]),
        "metrics": metrics[:200],
    }


def _load_events(
    store: EventStore,
    *,
    session_date: str | None,
    runtime_mode: str | None,
    markets: list[str] | None,
) -> list[dict[str, Any]]:
    if markets:
        events: list[dict[str, Any]] = []
        for market in markets:
            events.extend(store.events_for_session(market=market, runtime_mode=runtime_mode, session_date=session_date))
        return events
    return store.events_for_session(runtime_mode=runtime_mode, session_date=session_date)


def _load_path_runs(
    store: EventStore,
    *,
    session_date: str | None,
    runtime_mode: str | None,
    markets: list[str] | None,
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    if markets:
        for market in markets:
            runs.extend(store.path_runs_for_session(market=market, runtime_mode=runtime_mode, session_date=session_date))
        return runs
    return store.path_runs_for_session(runtime_mode=runtime_mode, session_date=session_date)


def _path_type_for_decision(path_runs_by_decision: dict[str, list[dict[str, Any]]], decision_id: str) -> str:
    runs = path_runs_by_decision.get(decision_id) or []
    if not runs:
        return "patha_or_legacy"
    return str((runs[-1] or {}).get("path_type") or "unknown_path")


def _path_run_counts(path_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = {}
    for run in path_runs:
        key = (str(run.get("path_type") or "unknown"), str(run.get("status") or "unknown"))
        counts[key] = counts.get(key, 0) + 1
    return [
        {"path_type": path_type, "status": status, "count": count}
        for (path_type, status), count in sorted(counts.items())
    ]


def _unique_fill_path_coverage(unique_fills: list[dict[str, Any]]) -> dict[str, Any]:
    by_path: dict[str, int] = {}
    for item in unique_fills:
        path_type = str(item.get("path_type") or "unknown")
        by_path[path_type] = by_path.get(path_type, 0) + 1
    return {
        "total": len(unique_fills),
        "with_v2_path_run": sum(count for path_type, count in by_path.items() if path_type not in {"patha_or_legacy", "unknown"}),
        "by_path_type": [{"path_type": key, "count": value} for key, value in sorted(by_path.items())],
    }


def _unique_fill_key(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    execution_id = str(event.get("execution_id") or "").strip()
    if execution_id:
        return execution_id
    position_id = str(event.get("position_id") or "").strip()
    if position_id:
        return position_id
    qty = payload.get("qty") or payload.get("filled_qty") or ""
    price = payload.get("price") or payload.get("avg_price") or ""
    return "|".join(
        [
            str(event.get("decision_id") or ""),
            str(event.get("market") or ""),
            str(event.get("ticker") or ""),
            str(event.get("occurred_at") or ""),
            str(qty),
            str(price),
        ]
    )


def _event_ref(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "reason_code": event.get("reason_code"),
        "market": event.get("market"),
        "session_date": event.get("session_date"),
        "ticker": event.get("ticker"),
        "decision_id": event.get("decision_id"),
        "occurred_at": event.get("occurred_at"),
    }


def _v2_evidence_for_ticker(
    store: EventStore,
    *,
    market: str,
    ticker: str,
    session_date: str,
    runtime_mode: str | None = "live",
) -> dict[str, Any]:
    if not market or not ticker or not session_date:
        return {}
    events = store.events_for_session(market=market, runtime_mode=runtime_mode, session_date=session_date)
    matching = [event for event in events if str(event.get("ticker") or "") == ticker]
    if not matching:
        return {}
    decision_id = str(matching[0].get("decision_id") or "")
    path_runs = []
    if decision_id:
        with store.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM v2_path_runs
                WHERE decision_id=? AND (? IS NULL OR runtime_mode=?)
                ORDER BY created_at
                """,
                (decision_id, runtime_mode, runtime_mode),
            ).fetchall()
        path_runs = [store._path_run_row_to_dict(row) for row in rows]
    path_run = path_runs[-1] if path_runs else {}
    plan = path_run.get("plan") if isinstance(path_run.get("plan"), dict) else {}
    return {
        "path": path_run.get("path_type") or ("v2_lifecycle_no_path_run" if matching else ""),
        "decision_id": decision_id,
        "path_run_id": path_run.get("path_run_id"),
        "planned_stop_loss": _safe_float(plan.get("stop_loss")) if plan else None,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(values: list[Any]) -> float | None:
    for value in values:
        parsed = _safe_float(value)
        if parsed is not None and parsed > 0:
            return parsed
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Build V2 quality, reconciliation, and forensic audit.")
    parser.add_argument("--session-date", default="")
    parser.add_argument("--runtime-mode", choices=["live", "paper"], default="live")
    parser.add_argument("--market", choices=["KR", "US", "ALL"], default="ALL")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    markets = None if args.market == "ALL" else [args.market]
    report = build_quality_audit(
        session_date=args.session_date or None,
        runtime_mode=args.runtime_mode,
        markets=markets,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
