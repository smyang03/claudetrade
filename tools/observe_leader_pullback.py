#!/usr/bin/env python3
"""주도주 눌림목 스캐너 — 패밀리 B2 (2026-09-01 사전등록, Claude 제안).

**테제**: 급락 레인은 부러진 종목을 산다. 이 레인은 **건강한 추세주의 일상적
되돌림**을 산다 — 수확 대상은 조급한 차익실현 물량. 급락 레인이 노는
강세·횡보 국면에서 일하는 직교 패밀리다.

기각 목록과의 구분: 추격 금지(07-01)는 "뻗은 것을 사는 것"의 기각 — 여기는
뻗은 뒤 **쉬는 것**을 산다. 스크리너 리랭킹 역효과는 Path A 프롬프트 재정렬
문제로 별개.

== 정의 (사전 고정, 재탐색 금지) ==
  신호일 i 조건 (전시장 Alpaca 캐시, 130일 창):
    유동성: close >= $10 AND 신호일 거래대금 >= 100M USD
    추세:   60거래일 수익률 >= +30% AND close > MA50
    눌림:   20일 고점 대비 -10% <= drawdown <= -4%
    배타:   신호일 등락 > -5% (급락이면 fallen 레인 몫)
  진입: 다음 세션 시가. 픽: ret60 높은순(더 강한 추세 우선) 1건/일.
  계약(가상 북): TP8 / SL-8 / D7 / BE락4, 비용 0.50%.
  판정: forward만(백필은 배관 검증). 반증(사전등록): forward 30건에서
  세션 클러스터 평균 net <= 0 또는 같은 모집단 무작위 널 백분위 < 50.

관측 전용. 원장: data/shadow/leader_pullback_shadow.jsonl (세션 멱등).
사용: python tools/observe_leader_pullback.py
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "analysis" / "slow_fallen_market_cache.json"
LEDGER = ROOT / "data" / "shadow" / "leader_pullback_shadow.jsonl"

MIN_PRICE = 10.0
MIN_DVOL_USD = 100e6
RET60_GE = 30.0
DD20_LO, DD20_HI = -10.0, -4.0
DAILY_GT = -5.0


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
        n = len(b)
        for i in range(60, n):
            c = closes[i]
            if c < MIN_PRICE or closes[i - 60] <= 0:
                continue
            dvol = c * float(b[i].get("v") or 0)
            if dvol < MIN_DVOL_USD:
                continue
            ret60 = 100.0 * (c / closes[i - 60] - 1.0)
            if ret60 < RET60_GE:
                continue
            if i >= 1 and closes[i - 1] > 0 and 100.0 * (c / closes[i - 1] - 1.0) <= DAILY_GT:
                continue  # 급락은 fallen 몫
            ma50 = sum(closes[i - 49: i + 1]) / 50.0
            if c <= ma50:
                continue
            hi20 = max(closes[i - 19: i + 1])
            dd20 = 100.0 * (c / hi20 - 1.0)
            if not (DD20_LO <= dd20 <= DD20_HI):
                continue
            out[str(b[i]["t"])[:10]].append({
                "ticker": sym, "ret60": round(ret60, 1), "dd20": round(dd20, 2),
                "dvol_m": round(dvol / 1e6, 1),
            })
    return out


def main() -> int:
    if not CACHE.exists():
        print("[leader_pb] 캐시 없음 — observe_slow_fallen 먼저 실행")
        return 1
    series = json.loads(CACHE.read_text())
    cache_age_h = round((time.time() - CACHE.stat().st_mtime) / 3600.0, 1)
    hits = scan(series)
    done = recorded_sessions()
    todo = [sd for sd in sorted(hits) if sd not in done]
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with LEDGER.open("a", encoding="utf-8") as fh:
        for sd in todo:
            cands = sorted(hits[sd], key=lambda c: -c["ret60"])[:30]
            fh.write(json.dumps({"session_date": sd, "n": len(hits[sd]),
                                 "candidates": cands, "recorded_at": stamp,
                                 "cache_age_h": cache_age_h},
                                ensure_ascii=False) + "\n")
            print(f"[leader_pb] {sd} 후보 {len(hits[sd])}건 (1위 {cands[0]['ticker']} ret60 {cands[0]['ret60']}%)")
    if not todo:
        print("[leader_pb] 신규 세션 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
