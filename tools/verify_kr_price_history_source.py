#!/usr/bin/env python3
"""KR 가격 히스토리 백필 소스 검증 — 생존편향 채택 게이트 (2026-08-25).

학습용 가격 CSV가 ~2025-04까지뿐인 격차의 KR측 백필 후보를 실측한다.
채택 게이트 = "이후 상장폐지된 종목의 과거 일봉이 소스에 남아 있는가".
0이면 그 소스는 학습용 부적격(급락 레인은 정확히 폐지되는 유형을 산다).

08-25 첫 실측:
  - 네이버 siseJson: 048260(오스템임플란트, 2023-08 자진상폐)의 2023-05 일봉
    보존 확인 → 게이트 통과. 단 수정주가만 제공(무수정가 없음) — 정본 저장은
    "수집 시점 명시 + 이벤트 대조" 규약 필요.
  - KRX 정보데이터시스템 원 API: HTTP 400 'LOGOUT'(세션 요구) → pykrx 1.0.51
    일자별 전종목 스냅샷 경로도 같은 벽. 현재 닫힘.

read-only: live DB·KIS 접근 없음. 외부 호출은 네이버/KRX 공개 엔드포인트뿐.
장중 사용 무방(분석은 네이버 소스 — analysis-script-runbook 규약).
"""
from __future__ import annotations

import argparse
import sys

import requests

NAVER_URL = "https://api.finance.naver.com/siseJson.naver"
KRX_URL = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
UA = {"User-Agent": "Mozilla/5.0"}

# 상폐 실측 케이스: (종목코드, 상폐 시기 메모, 존재해야 하는 과거 창)
DELISTED_CASES = [
    ("048260", "오스템임플란트 2023-08 자진상폐", "20230501", "20230610"),
]
ACTIVE_CASE = ("005930", "삼성전자", "20230101", "20230131")


def _naver_days(symbol: str, start: str, end: str) -> int:
    resp = requests.get(
        NAVER_URL,
        params={"symbol": symbol, "requestType": 1, "startTime": start, "endTime": end, "timeframe": "day"},
        headers=UA,
        timeout=15,
    )
    if resp.status_code != 200:
        return -1
    # 응답은 JSON 유사 배열 — 날짜 행(["YYYYMMDD", ...]) 수만 센다
    return sum(1 for line in resp.text.splitlines() if line.strip().startswith('["2'))


def main() -> int:
    argparse.ArgumentParser(description="KR 백필 소스 생존편향 게이트").parse_args()
    ok = True

    code, note, start, end = ACTIVE_CASE
    days = _naver_days(code, start, end)
    print(f"[네이버] 활성 {code}({note}) {start}~{end}: 일봉 {days}행 {'OK' if days > 0 else 'FAIL'}")
    ok = ok and days > 0

    for code, note, start, end in DELISTED_CASES:
        days = _naver_days(code, start, end)
        verdict = "게이트 통과(상폐 후에도 과거 일봉 보존)" if days > 0 else "게이트 실패 — 학습용 부적격"
        print(f"[네이버] 상폐 {code}({note}) {start}~{end}: 일봉 {days}행 → {verdict}")
        ok = ok and days > 0

    try:
        resp = requests.post(
            KRX_URL,
            data={"bld": "dbms/MDC/STAT/standard/MDCSTAT01501", "mktId": "ALL", "trdDd": "20230601",
                  "share": "1", "money": "1", "csvxls_isNo": "false"},
            headers={**UA, "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd"},
            timeout=15,
        )
        krx_state = f"HTTP {resp.status_code} {resp.text[:40]!r}"
        krx_open = resp.status_code == 200 and "OutBlock" in resp.text
    except requests.RequestException as exc:
        krx_state, krx_open = f"요청 실패 {exc}", False
    print(f"[KRX] 일자별 전종목 스냅샷 원 API: {krx_state} → {'열림' if krx_open else '닫힘(세션 요구) — pykrx 스냅샷 경로 사용 불가'}")

    print()
    print("규약: 네이버는 수정주가만 제공(조회시점 소급 재계산) — 백필 정본에는 수집일을 박고,")
    print("      분할/배당 이벤트일 전후 ±3일을 기존 수집 CSV와 대조한다. live DB와 분리(shadow 선행).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
