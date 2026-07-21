# 재정렬 net 이득 검증 — change_rate(진입급등률) 밴드별 (A)실체결 net (B)forward net-proxy
# 재정렬 로직: 중간모멘텀(3~7%) 우선, 급등(15%+) 후순위. 그 방향이 우리 net으로도 맞는가.
from __future__ import annotations
import sqlite3, json, glob, os, statistics
from collections import defaultdict
from pathlib import Path
ROOT = Path(r"E:\code\claudetrade")

def band(chg):
    if chg is None: return "?"
    c=float(chg)
    if c<0: return "0_neg"
    if c<3: return "a_0_3"
    if c<7: return "b_3_7(재정렬선호)"
    if c<15: return "c_7_15"
    return "d_15+"

# --- change_rate 인덱스: (mkt,date,ticker) -> change_rate (screener_quality 첫 관측) ---
chg_map={}
for f in sorted(glob.glob(str(ROOT/"logs"/"screener_quality"/"202*_KR_candidates.jsonl"))):
    day=os.path.basename(f)[:8]; sd=f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    if sd<'2026-05-01': continue
    for line in open(f,encoding='utf-8'):
        try: d=json.loads(line)
        except: continue
        k=(sd,str(d.get('ticker') or ''))
        if k not in chg_map and d.get('change_rate') is not None:
            chg_map[k]=d.get('change_rate')

# --- (A) 실체결 net by band ---
con=sqlite3.connect(f"file:{ROOT/'data'/'ml'/'decisions.db'}?mode=ro",uri=True); con.row_factory=sqlite3.Row
con.execute("pragma busy_timeout=5000")
fills=[dict(r) for r in con.execute("""select session_date,ticker,coalesce(pnl_pct_net,pnl_pct) net
from v2_learning_performance where runtime_mode='live' and closed=1 and market='KR' and session_date>='2026-05-01'""")]
con.close()
A=defaultdict(list)
for r in fills:
    c=chg_map.get((r['session_date'],str(r['ticker'])))
    if c is not None and r['net'] is not None: A[band(c)].append(r['net'])
print("=== (A) KR 실체결 net by 진입급등률 밴드 (표본 작음) ===")
for b in ("0_neg","a_0_3","b_3_7(재정렬선호)","c_7_15","d_15+"):
    v=A.get(b,[])
    if v: print(f"  {b:20} n={len(v):>2} mean_net={statistics.fmean(v):+.3f}% win={sum(1 for x in v if x>0)/len(v):.0%}")

# --- (B) forward 60min net-proxy by band (표본 큼) ---
KR_ROUNDTRIP_COST=0.35  # KR 왕복 근사(수수료+세금+슬리피지 보수적)
acon=sqlite3.connect(f"file:{ROOT/'data'/'audit'/'candidate_audit.db'}?mode=ro",uri=True); acon.row_factory=sqlite3.Row
acon.execute("pragma busy_timeout=5000")
B=defaultdict(list)
for r in acon.execute("""select r.session_date,r.ticker,o.return_pct
from audit_candidate_rows r join audit_candidate_outcomes o on o.candidate_key=r.candidate_key
where r.market='KR' and r.session_date>='2026-05-01' and o.horizon_min=60 and o.return_pct is not null"""):
    c=chg_map.get((str(r['session_date']),str(r['ticker'])))
    if c is not None:
        B[band(c)].append(float(r['return_pct'])-KR_ROUNDTRIP_COST)  # net-proxy = forward - 비용
acon.close()
print(f"\n=== (B) KR forward60 net-proxy(=forward-{KR_ROUNDTRIP_COST}%) by 밴드 (표본 큼) ===")
for b in ("0_neg","a_0_3","b_3_7(재정렬선호)","c_7_15","d_15+"):
    v=B.get(b,[])
    if v: print(f"  {b:20} n={len(v):>4} mean={statistics.fmean(v):+.3f}% median={statistics.median(v):+.3f}% pos={sum(1 for x in v if x>0)/len(v):.0%}")
print("\n판정: b_3_7(재정렬선호)가 c_7_15·d_15+보다 net(-proxy) 높으면 재정렬 방향 지지")
