#!/usr/bin/env python3
"""net 기준 종합 수익성 리뷰 (A1, read-only).

운영자가 보는 성과를 gross(pnl_pct) 착시가 아니라 net(수수료·FX 반영)으로 본다.
net 소스 우선순위: plan_json.pnl_pct_net_est(실측 수수료반영) > pnl_pct - 비용가정.
비용가정은 한투 일반 사용자 실측: US 0.70%p(수수료0.50+FX0.20), KR 0.21%p (KOSPI 0.147+거래세).
외부 API 없음. v2_event_store.db만 읽는다.

사용: python tools/net_profitability_review.py [--since YYYY-MM-DD]
"""
from __future__ import annotations
import argparse, json, sqlite3, statistics as st
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "v2_event_store.db"
# 한투 일반 사용자 실측 왕복비용(혜택 없음): kis-fees-and-ladder-sim 메모리
COST = {"US": 0.70, "KR": 0.21}


def _f(d: dict, k: str):
    v = d.get(k)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def net_of(market: str, d: dict) -> float | None:
    """net 우선순위: 실측 net_est > gross - 비용가정."""
    ne = _f(d, "pnl_pct_net_est")
    if ne is not None:
        return ne
    g = _f(d, "pnl_pct")
    if g is None:
        return None
    return g - COST.get(str(market or "").upper(), 0.5)


def _agg(vals: list[float]) -> str:
    if not vals:
        return "n=0"
    win = sum(1 for x in vals if x > 0) / len(vals) * 100
    return f"n={len(vals):4d} mean={st.mean(vals):+.3f}% median={st.median(vals):+.3f}% win={win:.0f}% sum={sum(vals):+.0f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="session_date 하한 (YYYY-MM-DD)")
    args = ap.parse_args()

    con = sqlite3.connect(str(DB))
    con.execute("PRAGMA busy_timeout=8000")
    q = ("SELECT market, session_date, plan_json FROM v2_path_runs "
         "WHERE status='CLOSED' AND path_type='claude_price' AND plan_json IS NOT NULL")
    if args.since:
        q += f" AND session_date >= '{args.since}'"
    rows = []
    for market, sd, pj in con.execute(q):
        try:
            d = json.loads(pj)
        except Exception:
            continue
        rows.append((market, sd, d))
    con.close()

    print(f"=== net 기준 수익성 리뷰 (CLOSED {len(rows)}건{', since '+args.since if args.since else ''}) ===")
    print(f"  비용가정(net_est 없을때): US {COST['US']}%p · KR {COST['KR']}%p (한투 일반 실측)")

    print("\n[1] 시장별 — gross vs net")
    for mkt in ("US", "KR"):
        sub = [d for m, _, d in rows if m == mkt]
        g = [_f(d, "pnl_pct") for d in sub if _f(d, "pnl_pct") is not None]
        n = [net_of(mkt, d) for d in sub if net_of(mkt, d) is not None]
        cov = sum(1 for d in sub if _f(d, "pnl_pct_net_est") is not None)
        if g:
            print(f"  {mkt} gross: {_agg(g)}")
        if n:
            print(f"  {mkt} NET  : {_agg(n)}  (실측net_est 커버 {cov}/{len(sub)})")

    print("\n[2] 시장 x 월별 — net")
    mm = defaultdict(list)
    for m, sd, d in rows:
        v = net_of(m, d)
        if v is not None:
            mm[(m, (sd or "")[:7])].append(v)
    for k in sorted(mm):
        print(f"  {k[0]} {k[1]}: {_agg(mm[k])}")

    print("\n[3] close_reason별 — net (어디서 비용에 먹히나)")
    for mkt in ("US", "KR"):
        print(f"  {mkt}:")
        cr = defaultdict(list)
        for m, _, d in rows:
            if m != mkt:
                continue
            v = net_of(m, d)
            if v is not None:
                cr[d.get("close_reason", "?")].append(v)
        for r in sorted(cr, key=lambda x: -len(cr[x])):
            if len(cr[r]) < 2:
                continue
            g = [_f(d, "pnl_pct") for m, _, d in rows if m == mkt and d.get("close_reason") == r and _f(d, "pnl_pct") is not None]
            gm = st.mean(g) if g else float("nan")
            flip = "  <-gross+ net-" if gm > 0 and st.mean(cr[r]) < 0 else ""
            print(f"    {str(r):30s} gross {gm:+.2f}% -> NET {st.mean(cr[r]):+.2f}% (n={len(cr[r])}){flip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
