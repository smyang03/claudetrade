# -*- coding: utf-8 -*-
"""지수 ETF 오버나이트/인트라데이 신호 전면 스윕 (2026-09-10, 용량 우회 방향 확장).

배경: 개별주 K=1~3 실행은 6번 다 죽었고(알파=용량), 지수 ETF 이식은 처음으로 통했다
(코스닥 갭≤−1 or 종가≤−2.5 → 11년 250건 t 3.40 연도 11/11).
그러면 지수 ETF에서 **다른 신호도 있는지** 같은 규율로 전수 탐색한다.

규율(다중비교 방어):
 - 모든 특성은 그날 종가 시점에 확정(no-lookahead). 미국 전일 등락은 KR 개장 전 확정이라 사용 가능.
 - 셀 수를 세고, **기간 3분할 전부 같은 부호 + 연도 양수 비율 ≥70%**를 넘긴 것만 후보로 부른다.
 - 판정 아님. 후보는 사전등록 후 forward로만 승격.

실행 형태 2종:
 ON  오버나이트: 그날 종가 매수 → 다음날 시가 매도
 ID  인트라데이: 다음날 시가 매수 → 다음날 종가 매도
사용: python tools/research/research_index_etf_signal_sweep.py <scratchpad_dir>
"""
import csv
import statistics as st
import sys
from pathlib import Path

SP = Path(sys.argv[1])
COST = 0.05


def load(n):
    return sorted([(r['date'], float(r['open']), float(r['high']), float(r['low']), float(r['close']))
                   for r in csv.DictReader((SP / f'{n}.csv').open(encoding='utf-8'))])


def feats(bars, us_prev):
    """date -> 특성 dict. 전부 그날 종가까지의 정보."""
    out = {}
    for i in range(20, len(bars)):
        d, o, h, lo, c = bars[i]
        pc = bars[i - 1][4]
        if pc <= 0 or h <= lo:
            continue
        w = bars[i - 19:i + 1]
        ma = st.mean(x[4] for x in w)
        rets = [(w[k][4] / w[k - 1][4] - 1) * 100 for k in range(1, len(w))]
        streak = 0
        for k in range(i, 0, -1):
            if bars[k][4] < bars[k - 1][4]:
                streak += 1
            else:
                break
        out[d] = {
            'gap': (o / pc - 1) * 100,
            'chg': (c / pc - 1) * 100,
            'ibs': (c - lo) / (h - lo) * 100,
            'ma20_disc': (c / ma - 1) * 100,
            'rv20': st.pstdev(rets),
            'cum5': (c / bars[i - 5][4] - 1) * 100,
            'down_streak': streak,
            'range_pct': (h - lo) / pc * 100,
            'us_prev': us_prev.get(d, {}).get('chg'),
            'dow': __import__('datetime').date.fromisoformat(d).weekday(),
        }
    return out


def us_prev_map(kr_dates, us_bars):
    """KR 날짜 -> 그 전 미국 거래일 등락(KR 개장 전 확정)."""
    us = [(d, (c / us_bars[i - 1][4] - 1) * 100) for i, (d, _, _, _, c) in enumerate(us_bars) if i]
    out, j = {}, 0
    for d in kr_dates:
        while j + 1 < len(us) and us[j + 1][0] < d:
            j += 1
        if us and us[j][0] < d:
            out[d] = {'chg': us[j][1]}
    return out


def rets(bars, form):
    out = {}
    for i in range(len(bars) - 1):
        d = bars[i][0]
        nxt = bars[i + 1]
        out[d] = ((nxt[1] / bars[i][4] - 1) * 100 - COST) if form == 'ON' else ((nxt[4] / nxt[1] - 1) * 100 - COST)
    return out


def cell(sel, base_mean):
    xs = [r for _, r in sel]
    if len(xs) < 25:
        return None
    ex = [x - base_mean for x in xs]
    t = st.mean(ex) / (st.pstdev(ex) / len(ex) ** 0.5)
    byy = {}
    for d, r in sel:
        byy.setdefault(d[:4], []).append(r - base_mean)
    posy = sum(1 for y in byy if st.mean(byy[y]) > 0)
    thirds = []
    n = len(sel)
    for a, b in ((0, n // 3), (n // 3, 2 * n // 3), (2 * n // 3, n)):
        seg = [r - base_mean for _, r in sel[a:b]]
        thirds.append(st.mean(seg) if seg else 0.0)
    return dict(n=len(xs), mean=st.mean(xs), ex=st.mean(ex), t=t, win=100 * sum(x > 0 for x in xs) / len(xs),
                posy=posy, ny=len(byy), thirds=thirds, worst=min(xs))


LADDERS = {
    'gap': [(-99, -2), (-2, -1), (-1, -0.3), (-0.3, 0.3), (0.3, 99)],
    'chg': [(-99, -2.5), (-2.5, -1), (-1, 0), (0, 1), (1, 99)],
    'ibs': [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)],
    'ma20_disc': [(-99, -5), (-5, -2), (-2, 2), (2, 5), (5, 99)],
    'rv20': [(0, 1), (1, 1.5), (1.5, 2.2), (2.2, 99)],
    'cum5': [(-99, -5), (-5, -2), (-2, 2), (2, 99)],
    'down_streak': [(0, 1), (1, 2), (2, 3), (3, 99)],
    'range_pct': [(0, 1.2), (1.2, 2), (2, 3), (3, 99)],
    'us_prev': [(-99, -1.5), (-1.5, -0.5), (-0.5, 0.5), (0.5, 99)],
    'dow': [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)],
}

ETFS = [('233740', 'kosdaq_ohlc', 'etf_233740'), ('229200', 'kosdaq_ohlc', 'etf_229200'), ('069500', 'kospi_ohlc', 'etf_069500')]
SPY = load('us_SPY')
cells_tested = 0
survivors = []

for tk, idx_name, etf_name in ETFS:
    idx = load(idx_name)
    ebars = load(etf_name)
    F = feats(idx, us_prev_map([d for d, *_ in idx], SPY))
    print(f"\n{'='*100}\n{tk} (신호 {idx_name}) — ETF {ebars[0][0]}~{ebars[-1][0]}")
    for form in ('ON', 'ID'):
        R = rets(ebars, form)
        base = sorted([(d, r) for d, r in R.items() if d in F])
        bm = st.mean(r for _, r in base)
        print(f"\n  [{form}] 무조건 기저 {bm:+.4f}%/회 n {len(base)}")
        for f, bins in LADDERS.items():
            row = f"    {f:12s}"
            for lo, hi in bins:
                sel = [(d, r) for d, r in base if F[d].get(f) is not None and lo <= F[d][f] < hi]
                cells_tested += 1
                c = cell(sel, bm)
                if c is None:
                    row += f" {'·':>13}"
                    continue
                mark = '*' if (c['t'] >= 2 and all(x > 0 for x in c['thirds']) and c['posy'] / c['ny'] >= 0.70) else ' '
                row += f" {c['ex']:+.3f}/t{c['t']:+.1f}{mark}"
                if mark == '*':
                    survivors.append((tk, form, f, (lo, hi), c))
            print(row)

print(f"\n{'='*100}")
print(f"검정한 셀 {cells_tested}개 · 생존 조건(초과 t≥2 & 기간3분할 전부 양수 & 연도 양수비율≥70%) 통과 {len(survivors)}개\n")
print("ETF     형태 특성           구간              n   평균    초과   t     승률  연도    3분할              최악")
for tk, form, f, (lo, hi), c in sorted(survivors, key=lambda x: -x[4]['t']):
    print(f"{tk:7s} {form:4s} {f:14s} [{lo:>5},{hi:>5})  {c['n']:4d} {c['mean']:+.3f} {c['ex']:+.3f} {c['t']:+5.2f} "
          f"{c['win']:4.1f}% {c['posy']}/{c['ny']:<3d} " + " ".join(f"{x:+.2f}" for x in c['thirds']) + f"  {c['worst']:+.2f}")
