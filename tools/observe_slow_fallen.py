#!/usr/bin/env python3
"""느린 급락(slow fallen) 스캐너 — 다일 완만 하락 공급 레인 관측 (2026-09-01 사전등록).

**가설**: 우리 스크리너는 하루 -5% 급락(day_losers)만 잡는다. 5거래일에 걸쳐
-12% 이상 빠졌지만 단일일 -5%를 안 찍은 종목은 구조적 사각이다. 기각된
'올랐다 내린'(투기 급등 붕괴, 09-01 인구조사)과 달리 **선행 급등 없는 순수
다일 하락**은 검정된 적이 없다. day_losers와 배타 조건이라 기존 레인과 겹치지
않는 독립 공급이다.

== 정의 (사전 고정) ==
  신호일 i: close[i]/close[i-5] - 1 <= -12%
           AND 창 내 각 일간 등락 > -5% (day_losers 배타)
           AND 신호일 거래대금(close x vol) 100~500M USD (밴드 동일)
           AND close >= $5
  진입: 다음 세션 시가. 계약: 가상 북 정본(TP12/SL25/D7·BE락).
  픽(가상 북): 5일 누적낙폭 깊은순 1건/일.

**데이터**: Alpaca 전시장 일봉(full_market_net_census.collect 재사용).
  자체 캐시(slow_fallen_market_cache.json)를 20시간 넘으면 리프레시.
  최초 시드는 8월 인구조사 캐시(full_market_scan_202608.json)를 복사.
  생존편향(현재 상장 종목만) 명시 — 절대값 판정에 쓰지 않고 가상 북 forward로만.

관측 전용, 라이브 무접촉. 원장: data/shadow/slow_fallen_shadow.jsonl (세션 멱등).
사용: python tools/observe_slow_fallen.py [--no-refresh]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

CACHE = ROOT / "data" / "analysis" / "slow_fallen_market_cache.json"
SEED = ROOT / "data" / "analysis" / "full_market_scan_202608.json"
LEDGER = ROOT / "data" / "shadow" / "slow_fallen_shadow.jsonl"

CUM5_LE = -12.0
DAILY_GT = -5.0
BAND_LO_USD, BAND_HI_USD = 100e6, 500e6
MIN_PRICE = 5.0
REFRESH_AGE_H = 20.0


def load_cache(allow_refresh: bool) -> dict:
    if not CACHE.exists() and SEED.exists():
        CACHE.write_text(SEED.read_text(), encoding="utf-8")
        print(f"[slow_fallen] 시드 복사: {SEED.name} → {CACHE.name}")
    stale = True
    if CACHE.exists():
        stale = (time.time() - CACHE.stat().st_mtime) > REFRESH_AGE_H * 3600
    if allow_refresh and stale:
        try:
            from full_market_net_census import collect
            start = (datetime.now(tz=timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%d")
            series = collect(start)
            CACHE.write_text(json.dumps(series), encoding="utf-8")
            print(f"[slow_fallen] Alpaca 리프레시 완료 ({len(series)}종목)")
            return series
        except Exception as exc:
            print(f"[slow_fallen] 리프레시 실패({str(exc)[:80]}) — 기존 캐시 사용")
    if not CACHE.exists():
        raise SystemExit("[slow_fallen] 캐시 없음 — .env.alpaca 확인 후 재실행")
    return json.loads(CACHE.read_text())


def recorded_sessions() -> set[str]:
    done: set[str] = set()
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                done.add(str(json.loads(line).get("session_date")))
            except ValueError:
                continue
    return done


def scan(series: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for sym, raw in series.items():
        b = sorted((x for x in raw if x.get("c")), key=lambda x: x["t"])
        closes = [float(x["c"]) for x in b]
        for i in range(5, len(b)):
            c = closes[i]
            if c < MIN_PRICE or closes[i - 5] <= 0:
                continue
            cum5 = 100.0 * (c / closes[i - 5] - 1.0)
            if cum5 > CUM5_LE:
                continue
            if any(100.0 * (closes[j] / closes[j - 1] - 1.0) <= DAILY_GT
                   for j in range(i - 4, i + 1) if closes[j - 1] > 0):
                continue  # day_losers 배타 — 단일 -5% 있으면 기존 레인 몫
            dvol = c * float(b[i].get("v") or 0)
            if not (BAND_LO_USD <= dvol < BAND_HI_USD):
                continue
            out[str(b[i]["t"])[:10]].append({
                "ticker": sym, "cum5": round(cum5, 2), "dvol_m": round(dvol / 1e6, 1),
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-refresh", action="store_true")
    args = ap.parse_args()
    series = load_cache(allow_refresh=not args.no_refresh)
    hits = scan(series)
    done = recorded_sessions()
    todo = [sd for sd in sorted(hits) if sd not in done]
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with LEDGER.open("a", encoding="utf-8") as fh:
        for sd in todo:
            cands = sorted(hits[sd], key=lambda c: c["cum5"])
            fh.write(json.dumps({"session_date": sd, "n": len(cands),
                                 "candidates": cands, "recorded_at": stamp},
                                ensure_ascii=False) + "\n")
            print(f"[slow_fallen] {sd} 후보 {len(cands)}건 (최심 {cands[0]['ticker']} {cands[0]['cum5']}%)")
    if not todo:
        print("[slow_fallen] 신규 세션 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
