# -*- coding: utf-8 -*-
"""패닉 rv/max 칸: 저장소 정산 엔진(contract_exit_v2)으로 인샘플 재현·OOS. 사용: panic_engine_test.py <price_dir> <start> <end> <label>"""
import csv,statistics as st,sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0,'E:/code/claudetrade/tools')
from virtual_books import contract_exit_v2, FEE_US, HOLD_SESSIONS
D=Path(sys.argv[1]); start,end,label=sys.argv[2],sys.argv[3],sys.argv[4]
px={}
for p in D.glob('us_*.csv'):
    rows=list(csv.DictReader(p.open(encoding='utf-8-sig')))
    if len(rows)<120: continue
    try: px[p.stem[3:]]=[(r['date'],float(r['open']),float(r['high']),float(r['low']),float(r['close']),float(r['volume'] or 0)) for r in rows]
    except ValueError: continue
down=defaultdict(int); tot=defaultdict(int); idx={}
for tk,b in px.items():
    idx[tk]={x[0]:i for i,x in enumerate(b)}
    for i in range(1,len(b)):
        if b[i-1][4]>0: tot[b[i][0]]+=1; down[b[i][0]]+=(b[i][4]<b[i-1][4])
breadth={d:100*down[d]/tot[d] for d in tot if tot[d]>=300}
panic=sorted(d for d,v in breadth.items() if v>=65 and start<=d<=end)
print(f"[{label}] tickers {len(px)} panic days {len(panic)} by year",{y:sum(1 for d in panic if d.startswith(y)) for y in sorted({d[:4] for d in panic})})
R=[]
for d in panic:
    for tk,b in px.items():
        i=idx[tk].get(d)
        if i is None or i<22 or i+1+HOLD_SESSIONS>len(b): continue
        c0,c=b[i-1][4],b[i][4]
        if c0<=0 or (c/c0-1)*100>-3 or c0*b[i-1][5]<5e7: continue
        cl=[x[4] for x in b[i-21:i+1]]; rets=[(cl[k]/cl[k-1]-1)*100 for k in range(1,len(cl))]
        rv=st.pstdev(rets[-20:]); mx=max(abs(x) for x in rets[-21:])
        entry=b[i+1][1]; win=b[i+1:i+1+HOLD_SESSIONS]
        res=contract_exit_v2(entry,win,fee=FEE_US,be_lock=True)
        if res is None: continue
        R.append(dict(sd=d,tk=tk,v=res[0],why=res[1],rv=rv,mx=mx))
def sess(rows):
    by=defaultdict(list)
    for r in rows: by[r['sd']].append(r['v'])
    sm=[st.mean(v) for v in by.values()]
    return len(rows),len(sm),(st.mean(sm) if sm else float('nan')),(st.mean(sm)/(st.pstdev(sm)/len(sm)**0.5) if len(sm)>1 and st.pstdev(sm) else 0),(100*sum(r['v']>0 for r in rows)/len(rows) if rows else 0)
def rep(l,rows):
    n,s,m,t,w=sess(rows); ex={k:sum(1 for r in rows if r['why']==k) for k in ('TP','SL','BE','D_MAT')}; print(f"  {l:26s} n {n:5d} sess {s:3d} mean {m:+.2f} t {t:+.2f} win {w:.0f}% {ex}")
rep('패닉 전량',R); cell=[r for r in R if 3.5<=r['rv']<5 and 8<=r['mx']<15]; rep('rv3.5~5 & max21 8~15',cell); rep('나머지',[r for r in R if r not in cell])
for y in sorted({r['sd'][:4] for r in R}): rep(f' {y} 전량',[r for r in R if r['sd'].startswith(y)]); rep(f' {y} 칸',[r for r in cell if r['sd'].startswith(y)])
print(' 이웃 (rv × max21):')
for lo,hi in ((2,3.5),(3.5,5),(5,7),(7,99)):
    for mlo,mhi in ((4,8),(8,15),(15,25),(25,999)):
        sub=[r for r in R if lo<=r['rv']<hi and mlo<=r['mx']<mhi]
        if len(sub)>=30: n,s,m,t,w=sess(sub); print(f"   rv{lo}~{hi} mx{mlo}~{mhi}: n {n:5d} mean {m:+.2f} t {t:+.2f}")
