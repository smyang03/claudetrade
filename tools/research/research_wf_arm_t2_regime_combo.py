# -*- coding: utf-8 -*-
"""arm t≥2 선택 규칙 심화 (2026-09-10): 월별 유지 arm 목록·arm별 기여, 26-07 −7.0 분해, KR MA20 아래 게이트 결합, 월 세션≥3 arm만 집계 변형, 현재(09-10) 규칙 적용 시 유지 arm."""
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
    raw = c.execute("select strategy_id, session_date, net_pct, meta, backfill, status from trades where net_pct is not null and strategy_id like 'c_%'").fetchall()
    bd = {r[0]: r for r in c.execute("select strategy_id, open_n, open_mtm_krw, realized_pnl_krw from book_daily where asof=(select max(asof) from book_daily)")}
T = []
for sid, d, net, meta, bf, stt in raw:
    m = json.loads(meta) if meta else {}; rg = m.get('regime') or {}
    T.append(dict(sid=sid, d=d, ym=d[:7], mk='KR' if sid.startswith('c_kr') else 'US', net=net, above=rg.get('idx_above_ma20'), bf=bf, closed=stt == 'CLOSED'))
B = [t for t in T if t['bf'] and t['closed']]
arms = sorted({t['sid'] for t in B})
fm = lambda v: f"{v:+.2f}" if v is not None else "  ·  "

print("1) KR arm별 지수 MA20 위/아래 세션평균(전기간 백필)")
for a in arms:
    if not a.startswith('c_kr'): continue
    ab = sm([(t['d'], t['net']) for t in B if t['sid'] == a and t['above'] is True]); bl = sm([(t['d'], t['net']) for t in B if t['sid'] == a and t['above'] is False])
    print(f"   {a:28s} 위 {fm(ab[0])}({ab[1]:>3}세션)  아래 {fm(bl[0])}({bl[1]:>3}세션)")

def keep_t2(M, tmin=2.0):
    out = {}
    for a in arms:
        mu, n, tt = sm([(t['d'], t['net']) for t in B if t['sid'] == a and t['ym'] < M])
        if n >= 20 and mu is not None and mu > 0 and (tt or 0) >= tmin: out[a] = (mu, n, tt)
    return out
def book(sel, arm_set, min_sess=1):
    per = {}
    for a in arm_set:
        mu, n, _ = sm([(t['d'], t['net']) for t in sel if t['sid'] == a])
        if mu is not None and n >= min_sess: per[a] = (mu, n)
    return (st.mean(v[0] for v in per.values()) if per else None), per

print("\n2) t≥2 규칙 — 월별 유지 arm과 그 달 실적(세션평균/세션수)")
res = defaultdict(list)
for M in EVAL:
    keep = keep_t2(M); cur = [t for t in B if t['ym'] == M]
    b_all, _ = book(cur, arms); b_keep, per = book(cur, keep); b_keep3, per3 = book(cur, keep, 3)
    # KR MA20 아래 게이트 결합: KR arm은 아래 상태 거래만, US는 그대로
    gated = [t for t in cur if t['mk'] == 'US' or t['above'] is False]
    b_gate, _ = book(gated, keep)
    for k, v in (('all', b_all), ('keep', b_keep), ('keep3', b_keep3), ('gate', b_gate)):
        if v is not None: res[k].append(v)
    print(f"  {M} 전체 {fm(b_all)} | 유지 {fm(b_keep)} | 유지(월≥3세션) {fm(b_keep3)} | 유지+KR아래게이트 {fm(b_gate)}")
    print("        " + "  ".join(f"{a.replace('c_','')}:{v[0]:+.1f}/{v[1]}" for a, v in sorted(per.items())))
for k, lab in (('all', '전체'), ('keep', '유지 t≥2'), ('keep3', '유지·월≥3세션'), ('gate', '유지+KR MA20아래')):
    xs = res[k]; print(f"  {lab:14s} 평균 {st.mean(xs):+.2f} 양수월 {sum(x>0 for x in xs)}/{len(xs)} 최악 {min(xs):+.2f}")

print("\n3) 지금(09-10) 규칙 적용 — 전기간 백필로 t≥2인 arm, 포워드 상태")
keep_now = keep_t2('2026-09')
for a, (mu, n, tt) in sorted(keep_now.items(), key=lambda x: -x[1][2]):
    fw = [t for t in T if t['sid'] == a and not t['bf']]
    r = bd.get(a)
    print(f"   {a:28s} 백필 {mu:+.2f} {n}세션 t {tt:.1f} | 포워드 열림 {r[1] if r else 0} MTM {((r[2] or 0)/1e4 if r else 0):+.1f}만 실현 {((r[3] or 0)/1e4 if r else 0):+.1f}만")
print("   미유지:", ", ".join(a.replace('c_', '') for a in arms if a not in keep_now))
