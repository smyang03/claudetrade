import yfinance as yf, pandas as pd, numpy as np
def load(t,start="2010-01-01"):
    d=yf.download(t,start=start,auto_adjust=False,progress=False,threads=False)
    if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
    return d.dropna()
def run(t,label,cost_rt,tax):
    d=load(t); o=d['Open']; c=d['Close']
    ov=(o.shift(-1)/c-1)*100; intra=(c/o-1)*100; cc=(c.shift(-1)/c-1)*100
    df=pd.DataFrame(dict(ov=ov,intra=intra,cc=cc)).dropna()
    df['ov_net']=df['ov']-cost_rt
    df['ov_net_tax']=df['ov_net']-np.where(df['ov_net']>0,df['ov_net']*tax,0)
    df['yr']=df.index.year
    print(f"\n== {label} {d.index[0].date()}~ n {len(df)} | per-night ov {df['ov'].mean():+.3f} intra {df['intra'].mean():+.3f} cc {df['cc'].mean():+.3f} | ov win {100*(df['ov']>0).mean():.0f}% std {df['ov'].std():.2f} min {df['ov'].min():+.1f}")
    def ann(x): e=np.cumprod(1+x/100); return 100*(e.iloc[-1]**(252/len(x))-1), 100*(e/e.cummax()-1).min()
    for k in ['ov','ov_net','ov_net_tax','intra','cc']:
        a,dd=ann(df[k]); print(f"   {k:11s} CAGR {a:+6.1f}%  maxDD {dd:+6.1f}%")
    g=df.groupby('yr').agg(ov=('ov','sum'),ov_net_tax=('ov_net_tax','sum'),intra=('intra','sum'),cc=('cc','sum'),n=('ov','size'))
    print(g.round(1).T.to_string())
    # conditional: only nights after a down day (close<prev close) vs up day
    prev=df['cc'].shift(1)
    for name,m in [('after down day',prev<0),('after up day',prev>0),('after <=-1.5%',prev<=-1.5),('after >=+1.5%',prev>=1.5)]:
        x=df.loc[m.fillna(False),'ov_net_tax']; print(f"   {name:16s} n {len(x):5d} mean {x.mean():+.3f} win {100*(x>0).mean():.0f}% sum/yr {x.sum()/ (len(df)/252):+.1f}")
    return df
run('233740.KS','코스닥150 레버리지 233740',0.05,0.154)
run('229200.KS','코스닥150 229200',0.05,0.0)
run('122630.KS','KODEX 레버리지 122630',0.05,0.154)
run('069500.KS','KODEX200 069500',0.05,0.0)
run('QQQ','QQQ (US cost 0.5)',0.5,0.0)
