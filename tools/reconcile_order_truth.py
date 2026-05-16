from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent


EVENT_BY_RESOLUTION = {
    "filled": "FILLED",
    "rejected": "ORDER_REJECTED",
    "cancelled": "ORDER_CANCELLED",
    "not_found": "ORDER_CANCELLED",
    "unresolved": "QUALITY_MARKED",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_market(value: str) -> str:
    market = str(value or "ALL").strip().upper()
    return market if market in {"KR", "US", "ALL"} else "ALL"


def _load_unknown_runs(
    store: EventStore,
    *,
    date: str = "",
    market: str = "ALL",
    ticker: str = "",
    order_id: str = "",
) -> list[dict[str, Any]]:
    market_key = _normalize_market(market)
    markets = ["KR", "US"] if market_key == "ALL" else [market_key]
    rows: list[dict[str, Any]] = []
    for item_market in markets:
        if date:
            candidates = store.path_runs_for_session(
                runtime_mode="live",
                market=item_market,
                session_date=date,
                status="ORDER_UNKNOWN",
            )
        else:
            candidates = []
            # Keep the no-date path conservative: only current active unknowns are
            # needed for remediation commands printed by live_guardian.
            for run in store.path_runs_for_session(runtime_mode="live", market=item_market, session_date="", status="ORDER_UNKNOWN"):
                candidates.append(run)
        for run in candidates:
            plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
            if ticker and str(run.get("ticker") or "").strip().upper() != str(ticker).strip().upper():
                continue
            run_order = str(
                plan.get("order_no")
                or plan.get("execution_id")
                or plan.get("entry_execution_id")
                or run.get("execution_id")
                or ""
            )
            if order_id and run_order != str(order_id).strip():
                continue
            rows.append({**run, "_order_id": run_order})
    return rows


def reconcile_order_truth(
    *,
    date: str = "",
    market: str = "ALL",
    ticker: str = "",
    order_id: str = "",
    dry_run: bool = True,
    resolution: str = "unresolved",
    operator: str = "",
    reason: str = "",
    store_path: str | Path | None = None,
) -> dict[str, Any]:
    store = EventStore(store_path) if store_path else EventStore()
    rows = _load_unknown_runs(store, date=date, market=market, ticker=ticker, order_id=order_id)
    resolution_key = str(resolution or "unresolved").strip().lower()
    if resolution_key not in EVENT_BY_RESOLUTION:
        resolution_key = "unresolved"
    actions: list[dict[str, Any]] = []
    for run in rows:
        action = {
            "path_run_id": run.get("path_run_id"),
            "market": run.get("market"),
            "session_date": run.get("session_date"),
            "ticker": run.get("ticker"),
            "order_id": run.get("_order_id", ""),
            "status_before": run.get("status"),
            "broker_truth": resolution_key,
            "would_event_type": EVENT_BY_RESOLUTION[resolution_key],
            "applied": False,
        }
        if not dry_run:
            event_type = EVENT_BY_RESOLUTION[resolution_key]
            payload = {
                "source": "reconcile_order_truth",
                "path_run_id": run.get("path_run_id"),
                "broker_truth": resolution_key,
                "operator": operator,
                "reason": reason or "manual_order_unknown_reconciliation",
                "reconciled_at": _now(),
            }
            store.append(
                LifecycleEvent(
                    event_type=event_type,
                    market=str(run.get("market") or "KR"),
                    runtime_mode=str(run.get("runtime_mode") or "live"),
                    session_date=str(run.get("session_date") or date),
                    ticker=str(run.get("ticker") or ticker),
                    decision_id=str(run.get("decision_id") or f"manual_reconcile_{run.get('path_run_id', '')}"),
                    execution_id=action["order_id"] or None,
                    prompt_version="manual_reconcile",
                    brain_snapshot_id="manual_reconcile",
                    reason_code=f"ORDER_UNKNOWN_{resolution_key.upper()}",
                    payload=payload,
                )
            )
            if resolution_key != "unresolved":
                next_status = "FILLED" if resolution_key == "filled" else "ORDER_CANCELLED"
                store.update_path_run(
                    str(run.get("path_run_id") or ""),
                    status=next_status,
                    plan={"order_unknown_resolution": resolution_key, "order_unknown_reconciled_at": _now()},
                    merge_plan=True,
                )
            action["applied"] = True
        actions.append(action)
    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "count": len(rows),
        "resolution": resolution_key,
        "actions": actions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run/apply ORDER_UNKNOWN broker truth reconciliation.")
    parser.add_argument("--date", default="")
    parser.add_argument("--market", default="ALL", choices=["KR", "US", "ALL"])
    parser.add_argument("--ticker", default="")
    parser.add_argument("--order-id", default="")
    parser.add_argument("--resolution", default="unresolved", choices=sorted(EVENT_BY_RESOLUTION))
    parser.add_argument("--operator", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--store-path", default="")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    payload = reconcile_order_truth(
        date=args.date,
        market=args.market,
        ticker=args.ticker,
        order_id=args.order_id,
        dry_run=not bool(args.apply),
        resolution=args.resolution,
        operator=args.operator,
        reason=args.reason,
        store_path=args.store_path or None,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
