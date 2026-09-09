# -*- coding: utf-8 -*-
"""코어 ETF 북 — 리밸런스 주기·세금 드래그 검정 (2026-09-10).

본체 층(자금 250~300만)의 유일한 조정 손잡이는 리밸런스 규칙이다. 현행은 월 1회 무조건 등가중 복귀.
3.7년 세금 10.2만(자본 260만의 3.9%)은 결코 작지 않다 — 파생형 ETF 매매차익 15.4%(069500은 국내주식형이라 비과세).
질문: 월 1회가 최선인가, 아니면 분기/연/밴드가 세금 후 더 나은가. 매수 후 무리밸런스와도 비교한다.

계약: 자본 260만, 정수주, 수수료 편도 0.015%, 파생형 매매차익 15.4%(실현 시), 069500 비과세.
가격: data/analysis/core_etf_prices.csv (코어 북 도구가 쓰는 캐시와 동일). 5자산 모두 값이 있는 날부터.
"""
import csv
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "analysis" / "core_etf_prices.csv"
UNIV = ["379810", "360750", "411060", "305080", "069500"]
TAX_FREE = {"069500"}
CAPITAL = 2_600_000.0
FEE_SIDE = 0.00015
TAX = 0.154


def load():
    rows = {}
    with CACHE.open(encoding="utf-8-sig") as fh:
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


PX = load()
DATES = list(PX)
print(f"코어 5자산 동시 가격 {DATES[0]} ~ {DATES[-1]} ({len(DATES)}일)")


def rebalance_days(mode):
    """리밸런스 후보일 — 월/분기/연 첫 거래일. 밴드·보유는 별도 처리."""
    out, seen = [], set()
    for d in DATES:
        k = {"M": d[:7], "Q": f"{d[:4]}Q{(int(d[5:7])-1)//3}", "Y": d[:4]}.get(mode)
        if k and k not in seen:
            seen.add(k)
            out.append(d)
    return out


def simulate(mode, band=None, label=""):
    """mode: M/Q/Y/HOLD. band: 목표 대비 이탈 pp(예 0.05)이면 그날 리밸런스."""
    cash, hold, cost_basis = CAPITAL, {t: 0 for t in UNIV}, {t: 0.0 for t in UNIV}
    fees = taxes = 0.0
    rdays = set(rebalance_days(mode)) if mode in ("M", "Q", "Y") else set()
    navs, first = [], True
    turnover = 0.0
    for d in DATES:
        px = PX[d]
        nav = cash + sum(hold[t] * px[t] for t in UNIV)
        do = first or (d in rdays)
        if band is not None and not do and nav > 0:
            w = {t: hold[t] * px[t] / nav for t in UNIV}
            do = any(abs(w[t] - 0.2) >= band for t in UNIV)
        if do:
            tgt = {t: int(nav * 0.2 // px[t]) for t in UNIV}
            # 매도 먼저(세금·현금 확보)
            for t in UNIV:
                if tgt[t] < hold[t]:
                    q = hold[t] - tgt[t]
                    gross = q * px[t]
                    avg = cost_basis[t] / hold[t] if hold[t] else px[t]
                    gain = (px[t] - avg) * q
                    fee = gross * FEE_SIDE
                    tx = max(0.0, gain) * TAX if t not in TAX_FREE else 0.0
                    cash += gross - fee - tx
                    fees += fee
                    taxes += tx
                    turnover += gross
                    cost_basis[t] -= avg * q
                    hold[t] -= q
            for t in UNIV:
                if tgt[t] > hold[t]:
                    q = tgt[t] - hold[t]
                    cost = q * px[t] * (1 + FEE_SIDE)
                    while q > 0 and cost > cash:
                        q -= 1
                        cost = q * px[t] * (1 + FEE_SIDE)
                    if q > 0:
                        cash -= cost
                        fees += q * px[t] * FEE_SIDE
                        turnover += q * px[t]
                        cost_basis[t] += q * px[t]
                        hold[t] += q
            first = False
        navs.append((d, cash + sum(hold[t] * px[t] for t in UNIV)))
    final = navs[-1][1]
    # 미실현 이익에 대한 이연 세금(청산 가정) — 규칙 간 공정 비교
    px = PX[DATES[-1]]
    deferred = sum(max(0.0, hold[t] * px[t] - cost_basis[t]) * TAX for t in UNIV if t not in TAX_FREE)
    peak = 0.0
    mdd = 0.0
    for _, v in navs:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    yrs = (int(DATES[-1][:4]) - int(DATES[0][:4])) + (int(DATES[-1][5:7]) - int(DATES[0][5:7])) / 12
    cagr = (final / CAPITAL) ** (1 / yrs) - 1 if yrs > 0 else 0
    aft = final - deferred
    print(f"  {label:22s} 최종 {final:>10,.0f} CAGR {100*cagr:5.2f}% maxDD {100*mdd:6.2f}% | "
          f"수수료 {fees:>7,.0f} 실현세 {taxes:>8,.0f} 이연세 {deferred:>8,.0f} | 청산후 {aft:>10,.0f} "
          f"({100*(aft/CAPITAL-1):+6.1f}%) 회전 {turnover/CAPITAL:5.2f}배")
    return dict(final=final, after=aft, taxes=taxes, deferred=deferred, mdd=mdd, cagr=cagr)


print("\n[리밸런스 규칙 비교] 260만 · 정수주 · 수수료 0.015%/편도 · 파생형 15.4%(069500 비과세)")
res = {}
res["월"] = simulate("M", label="월 1회(현행)")
res["분기"] = simulate("Q", label="분기 1회")
res["연"] = simulate("Y", label="연 1회")
res["밴드5"] = simulate(None, band=0.05, label="밴드 ±5%p")
res["밴드3"] = simulate(None, band=0.03, label="밴드 ±3%p")
res["밴드8"] = simulate(None, band=0.08, label="밴드 ±8%p")
res["보유"] = simulate("HOLD", label="매수 후 무리밸런스")

best = max(res.items(), key=lambda x: x[1]["after"])
cur = res["월"]
print(f"\n  현행(월) 청산후 {cur['after']:,.0f} → 최선({best[0]}) {best[1]['after']:,.0f} "
      f"차이 {best[1]['after']-cur['after']:+,.0f}원 ({100*(best[1]['after']-cur['after'])/CAPITAL:+.2f}%p of 자본)")

print("\n[개별 자산 단독 보유 대조 — 같은 기간]")
for t in UNIV:
    p0, p1 = PX[DATES[0]][t], PX[DATES[-1]][t]
    q = int(CAPITAL // p0)
    gain = q * (p1 - p0)
    tx = max(0.0, gain) * (0 if t in TAX_FREE else TAX)
    print(f"  {t} 단독 {100*(p1/p0-1):+7.2f}% (세후 {100*((q*p1-tx)/(q*p0)-1):+7.2f}%)")

print("\n[구간별 — 리밸런스 규칙 상위 3종]")
for lab, mode, band in (("월 1회", "M", None), ("연 1회", "Y", None), ("무리밸런스", "HOLD", None)):
    yrs = sorted({d[:4] for d in DATES})
    row = f"  {lab:10s}"
    for y in yrs:
        sub = [d for d in DATES if d[:4] == y]
        if len(sub) < 60:
            row += f" {y[2:]}:  ·  "
            continue
        g = PX[sub[-1]]
        s0 = PX[sub[0]]
        eq = st.mean(g[t] / s0[t] - 1 for t in UNIV)
        row += f" {y[2:]}:{100*eq:+6.1f}"
    print(row + "   (등가중 단순 수익, 세전)")
