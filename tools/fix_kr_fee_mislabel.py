#!/usr/bin/env python3
"""KR fee 오라벨 교정 (2026-07-08 측정배관).

배경: tools/backfill_net_apr_may.py가 KR 왕복수수료를 US값 0.5로 하드코딩(FEE_PCT 버그)해
4·5월 KR closed 행(net_basis='backfilled_exact')의 pnl_pct_net을 gross-0.5로 과대차감했다.
KR 실제 왕복 = 0.21%(매수 0.015 + 매도 0.195 거래세, 환전 없음, 권위값
execution.claude_price_sell_manager._fee_rates_for_market). 거래당 net을 −0.29%p씩 왜곡,
KR 통산 net을 음전시킨 측정 사각의 근본.

이 도구는 net_basis='backfilled_exact'(=KR 4·5월 백필분) 행에 한해 fee_pct_round_trip을
0.21로, pnl_pct_net을 gross(pnl_pct)−0.21로 **재계산**한다(현 net 상태 무관, gross 기준이라
멱등). measured 행(라이브, 이미 0.21)·US 행 무접촉. status·트리거·주문·매매 로직 무접촉.

기본 dry-run. 실제 기록은 --apply.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ML_DB = ROOT / "data" / "ml" / "decisions.db"
TABLES = ("v2_learning_performance", "v2_canonical_performance")

# 권위값: _fee_rates_for_market("KR") = (0.00015, 0.00195) → 왕복 0.21%
KR_FEE_RT = 0.21
WRONG_FEE = 0.5


def fix_table(con: sqlite3.Connection, table: str, apply: bool) -> dict:
    cur = con.cursor()
    cols = {d[1] for d in cur.execute(f"PRAGMA table_info({table})")}
    need = {"market", "net_basis", "pnl_pct", "pnl_pct_net", "fee_pct_round_trip"}
    if need - cols:
        return {"table": table, "skipped": f"missing {sorted(need - cols)}"}
    rows = cur.execute(
        f"""SELECT rowid, pnl_pct, pnl_pct_net, fee_pct_round_trip FROM {table}
            WHERE net_basis='backfilled_exact' AND UPPER(market)='KR'
              AND pnl_pct IS NOT NULL
              AND ABS(COALESCE(fee_pct_round_trip,0) - {KR_FEE_RT}) > 1e-9"""
    ).fetchall()
    updates = []
    for rowid, gross, old_net, old_fee in rows:
        new_net = round(float(gross) - KR_FEE_RT, 6)
        updates.append((new_net, KR_FEE_RT, rowid))
    delta_avg = (
        sum(u[0] - r[2] for u, r in zip(updates, rows) if r[2] is not None) / len(updates)
        if updates else 0.0
    )
    if apply and updates:
        cur.executemany(
            f"UPDATE {table} SET pnl_pct_net=?, fee_pct_round_trip=? WHERE rowid=?", updates
        )
        con.commit()
    return {"table": table, "candidates": len(rows), "updated": len(updates),
            "net_delta_avg_pp": round(delta_avg, 4)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_ML_DB))
    ap.add_argument("--apply", action="store_true", help="실제 기록 (미지정=dry-run)")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    con.execute("PRAGMA busy_timeout=8000")
    print(f"DB: {args.db}")
    print(f"모드: {'APPLY' if args.apply else 'DRY-RUN'}  (KR 왕복수수료 {WRONG_FEE}→{KR_FEE_RT})")
    for t in TABLES:
        r = fix_table(con, t, args.apply)
        if "skipped" in r:
            print(f"[{r['table']}] SKIP {r['skipped']}")
        else:
            print(f"[{r['table']}] 대상 {r['candidates']}건 → 교정 {r['updated']}건 "
                  f"(net 평균 {r['net_delta_avg_pp']:+}%p 개선)")
    con.close()
    if not args.apply:
        print("\n→ 적용: python tools/fix_kr_fee_mislabel.py --apply")


if __name__ == "__main__":
    main()
