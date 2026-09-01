#!/usr/bin/env python3
"""그물 밖 '올랐다 내린' 패턴 인구조사 (2026-09-01).

운영자 질문: "후보가 잘 안 바뀌는 것 같다. 우리 후보에 없는 종목이 야후/알파카에
있는지 확인하고, 놓친 거라면 개선점이 있지 않겠나. 최근 2주간 10% 이상 올랐다
내린 애들 다 찾아서 분석해봐."

**왜 새 축인가**: full_market_net_census(08-27)는 **일간 -5% 급락 프로필**로
그물 밖을 셌고 "수집 갭 97%, 질은 낮은 방향"으로 끝났다. 그런데 우리 수집기는
day_losers(일간 급락)만 본다 — **며칠에 걸쳐 고점에서 되돌린 종목은 구조적으로
안 걸린다.** 하루 -5%를 안 찍고 5일에 걸쳐 -15% 빠지면 우리 레이더에 없다.
이 스크립트는 그 사각을 센다.

== 정의 (사전 고정) ==
  창: 최근 14거래일.
  runup    = 창 내 최저 종가 -> 그 이후 최고 종가 상승률
  drawdown = 그 최고점 -> 현재 종가 하락률
  '올랐다 내린' = runup >= +10% AND drawdown <= -10%
  (운영자 표현의 조작적 정의. 순서를 지킨다 — 오른 뒤 내린 것만 센다.)

== 판정 ==
그물 밖 종목이 많아도 **질**이 낮으면 개선 대상이 아니다(08-27과 같은 논리).
따라서 개수뿐 아니라 **놓친 종목의 계약 forward net**(TP12/SL25/D5 일봉 시뮬)을
함께 낸다. 우리 실제 후보 풀과 대조해 교집합/차집합을 보고, 차집합의 성과가
우리 풀보다 나은지로 판정한다. 나쁘면 "그물은 좁아도 정확하다"는 뜻이다.

**한계**: 생존편향(현재 상장 종목만), Alpaca 보통주 휴리스틱(ETF/펀드 이름
제외), MAX 하한 미적용. 관측 전용이며 라이브에 아무것도 반영하지 않는다.

사용: python tools/research_runup_drawdown_gap.py [--refresh]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from full_market_net_census import CACHE, collect  # noqa: E402

POOL_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
WINDOW = 14
RUNUP_MIN = 10.0
DRAW_MAX = -10.0
TP, SL, HOLD, FEE = 12.0, -25.0, 5, 0.48
BAND = (100.0, 500.0)


def pool_tickers() -> tuple[set, dict]:
    con = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True, timeout=10)
    try:
        pool = {str(r[0]).upper() for r in con.execute("SELECT DISTINCT ticker FROM candidate_pool_all")}
        sig = {str(r[0]).upper(): r[1] for r in con.execute(
            "SELECT ticker, COUNT(*) FROM signals GROUP BY ticker")}
    finally:
        con.close()
    return pool, sig


def contract_net(bars: list[dict], i: int) -> float | None:
    """신호일 i 다음 세션 시가 진입, TP12/SL25/D5 일봉 시뮬."""
    if i + 1 >= len(bars):
        return None
    entry = bars[i + 1].get("o")
    if not entry:
        return None
    win = bars[i + 1: i + 2 + HOLD]
    if len(win) < 2:
        return None
    for j, b in enumerate(win):
        hi = (b["h"] - entry) / entry * 100.0 if j > 0 else (b["c"] - entry) / entry * 100.0
        cp = (b["c"] - entry) / entry * 100.0
        if hi >= TP:
            return TP - FEE
        if cp <= SL:
            return cp - FEE
    return (win[-1]["c"] - entry) / entry * 100.0 - FEE


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="Alpaca 재수집")
    ap.add_argument("--start", default="2026-07-25")
    args = ap.parse_args()

    if args.refresh or not CACHE.exists():
        series = collect(args.start)
    else:
        series = json.loads(CACHE.read_text())
        print(f"캐시 재사용: {CACHE.name} ({len(series)}종목)")

    pool, sig_count = pool_tickers()
    print(f"우리 후보 풀 {len(pool)}종목 / 신호 발생 {len(sig_count)}종목\n")

    hits = []
    for sym, bars in series.items():
        bars = sorted((b for b in bars if b.get("c")), key=lambda x: x["t"])
        if len(bars) < WINDOW + 2:
            continue
        win = bars[-WINDOW:]
        closes = [b["c"] for b in win]
        lo_i = closes.index(min(closes))
        after = closes[lo_i:]
        if len(after) < 2:
            continue
        hi_rel = after.index(max(after))
        hi_i = lo_i + hi_rel
        runup = (closes[hi_i] / closes[lo_i] - 1) * 100.0
        draw = (closes[-1] / closes[hi_i] - 1) * 100.0
        if runup < RUNUP_MIN or draw > DRAW_MAX:
            continue
        b = win[hi_i]
        dvol = b["c"] * b.get("v", 0) / 1e6
        hits.append({
            "sym": sym, "runup": runup, "draw": draw,
            "peak_date": str(b["t"])[:10], "dvol_at_peak": dvol,
            "in_pool": sym in pool, "sig": sig_count.get(sym, 0),
            "cnet": contract_net(bars, len(bars) - 1 - (WINDOW - 1 - hi_i)),
        })

    print(f"=== '올랐다 내린' (runup>=+{RUNUP_MIN:.0f}% 후 drawdown<={DRAW_MAX:.0f}%) ===")
    print(f"최근 {WINDOW}거래일 창 | 전체 {len(series)}종목 중 {len(hits)}종목 해당\n")

    inp = [h for h in hits if h["in_pool"]]
    outp = [h for h in hits if not h["in_pool"]]
    print(f"  우리 후보 풀에 있던 것   {len(inp):4d}종목 ({100*len(inp)/max(1,len(hits)):.0f}%)")
    print(f"  풀 밖(놓친 것)          {len(outp):4d}종목 ({100*len(outp)/max(1,len(hits)):.0f}%)")
    band = [h for h in outp if BAND[0] <= h["dvol_at_peak"] <= BAND[1]]
    print(f"    그중 밴드(100~500M)   {len(band):4d}종목  <- 계약상 살 수 있었던 것\n")

    for label, group in (("풀 안", inp), ("풀 밖 전체", outp), ("풀 밖 x 밴드", band)):
        nets = [h["cnet"] for h in group if h["cnet"] is not None]
        if len(nets) < 5:
            print(f"  {label:14s} 정산 {len(nets)}건 (표본부족)")
            continue
        print(f"  {label:14s} 정산 {len(nets):3d}건 평균 {st.mean(nets):+6.2f}% "
              f"승률 {100*sum(1 for x in nets if x>0)/len(nets):3.0f}% "
              f"| 낙폭 중앙 {st.median([h['draw'] for h in group]):+.1f}%")

    print(f"\n[낙폭 상위 20 — 풀 밖 x 밴드]")
    for h in sorted(band, key=lambda x: x["draw"])[:20]:
        n = f"{h['cnet']:+6.2f}%" if h["cnet"] is not None else "  대기 "
        print(f"  {h['sym']:6s} 상승 {h['runup']:+6.1f}% → 낙폭 {h['draw']:+6.1f}% "
              f"(고점 {h['peak_date']}, {h['dvol_at_peak']:6.1f}M) 계약net {n}")

    print(f"\n[대조] 우리가 실제 신호를 낸 종목 중 이 패턴 해당: "
          f"{sum(1 for h in hits if h['sig'] > 0)}종목")
    return 0


if __name__ == "__main__":
    sys.exit(main())
