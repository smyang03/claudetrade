from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.candidate_counterfactual_store import CandidateCounterfactualStore, merge_metadata_for_outcome
from bot.session_date import KST, resolve_session_date_str
from runtime_paths import get_runtime_path


DONE_STATUSES = {"OUTCOME_FILLED", "CLOSE_OUTCOME_FILLED", "OUTCOME_PARTIAL"}
DEFAULT_TARGET_STATUSES = {"TRIGGERED", "PENDING"}
LEGACY_REPAIR_REASONS = {"minute_data_not_available", "daily_close_not_calculated"}
RETRYABLE_PRICE_STATUSES = {"PRICE_PENDING", "PRICE_UNAVAILABLE"}
RETRYABLE_CLOSE_STATUSES = {"OUTCOME_PARTIAL"}
RETRYABLE_OUTCOME_STATUSES = RETRYABLE_PRICE_STATUSES | RETRYABLE_CLOSE_STATUSES
PRICE_FILE_TRANSIENT_REASONS = {"price_file_missing", "price_file_empty"}
ENTRY_PRICE_TRANSIENT_REASONS = {
    "minute_file_missing",
    "minute_file_empty",
    "minute_entry_sample_missing",
    "minute_entry_sample_missing_same_session",
    "minute_entry_sample_out_of_session",
    "minute_entry_sample_too_late",
    "minute_file_read_error",
}
US_DAILY_BAR_GRACE_END_KST = dt_time(7, 0)
MAX_ENTRY_SAMPLE_LATENCY = timedelta(minutes=5)
MAX_OUTCOME_SAMPLE_LATENCY = timedelta(minutes=5)
MINUTE_OUTCOME_FIELDS = (
    "outcome_30m_pct",
    "outcome_60m_pct",
    "max_runup_60m_pct",
    "max_drawdown_60m_pct",
)


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


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _compare_dt(value: datetime, reference: datetime) -> datetime:
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)
    if value.tzinfo is not None and reference.tzinfo is None:
        return value.replace(tzinfo=None)
    return value


def _kst_dt(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(KST)
    return value.replace(tzinfo=KST)


def _market_key(market: str) -> str:
    key = str(market or "").upper()
    return key if key in {"KR", "US"} else ""


def _target_session_date(row: dict[str, Any], market: str, trigger_at: datetime) -> str:
    market_key = _market_key(market)
    if not market_key:
        return ""
    date_text = str(row.get("session_date") or "")[:10]
    return date_text or resolve_session_date_str(market_key, _kst_dt(trigger_at))


def _sample_session_date(market: str, sample_dt: datetime) -> str:
    key = _market_key(market)
    if not key:
        return ""
    return resolve_session_date_str(key, _kst_dt(sample_dt))


def _same_session_samples(
    samples: list[dict[str, Any]],
    *,
    market: str,
    trigger_at: datetime,
    target_session: str,
) -> list[dict[str, Any]]:
    market_key = _market_key(market)
    if not market_key or not str(target_session or "")[:10]:
        return []
    normalized: list[dict[str, Any]] = []
    for sample in samples:
        dt = _compare_dt(sample["dt"], trigger_at)
        if _sample_session_date(market_key, dt) != target_session:
            continue
        normalized.append({**sample, "dt": dt})
    return normalized


def _pct(entry_price: float, price: float) -> float | None:
    if entry_price <= 0 or price <= 0:
        return None
    return ((float(price) - float(entry_price)) / float(entry_price)) * 100.0


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
            "price_source",
            "price_sample_count",
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


def _minute_csv_candidates(minute_root: str | Path, market: str, ticker: str) -> list[Path]:
    root = Path(minute_root)
    market_key = str(market or "").upper()
    ticker_text = str(ticker or "").strip()
    ticker_key = ticker_text.upper() if market_key == "US" else ticker_text.zfill(6)
    market_dir = market_key.lower()
    return [
        root / "minute" / market_dir / f"{market_dir}_{ticker_key}.csv",
        root / "minute" / market_dir / f"{ticker_key}.csv",
        root / "intraday" / market_dir / f"{market_dir}_{ticker_key}.csv",
        root / "intraday" / market_dir / f"{ticker_key}.csv",
        root / market_dir / f"{market_dir}_{ticker_key}_minute.csv",
        root / market_dir / f"{ticker_key}_minute.csv",
    ]


def _load_minute_samples(minute_root: str | Path, market: str, ticker: str) -> tuple[list[dict[str, Any]], str, str]:
    path = next((item for item in _minute_csv_candidates(minute_root, market, ticker) if item.exists()), None)
    if path is None:
        return [], "", "minute_file_missing"
    samples: list[dict[str, Any]] = []
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                raw_ts = (
                    row.get("ts")
                    or row.get("datetime")
                    or row.get("sampled_at")
                    or row.get("time")
                    or row.get("Date")
                    or row.get("date")
                )
                dt = _parse_dt(raw_ts)
                if dt is None:
                    continue
                raw_close = row.get("close") if row.get("close") is not None else row.get("Close")
                raw_high = row.get("high") if row.get("high") is not None else row.get("High")
                raw_low = row.get("low") if row.get("low") is not None else row.get("Low")
                close = float(raw_close)
                high = float(raw_high if raw_high not in (None, "") else close)
                low = float(raw_low if raw_low not in (None, "") else close)
                samples.append({"dt": dt, "close": close, "high": high, "low": low})
    except Exception:
        return [], str(path), "minute_file_read_error"
    samples.sort(key=lambda item: item["dt"])
    return samples, str(path), "" if samples else "minute_file_empty"


def _minute_outcomes(
    row: dict[str, Any],
    *,
    minute_root: str | Path,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    trigger_at = _parse_dt(row.get("trigger_time") or row.get("known_at") or row.get("signal_time"))
    entry_price = float(row.get("entry_price") or 0.0)
    market = str(row.get("market") or "")
    samples, source, reason = _load_minute_samples(minute_root, market, str(row.get("ticker") or ""))
    metadata: dict[str, Any] = {
        "minute_price_source": source,
        "minute_sample_count": len(samples),
    }
    missing: list[str] = []
    updates: dict[str, Any] = {}
    if trigger_at is None:
        return updates, {**metadata, "minute_reason": "trigger_time_missing"}, ["trigger_time"]
    if entry_price <= 0:
        return updates, {**metadata, "minute_reason": "entry_price_missing"}, ["entry_price"]
    if not samples:
        return updates, {**metadata, "minute_reason": reason or "minute_file_missing"}, ["minute_samples"]

    target_session = _target_session_date(row, market, trigger_at)
    normalized = _same_session_samples(samples, market=market, trigger_at=trigger_at, target_session=target_session)
    metadata["minute_session_date"] = target_session
    metadata["minute_same_session_sample_count"] = len(normalized)
    if not normalized:
        return (
            updates,
            {
                **metadata,
                "minute_reason": "minute_samples_missing_same_session",
                "outcome_30m_reason": "minute_outcome_sample_missing_same_session",
                "outcome_60m_reason": "minute_outcome_sample_missing_same_session",
            },
            list(MINUTE_OUTCOME_FIELDS),
        )
    for horizon in (30, 60):
        target = trigger_at + timedelta(minutes=horizon)
        observed = next((sample for sample in normalized if sample["dt"] >= target), None)
        if observed is None:
            missing.append(f"outcome_{horizon}m_pct")
            metadata[f"outcome_{horizon}m_reason"] = "minute_outcome_sample_missing_same_session"
            continue
        lag = observed["dt"] - target
        if lag > MAX_OUTCOME_SAMPLE_LATENCY:
            missing.append(f"outcome_{horizon}m_pct")
            metadata[f"outcome_{horizon}m_reason"] = "minute_outcome_sample_too_late"
            metadata[f"outcome_{horizon}m_rejected_observed_at"] = observed["dt"].isoformat()
            metadata[f"outcome_{horizon}m_sample_lag_seconds"] = int(lag.total_seconds())
            continue
        updates[f"outcome_{horizon}m_pct"] = _pct(entry_price, float(observed["close"]))
        metadata[f"outcome_{horizon}m_observed_at"] = observed["dt"].isoformat()
        metadata[f"outcome_{horizon}m_price_source"] = source

    target_60 = trigger_at + timedelta(minutes=60)
    window_60 = [sample for sample in normalized if trigger_at <= sample["dt"] <= target_60]
    if window_60:
        high = max(float(sample["high"]) for sample in window_60)
        low = min(float(sample["low"]) for sample in window_60)
        updates["max_runup_60m_pct"] = _pct(entry_price, high)
        updates["max_drawdown_60m_pct"] = _pct(entry_price, low)
        metadata["mfe_mae_sample_count"] = len(window_60)
        metadata["mfe_mae_price_source"] = source
    else:
        missing.extend(["max_runup_60m_pct", "max_drawdown_60m_pct"])
    return updates, metadata, missing


def _infer_entry_price_from_minute(
    row: dict[str, Any],
    *,
    minute_root: str | Path,
) -> tuple[float | None, dict[str, Any], list[str]]:
    trigger_at = _parse_dt(row.get("trigger_time") or row.get("known_at") or row.get("signal_time"))
    market = str(row.get("market") or "")
    samples, source, reason = _load_minute_samples(minute_root, market, str(row.get("ticker") or ""))
    metadata = {
        "entry_price_source": "minute_csv_trigger",
        "entry_price_minute_source": source,
        "entry_price_minute_sample_count": len(samples),
    }
    if trigger_at is None:
        return None, {**metadata, "entry_price_reason": "trigger_time_missing"}, ["trigger_time"]
    if not samples:
        return None, {**metadata, "entry_price_reason": reason or "minute_file_missing"}, ["entry_price"]
    target_session = _target_session_date(row, market, trigger_at)
    all_normalized = [{**sample, "dt": _compare_dt(sample["dt"], trigger_at)} for sample in samples]
    normalized = _same_session_samples(samples, market=market, trigger_at=trigger_at, target_session=target_session)
    metadata["entry_price_session_date"] = target_session
    metadata["entry_price_session_filter"] = "same_session"
    metadata["entry_price_same_session_sample_count"] = len(normalized)
    if not normalized:
        rejected = next((sample for sample in all_normalized if sample["dt"] >= trigger_at), None)
        if rejected is not None:
            observed_session = _sample_session_date(market, rejected["dt"])
            if target_session and observed_session and observed_session != target_session:
                return (
                    None,
                    {
                        **metadata,
                        "entry_price_reason": "minute_entry_sample_out_of_session",
                        "entry_price_rejected_observed_at": rejected["dt"].isoformat(),
                        "entry_price_trigger_session": target_session,
                        "entry_price_observed_session": observed_session,
                    },
                    ["entry_price"],
                )
        return None, {**metadata, "entry_price_reason": "minute_entry_sample_missing_same_session"}, ["entry_price"]
    observed = next((sample for sample in normalized if sample["dt"] >= trigger_at), None)
    if observed is None:
        return None, {**metadata, "entry_price_reason": "minute_entry_sample_missing_same_session"}, ["entry_price"]
    observed_at = observed["dt"].isoformat()
    lag = observed["dt"] - trigger_at
    if lag > MAX_ENTRY_SAMPLE_LATENCY:
        return (
            None,
            {
                **metadata,
                "entry_price_reason": "minute_entry_sample_too_late",
                "entry_price_rejected_observed_at": observed_at,
                "entry_price_sample_lag_seconds": int(lag.total_seconds()),
            },
            ["entry_price"],
        )
    return float(observed["close"]), {**metadata, "entry_price_observed_at": observed_at}, []


def _kst_now(now_dt: datetime | None = None) -> datetime:
    if now_dt is None:
        return datetime.now(KST)
    if now_dt.tzinfo is not None:
        return now_dt.astimezone(KST)
    return now_dt


def _is_current_or_future_session(*, session_date: str, market: str, now_dt: datetime | None = None) -> bool:
    date_text = str(session_date or "")[:10]
    if not date_text:
        return False
    return date_text >= resolve_session_date_str(market, _kst_now(now_dt))


def _is_recent_us_close_waiting_for_daily_bar(*, session_date: str, market: str, now_dt: datetime | None = None) -> bool:
    if str(market or "").upper() != "US":
        return False
    now_kst = _kst_now(now_dt)
    if now_kst.time() >= US_DAILY_BAR_GRACE_END_KST:
        return False
    return str(session_date or "")[:10] == (now_kst.date() - timedelta(days=1)).isoformat()


def _should_mark_price_pending(
    *,
    session_date: str,
    market: str,
    reason: str,
    now_dt: datetime | None = None,
) -> bool:
    if reason not in PRICE_FILE_TRANSIENT_REASONS:
        return False
    return _is_current_or_future_session(
        session_date=session_date,
        market=market,
        now_dt=now_dt,
    ) or _is_recent_us_close_waiting_for_daily_bar(
        session_date=session_date,
        market=market,
        now_dt=now_dt,
    )


def _minute_outcome_complete(row: dict[str, Any]) -> bool:
    return all(row.get(field) is not None for field in MINUTE_OUTCOME_FIELDS)


def _new_minute_updates(row: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    return {
        field: value
        for field, value in updates.items()
        if field in MINUTE_OUTCOME_FIELDS and value is not None and row.get(field) is None
    }


def _remaining_missing_minute_fields(row: dict[str, Any], missing_fields: list[str]) -> list[str]:
    return sorted({field for field in missing_fields if row.get(field) is None})


def _has_outcome_value(row: dict[str, Any], updates: dict[str, Any], field: str) -> bool:
    return row.get(field) is not None or updates.get(field) is not None


def _needs_minute_backfill_after_close(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "")
    return (
        row.get("outcome_close_pct") is not None
        and status in {"CLOSE_OUTCOME_FILLED", "OUTCOME_FILLED"}
        and row.get("entry_price") is not None
        and row.get("trigger_time") is not None
        and not _minute_outcome_complete(row)
    )


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
        if row.get("outcome_close_pct") is not None:
            if _needs_minute_backfill_after_close(row):
                targets.append(row)
            else:
                skipped += 1
            continue
        if retry_missing:
            if (
                status in RETRYABLE_OUTCOME_STATUSES
                and row.get("trigger_time") is not None
                and row.get("outcome_close_pct") is None
            ):
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


def _mark_minute_only_outcome(
    store: CandidateCounterfactualStore,
    row: dict[str, Any],
    *,
    minute_updates: dict[str, Any],
    minute_metadata: dict[str, Any],
    minute_missing_fields: list[str],
) -> None:
    existing_metadata = _metadata_dict(row)
    label_horizons = sorted(
        set(
            _metadata_list(existing_metadata.get("label_horizons"))
            + (["30m"] if _has_outcome_value(row, minute_updates, "outcome_30m_pct") else [])
            + (["60m"] if _has_outcome_value(row, minute_updates, "outcome_60m_pct") else [])
        )
    )
    existing_price_source = existing_metadata.get("price_source")
    price_source = dict(existing_price_source) if isinstance(existing_price_source, dict) else {"close": existing_price_source or ""}
    price_source["minute"] = minute_metadata.get("minute_price_source", "")
    existing_sample_count = existing_metadata.get("price_sample_count")
    price_sample_count = dict(existing_sample_count) if isinstance(existing_sample_count, dict) else {}
    price_sample_count["minute"] = int(minute_metadata.get("minute_sample_count") or 0)
    store.mark_outcome(
        int(row["id"]),
        **minute_updates,
        metadata_quality="backfill_diagnostic",
        label_source=str(row.get("label_source") or "") or "virtual_immediate_shadow",
        metadata_json=_metadata(
            row,
            label_horizons=label_horizons,
            source_attempts=sorted(set(_metadata_list(existing_metadata.get("source_attempts")) + ["minute_csv"])),
            outcome_update_source="tools/update_counterfactual_outcomes.py",
            final_attempt_at=datetime.now().isoformat(timespec="seconds"),
            price_source=price_source,
            price_sample_count=price_sample_count,
            missing_fields=_remaining_missing_minute_fields(row, minute_missing_fields),
            **minute_metadata,
        ),
    )


def _mark_unavailable(
    store: CandidateCounterfactualStore,
    row: dict[str, Any],
    *,
    status: str,
    reason: str,
    missing_fields: list[str] | None = None,
    metadata_updates: dict[str, Any] | None = None,
) -> None:
    extra = dict(metadata_updates or {})
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
            **extra,
        ),
    )


def _mark_partial_minute_outcome(
    store: CandidateCounterfactualStore,
    row: dict[str, Any],
    *,
    minute_updates: dict[str, Any],
    minute_metadata: dict[str, Any],
    minute_missing_fields: list[str],
    close_reason: str,
) -> None:
    existing_metadata = _metadata_dict(row)
    label_horizons = sorted(
        set(
            _metadata_list(existing_metadata.get("label_horizons"))
            + (["30m"] if _has_outcome_value(row, minute_updates, "outcome_30m_pct") else [])
            + (["60m"] if _has_outcome_value(row, minute_updates, "outcome_60m_pct") else [])
        )
    )
    missing_fields = sorted(set(["outcome_close_pct", *_remaining_missing_minute_fields(row, minute_missing_fields)]))
    store.mark_outcome(
        int(row["id"]),
        **minute_updates,
        status="OUTCOME_PARTIAL",
        metadata_quality="backfill_diagnostic",
        label_source="counterfactual_outcome_updater",
        metadata_json=_metadata(
            row,
            label_source="counterfactual_outcome_updater",
            is_virtual_pnl=True,
            label_horizons=label_horizons,
            source_attempts=sorted(set(_metadata_list(existing_metadata.get("source_attempts")) + ["minute_csv", "daily_close"])),
            outcome_update_source="tools/update_counterfactual_outcomes.py",
            final_attempt_at=datetime.now().isoformat(timespec="seconds"),
            price_source={"minute": minute_metadata.get("minute_price_source", ""), "close": ""},
            price_sample_count={"minute": int(minute_metadata.get("minute_sample_count") or 0), "daily_close": 0},
            missing_fields=missing_fields,
            reason=close_reason,
            **minute_metadata,
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
    minute_root: str | Path | None = None,
    _now: datetime | None = None,
) -> dict[str, Any]:
    store = CandidateCounterfactualStore(db_path or get_runtime_path("data", "audit", "candidate_audit.db"))
    rows = store.fetch_rows(session_date=session_date, market=market)
    targets, skipped = _target_rows(
        rows,
        retry_missing=retry_missing,
        repair_legacy_data_missing=repair_legacy_data_missing,
    )
    root = Path(price_root) if price_root is not None else get_runtime_path("data", "price")
    minute = Path(minute_root) if minute_root is not None else root
    filled = 0
    partial = 0
    data_missing = 0
    price_pending = 0
    price_unavailable = 0
    minute_filled = 0
    minute_missing = 0
    for row in targets:
        inferred_entry_updates: dict[str, Any] = {}
        inferred_entry_metadata: dict[str, Any] = {}
        inferred_entry_missing: list[str] = []
        if row.get("entry_price") is None and row.get("trigger_time") is not None:
            inferred_entry, inferred_entry_metadata, inferred_entry_missing = _infer_entry_price_from_minute(
                row,
                minute_root=minute,
            )
            if inferred_entry is not None and inferred_entry > 0:
                row["entry_price"] = inferred_entry
                row["trigger_price"] = row.get("trigger_price") or inferred_entry
                inferred_entry_updates = {
                    "entry_price": inferred_entry,
                    "trigger_price": row.get("trigger_price") or inferred_entry,
                }

        if row.get("entry_price") is None or row.get("trigger_time") is None:
            entry_reason = str(inferred_entry_metadata.get("entry_price_reason") or "")
            if (
                row.get("entry_price") is None
                and row.get("trigger_time") is not None
                and entry_reason in ENTRY_PRICE_TRANSIENT_REASONS
            ):
                date_text = str(row.get("session_date") or "")[:10]
                status = (
                    "PRICE_PENDING"
                    if _is_current_or_future_session(
                        session_date=date_text,
                        market=str(row.get("market") or ""),
                        now_dt=_now,
                    )
                    or _is_recent_us_close_waiting_for_daily_bar(
                        session_date=date_text,
                        market=str(row.get("market") or ""),
                        now_dt=_now,
                    )
                    else "PRICE_UNAVAILABLE"
                )
                _mark_unavailable(
                    store,
                    row,
                    status=status,
                    reason=entry_reason,
                    missing_fields=sorted(set(["entry_price"] + inferred_entry_missing)),
                    metadata_updates=inferred_entry_metadata,
                )
                if status == "PRICE_PENDING":
                    price_pending += 1
                else:
                    price_unavailable += 1
                continue
            _mark_unavailable(
                store,
                row,
                status="DATA_MISSING",
                reason="entry_or_trigger_missing",
                missing_fields=sorted(set(["entry_price", "trigger_time"] + inferred_entry_missing)),
                metadata_updates=inferred_entry_metadata,
            )
            data_missing += 1
            continue

        minute_updates, minute_metadata, minute_missing_fields = _minute_outcomes(row, minute_root=minute)
        minute_updates_to_write = _new_minute_updates(row, minute_updates)
        minute_missing_fields = _remaining_missing_minute_fields(row, minute_missing_fields)
        minute_filled_fields = [key for key, value in minute_updates_to_write.items() if value is not None]
        if minute_filled_fields:
            minute_filled += 1
        if minute_missing_fields:
            minute_missing += 1

        if row.get("outcome_close_pct") is not None:
            if minute_updates_to_write or inferred_entry_updates:
                _mark_minute_only_outcome(
                    store,
                    row,
                    minute_updates={**inferred_entry_updates, **minute_updates_to_write},
                    minute_metadata={**inferred_entry_metadata, **minute_metadata},
                    minute_missing_fields=minute_missing_fields,
                )
            continue

        date_text = str(row.get("session_date") or "")[:10]
        closes, load_reason = _load_daily_closes(root, str(row.get("market") or ""), str(row.get("ticker") or ""))
        if not closes:
            reason = load_reason or "price_file_empty"
            if minute_updates_to_write:
                _mark_partial_minute_outcome(
                    store,
                    row,
                    minute_updates={**inferred_entry_updates, **minute_updates_to_write},
                    minute_metadata={**inferred_entry_metadata, **minute_metadata},
                    minute_missing_fields=minute_missing_fields,
                    close_reason=reason,
                )
                partial += 1
                continue
            if str(row.get("status") or "") == "OUTCOME_PARTIAL":
                continue
            if _should_mark_price_pending(
                session_date=date_text,
                market=str(row.get("market") or ""),
                reason=reason,
                now_dt=_now,
            ):
                _mark_unavailable(store, row, status="PRICE_PENDING", reason=reason)
                price_pending += 1
            else:
                _mark_unavailable(store, row, status="PRICE_UNAVAILABLE", reason=reason)
                price_unavailable += 1
            continue

        close_price = closes.get(date_text)
        if close_price is None:
            max_date = max(closes)
            if minute_updates_to_write:
                _mark_partial_minute_outcome(
                    store,
                    row,
                    minute_updates={**inferred_entry_updates, **minute_updates_to_write},
                    minute_metadata={**inferred_entry_metadata, **minute_metadata},
                    minute_missing_fields=minute_missing_fields,
                    close_reason="daily_close_not_available_yet" if date_text and date_text > max_date else "daily_close_missing_for_date",
                )
                partial += 1
                continue
            if str(row.get("status") or "") == "OUTCOME_PARTIAL":
                continue
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
        label_horizons = sorted(
            set(
                _metadata_list(existing_metadata.get("label_horizons"))
                + ["close"]
                + (["30m"] if _has_outcome_value(row, minute_updates_to_write, "outcome_30m_pct") else [])
                + (["60m"] if _has_outcome_value(row, minute_updates_to_write, "outcome_60m_pct") else [])
            )
        )
        source_attempts = sorted(
            set(_metadata_list(existing_metadata.get("source_attempts")) + ["daily_close", "minute_csv"])
        )
        missing_fields = sorted(set(minute_missing_fields))
        metadata_json = _metadata(
            row,
            label_source="virtual_immediate_shadow",
            entry_price_source=inferred_entry_metadata.get(
                "entry_price_source",
                existing_metadata.get("entry_price_source", "context_current_price"),
            ),
            is_virtual_pnl=True,
            label_horizons=label_horizons,
            source_attempts=source_attempts,
            final_attempt_at=datetime.now().isoformat(timespec="seconds"),
            outcome_source="daily_close_csv",
            outcome_update_source="tools/update_counterfactual_outcomes.py",
            price_source={"close": "daily_close_csv", "minute": minute_metadata.get("minute_price_source", "")},
            price_sample_count={
                "daily_close": len(closes),
                "minute": int(minute_metadata.get("minute_sample_count") or 0),
            },
            missing_fields=missing_fields,
            reason="daily_close_labeled",
            **{k: v for k, v in inferred_entry_metadata.items() if k != "entry_price_source"},
            **minute_metadata,
        )
        updates = {
            **inferred_entry_updates,
            **minute_updates_to_write,
            "outcome_close_pct": outcome,
            "status": "CLOSE_OUTCOME_FILLED",
            "metadata_quality": "backfill_diagnostic",
            "label_source": "virtual_immediate_shadow",
            "metadata_json": metadata_json,
        }
        store.mark_outcome(
            int(row["id"]),
            **updates,
        )
        filled += 1
    return {
        "ok": True,
        "rows": len(rows),
        "targeted": len(targets),
        "filled": filled,
        "partial": partial,
        "minute_filled": minute_filled,
        "minute_missing": minute_missing,
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
    parser.add_argument("--minute-root", default="", help="minute CSV root; defaults to --price-root")
    args = parser.parse_args(argv)
    payload = update_counterfactual_outcomes(
        db_path=args.db_path,
        session_date=args.date,
        market=args.market,
        retry_missing=args.retry_missing,
        repair_legacy_data_missing=args.repair_legacy_data_missing,
        price_root=args.price_root,
        minute_root=args.minute_root or args.price_root,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
