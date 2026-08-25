#!/usr/bin/env python3
"""US 일봉 과거 백필 — Alpaca SIP (2026-08-25).

격차: data/price/us CSV가 과거로 2025-04-14까지밖에 없다(1,600+종목). 교재
재구축·장기 검증이 "창 이동(과거 상실)"에 갇히는 원인. 이 도구는 그 이전
구간을 Alpaca 일봉으로 백필한다.

원칙(CLAUDE.md 외부 데이터):
- **라이브 수집 CSV와 분리 저장** — data/price_backfill_alpaca/us/. 라이브
  지표·시뮬 경로는 이 디렉터리를 모르는 채로 둔다(오염 차단). 병합은 검증
  통과 후 별도 승인으로만.
- 출처·시점·수정방식은 manifest(json)에 박는다.
- **경계 검증**: 기존 CSV와 겹치는 2025-04-14~04-30 구간의 종가를 대조해
  수정주가 규약이 같은지 실측한다(불일치율 보고). 대조 없이 병합 금지.
- 상폐 커버리지: 08-25 실측으로 Alpaca가 상폐 종목 히스토리 보존 확인
  (SIVB/ATVI/SGEN — 마지막 거래일까지 정확). 백필 후 0행 종목 수를 보고한다.

사용:
  python tools/backfill_us_daily_from_alpaca.py --pilot        # 10종목 파일럿+경계 대조
  python tools/backfill_us_daily_from_alpaca.py                # 전체 (기본 2023-01-01~2025-04-30)
키: .env.alpaca. read-only 외부 조회 전용(live DB·주문 접근 없음).
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

import requests

ROOT = Path(__file__).resolve().parent.parent
LIVE_PRICE_DIR = ROOT / "data" / "price" / "us"
OUT_DIR = ROOT / "data" / "price_backfill_alpaca" / "us"
MANIFEST = ROOT / "data" / "price_backfill_alpaca" / "manifest_us.json"
ENV_FILE = ROOT / ".env.alpaca"
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
BATCH = 100


def _alpaca_headers() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return {"APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"], "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"]}


def _universe() -> list[str]:
    return sorted(p.stem.replace("us_", "", 1) for p in LIVE_PRICE_DIR.glob("us_*.csv"))


def _fetch_batch(headers: dict, symbols: list[str], start: str, end: str, adjustment: str) -> dict[str, list]:
    params = {
        "symbols": ",".join(symbols), "timeframe": "1Day",
        "start": f"{start}T00:00:00Z", "end": f"{end}T23:59:00Z",
        "feed": "sip", "limit": "10000", "adjustment": adjustment,
    }
    bars_by_symbol: dict[str, list] = {}
    token = ""
    while True:
        if token:
            params["page_token"] = token
        resp = requests.get(BARS_URL, headers=headers, params=params, timeout=60)
        if resp.status_code == 429:
            time.sleep(3)
            continue
        resp.raise_for_status()
        payload = resp.json()
        for sym, bars in (payload.get("bars") or {}).items():
            bars_by_symbol.setdefault(sym, []).extend(bars)
        token = payload.get("next_page_token") or ""
        if not token:
            break
    return bars_by_symbol


def _existing_close(ticker: str) -> dict[str, float]:
    path = LIVE_PRICE_DIR / f"us_{ticker}.csv"
    closes: dict[str, float] = {}
    if path.exists():
        with path.open(encoding="utf-8-sig") as fh:
            for row in csv.reader(fh):
                if len(row) >= 6 and row[0][:2] == "20":
                    try:
                        closes[row[0]] = float(row[4])
                    except ValueError:
                        continue
    return closes


def _boundary_check(ticker: str, bars: list, tol_pct: float = 0.5) -> tuple[int, int]:
    """겹치는 날짜의 종가 대조 — (일치, 비교표본)."""
    existing = _existing_close(ticker)
    match = total = 0
    for bar in bars:
        day = str(bar.get("t") or "")[:10]
        if day in existing:
            total += 1
            ours = existing[day]
            theirs = float(bar.get("c") or 0)
            if ours > 0 and abs(theirs / ours - 1.0) * 100.0 <= tol_pct:
                match += 1
    return match, total


def main() -> int:
    parser = argparse.ArgumentParser(description="Alpaca US 일봉 과거 백필")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2025-04-30", help="기존 CSV 시작(2025-04-14)과 겹치게 잡아 경계 대조")
    parser.add_argument("--adjustment", default="split", choices=["raw", "split", "dividend", "all"])
    parser.add_argument("--pilot", action="store_true", help="10종목만 (경계 대조 확인용)")
    args = parser.parse_args()

    universe = _universe()
    if args.pilot:
        universe = universe[:5] + ["AAPL", "MXL", "WIX", "NVDA", "TSLA"]
        universe = sorted(set(universe))
    print(f"대상 {len(universe)}종목 | {args.start}~{args.end} | adjustment={args.adjustment}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    headers = _alpaca_headers()

    done = empty = 0
    match_sum = compare_sum = 0
    for i in range(0, len(universe), BATCH):
        batch = universe[i:i + BATCH]
        bars_by_symbol = _fetch_batch(headers, batch, args.start, args.end, args.adjustment)
        for ticker in batch:
            bars = bars_by_symbol.get(ticker) or []
            if not bars:
                empty += 1
                continue
            m, c = _boundary_check(ticker, bars)
            match_sum += m
            compare_sum += c
            with (OUT_DIR / f"us_{ticker}.csv").open("w", encoding="utf-8", newline="") as out:
                writer = csv.writer(out)
                writer.writerow(["date", "open", "high", "low", "close", "volume"])
                for bar in bars:
                    writer.writerow([str(bar["t"])[:10], bar["o"], bar["h"], bar["l"], bar["c"], bar["v"]])
            done += 1
        print(f"  진행 {min(i + BATCH, len(universe))}/{len(universe)} (저장 {done} · 0행 {empty})")
    boundary_pct = (match_sum / compare_sum * 100.0) if compare_sum else 0.0
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({
        "source": "alpaca_sip_daily", "adjustment": args.adjustment,
        "window": [args.start, args.end], "tickers_written": done, "tickers_empty": empty,
        "boundary_close_match_pct": round(boundary_pct, 2), "boundary_compared": compare_sum,
        "boundary_tolerance_pct": 0.5,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "라이브 price CSV와 분리 보관. 병합은 경계 대조 검증+운영자 승인 후에만.",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장 {done}종목 | 0행(상장 전/심볼 부재) {empty}종목")
    print(f"경계 대조(2025-04 중첩, ±0.5%): {match_sum}/{compare_sum} = {boundary_pct:.1f}%")
    print(f"manifest: {MANIFEST}")
    if compare_sum and boundary_pct < 95.0:
        print("⚠️ 경계 불일치 높음 — adjustment 규약이 기존 CSV와 다르다. raw/all로 재시도해 비교할 것.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
