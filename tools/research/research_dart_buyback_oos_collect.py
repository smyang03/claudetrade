# -*- coding: utf-8 -*-
"""자사주 OOS용: DART list.json(주요사항보고 B) 2023-01~2025-08에서 '자기주식취득결정' 접수 목록 수집 → scratchpad jsonl."""
import json,os,time,urllib.request,urllib.parse
from datetime import date,timedelta
from pathlib import Path
ROOT=Path('E:/code/claudetrade'); OUT=Path(__file__).with_name('dart_buyback_filings_2023_2025.jsonl')
key=''
for line in (ROOT/'.env.live').read_text(encoding='utf-8',errors='replace').splitlines():
    if line.startswith('DART_API_KEY='): key=line.split('=',1)[1].strip()
corp=json.load(open(ROOT/'data/dart_corp_codes.json',encoding='utf-8'))
c2s={}
if isinstance(corp,dict):
    for k,v in corp.items():
        if isinstance(v,dict): 
            cc=v.get('corp_code'); sc=v.get('stock_code') or v.get('stock')
            if cc and sc: c2s[cc]=sc
            elif sc: c2s[k]=sc
        elif isinstance(v,str): c2s[k]=v
elif isinstance(corp,list):
    for v in corp:
        if v.get('corp_code') and v.get('stock_code'): c2s[v['corp_code']]=v['stock_code']
print('corp map',len(c2s))
have=set()
if OUT.exists():
    for l in OUT.open(encoding='utf-8'): have.add(json.loads(l)['rcept_no'])
d0=date(2023,1,1); end=date(2025,8,31); calls=0; new=0
with OUT.open('a',encoding='utf-8') as fh:
    while d0<=end:
        d1=min(d0+timedelta(days=13),end); page=1
        while True:
            q=urllib.parse.urlencode({'crtfc_key':key,'bgn_de':d0.strftime('%Y%m%d'),'end_de':d1.strftime('%Y%m%d'),'pblntf_ty':'B','page_no':page,'page_count':100})
            try:
                d=json.loads(urllib.request.urlopen(f'https://opendart.fss.or.kr/api/list.json?{q}',timeout=30).read()); calls+=1
            except Exception as e:
                print('ERR',d0,page,e); time.sleep(3); continue
            if d.get('status')!='000': print('status',d.get('status'),d.get('message'),d0); break
            for r in d.get('list',[]):
                nm=r.get('report_nm','')
                if '자기주식취득결정' in nm and '신탁' not in nm and r['rcept_no'] not in have:
                    sc=r.get('stock_code') or c2s.get(r.get('corp_code'),'')
                    fh.write(json.dumps({'rcept_no':r['rcept_no'],'date':r['rcept_dt'],'stock':sc,'corp':r.get('corp_name'),'report':nm,'corp_cls':r.get('corp_cls')},ensure_ascii=False)+'\n'); have.add(r['rcept_no']); new+=1
            tp=int(d.get('total_page') or 1)
            if page>=tp: break
            page+=1; time.sleep(0.15)
        fh.flush(); d0=d1+timedelta(days=1)
print('calls',calls,'new filings',new,'total',len(have))
