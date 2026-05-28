from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.session_date import resolve_session_date
from lifecycle.event_store import EventStore
from runtime_paths import get_runtime_path

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


ACTIVE_STATUSES = {
    "WAITING",
    "HIT",
    "ORDER_SENT",
    "ORDER_ACKED",
    "PARTIAL_FILLED",
    "FILLED",
    "SELL_SENT",
    "SELL_ACKED",
    "SELL_PARTIAL_FILLED",
    "ORDER_UNKNOWN",
}

TERMINAL_EVENT_BY_STATUS = {
    "FILLED": "FILLED",
    "PARTIAL_FILLED": "PARTIAL_FILLED",
    "CLOSED": "CLOSED",
}
PRE_RUN_EVENT_TYPES = {"CLAUDE_PRICE_PLAN_GATE_WARNING", "SAFETY_BLOCKED"}


def _now_kst() -> datetime:
    if ZoneInfo is None:
        return datetime.now()
    return datetime.now(ZoneInfo("Asia/Seoul"))


def _current_sessions(now_dt: datetime | None = None) -> dict[str, str]:
    now = now_dt or _now_kst()
    return {
        "KR": resolve_session_date("KR", now).isoformat(),
        "US": resolve_session_date("US", now).isoformat(),
    }


def _row_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _load_plan(value: Any) -> dict[str, Any]:
    try:
        data = json.loads(str(value or "{}"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_broker_truth_snapshot(mode: str, broker_truth_path: str | Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates = []
    if broker_truth_path:
        candidates.append(Path(broker_truth_path))
    else:
        runtime_mode = "live" if str(mode or "").lower() == "live" else "paper"
        candidates.append(get_runtime_path("state", f"{runtime_mode}_broker_truth_snapshot.json", make_parents=False))
        candidates.append(get_runtime_path("state", "broker_truth_snapshot.json", make_parents=False))
    for path in candidates:
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                return (data if isinstance(data, dict) else {}), {"path": str(path), "loaded": True}
        except Exception as exc:
            return {}, {"path": str(path), "loaded": False, "error": str(exc)}
    return {}, {"path": str(candidates[0]) if candidates else "", "loaded": False, "error": "snapshot_missing"}


def _ticker_from_item(item: dict[str, Any]) -> str:
    for key in ("ticker", "symbol", "code", "pdno", "stock_code", "stockCode"):
        value = str(item.get(key) or "").strip().upper()
        if value:
            return value
    return ""


def _qty_from_item(item: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        raw = item.get(key)
        if raw is None:
            continue
        try:
            return float(str(raw).replace(",", ""))
        except Exception:
            continue
    return 0.0


def _broker_truth_evidence(
    snapshot: dict[str, Any],
    meta: dict[str, Any],
    *,
    market: str,
    ticker: str,
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    ticker_key = str(ticker or "").upper()
    markets = snapshot.get("markets") if isinstance(snapshot, dict) else {}
    market_data = (markets or {}).get(market_key) if isinstance(markets, dict) else None
    base = {
        "snapshot_path": meta.get("path", ""),
        "snapshot_loaded": bool(meta.get("loaded")),
        "snapshot_error": meta.get("error", ""),
        "snapshot_generated_at": snapshot.get("generated_at", "") if isinstance(snapshot, dict) else "",
        "market_present": isinstance(market_data, dict),
        "fresh": None,
        "trusted": None,
        "ticker": ticker_key,
        "position_count": 0,
        "position_qty": 0.0,
        "open_order_count": 0,
        "open_remaining_qty": 0.0,
        "today_fill_count": 0,
        "fill_sides": [],
        "evidence_state": "missing_broker_truth",
    }
    if not isinstance(market_data, dict):
        return base
    base["fresh"] = market_data.get("fresh")
    base["trusted"] = market_data.get("trusted")
    positions = [item for item in (market_data.get("positions") or []) if isinstance(item, dict) and _ticker_from_item(item) == ticker_key]
    open_orders = [item for item in (market_data.get("open_orders") or []) if isinstance(item, dict) and _ticker_from_item(item) == ticker_key]
    fills = [item for item in (market_data.get("today_fills") or []) if isinstance(item, dict) and _ticker_from_item(item) == ticker_key]
    base["position_count"] = len(positions)
    base["position_qty"] = sum(_qty_from_item(item, ("qty", "quantity", "holding_qty", "hldg_qty", "position_qty")) for item in positions)
    base["open_order_count"] = len(open_orders)
    base["open_remaining_qty"] = sum(_qty_from_item(item, ("remaining_qty", "remain_qty", "qty", "quantity")) for item in open_orders)
    base["today_fill_count"] = len(fills)
    base["fill_sides"] = sorted({str(item.get("side") or item.get("order_side") or "").lower() for item in fills if str(item.get("side") or item.get("order_side") or "").strip()})
    if positions or open_orders or fills:
        base["evidence_state"] = "broker_exposure_or_fill_found"
    else:
        base["evidence_state"] = "no_broker_exposure_found"
    return base


def _attach_broker_truth_evidence(
    row: dict[str, Any],
    snapshot: dict[str, Any],
    meta: dict[str, Any],
    *,
    do_not_start: bool = True,
) -> dict[str, Any]:
    evidence = _broker_truth_evidence(
        snapshot,
        meta,
        market=str(row.get("market") or ""),
        ticker=str(row.get("ticker") or ""),
    )
    reason = "requires_operator_broker_reconcile"
    if evidence.get("evidence_state") == "missing_broker_truth":
        reason = "broker_truth_snapshot_missing_or_unusable"
    elif evidence.get("evidence_state") == "broker_exposure_or_fill_found":
        reason = "broker_truth_has_position_order_or_fill"
    return {
        **row,
        "broker_truth_evidence": evidence,
        "do_not_start": bool(do_not_start),
        "do_not_start_reason": reason if do_not_start else "",
    }


def _path_run_id_from_payload(payload: dict[str, Any]) -> str:
    return str(payload.get("path_run_id") or payload.get("pathb_path_run_id") or "").strip()


def _terminal_missing_events(
    runs: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    event_by_path_and_type: set[tuple[str, str]] = set()
    for row in events:
        payload = _load_plan(row.get("payload_json", ""))
        path_run_id = _path_run_id_from_payload(payload)
        if path_run_id:
            event_by_path_and_type.add((path_run_id, str(row.get("event_type") or "")))
    missing: list[dict[str, Any]] = []
    for row in runs:
        status = str(row.get("status") or "")
        expected = TERMINAL_EVENT_BY_STATUS.get(status)
        if expected and (str(row.get("path_run_id") or ""), expected) not in event_by_path_and_type:
            missing.append({**row, "missing_event": expected})
    return missing


def _pathb_like_events_missing_path_run_id(
    events: list[dict[str, Any]],
    decision_ids_with_runs: set[str],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    pre_run: list[dict[str, Any]] = []
    post_run: list[dict[str, Any]] = []
    linkable = 0
    unlinkable = 0
    for row in events:
        payload = _load_plan(row.get("payload_json", ""))
        path_run_id = _path_run_id_from_payload(payload)
        event_type = str(row.get("event_type") or "")
        path_type = str(payload.get("path_type") or payload.get("buy_path") or "")
        if not ((event_type.startswith("CLAUDE_PRICE") or path_type in {"claude_price", "path_b"}) and not path_run_id):
            continue
        decision_id = str(row.get("decision_id") or "")
        item = {
            "event_type": event_type,
            "market": row.get("market"),
            "ticker": row.get("ticker"),
            "decision_id": decision_id,
        }
        rows.append(item)
        if decision_id and decision_id in decision_ids_with_runs:
            linkable += 1
        else:
            unlinkable += 1
        if event_type in PRE_RUN_EVENT_TYPES:
            pre_run.append(item)
        else:
            post_run.append(item)
    return {
        "rows": rows,
        "pre_run": pre_run,
        "post_run": post_run,
        "decision_id_linkable_count": linkable,
        "decision_id_unlinkable_count": unlinkable,
    }


def _status_action(status: str) -> str:
    if status == "ORDER_UNKNOWN":
        return "broker_reconcile_required"
    if status in {"ORDER_SENT", "ORDER_ACKED"}:
        return "verify_open_order_or_fill_before_resolution"
    if status in {"SELL_SENT", "SELL_ACKED", "SELL_PARTIAL_FILLED"}:
        return "verify_sell_fill_or_open_order_before_resolution"
    if status in {"FILLED", "PARTIAL_FILLED"}:
        return "verify_position_or_close_event_before_marking_resolved"
    if status in {"WAITING", "HIT"}:
        return "expire_or_cancel_if_plan_is_from_previous_session"
    return "manual_review"


def _remediation_item(row: dict[str, Any], *, category: str, recommended_action: str) -> dict[str, Any]:
    return {
        "category": category,
        "market": row.get("market"),
        "runtime_mode": row.get("runtime_mode"),
        "session_date": row.get("session_date"),
        "ticker": row.get("ticker"),
        "path_run_id": row.get("path_run_id"),
        "status": row.get("status"),
        "recommended_action": recommended_action,
        "broker_truth_evidence": row.get("broker_truth_evidence") or {},
        "do_not_start": bool(row.get("do_not_start")),
        "do_not_start_reason": row.get("do_not_start_reason", ""),
        "source_of_truth_required": ["broker_fills", "broker_open_orders", "broker_positions"],
        "production_write": False,
    }


def _build_remediation_plan(
    *,
    current_unknown: list[dict[str, Any]],
    previous_unknown: list[dict[str, Any]],
    stale_active: list[dict[str, Any]],
    missing_events: list[dict[str, Any]],
    events_missing_path_run_id: list[dict[str, Any]],
) -> dict[str, Any]:
    order_unknown_items = [
        _remediation_item(row, category="current_order_unknown", recommended_action="block_new_entries_until_broker_reconciled")
        for row in current_unknown
    ] + [
        _remediation_item(row, category="previous_order_unknown", recommended_action="broker_reconcile_then_append_audited_resolution")
        for row in previous_unknown
    ]
    stale_items = [
        _remediation_item(
            row,
            category="previous_session_active_pathb",
            recommended_action=str(row.get("recommended_action") or _status_action(str(row.get("status") or ""))),
        )
        for row in stale_active
    ]
    lifecycle_items = [
        {
            "category": "missing_lifecycle_event",
            "market": row.get("market"),
            "runtime_mode": row.get("runtime_mode"),
            "session_date": row.get("session_date"),
            "ticker": row.get("ticker"),
            "path_run_id": row.get("path_run_id"),
            "status": row.get("status"),
            "missing_event": row.get("missing_event"),
            "recommended_action": "append_audited_backfill_event_after_source_verification",
            "source_of_truth_required": ["path_run_status", "broker_fills", "operator_review"],
            "production_write": False,
        }
        for row in missing_events
    ]
    payload_items = [
        {
            "category": "event_missing_path_run_id",
            "event_type": row.get("event_type"),
            "market": row.get("market"),
            "ticker": row.get("ticker"),
            "recommended_action": "link_to_path_run_id_only_if_unique_match_exists",
            "source_of_truth_required": ["lifecycle_event_payload", "v2_path_runs", "operator_review"],
            "production_write": False,
        }
        for row in events_missing_path_run_id
    ]
    return {
        "dry_run_only": True,
        "production_writes_supported": False,
        "requires_broker_truth": bool(order_unknown_items or stale_items or lifecycle_items),
        "summary": {
            "order_unknown_items": len(order_unknown_items),
            "stale_active_items": len(stale_items),
            "missing_lifecycle_event_items": len(lifecycle_items),
            "event_payload_link_items": len(payload_items),
        },
        "order_unknown": order_unknown_items,
        "stale_active": stale_items,
        "lifecycle_backfill_candidates": lifecycle_items,
        "event_payload_link_candidates": payload_items,
    }


def build_report(
    *,
    db_path: str | Path | None = None,
    mode: str = "live",
    current_sessions: dict[str, str] | None = None,
    limit: int = 200,
    broker_truth_path: str | Path | None = None,
) -> dict[str, Any]:
    store = EventStore(db_path) if db_path else EventStore()
    sessions = current_sessions or _current_sessions()
    limit = max(1, int(limit or 200))
    broker_truth_snapshot, broker_truth_meta = _load_broker_truth_snapshot(mode, broker_truth_path)
    with store.connect() as conn:
        unknown_rows = conn.execute(
            """
            SELECT market, runtime_mode, session_date, ticker, path_run_id, status, updated_at, plan_json
            FROM v2_path_runs
            WHERE runtime_mode=? AND path_type='claude_price' AND status='ORDER_UNKNOWN'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (mode, limit),
        ).fetchall()
        stale_rows = conn.execute(
            f"""
            SELECT market, runtime_mode, session_date, ticker, path_run_id, status, updated_at, plan_json
            FROM v2_path_runs
            WHERE runtime_mode=? AND path_type='claude_price' AND status IN ({','.join('?' for _ in ACTIVE_STATUSES)})
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (mode, *sorted(ACTIVE_STATUSES), limit),
        ).fetchall()
        recent_events = conn.execute(
            """
            SELECT event_type, market, runtime_mode, session_date, ticker, decision_id, payload_json
            FROM lifecycle_events
            WHERE runtime_mode=?
            ORDER BY event_id DESC
            LIMIT ?
            """,
            (mode, limit * 5),
        ).fetchall()
        recent_runs = conn.execute(
            """
            SELECT path_run_id, market, runtime_mode, session_date, ticker, status
            FROM v2_path_runs
            WHERE runtime_mode=? AND path_type='claude_price'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (mode, limit),
        ).fetchall()
        full_events = conn.execute(
            """
            SELECT event_type, market, runtime_mode, session_date, ticker, decision_id, payload_json
            FROM lifecycle_events
            WHERE runtime_mode=?
            ORDER BY event_id
            """,
            (mode,),
        ).fetchall()
        full_runs = conn.execute(
            """
            SELECT path_run_id, market, runtime_mode, session_date, ticker, status
            FROM v2_path_runs
            WHERE runtime_mode=? AND path_type='claude_price'
            ORDER BY updated_at DESC
            """,
            (mode,),
        ).fetchall()
        decision_id_rows = conn.execute(
            """
            SELECT DISTINCT decision_id
            FROM v2_path_runs
            WHERE runtime_mode=? AND path_type='claude_price' AND decision_id IS NOT NULL AND decision_id<>''
            """,
            (mode,),
        ).fetchall()

    current_unknown: list[dict[str, Any]] = []
    previous_unknown: list[dict[str, Any]] = []
    for row in unknown_rows:
        item = _row_dict(row)
        plan = _load_plan(item.pop("plan_json", ""))
        item["order_unknown_phase"] = str(plan.get("order_unknown_phase") or "")
        item["order_unknown_resolution"] = str(plan.get("order_unknown_resolution") or "")
        item["recommended_action"] = _status_action("ORDER_UNKNOWN")
        item = _attach_broker_truth_evidence(item, broker_truth_snapshot, broker_truth_meta)
        if item.get("session_date") == sessions.get(str(item.get("market") or "")):
            current_unknown.append(item)
        else:
            previous_unknown.append(item)

    stale_active: list[dict[str, Any]] = []
    for row in stale_rows:
        item = _row_dict(row)
        item.pop("plan_json", None)
        if item.get("session_date") == sessions.get(str(item.get("market") or "")):
            continue
        item["recommended_action"] = _status_action(str(item.get("status") or ""))
        item = _attach_broker_truth_evidence(item, broker_truth_snapshot, broker_truth_meta)
        stale_active.append(item)

    recent_events_dict = [_row_dict(row) for row in recent_events]
    recent_runs_dict = [_row_dict(row) for row in recent_runs]
    full_events_dict = [_row_dict(row) for row in full_events]
    full_runs_dict = [_row_dict(row) for row in full_runs]
    decision_ids_with_runs = {str(row["decision_id"] or "") for row in decision_id_rows}
    missing_events = _terminal_missing_events(recent_runs_dict, recent_events_dict)
    events_missing = _pathb_like_events_missing_path_run_id(recent_events_dict, decision_ids_with_runs)
    events_missing_path_run_id = events_missing["rows"]
    full_missing_events = _terminal_missing_events(full_runs_dict, full_events_dict)

    status_counts = Counter(str(item.get("status") or "") for item in stale_active)
    market_counts = Counter(str(item.get("market") or "") for item in stale_active)
    remediation_plan = _build_remediation_plan(
        current_unknown=current_unknown,
        previous_unknown=previous_unknown,
        stale_active=stale_active,
        missing_events=missing_events,
        events_missing_path_run_id=events_missing_path_run_id,
    )
    return {
        "generated_at": _now_kst().isoformat(timespec="seconds"),
        "mode": mode,
        "db_path": str(store.path),
        "dry_run": True,
        "write_supported": False,
        "current_sessions": sessions,
        "broker_truth": {
            "snapshot_path": broker_truth_meta.get("path", ""),
            "snapshot_loaded": bool(broker_truth_meta.get("loaded")),
            "snapshot_error": broker_truth_meta.get("error", ""),
            "generated_at": broker_truth_snapshot.get("generated_at", "") if isinstance(broker_truth_snapshot, dict) else "",
        },
        "order_unknown": {
            "current_count": len(current_unknown),
            "previous_count": len(previous_unknown),
            "current_session": current_unknown,
            "previous_session": previous_unknown,
        },
        "stale_active": {
            "count": len(stale_active),
            "by_status": dict(sorted(status_counts.items())),
            "by_market": dict(sorted(market_counts.items())),
            "rows": stale_active,
        },
        "lifecycle_window_consistency": {
            "missing_events_count": len(missing_events),
            "events_missing_path_run_id_count": len(events_missing_path_run_id),
            "recent_window_missing_events_count": len(missing_events),
            "recent_window_size_events": limit * 5,
            "recent_window_size_runs": limit,
            "missing_events": missing_events,
            "events_missing_path_run_id": events_missing_path_run_id,
            "pathb_pre_run_events_missing_path_run_id": events_missing["pre_run"],
            "pathb_post_run_events_missing_path_run_id": events_missing["post_run"],
            "pathb_pre_run_events_missing_path_run_id_count": len(events_missing["pre_run"]),
            "pathb_post_run_events_missing_path_run_id_count": len(events_missing["post_run"]),
            "decision_id_linkable_count": events_missing["decision_id_linkable_count"],
            "decision_id_unlinkable_count": events_missing["decision_id_unlinkable_count"],
        },
        "lifecycle_full_consistency": {
            "missing_events_count": len(full_missing_events),
            "full_terminal_missing_events_count": len(full_missing_events),
            "missing_events": full_missing_events,
            "checked_runs": len(full_runs_dict),
            "checked_events": len(full_events_dict),
        },
        "lifecycle_consistency": {
            "basis": "recent_window",
            "missing_events_count": len(missing_events),
            "events_missing_path_run_id_count": len(events_missing_path_run_id),
            "full_terminal_missing_events_count": len(full_missing_events),
            "missing_events": missing_events,
            "events_missing_path_run_id": events_missing_path_run_id,
        },
        "remediation_plan": remediation_plan,
        "recommended_next_steps": [
            "Use broker fills/open orders as the source of truth before changing any ORDER_UNKNOWN or stale active row.",
            "Resolve current-session ORDER_UNKNOWN before allowing same-market or same-ticker new entries.",
            "For previous-session rows, prefer an audited reconciliation event/backfill over direct manual SQL updates.",
            "Do not run production DB write cleanup from this tool; it is intentionally report-only.",
        ],
    }


def _to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# PathB Legacy Remediation Report",
        "",
        f"- generated_at: {report['generated_at']}",
        f"- mode: {report['mode']}",
        f"- db_path: {report['db_path']}",
        f"- dry_run: {report['dry_run']}",
        f"- write_supported: {report['write_supported']}",
        f"- broker_truth_loaded: {(report.get('broker_truth') or {}).get('snapshot_loaded')}",
        f"- broker_truth_path: {(report.get('broker_truth') or {}).get('snapshot_path', '')}",
        "",
        "## Summary",
        "",
        f"- current ORDER_UNKNOWN: {report['order_unknown']['current_count']}",
        f"- previous ORDER_UNKNOWN: {report['order_unknown']['previous_count']}",
        f"- stale active rows: {report['stale_active']['count']}",
        f"- recent-window missing lifecycle events: {report['lifecycle_window_consistency']['missing_events_count']}",
        f"- full terminal missing lifecycle events: {report['lifecycle_full_consistency']['missing_events_count']}",
        f"- events missing payload_json.path_run_id: {report['lifecycle_window_consistency']['events_missing_path_run_id_count']}",
        f"- remediation plan dry-run only: {report['remediation_plan']['dry_run_only']}",
        "",
        "## Stale Active By Status",
        "",
    ]
    for status, count in (report["stale_active"].get("by_status") or {}).items():
        lines.append(f"- {status}: {count}")
    lines.extend(["", "## Remediation Plan Summary", ""])
    for name, count in (report["remediation_plan"].get("summary") or {}).items():
        lines.append(f"- {name}: {count}")
    lines.extend(["", "## Recommended Next Steps", ""])
    for item in report["recommended_next_steps"]:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Report legacy PathB/ORDER_UNKNOWN remediation candidates.")
    parser.add_argument("--db", default="", help="EventStore SQLite DB path; default uses runtime path")
    parser.add_argument("--mode", default="live", choices=["live", "paper"])
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--broker-truth", default="", help="broker truth snapshot JSON path; default uses runtime state snapshot")
    parser.add_argument("--json", action="store_true", dest="print_json")
    parser.add_argument("--write-report", action="store_true", help="write JSON/MD report under data/v2_reports")
    args = parser.parse_args()

    report = build_report(
        db_path=args.db or None,
        mode=args.mode,
        limit=args.limit,
        broker_truth_path=args.broker_truth or None,
    )
    if args.write_report:
        out_dir = ROOT / "data" / "v2_reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = _now_kst().strftime("%Y%m%d_%H%M%S")
        json_path = out_dir / f"pathb_legacy_remediation_{stamp}.json"
        md_path = out_dir / f"pathb_legacy_remediation_{stamp}.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(_to_markdown(report), encoding="utf-8")
        report["report_paths"] = {"json": str(json_path), "md": str(md_path)}
    if args.print_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"db_path={report['db_path']}")
        print(f"current_order_unknown={report['order_unknown']['current_count']}")
        print(f"previous_order_unknown={report['order_unknown']['previous_count']}")
        print(f"stale_active={report['stale_active']['count']}")
        print(f"broker_truth_loaded={report['broker_truth']['snapshot_loaded']}")
        print(f"missing_lifecycle_events={report['lifecycle_consistency']['missing_events_count']}")
        print(f"events_missing_path_run_id={report['lifecycle_consistency']['events_missing_path_run_id_count']}")
        if report.get("report_paths"):
            print(f"json={report['report_paths']['json']}")
            print(f"md={report['report_paths']['md']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
