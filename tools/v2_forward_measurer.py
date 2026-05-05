from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.v2 import DEFAULT_V2_CONFIG
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent


DEFAULT_PRICE_DIR = ROOT / "data" / "price"
DEFAULT_HORIZONS = (1, 3, 5)


def measure_forward_pending(
    store: EventStore,
    *,
    session_date: str,
    runtime_mode: str,
    markets: list[str],
    price_dir: str | Path = DEFAULT_PRICE_DIR,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    dry_run: bool = False,
) -> dict[str, Any]:
    decisions = _decisions_for_session(
        store,
        session_date=session_date,
        runtime_mode=runtime_mode,
        markets=markets,
    )
    measured: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    missing_csv: list[dict[str, Any]] = []
    pending_data: list[dict[str, Any]] = []
    price_root = Path(price_dir)

    for decision in decisions:
        decision_id = str(decision.get("decision_id") or "")
        market = str(decision.get("market") or "").upper()
        ticker = str(decision.get("ticker") or "")
        due_horizons = _due_horizons(decision, horizons)
        events = store.events_for_decision(decision_id)
        already_measured = _measured_horizons(events)
        remaining = [h for h in due_horizons if h not in already_measured]
        if not remaining:
            skipped.append({"decision_id": decision_id, "reason": "already_measured", "ticker": ticker})
            continue

        price_rows = _load_price_rows(price_root, market, ticker)
        if price_rows is None:
            missing_csv.append({"decision_id": decision_id, "market": market, "ticker": ticker})
            continue

        calc = calculate_forward_returns(price_rows, str(decision.get("session_date") or session_date), remaining)
        if not calc["measured_horizons"]:
            pending_data.append(
                {
                    "decision_id": decision_id,
                    "market": market,
                    "ticker": ticker,
                    "reason": calc.get("reason", "future_price_unavailable"),
                }
            )
            continue

        after_measured = sorted(set(already_measured) | set(calc["measured_horizons"]))
        complete = all(h in after_measured for h in due_horizons)
        payload = {
            "source": "price_csv",
            "session_date": decision.get("session_date") or session_date,
            "due_horizons": [f"{h}d" for h in due_horizons],
            "measured_horizons": [f"{h}d" for h in calc["measured_horizons"]],
            "all_measured_horizons": [f"{h}d" for h in after_measured],
            "complete": complete,
            "base_close": calc.get("base_close"),
            "future_closes": calc.get("future_closes", {}),
            "forward_returns": calc.get("forward_returns", {}),
        }
        item = {
            "decision_id": decision_id,
            "market": market,
            "ticker": ticker,
            "measured_horizons": payload["measured_horizons"],
            "complete": complete,
            "forward_returns": payload["forward_returns"],
        }
        measured.append(item)
        if not dry_run:
            store.append(
                LifecycleEvent(
                    event_type="FORWARD_MEASURED",
                    market=market,
                    runtime_mode=str(decision.get("runtime_mode") or runtime_mode),
                    session_date=str(decision.get("session_date") or session_date),
                    ticker=ticker,
                    decision_id=decision_id,
                    prompt_version=str(decision.get("prompt_version") or DEFAULT_V2_CONFIG.prompt_version),
                    brain_snapshot_id=str(decision.get("brain_snapshot_id") or "brain_pending"),
                    reason_code="FORWARD_COMPLETE" if complete else "FORWARD_PARTIAL",
                    payload=payload,
                )
            )

    return {
        "decision_count": len(decisions),
        "measured_count": len(measured),
        "skipped_count": len(skipped),
        "missing_csv_count": len(missing_csv),
        "pending_data_count": len(pending_data),
        "dry_run": dry_run,
        "measured": measured,
        "skipped": skipped,
        "missing_csv": missing_csv[:50],
        "pending_data": pending_data[:50],
    }


def calculate_forward_returns(
    price_rows: list[dict[str, Any]],
    session_date: str,
    horizons: list[int] | tuple[int, ...],
) -> dict[str, Any]:
    dates = [str(row.get("date") or "") for row in price_rows]
    if session_date not in dates:
        return {"measured_horizons": [], "reason": "session_date_not_in_price_csv"}
    base_idx = dates.index(session_date)
    base_close = _safe_float(price_rows[base_idx].get("close"))
    if base_close is None or base_close <= 0:
        return {"measured_horizons": [], "reason": "invalid_base_close"}

    measured: list[int] = []
    future_closes: dict[str, float] = {}
    forward_returns: dict[str, float] = {}
    for horizon in sorted({int(h) for h in horizons}):
        future_idx = base_idx + horizon
        if future_idx >= len(price_rows):
            continue
        future_close = _safe_float(price_rows[future_idx].get("close"))
        if future_close is None or future_close <= 0:
            continue
        key = f"{horizon}d"
        measured.append(horizon)
        future_closes[key] = round(future_close, 6)
        forward_returns[key] = round((future_close - base_close) / base_close * 100.0, 4)

    return {
        "measured_horizons": measured,
        "base_close": round(base_close, 6),
        "future_closes": future_closes,
        "forward_returns": forward_returns,
        "reason": "ok" if measured else "future_price_unavailable",
    }


def _decisions_for_session(
    store: EventStore,
    *,
    session_date: str,
    runtime_mode: str,
    markets: list[str],
) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in markets)
    sql = (
        "SELECT * FROM v2_decisions "
        f"WHERE session_date=? AND runtime_mode=? AND market IN ({placeholders}) "
        "ORDER BY created_at, decision_id"
    )
    params: list[Any] = [session_date, runtime_mode, *markets]
    with store.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    raw = data.pop("payload_json", "{}")
    try:
        data["payload"] = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data["payload"] = {}
    return data


def _due_horizons(decision: dict[str, Any], default_horizons: tuple[int, ...]) -> list[int]:
    payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
    raw = payload.get("due_horizons") if isinstance(payload, dict) else None
    if not raw:
        return sorted({int(h) for h in default_horizons})
    values: list[int] = []
    for item in raw:
        text = str(item).strip().lower().removesuffix("d")
        try:
            values.append(int(text))
        except ValueError:
            continue
    return sorted(set(values)) or sorted({int(h) for h in default_horizons})


def _measured_horizons(events: list[dict[str, Any]]) -> set[int]:
    measured: set[int] = set()
    for event in events:
        if str(event.get("event_type") or "") != "FORWARD_MEASURED":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        raw = payload.get("all_measured_horizons") or payload.get("measured_horizons") or []
        for item in raw:
            text = str(item).strip().lower().removesuffix("d")
            try:
                measured.add(int(text))
            except ValueError:
                continue
    return measured


def _load_price_rows(price_dir: Path, market: str, ticker: str) -> list[dict[str, Any]] | None:
    mkt = str(market or "").lower()
    path = price_dir / mkt / f"{mkt}_{ticker}.csv"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda row: str(row.get("date") or ""))
    return rows


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure V2 forward returns from local price CSVs.")
    parser.add_argument("--session-date", required=True)
    parser.add_argument("--runtime-mode", choices=["live", "paper"], default="live")
    parser.add_argument("--market", choices=["KR", "US", "ALL"], default="ALL")
    parser.add_argument("--price-dir", default=str(DEFAULT_PRICE_DIR))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    markets = ["KR", "US"] if args.market == "ALL" else [args.market]
    result = measure_forward_pending(
        EventStore(),
        session_date=args.session_date,
        runtime_mode=args.runtime_mode,
        markets=markets,
        price_dir=args.price_dir,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
