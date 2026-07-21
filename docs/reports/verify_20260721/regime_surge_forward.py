"""국면×급등 forward 검증 — 불장에서 급등종목이 이어지나(추세 진입 가치).
근거(2026-07-21): 운영자 질문 "불장도 못 먹나". KR/US 정반대 발견.
사용: python docs/reports/verify_20260721/regime_surge_forward.py
"""
from __future__ import annotations
import sqlite3, json, glob, os, statistics
from collections import defaultdict
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]

def load_regime():
    rd={}
    for f in glob.glob(str(ROOT/"logs"/"daily_judgment"/"live_*_KR.json"))+glob.glob(str(ROOT/"logs"/"daily_judgment"/"live_*_US.json")):
        try: d=json.load(open(f,encoding="utf-8"))
        except: continue
        b=os.path.basename(f); mkt="KR" if "_KR" in b else "US"
        day=b.split("_")[1]; sd=f"{day[:4]}-{day[4:6]}-{day[6:8]}"
        ch=((d.get("digest_raw") or {}).get("context") or {}).get("kospi" if mkt=="KR" else "sp500",{}).get("change_pct")
        if ch is not None:
            try: rd[(mkt,sd)]=float(ch)
            except: pass
    return rd

def load_chg():
    cm={}
    for f in sorted(glob.glob(str(ROOT/"logs"/"screener_quality"/"202*_candidates.jsonl"))):
        b=os.path.basename(f); day=b[:8]; mkt=b.split("_")[1]; sd=f"{day[:4]}-{day[4:6]}-{day[6:8]}"
        if sd<"2026-06-01": continue
        for line in open(f,encoding="utf-8"):
            try: dd=json.loads(line)
            except: continue
            k=(mkt,sd,str(dd.get("ticker") or ""))
            if k not in cm and dd.get("change_rate") is not None: cm[k]=dd.get("change_rate")
    return cm

def main():
    rd=load_regime(); cm=load_chg()
    con=sqlite3.connect(f"file:{ROOT/'data'/'audit'/'candidate_audit.db'}?mode=ro",uri=True); con.row_factory=sqlite3.Row
    con.execute("pragma busy_timeout=5000")
    res=defaultdict(lambda: defaultdict(list))
    for r in con.execute("""select r.market,r.session_date,r.ticker,o.horizon_min,o.return_pct
    from audit_candidate_rows r join audit_candidate_outcomes o on o.candidate_key=r.candidate_key
    where r.session_date>='2026-06-01' and o.horizon_min = 60 and o.return_pct is not null"""):
        mkt=str(r["market"]); sd=str(r["session_date"])
        idx=rd.get((mkt,sd)); chg=cm.get((mkt,sd,str(r["ticker"])))
        if idx is None or chg is None or float(chg)<7: continue
        dt="강세일" if idx>=1.0 else ("약세일" if idx<=-1.0 else "중립일")
        res[mkt][dt].append(float(r["return_pct"]))
    for mkt,dt in sorted(res.items()):
        print(f"=== {mkt} 급등종목(chg7%+) forward60 세션국면별 ===")
        for d in ("강세일","중립일","약세일"):
            v=dt.get(d,[])
            if v:
                v2=sorted(v)
                print(f"  {d} n={len(v):>4} mean={statistics.fmean(v):+.3f}% p90={v2[int(len(v)*.9)]:+.2f} 상위10%={statistics.fmean(v2[int(len(v)*.9):]):+.2f}")
        print()
    print("판정: US 강세일=양수+볼록꼬리(추세진입 기회), KR 강세일=음수(추격 금지). 정반대.")

if __name__=="__main__":
    main()
