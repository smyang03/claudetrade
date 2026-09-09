# -*- coding: utf-8 -*-
"""지수 ETF 갭 확장 규칙 정밀 검증 (2026-09-10). 연도별·기간 격리·비용·실행 변형·최악 사례.
현행 패닉 레인 규칙: 코스닥 종가 ≤−2.5% → 233740/229200 종가 매수 → 다음날 시가.
확장 후보: 코스닥 **시가 갭 ≤−1%** 또는 종가 ≤−2.5%.
사용: python tools/research/research_index_gap_etf_detail.py <scratchpad_dir>
"""
import csv, statistics as st, sys
from pathlib import Path
SP = Path(sys.argv[1])

def load(n):
    return sorted([(r['date'], float(r['open']), float(r['high']), float(r['low']), float(r['close']))
                   for r in csv.DictReader((SP / f'{n}.csv').open(encoding='utf-8'))])
KQ = load('kosdaq_ohlc')
S = {}
for i in range(20, len(KQ)):
    d, o, _, _, c = KQ[i]; pc = KQ[i - 1][4]
    S[d] = ((o / pc - 1) * 100, (c / pc - 1) * 100, c < st.mean(x[4] for x in KQ[i - 19:i + 1]))
ETF = {'233740': load('etf_233740'), '229200': load('etf_229200')}
RULES = {
    "A 현행 종가≤−2.5": lambda g, c, b: c <= -2.5,
    "B 갭≤−1": lambda g, c, b: g <= -1.0,
    "C 갭≤−1 or 종가≤−2.5": lambda g, c, b: g <= -1.0 or c <= -2.5,
    "D C & MA20 아래": lambda g, c, b: (g <= -1.0 or c <= -2.5) and b,
    "E 갭≤−2": lambda g, c, b: g <= -2.0,
}

def trades(tk, cond, exit_at='open', cost=0.05, start='2000', end='2100'):
    b = ETF[tk]; out = []
    for i in range(len(b) - 1):
        d = b[i][0]
        if not (start <= d[:4] <= end) or d not in S: continue
        g, c, bl = S[d]
        if not cond(g, c, bl): continue
        px = b[i + 1][1] if exit_at == 'open' else b[i + 1][4]
        out.append((d, (px / b[i][4] - 1) * 100 - cost))
    return out

def line(rows, label, indent="  "):
    if not rows: print(f"{indent}{label:26s} n 0"); return
    xs = [r for _, r in rows]
    t = st.mean(xs) / (st.pstdev(xs) / len(xs) ** 0.5) if len(xs) > 2 else float('nan')
    dd = st.mean(xs) / st.pstdev(xs) * (250 / (len(xs) / ((int(rows[-1][0][:4]) - int(rows[0][0][:4])) + 1))) ** 0 if st.pstdev(xs) else 0
    print(f"{indent}{label:26s} n {len(xs):4d} 평균 {st.mean(xs):+.3f} t {t:+5.2f} 승 {100*sum(x>0 for x in xs)/len(xs):4.1f}% "
          f"최악 {min(xs):+6.2f} 중앙 {st.median(xs):+.2f} 합 {sum(xs):+7.1f}%")

for tk in ('233740', '229200'):
    print(f"\n===== {tk} =====")
    print("[연도별 평균(%) / 건수]")
    yrs = sorted({d[:4] for d, _ in trades(tk, lambda *a: True)})
    hdr = "규칙".ljust(28) + "".join(y[2:].rjust(7) for y in yrs)
    print(hdr)
    for name, cond in RULES.items():
        T = trades(tk, cond); byy = {}
        for d, r in T: byy.setdefault(d[:4], []).append(r)
        row = name.ljust(28)
        for y in yrs:
            row += (f"{st.mean(byy[y]):+6.2f}" if y in byy else "     ·").rjust(7)
        pos = sum(1 for y in byy if st.mean(byy[y]) > 0)
        print(row + f"  → 양수 {pos}/{len(byy)}")
    print("\n[기간 격리 — 규칙 C(갭≤−1 or 종가≤−2.5)]")
    for lab, s, e in (("2016~2018", '2016', '2018'), ("2019~2021", '2019', '2021'), ("2022~2023", '2022', '2023'), ("2024~2026", '2024', '2026')):
        line(trades(tk, RULES["C 갭≤−1 or 종가≤−2.5"], start=s, end=e), lab)
    print("\n[전체 규칙 비교]")
    for name, cond in RULES.items(): line(trades(tk, cond), name)
    print("\n[비용 민감도 — 규칙 C]")
    for cst in (0.05, 0.10, 0.20, 0.30):
        line(trades(tk, RULES["C 갭≤−1 or 종가≤−2.5"], cost=cst), f"왕복 {cst}%")
    print("\n[출구 변형 — 규칙 C]")
    line(trades(tk, RULES["C 갭≤−1 or 종가≤−2.5"], exit_at='open'), "다음날 시가 매도")
    line(trades(tk, RULES["C 갭≤−1 or 종가≤−2.5"], exit_at='close'), "다음날 종가 매도")
    print("\n[최악 5건 — 규칙 C]")
    for d, r in sorted(trades(tk, RULES["C 갭≤−1 or 종가≤−2.5"]), key=lambda x: x[1])[:5]:
        g, c, bl = S[d]; print(f"    {d} 수익 {r:+6.2f}% (그날 갭 {g:+.2f} 종가 {c:+.2f} MA20{'아래' if bl else '위'})")
    print("[연속 손실 최장 / 누적 낙폭 — 규칙 C]")
    T = trades(tk, RULES["C 갭≤−1 or 종가≤−2.5"]); eq = 0; peak = 0; mdd = 0; streak = 0; worst = 0
    for _, r in T:
        eq += r; peak = max(peak, eq); mdd = min(mdd, eq - peak)
        streak = streak + 1 if r < 0 else 0; worst = max(worst, streak)
    print(f"    누적 {eq:+.1f}%p, 최대 낙폭 {mdd:+.1f}%p, 최장 연속손실 {worst}건, 연 {len(T)/11:.0f}회")
