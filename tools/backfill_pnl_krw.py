#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pnl_krw(gross 원화 손익) 정확 백필 (C1-② 과거분, 2026-07-09).

배경: PathB 청산 소유권 이전(5~6월) 후 Writer B 미배선으로 pnl_krw 결측.
전방 배선은 495abf5로 수리 — 이 도구는 과거 행 중 **정확 복원 가능분만** 채운다.

정확 복원 2경로(근사 금지 — US 진입 FX 부재분은 건드리지 않음):
  A) pnl_krw = pnl_krw_net + fee_krw_est  (둘 다 실측 기록된 행, 6월 집중)
  B) KR 한정: pnl_krw = qty × entry_price × pnl_pct/100  (entry가 KRW 네이티브)
가드: closed=1, pnl_krw IS NULL/0인 행만. 기본 dry-run, --apply로 기록.
"""
import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ml" / "decisions.db"
TABLES = ("v2_learning_performance", "v2_canonical_performance")


def backfill(con: sqlite3.Connection, table: str, apply: bool) -> dict:
    cur = con.cursor()
    cols = {d[1] for d in cur.execute(f"PRAGMA table_info({table})")}
    need = {"market", "pnl_krw", "pnl_krw_net", "fee_krw_est", "qty", "entry_price", "pnl_pct", "closed"}
    if need - cols:
        return {"table": table, "skipped": f"missing: {sorted(need - cols)}"}
    rows = list(cur.execute(
        f"""SELECT rowid, market, pnl_krw_net, fee_krw_est, qty, entry_price, pnl_pct
            FROM {table}
            WHERE closed=1 AND (pnl_krw IS NULL OR pnl_krw=0) AND pnl_pct IS NOT NULL"""
    ))
    updates, stats = [], {"A_net+fee": 0, "B_kr_qty": 0, "skip": 0}
    for rowid, mkt, net_krw, fee_krw, qty, entry, pct in rows:
        mkt = str(mkt or "").upper()
        val = None
        if net_krw is not None and fee_krw is not None:
            val = float(net_krw) + float(fee_krw)          # A: gross = net + fee
            stats["A_net+fee"] += 1
        elif mkt == "KR" and qty and entry:
            val = float(qty) * float(entry) * float(pct) / 100.0   # B: KRW 네이티브
            stats["B_kr_qty"] += 1
        if val is None:
            stats["skip"] += 1
            continue
        updates.append((round(val, 0), rowid))
    if apply and updates:
        cur.executemany(f"UPDATE {table} SET pnl_krw=? WHERE rowid=?", updates)
        con.commit()
    return {"table": table, "candidates": len(rows), "updated": len(updates), "stats": stats}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    print(f"모드: {'APPLY' if args.apply else 'DRY-RUN'}")
    for t in TABLES:
        r = backfill(con, t, args.apply)
        detail = r.get("skipped") or f"후보 {r['candidates']} → 채움 {r['updated']} {r['stats']}"
        print(f"[{r['table']}] {detail}")
    if not args.apply:
        print("→ 실제 기록: --apply")


if __name__ == "__main__":
    main()
