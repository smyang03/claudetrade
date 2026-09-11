import sys, statistics as st
sys.path.insert(0, r'E:\code\claudetrade')
from tools.selection_account_compare import read_snapshot, simulate

cs, calendar, cfg, audit = read_snapshot(r'docs/reports/selection_account_20260911_kr/inputs.json')
print(f"pool {len(cs)}  calendar {calendar[0]}~{calendar[-1]}  cfg {cfg}")

def run(pool, label):
    out = {}
    for rule in ("baseline", "live"):
        out[rule] = simulate(pool, calendar, cfg, rule)["return_pct"]
    vals = sorted(simulate(pool, calendar, cfg, "random1", seed=s)["return_pct"] for s in range(200))
    worse = sum(v <= out["live"] for v in vals)
    worse_b = sum(v <= out["baseline"] for v in vals)
    print(f"--- {label} (n={len(pool)}) ---")
    print(f"  baseline {out['baseline']:+8.2f}%  (무작위 200 중 {worse_b}개가 이보다 나쁨)")
    print(f"  live     {out['live']:+8.2f}%  (무작위 200 중 {worse}개가 이보다 나쁨)")
    print(f"  random1  median {st.median(vals):+8.2f}%  min {vals[0]:+.2f}  max {vals[-1]:+.2f}")

run(cs, "전체 풀 (현행)")
# 라이브 price_ge=7110은 신호일 종가 기준. 스냅샷에 신호일 종가가 없어 진입 시가로 근사.
f1 = [c for c in cs if c.win and c.win[0][1] >= 7110]
run(f1, "진입시가 >= 7,110원 (라이브 price_ge 근사)")
# 유동성: 라이브는 20일 평균 거래대금 >= 10억. 여기 rank_value는 신호일 거래대금이라 다른 양이다.
f2 = [c for c in f1 if c.rank_value >= 1e9]
run(f2, "+ 신호일 거래대금 >= 10억 (라이브 amt20 대용, 같은 양 아님)")
