# -*- coding: utf-8 -*-
"""강제 매도 가설 1차 검정 (2026-09-10).

가설: 급락 매수의 승패를 가르는 것은 국면이 아니라 **판 사람이 누구인가**다.
 - 강제 매도(반대매매·신용청산·마진콜)는 정보 없는 매도 → 반등한다.
 - 정보 매도(악재)는 계속 빠진다.
급락일 일봉만으로 찍는 강제매도 지문(no-lookahead, 전부 신호일 종가까지의 정보):
 FS1 gap ≤ −3%      동시호가에 시장가로 던짐(반대매매는 장 시작에 나온다)
 FS2 ibs ≥ 50       저가 대비 회복 마감(던지고 나서 되사짐)
 FS3 down_streak ≥3 여러 날 연쇄 청산(담보비율 미달이 며칠 이어짐)
 FS4 vol_spike ≥ 2  평소의 2배 이상 물량
정보매도 지문(반대): gap ≥ 0(갭 없이 장중 계속 밀림) & ibs < 20(종가=저가).

원장: virtual_books.db xkr_fallen3 백필(KR 급락 풀 15개월, 계약 TP12/SL25/D7·KR 비용).
"""
import sqlite3, json, statistics as st
from contextlib import closing
from collections import defaultdict
DB = 'file:data/shadow/virtual_books.db?mode=ro'
POOL = 'xkr_fallen3'

def sess(rows, label, indent="  "):
    """rows = [(session_date, ticker, net)] — 세션 클러스터 t + 종목 클러스터 t."""
    if not rows:
        print(f"{indent}{label:34s} n 0"); return None
    byd = defaultdict(list); byt = defaultdict(list)
    for d, tk, v in rows: byd[d].append(v); byt[tk].append(v)
    sm = [st.mean(v) for v in byd.values()]; tm = [st.mean(v) for v in byt.values()]
    def _t(xs): return st.mean(xs) / (st.pstdev(xs) / len(xs) ** 0.5) if len(xs) > 2 and st.pstdev(xs) else float('nan')
    mu = st.mean(sm)
    print(f"{indent}{label:34s} n {len(rows):6d} 세션 {len(sm):3d} 세션평균 {mu:+6.2f} t {_t(sm):+5.2f} | 종목 {len(tm):4d} t {_t(tm):+5.2f} | 승 {100*sum(v>0 for _,_,v in rows)/len(rows):4.1f}%")
    return mu

with closing(sqlite3.connect(DB, uri=True, timeout=5)) as c:
    raw = c.execute(f"select session_date, ticker, net_pct, meta from trades where strategy_id='{POOL}' and backfill=1 and status='CLOSED' and net_pct is not null").fetchall()
T = []
for d, tk, net, meta in raw:
    f = (json.loads(meta) if meta else {}).get('feat') or {}
    if f.get('gap') is None or f.get('ibs') is None: continue
    T.append(dict(d=d, tk=tk, v=net, gap=f['gap'], ibs=f['ibs'], ds=f.get('down_streak') or 0,
                  vs=f.get('vol_spike') or 0, chg=f.get('chg'), dvol=f.get('dvol') or 0, half='H1' if d < '2026-01-01' else 'H2'))
print(f"KR 급락 풀 {POOL} 백필 {len(T)}건 {min(t['d'] for t in T)}~{max(t['d'] for t in T)}\n")
R = lambda L: [(t['d'], t['tk'], t['v']) for t in L]
sess(R(T), "전량(기저)")

print("\n[1] 지문 단독 — 있음 vs 없음")
FS = {"FS1 갭다운 ≤−3%": lambda t: t['gap'] <= -3, "FS2 저가대비 회복 IBS≥50": lambda t: t['ibs'] >= 50,
      "FS3 연속하락 ≥3일": lambda t: t['ds'] >= 3, "FS4 거래량 ≥2배": lambda t: t['vs'] >= 2}
for name, fn in FS.items():
    sess(R([t for t in T if fn(t)]), name + " 있음"); sess(R([t for t in T if not fn(t)]), name + " 없음")

print("\n[2] 2×2 — 갭다운 × 장중 회복 (핵심 대비)")
for g, gl in ((True, "갭다운 ≤−3"), (False, "갭 > −3")):
    for i, il in ((True, "IBS≥50 회복"), (False, "IBS<50")):
        sess(R([t for t in T if (t['gap'] <= -3) == g and (t['ibs'] >= 50) == i]), f"{gl} × {il}")

print("\n[3] 강제매도 점수 0~4 사다리")
for k in range(5):
    sess(R([t for t in T if sum(fn(t) for fn in FS.values()) == k]), f"점수 {k}")
print()
for k in (2, 3):
    sess(R([t for t in T if sum(fn(t) for fn in FS.values()) >= k]), f"점수 ≥{k}")
sess(R([t for t in T if t['gap'] >= 0 and t['ibs'] < 20]), "정보매도형(갭≥0 & IBS<20)")

print("\n[4] 반기 안정성 — 점수 ≥2 vs 정보매도형")
for h in ("H1", "H2"):
    sess(R([t for t in T if t['half'] == h and sum(fn(t) for fn in FS.values()) >= 2]), f"{h} 점수≥2")
    sess(R([t for t in T if t['half'] == h and t['gap'] >= 0 and t['ibs'] < 20]), f"{h} 정보매도형")

print("\n[5] 상위 2세션 제외 (점수 ≥2)")
sel = [t for t in T if sum(fn(t) for fn in FS.values()) >= 2]
byd = defaultdict(list)
for t in sel: byd[t['d']].append(t['v'])
top2 = {d for d, _ in sorted(((d, st.mean(v)) for d, v in byd.items()), key=lambda x: -x[1])[:2]}
sess(R([t for t in sel if t['d'] not in top2]), "점수≥2, 상위2세션 제외")

print("\n[6] 실행 형태 — 세션당 거래대금 1위 1종목(K=1)")
for lab, fn in (("전량 K1", lambda t: True), ("점수≥2 K1", lambda t: sum(f(t) for f in FS.values()) >= 2),
                ("정보매도형 K1", lambda t: t['gap'] >= 0 and t['ibs'] < 20)):
    byd = defaultdict(list)
    for t in T:
        if fn(t): byd[t['d']].append(t)
    picks = [max(v, key=lambda x: x['dvol']) for v in byd.values()]
    sess(R(picks), lab)
