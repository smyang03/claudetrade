# -*- coding: utf-8 -*-
"""자사주 깊은 눌림 OOS 2023-01~2025-08: DART 자기주식취득결정 접수 후 0~30일 내 ≤−5% & 깊은 눌림(mom20≤−15 or 고점대비≤−20) → 다음 시가, TP12/SL25/D7(KR BE 없음) 비용 0.25. 대조 = 같은 종목 31~120일 창."""
import csv,json,statistics as st,sys
from collections import defaultdict
from datetime import date,timedelta
from pathlib import Path
sys.path.insert(0,'E:/code/claudetrade/tools')
from virtual_books import contract_exit_v2, FEE_KR, HOLD_SESSIONS
F=[json.loads(l) for l in open('dart_buyback_filings_2023_2025.jsonl',encoding='utf-8')]
bystock=defaultdict(list)
for r in F:
    s=(r.get('stock') or '').strip()
    if len(s)==6: bystock[s].append(date.fromisoformat(f"{r['date'][:4]}-{r['date'][4:6]}-{r['date'][6:]}"))
def load(s):
    p=Path('kr_oos_px')/f'{s}.csv'
    if not p.exists(): return None
    out=[]
    for r in csv.DictReader(p.open(encoding='utf-8')):
        try: out.append((r['Date'][:10],float(r['Open']),float(r['High']),float(r['Low']),float(r['Close']),float(r['Volume'] or 0)))
        except (ValueError,KeyError): continue
    return out
R=[]
for s,fdates in bystock.items():
    b=load(s)
    if not b or len(b)<60: continue
    dl=[x[0] for x in b]
    for i in range(21,len(b)-HOLD_SESSIONS-1):
        d=date.fromisoformat(dl[i]); c0,c=b[i-1][4],b[i][4]
        if c0<=0 or (c/c0-1)*100>-5: continue
        if c*b[i][5]<2e9: continue
        days=min(((d-fd).days for fd in fdates if fd<=d),default=None)
        if days is None: continue
        cl=[x[4] for x in b[i-20:i+1]]; mom20=(cl[-1]/cl[0]-1)*100; hi20=max(x[2] for x in b[i-20:i+1]); fh=(c/hi20-1)*100
        deep=mom20<=-15 or fh<=-20
        entry=b[i+1][1]; win=b[i+1:i+1+HOLD_SESSIONS]
        res=contract_exit_v2(entry,win,fee=FEE_KR,be_lock=False)
        if res is None: continue
        R.append(dict(sd=dl[i],tk=s,v=res[0],why=res[1],days=days,deep=deep,yr=dl[i][:4]))
def sess(rows):
    by=defaultdict(list)
    for r in rows: by[r['sd']].append(r['v'])
    sm=[st.mean(v) for v in by.values()]
    return len(rows),len(sm),(st.mean(sm) if sm else float('nan')),(st.mean(sm)/(st.pstdev(sm)/len(sm)**0.5) if len(sm)>1 and st.pstdev(sm) else 0),(100*sum(r['v']>0 for r in rows)/len(rows) if rows else 0),len({r['tk'] for r in rows})
def rep(l,rows):
    n,s,m,t,w,k=sess(rows); print(f"  {l:34s} n {n:4d} sess {s:3d} 종목 {k:3d} mean {m:+.2f} t {t:+.2f} win {w:.0f}%")
print(f"filings {len(F)} stocks with px {sum(1 for s in bystock if Path('kr_oos_px',s+'.csv').exists())}/{len(bystock)}  trades {len(R)}")
for lo,hi in ((0,30),(31,60),(61,120),(121,365)):
    rep(f'공시 후 {lo}~{hi}일 · 깊음',[r for r in R if lo<=r['days']<=hi and r['deep'] and r['sd']<='2025-08-31'])
    rep(f'공시 후 {lo}~{hi}일 · 얕음',[r for r in R if lo<=r['days']<=hi and not r['deep'] and r['sd']<='2025-08-31'])
print(' 연도별 (0~30일·깊음 vs 31~120일·깊음):')
for y in ('2023','2024','2025'):
    rep(f'  {y} 0~30 깊음',[r for r in R if r['yr']==y and r['days']<=30 and r['deep'] and r['sd']<='2025-08-31']); rep(f'  {y} 31~120 깊음',[r for r in R if r['yr']==y and 31<=r['days']<=120 and r['deep'] and r['sd']<='2025-08-31'])
rep('인샘플 구간 재현 2025-09~10 0~30 깊음',[r for r in R if r['sd']>='2025-09-01' and r['days']<=30 and r['deep']])
