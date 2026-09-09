# -*- coding: utf-8 -*-
"""회피 규칙 결합 판정 (2026-09-10, 강제매도 가설 5차 = 종결).

지금까지: 강제매도 지문(갭다운·회복·연쇄·거래량)은 매수 신호로 죽었다. 갭다운 시장폭 ≥20%를 매수 신호로 쓰는 것도
다른 유니버스 같은 기간에서 부호가 뒤집혀 죽었다(패널 A 2025-06~12 −0.89 vs xkr +1.12).
남은 후보는 **회피 규칙 2종**뿐이다:
  R1  지수(KOSPI) 종가 < MA20 이 아닌 날 = "MA20 위"에서는 급락 매수 금지     (09-10 OOS 6/6)
  R2  시장이 조용한데(급락 풀 갭다운비율 <20%) 혼자 갭다운(≤−3%)한 종목 금지  (인샘플 −1.73, 패널A −1.42, 패널B −3.11)
질문: R2가 R1 위에 증분을 주는가. 그리고 둘을 합치면 기저가 얼마나 올라가는가(양수가 되는가).
사용: python tools/research/research_avoid_rules_combined.py <scratchpad_dir>
"""
import csv, json, statistics as st, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, 'tools')
from virtual_books import contract_exit_v2, FEE_KR, HOLD_SESSIONS
SP = Path(sys.argv[1])
rows = sorted((r['date'], float(r['close'])) for r in csv.DictReader((SP / 'kospi_index.csv').open(encoding='utf-8')))
IDX_OOS = {rows[i][0]: rows[i][1] >= st.mean(x[1] for x in rows[i - 19:i + 1]) for i in range(20, len(rows))}

def cell(rows_, label, indent="  "):
    if not rows_:
        print(f"{indent}{label:34s} n 0"); return None
    byd = defaultdict(list)
    for d, v in rows_: byd[d].append(v)
    sm = [st.mean(v) for v in byd.values()]
    t = st.mean(sm) / (st.pstdev(sm) / len(sm) ** 0.5) if len(sm) > 2 and st.pstdev(sm) else float('nan')
    print(f"{indent}{label:34s} n {len(rows_):6d} 세션 {len(sm):3d} 세션평균 {st.mean(sm):+6.2f} t {t:+5.2f} | "
          f"거래평균 {st.mean(v for _,v in rows_):+6.2f} | 승 {100*sum(v>0 for _,v in rows_)/len(rows_):4.1f}%")
    return st.mean(sm)

def analyze(T, label):
    """T = [{d, v, gap, above}] — gr은 세션 내에서 계산."""
    byd = defaultdict(list)
    for t in T: byd[t['d']].append(t)
    for d, v in byd.items():
        gr = sum(1 for x in v if x['gap'] <= -3) / len(v)
        for x in v: x['gr'] = gr
    print(f"\n== {label} — {len(T)}건 / {len(byd)}세션 ==")
    R = lambda L: [(t['d'], t['v']) for t in L]
    base = cell(R(T), "기저(전량)")
    cell(R([t for t in T if t['above'] is not False]), "  R1 위반(MA20 위) — 버릴 쪽")
    r1 = cell(R([t for t in T if t['above'] is False]), "R1 적용(MA20 아래만)")
    cell(R([t for t in T if t['gr'] < .20 and t['gap'] <= -3]), "  R2 위반(조용한 날 혼자 갭다운) — 버릴 쪽")
    r2 = cell(R([t for t in T if not (t['gr'] < .20 and t['gap'] <= -3)]), "R2 적용")
    r12 = cell(R([t for t in T if t['above'] is False and not (t['gr'] < .20 and t['gap'] <= -3)]), "R1+R2 적용")
    cell(R([t for t in T if t['above'] is False and t['gr'] < .20 and t['gap'] <= -3]), "  (R1 안에서 R2 위반분)")
    if base is not None:
        print(f"   → 기저 {base:+.2f} | R1 {r1:+.2f}({r1-base:+.2f}pp) | R2 {r2:+.2f}({r2-base:+.2f}pp) | R1+R2 {r12:+.2f}({r12-base:+.2f}pp)"
              if None not in (r1, r2, r12) else "")

# 인샘플 — 원장
import sqlite3
from contextlib import closing
with closing(sqlite3.connect('file:data/shadow/virtual_books.db?mode=ro', uri=True, timeout=5)) as c:
    raw = c.execute("select session_date, net_pct, meta from trades where strategy_id='xkr_fallen3' and backfill=1 and status='CLOSED' and net_pct is not null").fetchall()
IN = []
for d, net, meta in raw:
    m = json.loads(meta) if meta else {}; f = m.get('feat') or {}
    if f.get('gap') is None: continue
    IN.append(dict(d=d, v=net, gap=f['gap'], above=(m.get('regime') or {}).get('idx_above_ma20')))
analyze(IN, "인샘플 xkr_fallen3 2025-06~2026-08 (1,490종목)")

def build(bars_by_tk, start, end):
    R = []
    for tk, b in bars_by_tk.items():
        for i in range(21, len(b) - HOLD_SESSIONS - 1):
            d = b[i][0]
            if d < start or d > end or IDX_OOS.get(d) is None: continue
            c0, o, c, vol = b[i - 1][4], b[i][1], b[i][4], b[i][5]
            if c0 <= 0 or o <= 0 or c <= 0: continue
            chg = (c / c0 - 1) * 100
            if chg > -3 or chg <= -30 or c * vol < 2e9: continue
            res = contract_exit_v2(b[i + 1][1], b[i + 1:i + 1 + HOLD_SESSIONS], fee=FEE_KR, be_lock=False)
            if res: R.append(dict(d=d, v=res[0], gap=(o / c0 - 1) * 100, above=IDX_OOS[d]))
    return R

raw = json.load(open('data/analysis/kr_fallen_price_cache_2025.json', encoding='utf-8'))
A = {tk: [(x['d'], x['o'], x['h'], x['l'], x['c'], x['v']) for x in v if x.get('o') and x.get('c')] for tk, v in raw.items()}
analyze(build(A, '2024-12-01', '2025-05-31'), "OOS 패널 A 634종목 2024-12~2025-05")
B = {}
for p in (SP / 'kr_oos_px').glob('*.csv'):
    b = []
    for r in csv.DictReader(p.open(encoding='utf-8')):
        try: b.append((r['Date'][:10], float(r['Open']), float(r['High']), float(r['Low']), float(r['Close']), float(r['Volume'] or 0)))
        except (ValueError, KeyError): pass
    if len(b) > 60: B[p.stem] = b
analyze(build(B, '2023-01-01', '2025-05-31'), f"OOS 패널 B 자사주 {len(B)}종목 2023-01~2025-05")
analyze(build(B, '2023-01-01', '2023-12-31'), "OOS 패널 B 2023만")
analyze(build(B, '2024-01-01', '2024-12-31'), "OOS 패널 B 2024만")
