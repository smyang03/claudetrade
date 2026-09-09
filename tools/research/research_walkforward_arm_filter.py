# -*- coding: utf-8 -*-
"""신규 c_* arm 걸러내기 규칙의 워크포워드 검증 (2026-09-10, 운영자 "걸러낼 방법을 검증하면서 해봐야지").

규칙(사전 고정): 월 M에서 M 이전 데이터만으로 arm 선택 — 세션 평균 >0 & 세션 ≥20 & t≥1(감도: t 조건 없음).
회피 필터(동전주·급등 중 내부자·개별 악재형·패닉 IBS≥75·삼성하이닉스)는 M 이전 데이터에서 제거군 세션 평균 <0일 때만 적용.
집계: arm별 월 세션 평균 → 그 달 데이터 있는 arm 등가중 = 북 수익률(거래 가중 아님). 평가 2025-12~2026-08.
누수 고지: 필터 후보 자체는 09-09 전체 창을 보고 고른 것. DB는 2025-06~2026-09 한 국면.
"""
import sqlite3, json, statistics as st, sys
from contextlib import closing
from collections import defaultdict
DB = 'file:data/shadow/virtual_books.db?mode=ro'
EVAL = [f"2025-{m:02d}" for m in (12,)] + [f"2026-{m:02d}" for m in range(1, 9)]
MIN_SESS, T_MIN = 20, float(sys.argv[1]) if len(sys.argv) > 1 else 1.0

def avoid(sid, kr, tk, f, rg):
    if kr and f.get('price') is not None and f['price'] < 5000: return '동전주'
    if 'insider' in sid and f.get('chg') is not None and f['chg'] >= 15: return '급등중내부자'
    if ('fallen' in sid or 'panic' in sid) and rg.get('breadth_down_pct') is not None and rg['breadth_down_pct'] < 40: return '개별악재형'
    if 'panic' in sid and f.get('ibs') is not None and f['ibs'] >= 75: return '패닉IBS75'
    if 'insider' in sid and tk in ('005930', '000660'): return '삼성하이닉스'
    return None

def sess_mean_t(rows):
    by = defaultdict(list)
    for d, v in rows: by[d].append(v)
    sm = [st.mean(v) for v in by.values()]
    if len(sm) < 2: return len(sm), (st.mean(sm) if sm else None), None
    sd = st.pstdev(sm)
    return len(sm), st.mean(sm), (st.mean(sm) / (sd / len(sm) ** 0.5) if sd else None)

with closing(sqlite3.connect(DB, uri=True, timeout=5)) as c:
    raw = c.execute("select strategy_id, session_date, ticker, net_pct, meta from trades where backfill=1 and status='CLOSED' and net_pct is not null and strategy_id like 'c_%'").fetchall()
T = []
for sid, d, tk, net, meta in raw:
    m = json.loads(meta) if meta else {}
    T.append((sid, d, d[:7], tk, net, avoid(sid, sid.startswith('c_kr'), tk, m.get('feat') or {}, m.get('regime') or {})))
arms = sorted({t[0] for t in T})
print(f"대상 arm {len(arms)}개 · 거래 {len(T)} · 평가월 {EVAL[0]}~{EVAL[-1]} · 규칙: 세션평균>0 & 세션≥{MIN_SESS} & t≥{T_MIN}")
print("월     | 데이터 arm | 유지 arm | 북 전체(등가중) | 북 유지 | 북 유지+필터 | 필터 적용 | 유지 arm 중 다음달 양수")
agg = defaultdict(list); hits = []
for M in EVAL:
    prior = [t for t in T if t[2] < M]; cur = [t for t in T if t[2] == M]
    keep, filt_on = set(), set()
    for a in arms:
        n, mu, tt = sess_mean_t([(t[1], t[4]) for t in prior if t[0] == a])
        if n >= MIN_SESS and mu is not None and mu > 0 and (T_MIN <= 0 or (tt is not None and tt >= T_MIN)): keep.add(a)
    for r in ('동전주', '급등중내부자', '개별악재형', '패닉IBS75', '삼성하이닉스'):
        n, mu, _ = sess_mean_t([(t[1], t[4]) for t in prior if t[5] == r])
        if n >= 10 and mu is not None and mu < 0: filt_on.add(r)
    def book(sel, arms_set):
        per = []
        for a in arms_set:
            rows = [(t[1], t[4]) for t in sel if t[0] == a]
            if rows: per.append(sess_mean_t(rows)[1])
        return (st.mean(per) if per else None), len(per)
    b_all, n_all = book(cur, arms)
    b_keep, n_keep = book(cur, keep)
    b_filt, _ = book([t for t in cur if t[5] not in filt_on], keep)
    hit = [book(cur, {a})[0] for a in keep if any(t[0] == a for t in cur)]
    hits += [h > 0 for h in hit if h is not None]
    for k, v in (('all', b_all), ('keep', b_keep), ('filt', b_filt)):
        if v is not None: agg[k].append(v)
    f = lambda v: f"{v:+.2f}" if v is not None else "  · "
    print(f"{M} | {n_all:>9} | {len(keep):>7} | {f(b_all):>13} | {f(b_keep):>7} | {f(b_filt):>11} | {','.join(sorted(filt_on)) or '-'} | {sum(h>0 for h in hit if h is not None)}/{len([h for h in hit if h is not None])}")
def mt(xs): return f"{st.mean(xs):+.2f} (t {st.mean(xs)/(st.pstdev(xs)/len(xs)**0.5):+.1f}, 양수월 {sum(x>0 for x in xs)}/{len(xs)})" if len(xs) > 1 and st.pstdev(xs) else "n/a"
print(f"\n월 등가중 평균 — 전체 {mt(agg['all'])} | 유지 {mt(agg['keep'])} | 유지+필터 {mt(agg['filt'])}")
print(f"유지 arm의 다음달 양수 적중 {sum(hits)}/{len(hits)} = {100*sum(hits)/max(1,len(hits)):.0f}%")
