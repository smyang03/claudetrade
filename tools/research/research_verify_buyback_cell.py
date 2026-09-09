# -*- coding: utf-8 -*-
"""자사주 깊은 눌림 칸 검증: 시차(lookahead)·창 감쇠·출구 독립·진입 지연·세션 매칭 부트스트랩."""
import sqlite3,json,statistics as st,csv,random
from collections import defaultdict
from pathlib import Path
from datetime import date
random.seed(1)
ROOT=Path('E:/code/claudetrade')
con=sqlite3.connect(f'file:{ROOT}/data/shadow/virtual_books.db?mode=ro',uri=True); con.execute('pragma busy_timeout=5000')
X=[]
for sd,tk,net,meta in con.execute("select session_date,ticker,net_pct,meta from trades where strategy_id='c_kr_fallen_nomajor' and backfill=1 and status='CLOSED' and net_pct IS NOT NULL"):
    m=json.loads(meta); f=m.get('feat') or {}
    X.append(dict(sd=sd,tk=tk,v=float(net),f=f,bd=f.get('buyback_days'),dart=f.get('dart_ok')))
deep=lambda r: ((r['f'].get('mom20') or 0)<=-15 or (r['f'].get('from_high20') or 0)<=-20)
def sess(rows):
    by=defaultdict(list)
    for r in rows: by[r['sd']].append(r['v'])
    sm=[st.mean(v) for v in by.values()]
    return len(rows),len(sm),(st.mean(sm) if sm else float('nan')),(st.mean(sm)/(st.pstdev(sm)/len(sm)**0.5) if len(sm)>1 and st.pstdev(sm) else 0),(100*sum(r['v']>0 for r in rows)/len(rows) if rows else 0)
def rep(l,rows):
    n,s,m,t,w=sess(rows); print(f"  {l:40s} n {n:5d} sess {s:3d} mean {m:+.2f} t {t:+.2f} win {w:.0f}%")
print("== ① 시차 점검: buyback_days 분포 (음수면 lookahead) ==")
bb=[r for r in X if r['bd'] is not None]; print('  buyback_days 있는 행',len(bb),'min',min(r['bd'] for r in bb),'max',max(r['bd'] for r in bb), '음수',sum(1 for r in bb if r['bd']<0))
# 원장 date가 접수일인지: kr_dart_terms의 date vs rcept_no 앞 8자리
terms=[json.loads(l) for l in (ROOT/'data/shadow/kr_dart_terms.jsonl').open(encoding='utf-8') if l.strip()]
mis=sum(1 for t in terms if t.get('kind')=='buyback' and t.get('rcept_no','')[:8]!=t.get('date','').replace('-',''))
print('  자사주 원장 date≠접수번호 날짜 건수:',mis,'/',sum(1 for t in terms if t.get('kind')=='buyback'))
print("\n== ② 창 감쇠: 공시 후 경과일 × 깊은 눌림 (xkr_fallen3 ≤−5% 부분집합) ==")
F=[r for r in X if (r['f'].get('chg') or 0)<=-5 and r['dart']]
for lo,hi in ((0,7),(8,15),(16,30),(31,60),(61,120)):
    rep(f'공시 후 {lo}~{hi}일 · 깊음',[r for r in F if r['bd'] is not None and lo<=r['bd']<=hi and deep(r)])
    rep(f'공시 후 {lo}~{hi}일 · 얕음',[r for r in F if r['bd'] is not None and lo<=r['bd']<=hi and not deep(r)])
rep('자사주 없음(≥120일 또는 없음) · 깊음',[r for r in F if (r['bd'] is None or r['bd']>120) and deep(r)])
rep('자사주 없음 · 얕음',[r for r in F if (r['bd'] is None or r['bd']>120) and not deep(r)])
# 가격 CSV
PX={}
def bars(tk):
    if tk in PX: return PX[tk]
    p=ROOT/'data/price/kr'/f'kr_{tk}.csv'; out=None
    if p.exists():
        rows=list(csv.DictReader(p.open(encoding='utf-8-sig'))); out=([r['date'] for r in rows],{r['date']:(float(r['open']),float(r['high']),float(r['low']),float(r['close'])) for r in rows})
    PX[tk]=out; return out
def plain(rows,hold,delay=0):
    out=[]
    for r in rows:
        b=bars(r['tk'])
        if not b or r['sd'] not in b[1]: continue
        d,bd=b; i=d.index(r['sd'])+delay
        if i+1+hold>=len(d): continue
        e=bd[d[i+1]][0]; x=bd[d[i+hold]][3]
        if e>0: out.append(dict(sd=r['sd'],v=(x/e-1)*100-0.25))
    return out
cell=[r for r in F if r['bd'] is not None and 0<=r['bd']<=30 and deep(r)]; ctrl=[r for r in F if (r['bd'] is None or r['bd']>30) and deep(r)]
print("\n== ③ 출구 독립 (TP/SL 없이 k봉 종가) ==")
for hold in (2,5,7,10): a=sess(plain(cell,hold)); b=sess(plain(ctrl,hold)); print(f"  hold {hold:2d}: 자사주·깊음 n {a[0]} {a[2]:+.2f} t {a[3]:+.2f} | 비자사주·깊음 n {b[0]} {b[2]:+.2f} t {b[3]:+.2f}")
print("\n== ④ 진입 지연 (신호 다음날 시가 → 그 다음날 시가, D7 종가) ==")
a=sess(plain(cell,7,0)); b=sess(plain(cell,7,1)); print(f"  즉시 {a[2]:+.2f} (n {a[0]}) | 하루 지연 {b[2]:+.2f} (n {b[0]}) t {b[3]:+.2f}")
print("\n== ⑤ 세션 매칭 부트스트랩: 같은 세션의 비자사주·깊음 종목을 같은 개수로 1000회 추출 ==")
bys=defaultdict(list)
for r in ctrl: bys[r['sd']].append(r['v'])
draws=[]
for _ in range(1000):
    vals=[]
    for r in cell:
        pool=bys.get(r['sd'])
        vals.append(random.choice(pool) if pool else r['v']*0)
    draws.append(st.mean(vals))
obs=st.mean(r['v'] for r in cell); draws.sort()
print(f"  관측 {obs:+.2f} | 매칭 대조 평균 {st.mean(draws):+.2f} 95% [{draws[25]:+.2f},{draws[975]:+.2f}] | P(대조≥관측) {sum(x>=obs for x in draws)/1000:.3f} | 대조 없는 세션 {sum(1 for r in cell if r['sd'] not in bys)}/{len(cell)}")
