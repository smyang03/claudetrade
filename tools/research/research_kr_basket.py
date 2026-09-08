import pandas as pd, numpy as np, glob, os, json, random
random.seed(7); np.random.seed(7)
ROOT='E:/code/claudetrade'
# prices
px={}
for f in glob.glob(ROOT+'/data/price/kr/kr_*.csv'):
    t=os.path.basename(f)[3:9]
    try:
        d=pd.read_csv(f,encoding='utf-8-sig',parse_dates=['date']).set_index('date').sort_index()
        if len(d)>60: px[t]=d
    except Exception: pass
print('tickers',len(px))
sec=json.load(open(ROOT+'/data/sector_map.json',encoding='utf-8'))['KR']
bio={t for t,v in sec.items() if v.get('sector')=='제약·바이오'}
# regime
idx=pd.DataFrame(json.load(open(ROOT+'/state/kr_index_history_kospi.json',encoding='utf-8'))['rows']); idx['date']=pd.to_datetime(idx['date']); idx=idx.set_index('date')['close'].sort_index()
ma20=idx.rolling(20).mean(); below=(idx<ma20)
# panel
close=pd.DataFrame({t:d['close'] for t,d in px.items()}); openp=pd.DataFrame({t:d['open'] for t,d in px.items()})
high=pd.DataFrame({t:d['high'] for t,d in px.items()}); low=pd.DataFrame({t:d['low'] for t,d in px.items()}); vol=pd.DataFrame({t:d['volume'] for t,d in px.items()})
chg=close.pct_change()*100; dvol=(close*vol)
dates=close.index
COST=0.25; TP=12; SL=25; D=5; BUDGET=98000
def trade(t,i):  # signal at dates[i], entry open dates[i+1]
    if i+1>=len(dates): return None
    e=openp[t].iloc[i+1]
    if not (e>0): return None
    for k in range(i+1,min(i+1+D,len(dates))):
        lo=low[t].iloc[k]; hi=high[t].iloc[k]
        if lo<=e*(1-SL/100): return dict(ret=-SL-COST,reason='SL',exit=k)
        if hi>=e*(1+TP/100): return dict(ret=TP-COST,reason='TP',exit=k)
    k=min(i+D,len(dates)-1)
    if k<=i: return None
    return dict(ret=(close[t].iloc[k]/e-1)*100-COST,reason='D',exit=k)
def sess_stats(rows,label):
    if not rows: print(f"  {label:34s} n 0"); return
    df=pd.DataFrame(rows); g=df.groupby('sd')['ret'].mean()
    t=g.mean()/(g.std()/np.sqrt(len(g))) if len(g)>1 else float('nan')
    h=len(g)//2; ex=g.sort_values(ascending=False).iloc[2:].mean() if len(g)>4 else float('nan')
    print(f"  {label:34s} n {len(df):5d} sess {len(g):3d} sess_mean {g.mean():+.2f} t {t:5.2f} win {100*(df['ret']>0).mean():.0f}% H1 {g.iloc[:h].mean():+.2f} H2 {g.iloc[h:].mean():+.2f} top2ex {ex:+.2f} TP/SL {int((df['reason']=='TP').sum())}/{int((df['reason']=='SL').sum())}")
    return g
res={k:[] for k in ['all','all_below','all_above','dvol10_below','dvol10_nobio_below','deep10_below','rand10_below','dvol10_nobio_all','dvol10_nobio_above']}
concurrent=[]
for i in range(20,len(dates)-1):
    sd=dates[i]
    if sd not in below.index: continue
    c=chg.iloc[i]; pool=[t for t in c.index if c[t]<=-5 and close[t].iloc[i]<=BUDGET and close[t].iloc[i]>=1000 and dvol[t].iloc[i]>=2e9]
    if not pool: continue
    trades={}
    for t in pool:
        r=trade(t,i)
        if r: trades[t]=r
    if not trades: continue
    isb=bool(below.loc[sd])
    def add(key,ts):
        for t in ts: res[key].append(dict(sd=sd,t=t,ret=trades[t]['ret'],reason=trades[t]['reason']))
    ts=list(trades); add('all',ts); add('all_below' if isb else 'all_above',ts)
    dv=sorted(ts,key=lambda t:-dvol[t].iloc[i]); nb=[t for t in dv if t not in bio]
    add('dvol10_nobio_all',nb[:10])
    if isb:
        add('dvol10_below',dv[:10]); add('dvol10_nobio_below',nb[:10]); add('deep10_below',sorted(ts,key=lambda t:c[t])[:10])
        for _ in range(50):
            for t in random.sample(ts,min(10,len(ts))): res['rand10_below'].append(dict(sd=sd,t=t,ret=trades[t]['ret'],reason=trades[t]['reason']))
        concurrent.append((sd,len(nb[:10])))
    else: add('dvol10_nobio_above',nb[:10])
print(f"span {dates[20].date()}~{dates[-1].date()} sessions {len(dates)-21} regime-below sessions {int(below.reindex(dates).fillna(False).sum())}")
for k in res: sess_stats(res[k],k)
# capital: concurrent baskets (D5 → signal sessions within 5 trading days)
sds=[s for s,_ in concurrent]; mx=0
for j,s in enumerate(sds):
    n=sum(1 for s2 in sds if 0<=(dates.get_loc(s)-dates.get_loc(s2))<=5); mx=max(mx,n)
print(f"regime-below signal sessions {len(sds)}, max concurrent baskets (5d) {mx} → capital need {mx}×98만 = {mx*98}만 ; avg names/basket {np.mean([n for _,n in concurrent]):.1f}")
