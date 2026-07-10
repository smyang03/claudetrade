#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""동시보유 상관 클러스터 판독 (2026-07-10) — read-only.

질문: 우리 손실의 90%가 '3종목+ 동시청산일'에 집중(실측) — 그날 포지션들이 고상관 클러스터였나?
= 상관/집중 사이징(A3)·리스크오프 청산순위(S1)가 겨냥할 손실인가.

방법: 보유기간 겹친 포지션쌍의 진입전 60일 수익률 상관(로컬 CSV, no-lookahead) →
고상관 동시보유일의 결합 net vs 저상관일. gross 상대비교(우리 net은 v2_learning). 방향 판정.
"""
import argparse
import sqlite3
import statistics as st
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML = ROOT / "data" / "ml" / "decisions.db"
PRICE = {"US": ROOT / "data" / "price" / "us", "KR": ROOT / "data" / "price" / "kr"}
PFX = {"US": "us_", "KR": "kr_"}


def _rets(market, sym, before_day, n=60):
    for cand in ([sym] if market == "US" else [sym]):
        f = PRICE[market] / f"{PFX[market]}{cand}.csv"
        if not f.exists():
            return None
        rows = []
        for line in f.read_text(encoding="utf-8-sig").splitlines()[1:]:
            p = line.split(",")
            if len(p) >= 5 and p[0] < before_day:
                try:
                    rows.append((p[0], float(p[4])))
                except ValueError:
                    pass
        if len(rows) < n + 1:
            return None
        rows = rows[-(n + 1):]
        return [(rows[i][1] / rows[i - 1][1] - 1) for i in range(1, len(rows)) if rows[i - 1][1] > 0]
    return None


def _corr(a, b):
    if not a or not b:
        return None
    n = min(len(a), len(b))
    a, b = a[-n:], b[-n:]
    if n < 20:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / (va ** 0.5 * vb ** 0.5)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-day", default="2026-05-12")  # 로컬 CSV 커버 구간
    args = ap.parse_args()
    c = sqlite3.connect(f"file:{ML}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        """SELECT DISTINCT session_date, market, ticker, pnl_pct_net
           FROM v2_learning_performance WHERE closed=1 AND filled=1 AND runtime_mode='live'
             AND pnl_pct_net IS NOT NULL AND session_date>=?""", (args.min_day,))]
    c.close()

    byday = defaultdict(list)
    for r in rows:
        byday[(r["market"], r["session_date"])].append((r["ticker"], r["pnl_pct_net"]))
    multi = {k: v for k, v in byday.items() if len(v) >= 3}
    print(f"3종목+ 동시청산일: {len(multi)} (min_day {args.min_day}+)")

    hi, lo, skip = [], [], 0
    for (mk, day), items in multi.items():
        tickers = [t for t, _ in items]
        rr = {t: _rets(mk, t, day) for t in tickers}
        pairs = []
        for i in range(len(tickers)):
            for j in range(i + 1, len(tickers)):
                cv = _corr(rr.get(tickers[i]), rr.get(tickers[j]))
                if cv is not None:
                    pairs.append(cv)
        if not pairs:
            skip += 1
            continue
        avg_corr = st.mean(pairs)
        daynet = sum(p for _, p in items)
        (hi if avg_corr >= 0.5 else lo).append((mk, day, round(avg_corr, 2), round(daynet, 1), len(items)))

    def rep(name, arr):
        if not arr:
            print(f"  {name}: n=0")
            return
        nets = [x[3] for x in arr]
        print(f"  {name}: n={len(arr)}일 결합net합={sum(nets):+.1f} 중앙={st.median(nets):+.1f} 손실일{sum(1 for x in nets if x<0)}/{len(arr)}")

    print(f"상관계산 성공 {len(hi)+len(lo)}일 / CSV부족 스킵 {skip}")
    print("=== 동시보유일 평균상관 ≥0.5(고상관 클러스터) vs <0.5 ===")
    rep("고상관", hi)
    rep("저상관", lo)
    print("\n최악 고상관 손실일:")
    for x in sorted(hi, key=lambda z: z[3])[:6]:
        print(f"  {x[0]} {x[1]} 상관{x[2]} {x[4]}종목 결합net{x[3]}")
    print("\n판정: 고상관일 결합손실 >> 저상관일 → 상관 사이징/청산순위(A3/S1)가 손실 겨냥. gross 방향만.")


if __name__ == "__main__":
    main()
