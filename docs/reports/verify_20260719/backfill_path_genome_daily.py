"""path_genome 일봉 백필 — 우리 315 트레이드의 진입 유니버스에서 ride-규칙 검증.
외부 대형주가 아니라 '우리가 실제 진입한 종목/날짜'로 일봉 경로 재구성 → ride-규칙이
우리 book에도 통하는지 사전 검증. 결과 durable 저장. read-only(DB)."""
import sqlite3, csv, os, json
from datetime import datetime

conn = sqlite3.connect('file:data/ml/decisions.db?mode=ro', uri=True, timeout=10)
conn.execute('PRAGMA busy_timeout=5000'); conn.row_factory = sqlite3.Row
T = conn.execute(
    "SELECT ticker,market,session_date,close_reason,pnl_pct_net,strategy,market_regime "
    "FROM v2_learning_performance WHERE closed=1 AND pnl_pct_net IS NOT NULL AND session_date IS NOT NULL"
).fetchall()
conn.close()

COST = 0.30


def daily_path(mk, tk, entry_date, horizon=11):
    p = os.path.join('data', 'price', mk.lower(), f'{mk.lower()}_{tk}.csv')
    if not os.path.exists(p):
        return None
    rows = []
    try:
        for r in csv.DictReader(open(p, encoding='utf-8-sig')):
            d = str(r.get('date', '')).strip(); c = r.get('close'); h = r.get('high'); lo = r.get('low')
            if d and c not in (None, ''):
                try: rows.append((d, float(c), float(h or c), float(lo or c)))
                except: pass
    except OSError:
        return None
    rows.sort()
    # entry_date 이후(포함) 인덱스
    idx = next((i for i, x in enumerate(rows) if x[0] >= entry_date), None)
    if idx is None or idx + horizon >= len(rows):
        return None
    return rows[idx:idx + horizon + 1]  # d0..d(horizon)


records = []
base_all, rule_all = [], []
cov = 0
for t in T:
    path = daily_path(t['market'], t['ticker'], t['session_date'])
    if not path:
        continue
    cov += 1
    c0 = path[0][1]
    if c0 <= 0:
        continue
    closes = [x[1] for x in path]
    d1 = (closes[1] / c0 - 1) * 100
    d5 = (closes[5] / c0 - 1) * 100
    d10 = (closes[10] / c0 - 1) * 100
    # 일봉 genome
    fwd = [(closes[i] / c0 - 1) * 100 for i in range(len(closes))]
    peak_day = max(range(1, len(fwd)), key=lambda i: fwd[i])
    low_day = min(range(1, len(fwd)), key=lambda i: fwd[i])
    mfe = fwd[peak_day]; mae = fwd[low_day]
    shape = "dip_then_run" if low_day < peak_day else ("run_then_giveback" if peak_day < low_day else "flat")
    early_confirmed = d1 > 0
    ride_candidate = early_confirmed and shape != "run_then_giveback"
    # baseline=d5 보유, rule=확인(d1>1)이면 d10 연장 else d5
    base_all.append(d5 - COST)
    rule_all.append((d10 - COST) if d1 > 1 else (d5 - COST))
    records.append({
        "ticker": t['ticker'], "market": t['market'], "session_date": t['session_date'],
        "close_reason": t['close_reason'], "actual_pnl_pct_net": t['pnl_pct_net'],
        "strategy": t['strategy'], "market_regime": t['market_regime'],
        "d1": round(d1, 3), "d5": round(d5, 3), "d10": round(d10, 3),
        "mfe_daily": round(mfe, 3), "mae_daily": round(mae, 3),
        "peak_day": peak_day, "low_day": low_day, "shape": shape,
        "early_confirmed": early_confirmed, "ride_candidate": ride_candidate,
        "source": "daily_backfill",
    })

import statistics as st
print(f"백필 커버 {cov}/{len(T)}, 유효 {len(records)}건\n")

# ★우리 유니버스에서 ride-규칙 검증
b = base_all; r = rule_all
print("=== ★우리 진입 유니버스 ride-규칙 검증 (일봉) ===")
print(f"  baseline(d5보유): 평균 {sum(b)/len(b):+.3f}% 승률 {100*sum(1 for x in b if x>0)/len(b):.0f}%")
print(f"  ride규칙(확인→d10):평균 {sum(r)/len(r):+.3f}% 승률 {100*sum(1 for x in r if x>0)/len(r):.0f}%")
print(f"  개선 {sum(r)/len(r)-sum(b)/len(b):+.3f}%p/건 → {'우리 book에도 실익' if sum(r)/len(r)>sum(b)/len(b) else '우리 book엔 실익 없음(외부와 다름)'}\n")

# genome 분포 + 실제 outcome
print("=== genome(일봉) 분포 × 실제 net ===")
from collections import defaultdict
byshape = defaultdict(list)
for rec in records:
    byshape[rec['shape']].append(rec['actual_pnl_pct_net'])
for s, v in sorted(byshape.items(), key=lambda x: -len(x[1])):
    print(f"  {s:20} n={len(v):>3} 실제 avg={sum(v)/len(v):+.3f}%")
ride = [rec['actual_pnl_pct_net'] for rec in records if rec['ride_candidate']]
nonride = [rec['actual_pnl_pct_net'] for rec in records if not rec['ride_candidate']]
print(f"  ride_candidate: n={len(ride)} 실제 avg={sum(ride)/len(ride):+.3f}%" if ride else "  ride 없음")
print(f"  non-ride:       n={len(nonride)} 실제 avg={sum(nonride)/len(nonride):+.3f}%" if nonride else "")

# durable 저장
os.makedirs('data/analysis', exist_ok=True)
out = 'data/analysis/path_genome_daily_backfill_20260719.jsonl'
with open(out, 'w', encoding='utf-8') as f:
    for rec in records:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print(f"\n저장: {out} ({len(records)}건) — 사전축적 완료")
