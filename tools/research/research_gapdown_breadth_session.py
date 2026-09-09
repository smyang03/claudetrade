# -*- coding: utf-8 -*-
"""갭다운 시장폭 — 세션 지표 (2026-09-10, 강제매도 가설 3차).

2차 결과가 가설을 뒤집었다:
 - 혼자 갭다운(시장은 멀쩡) = 거래평균 −1.96, 승 47.2% → 정보 매도(악재). 사면 안 된다.
 - 다같이 갭다운 = 거래평균 +2.10, 승 60.1% → 시장 전체 강제 매도. 여기가 반등한다.
그리고 **종가 기준 시장폭(breadth_down_pct)은 이 둘을 못 가른다**(49.2 vs 47.3). 시가 갭 기준만 가른다.
→ 세션 지표 "그날 급락 풀에서 시가 갭 ≤−3%로 시작한 종목 비율"(신호일 종가에 확정, no-lookahead)로 사다리를 만든다.
진입은 다음 세션 시가. 계약 TP12/SL25/D7 KR 비용(원장 net_pct).
"""
import sqlite3, json, statistics as st
from contextlib import closing
from collections import defaultdict
DB = 'file:data/shadow/virtual_books.db?mode=ro'

def cell(rows, label, indent="  "):
    if not rows:
        print(f"{indent}{label:30s} n 0"); return
    byd = defaultdict(list); byt = defaultdict(list)
    for d, tk, v in rows: byd[d].append(v); byt[tk].append(v)
    sm = [st.mean(v) for v in byd.values()]; tm = [st.mean(v) for v in byt.values()]
    f = lambda xs: st.mean(xs) / (st.pstdev(xs) / len(xs) ** 0.5) if len(xs) > 2 and st.pstdev(xs) else float('nan')
    print(f"{indent}{label:30s} n {len(rows):6d} 세션 {len(sm):3d} 세션평균 {st.mean(sm):+6.2f} t {f(sm):+5.2f} | "
          f"거래평균 {st.mean(v for _,_,v in rows):+6.2f} | 종목 t {f(tm):+5.2f} | 승 {100*sum(v>0 for _,_,v in rows)/len(rows):4.1f}%")

with closing(sqlite3.connect(DB, uri=True, timeout=5)) as c:
    raw = c.execute("select session_date, ticker, net_pct, meta from trades where strategy_id='xkr_fallen3' and backfill=1 and status='CLOSED' and net_pct is not null").fetchall()
T = []
for d, tk, net, meta in raw:
    m = json.loads(meta) if meta else {}; f = m.get('feat') or {}; rg = m.get('regime') or {}
    if f.get('gap') is None: continue
    T.append(dict(d=d, tk=tk, v=net, gap=f['gap'], dvol=f.get('dvol') or 0, chg=f.get('chg'),
                  br=rg.get('breadth_down_pct'), above=rg.get('idx_above_ma20'), half='H1' if d < '2026-01-01' else 'H2'))
# 세션 지표: 그날 급락 풀에서 갭다운 ≤−3 비율 (신호일 종가 확정)
byd = defaultdict(list)
for t in T: byd[t['d']].append(t)
GR = {d: sum(1 for x in v if x['gap'] <= -3) / len(v) for d, v in byd.items()}
POOL_N = {d: len(v) for d, v in byd.items()}
for t in T: t['gr'] = GR[t['d']]
R = lambda L: [(t['d'], t['tk'], t['v']) for t in L]
print(f"xkr_fallen3 백필 {len(T)}건 / {len(byd)}세션 · 갭다운비율 중앙 {100*st.median(GR.values()):.1f}%\n")

print("[1] 갭다운 시장폭 사다리 — 전량 매수")
BINS = [(0, .05), (.05, .10), (.10, .20), (.20, .35), (.35, .60), (.60, 1.01)]
for lo, hi in BINS:
    cell(R([t for t in T if lo <= t['gr'] < hi]), f"갭다운비율 {100*lo:.0f}~{100*hi:.0f}%")
print("\n[2] 같은 사다리 — 그날 갭다운 종목만 매수")
for lo, hi in BINS:
    cell(R([t for t in T if lo <= t['gr'] < hi and t['gap'] <= -3]), f"갭다운비율 {100*lo:.0f}~{100*hi:.0f}% × 갭다운주")
print("\n[3] 문턱 후보 — 비율 ≥20% 세션")
for lab, fn in (("≥20% 전량", lambda t: t['gr'] >= .20),
                ("≥20% × 갭다운주", lambda t: t['gr'] >= .20 and t['gap'] <= -3),
                ("≥20% × 갭다운주 & 낙폭≤−5", lambda t: t['gr'] >= .20 and t['gap'] <= -3 and (t['chg'] or 0) <= -5),
                ("<20% 전량(대조)", lambda t: t['gr'] < .20),
                ("<20% × 갭다운주(대조)", lambda t: t['gr'] < .20 and t['gap'] <= -3)):
    cell(R([t for t in T if fn(t)]), lab)
print("\n[4] 종가 기준 시장폭과 비교 (같은 사다리)")
for lo, hi in ((0, 40), (40, 50), (50, 60), (60, 70), (70, 101)):
    cell(R([t for t in T if t['br'] is not None and lo <= t['br'] < hi]), f"종가 하락비율 {lo}~{hi}%")
print("\n[5] 갭다운비율 × 지수 MA20 (교차)")
for gk, gfn in (("비율≥20%", lambda t: t['gr'] >= .20), ("비율<20%", lambda t: t['gr'] < .20)):
    for ak, afn in (("MA20 아래", lambda t: t['above'] is False), ("MA20 위", lambda t: t['above'] is True)):
        cell(R([t for t in T if gfn(t) and afn(t) and t['gap'] <= -3]), f"{gk} × {ak} × 갭다운주")
print("\n[6] 반기·상위세션 제외 — 비율≥20% × 갭다운주")
sel = [t for t in T if t['gr'] >= .20 and t['gap'] <= -3]
for h in ("H1", "H2"): cell(R([t for t in sel if t['half'] == h]), h)
byd2 = defaultdict(list)
for t in sel: byd2[t['d']].append(t['v'])
top2 = {d for d, _ in sorted(((d, st.mean(v)) for d, v in byd2.items()), key=lambda x: -x[1])[:2]}
cell(R([t for t in sel if t['d'] not in top2]), "상위2세션 제외")
print("\n[7] 실행 형태 — 세션당 K=1/K=3 (거래대금 1위부터)")
for k in (1, 3):
    for lab, fn in (("비율≥20% × 갭다운주", lambda t: t['gr'] >= .20 and t['gap'] <= -3),
                    ("전량 무조건(대조)", lambda t: True)):
        bd = defaultdict(list)
        for t in T:
            if fn(t): bd[t['d']].append(t)
        picks = [x for v in bd.values() for x in sorted(v, key=lambda y: -y['dvol'])[:k]]
        cell(R(picks), f"K={k} {lab}")
