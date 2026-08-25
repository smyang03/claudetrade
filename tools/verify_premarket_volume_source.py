#!/usr/bin/env python3
"""프리마켓 거래량 소스(Alpaca SIP) 검증 — 채택 게이트 실측 도구 (2026-08-25).

배경: 신호일 저녁(22:00~22:35 KST) 후보의 프리마켓 유동성 확인 축이 3소스
(yfinance prepost=volume 전부 0 · KIS · Finnhub 무료 403) 막혀 있었다.
조사(08-25)에서 Alpaca Basic(무료)이 1순위 후보 — `end`를 15분 이상 과거로
주면 SIP(전 거래소 통합) 분봉을 무료로 준다.

⚠️ 문서 검증까지가 조사였고, 이 도구가 채택 게이트다(픽스처 교훈 계열 —
"문서가 된다"와 "우리 키로 실제 나온다"는 다르다):
  1) 분봉 volume이 비영(0 아님)으로 나오는가  ← yfinance가 여기서 떨어졌다
  2) 프리마켓(4:00~9:30 ET) 창이 실제로 포함되는가
  3) 누적량을 Nasdaq 사이트 프리마켓 페이지와 1회 수동 대조(sale condition
     과소집계 — 절대량이 아니라 종목 간 상대 비교로 쓸 것)

사용:
  set ALPACA_API_KEY_ID=... & set ALPACA_API_SECRET_KEY=...  (paper 계정 키)
  python tools/verify_premarket_volume_source.py --tickers AAPL,NVDA,TSLA
  python tools/verify_premarket_volume_source.py --date 2026-08-24

read-only: 주문·브로커·live DB 접근 없음. 외부 호출은 data.alpaca.markets뿐.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import os
import sys
from zoneinfo import ZoneInfo

import requests

BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
ET = ZoneInfo("America/New_York")


def _last_us_weekday(now_et: datetime) -> str:
    day = now_et.date()
    # 오늘 프리마켓이 아직 안 끝났을 수 있으니 전 거래일 기본 (주말은 금요일로)
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Alpaca SIP 프리마켓 분봉 volume 검증")
    parser.add_argument("--tickers", default="AAPL,NVDA,TSLA")
    parser.add_argument("--date", default="", help="검증할 미국 세션 날짜(YYYY-MM-DD), 기본=직전 평일")
    parser.add_argument("--feed", default="sip", choices=["sip", "iex", "delayed_sip"])
    args = parser.parse_args()

    key_id = os.getenv("ALPACA_API_KEY_ID", "").strip()
    secret = os.getenv("ALPACA_API_SECRET_KEY", "").strip()
    if not key_id or not secret:
        print("FAIL: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY 환경변수가 없다.")
        print("  → https://alpaca.markets 에서 paper 계정 가입 후 API 키를 발급해 환경변수로 넣고 재실행.")
        print("  (키는 .env.live에 넣지 않는다 — 브로커 자격증명과 분리, 셸 환경변수 또는 별도 파일로.)")
        return 2

    now_et = datetime.now(tz=ET)
    session = args.date or _last_us_weekday(now_et)
    start_et = datetime.fromisoformat(f"{session}T04:00:00").replace(tzinfo=ET)
    end_et = datetime.fromisoformat(f"{session}T09:30:00").replace(tzinfo=ET)
    # 무료 티어 제약: end가 현재로부터 15분 이상 과거여야 SIP 허용
    latest_allowed = datetime.now(tz=timezone.utc) - timedelta(minutes=16)
    end_utc = min(end_et.astimezone(timezone.utc), latest_allowed)

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
    params = {
        "symbols": ",".join(tickers),
        "timeframe": "1Min",
        "start": start_et.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "end": end_utc.isoformat().replace("+00:00", "Z"),
        "feed": args.feed,
        "limit": "10000",
        "adjustment": "raw",
    }
    resp = requests.get(BARS_URL, headers=headers, params=params, timeout=20)
    if resp.status_code != 200:
        print(f"FAIL: HTTP {resp.status_code} — {resp.text[:300]}")
        if resp.status_code in (401, 403):
            print("  → 키가 틀렸거나 이 feed 권한이 없다. --feed delayed_sip / iex로 재시도해 어디까지 되는지 확인.")
        return 1
    payload = resp.json()
    bars_by_symbol = payload.get("bars") or {}

    print(f"=== Alpaca {args.feed} 프리마켓 검증 — 세션 {session} (4:00 ET ~ {end_utc.astimezone(ET).strftime('%H:%M')} ET) ===")
    overall_pass = True
    for ticker in tickers:
        bars = bars_by_symbol.get(ticker) or []
        total_vol = sum(int(b.get("v") or 0) for b in bars)
        nonzero = sum(1 for b in bars if int(b.get("v") or 0) > 0)
        verdict = "PASS" if bars and total_vol > 0 else "FAIL"
        if verdict == "FAIL":
            overall_pass = False
        print(f"  {ticker:6s} 봉 {len(bars):4d}개 | volume>0 봉 {nonzero:4d}개 | 누적 {total_vol:,}주 | {verdict}")
    if not bars_by_symbol:
        print("  (응답에 bars 없음 — 날짜/권한 확인)")
        overall_pass = False
    print()
    if overall_pass:
        print("판정: 1차 게이트 통과(volume 비영). 다음 = Nasdaq 사이트 프리마켓 페이지와 같은 세션 누적량 수동 대조 1회.")
        print("      대조 후 채택이면 후보 핸드오프 배선은 별도 승인으로 진행한다(관측 전용부터).")
    else:
        print("판정: 게이트 실패 — 이 소스도 닫힌 축이다. 2순위(Massive Starter $29/월) 검토로 넘어간다.")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
