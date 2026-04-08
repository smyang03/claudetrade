"""
phase1_trainer/sim_runner.py

통합 시뮬레이션 러너.

목적:
- 2022-01-01 이후 KR/US 전략별 그리드 시뮬레이션을 한 번에 실행
- fixed 엔진(backtester 기반), adaptive 엔진(mode 반영) 중 선택 가능
- 결과를 콘솔 표 + CSV + JSON으로 저장

예시:
  python -m phase1_trainer.sim_runner --market ALL --engine both --start 2022-01-01 --top 15
  python -m phase1_trainer.sim_runner --market US --strategy mean_reversion --engine adaptive
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from phase1_trainer.backtester import _calc_stats, _load_price, backtest_ticker
from phase1_trainer.grid_search import GRIDS as FIXED_GRIDS
from phase1_trainer.grid_search_adaptive import (
    GRIDS as ADAPTIVE_GRIDS,
    _SIG_FN,
    _backtest_adaptive,
    _build_mode_series,
    _stats_by_mode,
)

ROOT = Path(__file__).parent.parent
PRICE_DIR = ROOT / "data" / "price"
RESULT_DIR = ROOT / "data" / "backtest"
RESULT_DIR.mkdir(parents=True, exist_ok=True)


def _get_all_tickers(market: str) -> list[str]:
    mkt = market.lower()
    return sorted(p.stem[len(mkt) + 1:] for p in (PRICE_DIR / mkt).glob(f"{mkt}_*.csv"))


def _param_to_str(strategy: str, p: dict) -> str:
    if strategy == "mean_reversion":
        return (
            f"rsi={p.get('rsi_thr')} "
            f"bb={p.get('bb_thr')} "
            f"ma60={p.get('ma60_thr')} "
            f"vol<={p.get('vol_limit')}"
        )
    if strategy == "volatility_breakout":
        return f"vol_mult={p.get('vol_mult')} k={p.get('k')}"
    if strategy == "momentum":
        return f"vol_mult={p.get('vol_mult')}"
    if strategy == "gap_pullback":
        return f"gap_min={p.get('gap_min')} vol_mult={p.get('vol_mult')}"
    return json.dumps(p, ensure_ascii=False, sort_keys=True)


def _format_pf(value) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}"


def _print_table(title: str, rows: list[dict], top_n: int):
    print("\n" + "=" * 110)
    print(title)
    print("=" * 110)
    print(f"{'순위':>4}  {'거래':>6}  {'승률':>7}  {'평균PnL':>9}  {'Sharpe':>7}  {'MaxDD':>8}  {'PF':>6}  파라미터")
    print("-" * 110)
    for i, r in enumerate(rows[:top_n], 1):
        print(
            f"{i:>4}  "
            f"{r['trades']:>6}  "
            f"{r['win_rate']:>6.1f}%  "
            f"{r['avg_pnl']:>+8.3f}%  "
            f"{r['sharpe']:>7.3f}  "
            f"{r['maxdd']:>+7.1f}%  "
            f"{_format_pf(r.get('pf')):>6}  "
            f"{r['param_str']}"
        )


def _save_outputs(prefix: str, payload: dict, rows: list[dict]):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = RESULT_DIR / f"{prefix}_{ts}.json"
    csv_path = RESULT_DIR / f"{prefix}_{ts}.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "rank", "market", "strategy", "engine", "trades", "win_rate",
                "avg_pnl", "sharpe", "maxdd", "pf", "param_str",
            ],
        )
        writer.writeheader()
        for i, row in enumerate(rows, 1):
            writer.writerow({
                "rank": i,
                "market": row["market"],
                "strategy": row["strategy"],
                "engine": row["engine"],
                "trades": row["trades"],
                "win_rate": row["win_rate"],
                "avg_pnl": row["avg_pnl"],
                "sharpe": row["sharpe"],
                "maxdd": row["maxdd"],
                "pf": row.get("pf"),
                "param_str": row["param_str"],
            })

    return json_path, csv_path


# 시장+전략 조합 비활성화 목록 (strategy.params()의 disabled=True 반영)
_DISABLED_COMBOS: set[tuple[str, str]] = {
    ("volatility_breakout", "KR"),
    ("volatility_breakout", "US"),
    ("momentum", "US"),
}


def run_fixed_grid(market: str, strategy: str, start_date: str) -> list[dict]:
    if (strategy, market) in _DISABLED_COMBOS:
        print(f"  [SKIP] {market} {strategy} — strategy disabled for this market")
        return []
    tickers = _get_all_tickers(market)
    grid = FIXED_GRIDS[strategy]
    dfs: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        df = _load_price(market, ticker)
        if not df.empty:
            dfs[ticker] = df

    rows = []
    for params in grid:
        all_trades = []
        for ticker, df in dfs.items():
            result = backtest_ticker(
                df,
                ticker,
                strategy,
                market=market,
                start_date=start_date,
                params_override=params,
            )
            all_trades.extend(result.get("trades", []))

        if len(all_trades) < 10:
            continue

        stats = _calc_stats(all_trades)
        rows.append({
            "market": market,
            "strategy": strategy,
            "engine": "fixed",
            "params": params,
            "param_str": _param_to_str(strategy, params),
            "trades": stats["n_trades"],
            "win_rate": stats["win_rate"],
            "avg_pnl": stats["avg_pnl"],
            "sharpe": stats["sharpe"],
            "maxdd": stats["max_drawdown"],
            "pf": stats.get("profit_factor"),
        })

    rows.sort(key=lambda x: (x["sharpe"], x["avg_pnl"]), reverse=True)
    return rows


def run_adaptive_grid(market: str, strategy: str, start_date: str) -> list[dict]:
    if (strategy, market) in _DISABLED_COMBOS:
        print(f"  [SKIP] {market} {strategy} — strategy disabled for this market")
        return []
    tickers = _get_all_tickers(market)
    grid = ADAPTIVE_GRIDS[strategy]
    sig_fn: Callable = _SIG_FN[strategy]
    mode_series = _build_mode_series(market)

    dfs: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        df = _load_price(market, ticker)
        if not df.empty:
            dfs[ticker] = df

    rows = []
    for params in grid:
        all_trades = []
        for _, df in dfs.items():
            trades = _backtest_adaptive(
                df,
                sig_fn,
                params,
                mode_series,
                start_date,
                strategy=strategy,
                market=market,
            )
            all_trades.extend(trades)

        if len(all_trades) < 10:
            continue

        stats = _calc_stats(all_trades)
        rows.append({
            "market": market,
            "strategy": strategy,
            "engine": "adaptive",
            "params": params,
            "param_str": _param_to_str(strategy, params),
            "trades": stats["n_trades"],
            "win_rate": stats["win_rate"],
            "avg_pnl": stats["avg_pnl"],
            "sharpe": stats["sharpe"],
            "maxdd": stats["max_drawdown"],
            "pf": stats.get("profit_factor"),
            "by_mode": _stats_by_mode(all_trades),
        })

    rows.sort(key=lambda x: (x["sharpe"], x["avg_pnl"]), reverse=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description="KR/US 통합 시뮬레이션 러너")
    parser.add_argument("--market", choices=["KR", "US", "ALL"], default="ALL")
    parser.add_argument(
        "--strategy",
        choices=["mean_reversion", "volatility_breakout", "momentum", "gap_pullback", "ALL"],
        default="ALL",
    )
    parser.add_argument("--engine", choices=["fixed", "adaptive", "both"], default="both")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    markets = ["KR", "US"] if args.market == "ALL" else [args.market]
    strategies = list(FIXED_GRIDS.keys()) if args.strategy == "ALL" else [args.strategy]
    engines = ["fixed", "adaptive"] if args.engine == "both" else [args.engine]

    started_at = datetime.now()
    print("#" * 110)
    print(f"통합 시뮬레이션 시작: {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"시장={markets} 전략={strategies} 엔진={engines} 시작일={args.start}")
    print("#" * 110)

    all_rows = []
    for market in markets:
        for strategy in strategies:
            for engine in engines:
                print(f"\n[{engine}] {market} / {strategy} 실행 중...")
                if engine == "fixed":
                    rows = run_fixed_grid(market, strategy, args.start)
                else:
                    rows = run_adaptive_grid(market, strategy, args.start)

                title = f"{engine.upper()} | {market} | {strategy} | start={args.start}"
                _print_table(title, rows, args.top)
                payload = {
                    "market": market,
                    "strategy": strategy,
                    "engine": engine,
                    "start_date": args.start,
                    "results": rows[: max(args.top, 50)],
                }
                prefix = f"sim_{engine}_{market}_{strategy}"
                json_path, csv_path = _save_outputs(prefix, payload, rows[: max(args.top, 50)])
                print(f"저장: {json_path}")
                print(f"저장: {csv_path}")
                all_rows.extend(rows)

    if all_rows:
        all_rows.sort(key=lambda x: (x["sharpe"], x["avg_pnl"]), reverse=True)
        _print_table("전체 통합 상위 결과", all_rows, args.top)
        summary_payload = {
            "started_at": started_at.isoformat(timespec="seconds"),
            "market": args.market,
            "strategy": args.strategy,
            "engine": args.engine,
            "start_date": args.start,
            "results": all_rows[: max(args.top, 100)],
        }
        json_path, csv_path = _save_outputs("sim_summary", summary_payload, all_rows[: max(args.top, 100)])
        print(f"\n통합 저장: {json_path}")
        print(f"통합 저장: {csv_path}")


if __name__ == "__main__":
    main()
