#!/usr/bin/env python3
"""KR 급락 풀 장기 초과분 — 2018~2026, 연도별·국면별 (read-only, 2026-09-12).

E5 풀(R2∪R4 + 라이브 바닥, TP12/SL25/D5)을 장기 캐시로 재구성하고
**지수 초과분**으로만 판정한다. 원 net은 옆에 참고로만 둔다 —
09-11 C6 사건(원 net t 3.45가 초과분 −0.12%였다)의 교훈이다.

규약:
  · 후보·정산은 `research_kr_rank_pool_reconcile.build()` 그대로(E5 원본 일치 게이트 통과).
  · 벤치마크는 같은 창 069500 — 진입일 시가 → D5 종가. 백필분(2018~)을 단일 출처로 쓴다.
  · t는 **날짜 클러스터**가 기본(급락일에 표본이 몰리므로 종목 클러스터는 부풀린다).
    월 클러스터도 같이 낸다.
  · 국면은 신호일의 069500 종가 vs 직전 20봉 평균(MA20) — 신호 시점 정보만 쓴다.

⚠️ **생존편향**: 장기 캐시 대상은 2026-09 현재 CSV 보유 종목이다. 상폐분이 빠져
좌측 꼬리가 결손이다. 측정 하한 3.3%(표본의 35%가 미보존이라 하한).
**장기 수치는 위쪽으로 치우쳐 있다.**
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_benchmark_excess import bench_bars  # noqa: E402
from tools.research_kr_rank_pool_reconcile import build, by_session  # noqa: E402

LONG = ROOT / "data" / "price_backfill_yf" / "kr_fallen_price_cache_2018_2026.json"
HOLD = 5


def bench_index():
    bars = bench_bars("kr", "069500")
    return bars, {b[0]: i for i, b in enumerate(bars)}


def cluster_t(pairs, keyfn):
    g = defaultdict(list)
    for day, value in pairs:
        g[keyfn(day)].append(value)
    means = [st.mean(v) for v in g.values()]
    if len(means) < 3 or st.stdev(means) == 0:
        return None, len(means)
    return st.mean(means) / (st.stdev(means) / math.sqrt(len(means))), len(means)


def row(label, pairs, extra=""):
    if not pairs:
        return f"| {label} | 0 | - | - | - | {extra} |"
    td, kd = cluster_t(pairs, lambda d: d)
    tm, km = cluster_t(pairs, lambda d: d[:7])
    f = lambda x: f"{x:.2f}" if x is not None else "-"      # noqa: E731
    return (f"| {label} | {len(pairs)} | {st.mean(v for _, v in pairs):+.2f}% | "
            f"{f(td)} (k{kd}) | {f(tm)} (k{km}) | {extra} |")


def main():
    if not LONG.exists():
        print(f"장기 캐시 없음: {LONG}\n"
              f"먼저 `python tools/backfill_kr_price_cache_yfinance.py`를 돌린다.", file=sys.stderr)
        return 2
    cache = json.loads(LONG.read_text(encoding="utf-8"))
    sessions = by_session(build(cache))
    bars, idx = bench_index()

    def excess(entry_date, net):
        i = idx.get(entry_date)
        if i is None or i + HOLD > len(bars):
            return None
        return net - (bars[i + HOLD - 1][4] / bars[i][1] - 1) * 100

    def regime(signal_date):
        """신호일 종가가 직전 20봉 평균 위/아래. 신호 시점 정보만."""
        i = idx.get(signal_date)
        if i is None or i < 20:
            return None
        return "MA20 위" if bars[i][4] >= st.mean(b[4] for b in bars[i - 20:i]) else "MA20 아래"

    recs = []
    miss = 0
    for day, pool in sessions.items():
        for c in pool:
            ex = excess(c["entry_date"], c["net"])
            if ex is None:
                miss += 1
                continue
            recs.append({"day": day, "net": c["net"], "excess": ex, "k": len(pool),
                         "regime": regime(c["session"]), "year": day[:4]})

    lines = [f"# KR 급락 풀 장기 초과분 — 2018~2026 ({date.today().isoformat()})", "",
             "read-only. 라이브 변경 없음. 후보·정산은 E5 규약(R2∪R4, TP12/SL25/D5, cost 0.25),",
             "벤치마크는 같은 창 069500(진입일 시가 → D5 종가). t는 날짜 클러스터가 기본이다.", "",
             f"장기 캐시 {len(cache)}종목 · 후보 {len(recs)}건 · 세션 {len(sessions)}일 "
             f"(벤치 결측 제외 {miss}건)", "",
             "⚠️ **생존편향**: 대상이 2026-09 현재 CSV 보유 종목이라 상폐분이 빠졌다.",
             "측정 하한 3.3%(표본의 35%가 yfinance 미보존이라 하한). **수치는 위쪽으로 치우쳐 있다.**"]

    lines += ["", "## 연도별 초과분", "",
              "| 연도 | n | 초과 평균 | t(진입일) | t(월) | 원 net 평균 |", "|---|---:|---:|---:|---:|---:|"]
    for y in sorted({r["year"] for r in recs}):
        sub = [r for r in recs if r["year"] == y]
        lines.append(row(y, [(r["day"], r["excess"]) for r in sub],
                         f"{st.mean(r['net'] for r in sub):+.2f}%"))
    lines.append(row("**전체**", [(r["day"], r["excess"]) for r in recs],
                     f"{st.mean(r['net'] for r in recs):+.2f}%"))

    lines += ["", "## 국면별 초과분 (신호일 069500 vs MA20)", "",
              "| 국면 | n | 초과 평균 | t(진입일) | t(월) | 원 net 평균 |", "|---|---:|---:|---:|---:|---:|"]
    for g in ("MA20 아래", "MA20 위"):
        sub = [r for r in recs if r["regime"] == g]
        lines.append(row(g, [(r["day"], r["excess"]) for r in sub],
                         f"{st.mean(r['net'] for r in sub):+.2f}%" if sub else "-"))

    lines += ["", "## 후보 수(k) 구간별 초과분", "",
              "| 구간 | n | 초과 평균 | t(진입일) | t(월) | 일수 |", "|---|---:|---:|---:|---:|---:|"]
    for lab, lo, hi in (("k=1", 1, 1), ("k=2~4", 2, 4), ("k=5~9", 5, 9),
                        ("k=10~19", 10, 19), ("k≥20", 20, 10 ** 9)):
        sub = [r for r in recs if lo <= r["k"] <= hi]
        lines.append(row(lab, [(r["day"], r["excess"]) for r in sub],
                         str(len({r["day"] for r in sub}))))

    lines += ["", "## 연도 × 국면 (초과 평균 / n)", "",
              "| 연도 | MA20 아래 | MA20 위 |", "|---|---:|---:|"]
    for y in sorted({r["year"] for r in recs}):
        cells = []
        for g in ("MA20 아래", "MA20 위"):
            sub = [r for r in recs if r["year"] == y and r["regime"] == g]
            cells.append(f"{st.mean(r['excess'] for r in sub):+.2f}% / {len(sub)}" if sub else "-")
        lines.append(f"| {y} | {cells[0]} | {cells[1]} |")

    out = ROOT / "docs" / "reports" / f"kr_pool_long_excess_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
