import yfinance as yf, pandas as pd, numpy as np
def load(t,start="2010-01-01"):
    d=yf.download(t,start=start,auto_adjust=False,progress=False,threads=False)
    if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
    return d.dropna()
kq=load('^KQ11'); ks=load('^KS11')
def ov_study(sig,thr,inst,label):
    s=sig['Close'].pct_change()*100; idx=inst.index; out=[]
    for dt in s[s<=thr].index:
        if dt not in idx: continue
        i=idx.get_loc(dt)
        if i+1>=len(idx): continue
        c0=inst['Close'].iloc[i]
        out.append(dict(date=dt.date(),sig=s.loc[dt],ov=(inst['Open'].iloc[i+1]/c0-1)*100,d1=(inst['Close'].iloc[i+1]/c0-1)*100))
    r=pd.DataFrame(out).set_index('date')
    allov=((inst['Open'].shift(-1)/inst['Close']-1)*100).dropna()
    x=r['ov']; ex=x-allov.mean(); t=ex.mean()/(ex.std()/np.sqrt(len(ex)))
    print(f"\n== {label} thr {thr} n={len(r)} span {inst.index[0].date()}~ | ov mean {x.mean():+.2f} uncond {allov.mean():+.2f} excess {ex.mean():+.2f} t {t:.2f} win {100*(x>0).mean():.0f}% std {x.std():.2f} min {x.min():+.2f} p10 {x.quantile(.1):+.2f}")
    r['yr']=[d.year for d in r.index]
    g=r.groupby('yr')['ov'].agg(['count','mean','min']); print(g.round(2).T.to_string())
    # post-2019 vs pre
    for lo,hi in [(2016,2018),(2019,2022),(2023,2026)]:
        m=(r['yr']>=lo)&(r['yr']<=hi)
        if m.sum(): print(f"  {lo}-{hi}: n {m.sum()} ov {r.loc[m,'ov'].mean():+.2f} win {100*(r.loc[m,'ov']>0).mean():.0f}%")
    # depth buckets
    r['b']=pd.cut(r['sig'],[-99,-4,-3,-2.5,-2,-1.5,0])
    print(r.groupby('b',observed=True)['ov'].agg(['count','mean']).round(2).T.to_string())
    return r
r1=ov_study(kq,-1.5,load('233740.KS'),'KOSDAQ150 lev 233740')
r2=ov_study(kq,-1.5,load('229200.KS'),'KOSDAQ150 unlev 229200')
r3=ov_study(ks,-1.5,load('122630.KS'),'KODEX lev 122630 (KOSPI)')
r4=ov_study(ks,-1.5,load('069500.KS'),'KODEX200 069500')
# consecutive-day clustering: how many signals were preceded by a signal the day before
s=kq['Close'].pct_change()*100; sig=(s<=-2.5); print("\nKOSDAQ<=-2.5 events",sig.sum(),"of which prev day also event:",(sig&sig.shift(1,fill_value=False)).sum())
# 2026 events list
x=r1[r1['yr']==2026][['sig','ov']]; print(x.round(2).to_string())
