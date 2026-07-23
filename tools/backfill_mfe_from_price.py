#!/usr/bin/env python3
"""MFE/MAE 소급 백필 (2026-07-08 측정배관).

배경: v2_learning_performance.mfe_pct 커버리지 ~23%(71/314). observed_peak 영속화가
6월 중순 이후만이라 그 이전 청산은 MFE 결측 → capture/출구 분석이 저출력.

이 도구는 mfe_pct(또는 mae_pct)가 NULL인 CLOSED 행에 한해, 로컬 일봉
(data/price/{us,kr})으로 보유기간(session_date~closed_at) 고/저를 읽어
MFE=(기간 최고가/진입가−1), MAE=(기간 최저가/진입가−1)를 계산해 채운다.
라이브 observed(NULL 아님) 값은 무접촉(덮어쓰지 않음, 멱등).

근사 주의: 일봉 고/저 기반이라 intraday 경로는 못 보나 보유기간 상하단은 bound한다.
당일 청산도 그날 고/저를 쓴다. 진입가=v2.entry_price(실체결). 파일 없거나 날짜 밖이면 skip.
백필분은 mfe_source='daily_backfill'로 표식(컬럼 있으면).

기본 dry-run. 실제 기록은 --apply.
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ML_DB = ROOT / "data" / "ml" / "decisions.db"
PRICE = {"US": ROOT / "data" / "price" / "us", "KR": ROOT / "data" / "price" / "kr"}


def _load_ohlc(market: str, ticker: str):
    prefix = "us_" if market == "US" else "kr_"
    f = PRICE[market] / f"{prefix}{ticker}.csv"
    if not f.exists():
        return None
    out = {}
    try:
        with open(f, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                try:
                    out[r["date"]] = (float(r["high"]), float(r["low"]))
                except Exception:
                    continue
    except Exception:
        return None
    return out


def _mfe_mae(ohlc, entry_date, exit_date, entry_price):
    if not ohlc or entry_price <= 0:
        return None
    hi = lo = None
    for d, (h, l) in ohlc.items():
        if entry_date <= d <= exit_date:
            hi = h if hi is None else max(hi, h)
            lo = l if lo is None else min(lo, l)
    if hi is None:
        return None
    return round((hi / entry_price - 1) * 100, 4), round((lo / entry_price - 1) * 100, 4)


def backfill(con: sqlite3.Connection, table: str, apply: bool) -> dict:
    cur = con.cursor()
    cols = {d[1] for d in cur.execute(f"PRAGMA table_info({table})")}
    need = {"market", "ticker", "session_date", "closed_at", "entry_price", "mfe_pct", "mae_pct"}
    if need - cols:
        return {"table": table, "skipped": f"missing {sorted(need - cols)}"}
    has_src = "mfe_source" in cols
    rows = cur.execute(
        f"""SELECT rowid, market, ticker, session_date, closed_at, entry_price, mfe_pct, mae_pct
            FROM {table}
            WHERE closed=1 AND entry_price IS NOT NULL AND entry_price>0
              AND (mfe_pct IS NULL OR mae_pct IS NULL)"""
    ).fetchall()
    updates = []
    skip = {"no_file": 0, "no_dates": 0, "no_bars": 0}
    cache = {}
    for rowid, market, ticker, sd, ca, ep, mfe, mae in rows:
        m = str(market or "").upper()
        if m not in PRICE:
            continue
        entry_date = (sd or "")[:10]
        exit_date = (ca or "")[:10] or entry_date
        if not entry_date:
            skip["no_dates"] += 1
            continue
        if exit_date < entry_date:
            exit_date = entry_date
        key = (m, ticker)
        if key not in cache:
            cache[key] = _load_ohlc(m, str(ticker))
        ohlc = cache[key]
        if ohlc is None:
            skip["no_file"] += 1
            continue
        res = _mfe_mae(ohlc, entry_date, exit_date, float(ep))
        if res is None:
            skip["no_bars"] += 1
            continue
        new_mfe, new_mae = res
        # NULL인 것만 채움(멱등, 라이브값 보존)
        set_mfe = new_mfe if mfe is None else mfe
        set_mae = new_mae if mae is None else mae
        updates.append((set_mfe, set_mae, rowid))
    if apply and updates:
        cur.executemany(
            f"UPDATE {table} SET mfe_pct=?, mae_pct=? WHERE rowid=?", updates
        )
        if has_src:
            ids = [u[2] for u in updates]
            cur.executemany(
                f"UPDATE {table} SET mfe_source='daily_backfill' WHERE rowid=? AND (mfe_source IS NULL OR mfe_source='')",
                [(i,) for i in ids],
            )
        con.commit()
    return {"table": table, "candidates": len(rows), "filled": len(updates), "skip": skip}


def propagate_learning_to_canonical(con: sqlite3.Connection, apply: bool) -> dict:
    """learning.mfe_pct/mae_pct → canonical (canonical 이 NULL 인 것만).

    왜 필요한가 (2026-07-23 데이터 흐름 점검):
      mfe backfill 소스가 여럿이고(일봉·yfinance·라이브 observed), 일부는 learning만
      갱신해 canonical 이 뒤처진다. 실측: 6월 US canonical.mfe_pct 45/130 vs learning 130/130.
      canonical 을 읽는 분석이 편향 부분표본을 보게 된다(대박률 19% vs 실제 35%).
      두 값은 동일 소스라(둘 다 보유분 74건 전량 일치) 복사가 안전하다 — 재계산 아님.
    """
    if not _table_exists(con, "v2_learning_performance") or not _table_exists(con, "v2_canonical_performance"):
        return {"propagate": "skipped(table missing)"}
    n = con.execute(
        "SELECT COUNT(*) FROM v2_canonical_performance c "
        "WHERE c.closed=1 AND (c.mfe_pct IS NULL OR c.mae_pct IS NULL) "
        "AND EXISTS (SELECT 1 FROM v2_learning_performance l WHERE l.v2_decision_id=c.v2_decision_id "
        "AND (l.mfe_pct IS NOT NULL OR l.mae_pct IS NOT NULL))"
    ).fetchone()[0]
    if apply and n:
        con.execute(
            "UPDATE v2_canonical_performance SET "
            "mfe_pct=COALESCE(mfe_pct,(SELECT l.mfe_pct FROM v2_learning_performance l WHERE l.v2_decision_id=v2_canonical_performance.v2_decision_id)), "
            "mae_pct=COALESCE(mae_pct,(SELECT l.mae_pct FROM v2_learning_performance l WHERE l.v2_decision_id=v2_canonical_performance.v2_decision_id)) "
            "WHERE closed=1 AND (mfe_pct IS NULL OR mae_pct IS NULL) "
            "AND EXISTS (SELECT 1 FROM v2_learning_performance l WHERE l.v2_decision_id=v2_canonical_performance.v2_decision_id "
            "AND (l.mfe_pct IS NOT NULL OR l.mae_pct IS NOT NULL))"
        )
        con.commit()
    return {"propagate": n}


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone() is not None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_ML_DB))
    ap.add_argument("--apply", action="store_true", help="실제 기록 (미지정=dry-run)")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    con.execute("PRAGMA busy_timeout=8000")
    print(f"DB: {args.db}  모드: {'APPLY' if args.apply else 'DRY-RUN'}")
    for t in ("v2_learning_performance", "v2_canonical_performance"):
        r = backfill(con, t, args.apply)
        if "skipped" in r:
            print(f"[{r['table']}] SKIP {r['skipped']}")
        else:
            print(f"[{r['table']}] NULL 후보 {r['candidates']} → 채움 {r['filled']}  skip={r['skip']}")
    # 어느 백필 소스가 learning 을 채웠든 canonical 을 일치시킨다(2026-07-23 데이터 흐름 가드).
    pr = propagate_learning_to_canonical(con, args.apply)
    print(f"[propagate learning→canonical] mfe/mae 갭 {pr.get('propagate')}")
    con.close()
    if not args.apply:
        print("\n→ 적용: python tools/backfill_mfe_from_price.py --apply")


if __name__ == "__main__":
    main()
