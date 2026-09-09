# -*- coding: utf-8 -*-
"""3층 전략 합산 시뮬레이션 (2026-09-10) — "그래서 월 얼마인가"에 대한 실측 답.

층 구성(09-10 재점검 결론):
  본체 코어 ETF 5자산 무헤지 등가중, 밴드 ±8%p 리밸런스 (ew_band8)          자금 300만
  위성 코스닥 패닉 오버나이트 v2(갭≤−1 or 종가≤−2.5) → 233740 종가→다음시가  자금 100만
  관찰 개별주 19+9 arm                                                     0원(판정 전)
전체 자본 432만, 나머지 32만은 현금 버퍼.

주의: 오버나이트 층은 신호일에만 자금을 쓴다(연 23회). 그 사이 자금은 놀거나 코어에 들어가 있다.
여기서는 **보수적으로** 위성 100만을 통째로 놀리는 것으로 잡는다(코어 겸용 이득 미반영).
가격은 data/analysis/core_etf_prices.csv(코어)와 스크래치패드 etf_233740.csv(위성).
사용: python tools/research/research_three_layer_portfolio.py <scratchpad_dir>
"""
import csv
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SP = Path(sys.argv[1])
CORE_CAP, SAT_CAP, TOTAL = 3_000_000.0, 1_000_000.0, 4_320_000.0
UNIV = ["379810", "360750", "411060", "305080", "069500"]
TAX_FREE = {"069500"}
FEE_SIDE, TAX = 0.00015, 0.154
BAND = 0.08
SAT_COST = 0.05


def load_core():
    rows = {}
    with (ROOT / "data" / "analysis" / "core_etf_prices.csv").open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            px = {}
            for t in UNIV:
                try:
                    px[t] = float(r[t])
                except (ValueError, TypeError, KeyError):
                    px = None
                    break
            if px:
                rows[r["date"]] = px
    return dict(sorted(rows.items()))


def load_sp(n):
    return sorted([(r['date'], float(r['open']), float(r['close'])) for r in csv.DictReader((SP / f'{n}.csv').open(encoding='utf-8'))])


CORE = load_core()
DATES = list(CORE)
KQ = load_sp('kosdaq_ohlc')
ETF = {d: (o, c) for d, o, c in load_sp('etf_233740')}
SIG = {}
for i in range(1, len(KQ)):
    d, o, c = KQ[i]
    pc = KQ[i - 1][2]
    SIG[d] = (o / pc - 1) * 100 <= -1.0 or (c / pc - 1) * 100 <= -2.5

# ── 본체: 밴드 ±8%p ────────────────────────────────────────────────────────
cash, hold, basis = CORE_CAP, {t: 0 for t in UNIV}, {t: 0.0 for t in UNIV}
core_nav, core_tax, core_reb = {}, 0.0, 0
first = True
for d in DATES:
    px = CORE[d]
    nav = cash + sum(hold[t] * px[t] for t in UNIV)
    do = first
    if not do and nav > 0:
        do = any(abs(hold[t] * px[t] / nav - 0.2) >= BAND for t in UNIV)
    if do:
        core_reb += 1
        tgt = {t: int(nav * 0.2 // px[t]) for t in UNIV}
        for t in UNIV:
            if tgt[t] < hold[t]:
                q = hold[t] - tgt[t]
                avg = basis[t] / hold[t] if hold[t] else px[t]
                tx = max(0.0, (px[t] - avg) * q) * (0 if t in TAX_FREE else TAX)
                cash += q * px[t] * (1 - FEE_SIDE) - tx
                core_tax += tx
                basis[t] -= avg * q
                hold[t] -= q
        for t in UNIV:
            if tgt[t] > hold[t]:
                q = tgt[t] - hold[t]
                while q > 0 and q * px[t] * (1 + FEE_SIDE) > cash:
                    q -= 1
                if q > 0:
                    cash -= q * px[t] * (1 + FEE_SIDE)
                    basis[t] += q * px[t]
                    hold[t] += q
        first = False
    core_nav[d] = cash + sum(hold[t] * px[t] for t in UNIV)

# ── 위성: 패닉 오버나이트(신호일 종가 매수 → 다음 시가 매도) ────────────────
sat_pnl = defaultdict(float)
sat_n = defaultdict(int)
edates = sorted(ETF)
for i in range(len(edates) - 1):
    d = edates[i]
    if d < DATES[0] or d > DATES[-1] or not SIG.get(d):
        continue
    r = (ETF[edates[i + 1]][0] / ETF[d][1] - 1) * 100 - SAT_COST
    gain = SAT_CAP * r / 100
    sat_pnl[edates[i + 1][:7]] += gain - max(0.0, gain) * TAX   # 233740은 파생형 15.4% 과세
    sat_n[edates[i + 1][:7]] += 1

months = sorted({d[:7] for d in DATES})
print(f"3층 합산 — 코어 {CORE_CAP:,.0f}(밴드 ±8%p) + 위성 {SAT_CAP:,.0f}(패닉 v2) / 전체 자본 {TOTAL:,.0f}")
print(f"기간 {DATES[0]} ~ {DATES[-1]}, {len(months)}개월\n")
print("월       코어 NAV     코어 손익   위성 손익(건)   합계 손익   자본 대비")
prev = CORE_CAP
tot_core = tot_sat = 0.0
rows = []
for m in months:
    ds = [d for d in DATES if d[:7] == m]
    if not ds:
        continue
    nav = core_nav[ds[-1]]
    cg = nav - prev
    sg = sat_pnl.get(m, 0.0)
    prev = nav
    tot_core += cg
    tot_sat += sg
    rows.append((m, nav, cg, sg, sat_n.get(m, 0)))
    print(f"{m}  {nav:>10,.0f}  {cg:>+10,.0f}  {sg:>+9,.0f}({sat_n.get(m,0):>2})  {cg+sg:>+10,.0f}  {100*(cg+sg)/TOTAL:>+7.2f}%")

n = len(rows)
tot = tot_core + tot_sat
pos = sum(1 for _, _, c, s, _ in rows if c + s > 0)
mm = [c + s for _, _, c, s, _ in rows]
print(f"\n합계 {n}개월: 코어 {tot_core:+,.0f} + 위성 {tot_sat:+,.0f} = {tot:+,.0f}")
print(f"  월 평균 {tot/n:+,.0f}원 = 자본 {TOTAL:,.0f} 대비 월 {100*tot/n/TOTAL:+.2f}% (연 {100*tot/n*12/TOTAL:+.1f}%)")
print(f"  양수월 {pos}/{n} · 최악월 {min(mm):+,.0f} · 최고월 {max(mm):+,.0f} · 월 표준편차 {st.pstdev(mm):,.0f}")
print(f"  코어 리밸런스 {core_reb}회 세금 {core_tax:,.0f}원 · 위성 신호 {sum(sat_n.values())}회(연 {sum(sat_n.values())/(n/12):.0f}회)")
print(f"\n주의: 이 기간({DATES[0][:7]}~)은 코어 자산이 동반 상승한 국면이다. 위성만 떼어 보면")
print(f"  위성 {tot_sat:+,.0f}원 / {n}개월 = 월 {tot_sat/n:+,.0f}원 (자본 대비 월 {100*tot_sat/n/TOTAL:+.2f}%) — 이쪽이 11년 검증된 부분이다.")
print(f"  코어는 자산배분이라 국면에 따라 −20%도 가능하다(밴드 arm maxDD −10.9%, 2022년 −15.3%).")
