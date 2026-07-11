"""pnl_krw_net(순원화 손익) 백필 + realized equity curve (워크플랜 P1-6).

원칙(워크플랜 §11): 원천 사실이 있는 행만 백필, 추정과 실측을 혼합하지 않는다.

복구 정책 (FX 명세서는 운영자 제외 지시 → pnl_pct_net이 이미 FX 가정 반영된 net이므로 사용):
  KR: pnl_krw_net = round(qty * entry_price * pnl_pct_net / 100)  (KRW 네이티브 = 정확)
  US(gross 있음): notional = pnl_krw / (pnl_pct/100) → pnl_krw_net = pnl_krw * (pnl_pct_net/pnl_pct)
                  (실제 gross KRW로 명목가 역산 = FX 불필요·실데이터 파생)
  US(gross 없음): pnl_krw_net = round(FIXED_ORDER_KRW_US * pnl_pct_net / 100)  (추정, net_basis로 구분)

FIXED_ORDER_KRW_US: 우리 거래창(2026-04~07-06)은 US 소액화(20만) 이전이라 500,000원.

가드: closed=1, pnl_krw_net IS NULL. 기본 dry-run, --apply로 기록.
net_basis로 정확/파생/추정 구분(measured 원본은 미변경).
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ml" / "decisions.db"
FIXED_ORDER_KRW_US = 500000.0  # 거래창(2026-04~07-06)은 US 20만 소액화 이전


def run(db: Path, apply: bool) -> dict:
    con = sqlite3.connect(db, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    cur = con.cursor()
    cols = {r[1] for r in cur.execute("PRAGMA table_info(v2_learning_performance)")}
    assert {"pnl_krw_net", "market", "entry_price", "qty", "pnl_pct_net", "pnl_krw", "pnl_pct", "net_basis"} <= cols

    total = cur.execute("SELECT COUNT(*) FROM v2_learning_performance WHERE closed=1").fetchone()[0]
    have0 = cur.execute("SELECT COUNT(*) FROM v2_learning_performance WHERE closed=1 AND pnl_krw_net IS NOT NULL").fetchone()[0]

    rows = cur.execute(
        "SELECT rowid, market, entry_price, qty, pnl_pct_net, pnl_krw, pnl_pct "
        "FROM v2_learning_performance WHERE closed=1 AND pnl_krw_net IS NULL"
    ).fetchall()
    updates = []  # (val, basis, rowid)
    stats = {"kr_native_exact": 0, "us_from_gross": 0, "us_estimated_fixed_order": 0, "no_source_blocked": 0}
    for rowid, mkt, entry, qty, npct, gross_krw, gross_pct in rows:
        if npct is None:
            stats["no_source_blocked"] += 1
            continue
        if mkt == "KR" and entry and qty:
            updates.append((round(float(qty) * float(entry) * float(npct) / 100.0), "backfilled_krw_native", rowid))
            stats["kr_native_exact"] += 1
        elif mkt == "US" and gross_krw is not None and gross_pct not in (None, 0):
            # 실제 gross KRW로 명목가 역산 → net (FX 불필요)
            updates.append((round(float(gross_krw) * float(npct) / float(gross_pct)), "backfilled_us_from_gross", rowid))
            stats["us_from_gross"] += 1
        elif mkt == "US":
            updates.append((round(FIXED_ORDER_KRW_US * float(npct) / 100.0), "estimated_fixed_order_us", rowid))
            stats["us_estimated_fixed_order"] += 1
        else:
            stats["no_source_blocked"] += 1

    if apply and updates:
        cur.executemany(
            "UPDATE v2_learning_performance SET pnl_krw_net=?, net_basis=? WHERE rowid=?",
            updates,
        )
        con.commit()

    have1 = have0 + (len(updates) if apply else 0)
    con.close()
    return {
        "total_closed": total,
        "coverage_before": round(100 * have0 / total, 1),
        "backfilled": len(updates),
        "applied": apply,
        "coverage_after": round(100 * have1 / total, 1),
        "stats": stats,
    }


def equity_curve(db: Path) -> dict:
    """일자순 realized equity curve + MDD (pnl_krw_net 있는 행만; 시장별 coverage 명시)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT substr(closed_at,1,10) d, market, pnl_krw_net "
        "FROM v2_learning_performance WHERE closed=1 AND pnl_krw_net IS NOT NULL AND closed_at IS NOT NULL "
        "ORDER BY closed_at"
    ).fetchall()
    cov = {}
    for m in ("KR", "US"):
        tot = con.execute("SELECT COUNT(*) FROM v2_learning_performance WHERE closed=1 AND market=?", (m,)).fetchone()[0]
        hv = con.execute("SELECT COUNT(*) FROM v2_learning_performance WHERE closed=1 AND market=? AND pnl_krw_net IS NOT NULL", (m,)).fetchone()[0]
        cov[m] = f"{hv}/{tot} ({round(100*hv/tot) if tot else 0}%)"
    con.close()
    from collections import defaultdict
    daily = defaultdict(float)
    for d, m, v in rows:
        daily[d] += float(v)
    cum, peak, mdd = 0.0, 0.0, 0.0
    for d in sorted(daily):
        cum += daily[d]
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    con2 = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    from collections import Counter
    basis = dict(Counter(r[0] for r in con2.execute(
        "SELECT net_basis FROM v2_learning_performance WHERE closed=1 AND pnl_krw_net IS NOT NULL")).most_common())
    con2.close()
    est = sum(v for k, v in basis.items() if str(k).startswith("estimated"))
    return {"coverage_by_market": cov, "days": len(daily),
            "final_cum_krw": round(cum), "max_drawdown_krw": round(mdd),
            "net_basis_breakdown": basis,
            "estimated_rows": est,
            "note": "US gross없는 행은 고정주문 500k 추정(net_basis=estimated_*). 실측/파생과 라벨로 구분."}


def main() -> int:
    ap = argparse.ArgumentParser(description="pnl_krw_net 백필 + equity curve (P1-6)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true", help="실제 DB 기록 (기본 dry-run)")
    args = ap.parse_args()
    db = Path(args.db)
    res = run(db, args.apply)
    print("=== pnl_krw_net 백필 ===")
    for k, v in res.items():
        print(f"  {k}: {v}")
    print("=== realized equity curve (pnl_krw_net 있는 행) ===")
    for k, v in equity_curve(db).items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
