from __future__ import annotations

import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from runtime_paths import get_runtime_path


FlowFetchFn = Callable[[str, str, str], dict[str, Any]]


def normalize_kr_ticker(ticker: Any) -> str:
    text = str(ticker or "").strip()
    if text.isdigit():
        return text.zfill(6)
    return text


def flow_cache_path(session_date: str | date, *, path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    day = _date_key(session_date)
    return get_runtime_path("state", f"kr_candidate_flow_{day.replace('-', '')}.json")


def load_flow_cache(session_date: str | date, *, path: str | Path | None = None) -> dict[str, Any]:
    cache_path = flow_cache_path(session_date, path=path)
    if not cache_path.exists():
        return _empty_cache(session_date)
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return _empty_cache(session_date)
    if not isinstance(payload, dict):
        return _empty_cache(session_date)
    payload.setdefault("date", _date_key(session_date))
    payload.setdefault("records", {})
    if not isinstance(payload.get("records"), dict):
        payload["records"] = {}
    if _annotate_flow_cache_quality(payload):
        payload["_flow_quality_annotation_changed"] = True
    return payload


def save_flow_cache(cache: dict[str, Any], *, path: str | Path | None = None) -> Path:
    session_date = cache.get("date") or date.today().isoformat()
    _annotate_flow_cache_quality(cache)
    cache.pop("_flow_quality_annotation_changed", None)
    cache_path = flow_cache_path(session_date, path=path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_name(cache_path.name + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(cache_path)
    return cache_path


def update_candidate_flow_cache(
    tickers: list[Any],
    *,
    session_date: str | date,
    token: str,
    fetch_fn: FlowFetchFn | None = None,
    max_tickers: int = 30,
    sleep_sec: float = 0.5,
    path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Populate date/ticker investor flow cache.

    This function is designed for pre-session or shadow enrichment. It never
    raises on a ticker-level fetch failure; failures are stored per record.
    """
    cache = load_flow_cache(session_date, path=path)
    records = cache.setdefault("records", {})
    fetch = fetch_fn or _default_fetch_fn()
    fetched_at = (now or datetime.now()).isoformat(timespec="seconds")

    selected = _unique_tickers(tickers)[: max(0, int(max_tickers or 0))]
    changed = False
    for idx, ticker in enumerate(selected):
        existing = records.get(ticker)
        if isinstance(existing, dict) and existing.get("status") == "ok":
            continue
        try:
            flow = fetch(ticker, _date_key(session_date), token) or {}
            if not isinstance(flow, dict):
                flow = {}
            records[ticker] = {
                "ticker": ticker,
                "date": _date_key(session_date),
                "fetched_at": fetched_at,
                "status": "ok" if flow else "missing",
                "foreign": _optional_int(flow.get("foreign")),
                "institution": _optional_int(flow.get("institution")),
                "individual": _optional_int(flow.get("individual")),
                "source": "kis:inquire-investor",
            }
        except Exception as exc:
            records[ticker] = {
                "ticker": ticker,
                "date": _date_key(session_date),
                "fetched_at": fetched_at,
                "status": "error",
                "error": str(exc)[:200],
                "source": "kis:inquire-investor",
            }
        changed = True
        if sleep_sec > 0 and idx < len(selected) - 1:
            time.sleep(float(sleep_sec))

    cache["updated_at"] = fetched_at
    quality_changed = bool(cache.pop("_flow_quality_annotation_changed", False))
    quality_changed = _annotate_flow_cache_quality(cache) or quality_changed
    if changed or quality_changed:
        save_flow_cache(cache, path=path)
    return cache


def flow_for_ticker(cache: dict[str, Any], ticker: Any) -> dict[str, Any]:
    key = normalize_kr_ticker(ticker)
    record = ((cache or {}).get("records") or {}).get(key)
    if not isinstance(record, dict):
        return {}
    out = dict(record or {})
    quality = str((cache or {}).get("data_quality") or "").strip()
    if quality:
        out["flow_data_quality"] = quality
        out["investor_flow_quality"] = quality
    flags = (cache or {}).get("quality_flags") or (cache or {}).get("data_quality_flags") or []
    if isinstance(flags, (list, tuple, set)):
        out["flow_quality_flags"] = [str(flag) for flag in flags if str(flag).strip()]
    return out


def rolling_flow_from_caches(caches: list[dict[str, Any]], ticker: Any) -> dict[str, Any]:
    key = normalize_kr_ticker(ticker)
    records = []
    for cache in sorted(caches or [], key=lambda item: str((item or {}).get("date") or "")):
        record = flow_for_ticker(cache, key)
        if record:
            records.append(record)
    from bot.kr_candidate_features import rolling_flow_features

    return rolling_flow_features(records)


def _default_fetch_fn() -> FlowFetchFn:
    from phase1_trainer.supplement_collector import fetch_investor_flow_kr

    return fetch_investor_flow_kr


def _empty_cache(session_date: str | date) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "date": _date_key(session_date),
        "records": {},
        "data_quality": "empty",
        "quality_flags": [],
        "data_quality_flags": [],
        "record_count": 0,
        "ok_record_count": 0,
        "zero_flow_record_count": 0,
    }


def _annotate_flow_cache_quality(cache: dict[str, Any]) -> bool:
    before = {
        key: cache.get(key)
        for key in (
            "data_quality",
            "quality_flags",
            "data_quality_flags",
            "record_count",
            "ok_record_count",
            "zero_flow_record_count",
        )
    }
    records = cache.get("records") if isinstance(cache.get("records"), dict) else {}
    record_values = [record for record in records.values() if isinstance(record, dict)]
    ok_records = [record for record in record_values if str(record.get("status") or "").lower() == "ok"]

    def _is_zero_flow(record: dict[str, Any]) -> bool:
        return (
            record.get("foreign") == 0
            and record.get("institution") == 0
            and record.get("individual") == 0
        )

    zero_records = [record for record in ok_records if _is_zero_flow(record)]
    try:
        min_records = int(float(os.getenv("KR_CANDIDATE_FLOW_ZERO_CLUSTER_MIN_RECORDS", "10") or 10))
    except Exception:
        min_records = 10
    min_records = max(1, min_records)
    flags: list[str] = []
    if not record_values:
        quality = "empty"
    elif not ok_records:
        quality = "no_ok_flow"
        flags.append("kr_investor_flow_no_ok_records")
    elif len(ok_records) >= min_records and len(zero_records) == len(ok_records):
        quality = "bad_zero_flow_cluster"
        flags.append("kr_investor_flow_all_zero_cluster")
    else:
        quality = "ok"
        if zero_records:
            flags.append("kr_investor_flow_partial_zero_records")
    cache["data_quality"] = quality
    cache["quality_flags"] = flags
    cache["data_quality_flags"] = list(flags)
    cache["record_count"] = len(record_values)
    cache["ok_record_count"] = len(ok_records)
    cache["zero_flow_record_count"] = len(zero_records)
    after = {
        key: cache.get(key)
        for key in (
            "data_quality",
            "quality_flags",
            "data_quality_flags",
            "record_count",
            "ok_record_count",
            "zero_flow_record_count",
        )
    }
    return before != after


def _unique_tickers(tickers: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for ticker in tickers or []:
        key = normalize_kr_ticker(ticker)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _date_key(value: str | date) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text or date.today().isoformat()


def _optional_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(str(value).replace(",", "").strip()))
    except Exception:
        return None
