from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lifecycle.event_store import EventStore
from runtime.broker_truth_snapshot import BrokerTruthSnapshot, load_broker_truth_snapshot
from runtime_paths import get_runtime_path
from tools.live_preflight import _broker_evidence_for_ticker, _safe_int, _ticker_key


ACTIVE_STATUSES = {
    "ORDER_UNKNOWN",
    "ORDER_ACKED",
    "SELL_SENT",
    "SELL_ACKED",
    "SELL_PARTIAL_FILLED",
    "FILLED",
    "PARTIAL_FILLED",
}


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _order_id_from_run(run: dict[str, Any]) -> str:
    plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
    return str(
        plan.get("order_no")
        or plan.get("execution_id")
        or plan.get("entry_execution_id")
        or run.get("execution_id")
        or ""
    ).strip()


def _local_matches(mode: str, market: str, ticker: str, path_run_id: str, sell_order_id: str = "") -> dict[str, Any]:
    positions = _read_json_list(get_runtime_path("state", f"{mode}_open_positions.json", make_parents=False))
    pending = _read_json_list(get_runtime_path("state", f"{mode}_pending_orders.json", make_parents=False))
    market_key = str(market or "").upper()
    ticker_key = _ticker_key(market_key, ticker)
    matched_positions = []
    matched_pending = []
    for item in positions:
        item_market = str(item.get("market") or "").upper()
        item_ticker = _ticker_key(item_market or market_key, item.get("ticker"))
        item_path = str(item.get("pathb_path_run_id") or item.get("path_run_id") or "").strip()
        item_sell = str(item.get("pathb_pending_sell_order_no") or item.get("pending_sell_order_no") or item.get("sell_order_id") or "").strip()
        if item_market == market_key and item_ticker == ticker_key and (not path_run_id or item_path == path_run_id):
            if not sell_order_id or item_sell == sell_order_id:
                matched_positions.append(item)
    for item in pending:
        item_market = str(item.get("market") or "").upper()
        item_ticker = _ticker_key(item_market or market_key, item.get("ticker"))
        item_path = str(item.get("pathb_path_run_id") or item.get("path_run_id") or "").strip()
        item_sell = str(item.get("pathb_pending_sell_order_no") or item.get("pending_sell_order_no") or item.get("sell_order_id") or item.get("order_id") or "").strip()
        if item_market == market_key and item_ticker == ticker_key and (not path_run_id or item_path == path_run_id):
            if not sell_order_id or item_sell == sell_order_id:
                matched_pending.append(item)
    local_qty = sum(_safe_int(item.get("qty", item.get("quantity", 0))) for item in matched_positions)
    local_pending_sell_order_id = sell_order_id or next(
        (
            str(item.get("pathb_pending_sell_order_no") or item.get("pending_sell_order_no") or item.get("sell_order_id") or item.get("order_id") or "").strip()
            for item in [*matched_positions, *matched_pending]
            if str(item.get("pathb_pending_sell_order_no") or item.get("pending_sell_order_no") or item.get("sell_order_id") or item.get("order_id") or "").strip()
        ),
        "",
    )
    return {
        "local_position_match": bool(matched_positions),
        "local_pending_order_match": bool(matched_pending),
        "local_position_qty": local_qty,
        "local_pending_sell_order_id": local_pending_sell_order_id,
        "local_position_count": len(matched_positions),
        "local_pending_order_count": len(matched_pending),
    }


def _load_broker_snapshot(mode: str, market: str, refresh: bool) -> dict[str, Any]:
    if refresh:
        snapshot = BrokerTruthSnapshot(runtime_mode=mode)
        snapshot.refresh_market(market, force=True, ttl_sec=30)
        return load_broker_truth_snapshot(mode)
    return load_broker_truth_snapshot(mode)


def _suggest_action(local: dict[str, Any], broker: dict[str, Any], *, status: str = "") -> tuple[str, bool]:
    if broker.get("broker_sell_fill_evidence"):
        return "close_path_run", False
    if broker.get("broker_open_order_evidence"):
        return "restore_pending_sell", True
    local_qty = _safe_int(local.get("local_position_qty"))
    broker_qty = _safe_int(broker.get("broker_position_qty"))
    if local_qty > 0 and broker_qty > 0 and local_qty == broker_qty:
        if str(status or "").upper() == "ORDER_UNKNOWN" and str(local.get("local_pending_sell_order_id") or "").strip():
            return "recover_still_held", False
        return "keep_position", False
    if local_qty > 0 or broker_qty > 0:
        return "manual_review", True
    return "keep_position", False


def reconcile_live_truth(
    *,
    mode: str = "live",
    market: str = "US",
    ticker: str = "",
    path_run_id: str = "",
    order_id: str = "",
    sell_order_id: str = "",
    refresh_broker: bool = False,
    store_path: str | Path | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    if apply:
        return {
            "ok": False,
            "dry_run": False,
            "error": "apply mode is intentionally blocked until backup/operator/reason workflow is implemented",
        }
    market_key = str(market or "US").upper()
    store = EventStore(store_path) if store_path else EventStore()
    if path_run_id:
        run = store.find_path_run(path_run_id)
        runs = [run] if run else []
    else:
        runs = store.active_path_runs_for_ticker(market=market_key, ticker=ticker, runtime_mode=mode)
    broker_snapshot = _load_broker_snapshot(mode, market_key, refresh_broker)
    actions: list[dict[str, Any]] = []
    for run in runs:
        if not run:
            continue
        status = str(run.get("status") or "")
        if status not in ACTIVE_STATUSES:
            continue
        run_order_id = _order_id_from_run(run)
        if order_id and run_order_id != str(order_id).strip():
            continue
        run_ticker = str(run.get("ticker") or ticker or "").strip()
        run_path_id = str(run.get("path_run_id") or "").strip()
        local = _local_matches(mode, market_key, run_ticker, run_path_id, sell_order_id)
        broker = _broker_evidence_for_ticker(
            broker_snapshot,
            market_key,
            run_ticker,
            local_sell_order_id=sell_order_id or local.get("local_pending_sell_order_id", ""),
        )
        suggested_action, do_not_start = _suggest_action(local, broker, status=status)
        actions.append(
            {
                "market": market_key,
                "ticker": run_ticker,
                "path_run_id": run_path_id,
                "status": status,
                "session_date": run.get("session_date", ""),
                "order_id": run_order_id,
                "sell_order_id": sell_order_id or local.get("local_pending_sell_order_id", ""),
                "broker_position_qty": broker.get("broker_position_qty"),
                "broker_open_order_match": bool(broker.get("broker_open_order_evidence")),
                "broker_today_fill_match": bool(broker.get("broker_sell_fill_evidence")),
                **local,
                "suggested_action": suggested_action,
                "do_not_start": do_not_start,
            }
        )
    return {"ok": True, "dry_run": True, "count": len(actions), "actions": actions}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run live PathB broker/local truth reconciliation.")
    parser.add_argument("--mode", default="live")
    parser.add_argument("--market", default="US", choices=["KR", "US"])
    parser.add_argument("--ticker", default="")
    parser.add_argument("--path-run-id", default="")
    parser.add_argument("--order-id", default="")
    parser.add_argument("--sell-order-id", default="")
    parser.add_argument("--refresh-broker", action="store_true")
    parser.add_argument("--store-path", default="")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    payload = reconcile_live_truth(
        mode=args.mode,
        market=args.market,
        ticker=args.ticker,
        path_run_id=args.path_run_id,
        order_id=args.order_id,
        sell_order_id=args.sell_order_id,
        refresh_broker=bool(args.refresh_broker),
        store_path=args.store_path or None,
        apply=bool(args.apply),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
