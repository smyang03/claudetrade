# -*- coding: utf-8 -*-
"""KR 걸러내기 후보(가격 하한·IBS·dvol20) 심화 — 워크포워드 특성 스크린(1단계)에서 살아남은 것만 다시 본다 (2026-09-10).
A) 가격 문턱 3천/5천/1만 — c_kr arm별·월별 리프트(제외군 vs 유지군), 15개월 전체.
B) 중첩 워크포워드 변형: 월 M에서 이전 월 양수 비율 상위 2개 특성만 AND(최소 4개월 이력).
C) 부모 풀(xkr_fallen3·rise5·volspike·breakout, c_ arm과 다른 유니버스)에서 가격<5천 vs ≥5천 월별 세션 평균 — 반독립 재현.
"""
import sqlite3, json, statistics as st
from contextlib import closing
from collections import defaultdict
DB = 'file:data/shadow/virtual_books.db?mode=ro'
def sm(rows):
    by = defaultdict(list)
    for d, v in rows: by[d].append(v)
    return (st.mean([st.mean(v) for v in by.values()]) if by else None), len(by)
with closing(sqlite3.connect(DB, uri=True, timeout=5)) as c:
    raw = c.execute("select strategy_id, session_date, net_pct, meta from trades where backfill=1 and status='CLOSED' and net_pct is not null and (strategy_id like 'c_kr%' or strategy_id like 'xkr%')").fetchall()
T = []
for sid, d, net, meta in raw:
    m = json.loads(meta) if meta else {}; f = m.get('feat') or {}
    T.append(dict(sid=sid, d=d, ym=d[:7], net=net, price=f.get('price'), ibs=f.get('ibs'), dvol20=f.get('dvol20')))
C = [t for t in T if t['sid'].startswith('c_kr')]; X = [t for t in T if t['sid'].startswith('xkr')]
yms = sorted({t['ym'] for t in T if t['ym'] < '2026-09'})

print("A) c_kr 10 arm — 가격 문턱별 '유지군 − 제외군' (arm별 월 세션평균 등가중), 월별")
for thr in (3000, 5000, 10000):
    L = []
    for ym in yms:
        cur = [t for t in C if t['ym'] == ym and t['price'] is not None]
        per = []
        for a in sorted({t['sid'] for t in cur}):
            k, _ = sm([(t['d'], t['net']) for t in cur if t['sid'] == a and t['price'] >= thr])
            x, nx = sm([(t['d'], t['net']) for t in cur if t['sid'] == a and t['price'] < thr])
            if k is not None and x is not None and nx >= 3: per.append(k - x)
        if per: L.append((ym, st.mean(per), len(per)))
    xs = [v for _, v, _ in L]
    excl = sum(1 for t in C if t['price'] is not None and t['price'] < thr) / max(1, len(C))
    print(f"  ≥{thr:>5}: 평균 {st.mean(xs):+.2f}pp 양수월 {sum(x>0 for x in xs)}/{len(xs)} 제외비율 {100*excl:.0f}% | " + " ".join(f"{ym[2:]}:{v:+.1f}" for ym, v, _ in L))
print("  arm별(≥5천 유지 − 미만 제외, 15개월 풀):")
for a in sorted({t['sid'] for t in C}):
    k, nk = sm([(t['d'], t['net']) for t in C if t['sid'] == a and t['price'] is not None and t['price'] >= 5000])
    x, nx = sm([(t['d'], t['net']) for t in C if t['sid'] == a and t['price'] is not None and t['price'] < 5000])
    if k is not None and x is not None: print(f"    {a:28s} 유지 {k:+.2f}({nk}세션)  제외 {x:+.2f}({nx}세션)  차 {k-x:+.2f}")

print("\nC) 부모 풀 xkr_* — 가격 <5천 vs ≥5천 월별 세션평균(풀별 등가중)")
L = []
for ym in yms:
    cur = [t for t in X if t['ym'] == ym and t['price'] is not None]
    per = []
    for a in sorted({t['sid'] for t in cur}):
        k, _ = sm([(t['d'], t['net']) for t in cur if t['sid'] == a and t['price'] >= 5000])
        x, nx = sm([(t['d'], t['net']) for t in cur if t['sid'] == a and t['price'] < 5000])
        if k is not None and x is not None and nx >= 3: per.append((k, x))
    if per: L.append((ym, st.mean(k for k, _ in per), st.mean(x for _, x in per)))
print("  " + " ".join(f"{ym[2:]}:{k:+.1f}/{x:+.1f}" for ym, k, x in L))
d = [k - x for _, k, x in L]
print(f"  유지−제외 평균 {st.mean(d):+.2f}pp 양수월 {sum(v>0 for v in d)}/{len(d)} | 유지군 평균 {st.mean(k for _,k,_ in L):+.2f} 제외군 {st.mean(x for _,_,x in L):+.2f}")
for a in sorted({t['sid'] for t in X}):
    k, nk = sm([(t['d'], t['net']) for t in X if t['sid'] == a and t['price'] is not None and t['price'] >= 5000])
    x, nx = sm([(t['d'], t['net']) for t in X if t['sid'] == a and t['price'] is not None and t['price'] < 5000])
    print(f"    {a:16s} ≥5천 {k:+.2f}({nk})  <5천 {x:+.2f}({nx})  차 {k-x:+.2f}")

print("\nD) c_kr: 가격≥5천 AND IBS 유지분위(이전 데이터로 결정) 워크포워드 — 북(등가중) 전체 vs 필터")
EVAL = ["2025-12"] + [f"2026-{m:02d}" for m in range(1, 9)]
arms = sorted({t['sid'] for t in C})
def book(sel):
    per = [sm([(t['d'], t['net']) for t in sel if t['sid'] == a])[0] for a in arms if any(t['sid'] == a for t in sel)]
    return st.mean(per) if per else None
rows_out = []
for M in EVAL:
    prior = [t for t in C if t['ym'] < M and t['ibs'] is not None]; cur = [t for t in C if t['ym'] == M]
    vals = sorted(t['ibs'] for t in prior); lo, hi = vals[len(vals)//3], vals[2*len(vals)//3]
    kb = {b for b in range(3) if (sm([(t['d'], t['net']) for t in prior if (0 if t['ibs']<=lo else 1 if t['ibs']<=hi else 2) == b])[0] or 0) > 0}
    f1 = [t for t in cur if t['price'] is not None and t['price'] >= 5000]
    f2 = [t for t in f1 if t['ibs'] is not None and (0 if t['ibs']<=lo else 1 if t['ibs']<=hi else 2) in kb]
    b0, b1, b2 = book(cur), book(f1), book(f2)
    rows_out.append((b0, b1, b2))
    fm = lambda v: f"{v:+.2f}" if v is not None else "  ·  "
    print(f"  {M} 전체 {fm(b0)} | ≥5천 {fm(b1)} | ≥5천&IBS{sorted(kb)} {fm(b2)} | 유지 {100*len(f2)/max(1,len(cur)):.0f}%")
for i, lab in enumerate(("전체", "≥5천", "≥5천&IBS")):
    xs = [r[i] for r in rows_out if r[i] is not None]
    print(f"  {lab:9s} 9개월 평균 {st.mean(xs):+.2f} 양수월 {sum(x>0 for x in xs)}/{len(xs)}")
