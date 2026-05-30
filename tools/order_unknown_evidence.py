from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from bot.session_date import resolve_session_date
from runtime_paths import get_runtime_path

KST = ZoneInfo("Asia/Seoul") if ZoneInfo is not None else None


def safe_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(str(value or "{}"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _first_nonempty(mapping: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def pathb_operator_context(item: dict[str, Any], plan: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = plan if isinstance(plan, dict) else {}
    order_no = _first_nonempty(
        item,
        ("order_no", "order_id", "execution_id", "entry_execution_id", "sell_order_no", "sell_order_id"),
    ) or _first_nonempty(
        plan,
        (
            "order_no",
            "order_id",
            "execution_id",
            "entry_execution_id",
            "sell_order_no",
            "sell_order_id",
            "pending_sell_order_no",
            "pathb_pending_sell_order_no",
        ),
    )
    last_event_at = _first_nonempty(item, ("last_event_at", "updated_at", "created_at")) or _first_nonempty(
        plan,
        ("last_event_at", "updated_at", "created_at", "filled_at", "sell_order_sent_at"),
    )
    return {
        "order_no": order_no,
        "last_event_at": last_event_at,
        "operator_action": (
            "read-only: verify broker positions/open orders/fills, then use audited remediation/backfill; "
            "do not close local PathB rows from DB state alone"
        ),
        "remediation_requires_broker_truth": True,
        "read_only_check": True,
        "auto_apply_allowed": False,
    }


def ticker_key(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "")))
    except Exception:
        return int(default)


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "[]")
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def pathb_local_exposure_index(mode: str) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], list[dict[str, Any]]]]:
    positions = _read_json_list(get_runtime_path("state", f"{mode}_open_positions.json", make_parents=False))
    pending_orders = _read_json_list(get_runtime_path("state", f"{mode}_pending_orders.json", make_parents=False))
    by_path: dict[str, dict[str, Any]] = {}
    by_ticker: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def add(item: dict[str, Any], source: str) -> None:
        plan = item.get("pathb_plan") if isinstance(item.get("pathb_plan"), dict) else {}
        market = str(item.get("market") or plan.get("market") or "").strip().upper()
        ticker = ticker_key(market, item.get("ticker") or plan.get("ticker"))
        if not market or not ticker:
            return
        path_run_id = str(
            item.get("pathb_path_run_id")
            or item.get("path_run_id")
            or plan.get("path_run_id")
            or ""
        ).strip()
        sell_order_id = str(
            item.get("pathb_pending_sell_order_no")
            or item.get("pending_sell_order_no")
            or item.get("sell_order_id")
            or ""
        ).strip()
        exposure = {
            "source": source,
            "sources": [source],
            "market": market,
            "ticker": ticker,
            "path_run_id": path_run_id,
            "qty": safe_int(item.get("qty", item.get("pathb_pending_sell_qty", item.get("order_qty", 0)))),
            "local_position_qty": safe_int(item.get("qty", 0)) if source == "local_position" else 0,
            "local_pending_sell_order_id": sell_order_id if source == "local_pending_order" else "",
            "local_sell_order_id": sell_order_id,
            "raw": item,
        }
        by_ticker.setdefault((market, ticker), []).append(exposure)
        if not path_run_id:
            return
        existing = by_path.get(path_run_id)
        if not existing:
            by_path[path_run_id] = exposure
            return
        existing["sources"] = sorted(set(list(existing.get("sources") or []) + [source]))
        existing["qty"] = max(safe_int(existing.get("qty")), safe_int(exposure.get("qty")))
        existing["local_position_qty"] = max(
            safe_int(existing.get("local_position_qty")),
            safe_int(exposure.get("local_position_qty")),
        )
        if exposure.get("local_pending_sell_order_id"):
            existing["local_pending_sell_order_id"] = exposure.get("local_pending_sell_order_id")
        if exposure.get("local_sell_order_id"):
            existing["local_sell_order_id"] = exposure.get("local_sell_order_id")

    for pos in positions:
        if pos.get("pathb_path_run_id") or pos.get("path_run_id") or pos.get("pathb_pending_sell_order_no"):
            add(pos, "local_position")
    for order in pending_orders:
        if order.get("pathb_path_run_id") or order.get("path_run_id") or order.get("pathb_pending_sell_order_no"):
            add(order, "local_pending_order")
    return by_path, by_ticker


def broker_rows_for_ticker(rows: list[Any], market: str, ticker: str) -> list[dict[str, Any]]:
    key = ticker_key(market, ticker)
    out = []
    for row in rows or []:
        if isinstance(row, dict) and ticker_key(market, row.get("ticker")) == key:
            out.append(row)
    return out


def broker_order_id(row: dict[str, Any]) -> str:
    return str(row.get("order_no") or row.get("order_id") or row.get("execution_id") or "").strip()


def row_side(row: dict[str, Any]) -> str:
    return str(row.get("side") or row.get("order_side") or "").strip().lower()


def broker_evidence_for_ticker(
    broker_snapshot: dict[str, Any],
    market: str,
    ticker: str,
    *,
    local_sell_order_id: str = "",
) -> dict[str, Any]:
    markets = broker_snapshot.get("markets") if isinstance(broker_snapshot, dict) else {}
    market_data = markets.get(market) if isinstance(markets, dict) else {}
    snapshot_error = str(broker_snapshot.get("load_error") or "") if isinstance(broker_snapshot, dict) else ""
    market_present = isinstance(market_data, dict) and bool(market_data)
    stale = bool((market_data or {}).get("stale")) if isinstance(market_data, dict) else False
    missing = bool((market_data or {}).get("missing")) if isinstance(market_data, dict) else True
    error = str((market_data or {}).get("error") or snapshot_error or "") if isinstance(market_data, dict) else snapshot_error
    last_success_at = str((market_data or {}).get("last_success_at") or "") if isinstance(market_data, dict) else ""
    if not market_present or missing or stale or error:
        return {
            "broker_truth_unavailable": True,
            "broker_truth_market_present": bool(market_present),
            "broker_truth_stale": bool(stale),
            "broker_truth_error": error,
            "broker_position_qty": None,
            "broker_position_count": 0,
            "broker_open_order_count": 0,
            "broker_fill_count": 0,
            "broker_open_order_evidence": False,
            "broker_any_open_order_evidence": False,
            "broker_sell_fill_evidence": False,
            "broker_any_fill_evidence": False,
            "broker_truth_last_success_at": last_success_at,
        }

    positions = broker_rows_for_ticker(list(market_data.get("positions") or []), market, ticker)
    open_orders = broker_rows_for_ticker(list(market_data.get("open_orders") or []), market, ticker)
    fills = broker_rows_for_ticker(list(market_data.get("today_fills") or []), market, ticker)
    active_open_orders = [
        row for row in open_orders
        if safe_int(row.get("remaining_qty", row.get("order_qty", row.get("qty", 0)))) > 0
    ]
    filled_rows = [
        row for row in fills
        if safe_int(row.get("filled_qty", row.get("qty", 0))) > 0
    ]
    open_sell = [
        row for row in active_open_orders
        if (not local_sell_order_id or broker_order_id(row) == local_sell_order_id)
        and (not row_side(row) or row_side(row) == "sell")
    ]
    sell_fills = [
        row for row in filled_rows
        if (not local_sell_order_id or broker_order_id(row) == local_sell_order_id)
        and (not row_side(row) or row_side(row) == "sell")
    ]
    return {
        "broker_truth_unavailable": False,
        "broker_truth_market_present": True,
        "broker_truth_stale": False,
        "broker_truth_error": "",
        "broker_position_qty": sum(safe_int(row.get("qty")) for row in positions),
        "broker_position_count": len(positions),
        "broker_open_order_count": len(active_open_orders),
        "broker_fill_count": len(filled_rows),
        "broker_open_order_evidence": bool(open_sell),
        "broker_any_open_order_evidence": bool(active_open_orders),
        "broker_sell_fill_evidence": bool(sell_fills),
        "broker_any_fill_evidence": bool(filled_rows),
        "broker_truth_last_success_at": last_success_at,
    }


def attach_exposure_evidence(
    item: dict[str, Any],
    exposure_by_path: dict[str, dict[str, Any]],
    broker_snapshot: dict[str, Any],
) -> None:
    path_run_id = str(item.get("path_run_id") or "")
    exposure = exposure_by_path.get(path_run_id) if path_run_id else None
    item["local_exposure"] = bool(exposure)
    item["local_position_qty"] = safe_int((exposure or {}).get("local_position_qty"))
    item["local_pending_sell_order_id"] = str((exposure or {}).get("local_pending_sell_order_id") or "")
    item["local_sell_order_id"] = str((exposure or {}).get("local_sell_order_id") or "")
    item["local_exposure_sources"] = list((exposure or {}).get("sources") or [])
    market = str(item.get("market") or (exposure or {}).get("market") or "").upper()
    ticker = ticker_key(market, item.get("ticker") or (exposure or {}).get("ticker"))
    item.update(
        broker_evidence_for_ticker(
            broker_snapshot,
            market,
            ticker,
            local_sell_order_id=item["local_sell_order_id"],
        )
    )
    item["pathb_recoverable_still_held"] = pathb_recoverable_still_held(item)
    item["pathb_recoverable_entry_holding"] = pathb_recoverable_entry_holding(item)


def pathb_recoverable_still_held(item: dict[str, Any]) -> bool:
    if str(item.get("status") or "").upper() != "ORDER_UNKNOWN":
        return False
    if bool(item.get("broker_truth_unavailable")):
        return False
    local_qty = safe_int(item.get("local_position_qty"))
    broker_qty = safe_int(item.get("broker_position_qty"))
    if local_qty <= 0 or broker_qty <= 0 or local_qty != broker_qty:
        return False
    if bool(item.get("broker_any_open_order_evidence", item.get("broker_open_order_evidence"))) or bool(
        item.get("broker_any_fill_evidence", item.get("broker_sell_fill_evidence"))
    ):
        return False
    local_sell_order_id = str(item.get("local_sell_order_id") or item.get("local_pending_sell_order_id") or "").strip()
    return bool(local_sell_order_id)


def pathb_recoverable_entry_holding(item: dict[str, Any]) -> bool:
    if str(item.get("status") or "").upper() not in {"ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED"}:
        return False
    if bool(item.get("broker_truth_unavailable")):
        return False
    local_qty = safe_int(item.get("local_position_qty"))
    broker_qty = safe_int(item.get("broker_position_qty"))
    return bool(local_qty > 0 and broker_qty > 0 and local_qty == broker_qty)


def order_unknown_remediation_command(mode: str, market: str, session_before: str) -> str:
    market_arg = str(market or "").upper() or "KR"
    return (
        "python tools/order_unknown_remediation.py "
        f"--mode {mode} --market {market_arg} --session-before {session_before} --dry-run --json"
    )


def order_unknown_remediation_blockers(item: dict[str, Any], *, previous_session: bool) -> list[str]:
    blockers: list[str] = []
    if str(item.get("status") or "").upper() != "ORDER_UNKNOWN":
        blockers.append("status_not_order_unknown")
    if str(item.get("path_type") or "") != "claude_price":
        blockers.append("not_pathb_claude_price")
    if not previous_session:
        blockers.append("current_session_order_unknown")
    if bool(item.get("broker_truth_unavailable")):
        blockers.append("broker_truth_unavailable")
    if not str(item.get("broker_truth_last_success_at") or "").strip():
        blockers.append("broker_truth_timestamp_missing")
    if bool(item.get("local_exposure")) or safe_int(item.get("local_position_qty")) > 0:
        blockers.append("local_exposure_present")
    if str(item.get("local_pending_sell_order_id") or item.get("local_sell_order_id") or "").strip():
        blockers.append("local_sell_order_present")
    if safe_int(item.get("broker_position_qty")) > 0:
        blockers.append("broker_position_present")
    if bool(item.get("broker_any_open_order_evidence", item.get("broker_open_order_evidence"))):
        blockers.append("broker_open_order_present")
    if bool(item.get("broker_any_fill_evidence", item.get("broker_sell_fill_evidence"))):
        blockers.append("broker_fill_present")
    if not str(item.get("path_run_id") or "").strip():
        blockers.append("path_run_id_missing")
    return blockers


def mark_order_unknown_remediation_hint(
    item: dict[str, Any],
    *,
    mode: str,
    previous_session: bool,
    session_before: str,
) -> dict[str, Any]:
    blockers = order_unknown_remediation_blockers(item, previous_session=previous_session)
    allowed = not blockers
    item["remediation_allowed"] = allowed
    item["audited_remediation_allowed"] = allowed
    item["remediation_blockers"] = blockers
    item["remediation_tool"] = order_unknown_remediation_command(
        mode,
        str(item.get("market") or ""),
        session_before,
    )
    if allowed:
        item["suggested_action"] = "run audited ORDER_UNKNOWN dry-run, then apply only after report review"
    elif previous_session:
        item["suggested_action"] = "manual broker-truth reconciliation required before closing this row"
    else:
        item["suggested_action"] = "current-session ORDER_UNKNOWN blocks PathB entry until reconciled"
    return item


def db_broker_truth_ttl_sec() -> int:
    try:
        return max(30, int(os.getenv("PREFLIGHT_DB_BROKER_TRUTH_TTL_SEC", "300") or 300))
    except Exception:
        return 300


def load_broker_truth_snapshot_for_db(mode: str) -> dict[str, Any]:
    from runtime.broker_truth_snapshot import BrokerTruthSnapshot

    ttl = db_broker_truth_ttl_sec()
    return BrokerTruthSnapshot(runtime_mode=mode).load_snapshot(ttl_by_market={"KR": ttl, "US": ttl})


def session_date_guess(market: str) -> str:
    now = datetime.now(KST) if KST is not None else datetime.now()
    return resolve_session_date(market, now).isoformat()
