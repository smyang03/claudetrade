# -*- coding: utf-8 -*-
"""US '전일 ≤−5% 급락 매수' × SPY MA20 국면 OOS (Alpaca SIP 수정주가 2023-01~2025-04). 계약 TP12/SL25/D7 BE락, 비용 0.5. 거래대금 ≥ $50M(전일)."""
import csv, statistics as st, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, 'tools')
from virtual_books import contract_exit_v2, FEE_US, HOLD_SESSIONS
D = Path('data/price_backfill_alpaca/us')
def load(p):
    b = []
    for r in csv.DictReader(p.open(encoding='utf-8-sig')):
        try: b.append((r['date'][:10], float(r['open']), float(r['high']), float(r['low']), float(r['close']), float(r['volume'] or 0)))
        except (ValueError, KeyError): pass
    return b
spy = load(D / 'us_SPY.csv')
IDX = {spy[i][0]: spy[i][4] >= st.mean(x[4] for x in spy[i - 19:i + 1]) for i in range(20, len(spy))}
R = []
for p in D.glob('us_*.csv'):
    if p.stem == 'us_SPY': continue
    b = load(p)
    for i in range(21, len(b) - HOLD_SESSIONS - 1):
        d = b[i][0]
        if d > '2025-04-30': continue
        c0, c = b[i - 1][4], b[i][4]
        if c0 <= 0 or c <= 0: continue
        chg = (c / c0 - 1) * 100
        if chg > -5 or b[i - 1][4] * b[i - 1][5] < 5e7 or IDX.get(d) is None: continue
        res = contract_exit_v2(b[i + 1][1], b[i + 1:i + 1 + HOLD_SESSIONS], fee=FEE_US, be_lock=True)
        if res: R.append(dict(d=d, v=res[0], below=not IDX[d], y=d[:4]))
def sess(rows):
    byd = defaultdict(list)
    for d, v in rows: byd[d].append(v)
    xs = [st.mean(v) for v in byd.values()]
    if not xs: return "n 0"
    t = st.mean(xs) / (st.pstdev(xs) / len(xs) ** 0.5) if len(xs) > 2 and st.pstdev(xs) else float('nan')
    return f"n {len(rows):5d} 세션 {len(xs):3d} 세션평균 {st.mean(xs):+.2f} 거래평균 {st.mean(v for _,v in rows):+.2f} t {t:+.2f} 승 {100*sum(v>0 for _,v in rows)/len(rows):.0f}%"
print("US 전일 ≤−5% & 거래대금≥$50M, 다음 시가 TP12/SL25/D7 BE — OOS 2023-01~2025-04")
print("  전량        :", sess([(r['d'], r['v']) for r in R]))
print("  SPY MA20 아래:", sess([(r['d'], r['v']) for r in R if r['below']]))
print("  SPY MA20 위 :", sess([(r['d'], r['v']) for r in R if not r['below']]))
for y in ('2023', '2024', '2025'):
    print(f"  {y} 아래: {sess([(r['d'], r['v']) for r in R if r['below'] and r['y']==y])} | 위: {sess([(r['d'], r['v']) for r in R if not r['below'] and r['y']==y])}")
