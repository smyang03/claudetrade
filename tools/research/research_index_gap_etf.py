# -*- coding: utf-8 -*-
"""개별주 조건 → 지수 ETF 이식 (2026-09-10, 강제매도 가설 6차 = 용량 우회).

배경: 개별주에서 찾은 조건은 6번 연속 "조건 전량이면 t≈2, K=1~3으로 고르면 t≈0"이었다(알파=용량).
지수 ETF는 K=1이 곧 전량이라 용량 제약이 없다. 개별주에서 통한 세션 조건을 지수 ETF 매매로 옮겨 10년 이상 검증한다.
조건 후보(전부 그날 종가 시점에 확정, no-lookahead):
  G  지수 시가 갭 ≤ 문턱      (밤사이 악재 → 동시호가 강제 매도)
  C  지수 종가 등락률 ≤ 문턱   (현행 패닉 레인 규칙)
  G&C, G|C, 그리고 MA20 아래 조건 결합
실행: 그날 종가 매수 → 다음날 시가 매도. 비용 0.05%(ETF 왕복). 069500 2010~, 229200 2015-10~, 233740 2015-12~.
사용: python tools/research/research_index_gap_etf.py <scratchpad_dir>
"""
import csv, statistics as st, sys
from pathlib import Path
SP = Path(sys.argv[1]); COST = 0.05

def load(name):
    out = []
    for r in csv.DictReader((SP / f'{name}.csv').open(encoding='utf-8')):
        try: out.append((r['date'], float(r['open']), float(r['high']), float(r['low']), float(r['close'])))
        except ValueError: pass
    return sorted(out)

IDX = {'KOSPI': load('kospi_ohlc'), 'KOSDAQ': load('kosdaq_ohlc')}
ETF = {'069500': ('KODEX200', 'KOSPI', load('etf_069500')), '229200': ('KODEX코스닥150', 'KOSDAQ', load('etf_229200')),
       '233740': ('KODEX코스닥150레버', 'KOSDAQ', load('etf_233740'))}

def sig(idx):
    """date → (gap%, chg%, below_ma20)"""
    out = {}
    for i in range(20, len(idx)):
        d, o, _, _, c = idx[i]; pc = idx[i - 1][4]
        if pc <= 0: continue
        ma = st.mean(x[4] for x in idx[i - 19:i + 1])
        out[d] = ((o / pc - 1) * 100, (c / pc - 1) * 100, c < ma)
    return out
S = {k: sig(v) for k, v in IDX.items()}

def test(tk, cond, label, start='2000-01-01'):
    name, ix, bars = ETF[tk]
    by = {b[0]: i for i, b in enumerate(bars)}
    rets, base = [], []
    for i in range(len(bars) - 1):
        d = bars[i][0]
        if d < start or d not in S[ix]: continue
        g, c, below = S[ix][d]
        nxt = bars[i + 1]
        r = (nxt[1] / bars[i][4] - 1) * 100 - COST
        base.append((d, r))
        if cond(g, c, below): rets.append((d, r))
    if not rets: print(f"  {label:34s} n 0"); return
    xs = [r for _, r in rets]; bx = [r for _, r in base]
    ex = [r - st.mean(bx) for r in xs]
    t = st.mean(xs) / (st.pstdev(xs) / len(xs) ** 0.5) if len(xs) > 2 else float('nan')
    te = st.mean(ex) / (st.pstdev(ex) / len(ex) ** 0.5) if len(ex) > 2 else float('nan')
    yrs = sorted({d[:4] for d, _ in rets})
    pos = sum(1 for y in yrs if st.mean([r for d, r in rets if d[:4] == y]) > 0)
    print(f"  {label:34s} n {len(xs):4d} 평균 {st.mean(xs):+.3f} t {t:+5.2f} | 초과 {st.mean(ex):+.3f} t {te:+5.2f} | "
          f"승 {100*sum(x>0 for x in xs)/len(xs):4.1f}% 최악 {min(xs):+6.2f} 양수연도 {pos}/{len(yrs)}")

for tk in ('233740', '229200', '069500'):
    name, ix, bars = ETF[tk]
    print(f"\n== {tk} {name} ({ix}) {bars[0][0]}~{bars[-1][0]} · 무조건 오버나이트 기저 ==")
    test(tk, lambda g, c, b: True, "무조건(기저)")
    print("  ── 지수 시가 갭 사다리")
    for th in (-0.5, -1.0, -1.5, -2.0, -2.5):
        test(tk, lambda g, c, b, th=th: g <= th, f"갭 ≤ {th}%")
    print("  ── 지수 종가 등락 사다리(현행 규칙)")
    for th in (-1.5, -2.0, -2.5, -3.0):
        test(tk, lambda g, c, b, th=th: c <= th, f"종가 ≤ {th}%")
    print("  ── 결합")
    test(tk, lambda g, c, b: g <= -1.0 and c <= -2.5, "갭≤−1 & 종가≤−2.5")
    test(tk, lambda g, c, b: g <= -1.0 or c <= -2.5, "갭≤−1 또는 종가≤−2.5")
    test(tk, lambda g, c, b: g <= -1.0 and c > -2.5, "갭≤−1 & 종가>−2.5 (갭만)")
    test(tk, lambda g, c, b: g > -1.0 and c <= -2.5, "갭>−1 & 종가≤−2.5 (장중만)")
    print("  ── MA20 결합")
    test(tk, lambda g, c, b: c <= -2.5 and b, "종가≤−2.5 & MA20 아래")
    test(tk, lambda g, c, b: c <= -2.5 and not b, "종가≤−2.5 & MA20 위")
    test(tk, lambda g, c, b: (g <= -1.0 or c <= -2.5) and b, "(갭≤−1 or 종가≤−2.5) & MA20아래")

print("\n== 기간 분할 — 233740 주요 조건 ==")
for lab, s in (("2016-01~2018-12", '2016-01-01'), ("2019-01~2021-12", '2019-01-01'), ("2022-01~2026-09", '2022-01-01')):
    print(f"  [{lab}]")
    for cl, cond in (("종가≤−2.5", lambda g, c, b: c <= -2.5), ("갭≤−1", lambda g, c, b: g <= -1.0),
                     ("갭≤−1 or 종가≤−2.5", lambda g, c, b: g <= -1.0 or c <= -2.5)):
        test('233740', cond, "    " + cl, start=s)
