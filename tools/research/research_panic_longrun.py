import yfinance as yf, pandas as pd, numpy as np, sys, json
def load(t, start="2000-01-01"):
    d = yf.download(t, start=start, auto_adjust=False, progress=False, threads=False)
    if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.get_level_values(0)
    return d.dropna()
def study(sig_df, thr, inst_df, label, cost=0.5):
    # signal: sig close chg <= thr (proxy for breadth>=65). entry at close (approx 15:45). exits: next open, D1 close, D5, D10 close.
    s = sig_df['Close'].pct_change()*100
    df = inst_df.copy()
    df['c']=df['Close']; df['o']=df['Open']
    out=[]
    idx=df.index
    for dt in s[s<=thr].index:
        if dt not in idx: continue
        i=idx.get_loc(dt)
        if i+10>=len(idx): continue
        c0=df['c'].iloc[i]
        r=dict(date=dt.date(), ov=(df['o'].iloc[i+1]/c0-1)*100, d1=(df['c'].iloc[i+1]/c0-1)*100, d5=(df['c'].iloc[i+5]/c0-1)*100, d10=(df['c'].iloc[i+10]/c0-1)*100)
        out.append(r)
    r=pd.DataFrame(out).set_index('date')
    # unconditional baseline over same span
    allr=pd.DataFrame(dict(ov=(df['o'].shift(-1)/df['c']-1)*100, d5=(df['c'].shift(-5)/df['c']-1)*100, d10=(df['c'].shift(-10)/df['c']-1)*100)).dropna()
    print(f"\n== {label} thr {thr}%  n={len(r)}  (cost {cost}% round trip not subtracted)")
    for k in ['ov','d1','d5','d10']:
        x=r[k]; base=allr[k].mean() if k in allr else float('nan')
        print(f"  {k:3s} mean {x.mean():+.2f} med {x.median():+.2f} win {100*(x>0).mean():.0f}% min {x.min():+.2f} | uncond mean {base:+.2f} | excess {x.mean()-base:+.2f}")
    r['yr']=[d.year for d in r.index]
    g=r.groupby('yr')[['ov','d10']].agg(['mean','count'])
    g.columns=['ov_mean','n','d10_mean','n2']; g=g[['n','ov_mean','d10_mean']]
    print(g.round(2).to_string())
    # non-overlapping D10 equity path: take trades sequentially, skip if within 10 days of previous
    last=None; seq=[]
    for d,row in r.iterrows():
        if last is None or (pd.Timestamp(d)-pd.Timestamp(last)).days>14:
            seq.append(row['d10']-cost); last=d
    eq=np.cumprod(1+np.array(seq)/100); dd=(eq/np.maximum.accumulate(eq)-1).min()*100
    print(f"  non-overlap D10 trades {len(seq)} mean {np.mean(seq):+.2f} total {100*(eq[-1]-1):+.1f}% maxDD {dd:+.1f}%")
    return r
spy=load('SPY'); qqq=load('QQQ'); tqqq=load('TQQQ'); iwm=load('IWM')
# calibrate threshold: SPY chg on the 55 panic sessions
sess=['2025-06-13','2025-06-17','2025-06-25','2025-07-07','2025-07-11','2025-07-15','2025-07-24','2025-07-30','2025-07-31','2025-08-01','2025-08-14','2025-08-25','2025-09-02','2025-09-12','2025-09-19','2025-09-25','2025-10-07','2025-10-09','2025-10-10','2025-10-16','2025-10-22','2025-10-28','2025-10-29','2025-11-04','2025-11-06','2025-11-13','2025-11-17','2025-11-20','2025-12-01','2025-12-29','2025-12-30','2025-12-31','2026-01-20','2026-01-30','2026-02-05','2026-02-12','2026-02-23','2026-03-03','2026-03-05','2026-03-06','2026-03-12','2026-03-18','2026-03-20','2026-03-26','2026-03-27','2026-04-21','2026-04-29','2026-05-15','2026-05-19','2026-06-05','2026-06-10','2026-06-17','2026-07-08','2026-08-20','2026-09-01']
chg=spy['Close'].pct_change()*100
x=chg.reindex(pd.to_datetime(sess)).dropna()
print("SPY chg on 55 panic sessions: mean %.2f median %.2f q25 %.2f q75 %.2f max %.2f; share<=-1: %.0f%%; share<=-0.5: %.0f%%"%(x.mean(),x.median(),x.quantile(.25),x.quantile(.75),x.max(),100*(x<=-1).mean(),100*(x<=-0.5).mean()))
iw=iwm['Close'].pct_change()*100; xi=iw.reindex(pd.to_datetime(sess)).dropna()
print("IWM chg on panic sessions: median %.2f q75 %.2f"%(xi.median(),xi.quantile(.75)))
for thr in [-1.0,-1.5,-2.0]:
    study(spy,thr,tqqq,'TQQQ 2010~ (SPY<=thr)')
study(spy,-1.5,qqq,'QQQ 2000~ (SPY<=-1.5)')
study(spy,-1.5,spy,'SPY 2000~')
# recent 15 months same window as backfill
w=spy.loc['2025-06-01':]; study(w,-1.0,tqqq.loc['2025-06-01':],'TQQQ 2025-06~ (SPY<=-1.0)')
