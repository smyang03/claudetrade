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


PENDING_STATUSES = {"SELL_SENT", "SELL_ACKED", "SELL_PARTIAL_FILLED"}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}


def _num(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except Exception:
        return 0.0


def _qty(value: Any) -> int:
    try:
        return max(0, int(float(str(value or "0").replace(",", ""))))
    except Exception:
        return 0


def _market_snapshot(snapshot: dict[str, Any], market: str) -> dict[str, Any]:
    markets = snapshot.get("markets") if isinstance(snapshot.get("markets"), dict) else {}
    return markets.get(str(market or "").upper()) if isinstance(markets.get(str(market or "").upper()), dict) else {}


def _sell_fills(snapshot: dict[str, Any], market: str) -> list[dict[str, Any]]:
    data = _market_snapshot(snapshot, market)
    rows = data.get("today_fills") if isinstance(data.get("today_fills"), list) else []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        side = str(row.get("side") or row.get("side_text") or "").lower()
        if "sell" not in side and "매도" not in side:
            continue
        if _qty(row.get("filled_qty") or row.get("qty")) <= 0:
            continue
        out.append(row)
    return out


def _ticker_key(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


def _match_fill(run: dict[str, Any], fills: list[dict[str, Any]]) -> dict[str, Any] | None:
    market = str(run.get("market") or "").upper()
    ticker = _ticker_key(market, run.get("ticker"))
    plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
    execution_id = str(plan.get("exit_execution_id") or "").strip()
    if execution_id:
        for fill in fills:
            if str(fill.get("order_no") or "").strip() == execution_id:
                return fill
    matches = [fill for fill in fills if _ticker_key(market, fill.get("ticker")) == ticker]
    return matches[0] if len(matches) == 1 else None


def _proposal(run: dict[str, Any], fill: dict[str, Any]) -> dict[str, Any]:
    plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
    exit_price = _num(fill.get("avg_price") or fill.get("fill_price") or fill.get("price"))
    entry_price = _num(plan.get("actual_entry_price") or plan.get("entry_price") or plan.get("buy_zone_high"))
    pnl_pct = ((exit_price / entry_price) - 1.0) * 100.0 if entry_price > 0 and exit_price > 0 else 0.0
    qty = _qty(fill.get("filled_qty") or fill.get("qty") or plan.get("exit_qty"))
    close_reason = str(plan.get("pending_close_reason") or plan.get("close_reason") or "CLOSED_CLAUDE_PRICE_PRE_CLOSE")
    return {
        "path_run_id": run.get("path_run_id", ""),
        "decision_id": run.get("decision_id", ""),
        "market": run.get("market", ""),
        "runtime_mode": run.get("runtime_mode", ""),
        "session_date": run.get("session_date", ""),
        "ticker": run.get("ticker", ""),
        "current_status": run.get("status", ""),
        "matched_order_no": str(fill.get("order_no") or ""),
        "matched_fill_qty": qty,
        "fill_price": exit_price,
        "entry_price": entry_price,
        "proposed_close_reason": close_reason,
        "proposed_pnl_pct": pnl_pct,
    }


def build_report(*, db_path: Path, snapshot_path: Path, market: str, mode: str) -> dict[str, Any]:
    store = EventStore(db_path)
    snapshot = _load_json(snapshot_path)
    fills = _sell_fills(snapshot, market)
    runs: list[dict[str, Any]] = []
    for status in sorted(PENDING_STATUSES):
        runs.extend(
            store.path_runs_for_session(
                market=str(market or "").upper(),
                runtime_mode=mode,
                status=status,
                path_type="claude_price",
            )
        )
    proposals = []
    unmatched = []
    for run in runs:
        fill = _match_fill(run, fills)
        if fill:
            proposals.append(_proposal(run, fill))
        else:
            unmatched.append(
                {
                    "path_run_id": run.get("path_run_id", ""),
                    "session_date": run.get("session_date", ""),
                    "ticker": run.get("ticker", ""),
                    "status": run.get("status", ""),
                }
            )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": True,
        "db_path": str(db_path),
        "snapshot_path": str(snapshot_path),
        "market": str(market or "").upper(),
        "mode": mode,
        "pending_runs": len(runs),
        "matched": len(proposals),
        "unmatched": unmatched,
        "proposals": proposals,
    }


def apply_report(report: dict[str, Any], *, db_path: Path) -> dict[str, Any]:
    store = EventStore(db_path)
    applied = []
    errors = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for item in report.get("proposals", []) or []:
        path_run_id = str(item.get("path_run_id") or "")
        if not path_run_id:
            continue
        try:
            store.update_path_run(
                path_run_id,
                status="CLOSED",
                plan={
                    "actual_exit_price": float(item.get("fill_price") or 0),
                    "pnl_pct": float(item.get("proposed_pnl_pct") or 0),
                    "close_reason": str(item.get("proposed_close_reason") or ""),
                    "exit_execution_id": str(item.get("matched_order_no") or ""),
                    "exit_fill_qty": int(item.get("matched_fill_qty") or 0),
                    "exit_fill_confirmed": True,
                    "sell_pending_resolution": "broker_sell_fill_backfilled",
                    "backfilled_from_broker_truth": True,
                    "backfilled_at": now,
                },
                merge_plan=True,
            )
            store.append(
                LifecycleEvent(
                    event_type="CLOSED",
                    market=str(item.get("market") or ""),
                    runtime_mode=str(item.get("runtime_mode") or "live"),
                    session_date=str(item.get("session_date") or ""),
                    ticker=str(item.get("ticker") or ""),
                    decision_id=str(item.get("decision_id") or ""),
                    execution_id=str(item.get("matched_order_no") or ""),
                    prompt_version="pathb_sell_reconcile_backfill",
                    brain_snapshot_id="backfill",
                    reason_code=str(item.get("proposed_close_reason") or ""),
                    payload={
                        "path_type": "claude_price",
                        "path_run_id": path_run_id,
                        "parent_decision_id": str(item.get("decision_id") or ""),
                        "backfill": True,
                        "broker_fill_confirmed": True,
                        "broker_filled_qty": int(item.get("matched_fill_qty") or 0),
                        "price": float(item.get("fill_price") or 0),
                        "pnl_pct": float(item.get("proposed_pnl_pct") or 0),
                        "close_reason": str(item.get("proposed_close_reason") or ""),
                    },
                )
            )
            applied.append(path_run_id)
        except Exception as exc:
            errors.append({"path_run_id": path_run_id, "error": str(exc)})
    return {"applied": applied, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run or apply PathB pending sell backfill from broker truth snapshot.")
    parser.add_argument("--db", default=str(ROOT / "data" / "v2_event_store.db"))
    parser.add_argument("--snapshot", default=str(ROOT / "state" / "live_broker_truth_snapshot.json"))
    parser.add_argument("--market", default="US")
    parser.add_argument("--mode", default="live")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = build_report(
        db_path=Path(args.db),
        snapshot_path=Path(args.snapshot),
        market=args.market,
        mode=args.mode,
    )
    if args.apply:
        report["dry_run"] = False
        report["apply_result"] = apply_report(report, db_path=Path(args.db))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
