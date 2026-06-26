#!/usr/bin/env python3
"""capture leak 모니터 (read-only) — 항목③ 우측꼬리 capture 측정.

배경: v2_learning_performance.mfe_pct는 6/19 배선 이후만 채워짐(라이브 N 희소).
mfe_backfill_yf(yfinance 5m bars 추정, 6/13 백필)를 v2_decision_id로 조인하면
MFE 커버리지 21%→80%. 두 소스를 합쳐 close_reason별 capture(=실현/MFE)와
giveback(=MFE−실현)을 측정 → 어느 청산 경로가 러너를 조기절단하는지(반납 큼) 식별.

정직 규율:
- MFE 소스 우선순위: live(v2.mfe_pct, 실측) > yf_backfill(yfinance_est 5m, 추정).
- yfinance 5m high는 *실제 체결 가능가*가 아니라 추정 상단 → giveback은 일부 un-capturable.
  즉 capture 100%는 불가능에 가까움. 경로 *간 상대 비교*로만 해석(절대값 과신 금지).
- 생존편향: TARGET은 승자만 그 경로로 마감 → capture 높은 게 당연. ladder/pre_close는 혼합.

read 전용: data/ml/decisions.db (v2_learning_performance LEFT JOIN mfe_backfill_yf). 쓰기/API 없음.
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"


def load(con: sqlite3.Connection):
    cur = con.cursor()
    return list(cur.execute(
        """
        SELECT v.market, v.close_reason, v.strategy,
               COALESCE(v.pnl_pct_net, v.pnl_pct) AS pnl,
               COALESCE(v.mfe_pct, b.mfe_pct) AS mfe,
               CASE WHEN v.mfe_pct IS NOT NULL THEN 'live'
                    WHEN b.mfe_pct IS NOT NULL THEN 'yf_backfill'
                    ELSE 'none' END AS mfe_src
        FROM v2_learning_performance v
        LEFT JOIN mfe_backfill_yf b ON v.v2_decision_id = b.v2_decision_id
        WHERE v.closed=1 AND v.runtime_mode='live'
          AND COALESCE(v.pnl_pct_net, v.pnl_pct) IS NOT NULL
        """
    ))


def cap_stat(rs):
    pos = [(p, m) for p, m in rs if m is not None and p is not None and m > 0.5]
    if not pos:
        return None
    caps = [max(-1.0, min(2.0, p / m)) for p, m in pos]
    gb = [m - p for p, m in pos]
    return {
        "n": len(pos),
        "pnl": st.mean(p for p, _ in pos),
        "mfe": st.mean(m for _, m in pos),
        "giveback": st.mean(gb),
        "capture": st.mean(caps) * 100,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(ML_DB))
    ap.add_argument("--min-n", type=int, default=4)
    args = ap.parse_args()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = load(con)
    con.close()

    tot = len(rows)
    cov = sum(1 for r in rows if r[4] is not None)
    src = defaultdict(int)
    for r in rows:
        src[r[5]] += 1
    print(f"=== MFE 커버리지: {cov}/{tot} ({cov/tot*100:.0f}%) | 소스 {dict(src)} ===\n")

    by = defaultdict(list)
    for m, cr, strat, pnl, mfe, _s in rows:
        by[cr].append((pnl, mfe))

    print(f"{'close_reason':34s} {'N':>3s} {'pnl':>7s} {'MFE':>7s} {'giveback':>9s} {'capture':>8s}")
    leaks = []
    order = sorted(by, key=lambda k: -(cap_stat(by[k])["n"] if cap_stat(by[k]) else 0))
    for cr in order:
        s = cap_stat(by[cr])
        if not s or s["n"] < args.min_n:
            continue
        print(f"{cr:34s} {s['n']:3d} {s['pnl']:+6.2f}% {s['mfe']:+6.2f}% {s['giveback']:+7.2f}pp {s['capture']:6.0f}%")
        # 진짜 leak = 실현이 양수(>=0.3%)인데 MFE를 못 잡음. 손절/반전 경로(실현 음수)는 제외
        # (그건 capture leak이 아니라 트레이드가 반전된 것 — 사전엔 못 잡음).
        if s["mfe"] >= 2.0 and s["capture"] < 40 and s["giveback"] >= 1.5 and s["pnl"] >= 0.3:
            leaks.append((cr, s))

    print("\n=== 진짜 capture leak (실현 양수>=0.3% & MFE>=2% & capture<40% & giveback>=1.5pp) ===")
    if not leaks:
        print("  없음")
    for cr, s in sorted(leaks, key=lambda x: -x[1]["giveback"] * x[1]["n"]):
        print(f"  ⚠ {cr}: N={s['n']} MFE {s['mfe']:+.2f}% → 실현 {s['pnl']:+.2f}% (반납 {s['giveback']:+.2f}pp/건, capture {s['capture']:.0f}%)")
    print("\n  주: MFE는 yfinance 5m 추정 상단이라 giveback 일부는 un-capturable. 경로 *간 상대비교*로만.")
    print("  러너 조기절단 의심 경로 → 트레일 폭 확대/ladder 분할 완화를 shadow A/B로 측정(giveback↓ vs 반전손실↑ 양날).")


if __name__ == "__main__":
    main()
