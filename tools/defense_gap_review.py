"""방어 관측기 리뷰 — ① 코어 진입 국면 × 사후net, ② next-open 청산 갭.
매매·config 무접촉. 근거: 2026-07-20 라이브 두 아쉬운점 데이터화.

사용: python tools/defense_gap_review.py [--days N]
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os

FUNNEL = os.path.join("logs", "funnel")


def _load(kind: str) -> list[dict]:
    rows = []
    for f in sorted(glob.glob(os.path.join(FUNNEL, f"{kind}_*.jsonl"))):
        try:
            for line in open(f, encoding="utf-8"):
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        except OSError:
            continue
    return rows


def _next_close(market: str, ticker: str, after_date: str):
    """after_date 다음 거래일의 시가·종가(익일 갭 근사)."""
    mk = str(market or "").lower()
    path = os.path.join("data", "price", mk, f"{mk}_{ticker}.csv")
    if not os.path.exists(path):
        return None
    try:
        rows = [r for r in csv.DictReader(open(path, encoding="utf-8-sig")) if r.get("date", "") > after_date]
    except OSError:
        return None
    if not rows:
        return None
    r = rows[0]
    try:
        return {"date": r["date"], "open": float(r["open"]), "close": float(r["close"])}
    except (ValueError, KeyError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()

    print("=== ① 코어 진입 국면 관측 ===")
    ce = _load("core_entry_regime")
    if not ce:
        print("  기록 없음 — 코어 진입 발생 시 채워짐(prospective).")
    else:
        from collections import Counter
        defensive = [r for r in ce if r.get("defensive_regime")]
        print(f"  코어 진입 {len(ce)}건 중 방어국면 진입 {len(defensive)}건")
        rc = Counter(r.get("regime") for r in ce)
        for reg, n in rc.most_common():
            print(f"    {reg}: {n}건")
        print("  → 방어국면 코어진입의 사후 net을 v2_learning과 조인해 '급락 코어진입 손해' 판정")

    print("\n=== ② next-open 청산 갭 관측 ===")
    ns = _load("next_open_sell_scheduled")
    if not ns:
        print("  기록 없음 — next-open SELL 예약 시 채워짐(prospective).")
    else:
        gaps = []
        for r in ns:
            sc = r.get("scheduled_close_price")
            nxt = _next_close(r.get("market"), r.get("ticker"), r.get("session_date"))
            if sc and nxt and sc > 0:
                # 익일 시가 갭(예약 종가 대비) — SELL이므로 갭다운이 손해
                gap_open = (nxt["open"] / sc - 1.0) * 100.0
                gaps.append((r.get("ticker"), r.get("session_date"), gap_open, r.get("pnl_pct_at_schedule")))
        print(f"  예약 {len(ns)}건 중 익일 데이터 매칭 {len(gaps)}건")
        for tk, d, g, pnl in gaps:
            print(f"    {tk} {d}: 예약시 {pnl}% → 익일시가 갭 {g:+.2f}%p (음수=밤사이 추가손실)")
        if gaps:
            avg = sum(g for _, _, g, _ in gaps) / len(gaps)
            neg = sum(1 for _, _, g, _ in gaps if g < 0)
            print(f"  평균 익일 갭 {avg:+.3f}%p, 갭다운 {neg}/{len(gaps)}건")
            print("  → 평균 갭이 크게 음수면 same-day 청산 금지의 비용 실증(운영자 판단 근거)")
    print("\n판정 규율: shadow 관측만. 표본 축적 후 우리 net으로 판정, 규칙변경은 운영자.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
