# -*- coding: utf-8 -*-
"""자사주 공시 후 0~30일 급락 매수 × KOSPI MA20 국면 OOS (2023-01~2025-08). 입력: 스크래치패드 dart_buyback_filings_2023_2025.jsonl, kr_oos_px/, kospi_index.csv.
사용: python tools/research/research_kr_buyback_regime_oos.py <scratchpad_dir>"""
import csv, json, statistics as st, sys
from collections import defaultdict
from datetime import date
from pathlib import Path
sys.path.insert(0, 'tools')
from virtual_books import contract_exit_v2, FEE_KR, HOLD_SESSIONS
SP = Path(sys.argv[1])
rows = sorted((r['date'], float(r['close'])) for r in csv.DictReader((SP / 'kospi_index.csv').open(encoding='utf-8')))
IDX = {rows[i][0]: rows[i][1] >= st.mean(x[1] for x in rows[i - 19:i + 1]) for i in range(20, len(rows))}
F = [json.loads(l) for l in (SP / 'dart_buyback_filings_2023_2025.jsonl').open(encoding='utf-8')]
by = defaultdict(list)
for r in F:
    s = (r.get('stock') or '').strip()
    if len(s) == 6: by[s].append(date.fromisoformat(f"{r['date'][:4]}-{r['date'][4:6]}-{r['date'][6:]}"))
R = []
for s, fd in by.items():
    p = SP / 'kr_oos_px' / f'{s}.csv'
    if not p.exists(): continue
    b = []
    for r in csv.DictReader(p.open(encoding='utf-8')):
        try: b.append((r['Date'][:10], float(r['Open']), float(r['High']), float(r['Low']), float(r['Close']), float(r['Volume'] or 0)))
        except (ValueError, KeyError): pass
    for i in range(21, len(b) - HOLD_SESSIONS - 1):
        dstr = b[i][0]
        if dstr < '2023-01-01' or dstr > '2025-08-31': continue
        d = date.fromisoformat(dstr); c0, c = b[i - 1][4], b[i][4]
        if c0 <= 0: continue
        chg = (c / c0 - 1) * 100
        if chg > -5 or chg <= -30 or c * b[i][5] < 2e9: continue
        days = min(((d - f).days for f in fd if f <= d), default=None)
        if days is None: continue
        res = contract_exit_v2(b[i + 1][1], b[i + 1:i + 1 + HOLD_SESSIONS], fee=FEE_KR, be_lock=False)
        if not res or IDX.get(dstr) is None: continue
        R.append(dict(d=dstr, v=res[0], win='0~30' if days <= 30 else '31~365' if days <= 365 else '365+', below=not IDX[dstr], y=dstr[:4]))
def sess(rows):
    byd = defaultdict(list)
    for d, v in rows: byd[d].append(v)
    xs = [st.mean(v) for v in byd.values()]
    if not xs: return "n 0"
    t = st.mean(xs) / (st.pstdev(xs) / len(xs) ** 0.5) if len(xs) > 2 and st.pstdev(xs) else float('nan')
    return f"n {len(rows):4d} 세션 {len(xs):3d} 세션평균 {st.mean(xs):+.2f} 거래평균 {st.mean(v for _,v in rows):+.2f} t {t:+.2f} 승 {100*sum(v>0 for _,v in rows)/len(rows):.0f}%"
print("자사주 공시 후 창 × KOSPI MA20 국면 — OOS 2023-01~2025-08 (미수정가 yfinance, 분할 의심 제외)")
for w in ('0~30', '31~365', '365+'):
    for below, lab in ((True, '아래'), (False, '위')):
        print(f"  {w:7s} MA20 {lab}: ", sess([(r['d'], r['v']) for r in R if r['win'] == w and r['below'] == below]))
print("  0~30 아래 연도별:", " | ".join(f"{y} {sess([(r['d'], r['v']) for r in R if r['win']=='0~30' and r['below'] and r['y']==y])}" for y in ('2023', '2024', '2025')))
print("  0~30 위 연도별  :", " | ".join(f"{y} {sess([(r['d'], r['v']) for r in R if r['win']=='0~30' and not r['below'] and r['y']==y])}" for y in ('2023', '2024', '2025')))
