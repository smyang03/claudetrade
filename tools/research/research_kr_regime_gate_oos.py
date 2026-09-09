# -*- coding: utf-8 -*-
"""KR '지수 MA20 아래 국면' 게이트 OOS (2026-09-10). 인샘플(virtual_books 2025-06~2026-09) KR 10 arm 중 9개에서 아래>위였다.
패널 A: DART 자사주 종목 250개 yfinance 일봉(스크래치패드 kr_oos_px, 미수정가) 2023-01~2025-05 — 전일 ≤−5%(−30% 초과 급락은 분할 의심 제외) & 거래대금≥20억, 다음 시가 진입.
패널 B: data/analysis/kr_fallen_price_cache_2025.json (634종목, 2024-11~2026-01) 중 인샘플 이전 2024-12~2025-05.
계약: 저장소 contract_exit_v2 (TP12/SL25/D7, KR 수수료, BE 없음). 국면: KOSPI/KOSDAQ 종가 ≥ 20일 MA(신호일 포함) — discovery_pools와 동일 정의.
사용: python tools/research/research_kr_regime_gate_oos.py <scratchpad_dir>"""
import csv, json, statistics as st, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, 'tools')
from virtual_books import contract_exit_v2, FEE_KR, HOLD_SESSIONS
SP = Path(sys.argv[1])
def load_idx(name):
    rows = sorted((r['date'], float(r['close'])) for r in csv.DictReader((SP / f'{name}_index.csv').open(encoding='utf-8')))
    out = {}
    for i in range(20, len(rows)):
        d, c = rows[i]; ma = st.mean(x[1] for x in rows[i - 19:i + 1])
        out[d] = (c >= ma, 100 * (c / rows[i - 20][1] - 1))
    return out
IDX = {'kospi': load_idx('kospi'), 'kosdaq': load_idx('kosdaq')}
def sess(rows):
    by = defaultdict(list)
    for d, v in rows: by[d].append(v)
    xs = [st.mean(v) for v in by.values()]
    if len(xs) < 3: return f"n {len(rows)} 세션 {len(xs)} 평균 {st.mean(xs) if xs else float('nan'):+.2f}"
    sd = st.pstdev(xs); t = st.mean(xs) / (sd / len(xs) ** 0.5) if sd else float('nan')
    return f"n {len(rows):5d} 세션 {len(xs):3d} 평균 {st.mean(xs):+.2f} t {t:+.2f} 승 {100*sum(v>0 for _,v in rows)/len(rows):.0f}%"
def run(bars_by_tk, start, end, label):
    R = []
    for tk, b in bars_by_tk.items():
        for i in range(21, len(b) - HOLD_SESSIONS - 1):
            d = b[i][0]
            if d < start or d > end: continue
            c0, c = b[i - 1][4], b[i][4]
            if c0 <= 0 or c <= 0: continue
            chg = (c / c0 - 1) * 100
            if chg > -5 or chg <= -30 or c * b[i][5] < 2e9: continue
            res = contract_exit_v2(b[i + 1][1], b[i + 1:i + 1 + HOLD_SESSIONS], fee=FEE_KR, be_lock=False)
            if not res: continue
            R.append(dict(d=d, tk=tk, v=res[0], y=d[:4], ks=IDX['kospi'].get(d), kq=IDX['kosdaq'].get(d)))
    print(f"\n== {label} {start}~{end} ==")
    print("전량            :", sess([(r['d'], r['v']) for r in R]))
    for idx in ('ks', 'kq'):
        for state, lab in ((False, '아래'), (True, '위')):
            sub = [r for r in R if r[idx] is not None and r[idx][0] == state]
            print(f"{'KOSPI' if idx=='ks' else 'KOSDAQ'} MA20 {lab:2s}  :", sess([(r['d'], r['v']) for r in sub]))
        print(f"  연도별({'KOSPI' if idx=='ks' else 'KOSDAQ'} 아래/위): " + " | ".join(f"{y} " + " / ".join(f"{st.mean([r['v'] for r in R if r['y']==y and r[idx] is not None and r[idx][0]==s]) if [r for r in R if r['y']==y and r[idx] is not None and r[idx][0]==s] else float('nan'):+.2f}(n{len([r for r in R if r['y']==y and r[idx] is not None and r[idx][0]==s])})" for s in (False, True)) for y in sorted({r['y'] for r in R})))
    return R
# 패널 A
A = {}
for p in (SP / 'kr_oos_px').glob('*.csv'):
    b = []
    for r in csv.DictReader(p.open(encoding='utf-8')):
        try: b.append((r['Date'][:10], float(r['Open']), float(r['High']), float(r['Low']), float(r['Close']), float(r['Volume'] or 0)))
        except (ValueError, KeyError): pass
    if len(b) > 60: A[p.stem] = b
run(A, '2023-01-01', '2025-05-31', f'패널 A 자사주 종목 {len(A)}개(yfinance)')
# 패널 B
raw = json.load(open('data/analysis/kr_fallen_price_cache_2025.json', encoding='utf-8'))
B = {tk: [(x['d'], x['o'], x['h'], x['l'], x['c'], x['v']) for x in v if x.get('o') and x.get('c')] for tk, v in raw.items()}
run(B, '2024-12-01', '2025-05-31', f'패널 B 634종목 캐시(인샘플 이전 6개월)')
# 참고: 패널 B 인샘플 구간(2025-06~2025-12)으로 방법 재현
run(B, '2025-06-01', '2025-12-31', '패널 B 인샘플 구간 재현(2025-06~12)')
