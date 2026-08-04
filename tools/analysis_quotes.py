"""분석 전용 KR 시세 (2026-08-04) — 라이브 봇의 KIS 앱키와 완전 분리.

배경: 장중 스탠드얼론 스크립트가 봇과 같은 KIS 키로 유니버스급 시세 루프를 돌리다
레이트리밋(500)을 건드린 실측(08-04). 분석·잠정 스캔·코호트 검토는 이 모듈만 쓴다.

1차 소스: 네이버 금융 폴링 API (키 불필요, 봇과 무관).
확장: 운영자가 KIS 분석용 별도 앱키(ANALYSIS_KIS_APP_KEY/SECRET)를 발급하면
      그 경로를 여기에 추가한다 — 라이브 kis_api는 절대 임포트하지 않는다.

사용:
    from tools.analysis_quotes import get_quote_kr
    q = get_quote_kr("005930")   # {price, open, high, low, volume, change_pct}
스로틀은 모듈이 강제한다(기본 0.25s). 실패 시 None.
"""
from __future__ import annotations

import time
from typing import Optional

import requests

_SESSION = requests.Session()
_LAST_CALL = 0.0
_MIN_INTERVAL = 0.25
_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"


def _num(raw) -> float:
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def get_quote_kr(code: str, timeout: float = 5.0) -> Optional[dict]:
    """네이버 실시간 시세 1건. 실패 시 None (예외 안 던짐)."""
    global _LAST_CALL
    wait = _MIN_INTERVAL - (time.time() - _LAST_CALL)
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL = time.time()
    try:
        r = _SESSION.get(_URL.format(code=str(code).strip()), timeout=timeout)
        r.raise_for_status()
        datas = (r.json() or {}).get("datas") or []
        if not datas:
            return None
        d = datas[0]
        price = _num(d.get("closePrice"))
        if price <= 0:
            return None
        return {
            "ticker": str(code),
            "price": price,
            "open": _num(d.get("openPrice")),
            "high": _num(d.get("highPrice")),
            "low": _num(d.get("lowPrice")),
            "volume": _num(d.get("accumulatedTradingVolume")),
            "change_pct": _num(d.get("fluctuationsRatio")),
            "source": "naver_polling",
        }
    except Exception:
        return None


if __name__ == "__main__":
    for c in ("005930", "069500"):
        print(c, get_quote_kr(c))
