from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.candidate_counterfactual_store import CandidateCounterfactualStore, merge_metadata_for_outcome
from runtime_paths import get_runtime_path


DONE_STATUSES = {"OUTCOME_FILLED", "CLOSE_OUTCOME_FILLED", "OUTCOME_PARTIAL"}
DEFAULT_TARGET_STATUSES = {"TRIGGERED", "PENDING"}
LEGACY_REPAIR_REASONS = {"minute_data_not_available", "daily_close_not_calculated"}


def _metadata_dict(row: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(row.get("metadata_json") or "{}")
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _metadata_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _lookup_ticker_value(data_by_ticker: dict[str, Any], *, market: str, ticker: str) -> Any:
    market_key = str(market or "").upper()
    raw_ticker = str(ticker or "").strip()
    ticker_key = raw_ticker.upper() if market_key == "US" else raw_ticker
    value = data_by_ticker.get(ticker_key)
    if value is None and market_key == "US":
        value = data_by_ticker.get(raw_ticker)
    return value


def _metadata(row: dict[str, Any], **updates: Any) -> str:
    return merge_metadata_for_outcome(
        row.get("metadata_json") or "{}",
        updates,
        force_keys={
            "label_source",
            "is_virtual_pnl",
            "outcome_update_source",
            "final_attempt_at",
            "missing_fields",
            "reason",
        },
    )


def _price_csv_path(price_root: str | Path, market: str, ticker: str) -> Path:
    root = Path(price_root)
    market_key = str(market or "").upper()
    ticker_text = str(ticker or "").strip()
    if market_key == "US":
        ticker_key = ticker_text.upper()
        return root / "us" / f"us_{ticker_key}.csv"
    ticker_key = ticker_text.zfill(6)
    return root / "kr" / f"kr_{ticker_key}.csv"


def _load_daily_closes(price_root: str | Path, market: str, ticker: str) -> tuple[dict[str, float], str]:
    path = _price_csv_path(price_root, market, ticker)
    if not path.exists():
        return {}, "price_file_missing"
    closes: dict[str, float] = {}
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                date_text = str(row.get("date") or row.get("Date") or "")[:10]
                if not date_text:
                    continue
                raw_close = row.get("close")
                if raw_close is None:
                    raw_close = row.get("Close")
                closes[date_text] = float(raw_close)
    except Exception:
        return {}, "price_file_read_error"
    return closes, ""


def _is_today_or_future_session(session_date: str) -> bool:
    date_text = str(session_date or "")[:10]
    return bool(date_text) and date_text >= date.today().isoformat()


def _should_mark_price_pending(*, session_date: str, reason: str) -> bool:
    return _is_today_or_future_session(session_date) and reason in {"price_file_missing", "price_file_empty"}


def _target_rows(
    rows: list[dict[str, Any]],
    *,
    retry_missing: bool,
    repair_legacy_data_missing: bool,
) -> tuple[list[dict[str, Any]], int]:
    targets: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        status = str(row.get("status") or "")
        if str(row.get("path_name") or "") != "immediate":
            skipped += 1
            continue
        if retry_missing:
            if status == "PRICE_PENDING" and row.get("entry_price") is not None and row.get("trigger_time") is not None:
                targets.append(row)
            else:
                skipped += 1
            continue
        if repair_legacy_data_missing:
            metadata = _metadata_dict(row)
            reason = str(metadata.get("reason") or "")
            if (
                status == "DATA_MISSING"
                and row.get("entry_price") is not None
                and row.get("trigger_time") is not None
                and reason in LEGACY_REPAIR_REASONS
            ):
                targets.append(row)
            else:
                skipped += 1
            continue
        if status in DEFAULT_TARGET_STATUSES:
            targets.append(row)
        else:
            skipped += 1
    return targets, skipped


def _mark_unavailable(
    store: CandidateCounterfactualStore,
    row: dict[str, Any],
    *,
    status: str,
    reason: str,
    missing_fields: list[str] | None = None,
) -> None:
    store.mark_outcome(
        int(row["id"]),
        status=status,
        metadata_quality="backfill_diagnostic",
        label_source="counterfactual_outcome_updater",
        metadata_json=_metadata(
            row,
            label_source="counterfactual_outcome_updater",
            missing_fields=missing_fields or ["outcome_close_pct"],
            source_attempts=["daily_close"],
            outcome_update_source="tools/update_counterfactual_outcomes.py",
            final_attempt_at=datetime.now().isoformat(timespec="seconds"),
            reason=reason,
        ),
    )


def update_counterfactual_outcomes(
    *,
    db_path: str | Path | None = None,
    session_date: str = "",
    market: str = "",
    retry_missing: bool = False,
    repair_legacy_data_missing: bool = False,
    price_root: str | Path | None = None,
) -> dict[str, Any]:
    store = CandidateCounterfactualStore(db_path or get_runtime_path("data", "audit", "candidate_audit.db"))
    rows = store.fetch_rows(session_date=session_date, market=market)
    targets, skipped = _target_rows(
        rows,
        retry_missing=retry_missing,
        repair_legacy_data_missing=repair_legacy_data_missing,
    )
    root = Path(price_root) if price_root is not None else get_runtime_path("data", "price")
    filled = 0
    data_missing = 0
    price_pending = 0
    price_unavailable = 0
    for row in targets:
        if row.get("entry_price") is None or row.get("trigger_time") is None:
            _mark_unavailable(
                store,
                row,
                status="DATA_MISSING",
                reason="entry_or_trigger_missing",
                missing_fields=["entry_price", "trigger_time"],
            )
            data_missing += 1
            continue

        date_text = str(row.get("session_date") or "")[:10]
        closes, load_reason = _load_daily_closes(root, str(row.get("market") or ""), str(row.get("ticker") or ""))
        if not closes:
            reason = load_reason or "price_file_empty"
            if _should_mark_price_pending(session_date=date_text, reason=reason):
                _mark_unavailable(store, row, status="PRICE_PENDING", reason=reason)
                price_pending += 1
            else:
                _mark_unavailable(store, row, status="PRICE_UNAVAILABLE", reason=reason)
                price_unavailable += 1
            continue

        close_price = closes.get(date_text)
        if close_price is None:
            max_date = max(closes)
            if date_text and date_text > max_date:
                _mark_unavailable(store, row, status="PRICE_PENDING", reason="daily_close_not_available_yet")
                price_pending += 1
            else:
                _mark_unavailable(store, row, status="PRICE_UNAVAILABLE", reason="daily_close_missing_for_date")
                price_unavailable += 1
            continue

        entry_price = float(row.get("entry_price") or 0.0)
        if entry_price == 0.0:
            _mark_unavailable(store, row, status="DATA_MISSING", reason="entry_price_zero", missing_fields=["entry_price"])
            data_missing += 1
            continue

        outcome = ((float(close_price) - entry_price) / entry_price) * 100.0
        existing_metadata = _metadata_dict(row)
        label_horizons = sorted(set(_metadata_list(existing_metadata.get("label_horizons")) + ["close"]))
        source_attempts = sorted(set(_metadata_list(existing_metadata.get("source_attempts")) + ["daily_close"]))
        metadata_json = _metadata(
            row,
            label_source="virtual_immediate_shadow",
            entry_price_source=existing_metadata.get("entry_price_source", "context_current_price"),
            is_virtual_pnl=True,
            label_horizons=label_horizons,
            source_attempts=source_attempts,
            final_attempt_at=datetime.now().isoformat(timespec="seconds"),
            outcome_source="daily_close_csv",
            outcome_update_source="tools/update_counterfactual_outcomes.py",
            reason="daily_close_labeled",
        )
        store.mark_outcome(
            int(row["id"]),
            outcome_close_pct=outcome,
            status="CLOSE_OUTCOME_FILLED",
            metadata_quality="backfill_diagnostic",
            label_source="virtual_immediate_shadow",
            metadata_json=metadata_json,
        )
        filled += 1
    return {
        "ok": True,
        "rows": len(rows),
        "targeted": len(targets),
        "filled": filled,
        "data_missing": data_missing,
        "price_pending": price_pending,
        "price_unavailable": price_unavailable,
        "skipped": skipped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill counterfactual path outcomes when local price labels exist.")
    parser.add_argument("--db-path", default=str(get_runtime_path("data", "audit", "candidate_audit.db")))
    parser.add_argument("--date", default="")
    parser.add_argument("--market", default="")
    parser.add_argument("--retry-missing", action="store_true")
    parser.add_argument("--repair-legacy-data-missing", action="store_true")
    parser.add_argument("--price-root", default=str(get_runtime_path("data", "price")))
    args = parser.parse_args(argv)
    payload = update_counterfactual_outcomes(
        db_path=args.db_path,
        session_date=args.date,
        market=args.market,
        retry_missing=args.retry_missing,
        repair_legacy_data_missing=args.repair_legacy_data_missing,
        price_root=args.price_root,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
