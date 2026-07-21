from __future__ import annotations

"""국면 진입게이트의 근거를 외부 표본으로 독립 검증한다.

내부 근거: US에서 CAUTIOUS·MILD_BEAR 국면 진입을 건너뛰면 -53.97% -> +5.09%.
다만 세션 단위 순열 p=0.102로 경계선이고, 25개 조합 중 최적을 고른 것이라 보정하면 더 약하다.
유지 근거는 통계가 아니라 "약세 국면에선 대부분 종목이 내린다"는 메커니즘이었다.

여기서 검증할 가설:
  시장 지수가 약한 날에 개별 종목을 사면 이후 성과가 실제로 나쁜가?
우리 국면 라벨(consensus mode)은 재현할 수 없으므로, 지수 기반 대리 국면을 쓴다.
대리 국면이 성과를 가른다면 "국면으로 거르는 것" 자체는 지지된다.

no-lookahead: 국면은 진입 시점 t 이전 정보(전일 종가 대비 당일 개장~t 수익률)로만 정하고,
성과는 t 이후에서만 측정한다. 세션 경계를 넘지 않는다.
한계: 60일 창 · gross · 우리 consensus 국면과 정의가 다른 대리 지표.

  python tools/regime_gate_external_validation.py --limit 45
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
    ap = argparse.ArgumentParser(description="국면 게이트 근거 외부 검증")
    ap.add_argument("--limit", type=int, default=45)
    ap.add_argument("--entry-after-min", type=int, default=30, help="진입 시점(개장 후 분)")
    ap.add_argument("--horizon-min", type=int, default=120)
    ap.add_argument("--index", default="SPY", help="국면 대리 지수")
    args = ap.parse_args()

    import pandas as pd
    import yfinance as yf

    after = max(1, args.entry_after_min // 5)
    hor = max(1, args.horizon_min // 5)

    # 지수: 각 세션의 '진입 시점까지' 수익률로 국면을 정의(그 이후 정보 미사용)
    spy = yf.download(args.index, period="60d", interval="5m", progress=False, auto_adjust=False)
    if spy is None or len(spy) == 0:
        print("지수 데이터를 받지 못했다.")
        return 1
    sc = spy["Close"].iloc[:, 0] if hasattr(spy["Close"], "columns") else spy["Close"]
    sidx = spy.index
    sdays = pd.Series(sidx.date, index=sidx)
    regime: dict[str, float] = {}
    for day, day_idx in sdays.groupby(sdays):
        pos = [sidx.get_loc(t) for t in day_idx.index]
        if len(pos) <= after:
            continue
        o = float(sc.iloc[pos[0]]); p = float(sc.iloc[pos[after]])
        if o > 0:
            regime[str(day)] = (p / o - 1.0) * 100.0
    print(f"국면 라벨 세션 {len(regime)}개 ({args.index} 개장~+{args.entry_after_min}분 수익률)")

    tickers = universe(args.limit)
    print(f"대상 {len(tickers)}종목")
    by_day: dict = defaultdict(list)
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
        days = pd.Series(idx.date, index=idx)
        for day, day_idx in days.groupby(days):
            key = str(day)
            if key not in regime:
                continue
            pos = [idx.get_loc(t) for t in day_idx.index]
            if len(pos) < after + hor + 2:
                continue
            t = pos[after]
            px = float(close.iloc[t])
            if px <= 0 or t + hor > pos[-1]:
                continue
            fut = float(close.iloc[t + hor])
            by_day[key].append((fut / px - 1.0) * 100.0)
        if i % 15 == 0:
            print(f"  {i}/{len(tickers)} …")

    rows = [(regime[d], r) for d, v in by_day.items() for r in v]
    print(f"\n표본 {len(rows)}건 / 세션 {len(by_day)}개")
    print("\n=== 지수 국면대별 개별종목 이후 성과 ===")
    print(f"  {'지수 개장~진입':>14} {'n':>6} {'평균':>10} {'승률':>7}")
    bands = [(-99, -0.5, "약세 <-0.5%"), (-0.5, -0.1, "-0.5~-0.1%"), (-0.1, 0.1, "보합"),
             (0.1, 0.5, "+0.1~0.5%"), (0.5, 99, "강세 >+0.5%")]
    for lo, hi, lab in bands:
        v = [r for g, r in rows if lo <= g < hi]
        if not v:
            continue
        w = sum(1 for x in v if x > 0) / len(v) * 100
        print(f"  {lab:>14} {len(v):6d} {mean(v):+9.4f}% {w:6.1f}%")

    print("\n=== '약세 국면 차단' 시뮬 ===")
    base = mean([r for _, r in rows])
    print(f"  전체 평균 {base:+.4f}%")
    for thr in (-0.5, -0.2, 0.0):
        kept = [r for g, r in rows if g >= thr]
        if not kept:
            continue
        print(f"  지수 {thr:+.1f}% 미만 세션 차단 → 평균 {mean(kept):+.4f}%  "
              f"(차이 {mean(kept)-base:+.4f}%p, 표본 {len(kept)}/{len(rows)})")
    print("\n한계: 60일 창 · gross · 우리 consensus 국면과 정의가 다른 대리 지표.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
