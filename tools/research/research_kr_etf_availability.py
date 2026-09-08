# -*- coding: utf-8 -*-
import yfinance as yf
c={'360750':'TIGER S&P500','133690':'TIGER NDX100','379810':'KODEX NDX100','379800':'KODEX S&P500','132030':'KODEX gold fut(H)','411060':'ACE KRX gold spot','305080':'TIGER UST10 fut','453850':'ACE UST30 active(H)','308620':'KODEX UST10 fut','069500':'KODEX200','229200':'KODEX KQ150','233740':'KODEX KQ150 lev','251340':'KODEX KQ150 inv','148070':'KOSEF KTB10','114260':'KODEX KTB3','439870':'KODEX NDX100(H)','143850':'TIGER S&P500 fut(H)','304660':'KODEX S&P500 fut(H)','458730':'TIGER UST30 stripped','476760':'KODEX UST30 active','449180':'KODEX UST30 active(H)?'}
for t,n in c.items():
    try:
        d=yf.download(t+'.KS',period='max',progress=False,threads=False,auto_adjust=False)
        print(t,n,'|',str(d.index[0].date()) if len(d) else 'NONE',len(d),'| last',float(d['Close'].iloc[-1]) if len(d) else '')
    except Exception as e: print(t,n,'ERR',e)
