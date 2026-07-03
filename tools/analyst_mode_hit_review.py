#!/usr/bin/env python3
"""analyst mode 방향 적중률 누적 리뷰 (read-only, 2026-07-03 미검증층 감사 개선안).

질문: analyst 3인 토론이 정하는 세션 지배 mode(강세/약세 계열)가 당일 지수
방향(시가→종가)과 얼마나 일치하나 — 토큰 40%를 쓰는 판단층의 유일한 정량 기여 지표.

첫 실측(n=24, KR): 46% ≈ 동전. 단 지수 데이터 결측 2~3건이 X로 오분류돼
과소평가 가능 — 이 도구는 결측을 '제외'(오분류 방지)하고 시장·월별로 누적한다.
판정 문턱(감사 보고서 고정): 다국면 누적 n>=60에서 적중 <=55%면 mode 방향성은
기여 없음 확정 → R2 조건부/빈도 축소의 근거. >=60%면 방향성 존치.
매매 무변경, DB mode=ro, 지수는 yfinance(분석용).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "ml" / "decisions.db"
BENCH = {"KR": "^KS11", "US": "SPY"}
BULL = {"AGGRESSIVE", "MODERATE_BULL", "MILD_BULL"}
BEAR = {"MILD_BEAR", "CAUTIOUS_BEAR", "DEFENSIVE", "CAUTIOUS"}


def main() -> int:
    ap = argparse.ArgumentParser(description="analyst mode 방향 적중률 (read-only)")
    ap.add_argument("--since", default="2026-05-01")
    args = ap.parse_args()

    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf

    con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=8000")
    rows = con.execute(
        "SELECT market, session_date, mode, COUNT(*) FROM decisions "
        "WHERE mode IS NOT NULL AND session_date>=? GROUP BY market, session_date, mode",
        (args.since,),
    ).fetchall()
    con.close()

    # 세션 지배 mode
    best: dict[tuple[str, str], tuple[str, int]] = {}
    for m, sd, mode, c in rows:
        k = (str(m).upper(), str(sd))
        if k not in best or c > best[k][1]:
            best[k] = (str(mode), c)

    for market in ("KR", "US"):
        sess = {sd: mode for (m, sd), (mode, _) in best.items() if m == market}
        if not sess:
            continue
        idx = yf.download(BENCH[market], start=args.since, interval="1d", progress=False, auto_adjust=False)
        ret = {}
        for d, r in idx.iterrows():
            try:
                o = float(r["Open"].iloc[0] if hasattr(r["Open"], "iloc") else r["Open"])
                cl = float(r["Close"].iloc[0] if hasattr(r["Close"], "iloc") else r["Close"])
                if o > 0 and cl > 0:
                    ret[d.strftime("%Y-%m-%d")] = (cl / o - 1) * 100
            except Exception:
                continue

        by_month = defaultdict(lambda: [0, 0])  # month -> [hit, total]
        excluded = {"missing_index": 0, "flat": 0, "neutral": 0}
        details = []
        for sd in sorted(sess):
            mode = sess[sd]
            r = ret.get(sd)
            if r is None:
                excluded["missing_index"] += 1
                continue
            if abs(r) < 0.05:
                excluded["flat"] += 1
                continue
            if mode in BULL:
                ok = r > 0
            elif mode in BEAR:
                ok = r < 0
            else:
                excluded["neutral"] += 1
                continue
            bm = by_month[sd[:7]]
            bm[0] += int(ok)
            bm[1] += 1
            details.append((sd, mode, round(r, 2), "O" if ok else "X"))

        tot_h = sum(v[0] for v in by_month.values())
        tot_n = sum(v[1] for v in by_month.values())
        print(f"===== {market} (bench {BENCH[market]}) =====")
        print(f"  누적 적중: {tot_h}/{tot_n} = {100*tot_h/tot_n:.0f}%" if tot_n else "  판정 표본 0")
        for mo in sorted(by_month):
            h, n = by_month[mo]
            print(f"    {mo}: {h}/{n} = {100*h/n:.0f}%")
        print(f"  제외: 지수결측 {excluded['missing_index']} / 보합 {excluded['flat']} / NEUTRAL {excluded['neutral']}")
        for d in details[-6:]:
            print(f"    {d}")
        print()
    print("판정 문턱(고정): 누적 n>=60에서 <=55% → mode 방향성 기여 없음 확정(R2 축소 근거) / >=60% → 존치")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
