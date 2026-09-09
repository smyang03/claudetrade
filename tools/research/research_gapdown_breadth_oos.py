# -*- coding: utf-8 -*-
"""갭다운 시장폭 세션 지표 OOS (2026-09-10, 강제매도 가설 4차 = 판정).

인샘플(2025-06~2026-09, xkr_fallen3 36,616건):
  갭다운비율 <20% 세션평균 −0.94 (t −2.98, 239세션) / ≥20% +1.12 (t +2.00, 66세션). 종가 기준 시장폭은 못 가름.
OOS 패널:
  A) data/analysis/kr_fallen_price_cache_2025.json (634종목, 2024-11~2026-01) → 2024-12~2025-05 (인샘플 이전)
  B) 스크래치패드 kr_oos_px (자사주 250종목 yfinance 미수정가) → 2023-01~2025-05
정의(양 패널 동일, no-lookahead): 신호일 전일 대비 ≤−3% & 거래대금 ≥20억 = 급락 풀. 세션 지표 = 그 풀에서 시가 갭 ≤−3% 비율.
진입 = 다음 세션 시가, 계약 = 저장소 contract_exit_v2 (TP12/SL25/D7, KR 비용 0.25, BE 없음). 분할 의심(≤−30%) 제외.
사용: python tools/research/research_gapdown_breadth_oos.py <scratchpad_dir>
"""
import csv, json, statistics as st, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, 'tools')
from virtual_books import contract_exit_v2, FEE_KR, HOLD_SESSIONS
SP = Path(sys.argv[1])

def build(bars_by_tk, start, end):
    """→ [{d, tk, v, gap, chg}]"""
    R = []
    for tk, b in bars_by_tk.items():
        for i in range(21, len(b) - HOLD_SESSIONS - 1):
            d = b[i][0]
            if d < start or d > end: continue
            c0, o, c, vol = b[i - 1][4], b[i][1], b[i][4], b[i][5]
            if c0 <= 0 or o <= 0 or c <= 0: continue
            chg = (c / c0 - 1) * 100
            if chg > -3 or chg <= -30 or c * vol < 2e9: continue
            res = contract_exit_v2(b[i + 1][1], b[i + 1:i + 1 + HOLD_SESSIONS], fee=FEE_KR, be_lock=False)
            if res: R.append(dict(d=d, tk=tk, v=res[0], gap=(o / c0 - 1) * 100, chg=chg))
    byd = defaultdict(list)
    for r in R: byd[r['d']].append(r)
    for d, v in byd.items():
        gr = sum(1 for x in v if x['gap'] <= -3) / len(v)
        for x in v: x['gr'] = gr; x['pool_n'] = len(v)
    return R

def cell(rows, label, indent="  "):
    if not rows:
        print(f"{indent}{label:28s} n 0"); return
    byd = defaultdict(list)
    for r in rows: byd[r['d']].append(r['v'])
    sm = [st.mean(v) for v in byd.values()]
    t = st.mean(sm) / (st.pstdev(sm) / len(sm) ** 0.5) if len(sm) > 2 and st.pstdev(sm) else float('nan')
    print(f"{indent}{label:28s} n {len(rows):5d} 세션 {len(sm):3d} 세션평균 {st.mean(sm):+6.2f} t {t:+5.2f} | "
          f"거래평균 {st.mean(r['v'] for r in rows):+6.2f} | 승 {100*sum(r['v']>0 for r in rows)/len(rows):4.1f}%")

def report(R, label, min_pool=5):
    R = [r for r in R if r['pool_n'] >= min_pool]
    print(f"\n== {label} — 급락 {len(R)}건 / {len({r['d'] for r in R})}세션 (풀 {min_pool}건 이상 세션만) ==")
    if not R: return
    print(f"   갭다운비율 중앙 {100*st.median({r['d']: r['gr'] for r in R}.values()):.1f}%")
    cell(R, "전량(기저)")
    for lo, hi in ((0, .10), (.10, .20), (.20, .35), (.35, 1.01)):
        cell([r for r in R if lo <= r['gr'] < hi], f"갭다운비율 {100*lo:.0f}~{100*hi:.0f}%")
    print("   ── 문턱 20%")
    cell([r for r in R if r['gr'] >= .20], "≥20% 전량")
    cell([r for r in R if r['gr'] >= .20 and r['gap'] <= -3], "≥20% × 갭다운주")
    cell([r for r in R if r['gr'] < .20], "<20% 전량 (걸러낼 구간)")
    cell([r for r in R if r['gr'] < .20 and r['gap'] <= -3], "<20% × 갭다운주")
    print("   ── 연도별 ≥20% / <20%")
    for y in sorted({r['d'][:4] for r in R}):
        cell([r for r in R if r['d'][:4] == y and r['gr'] >= .20], f"{y} ≥20%", indent="     ")
        cell([r for r in R if r['d'][:4] == y and r['gr'] < .20], f"{y} <20%", indent="     ")

# 패널 A
raw = json.load(open('data/analysis/kr_fallen_price_cache_2025.json', encoding='utf-8'))
A = {tk: [(x['d'], x['o'], x['h'], x['l'], x['c'], x['v']) for x in v if x.get('o') and x.get('c')] for tk, v in raw.items()}
report(build(A, '2024-12-01', '2025-05-31'), f"패널 A 634종목 캐시 2024-12~2025-05 (OOS)")
report(build(A, '2025-06-01', '2025-12-31'), f"패널 A 인샘플 구간 재현 2025-06~12")
# 패널 B
B = {}
for p in (SP / 'kr_oos_px').glob('*.csv'):
    b = []
    for r in csv.DictReader(p.open(encoding='utf-8')):
        try: b.append((r['Date'][:10], float(r['Open']), float(r['High']), float(r['Low']), float(r['Close']), float(r['Volume'] or 0)))
        except (ValueError, KeyError): pass
    if len(b) > 60: B[p.stem] = b
report(build(B, '2023-01-01', '2025-05-31'), f"패널 B 자사주 {len(B)}종목 2023-01~2025-05 (OOS)")
