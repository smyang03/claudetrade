import yfinance as yf, pandas as pd, numpy as np
def load(t,start="2010-01-01"):
    d=yf.download(t,start=start,auto_adjust=False,progress=False,threads=False)
    if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
    return d.dropna()
ks=load('^KS11'); lev=load('122630.KS'); k200=load('069500.KS'); kq=load('^KQ11'); kqlev=load('233740.KS')
def study(sig,thr,inst,label,cost):
    s=sig['Close'].pct_change()*100; idx=inst.index; out=[]
    for dt in s[s<=thr].index:
        if dt not in idx: continue
        i=idx.get_loc(dt)
        if i+10>=len(idx): continue
        c0=inst['Close'].iloc[i]
        out.append(dict(date=dt.date(),ov=(inst['Open'].iloc[i+1]/c0-1)*100,d1=(inst['Close'].iloc[i+1]/c0-1)*100,d5=(inst['Close'].iloc[i+5]/c0-1)*100,d10=(inst['Close'].iloc[i+10]/c0-1)*100))
    r=pd.DataFrame(out).set_index('date')
    allr=pd.DataFrame(dict(ov=(inst['Open'].shift(-1)/inst['Close']-1)*100,d1=(inst['Close'].shift(-1)/inst['Close']-1)*100,d5=(inst['Close'].shift(-5)/inst['Close']-1)*100,d10=(inst['Close'].shift(-10)/inst['Close']-1)*100)).dropna()
    print(f"\n== {label} thr {thr}% n={len(r)} span {inst.index[0].date()}~")
    for k in ['ov','d1','d5','d10']:
        x=r[k]; b=allr[k].mean()
        print(f"  {k:3s} mean {x.mean():+.2f} med {x.median():+.2f} win {100*(x>0).mean():.0f}% min {x.min():+.2f} | uncond {b:+.2f} | excess {x.mean()-b:+.2f}")
    r['yr']=[d.year for d in r.index]
    print(r.groupby('yr')[['ov','d5','d10']].mean().round(2).assign(n=r.groupby('yr').size()).to_string())
    last=None; seq=[]
    for d,row in r.iterrows():
        if last is None or (pd.Timestamp(d)-pd.Timestamp(last)).days>14: seq.append(row['d10']-cost); last=d
    eq=np.cumprod(1+np.array(seq)/100); dd=(eq/np.maximum.accumulate(eq)-1).min()*100
    print(f"  non-overlap D10 trades {len(seq)} mean {np.mean(seq):+.2f} total {100*(eq[-1]-1):+.1f}% maxDD {dd:+.1f}%")
for thr in [-1.5,-2.0,-2.5]:
    study(ks,thr,lev,'KODEX 레버리지 122630 (KOSPI<=thr)',0.05)
study(ks,-2.0,k200,'KODEX200 069500 (KOSPI<=-2)',0.05)
study(kq,-2.5,kqlev,'KODEX 코스닥150레버리지 233740 (KOSDAQ<=-2.5)',0.05)
