# -*- coding: utf-8 -*-
"""지수 ETF 오버나이트 — 생존 조건 5종의 겹침·독립 증분·최적 결합 (2026-09-10).

스윕(270셀) 생존 11개는 전부 "빠진 상태 오버나이트" 계열이고 인트라데이는 135셀 전부 탈락이었다.
생존 조건: gap≤−2 · chg≤−2.5 · cum5≤−5 · ma20_disc≤−5 · us_prev≤−1.5(전날 미국, KR 개장 전 확정).
질문 셋:
 1) 서로 같은 날인가(겹침)?
 2) 현행 v2(gap≤−1 or chg≤−2.5)에 무엇을 더하면 개선되고 무엇이 희석하는가?
 3) 강신호(gap≤−2)를 2배 크기로 두는 2단 사이징이 단일 크기보다 나은가?
사용: python tools/research/research_index_etf_rule_combine.py <scratchpad_dir>
"""
import csv
import itertools
import statistics as st
import sys
from pathlib import Path

SP = Path(sys.argv[1])
COST = 0.05


def load(n):
    return sorted([(r['date'], float(r['open']), float(r['high']), float(r['low']), float(r['close']))
                   for r in csv.DictReader((SP / f'{n}.csv').open(encoding='utf-8'))])


KQ = load('kosdaq_ohlc')
SPY = load('us_SPY')
us = [(d, (c / SPY[i - 1][4] - 1) * 100) for i, (d, _, _, _, c) in enumerate(SPY) if i]
F = {}
j = 0
for i in range(20, len(KQ)):
    d, o, h, lo, c = KQ[i]
    pc = KQ[i - 1][4]
    while j + 1 < len(us) and us[j + 1][0] < d:
        j += 1
    F[d] = {'gap': (o / pc - 1) * 100, 'chg': (c / pc - 1) * 100,
            'cum5': (c / KQ[i - 5][4] - 1) * 100,
            'ma20_disc': (c / st.mean(x[4] for x in KQ[i - 19:i + 1]) - 1) * 100,
            'us_prev': us[j][1] if us and us[j][0] < d else None}

COND = {
    'gap≤−1': lambda f: f['gap'] <= -1,
    'gap≤−2': lambda f: f['gap'] <= -2,
    'chg≤−2.5': lambda f: f['chg'] <= -2.5,
    'cum5≤−5': lambda f: f['cum5'] <= -5,
    'ma20≤−5': lambda f: f['ma20_disc'] <= -5,
    'usprev≤−1.5': lambda f: f['us_prev'] is not None and f['us_prev'] <= -1.5,
}


def series(tk):
    b = load(f'etf_{tk}')
    return sorted([(b[i][0], (b[i + 1][1] / b[i][4] - 1) * 100 - COST) for i in range(len(b) - 1) if b[i][0] in F])


def stats(sel, bm=None):
    xs = [r for _, r in sel]
    if not xs:
        return None
    t = st.mean(xs) / (st.pstdev(xs) / len(xs) ** 0.5) if len(xs) > 2 else float('nan')
    byy = {}
    for d, r in sel:
        byy.setdefault(d[:4], []).append(r)
    posy = sum(1 for y in byy if st.mean(byy[y]) > 0)
    n = len(sel)
    th = [st.mean([r for _, r in sel[a:b]]) for a, b in ((0, n // 3), (n // 3, 2 * n // 3), (2 * n // 3, n)) if b > a]
    return dict(n=n, mean=st.mean(xs), t=t, win=100 * sum(x > 0 for x in xs) / len(xs), posy=posy, ny=len(byy),
                th=th, tot=sum(xs), worst=min(xs))


def show(sel, label, indent="  "):
    s = stats(sel)
    if not s:
        print(f"{indent}{label:32s} n 0")
        return None
    print(f"{indent}{label:32s} n {s['n']:4d} 평균 {s['mean']:+.3f} t {s['t']:+5.2f} 승 {s['win']:4.1f}% "
          f"연도 {s['posy']}/{s['ny']:<2d} 3분할 " + " ".join(f"{x:+.2f}" for x in s['th']) + f" 누적 {s['tot']:+7.1f} 최악 {s['worst']:+6.2f}")
    return s


for tk in ('233740', '229200'):
    S = series(tk)
    print(f"\n{'='*110}\n{tk} — {S[0][0]}~{S[-1][0]}, 전체 {len(S)}회")
    print("\n[1] 조건별 단독 + 서로 겹침(자카드)")
    days = {k: {d for d, _ in S if fn(F[d])} for k, fn in COND.items()}
    for k in COND:
        show([(d, r) for d, r in S if d in days[k]], k)
    print("\n  겹침(자카드 %) — 행∩열 / 행∪열")
    print("  " + "".join(f"{k:>13s}" for k in COND))
    for a in COND:
        print(f"  {a:12s}" + "".join(f"{100*len(days[a]&days[b])/max(1,len(days[a]|days[b])):12.0f}%" for b in COND))

    print("\n[2] 현행 v2(gap≤−1 or chg≤−2.5)에 하나씩 추가")
    v2 = days['gap≤−1'] | days['chg≤−2.5']
    base = show([(d, r) for d, r in S if d in v2], "v2 현행")
    for k in ('cum5≤−5', 'ma20≤−5', 'usprev≤−1.5'):
        add = days[k] - v2
        show([(d, r) for d, r in S if d in add], f"  +{k} 순증분만(n={len(add)})")
        show([(d, r) for d, r in S if d in (v2 | days[k])], f"  v2 or {k}")

    print("\n[3] 3중 결합 후보")
    for combo in itertools.combinations(('gap≤−1', 'chg≤−2.5', 'cum5≤−5', 'ma20≤−5', 'usprev≤−1.5'), 3):
        u = set().union(*(days[c] for c in combo))
        show([(d, r) for d, r in S if d in u], " or ".join(combo))
    u_all = set().union(*(days[c] for c in ('gap≤−1', 'chg≤−2.5', 'cum5≤−5', 'ma20≤−5', 'usprev≤−1.5')))
    show([(d, r) for d, r in S if d in u_all], "5개 전부 OR")

    print("\n[4] 2단 사이징 — 강신호(gap≤−2)에 2배, 나머지 v2에 1배")
    strong = days['gap≤−2']
    w = [(d, r * (2 if d in strong else 1)) for d, r in S if d in v2]
    s1 = stats([(d, r) for d, r in S if d in v2])
    s2 = stats(w)
    print(f"    단일 크기 : 회당 {s1['mean']:+.3f} 누적 {s1['tot']:+.1f} t {s1['t']:+.2f}")
    print(f"    2단 사이징: 회당 {s2['mean']:+.3f} 누적 {s2['tot']:+.1f} t {s2['t']:+.2f} (강신호 {len(strong & v2)}회)")
    print(f"    → 노출 대비: 2단은 총노출 {len(v2)+len(strong & v2)}단위, 단일은 {len(v2)}단위. "
          f"단위당 {s2['tot']/(len(v2)+len(strong & v2)):+.3f} vs {s1['tot']/len(v2):+.3f}")

    print("\n[5] 2026 제외 재확인 — 상위 후보")
    S25 = [(d, r) for d, r in S if d[:4] <= '2025']
    for lab, sset in (("v2 현행", v2), ("v2 or usprev≤−1.5", v2 | days['usprev≤−1.5']),
                      ("v2 or cum5≤−5", v2 | days['cum5≤−5']), ("5개 전부 OR", u_all)):
        show([(d, r) for d, r in S25 if d in sset], lab + " (~2025)")
