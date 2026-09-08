import yfinance as yf, pandas as pd, numpy as np
def load(t,start="2000-01-01"):
    d=yf.download(t,start=start,auto_adjust=True,progress=False,threads=False)
    if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
    return d['Close'].dropna()
# proxies in KRW (unhedged KR-listed ETF mimic): US asset × USDKRW
fx=load('KRW=X')
us={'SPX':'SPY','NDX':'QQQ','GOLD':'GLD','UST20':'TLT'}
kr={'K200':'069500.KS'}
px={}
for k,t in us.items():
    s=load(t); px[k]=(s*fx.reindex(s.index).ffill()).dropna()
for k,t in kr.items(): px[k]=load(t)
P=pd.DataFrame(px).ffill().dropna()
print("span",P.index[0].date(),P.index[-1].date(),"assets",list(P.columns))
m=P.resample('M').last()   # month-end
ma200=P.rolling(200).mean().resample('M').last()
mom=m/m.shift(12)-1        # 12-month momentum (12-1 variant below)
mom121=m.shift(1)/m.shift(12)-1
ret=m.pct_change()
COST=0.001; TAX=0.154
def run(sig_fn,label,cash_rate=0.03):
    sig=sig_fn().shift(1)  # decide at month-end t-1, hold month t
    w=sig.div(sig.sum(axis=1).replace(0,np.nan),axis=0).fillna(0)
    port=(w*ret).sum(axis=1)
    cash_w=1-w.sum(axis=1); port=port+cash_w*(cash_rate/12)
    turn=(w-w.shift(1)).abs().sum(axis=1).fillna(0)
    port_net=port-turn*COST
    port_net=port_net.loc[ret.dropna().index[12]:]
    eq=(1+port_net).cumprod(); yrs=len(port_net)/12
    cagr=100*(eq.iloc[-1]**(1/yrs)-1); dd=100*(eq/eq.cummax()-1).min(); sh=port_net.mean()/port_net.std()*np.sqrt(12)
    yr=port_net.groupby(port_net.index.year).apply(lambda x:100*((1+x).prod()-1))
    print(f"\n== {label}: CAGR {cagr:+.1f}% maxDD {dd:+.1f}% Sharpe {sh:.2f} yrs {yrs:.1f} avg turnover/mo {turn.mean():.2f}")
    print(yr.round(1).to_string().replace('\n',' | '))
    return port_net
def all_in_k200(): s=pd.DataFrame(0.0,index=m.index,columns=m.columns); s['K200']=1; return s
def dual_mom(): return ((mom121>0)&(m>ma200)).astype(float)
def abs_mom(): return (mom121>0).astype(float)
def top2(): 
    s=(mom121>0)&(m>ma200); r=mom121.where(s).rank(axis=1,ascending=False); return (r<=2).astype(float)
def eq_weight(): return pd.DataFrame(1.0,index=m.index,columns=m.columns)
bh=run(all_in_k200,'KODEX200 buy&hold')
ew=run(eq_weight,'5자산 등가중 매월')
dm=run(dual_mom,'TSMOM: 12-1 mom>0 & >200MA, 통과 자산 등가중, 없으면 현금 3%')
am=run(abs_mom,'12-1 mom>0 만')
t2=run(top2,'통과 중 모멘텀 상위 2')
# subperiods
for lo,hi in [('2007','2009'),('2010','2019'),('2020','2022'),('2023','2026')]:
    x=dm.loc[lo:hi]; e=(1+x).cumprod(); print(f"  TSMOM {lo}-{hi}: total {100*(e.iloc[-1]-1):+.1f}% maxDD {100*(e/e.cummax()-1).min():+.1f}%  vs K200 {100*((1+bh.loc[lo:hi]).prod()-1):+.1f}%  vs EW {100*((1+ew.loc[lo:hi]).prod()-1):+.1f}%")
