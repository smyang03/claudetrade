from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from runtime_paths import get_runtime_path


FlowFetchFn = Callable[[str, str, str], dict[str, Any]]
_KR_CALENDAR: Any | None = None


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


def effective_flow_source_date(session_date: str | date, *, lag_trading_days: int = 1) -> str:
    """Return the completed KR trading day used for investor-flow enrichment."""
    cursor = _parse_date(session_date)
    remaining = max(0, int(lag_trading_days or 0))
    while remaining > 0:
        cursor -= timedelta(days=1)
        if _is_kr_trading_day(cursor):
            remaining -= 1
    return cursor.isoformat()


def load_effective_flow_cache(session_date: str | date, *, path: str | Path | None = None) -> dict[str, Any]:
    source_date = effective_flow_source_date(session_date)
    cache = load_flow_cache(source_date, path=path)
    cache["requested_session_date"] = _date_key(session_date)
    cache["effective_flow_source_date"] = source_date
    cache["flow_age_trading_days"] = _trading_day_distance(source_date, session_date)
    cache.setdefault("flow_source_date", source_date)
    cache.setdefault("flow_source_policy", "previous_completed_trading_day")
    return cache


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
    flow_source_date: str | date | None = None,
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
    target_date = _date_key(flow_source_date or session_date)
    session_day = _date_key(session_date)
    cache = load_flow_cache(target_date, path=path)
    cache["date"] = target_date
    cache["requested_session_date"] = session_day
    cache["flow_source_date"] = target_date
    cache["flow_age_trading_days"] = _trading_day_distance(target_date, session_day)
    cache.setdefault("flow_source_policy", "explicit_source_date")
    records = cache.setdefault("records", {})
    fetch = fetch_fn or _default_fetch_fn()
    fetched_at = (now or datetime.now()).isoformat(timespec="seconds")

    selected = _unique_tickers(tickers)[: max(0, int(max_tickers or 0))]
    changed = False
    for idx, ticker in enumerate(selected):
        existing = records.get(ticker)
        if (
            isinstance(existing, dict)
            and existing.get("status") == "ok"
            and _record_flow_values_trusted(existing, cache)
        ):
            continue
        try:
            flow = fetch(ticker, target_date, token) or {}
            if not isinstance(flow, dict):
                flow = {}
            fr = _optional_int(flow.get("foreign"))
            ins = _optional_int(flow.get("institution"))
            indv = _optional_int(flow.get("individual"))
            flow_date = str(flow.get("flow_date") or "").strip() or None
            date_matched = bool(flow.get("flow_date_matched"))
            all_zero = (fr or 0) == 0 and (ins or 0) == 0 and (indv or 0) == 0
            # 날짜 미매칭(=output[0] 폴백)인데 all-zero면 정산 전 당일값일 가능성 → 신뢰 안 함(재시도 유도)
            unsettled_zero = bool(flow) and all_zero and not date_matched
            records[ticker] = {
                "ticker": ticker,
                "date": target_date,
                "flow_source_date": target_date,
                "flow_age_trading_days": cache.get("flow_age_trading_days", 0),
                "fetched_at": fetched_at,
                "status": "ok" if flow else "missing",
                "foreign": fr,
                "institution": ins,
                "individual": indv,
                "flow_values_trusted": bool(flow) and not unsettled_zero,
                "source": "kis:inquire-investor",
            }
            if flow_date:
                records[ticker]["flow_reported_date"] = flow_date
            records[ticker]["flow_date_matched"] = date_matched
            if not flow:
                records[ticker]["flow_unavailable_reason"] = "missing"
            elif unsettled_zero:
                records[ticker]["flow_unavailable_reason"] = "unsettled_zero_unmatched_date"
        except Exception as exc:
            records[ticker] = {
                "ticker": ticker,
                "date": target_date,
                "flow_source_date": target_date,
                "flow_age_trading_days": cache.get("flow_age_trading_days", 0),
                "fetched_at": fetched_at,
                "status": "error",
                "error": str(exc)[:200],
                "flow_values_trusted": False,
                "flow_unavailable_reason": "fetch_error",
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


def update_candidate_flow_series(
    tickers: list[Any],
    *,
    session_date: str | date,
    days: int = 5,
    token: str,
    fetch_series_fn: Callable[[str, str, str, str], list] | None = None,
    max_tickers: int = 30,
    sleep_sec: float = 0.2,
    path_for: Callable[[str], Path] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """다중일 수급 시리즈 캡처(2026-07-21) — 티커당 1콜로 완료 거래일 lag 1..days를
    각 date별 캐시 파일에 분배 저장한다. 호출 수는 단일일 방식과 동일(엔드포인트가
    이미 범위 리스트를 반환). 반환값은 lag-1 캐시 + 시리즈 메타(기존 소비자 호환).

    no-lookahead: 완료 거래일(lag>=1)만 저장. 미래·당일(미정산) 행은 버린다.
    """
    session_day = _date_key(session_date)
    window = max(1, int(days or 1))
    source_dates: list[str] = []
    seen: set[str] = set()
    for lag in range(1, window + 1):
        d = effective_flow_source_date(session_day, lag_trading_days=lag)
        if d not in seen:
            seen.add(d)
            source_dates.append(d)
    if not source_dates:
        return _empty_cache(session_day)
    newest, oldest = source_dates[0], source_dates[-1]

    def _path(d: str) -> Path | None:
        return path_for(d) if path_for is not None else None

    caches: dict[str, dict[str, Any]] = {d: load_flow_cache(d, path=_path(d)) for d in source_dates}
    for d, cache in caches.items():
        cache["date"] = d
        cache["flow_source_date"] = d
        cache.setdefault("flow_source_policy", "series_capture")

    if fetch_series_fn is None:
        from phase1_trainer.supplement_collector import fetch_investor_flow_kr_series

        fetch_series_fn = fetch_investor_flow_kr_series
    fetched_at = (now or datetime.now()).isoformat(timespec="seconds")
    date_set = set(source_dates)
    changed: set[str] = set()
    fetch_errors = 0

    selected = _unique_tickers(tickers)[: max(0, int(max_tickers or 0))]
    for idx, ticker in enumerate(selected):
        # 윈도 내 모든 date에 신뢰 레코드가 이미 있으면 skip(증분 — 2일차부터는 최신 1일만 부족)
        missing = [
            d for d in source_dates
            if not (
                isinstance(caches[d]["records"].get(ticker), dict)
                and caches[d]["records"][ticker].get("status") == "ok"
                and _record_flow_values_trusted(caches[d]["records"][ticker], caches[d])
            )
        ]
        if not missing:
            continue
        try:
            series = fetch_series_fn(ticker, oldest, newest, token) or []
        except Exception as exc:
            fetch_errors += 1
            log_dates = missing[:1]
            for d in log_dates:
                caches[d]["records"][ticker] = {
                    "ticker": ticker,
                    "date": d,
                    "flow_source_date": d,
                    "fetched_at": fetched_at,
                    "status": "error",
                    "error": str(exc)[:200],
                    "flow_values_trusted": False,
                    "flow_unavailable_reason": "fetch_error",
                    "source": "kis:inquire-investor:series",
                }
                changed.add(d)
            continue
        by_date = {str(row.get("date") or ""): row for row in series if isinstance(row, dict)}
        for d in missing:
            row = by_date.get(d)
            if row is None:
                # 응답 범위에 그 date가 없으면 missing으로 기록(휴장 오판·범위 밖 구분용)
                caches[d]["records"][ticker] = {
                    "ticker": ticker,
                    "date": d,
                    "flow_source_date": d,
                    "fetched_at": fetched_at,
                    "status": "missing",
                    "flow_values_trusted": False,
                    "flow_unavailable_reason": "not_in_series_response",
                    "source": "kis:inquire-investor:series",
                }
                changed.add(d)
                continue
            caches[d]["records"][ticker] = {
                "ticker": ticker,
                "date": d,
                "flow_source_date": d,
                "flow_age_trading_days": _trading_day_distance(d, session_day),
                "fetched_at": fetched_at,
                "status": "ok",
                "foreign": _optional_int(row.get("foreign")),
                "institution": _optional_int(row.get("institution")),
                "individual": _optional_int(row.get("individual")),
                "flow_reported_date": str(row.get("flow_date") or d),
                "flow_date_matched": True,
                "flow_values_trusted": True,
                "source": "kis:inquire-investor:series",
            }
            changed.add(d)
        if sleep_sec > 0 and idx < len(selected) - 1:
            time.sleep(float(sleep_sec))

    for d in changed:
        caches[d]["updated_at"] = fetched_at
        save_flow_cache(caches[d], path=_path(d))

    primary = caches[newest]
    primary["requested_session_date"] = session_day
    primary["flow_age_trading_days"] = _trading_day_distance(newest, session_day)
    primary["series_dates"] = source_dates
    primary["series_days"] = window
    primary["series_changed_dates"] = sorted(changed)
    primary["series_fetch_errors"] = fetch_errors
    return primary


def flow_for_ticker(cache: dict[str, Any], ticker: Any) -> dict[str, Any]:
    key = normalize_kr_ticker(ticker)
    record = ((cache or {}).get("records") or {}).get(key)
    if not isinstance(record, dict):
        return {}
    out = dict(record or {})
    quality = str((cache or {}).get("data_quality") or "").strip()
    source_date = out.get("flow_source_date") or (cache or {}).get("flow_source_date") or (cache or {}).get("date")
    if source_date:
        out["flow_source_date"] = str(source_date)
    requested_session_date = (cache or {}).get("requested_session_date")
    if requested_session_date:
        out["requested_session_date"] = str(requested_session_date)
    effective_source_date = (cache or {}).get("effective_flow_source_date")
    if effective_source_date:
        out["effective_flow_source_date"] = str(effective_source_date)
    flow_age = out.get("flow_age_trading_days", (cache or {}).get("flow_age_trading_days"))
    if flow_age not in (None, ""):
        out["flow_age_trading_days"] = _optional_int(flow_age)
    if quality:
        out["flow_data_quality"] = quality
        out["investor_flow_quality"] = quality
        if quality == "bad_zero_flow_cluster":
            out["flow_values_trusted"] = False
            out["flow_unavailable_reason"] = "all_zero_cluster"
    if out.get("flow_values_trusted") is False:
        out.setdefault("flow_unavailable_reason", "untrusted")
    flags = (cache or {}).get("quality_flags") or (cache or {}).get("data_quality_flags") or []
    if isinstance(flags, (list, tuple, set)):
        out["flow_quality_flags"] = [str(flag) for flag in flags if str(flag).strip()]
    return out


def load_recent_flow_records(
    session_date: str | date,
    ticker: Any,
    *,
    days: int = 5,
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """세션 직전 완료 거래일부터 최대 `days`개 일별 flow 레코드를 반환(오래된→최신).

    lookahead 없음: effective_flow_source_date(lag=1..days)로 완료된 거래일만 사용한다.
    신뢰 불가(flow_values_trusted is False) 레코드는 제외한다. path는 테스트용 단일
    경로 오버라이드(운영 경로는 기본 date별 파일).
    """
    key = normalize_kr_ticker(ticker)
    window = max(1, int(days or 1))
    out: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for lag in range(1, window + 1):
        source_date = effective_flow_source_date(session_date, lag_trading_days=lag)
        if source_date in seen_dates:
            continue
        seen_dates.add(source_date)
        cache = load_flow_cache(source_date, path=path)
        record = flow_for_ticker(cache, key)
        if not record or record.get("flow_values_trusted") is False:
            continue
        rec = dict(record)
        rec.setdefault("date", source_date)
        out.append(rec)
    out.sort(key=lambda item: str(item.get("date") or ""))
    return out


def rolling_flow_from_caches(caches: list[dict[str, Any]], ticker: Any) -> dict[str, Any]:
    key = normalize_kr_ticker(ticker)
    records = []
    for cache in sorted(caches or [], key=lambda item: str((item or {}).get("date") or "")):
        record = flow_for_ticker(cache, key)
        if record and record.get("flow_values_trusted") is not False:
            records.append(record)
    from bot.kr_candidate_features import rolling_flow_features

    return rolling_flow_features(records)


def _default_fetch_fn() -> FlowFetchFn:
    from phase1_trainer.supplement_collector import fetch_investor_flow_kr

    return fetch_investor_flow_kr


def _empty_cache(session_date: str | date) -> dict[str, Any]:
    day = _date_key(session_date)
    return {
        "schema_version": 1,
        "date": day,
        "flow_source_date": day,
        "records": {},
        "data_quality": "empty",
        "quality_flags": [],
        "data_quality_flags": [],
        "record_count": 0,
        "ok_record_count": 0,
        "zero_flow_record_count": 0,
        "untrusted_flow_record_count": 0,
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
            "untrusted_flow_record_count",
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
    record_changed = False
    if quality == "bad_zero_flow_cluster":
        for record in zero_records:
            record_changed = _set_if_changed(record, "flow_values_trusted", False) or record_changed
            record_changed = _set_if_changed(record, "flow_unavailable_reason", "all_zero_cluster") or record_changed
            record_changed = _set_if_changed(record, "availability", "untrusted_all_zero_cluster") or record_changed
    else:
        for record in ok_records:
            if "flow_values_trusted" not in record:
                record["flow_values_trusted"] = True
                record_changed = True
            if record.get("flow_values_trusted") is not False and record.get("flow_unavailable_reason") == "all_zero_cluster":
                record.pop("flow_unavailable_reason", None)
                if record.get("availability") == "untrusted_all_zero_cluster":
                    record.pop("availability", None)
                record_changed = True
    untrusted_records = [
        record
        for record in record_values
        if isinstance(record, dict) and record.get("flow_values_trusted") is False
    ]
    cache["data_quality"] = quality
    cache["quality_flags"] = flags
    cache["data_quality_flags"] = list(flags)
    cache["record_count"] = len(record_values)
    cache["ok_record_count"] = len(ok_records)
    cache["zero_flow_record_count"] = len(zero_records)
    cache["untrusted_flow_record_count"] = len(untrusted_records)
    after = {
        key: cache.get(key)
        for key in (
            "data_quality",
            "quality_flags",
            "data_quality_flags",
            "record_count",
            "ok_record_count",
            "zero_flow_record_count",
            "untrusted_flow_record_count",
        )
    }
    return before != after or record_changed


def _record_flow_values_trusted(record: dict[str, Any], cache: dict[str, Any]) -> bool:
    if str((cache or {}).get("data_quality") or "").strip() == "bad_zero_flow_cluster":
        return False
    if record.get("flow_values_trusted") is False:
        return False
    if str(record.get("flow_unavailable_reason") or "").strip() == "all_zero_cluster":
        return False
    return True


def _set_if_changed(record: dict[str, Any], key: str, value: Any) -> bool:
    if record.get(key) == value:
        return False
    record[key] = value
    return True


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


def _parse_date(value: str | date) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _date_key(value)
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except Exception:
        return date.today()


def _is_kr_trading_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    # known-holiday 오버라이드 우선 — exchange_calendars(XKRX)가 모르는 KR 임시공휴일
    # (제헌절·선거 등)을 거래일로 오판하면 flow 소스가 휴장일(전량 0)로 잡힌다.
    # bot/market_utils._is_trading_day와 동일 계약(bot/session_date).
    try:
        from bot.session_date import is_known_market_holiday

        if is_known_market_holiday("KR", day):
            return False
    except Exception:
        pass
    global _KR_CALENDAR
    try:
        import exchange_calendars as ec

        if _KR_CALENDAR is None:
            _KR_CALENDAR = ec.get_calendar("XKRX")
        return bool(_KR_CALENDAR.is_session(day.isoformat()))
    except Exception:
        return day.weekday() < 5


def _trading_day_distance(source_date: str | date, session_date: str | date) -> int:
    source = _parse_date(source_date)
    session = _parse_date(session_date)
    if source >= session:
        return 0
    count = 0
    cursor = source
    while cursor < session:
        cursor += timedelta(days=1)
        if _is_kr_trading_day(cursor):
            count += 1
    return count


def _optional_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(str(value).replace(",", "").strip()))
    except Exception:
        return None
