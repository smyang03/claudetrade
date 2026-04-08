"""
phase1_trainer/grid_search.py — 파라미터 그리드 서치

backtester.py의 backtest_ticker()를 엔진으로 재사용.
별도 _backtest_one() 없음 — 엔진 완전 통일.

실행:
  python -m phase1_trainer.grid_search --market KR --strategy mean_reversion
  python -m phase1_trainer.grid_search --market US --strategy volatility_breakout
  python -m phase1_trainer.grid_search --market ALL --strategy ALL
  python -m phase1_trainer.grid_search --market KR --start 2022-01-01 --top 30

탐색 그리드 (mean_reversion):
  rsi_thr  : 25, 28, 30, 32, 34
  bb_thr   : 15, 17, 20, 25
  ma60_thr : 0.85, 0.90, 0.95
  vol_limit: 2.0, 2.5, 3.0

탐색 그리드 (volatility_breakout):
  vol_mult : 1.2, 1.4, 1.6, 1.8, 2.0
  k        : 0.40, 0.45, 0.50

탐색 그리드 (momentum):
  vol_mult : 1.2, 1.4, 1.6, 1.8

탐색 그리드 (gap_pullback):
  gap_min  : 0.010, 0.012, 0.015, 0.018
  vol_mult : 1.5, 1.8, 2.0
"""

from __future__ import annotations

import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from phase1_trainer.backtester import backtest_ticker, _load_price, _calc_stats

_PRICE_DIR = _ROOT / "data" / "price"

RESULT_DIR = _ROOT / "data" / "backtest"
RESULT_DIR.mkdir(parents=True, exist_ok=True)


def _get_all_tickers(market: str) -> list[str]:
    mkt = market.lower()
    return [p.stem[len(mkt) + 1:] for p in (_PRICE_DIR / mkt).glob(f"{mkt}_*.csv")]


# ── 그리드 정의 ───────────────────────────────────────────────────────────────
GRIDS: dict[str, list[dict]] = {
    "mean_reversion": [
        {"rsi_thr": rsi, "bb_thr": bb, "ma60_thr": ma, "vol_limit": vl,
         "tp_bb_mid": True, "sl_pct": 0.020, "tp_pct": 0.030, "max_hold": 7}
        for rsi in [25, 28, 30, 32, 34]
        for bb  in [15, 17, 20, 25]
        for ma  in [0.85, 0.90, 0.95]
        for vl  in [2.0, 2.5, 3.0]
    ],
    "volatility_breakout": [
        {"vol_mult": vm, "k": k, "tp_pct": 0.025, "sl_pct": 0.015, "max_hold": 2}
        for vm in [1.8, 2.0, 2.2, 2.5, 2.8]
        for k  in [0.40, 0.45, 0.50]
    ],
    "momentum": [
        {"vol_mult": vm, "tp_pct": 0.060, "sl_pct": 0.030, "max_hold": 5, "size_mult": 0.5}
        for vm in [1.2, 1.4, 1.6, 1.8]
    ],
    "gap_pullback": [
        {"gap_min": gm, "vol_mult": vm, "tp_pct": 0.025, "sl_pct": 0.010, "max_hold": 1}
        for gm in [0.010, 0.012, 0.015, 0.018]
        for vm in [1.5, 1.8, 2.0]
    ],
}


# ── 전체 종목 집계 ─────────────────────────────────────────────────────────────
def run_grid(market: str, strategy: str, start_date: str = "2022-01-01",
             top_n: int = 20) -> None:
    tickers = _get_all_tickers(market)
    grid    = GRIDS.get(strategy, [])

    if not grid:
        print(f"[grid_search] 전략 없음: {strategy}")
        return

    print(f"\n{'='*60}")
    print(f"그리드 서치: {market} / {strategy}")
    print(f"파라미터 조합: {len(grid)}개  종목: {len(tickers)}개  시작: {start_date}")
    print(f"엔진: backtester.backtest_ticker() (backtester와 완전 동일)")
    print(f"{'='*60}")

    # 가격 데이터 선로드
    dfs: dict[str, any] = {}
    for t in tickers:
        df = _load_price(market, t)
        if not df.empty:
            dfs[t] = df
    print(f"유효 종목: {len(dfs)}개\n")

    results = []
    for idx, params in enumerate(grid, 1):
        all_trades = []
        for ticker, df in dfs.items():
            r = backtest_ticker(
                df, ticker, strategy,
                market=market,
                start_date=start_date,
                params_override=params,
            )
            all_trades.extend(r.get("trades", []))

        if len(all_trades) < 10:
            continue

        stats = _calc_stats(all_trades)
        results.append({
            "params":   params,
            "trades":   stats["n_trades"],
            "win_rate": stats["win_rate"],
            "avg_pnl":  stats["avg_pnl"],
            "sharpe":   stats["sharpe"],
            "maxdd":    stats["max_drawdown"],
        })

        if idx % 20 == 0:
            print(f"  진행: {idx}/{len(grid)} 조합 완료...")

    if not results:
        print("결과 없음 (거래 10건 미만 조합만 존재)")
        return

    results.sort(key=lambda x: x["sharpe"], reverse=True)

    print(f"\n{'순위':>4}  {'거래':>5}  {'승률':>6}  {'평균PnL':>8}  {'Sharpe':>7}  {'MaxDD':>7}  파라미터")
    print("-" * 80)
    for rank, r in enumerate(results[:top_n], 1):
        p = r["params"]
        if strategy == "mean_reversion":
            p_str = f"rsi={p['rsi_thr']}  bb={p['bb_thr']}  ma60={p['ma60_thr']}  vol<={p['vol_limit']}"
        elif strategy == "volatility_breakout":
            p_str = f"vol_mult={p['vol_mult']}  k={p['k']}"
        elif strategy == "momentum":
            p_str = f"vol_mult={p['vol_mult']}"
        elif strategy == "gap_pullback":
            p_str = f"gap_min={p['gap_min']}  vol_mult={p['vol_mult']}"
        else:
            p_str = str(p)

        print(f"{rank:>4}  {r['trades']:>5}  {r['win_rate']:>5.1f}%  "
              f"{r['avg_pnl']:>+7.3f}%  {r['sharpe']:>7.3f}  {r['maxdd']:>+6.1f}%  {p_str}")

    fname = RESULT_DIR / f"grid_{market}_{strategy}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(fname, "w", encoding="utf-8") as f:
        json.dump({"market": market, "strategy": strategy,
                   "start_date": start_date, "results": results[:50]},
                  f, ensure_ascii=False, indent=2)
    print(f"\n저장: {fname}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--market",   choices=["KR", "US", "ALL"], default="ALL")
    parser.add_argument("--strategy", default="ALL",
                        help="mean_reversion / volatility_breakout / momentum / gap_pullback / ALL")
    parser.add_argument("--start",    default="2022-01-01")
    parser.add_argument("--top",      type=int, default=20)
    args = parser.parse_args()

    markets    = ["KR", "US"] if args.market == "ALL" else [args.market]
    strategies = list(GRIDS.keys()) if args.strategy == "ALL" else [args.strategy]

    for mkt in markets:
        for strat in strategies:
            run_grid(mkt, strat, start_date=args.start, top_n=args.top)
