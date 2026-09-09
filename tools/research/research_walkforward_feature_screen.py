# -*- coding: utf-8 -*-
"""신규 c_* arm 거래를 '이길 것/질 것'으로 가르는 특성을 워크포워드로 찾는다 (2026-09-10).

각 월 M: M 이전 데이터만으로 특성 f의 3분위 경계(시장별)와 분위별 세션 평균을 구해, 세션 평균>0인 분위만 '유지'.
M에서 유지 거래의 북 수익률(arm별 월 세션 평균 등가중) − 전체 북 = 그 달 리프트. 특성별로 9개월 리프트의 평균·양수월 수·유지 비율을 낸다.
2단계(중첩 워크포워드): 월 M에서 그 이전 월들의 리프트가 2/3 이상 양수였던 특성만 골라 AND로 결합 → M 평가. 특성 선택도 사후가 아니다.
"""
import sqlite3, json, statistics as st, sys
from contextlib import closing
from collections import defaultdict
DB = 'file:data/shadow/virtual_books.db?mode=ro'
EVAL = ["2025-12"] + [f"2026-{m:02d}" for m in range(1, 9)]
FEATS = ['price','chg','gap','dvol','dvol20','vol_spike','ibs','rv20','mom20','from_high20','ma20_disc','cum5','min1_in5','ret60','max21','down_streak','idx_ret20','breadth_down_pct']
MKT = sys.argv[1] if len(sys.argv) > 1 else 'ALL'   # KR / US / ALL

with closing(sqlite3.connect(DB, uri=True, timeout=5)) as c:
    raw = c.execute("select strategy_id, session_date, ticker, net_pct, meta from trades where backfill=1 and status='CLOSED' and net_pct is not null and strategy_id like 'c_%'").fetchall()
T = []
for sid, d, tk, net, meta in raw:
    mk = 'KR' if sid.startswith('c_kr') else 'US'
    if MKT != 'ALL' and mk != MKT: continue
    m = json.loads(meta) if meta else {}
    f = dict(m.get('feat') or {}); f.update({k: (m.get('regime') or {}).get(k) for k in ('idx_ret20', 'breadth_down_pct')})
    T.append(dict(sid=sid, d=d, ym=d[:7], mk=mk, net=net, f=f))
arms = sorted({t['sid'] for t in T})
print(f"[{MKT}] arm {len(arms)} 거래 {len(T)} 평가월 {EVAL[0]}~{EVAL[-1]}")

def sess_mean(rows):
    by = defaultdict(list)
    for d, v in rows: by[d].append(v)
    return (st.mean([st.mean(v) for v in by.values()]) if by else None), len(by)
def book(sel):
    per = []
    for a in arms:
        rows = [(t['d'], t['net']) for t in sel if t['sid'] == a]
        if rows: per.append(sess_mean(rows)[0])
    return st.mean(per) if per else None
def q3(vals):
    s = sorted(vals); n = len(s)
    return s[n // 3], s[2 * n // 3]
def bucket(v, lo, hi): return 0 if v <= lo else (1 if v <= hi else 2)

def keep_set(prior, f):
    """시장별 3분위 경계 + 유지 분위 → (mk → (lo,hi,keep_buckets))."""
    out = {}
    for mk in ('KR', 'US'):
        vals = [t['f'].get(f) for t in prior if t['mk'] == mk and t['f'].get(f) is not None]
        if len(vals) < 300: continue
        lo, hi = q3(vals); kb = set()
        for b in range(3):
            rows = [(t['d'], t['net']) for t in prior if t['mk'] == mk and t['f'].get(f) is not None and bucket(t['f'][f], lo, hi) == b]
            mu, ns = sess_mean(rows)
            if ns >= 30 and mu is not None and mu > 0: kb.add(b)
        out[mk] = (lo, hi, kb)
    return out
def passes(t, f, ks):
    if t['mk'] not in ks: return True           # 경계 못 만들면 필터 없음
    v = t['f'].get(f)
    if v is None: return False
    lo, hi, kb = ks[t['mk']]
    return bucket(v, lo, hi) in kb

lift = defaultdict(dict); kept_frac = defaultdict(list); base = {}
for M in EVAL:
    prior = [t for t in T if t['ym'] < M]; cur = [t for t in T if t['ym'] == M]
    b_all = book(cur); base[M] = b_all
    for f in FEATS:
        ks = keep_set(prior, f)
        sel = [t for t in cur if passes(t, f, ks)]
        b = book(sel)
        if b is not None and b_all is not None:
            lift[f][M] = b - b_all; kept_frac[f].append(len(sel) / max(1, len(cur)))
print("\n1단계 — 특성별 워크포워드 리프트(유지 북 − 전체 북, pp)")
print("특성            | 평균 리프트 | 양수월 | 유지 비율 | 월별")
rank = []
for f in FEATS:
    L = lift[f]
    if len(L) < 6: continue
    xs = [L[M] for M in EVAL if M in L]
    pos = sum(x > 0 for x in xs)
    rank.append((pos, st.mean(xs), f))
    print(f"{f:15s} | {st.mean(xs):+10.2f} | {pos:>2}/{len(xs)} | {100*st.mean(kept_frac[f]):5.0f}% | " + " ".join(f"{x:+.1f}" for x in xs))
rank.sort(reverse=True)

print("\n2단계 — 중첩 워크포워드: 월 M에서 이전 월 리프트 ≥2/3 양수인 특성만 AND 결합")
print("월     | 전체 북 | 선택 특성 | 결합 북 | 리프트 | 유지 비율")
comb = []
for i, M in enumerate(EVAL):
    if i < 3: continue
    prev = EVAL[:i]
    chosen = [f for f in FEATS if len([p for p in prev if p in lift[f]]) >= 3 and sum(lift[f][p] > 0 for p in prev if p in lift[f]) / len([p for p in prev if p in lift[f]]) >= 2 / 3]
    prior = [t for t in T if t['ym'] < M]; cur = [t for t in T if t['ym'] == M]
    kss = {f: keep_set(prior, f) for f in chosen}
    sel = [t for t in cur if all(passes(t, f, kss[f]) for f in chosen)]
    b = book(sel); b_all = base[M]
    if b is not None and b_all is not None:
        comb.append((b_all, b))
        print(f"{M} | {b_all:+7.2f} | {','.join(chosen) or '-'} | {b:+7.2f} | {b-b_all:+6.2f} | {100*len(sel)/max(1,len(cur)):4.0f}%")
if comb:
    A = [a for a, _ in comb]; B = [b for _, b in comb]
    print(f"결합 {len(comb)}개월: 전체 {st.mean(A):+.2f} → 결합 {st.mean(B):+.2f}, 리프트 양수월 {sum(b>a for a,b in comb)}/{len(comb)}")
