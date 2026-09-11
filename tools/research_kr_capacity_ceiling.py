import sys, json, statistics as st
sys.path.insert(0, r'E:\code\claudetrade')
from tools.research_kr_rank_pool_reconcile import build, by_session, RANKS, random_chooser, sequence
from tools.kr_fallen_ranking_experiment import CACHES

for year, path in CACHES.items():
    ss = by_session(build(json.loads(path.read_text(encoding="utf-8"))))
    months = len({d[:7] for d in ss})
    tot = sum(len(v) for v in ss.values())
    print(f"\n=== {year} — 세션 {len(ss)}일 / {months}개월 / 후보 {tot}건 ===")
    print(" 슬롯x일한도 |   최대노출 |        손익 | 진입 | 포획률 | 월손익 | 노출대비")
    for slots, per_day in ((3,1),(10,3),(20,3),(40,5),(80,10),(200,30),(10**6,10**6)):
        pnl, tr = sequence(ss, random_chooser(0), slots=slots, per_day=per_day)
        exp = min(slots, 10**6) * 220_000
        lab = "무제한" if slots > 10**5 else f"{slots} x {per_day}"
        print(f" {lab:>11} | {exp if slots<10**5 else 0:>10,} | {pnl:>+11,.0f} | {len(tr):>4} | "
              f"{len(tr)/tot*100:>5.1f}% | {pnl/months:>+7,.0f} | "
              f"{pnl/exp*100 if slots<10**5 else 0:>6.1f}%")

    print("\n  [후보수 구간별 후보당 net — 사냥철 명제]")
    from collections import defaultdict
    g = defaultdict(list)
    for d, v in ss.items():
        k = len(v)
        b = "k=1" if k==1 else "k=2~4" if k<=4 else "k=5~9" if k<=9 else "k=10~19" if k<=19 else "k>=20"
        g[b].extend(x["net"] for x in v)
    for b in ("k=1","k=2~4","k=5~9","k=10~19","k>=20"):
        if g[b]:
            n=g[b]; print(f"   {b:>8}: n={len(n):>4} 평균 {st.mean(n):+6.2f}% 승률 {sum(x>0 for x in n)/len(n)*100:4.0f}%")
