# -*- coding: utf-8 -*-
"""걸러내기 3축 워크포워드 (2026-09-10): B) 국면 상태 필터(진입 시점 지수 MA20·시장폭·idx_ret20, 시장별) C) arm 선택 변형(전기간/3개월 창/t≥2/t 가중).
북 = arm별 월 세션평균 등가중. 평가 2025-12~2026-08. c_* arm만."""
import sqlite3, json, statistics as st
from contextlib import closing
from collections import defaultdict
DB = 'file:data/shadow/virtual_books.db?mode=ro'
EVAL = ["2025-12"] + [f"2026-{m:02d}" for m in range(1, 9)]
def sm(rows):
    by = defaultdict(list)
    for d, v in rows: by[d].append(v)
    xs = [st.mean(v) for v in by.values()]
    if not xs: return None, 0, None
    t = st.mean(xs) / (st.pstdev(xs) / len(xs) ** 0.5) if len(xs) > 2 and st.pstdev(xs) else None
    return st.mean(xs), len(xs), t
with closing(sqlite3.connect(DB, uri=True, timeout=5)) as c:
    raw = c.execute("select strategy_id, session_date, net_pct, meta from trades where backfill=1 and status='CLOSED' and net_pct is not null and strategy_id like 'c_%'").fetchall()
T = []
for sid, d, net, meta in raw:
    m = json.loads(meta) if meta else {}; rg = m.get('regime') or {}
    T.append(dict(sid=sid, d=d, ym=d[:7], mk='KR' if sid.startswith('c_kr') else 'US', net=net,
                  above=rg.get('idx_above_ma20'), br=rg.get('breadth_down_pct'), ir=rg.get('idx_ret20')))
arms = sorted({t['sid'] for t in T})
def book(sel, arm_set=None):
    per = [sm([(t['d'], t['net']) for t in sel if t['sid'] == a])[0] for a in (arm_set or arms) if any(t['sid'] == a for t in sel)]
    return st.mean(per) if per else None
fm = lambda v: f"{v:+.2f}" if v is not None else "  ·  "

print("B) 국면 상태 필터 — 이전 데이터에서 상태별 세션평균>0인 상태만 유지(시장별), 월별 북 리프트")
def state(t, kind):
    if kind == 'ma20': return None if t['above'] is None else ('below' if not t['above'] else 'above')
    if kind == 'breadth': return None if t['br'] is None else ('wide' if t['br'] >= 60 else 'mid' if t['br'] >= 40 else 'narrow')
    if kind == 'idx20': return None if t['ir'] is None else ('neg' if t['ir'] < 0 else 'pos')
for kind in ('ma20', 'breadth', 'idx20'):
    for mk in ('KR', 'US'):
        L = []
        for M in EVAL:
            prior = [t for t in T if t['ym'] < M and t['mk'] == mk]; cur = [t for t in T if t['ym'] == M and t['mk'] == mk]
            keep = {s for s in {state(t, kind) for t in prior if state(t, kind)} if (sm([(t['d'], t['net']) for t in prior if state(t, kind) == s])[0] or 0) > 0}
            sel = [t for t in cur if state(t, kind) in keep]
            b0, b1 = book(cur), book(sel)
            if b0 is not None and b1 is not None: L.append((M, b0, b1, sorted(keep), len(sel) / max(1, len(cur))))
        d = [b1 - b0 for _, b0, b1, _, _ in L]
        print(f"  {kind:8s} {mk}: 리프트 평균 {st.mean(d):+.2f}pp 양수월 {sum(x>0 for x in d)}/{len(d)} | " + " ".join(f"{M[2:]}:{b1-b0:+.1f}{'/'.join(k)[:3]}" for M, b0, b1, k, _ in L))
    # 상태별 원 수치(전기간, 참고)
    for mk in ('KR', 'US'):
        print(f"     {mk} 전기간 상태별: " + " | ".join(f"{s} {fm(sm([(t['d'],t['net']) for t in T if t['mk']==mk and state(t,kind)==s])[0])}({sm([(t['d'],t['net']) for t in T if t['mk']==mk and state(t,kind)==s])[1]}세션)" for s in sorted({state(t, kind) for t in T if t['mk'] == mk and state(t, kind)})))

print("\nC) arm 선택 변형 — 유지 arm 북 vs 전체 북, 다음달 양수 적중")
def run(rule):
    L = []; hits = []
    for M in EVAL:
        cur = [t for t in T if t['ym'] == M]
        keep, w = set(), {}
        for a in arms:
            hist = [t for t in T if t['sid'] == a and t['ym'] < M]
            mu, n, tt = rule(hist, M)
            if mu is not None and mu > 0: keep.add(a); w[a] = max(0.0, tt or 0.0)
        b0 = book(cur); b1 = book(cur, keep)
        per = {a: sm([(t['d'], t['net']) for t in cur if t['sid'] == a])[0] for a in keep}
        per = {a: v for a, v in per.items() if v is not None}
        hits += [v > 0 for v in per.values()]
        bw = (sum(per[a] * w[a] for a in per) / sum(w[a] for a in per)) if per and sum(w[a] for a in per) > 0 else None
        if b0 is not None and b1 is not None: L.append((M, b0, b1, bw, len(keep)))
    return L, hits
def r_all(hist, M):
    mu, n, tt = sm([(t['d'], t['net']) for t in hist]); return (mu if n >= 20 and (tt or 0) >= 1 else None), n, tt
def r_all_t2(hist, M):
    mu, n, tt = sm([(t['d'], t['net']) for t in hist]); return (mu if n >= 20 and (tt or 0) >= 2 else None), n, tt
def r_3m(hist, M):
    y, m = int(M[:4]), int(M[5:]); lo = f"{y if m > 3 else y-1}-{(m-3-1)%12+1:02d}"
    mu, n, tt = sm([(t['d'], t['net']) for t in hist if t['ym'] >= lo]); return (mu if n >= 10 else None), n, tt
def r_6m(hist, M):
    y, m = int(M[:4]), int(M[5:]); lo = f"{y if m > 6 else y-1}-{(m-6-1)%12+1:02d}"
    mu, n, tt = sm([(t['d'], t['net']) for t in hist if t['ym'] >= lo]); return (mu if n >= 15 else None), n, tt
for name, rule in (("전기간 t≥1", r_all), ("전기간 t≥2", r_all_t2), ("최근 3개월 >0", r_3m), ("최근 6개월 >0", r_6m)):
    L, hits = run(rule)
    d = [b1 - b0 for _, b0, b1, _, _ in L]; bw = [x for _, _, _, x, _ in L if x is not None]
    print(f"  {name:12s}: 전체 {st.mean(b0 for _,b0,_,_,_ in L):+.2f} → 유지 {st.mean(b1 for _,_,b1,_,_ in L):+.2f} (t가중 {st.mean(bw):+.2f}) 리프트 양수월 {sum(x>0 for x in d)}/{len(d)} 적중 {sum(hits)}/{len(hits)}={100*sum(hits)/max(1,len(hits)):.0f}% | " + " ".join(f"{M[2:]}:{b1:+.1f}({k})" for M, _, b1, _, k in L))
