from __future__ import annotations

"""일일 진입 상한의 근거("많이 사면 나쁘다")를 외부 표본으로 독립 검증한다.

내부 근거: 국면게이트 통과 건에서 US 세션당 4~6건은 거래당 +0.864%, 7건 이상은 -0.470%.
선착순 순열 p=0.299(순서는 무의미)인데 무작위 5건을 골라도 상한 없음보다 나았다.
즉 "많이 사면 나쁘다"가 본질이라는 해석이었다.

여기서 검증할 가설:
  H1. 진입 후보가 많이 생기는 날(=많이 살 수 있는 날)은 그날 진입 성과가 나쁘다.
  H2. 같은 날 안에서도 늦게(N번째로) 잡히는 신호일수록 나쁘다.

H1이 성립하면 상한은 "나쁜 날 노출 축소"로 정당화된다. H2가 성립하면 선착순 상한 자체가
의미를 갖는다. 둘 다 아니면 상한의 이득은 단순히 '표본을 줄여 분산을 낮춘 것'일 수 있다.

no-lookahead: 신호 판정은 t 시점까지 정보만, 성과는 t 이후에서만 측정. 세션 경계 유지.
한계: 60일 창 · gross · 신호 정의가 우리 스크리너와 동일하지 않음(대리 지표).

  python tools/daily_cap_external_validation.py --limit 45
"""

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
SELECTION_DB = ROOT / "data" / "ticker_selection_log.db"


def universe(limit: int) -> list[str]:
    if not SELECTION_DB.exists():
        return ["AAPL", "NVDA", "AMD", "TSLA", "COIN"][:limit]
    con = sqlite3.connect(f"file:{SELECTION_DB}?mode=ro", uri=True, timeout=15)
    try:
        con.execute("PRAGMA busy_timeout=15000")
        rows = con.execute(
            """SELECT ticker, COUNT(*) c FROM ticker_selection_log
               WHERE market='US' AND date >= '2026-05-20'
               GROUP BY ticker ORDER BY c DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        con.close()
    return [str(r[0]).strip() for r in rows if str(r[0] or "").strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="일일 상한 근거 외부 검증")
    ap.add_argument("--limit", type=int, default=45)
    ap.add_argument("--signal-pct", type=float, default=2.0, help="신호 정의: 개장 후 상승률(%%)")
    ap.add_argument("--signal-after-min", type=int, default=30, help="신호 판정 시점(개장 후 분)")
    ap.add_argument("--horizon-min", type=int, default=120, help="성과 측정 구간(분)")
    args = ap.parse_args()

    import pandas as pd
    import yfinance as yf

    tickers = universe(args.limit)
    print(f"대상 {len(tickers)}종목")

    after = max(1, args.signal_after_min // 5)
    hor = max(1, args.horizon_min // 5)

    # (날짜, 티커) -> 신호여부/성과, 그리고 신호 순서
    sig_by_day: dict = defaultdict(list)   # day -> [(첫신호봉위치, ticker, ret)]
    for i, ticker in enumerate(tickers, 1):
        try:
            df = yf.download(ticker, period="60d", interval="5m", progress=False, auto_adjust=False)
        except Exception:
            df = None
        if df is None or len(df) == 0:
            continue
        try:
            close = df["Close"].iloc[:, 0] if hasattr(df["Close"], "columns") else df["Close"]
        except Exception:
            continue
        idx = df.index
        try:
            days = pd.Series(idx.date, index=idx)
        except Exception:
            continue
        for day, day_idx in days.groupby(days):
            pos = [idx.get_loc(t) for t in day_idx.index]
            if len(pos) < after + hor + 2:
                continue
            open_px = float(close.iloc[pos[0]])
            if open_px <= 0:
                continue
            # 신호: 개장 후 after 봉 시점에 +signal_pct 이상
            t = pos[after]
            px = float(close.iloc[t])
            up = (px / open_px - 1.0) * 100.0
            if up < args.signal_pct:
                continue
            if t + hor > pos[-1]:
                continue
            fut = float(close.iloc[t + hor])
            ret = (fut / px - 1.0) * 100.0     # 신호 시점 진입 → horizon 후
            sig_by_day[str(day)].append((t, ticker, ret))
        if i % 15 == 0:
            print(f"  {i}/{len(tickers)} …")

    days_with = {d: v for d, v in sig_by_day.items() if v}
    print(f"\n신호 발생 세션 {len(days_with)}개 / 총 신호 {sum(len(v) for v in days_with.values())}건")

    print("\n=== H1. 신호가 많은 날일수록 나쁜가 ===")
    print(f"  {'그날 신호수':>10} {'세션':>5} {'신호':>6} {'평균수익':>10} {'승률':>7}")
    buckets = [(1, 3, "1~2"), (3, 6, "3~5"), (6, 11, "6~10"), (11, 999, "11+")]
    h1 = []
    for lo, hi, lab in buckets:
        sel = [v for v in days_with.values() if lo <= len(v) < hi]
        rets = [r for v in sel for _, _, r in v]
        if not rets:
            continue
        w = sum(1 for r in rets if r > 0) / len(rets) * 100
        print(f"  {lab:>10} {len(sel):5d} {len(rets):6d} {mean(rets):+9.4f}% {w:6.1f}%")
        h1.append((lab, mean(rets)))

    print("\n=== H2. 같은 날 안에서 늦게 잡힌 신호일수록 나쁜가 ===")
    print(f"  {'순번':>6} {'n':>6} {'평균수익':>10} {'승률':>7}")
    by_rank = defaultdict(list)
    for v in days_with.values():
        for rank, (_, _, r) in enumerate(sorted(v, key=lambda x: x[0]), 1):
            by_rank[min(rank, 8)].append(r)
    for rank in sorted(by_rank):
        v = by_rank[rank]
        w = sum(1 for r in v if r > 0) / len(v) * 100
        lab = f"{rank}번째" if rank < 8 else "8번째+"
        print(f"  {lab:>6} {len(v):6d} {mean(v):+9.4f}% {w:6.1f}%")

    print("\n=== 상한 시뮬 (외부 표본) ===")
    allr = [r for v in days_with.values() for _, _, r in v]
    base = mean(allr)
    print(f"  상한 없음: 평균 {base:+.4f}%  (n={len(allr)})")
    for cap in (3, 5, 8):
        sel = [r for v in days_with.values() for _, _, r in sorted(v, key=lambda x: x[0])[:cap]]
        print(f"  상한 {cap:2d}건: 평균 {mean(sel):+.4f}%  (n={len(sel)})  차이 {mean(sel)-base:+.4f}%p")

    print("\n한계: 60일 창 · gross · 신호 정의가 우리 스크리너와 동일하지 않은 대리 지표.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
