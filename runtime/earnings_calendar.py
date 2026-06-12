"""실적 캘린더 — US(Finnhub 사전 캘린더) + KR(DART 실적성 공시 당일 감지).

용도 (방어 우선):
1. PathB 신규 등록 보류: US 실적 D-1~D+1, KR 실적 공시 D0~D+1
   (US ORCL 2026-06-11 거래정지 충돌 사건 처방)
2. 후보 라인 earn=D±n 토큰 (PEAD 정책상 earnings_date는 즉시 prompt 노출 허용)
3. 컨텍스트 경고: 보유/플랜 종목 실적 임박 표시

KR 한계: 발표 예정일 공시 제도가 없어 사전(D-1) 감지는 불가 — DART 잠정실적/
손익구조변동 공시를 당일 감지해 직후 변동성(D0~D+1)만 방어한다. 사전 추정
(작년 동분기 공시일 기반 윈도우)은 2단계 후보.

원칙: 캘린더 결측 시 무동작(fail-open), 수집 실패가 거래를 멈추지 않는다.
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


# KR 실적성 공시 report_nm 패턴 — '실적' 단순 매칭은 증권발행실적보고서 등 오탐
_KR_EARNINGS_PATTERNS = ("영업(잠정)실적", "잠정실적", "매출액또는손익구조", "영업실적등에대한전망")


def refresh_kr_earnings_disclosures(*, days_back: int = 2, timeout_sec: float = 15.0, max_pages: int = 8) -> dict[str, Any]:
    """DART 최근 공시에서 실적성 공시 종목을 감지해 캐시의 kr_by_code에 저장.

    pblntf_ty=I(거래소공시) 필수 — 전체 공시는 펀드 서류가 일 1,000건+를 차지해
    실적 공시에 페이지가 도달하지 못한다 (4/27~29 소급 검증: 전체 3,093건 중
    I 필터 693건, 실적성 매칭 65건).
    """
    key = str(os.getenv("DART_API_KEY", "") or "").strip()
    if not key:
        return {"ok": False, "reason": "no_api_key"}
    bgn = (date.today() - timedelta(days=days_back)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    kr_by_code: dict[str, dict[str, Any]] = {}
    try:
        for page in range(1, max_pages + 1):
            url = (
                "https://opendart.fss.or.kr/api/list.json"
                f"?crtfc_key={key}&bgn_de={bgn}&end_de={end}&pblntf_ty=I&page_count=100&page_no={page}"
            )
            with urllib.request.urlopen(url, timeout=timeout_sec) as r:
                d = json.loads(r.read())
            items = d.get("list") or []
            for it in items:
                name = str(it.get("report_nm") or "")
                if not any(p in name for p in _KR_EARNINGS_PATTERNS):
                    continue
                code = str(it.get("stock_code") or "").strip()
                if not code:
                    continue
                rcept = str(it.get("rcept_dt") or "")
                iso = f"{rcept[:4]}-{rcept[4:6]}-{rcept[6:8]}" if len(rcept) == 8 else ""
                existing = kr_by_code.get(code)
                if existing is None or iso > str(existing.get("date") or ""):
                    kr_by_code[code] = {"date": iso, "hour": "공시", "report": name[:40]}
            if len(items) < 100:
                break
    except Exception as exc:
        log.warning(f"[KR 실적 공시] 수집 실패 (기존 캐시 유지): {exc}")
        return {"ok": False, "reason": str(exc)[:100]}
    path = _cache_path()
    data: dict[str, Any] = {}
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data["kr_fetched_at"] = datetime.now().isoformat(timespec="seconds")
    data["kr_by_code"] = kr_by_code
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    with _LOCK:
        _MEM_CACHE["loaded_at"] = 0.0
    log.info(f"[KR 실적 공시] 갱신 완료: {bgn}~{end} {len(kr_by_code)}종목")
    return {"ok": True, "count": len(kr_by_code)}


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
    today_iso = date.today().isoformat()
    if _enabled() and str(data.get("fetched_at") or "")[:10] != today_iso:
        try:
            refresh_earnings_calendar()
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if _enabled() and str(data.get("kr_fetched_at") or "")[:10] != today_iso:
        try:
            refresh_kr_earnings_disclosures()
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    with _LOCK:
        _MEM_CACHE["data"] = data
        _MEM_CACHE["loaded_at"] = now
    return data


def earnings_info(ticker: str, market: str = "US") -> dict[str, Any] | None:
    """해당 종목의 가장 가까운 실적 정보. 없으면 None (fail-open).

    US = Finnhub 사전 캘린더, KR = DART 실적성 공시(당일 감지 — 과거만 존재).
    """
    if not _enabled():
        return None
    market_key = str(market or "").upper()
    sym = str(ticker or "").strip()
    if not sym:
        return None
    data = _load_calendar()
    if market_key == "US":
        info = (data.get("by_symbol") or {}).get(sym.upper())
    else:
        info = (data.get("kr_by_code") or {}).get(sym)
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
