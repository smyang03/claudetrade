# -*- coding: utf-8 -*-
"""지수 ETF 패닉 오버나이트 — US 이식 검정 (2026-09-10).
KR에서 통한 규칙(시가 갭 ≤−1% 또는 종가 ≤−2.5% → 그날 종가 매수 → 다음날 시가 매도)이 US에서도 되는지.
US는 오버나이트 자체에 알려진 양의 드리프트가 있으므로 **무조건 오버나이트 대비 초과**로만 판정한다.
비용 0.05%(ETF 왕복 근사, US는 수수료·스프레드 더 낮을 수 있음). 2005~2026.
사용: python tools/research/research_us_index_gap_etf.py <scratchpad_dir>
"""
import csv, statistics as st, sys
from pathlib import Path
SP = Path(sys.argv[1]); COST = 0.05

def load(n):
    return sorted([(r['date'], float(r['open']), float(r['high']), float(r['low']), float(r['close']))
                   for r in csv.DictReader((SP / f'{n}.csv').open(encoding='utf-8'))])

def sig(bars):
    out = {}
    for i in range(20, len(bars)):
        d, o, _, _, c = bars[i]; pc = bars[i - 1][4]
        if pc <= 0: continue
        out[d] = ((o / pc - 1) * 100, (c / pc - 1) * 100, c < st.mean(x[4] for x in bars[i - 19:i + 1]))
    return out

def run(tk, sig_tk, rules, label):
    bars = load(f'us_{tk}'); S = sig(load(f'us_{sig_tk}'))
    base = []
    for i in range(len(bars) - 1):
        d = bars[i][0]
        if d in S: base.append((d, (bars[i + 1][1] / bars[i][4] - 1) * 100 - COST))
    bx = [r for _, r in base]
    print(f"\n== {tk} (신호 {sig_tk}) {bars[0][0]}~{bars[-1][0]} · 무조건 오버나이트 {st.mean(bx):+.3f}%/일 n {len(bx)} ==")
    for name, cond in rules.items():
        sel = [(d, r) for d, r in base if cond(*S[d])]
        if len(sel) < 5: print(f"  {name:28s} n {len(sel)}"); continue
        xs = [r for _, r in sel]; ex = [r - st.mean(bx) for r in xs]
        t = st.mean(xs) / (st.pstdev(xs) / len(xs) ** 0.5)
        te = st.mean(ex) / (st.pstdev(ex) / len(ex) ** 0.5)
        byy = {}
        for d, r in sel: byy.setdefault(d[:4], []).append(r - st.mean(bx))
        pos = sum(1 for y in byy if st.mean(byy[y]) > 0)
        print(f"  {name:28s} n {len(xs):4d} 평균 {st.mean(xs):+.3f} t {t:+5.2f} | 초과 {st.mean(ex):+.3f} t {te:+5.2f} | "
              f"승 {100*sum(x>0 for x in xs)/len(xs):4.1f}% 최악 {min(xs):+6.2f} 초과양수연도 {pos}/{len(byy)}")

R = {
    "갭≤−0.5": lambda g, c, b: g <= -0.5, "갭≤−1": lambda g, c, b: g <= -1.0, "갭≤−2": lambda g, c, b: g <= -2.0,
    "종가≤−1.5": lambda g, c, b: c <= -1.5, "종가≤−2.5": lambda g, c, b: c <= -2.5, "종가≤−3": lambda g, c, b: c <= -3.0,
    "갭≤−1 or 종가≤−2.5 (규칙C)": lambda g, c, b: g <= -1.0 or c <= -2.5,
    "규칙C & MA20 아래": lambda g, c, b: (g <= -1.0 or c <= -2.5) and b,
    "규칙C & MA20 위": lambda g, c, b: (g <= -1.0 or c <= -2.5) and not b,
}
for tk in ('SPY', 'QQQ', 'IWM'):
    run(tk, tk, R, tk)
run('TQQQ', 'QQQ', R, 'TQQQ(3배, 신호는 QQQ)')

print("\n== 규칙 C 연도별 초과 — SPY/QQQ/IWM ==")
for tk in ('SPY', 'QQQ', 'IWM'):
    bars = load(f'us_{tk}'); S = sig(bars)
    base = [(d, (bars[i + 1][1] / bars[i][4] - 1) * 100 - COST) for i, (d, *_ ) in enumerate(bars[:-1]) if d in S]
    bm = st.mean(r for _, r in base)
    sel = [(d, r) for d, r in base if S[d][0] <= -1.0 or S[d][1] <= -2.5]
    byy = {}
    for d, r in sel: byy.setdefault(d[:4], []).append(r - bm)
    print(f"  {tk}: " + " ".join(f"{y[2:]}:{st.mean(v):+.2f}" for y, v in sorted(byy.items())))
