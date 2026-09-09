# -*- coding: utf-8 -*-
import sqlite3,json,statistics as st,sys
from collections import defaultdict
ROOT='E:/code/claudetrade'
con=sqlite3.connect(f'file:{ROOT}/data/shadow/virtual_books.db?mode=ro',uri=True); con.execute('pragma busy_timeout=5000')
sec=json.load(open(f'{ROOT}/data/sector_map.json',encoding='utf-8'))
def sector(mk,tk): return (sec.get(mk,{}).get(tk) or {}).get('sector') or '미분류'
def b(x,edges,labels):
    if x is None: return None
    for e,l in zip(edges,labels):
        if x<e: return l
    return labels[-1]
def cells(r,mk):
    f=r['feat']; rg=r['regime']; c={}
    c['chg']=b(f.get('chg'),[-10,-7,-5,-3,0,5,15],['≤−10','−10~−7','−7~−5','−5~−3','−3~0','0~+5','+5~+15','≥+15'])
    c['rank_dvol']=b(r['rank'],[2,6,16],['1위','2~5위','6~15위','16위+'])
    c['regime']='MA20아래' if rg.get('idx_above_ma20') is False else ('MA20위' if rg.get('idx_above_ma20') else None)
    c['breadth']=b(rg.get('breadth_down_pct'),[40,60],['<40 개별','40~60','≥60 동반'])
    c['from_high20']=b(f.get('from_high20'),[-30,-20,-10,0],['≤−30','−30~−20','−20~−10','−10~0','≥0'])
    c['max21']=b(f.get('max21'),[4,8,15],['<4','4~8','8~15','≥15'])
    c['mom20']=b(f.get('mom20'),[-15,-5,5,15],['≤−15','−15~−5','−5~+5','+5~+15','≥+15'])
    c['rv20']=b(f.get('rv20'),[2,3.5,5],['<2','2~3.5','3.5~5','≥5'])
    c['ibs']=b(f.get('ibs'),[25,50,75],['<25','25~50','50~75','≥75'])
    c['gap']=b(f.get('gap'),[-3,-1,0,1,3],['≤−3','−3~−1','−1~0','0~+1','+1~+3','≥+3'])
    if mk=='KR': c['price']=b(f.get('price'),[5000,20000,50000],['<5천','5천~2만','2만~5만','≥5만']); c['dvol']=b(f.get('dvol'),[5e9,2e10,1e11],['<50억','50~200억','200~1000억','≥1000억'])
    else: c['price']=b(f.get('price'),[10,30,100],['<$10','$10~30','$30~100','≥$100']); c['dvol']=b(f.get('dvol'),[1e8,5e8,2e9],['<100M','100~500M','0.5~2B','≥2B'])
    c['sector']=sector(mk,r['tk'])
    return c
def stats(rows):
    by=defaultdict(list)
    for r in rows: by[r['sd']].append(r['v'])
    sm=[st.mean(v) for v in by.values()]
    t=st.mean(sm)/(st.pstdev(sm)/len(sm)**0.5) if len(sm)>1 and st.pstdev(sm) else 0.0
    return len(rows),len(sm),st.mean(sm),t,100*sum(r['v']>0 for r in rows)/len(rows)
arms=sys.argv[1:] or [r[0] for r in con.execute("select distinct strategy_id from trades where backfill=1 and strategy_id like 'c_%'")]
for sid in sorted(arms):
    mk='KR' if '_kr_' in sid else 'US'
    R=[]
    for sd,tk,net,meta,rank in con.execute("select session_date,ticker,net_pct,meta,pick_pos from trades where strategy_id=? and backfill=1 and status='CLOSED' and net_pct is not null",(sid,)):
        m=json.loads(meta); R.append(dict(sd=sd,tk=tk,v=float(net),feat=m.get('feat') or {},regime=m.get('regime') or {},rank=(m.get('ranks') or {}).get('dvol_desc')))
    if len(R)<30: print(f"\n## {sid} n {len(R)} (표본 부족)"); continue
    dates=sorted({r['sd'] for r in R}); mid=dates[len(dates)//2]
    n,s,m_,t,w=stats(R); print(f"\n## {sid} 전체 n {n} sess {s} mean {m_:+.2f} t {t:+.2f} win {w:.0f}%  ({dates[0]}~{dates[-1]}, H2 {mid}~)")
    out=[]
    for r in R: r['c']=cells(r,mk)
    for key in R[0]['c']:
        groups=defaultdict(list)
        for r in R: groups[r['c'][key]].append(r)
        for val,rows in groups.items():
            if val is None or len(rows)<30: continue
            n,s,m_,t,w=stats(rows)
            if s<8: continue
            h1=[r for r in rows if r['sd']<mid]; h2=[r for r in rows if r['sd']>=mid]
            m1=st.mean(r['v'] for r in h1) if h1 else None; m2=st.mean(r['v'] for r in h2) if h2 else None
            same = (m1 is not None and m2 is not None and (m1>0)==(m2>0))
            out.append((m_,key,val,n,s,t,w,m1,m2,same))
    out.sort(key=lambda x:-x[0])
    def fmt(x):
        m_,key,val,n,s,t,w,m1,m2,same=x
        return f"  {key:11s} {val:12s} n {n:5d} sess {s:3d} mean {m_:+.2f} t {t:+.2f} win {w:.0f}% H1 {m1 if m1 is None else round(m1,2)} / H2 {m2 if m2 is None else round(m2,2)} {'★같은부호' if same else ''}"
    print(" [수익 상위]"); [print(fmt(x)) for x in out[:7]]
    print(" [손실 하위]"); [print(fmt(x)) for x in out[-6:]]
