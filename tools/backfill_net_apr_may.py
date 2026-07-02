#!/usr/bin/env python3
"""4·5월 closed trade에 net 측정값을 소급 백필 (A3 — 명제1 결판용 측정 복구).

배경: net_basis='measured'(라이브 비용메타)는 6/9~ 이후만 존재 → 4·5월은 net 미측정.
모든 net 판정이 6월 단일국면 위에 섰다. 이 도구가 4·5월 closed 행에 net을 소급 채운다.

정직 규율(중요):
- KR: 환전 없음 → net = gross − 왕복수수료 가 **정확**. net_basis='backfilled_exact'.
- US: 진입/청산 시점 usd_krw가 4·5월 payload에 **미기록**(복구 불가) → FX 스프레드 차감 불가.
  수수료만 차감한 **근사값**. net_basis='backfilled_fee_only'.
- 라이브 'measured'와 **다른 basis 라벨**로 저장 → 클린 measured 집합 오염 금지.
  net 분석은 measured + backfilled_*를 별도/합산 선택해서 본다.

기본 dry-run. 실제 기록은 --apply. read 컬럼: market, session_date, pnl_pct, closed.
write 컬럼: pnl_pct_net, fee_pct_round_trip, net_basis (기존 net_basis 비어있는 행만).
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ML_DB = ROOT / "data" / "ml" / "decisions.db"

# capture_net_review.py와 동일 상수 (왕복 수수료 %, US FX 스프레드 왕복 %)
FEE_PCT = {"US": 0.5, "KR": 0.5}
FX_SPREAD_PCT = {"US": 0.2, "KR": 0.0}

TABLES = ("v2_learning_performance", "v2_canonical_performance")
# 백필 대상 월 (session_date prefix). 6월은 라이브 measured가 있으므로 제외.
TARGET_MONTHS = ("2026-04", "2026-05")


def _basis_for(market: str) -> str:
    return "backfilled_exact" if str(market).upper() == "KR" else "backfilled_fee_only"


def backfill_table(con: sqlite3.Connection, table: str, apply: bool) -> dict:
    cur = con.cursor()
    # 컬럼 존재 확인
    cols = {d[1] for d in cur.execute(f"PRAGMA table_info({table})")}
    needed = {"market", "session_date", "pnl_pct", "pnl_pct_net", "fee_pct_round_trip", "net_basis", "closed"}
    missing = needed - cols
    if missing:
        return {"table": table, "skipped": f"missing columns: {sorted(missing)}"}

    month_pred = " OR ".join("session_date LIKE ?" for _ in TARGET_MONTHS)
    params = [f"{m}%" for m in TARGET_MONTHS]
    rows = list(
        cur.execute(
            f"""SELECT rowid, market, pnl_pct, net_basis FROM {table}
                WHERE closed=1 AND pnl_pct IS NOT NULL
                  AND ({month_pred})
                  AND (net_basis IS NULL OR net_basis='' OR net_basis NOT LIKE 'backfilled%')
                  AND net_basis != 'measured'""",
            params,
        )
    )
    updates = []
    by_mkt: dict[str, dict] = {}
    for rowid, market, pnl_pct, net_basis in rows:
        mkt = str(market or "").upper()
        if mkt not in FEE_PCT:
            continue
        fee = FEE_PCT[mkt]
        # US는 FX 스프레드를 복구 불가 → 수수료만. (정직: 근사 라벨)
        net = float(pnl_pct) - fee
        basis = _basis_for(mkt)
        updates.append((round(net, 6), fee, basis, rowid))
        b = by_mkt.setdefault(mkt, {"n": 0, "gross_sum": 0.0, "net_sum": 0.0})
        b["n"] += 1
        b["gross_sum"] += float(pnl_pct)
        b["net_sum"] += net

    if apply and updates:
        cur.executemany(
            f"UPDATE {table} SET pnl_pct_net=?, fee_pct_round_trip=?, net_basis=? WHERE rowid=?",
            updates,
        )
        con.commit()

    return {"table": table, "candidates": len(rows), "updated": len(updates), "by_market": by_mkt}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_ML_DB))
    ap.add_argument("--apply", action="store_true", help="실제 기록 (미지정 시 dry-run)")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    print(f"DB: {args.db}")
    print(f"모드: {'APPLY (기록)' if args.apply else 'DRY-RUN (미기록)'}")
    print(f"대상 월: {', '.join(TARGET_MONTHS)} / 수수료 왕복 KR·US {FEE_PCT['KR']}% (US FX는 데이터 없어 미반영=근사)\n")
    for table in TABLES:
        r = backfill_table(con, table, args.apply)
        if "skipped" in r:
            print(f"[{r['table']}] SKIP — {r['skipped']}")
            continue
        print(f"[{r['table']}] 후보 {r['candidates']}건 → 백필 {r['updated']}건")
        for mkt, b in sorted(r["by_market"].items()):
            n = b["n"] or 1
            basis = _basis_for(mkt)
            print(
                f"    {mkt}: N={b['n']} gross_avg={b['gross_sum']/n:+.3f}% "
                f"net_avg={b['net_sum']/n:+.3f}% (basis={basis})"
            )
    con.close()
    if not args.apply:
        print("\n→ 검토 후 실제 기록: python tools/backfill_net_apr_may.py --apply")


if __name__ == "__main__":
    main()
