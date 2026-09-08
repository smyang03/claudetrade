import yfinance as yf, pandas as pd, numpy as np
def load(t,start="2010-01-01"):
    d=yf.download(t,start=start,auto_adjust=False,progress=False,threads=False)
    if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
    return d.dropna()
kq=load('^KQ11'); ks=load('^KS11')
def ov_after(sig,thr,inst,label,up=True):
    s=sig['Close'].pct_change()*100; idx=inst.index; out=[]
    dates=s[s>=thr].index if up else s[s<=thr].index
    for dt in dates:
        if dt not in idx: continue
        i=idx.get_loc(dt)
        if i+1>=len(idx): continue
        out.append(dict(date=dt,sig=s.loc[dt],ov=(inst['Open'].iloc[i+1]/inst['Close'].iloc[i]-1)*100,d1=(inst['Close'].iloc[i+1]/inst['Close'].iloc[i]-1)*100))
    r=pd.DataFrame(out).set_index('date'); allov=((inst['Open'].shift(-1)/inst['Close']-1)*100).dropna()
    ex=r['ov']-allov.mean(); t=ex.mean()/(ex.std()/np.sqrt(len(ex)))
    print(f"  {label:34s} thr {thr:+.1f} n {len(r):3d} ov {r['ov'].mean():+.2f} uncond {allov.mean():+.2f} excess {ex.mean():+.2f} t {t:5.2f} win {100*(r['ov']>0).mean():.0f}% min {r['ov'].min():+.2f} | d1 {r['d1'].mean():+.2f}")
    r['yr']=r.index.year; return r
print("KOSDAQ 급등일 → 롱 ETF 오버나이트 (음수면 인버스 후보)")
u=load('229200.KS'); l=load('233740.KS'); inv=load('251340.KS')
for thr in [1.5,2.5,3.5]:
    ov_after(kq,thr,u,'229200 long after KOSDAQ up')
    ov_after(kq,thr,l,'233740 lev long after KOSDAQ up')
    r=ov_after(kq,thr,inv,'251340 INVERSE after KOSDAQ up')
    if thr==2.5: print(r.groupby('yr')['ov'].agg(['count','mean']).round(2).T.to_string())
print("\nKOSPI 급등일")
k=load('069500.KS'); kinv=load('252670.KS')
for thr in [1.5,2.5]:
    ov_after(ks,thr,k,'069500 long after KOSPI up')
    ov_after(ks,thr,kinv,'252670 INVERSE2X after KOSPI up')
print("\n대조: 급락일 인버스 (음수여야 일관)")
ov_after(kq,-2.5,inv,'251340 INVERSE after KOSDAQ down',up=False)
