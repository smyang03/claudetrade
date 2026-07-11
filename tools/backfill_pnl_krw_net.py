"""pnl_krw_net(순원화 손익) 백필 + realized equity curve (워크플랜 P1-6).

원칙(워크플랜 §11): 원천 사실이 있는 행만 백필, 추정과 실측을 혼합하지 않는다.

복구 정책:
  KR: pnl_krw_net = round(qty * entry_price * pnl_pct_net / 100)  (KRW 네이티브 = 정확)
  US: 진입 FX rate가 원장에 없고 usdkrw_daily도 거래창(2026-04-27~)을 커버하지 못해
      KRW 명목가를 원천으로 확정할 수 없다 → 백필하지 않고 fx_blocked로 보고(→ P1-4 의존).

가드: closed=1, pnl_krw_net IS NULL, entry/qty/pnl_pct_net 존재. 기본 dry-run, --apply로 기록.
백필 행은 net_basis='backfilled_krw_native'로 표시해 measured와 구분.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ml" / "decisions.db"


def run(db: Path, apply: bool) -> dict:
    con = sqlite3.connect(db, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    cur = con.cursor()
    cols = {r[1] for r in cur.execute("PRAGMA table_info(v2_learning_performance)")}
    assert {"pnl_krw_net", "market", "entry_price", "qty", "pnl_pct_net", "net_basis"} <= cols

    total = cur.execute("SELECT COUNT(*) FROM v2_learning_performance WHERE closed=1").fetchone()[0]
    have0 = cur.execute("SELECT COUNT(*) FROM v2_learning_performance WHERE closed=1 AND pnl_krw_net IS NOT NULL").fetchone()[0]

    rows = cur.execute(
        "SELECT rowid, market, entry_price, qty, pnl_pct_net "
        "FROM v2_learning_performance WHERE closed=1 AND pnl_krw_net IS NULL"
    ).fetchall()
    updates = []
    stats = {"kr_native_exact": 0, "us_fx_blocked": 0, "no_source_blocked": 0}
    for rowid, mkt, entry, qty, npct in rows:
        if entry and qty and npct is not None:
            if mkt == "KR":
                val = round(float(qty) * float(entry) * float(npct) / 100.0)
                updates.append((val, rowid))
                stats["kr_native_exact"] += 1
            elif mkt == "US":
                stats["us_fx_blocked"] += 1  # 진입 FX 원천 없음 → P1-4 의존
            else:
                stats["no_source_blocked"] += 1
        else:
            stats["no_source_blocked"] += 1

    if apply and updates:
        cur.executemany(
            "UPDATE v2_learning_performance SET pnl_krw_net=?, "
            "net_basis=COALESCE(NULLIF(net_basis,''),'backfilled_krw_native') WHERE rowid=?",
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
    return {"coverage_by_market": cov, "days": len(daily),
            "final_cum_krw": round(cum), "max_drawdown_krw": round(mdd),
            "note": "US coverage 낮음(FX blocked)→ equity curve는 사실상 KR 중심, 전체 계좌 곡선 아님"}


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
