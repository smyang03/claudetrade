from __future__ import annotations

"""TARGET 익절 후 forward 측정 — "팔고 나서 더 갔나 / 반전했나"를 전수로 깐다.

질문: CLOSED_CLAUDE_PRICE_TARGET 청산이 러너를 일찍 자르는가(capture 누수),
아니면 반전 전에 이익을 지키는가(giveback 방어)? AXON 일화가 패턴인지 예외인지.

방법(read-only, broker/Claude 무호출):
- v2_learning_performance에서 TARGET 청산 전수(티커/매도시각/실현) 로드
- 매도 시각을 yfinance 5분봉에 앵커 → 같은 세션 잔여 구간의 forward 측정
  - fwd_max%  : 매도 후 같은날 고점까지 추가 상승 (러너였으면 +)
  - fwd_close%: 매도 후 같은날 종가까지 (들고 있었으면 얻었을/잃었을 것)
  - nd_max%   : 다음 거래일 고점까지 (오버나잇 러너)
- 비용 주: 매도 대신 보유는 추가 왕복이 없으므로 fwd_close%는 거의 순수 기회손익.
  (AXON식 재매수는 별개 비용 — 이 도구는 그걸 측정 안 함)
"""

import argparse
import sqlite3
import sys
import time
from datetime import timedelta
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ml" / "decisions.db"


def _load_targets(db: Path, market: str | None) -> list[dict]:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    q = ("SELECT market,ticker,closed_at,pnl_pct,pnl_pct_net,market_regime "
         "FROM v2_learning_performance WHERE status='CLOSED' "
         "AND close_reason='CLOSED_CLAUDE_PRICE_TARGET' AND closed_at IS NOT NULL")
    args: list = []
    if market:
        q += " AND market=?"
        args.append(market)
    q += " ORDER BY market,closed_at"
    rows = [dict(r) for r in con.execute(q, args).fetchall()]
    con.close()
    return rows


def _yf_symbol(market: str, ticker: str) -> list[str]:
    if market == "KR":
        return [f"{ticker}.KS", f"{ticker}.KQ"]
    return [ticker]


def _fetch(sym: str):
    import yfinance as yf
    # 5분봉(60일 한도) 우선, 실패 시 15분봉
    for interval in ("5m", "15m"):
        try:
            df = yf.download(sym, period="60d", interval=interval,
                             progress=False, auto_adjust=False)
            if df is not None and not df.empty:
                # 멀티컬럼(단일티커) 평탄화
                if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                    df.columns = df.columns.get_level_values(0)
                return df, interval
        except Exception:
            continue
    return None, None


def _measure(df, closed_at_iso: str) -> dict | None:
    import pandas as pd
    ts = pd.Timestamp(closed_at_iso)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    ts = ts.tz_convert("UTC")
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    df = df.copy()
    df.index = idx

    after = df[df.index >= ts]
    if after.empty:
        return None
    sell_day = after.index[0].date()
    anchor = float(after.iloc[0]["Open"])
    if anchor <= 0:
        return None

    same = after[[d.date() == sell_day for d in after.index]]
    if same.empty:
        return None
    fwd_max = float(same["High"].max())
    fwd_close = float(same["Close"].iloc[-1])

    # 다음 거래일
    future = df[[d.date() > sell_day for d in df.index]]
    nd_max = None
    if not future.empty:
        nd_day = future.index[0].date()
        nd = future[[d.date() == nd_day for d in future.index]]
        if not nd.empty:
            nd_max = float(nd["High"].max())

    return {
        "anchor": anchor,
        "fwd_max_pct": (fwd_max / anchor - 1) * 100,
        "fwd_close_pct": (fwd_close / anchor - 1) * 100,
        "nd_max_pct": (nd_max / anchor - 1) * 100 if nd_max else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="TARGET 익절 후 forward 측정")
    ap.add_argument("--market", choices=["US", "KR"], default=None)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--runner-thresh", type=float, default=1.0,
                    help="매도 후 고점 추가상승 이 %% 이상이면 '러너 끊김'으로 분류")
    args = ap.parse_args()

    rows = _load_targets(Path(args.db), args.market)
    if not rows:
        print("TARGET 청산 없음")
        return 0

    print(f"{'MKT':3} {'TICKER':7} {'when':16} {'실현%':>7} "
          f"{'fwd_max%':>9} {'fwd_close%':>11} {'nd_max%':>8}  판정")
    by_mkt: dict[str, list[dict]] = {}
    for r in rows:
        df = itv = None
        for sym in _yf_symbol(r["market"], r["ticker"]):
            df, itv = _fetch(sym)
            if df is not None:
                break
        time.sleep(args.sleep)
        if df is None:
            print(f"{r['market']:3} {r['ticker']:7} {str(r['closed_at'])[:16]} "
                  f"{r['pnl_pct'] or 0:>7.2f}  (데이터없음)")
            continue
        m = _measure(df, r["closed_at"])
        if m is None:
            print(f"{r['market']:3} {r['ticker']:7} {str(r['closed_at'])[:16]} "
                  f"{r['pnl_pct'] or 0:>7.2f}  (앵커실패)")
            continue
        runner = m["fwd_max_pct"] >= args.runner_thresh
        reversed_ = m["fwd_close_pct"] < 0
        verdict = "러너끊김" if runner else ("반전방어" if reversed_ else "플랫")
        nd = f"{m['nd_max_pct']:>8.2f}" if m["nd_max_pct"] is not None else "     -  "
        print(f"{r['market']:3} {r['ticker']:7} {str(r['closed_at'])[:16]} "
              f"{r['pnl_pct'] or 0:>7.2f} {m['fwd_max_pct']:>9.2f} "
              f"{m['fwd_close_pct']:>11.2f} {nd}  {verdict}")
        m.update(runner=runner, reversed=reversed_)
        by_mkt.setdefault(r["market"], []).append(m)

    print("\n=== 집계 (매도 후 같은 세션 기준) ===")
    for mkt, ms in by_mkt.items():
        n = len(ms)
        runners = sum(1 for x in ms if x["runner"])
        revs = sum(1 for x in ms if x["reversed"])
        print(f"\n[{mkt}] n={n}")
        print(f"  fwd_max%   중앙 {median(x['fwd_max_pct'] for x in ms):+.2f} "
              f"평균 {mean(x['fwd_max_pct'] for x in ms):+.2f}")
        print(f"  fwd_close% 중앙 {median(x['fwd_close_pct'] for x in ms):+.2f} "
              f"평균 {mean(x['fwd_close_pct'] for x in ms):+.2f}  "
              f"<- 들고 있었으면 추가 손익(거의 순수, 추가왕복 없음)")
        print(f"  러너끊김(fwd_max>=+{args.runner_thresh}%): {runners}/{n} "
              f"({runners/n*100:.0f}%)")
        print(f"  반전방어(fwd_close<0): {revs}/{n} ({revs/n*100:.0f}%)")
        ndvals = [x["nd_max_pct"] for x in ms if x["nd_max_pct"] is not None]
        if ndvals:
            print(f"  다음날 고점 중앙 {median(ndvals):+.2f} (오버나잇 러너)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
