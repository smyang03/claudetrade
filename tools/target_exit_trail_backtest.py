from __future__ import annotations

"""TARGET 익절 vs 트레일/분할익절 — 과거 19건(US) 경로 백테스트 (net-of-cost).

운영자 지적(2026-06-25): 과거 매수/매도 DB로 지금 판정 가능. 미래 안 기다림.

비교 대상(모두 같은 entry, 같은 단일 매도 구조라 왕복 수수료 동일 → 상쇄):
- 현행 고정 target: target에서 매도. forward 추가 = 0.
- 풀 트레일: target에서 안 팔고 트레일(고점-giveback%)로 보유. 추가 왕복 없음.
- 분할 50/50: 절반 target 확정 + 절반 트레일. 트레일분에 매도 1회 추가(절반에 ~half_fee).
- 참고: 종가보유(naive) = 그냥 세션 종가까지.

트레일 룰: anchor(=target 매도가)부터 고점 추적, 고점*(1-give%) 이탈 시 그 가격 청산.
미발동이면 세션 종가 청산. give%가 곧 target에서의 하방(되돌림 허용폭)이라 별도 floor 불요.

read-only. yfinance forward 재구성. broker/Claude 무호출.
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ml" / "decisions.db"
# 왕복 수수료+슬리피지 가정(분할의 추가 매도분에만 적용). 절반 추가매도 ≈ half_fee.
HALF_EXTRA_FEE_PCT = 0.25


def _load(db: Path):
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT ticker,closed_at,pnl_pct FROM v2_learning_performance "
        "WHERE status='CLOSED' AND close_reason='CLOSED_CLAUDE_PRICE_TARGET' "
        "AND market='US' AND closed_at IS NOT NULL ORDER BY closed_at").fetchall()]
    con.close()
    return rows


def _fetch(sym: str):
    import yfinance as yf
    for itv in ("5m", "15m"):
        try:
            df = yf.download(sym, period="60d", interval=itv,
                             progress=False, auto_adjust=False)
            if df is not None and not df.empty:
                if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                    df.columns = df.columns.get_level_values(0)
                return df
        except Exception:
            continue
    return None


def _session_after(df, closed_at_iso: str, overnight: bool):
    import pandas as pd
    ts = pd.Timestamp(closed_at_iso)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    idx = df.index
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    d = df.copy()
    d.index = idx
    after = d[d.index >= ts]
    if after.empty:
        return None, None
    sell_day = after.index[0].date()
    anchor = float(after.iloc[0]["Open"])
    if anchor <= 0:
        return None, None
    if overnight:
        days = sorted({x.date() for x in after.index})[:2]
        seg = after[[x.date() in days for x in after.index]]
    else:
        seg = after[[x.date() == sell_day for x in after.index]]
    return anchor, seg


def _trail_forward(anchor: float, seg, give_pct: float) -> float:
    """anchor부터 트레일링. 반환 = (청산가/anchor-1)*100."""
    peak = anchor
    g = give_pct / 100.0
    for _, bar in seg.iterrows():
        hi = float(bar["High"]); lo = float(bar["Low"])
        peak = max(peak, hi)
        stop = peak * (1 - g)
        if lo <= stop:
            return (stop / anchor - 1) * 100
    return (float(seg["Close"].iloc[-1]) / anchor - 1) * 100


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--gives", default="2,3,4", help="트레일 giveback %% 후보")
    ap.add_argument("--overnight", action="store_true", help="다음 거래일까지 트레일 허용")
    ap.add_argument("--sleep", type=float, default=0.3)
    args = ap.parse_args()
    gives = [float(x) for x in args.gives.split(",")]

    rows = _load(Path(args.db))
    cache: dict[str, object] = {}
    recs = []
    for r in rows:
        tk = r["ticker"]
        if tk not in cache:
            cache[tk] = _fetch(tk)
            time.sleep(args.sleep)
        df = cache[tk]
        if df is None:
            continue
        anchor, seg = _session_after(df, r["closed_at"], args.overnight)
        if anchor is None or seg is None or seg.empty:
            continue
        rec = {"tk": tk, "when": str(r["closed_at"])[:10], "target": r["pnl_pct"] or 0.0,
               "close_fwd": (float(seg["Close"].iloc[-1]) / anchor - 1) * 100}
        for g in gives:
            rec[f"trail{g}"] = _trail_forward(anchor, seg, g)
        recs.append(rec)

    if not recs:
        print("데이터 없음")
        return 0

    hdr = f"{'TICKER':7}{'date':11}{'target%':>8}{'close_fwd%':>11}"
    for g in gives:
        hdr += f"{'trail'+str(g)+'%':>10}"
    print(hdr)
    for r in recs:
        line = f"{r['tk']:7}{r['when']:11}{r['target']:>8.2f}{r['close_fwd']:>11.2f}"
        for g in gives:
            line += f"{r[f'trail{g}']:>10.2f}"
        print(line)

    n = len(recs)
    print(f"\n=== 집계 n={n} (forward = target 이후 추가 손익, +면 러너 더 먹음) ===")
    print(f"현행 고정 target: 추가 0.00% (기준선)")
    naive = [r["close_fwd"] for r in recs]
    print(f"종가보유(naive): 평균 {mean(naive):+.2f}% 중앙 {median(naive):+.2f}% "
          f"개선건 {sum(1 for x in naive if x>0)}/{n}  <- 더 들고만 있기")
    print()
    for g in gives:
        vals = [r[f"trail{g}"] for r in recs]
        better = sum(1 for x in vals if x > 0.05)
        worse = sum(1 for x in vals if x < -0.05)
        # 풀 트레일: 추가 왕복 없음 -> net 추가 = 평균 forward
        full_net = mean(vals)
        # 분할 50/50: 절반만 트레일분 추가, 그 절반에 추가매도 수수료
        half_net = mean(0.5 * v - 0.5 * HALF_EXTRA_FEE_PCT for v in vals)
        print(f"[give {g}%] 풀트레일 추가 평균 {full_net:+.2f}% 중앙 {median(vals):+.2f}% | "
              f"개선 {better}/{n} 악화 {worse}/{n}")
        print(f"          분할50/50 net 추가 평균 {half_net:+.2f}% "
              f"(절반확정+절반트레일, 절반 추가매도 -{HALF_EXTRA_FEE_PCT}% 반영)")
    print("\n주: target/forward는 entry 기준 추가분. 풀트레일은 고정target과 왕복수 동일(수수료 상쇄).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
