"""US 실적 캘린더 — Finnhub 일 1회 수집, 지뢰 지도 용도 (2026-06-12 운영자 승인).

용도 (방어 우선):
1. PathB 신규 등록 보류: 실적 D-1~D+1 (ORCL 2026-06-11 거래정지 충돌 사건 처방)
2. 후보 라인 earn=D±n 토큰 (PEAD 정책상 earnings_date는 즉시 prompt 노출 허용)
3. 컨텍스트 경고: 보유/플랜 종목 실적 임박 표시

원칙: 캘린더 결측 시 무동작(fail-open — 소형주 커버리지 ~37% 현실 반영),
KR 미적용(Finnhub US 전용), 수집 실패가 거래를 멈추지 않는다.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from runtime_paths import get_runtime_path

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_MEM_CACHE: dict[str, Any] = {"loaded_at": 0.0, "data": None}
_MEM_TTL_SEC = 600.0


def _cache_path() -> Path:
    return get_runtime_path("data", "earnings_calendar.json")


def _enabled() -> bool:
    return str(os.getenv("EARNINGS_CALENDAR_ENABLED", "true") or "").strip().lower() in {"1", "true", "yes", "on"}


def refresh_earnings_calendar(*, days_back: int = 3, days_ahead: int = 14, timeout_sec: float = 15.0) -> dict[str, Any]:
    """Finnhub에서 실적 캘린더를 받아 캐시 파일로 저장. 실패 시 기존 캐시 유지."""
    key = str(os.getenv("FINNHUB_API_KEY", "") or os.getenv("FINNHUB_KEY", "") or "").strip()
    if not key:
        return {"ok": False, "reason": "no_api_key"}
    start = (date.today() - timedelta(days=days_back)).isoformat()
    end = (date.today() + timedelta(days=days_ahead)).isoformat()
    url = f"https://finnhub.io/api/v1/calendar/earnings?from={start}&to={end}&token={key}"
    try:
        with urllib.request.urlopen(url, timeout=timeout_sec) as r:
            items = json.loads(r.read()).get("earningsCalendar") or []
    except Exception as exc:
        log.warning(f"[실적 캘린더] 수집 실패 (기존 캐시 유지): {exc}")
        return {"ok": False, "reason": str(exc)[:100]}
    by_symbol: dict[str, dict[str, Any]] = {}
    for it in items:
        sym = str(it.get("symbol") or "").strip().upper()
        if not sym:
            continue
        # 같은 종목 다건이면 가장 가까운 미래/최근 날짜 우선
        existing = by_symbol.get(sym)
        if existing is None or str(it.get("date") or "") < str(existing.get("date") or "9999"):
            by_symbol[sym] = {
                "date": str(it.get("date") or ""),
                "hour": str(it.get("hour") or ""),
                "eps_estimate": it.get("epsEstimate"),
                "eps_actual": it.get("epsActual"),
            }
    payload = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "from": start,
        "to": end,
        "count": len(by_symbol),
        "by_symbol": by_symbol,
    }
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    with _LOCK:
        _MEM_CACHE["loaded_at"] = 0.0  # 다음 조회 시 재로드
    log.info(f"[실적 캘린더] 갱신 완료: {start}~{end} {len(by_symbol)}종목")
    return {"ok": True, "count": len(by_symbol)}


def _load_calendar() -> dict[str, Any]:
    now = time.time()
    with _LOCK:
        if _MEM_CACHE["data"] is not None and (now - _MEM_CACHE["loaded_at"]) < _MEM_TTL_SEC:
            return _MEM_CACHE["data"]
    path = _cache_path()
    data: dict[str, Any] = {}
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    # 하루 지난 캐시는 백그라운드성 갱신 시도 (실패해도 기존 데이터 사용)
    fetched = str(data.get("fetched_at") or "")[:10]
    if _enabled() and fetched != date.today().isoformat():
        try:
            refresh_earnings_calendar()
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    with _LOCK:
        _MEM_CACHE["data"] = data
        _MEM_CACHE["loaded_at"] = now
    return data


def earnings_info(ticker: str, market: str = "US") -> dict[str, Any] | None:
    """해당 종목의 가장 가까운 실적 정보. 없거나 KR이면 None (fail-open)."""
    if not _enabled() or str(market or "").upper() != "US":
        return None
    sym = str(ticker or "").strip().upper()
    if not sym:
        return None
    data = _load_calendar()
    info = (data.get("by_symbol") or {}).get(sym)
    return dict(info) if isinstance(info, dict) else None


def earnings_offset_days(ticker: str, market: str = "US", *, ref_date: date | None = None) -> int | None:
    """실적일까지 캘린더 일수 (음수=지남, 0=오늘, 양수=남음). 정보 없으면 None."""
    info = earnings_info(ticker, market)
    if not info or not info.get("date"):
        return None
    try:
        edate = date.fromisoformat(str(info["date"])[:10])
    except Exception:
        return None
    base = ref_date or date.today()
    return (edate - base).days


def earnings_tag(ticker: str, market: str = "US", *, max_abs_days: int = 3) -> str:
    """후보 라인용 토큰: 'earn=D-1(amc)' 등. 실적 ±max_abs_days 밖이거나 정보 없으면 빈 문자열.

    표기 관례: D-1 = 실적 하루 전(offset=+1), D0 = 당일, D+1 = 다음날(offset=-1).
    """
    offset = earnings_offset_days(ticker, market)
    if offset is None or abs(offset) > max_abs_days:
        return ""
    label = "D0" if offset == 0 else (f"D-{offset}" if offset > 0 else f"D+{-offset}")
    info = earnings_info(ticker, market) or {}
    hour = str(info.get("hour") or "").strip()
    return f"earn={label}({hour})" if hour else f"earn={label}"


def earnings_window_block(ticker: str, market: str = "US") -> dict[str, Any]:
    """PathB 신규 등록 보류 판정: 실적 D-1~D+1 (env 조정 가능). 정보 없으면 허용."""
    result = {"blocked": False, "offset_days": None, "earnings_date": ""}
    if str(os.getenv("EARNINGS_WINDOW_BLOCK_ENABLED", "true") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return result
    offset = earnings_offset_days(ticker, market)
    if offset is None:
        return result
    days_before = int(float(os.getenv("EARNINGS_WINDOW_BLOCK_DAYS_BEFORE", "1") or 1))
    days_after = int(float(os.getenv("EARNINGS_WINDOW_BLOCK_DAYS_AFTER", "1") or 1))
    info = earnings_info(ticker, market) or {}
    result["offset_days"] = offset
    result["earnings_date"] = str(info.get("date") or "")
    # offset > 0 = 실적이 미래 (D-offset), offset < 0 = 지남 (D+|offset|)
    if -days_after <= offset <= days_before:
        result["blocked"] = True
    return result
