# -*- coding: utf-8 -*-
"""패닉 rv/max 칸 검증: 이웃 칸 연속성 · 출구계약 독립(D5/D10 종가) · 15:45 마감진입 원장 재현 · 세션 부트스트랩."""
import sqlite3,json,statistics as st,csv,random
from collections import defaultdict
from pathlib import Path
random.seed(1)
ROOT=Path('E:/code/claudetrade')
con=sqlite3.connect(f'file:{ROOT}/data/shadow/virtual_books.db?mode=ro',uri=True); con.execute('pragma busy_timeout=5000')
R=[]
for sd,tk,net,meta in con.execute("select session_date,ticker,net_pct,meta from trades where strategy_id='c_us_panic_all' and backfill=1 and status='CLOSED' and net_pct is not null"):
    m=json.loads(meta); f=m.get('feat') or {}
    R.append(dict(sd=sd,tk=tk,v=float(net),rv=f.get('rv20'),mx=f.get('max21'),f=f))
def sess(rows):
    by=defaultdict(list)
    for r in rows: by[r['sd']].append(r['v'])
    sm=[st.mean(v) for v in by.values()]
    return len(rows),len(sm),(st.mean(sm) if sm else float('nan')),(st.mean(sm)/(st.pstdev(sm)/len(sm)**0.5) if len(sm)>1 and st.pstdev(sm) else 0)
print("== ① 이웃 칸 연속성 (rv20 × max21, 세션 평균 / n) ==")
rvb=[(0,2,'rv<2'),(2,3.5,'2~3.5'),(3.5,5,'3.5~5'),(5,7,'5~7'),(7,99,'≥7')]; mxb=[(0,4,'mx<4'),(4,8,'4~8'),(8,15,'8~15'),(15,25,'15~25'),(25,999,'≥25')]
print(f"{'':10s}"+''.join(f"{l:>14s}" for _,_,l in mxb))
for lo,hi,l in rvb:
    line=f"{l:10s}"
    for mlo,mhi,ml in mxb:
        sub=[r for r in R if r['rv'] is not None and r['mx'] is not None and lo<=r['rv']<hi and mlo<=r['mx']<mhi]
        n,s,m,t=sess(sub); line+=f"{('%+.2f/%d'%(m,n)) if n>=20 else '-':>14s}"
    print(line)
# 출구 계약 독립: 가격 CSV로 D5/D10 단순 종가·다음시가 진입
PX={}
def bars(tk):
    if tk in PX: return PX[tk]
    p=ROOT/'data/price/us'/f'us_{tk}.csv'; out=None
    if p.exists():
        rows=list(csv.DictReader(p.open(encoding='utf-8-sig'))); out=([r['date'] for r in rows],{r['date']:(float(r['open']),float(r['high']),float(r['low']),float(r['close'])) for r in rows})
    PX[tk]=out; return out
def plain(rows,hold):
    out=[]
    for r in rows:
        b=bars(r['tk'])
        if not b or r['sd'] not in b[1]: continue
        d,bd=b; i=d.index(r['sd'])
        if i+1+hold>=len(d): continue
        e=bd[d[i+1]][0]; x=bd[d[i+hold]][3]
        if e>0: out.append(dict(sd=r['sd'],v=(x/e-1)*100-0.5))
    return out
cell=[r for r in R if r['rv'] is not None and r['mx'] is not None and 3.5<=r['rv']<5 and 8<=r['mx']<15]; rest=[r for r in R if r not in cell]
print("\n== ② 출구 계약 독립 (TP/SL 없이 다음시가 진입 → k봉 뒤 종가, 비용 0.5) ==")
for hold in (2,5,7,10):
    a=sess(plain(cell,hold)); b=sess(plain(rest,hold)); print(f"  hold {hold:2d}: 칸 n {a[0]} sess {a[1]} {a[2]:+.2f} t {a[3]:+.2f} | 나머지 n {b[0]} {b[2]:+.2f} t {b[3]:+.2f}")
print("\n== ③ 15:45 마감 진입 원장(us_panic_close) 재현 — rv20·max21은 CSV로 계산 ==")
pc=[json.loads(l) for l in (ROOT/'data/shadow/us_panic_close.jsonl').open(encoding='utf-8') if l.strip()]
T=[r for r in pc if r['kind']=='trade' and r.get('status')=='CLOSED' and r.get('instrument')=='stock' and r.get('mode')=='backfill']
def feats(tk,sd):
    b=bars(tk)
    if not b or sd not in b[1]: return None
    d,bd=b; i=d.index(sd)
    if i<22: return None
    cl=[bd[x][3] for x in d[i-21:i+1]]; rets=[(cl[k]/cl[k-1]-1)*100 for k in range(1,len(cl))]
    rv=st.pstdev(rets[-20:]); mx=max(abs(x) for x in rets[-21:])
    return rv,mx
grid=defaultdict(list); tot=[]
for r in T:
    fx=feats(r['ticker'],r['session_date'])
    if not fx: continue
    rv,mx=fx; tot.append(dict(sd=r['session_date'],v=float(r['net_pct']),ov=float(r['overnight_pct'] or 0)))
    key=('rv3.5~5' if 3.5<=rv<5 else ('rv<3.5' if rv<3.5 else 'rv≥5'), 'mx8~15' if 8<=mx<15 else ('mx<8' if mx<8 else 'mx≥15'))
    grid[key].append(tot[-1])
n,s,m,t=sess(tot); print(f"  전체 n {n} sess {s} net {m:+.2f} t {t:+.2f}")
for k in sorted(grid):
    n,s,m,t=sess(grid[k]); ov=st.mean(r['ov'] for r in grid[k]); print(f"  {k[0]:8s} {k[1]:7s} n {n:5d} sess {s:3d} net {m:+.2f} t {t:+.2f} overnight {ov:+.2f}")
print("\n== ④ 세션 블록 부트스트랩 (칸 세션 평균, 1000회) ==")
by=defaultdict(list)
for r in cell: by[r['sd']].append(r['v'])
sm=[st.mean(v) for v in by.values()]
bs=sorted(st.mean(random.choices(sm,k=len(sm))) for _ in range(1000))
print(f"  칸 세션 {len(sm)} 평균 {st.mean(sm):+.2f} 95% CI [{bs[25]:+.2f}, {bs[975]:+.2f}]  P(mean≤0) {sum(x<=0 for x in bs)/1000:.3f}")
