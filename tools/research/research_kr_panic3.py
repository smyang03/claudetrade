import yfinance as yf, pandas as pd, numpy as np, glob, os
def load(t,start="2010-01-01"):
    d=yf.download(t,start=start,auto_adjust=False,progress=False,threads=False)
    if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
    return d.dropna()
kq=load('^KQ11'); u=load('229200.KS'); l=load('233740.KS')
r_kq=kq['Close'].pct_change()*100; r_u=u['Close'].pct_change()*100; r_l=l['Close'].pct_change()*100
df=pd.DataFrame(dict(kq=r_kq,u=r_u,l=r_l)).dropna()
df['res_u']=df['u']-df['kq']; df['res_l']=df['l']-2*df['kq']
print("2016~ corr(kq,u) %.3f  corr(kq,l) %.3f"%(df['kq'].corr(df['u']),df['kq'].corr(df['l'])))
bad=df[(df['res_u'].abs()>3)|(df['res_l'].abs()>6)]
print("suspect days (|resid| large):"); print(bad.round(2).to_string())
# repo CSV cross-check for 2026 event dates
ev=df.loc['2026-01-01':][df.loc['2026-01-01':,'kq']<=-1.5].index
files=glob.glob(r'E:/code/claudetrade/data/price/kr/kr_*.csv')
rets={}
for f in files:
    try:
        x=pd.read_csv(f,encoding='utf-8-sig',parse_dates=['date']).set_index('date')['close']
        rets[os.path.basename(f)]=x.pct_change()*100
    except Exception: pass
R=pd.DataFrame(rets)
print("repo KR stocks",R.shape)
rows=[]
for d in ev:
    if d in R.index:
        x=R.loc[d].dropna(); rows.append(dict(date=d.date(),kq=round(df.loc[d,'kq'],2),u=round(df.loc[d,'u'],2),l=round(df.loc[d,'l'],2),n=len(x),med=round(x.median(),2),down=round(100*(x<0).mean(),0)))
print(pd.DataFrame(rows).to_string())
# recompute overnight excess & t under exclusions
def ov(inst,sig,thr,mask_dates=None,label=''):
    s=sig['Close'].pct_change()*100; idx=inst.index; out=[]
    for dt in s[s<=thr].index:
        if dt not in idx or (mask_dates is not None and dt in mask_dates): continue
        i=idx.get_loc(dt)
        if i+1>=len(idx): continue
        out.append(dict(date=dt,ov=(inst['Open'].iloc[i+1]/inst['Close'].iloc[i]-1)*100))
    r=pd.DataFrame(out).set_index('date'); allov=((inst['Open'].shift(-1)/inst['Close']-1)*100).dropna()
    ex=r['ov']-allov.mean(); t=ex.mean()/(ex.std()/np.sqrt(len(ex)))
    print(f"  {label:28s} n {len(r):3d} ov {r['ov'].mean():+.2f} excess {ex.mean():+.2f} t {t:.2f} win {100*(r['ov']>0).mean():.0f}%")
    return r
susp=set(bad.index)
for thr in [-1.5,-2.5]:
    print(f"\nthr {thr}")
    for inst,name in [(l,'233740 lev'),(u,'229200 unlev')]:
        ov(inst,kq,thr,None,name+' all')
        ov(inst,kq,thr,set(kq.loc['2026-01-01':].index),name+' excl 2026')
        ov(inst,kq,thr,susp,name+' excl suspect')
        ov(inst.loc[:'2025-12-31'],kq.loc['2019-01-01':'2025-12-31'],thr,None,name+' 2019~2025')
