"""
pathb_capture_leak_review — Path B(claude_price) 청산 capture/leak을 read-only로 측정.
진짜 매매 truth는 v2_path_runs(Path B)이며, 수익 leak이 청산(PROFIT_LADDER/STOP)에 집중.
yfinance 미사용(로컬 v2_event_store.db만). 7월 표본 누적 후 --since 로 재실행해 추적.

배경(4~6월 측정): leak 80%가 PROFIT_LADDER(45%p)+STOP(42%p). LADDER capture 27%,
STOP은 이익(+2.5%)을 본전(+0.4%)서 절단. TARGET 청산은 +5.2%/win100(우수).

측정:
- 데이터: data/v2_event_store.db v2_path_runs status=CLOSED, plan_json.pnl_pct/close_reason/
  profit_review_peak_pnl_pct.  (peak 기반 — ladder_floor 단위오염 무관)
- ① close_reason별 net + giveback(peak-pnl) + 총 leak 기여
- ② STOP/LADDER 상세: peak vs 실현 capture율
- ③ 시장별 capture율·손익비
- floor 오염 진단(%vs절대가 혼입) 경고만

CLI:
  python tools/pathb_capture_leak_review.py --since 2026-04-01 --market ALL
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

_DB = Path(__file__).parent.parent / "data" / "v2_event_store.db"


def _load(since: str, market: str):
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=8000")
    q = ("SELECT market,session_date,ticker,plan_json FROM v2_path_runs "
         "WHERE status='CLOSED' AND session_date>=?")
    params = [since]
    if market != "ALL":
        q += " AND market=?"
        params.append(market)
    rows = con.execute(q, params).fetchall()
    con.close()
    out = []
    floor_pct = floor_price = 0
    for (mkt, sd, tk, pj) in rows:
        if not pj:
            continue
        try:
            p = json.loads(pj)
        except json.JSONDecodeError:
            continue
        pnl = p.get("pnl_pct")
        if pnl is None:
            continue
        fl = p.get("profit_review_ladder_floor")
        if fl is not None:
            if abs(fl) < 100:
                floor_pct += 1
            else:
                floor_price += 1
        out.append({"mkt": mkt, "tk": tk, "pnl": pnl,
                    "cr": str(p.get("close_reason") or "?"),
                    "peak": p.get("profit_review_peak_pnl_pct")})
    return out, floor_pct, floor_price


def _ms(v):
    v = [x for x in v if x is not None]
    if not v:
        return "n=0"
    return (f"n={len(v):3d} mean={statistics.mean(v):+.2f}% med={statistics.median(v):+.2f}% "
            f"win={100*sum(1 for x in v if x > 0)/len(v):.0f}%")


def run(since: str, market: str):
    R, fpct, fprice = _load(since, market)
    print("=" * 78)
    print(f" pathb_capture_leak_review  since={since}  market={market}  청산={len(R)}건")
    print("=" * 78)
    if not R:
        print(" CLOSED 0건 — 기간/시장 확인 (Path B 청산은 v2_path_runs).")
        return

    # ① close_reason별 leak
    print("① close_reason별 net + leak(giveback=peak-pnl, peak>0 건만)")
    print(f"  {'reason':30s} {'net':>26s}  {'건(peak)':>7s} {'평균gv':>7s} {'총leak':>7s}")
    tot = 0.0
    for cr in sorted({r["cr"] for r in R}, key=lambda c: -len([r for r in R if r["cr"] == c])):
        lst = [r for r in R if r["cr"] == cr]
        gl = [r["peak"] - r["pnl"] for r in lst if r["peak"] is not None and r["peak"] > 0]
        s = sum(gl)
        tot += s
        gtxt = f"{statistics.mean(gl):+5.2f}%p {s:+6.1f}" if gl else "    -       -"
        print(f"  {cr:30s} {_ms([r['pnl'] for r in lst]):>26s}  {len(gl):>7d} {gtxt}")
    print(f"  → 총 leak(peak 반납) ≈ {tot:.0f}%p")

    # ② STOP/LADDER capture
    print("\n② 주요 leak 청산 capture (peak 대비 실현)")
    for cr in ("CLOSED_PROFIT_LADDER", "CLOSED_CLAUDE_PRICE_STOP", "CLOSED_CLAUDE_PRICE_TARGET"):
        lst = [r for r in R if r["cr"] == cr and r["peak"] and r["peak"] > 0.5]
        if not lst:
            continue
        cap = statistics.mean([r["pnl"] / r["peak"] for r in lst]) * 100
        print(f"  {cr:30s} peak {statistics.mean([r['peak'] for r in lst]):+.2f}% "
              f"→ 실현 {statistics.mean([r['pnl'] for r in lst]):+.2f}%  capture {cap:.0f}% (n={len(lst)})")

    # ③ 시장별 capture·손익비
    print("\n③ 시장별 capture율·손익비")
    for mkt in sorted({r["mkt"] for r in R}):
        sub = [r for r in R if r["mkt"] == mkt]
        cap = [r["pnl"] / r["peak"] for r in sub if r["peak"] and r["peak"] > 0.5]
        w = [r["pnl"] for r in sub if r["pnl"] > 0]
        l = [r["pnl"] for r in sub if r["pnl"] <= 0]
        capt = f"capture {statistics.mean(cap)*100:.0f}%(n{len(cap)})" if cap else "capture n/a"
        pf = f"손익비 {statistics.mean(w)/abs(statistics.mean(l)):.2f}" if w and l else ""
        print(f"  [{mkt}] {capt}  이익 {statistics.mean(w) if w else 0:+.2f}/손실 {statistics.mean(l) if l else 0:+.2f} {pf}")

    # floor 오염 진단
    print(f"\n⚠ floor 오염 진단: ladder_floor %같음={fpct} 절대주가혼입={fprice} "
          f"{'← 단위버그(측정시 peak 기반 사용 권장)' if fprice else ''}")
    print(" 참고: ladder A/B 배포후 효과는 tools/ladder_ab_review.py --since <배포일>. "
          "STOP 이익보호 개선·floor 단위수정은 별도 과제.")


def main():
    ap = argparse.ArgumentParser(description="Path B capture/leak 측정 (read-only, 외부API 없음)")
    ap.add_argument("--since", default="2026-04-01")
    ap.add_argument("--market", default="ALL", choices=["ALL", "KR", "US"])
    a = ap.parse_args()
    run(a.since, a.market)


if __name__ == "__main__":
    main()
