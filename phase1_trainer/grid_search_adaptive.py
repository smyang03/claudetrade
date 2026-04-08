"""
phase1_trainer/grid_search_adaptive.py — 모드 적응형 파라미터 그리드 서치

목적:
  - 날짜별 시장 모드(Brain mode)를 반영해 현실적인 백테스트 수행
  - 2022-01-01 ~ 현재 전 기간 KR/US 동시 탐색
  - 고정 모드 그리드서치의 한계(약세장 대응 미반영) 보완

모드 결정 우선순위:
  1. logs/daily_judgment/{YYYYMMDD}_{MARKET}.json (2024-10-01~)
  2. 프록시: 시장 대표지수(SPY/005930) 종가 vs MA60으로 추정

모드별 진입 로직:
  HALT / DEFENSIVE  → 신규 진입 차단
  CAUTIOUS_BEAR     → vol 필터 강화 (vol_mult ×1.3, vol_limit ×0.8)
  MILD_BEAR         → vol 필터 소폭 강화 (vol_mult ×1.1, vol_limit ×0.9)
  NEUTRAL / BULL    → 기본 파라미터 사용

실행:
  python -m phase1_trainer.grid_search_adaptive --market KR
  python -m phase1_trainer.grid_search_adaptive --market US
  python -m phase1_trainer.grid_search_adaptive --market ALL
  python -m phase1_trainer.grid_search_adaptive --market ALL --strategy momentum
"""

from __future__ import annotations

import sys
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from phase1_trainer.backtester import _load_price, _calc_stats
import strategy.mean_reversion    as _mr
import strategy.volatility_breakout as _vb
import strategy.momentum           as _mom
import strategy.gap_pullback       as _gap

_PRICE_DIR    = _ROOT / "data" / "price"
_JUDGMENT_DIR = _ROOT / "logs" / "daily_judgment"
RESULT_DIR    = _ROOT / "data" / "backtest"
RESULT_DIR.mkdir(parents=True, exist_ok=True)

_SIG_FN = {
    "mean_reversion":     _mr.signal,
    "volatility_breakout": _vb.signal,
    "momentum":           _mom.signal,
    "gap_pullback":       _gap.signal,
}

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
        for vm in [1.2, 1.4, 1.6, 1.8, 2.0]
    ],
    "gap_pullback": [
        {"gap_min": gm, "vol_mult": vm, "tp_pct": 0.025, "sl_pct": 0.010, "max_hold": 1}
        for gm in [0.008, 0.010, 0.012, 0.015, 0.018]
        for vm in [1.2, 1.5, 1.8, 2.0]
    ],
}

_SKIP_MODES   = {"HALT", "DEFENSIVE"}
_BEAR_HEAVY   = {"CAUTIOUS_BEAR"}
_BEAR_MILD    = {"MILD_BEAR"}


# ── 모드 시리즈 구성 ──────────────────────────────────────────────────────────
def _build_mode_series(market: str) -> dict[str, tuple[str, float]]:
    """
    날짜 → (mode, conf) 매핑 반환.
    judgment 파일 우선, 없는 날짜는 지수 프록시로 추정.
    """
    mode_series: dict[str, tuple[str, float]] = {}

    # 1. daily_judgment 파일에서 로드
    mkt_upper = market.upper()
    for jpath in _JUDGMENT_DIR.glob(f"*_{mkt_upper}.json"):
        raw_date = jpath.stem.split("_")[0]   # "20241001"
        if len(raw_date) != 8:
            continue
        date_key = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        try:
            data = json.loads(jpath.read_text(encoding="utf-8"))
            consensus  = data.get("consensus") or {}
            judgments  = data.get("judgments") or {}
            mode = consensus.get("mode") or "NEUTRAL"
            confs = [
                j.get("confidence", 0.6)
                for j in judgments.values()
                if isinstance(j, dict) and j.get("confidence") is not None
            ]
            conf = sum(confs) / len(confs) if confs else 0.6
            mode_series[date_key] = (mode, round(conf, 3))
        except Exception:
            pass

    # 2. 프록시: 지수 가격 vs MA60 → 모드 추정
    proxy = "SPY" if market.upper() == "US" else "005930"
    proxy_path = _PRICE_DIR / market.lower() / f"{market.lower()}_{proxy}.csv"
    if proxy_path.exists():
        try:
            pdf = (pd.read_csv(proxy_path, parse_dates=["date"])
                   .sort_values("date").reset_index(drop=True))
            pdf["ma60"] = pdf["close"].rolling(60, min_periods=30).mean()
            pdf["ma20"] = pdf["close"].rolling(20, min_periods=10).mean()

            for _, row in pdf.iterrows():
                date_key = str(row["date"])[:10]
                if date_key in mode_series:
                    continue   # judgment 우선
                if pd.isna(row.get("ma60")) or row.get("ma60", 0) <= 0:
                    mode_series[date_key] = ("NEUTRAL", 0.6)
                    continue
                ratio = row["close"] / row["ma60"]
                if ratio < 0.92:
                    mode, conf = "CAUTIOUS_BEAR", 0.75
                elif ratio < 0.97:
                    mode, conf = "MILD_BEAR", 0.65
                elif ratio > 1.08:
                    mode, conf = "MODERATE_BULL", 0.70
                elif ratio > 1.03:
                    mode, conf = "MILD_BULL", 0.65
                else:
                    mode, conf = "NEUTRAL", 0.60
                mode_series[date_key] = (mode, conf)
        except Exception:
            pass

    return mode_series


def _mode_adjust_params(base: dict, mode: str,
                        strategy: str = "", market: str = "") -> Optional[dict]:
    """
    모드에 따라 파라미터 조정.
    HALT/DEFENSIVE → None (진입 차단).
    전략별 추가 차단:
      momentum            + CAUTIOUS_BEAR       → None
      mean_reversion      + KR + MODERATE_BULL  → None
      volatility_breakout + KR                  → None (전 모드 비활성화)
    """
    if mode in _SKIP_MODES:
        return None
    if strategy == "momentum" and mode == "CAUTIOUS_BEAR":
        return None
    if strategy == "mean_reversion" and market.upper() == "KR" and mode == "MODERATE_BULL":
        return None
    if strategy == "volatility_breakout" and market.upper() in {"KR", "US"}:
        return None
    if strategy == "momentum" and market.upper() == "US":
        return None
    if strategy == "momentum" and mode == "MILD_BEAR":
        return None
    if strategy == "gap_pullback" and market.upper() == "KR" and mode == "CAUTIOUS_BEAR":
        return None
    if strategy in {"mean_reversion", "gap_pullback"} and market.upper() == "US" and mode == "MODERATE_BULL":
        return None

    p = dict(base)
    if mode in _BEAR_HEAVY:
        if "vol_mult"  in p: p["vol_mult"]  = round(p["vol_mult"]  * 1.3, 2)
        if "vol_limit" in p: p["vol_limit"] = round(p["vol_limit"] * 0.8, 2)
        if "rsi_thr"   in p: p["rsi_thr"]   = max(20, p["rsi_thr"] - 3)
        if "gap_min"   in p: p["gap_min"]   = round(p["gap_min"]   * 1.3, 4)
    elif mode in _BEAR_MILD:
        if "vol_mult"  in p: p["vol_mult"]  = round(p["vol_mult"]  * 1.1, 2)
        if "vol_limit" in p: p["vol_limit"] = round(p["vol_limit"] * 0.9, 2)
        if "rsi_thr"   in p: p["rsi_thr"]   = max(20, p["rsi_thr"] - 1)
    return p


# ── 단일 종목 모드 적응형 백테스트 ───────────────────────────────────────────
def _backtest_adaptive(
    df: pd.DataFrame,
    sig_fn,
    base_params: dict,
    mode_series: dict[str, tuple[str, float]],
    start_date: Optional[str] = None,
    strategy: str = "",
    market: str = "",
) -> list[dict]:
    """
    날짜별 모드를 반영한 단일 종목 백테스트.
    backtester.py와 동일한 exit 로직 사용.
    """
    tp_pct   = float(base_params.get("tp_pct", 0.025))
    sl_pct   = float(base_params.get("sl_pct", 0.015))
    max_hold = int(base_params.get("max_hold", 5))

    work = df.copy()
    if start_date:
        work = work[work["date"] >= pd.Timestamp(start_date)]
    work = work.reset_index(drop=True)

    if len(work) < 30:
        return []

    trades: list[dict] = []
    position: Optional[dict] = None

    for i in range(30, len(work)):
        row   = work.iloc[i]
        price = float(row.get("close", 0))
        if price <= 0:
            continue

        date_str = str(row.get("date", ""))[:10]
        mode, _conf = mode_series.get(date_str, ("NEUTRAL", 0.6))

        # ── 보유 포지션 관리 (모드 무관) ──────────────────────────────────
        if position is not None:
            held       = i - position["entry_idx"]
            exit_price = None
            exit_reason = None

            if price >= position["tp"]:
                exit_price, exit_reason = position["tp"], "tp"
            elif price <= position["sl"]:
                exit_price, exit_reason = position["sl"], "sl"
            elif held >= max_hold:
                exit_price, exit_reason = price, "max_hold"

            if exit_price is not None:
                pnl = (exit_price - position["entry"]) / position["entry"] * 100
                trades.append({
                    "entry_date":  position["entry_date"],
                    "exit_date":   date_str,
                    "entry_price": round(position["entry"], 4),
                    "exit_price":  round(exit_price, 4),
                    "pnl_pct":     round(pnl, 3),
                    "reason":      exit_reason,
                    "held_days":   held,
                    "mode":        position["mode"],
                })
                position = None

        # ── 신규 진입 (모드 적용) ──────────────────────────────────────────
        if position is None:
            adj_params = _mode_adjust_params(base_params, mode, strategy, market)
            if adj_params is None:
                continue   # HALT/DEFENSIVE: 진입 차단

            try:
                fired = sig_fn(work, i, adj_params)
            except Exception:
                fired = False

            if fired:
                position = {
                    "entry":      price,
                    "entry_date": date_str,
                    "entry_idx":  i,
                    "tp":         price * (1 + tp_pct),
                    "sl":         price * (1 - sl_pct),
                    "mode":       mode,
                }

    # 미청산 포지션 마지막 종가로 강제 청산
    if position is not None and len(work) > 0:
        last = work.iloc[-1]
        ep   = float(last.get("close", position["entry"]))
        pnl  = (ep - position["entry"]) / position["entry"] * 100
        trades.append({
            "entry_date":  position["entry_date"],
            "exit_date":   str(last.get("date", ""))[:10],
            "entry_price": round(position["entry"], 4),
            "exit_price":  round(ep, 4),
            "pnl_pct":     round(pnl, 3),
            "reason":      "end_of_data",
            "held_days":   len(work) - 1 - position["entry_idx"],
            "mode":        position["mode"],
        })

    return trades


# ── 모드별 성과 분석 ──────────────────────────────────────────────────────────
def _stats_by_mode(trades: list[dict]) -> dict:
    from collections import defaultdict
    bucket: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        bucket[t.get("mode", "?")].append(t["pnl_pct"])
    return {
        mode: {
            "n":        len(pnls),
            "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
            "avg_pnl":  round(np.mean(pnls), 3),
        }
        for mode, pnls in bucket.items()
    }


# ── 전체 그리드 실행 ──────────────────────────────────────────────────────────
def run_grid(market: str, strategy: str,
             start_date: str = "2022-01-01", top_n: int = 20) -> None:
    mkt_lower = market.lower()
    tickers   = [p.stem[len(mkt_lower) + 1:]
                 for p in (_PRICE_DIR / mkt_lower).glob(f"{mkt_lower}_*.csv")]
    grid      = GRIDS.get(strategy, [])
    sig_fn    = _SIG_FN.get(strategy)

    if not grid or not sig_fn:
        print(f"[adaptive_grid] 전략 없음: {strategy}")
        return

    print(f"\n{'='*65}")
    print(f"모드 적응형 그리드 서치: {market} / {strategy}")
    print(f"파라미터 조합: {len(grid)}개  종목: {len(tickers)}개  시작: {start_date}")
    print(f"{'='*65}")

    # 모드 시리즈 구성
    print("  [1/3] 모드 시리즈 구성 중...")
    mode_series = _build_mode_series(market)
    judgment_days = sum(1 for v in mode_series.values() if v[1] != 0.6 or True)
    bear_days     = sum(1 for m, _ in mode_series.values() if "BEAR" in m)
    skip_days     = sum(1 for m, _ in mode_series.values() if m in _SKIP_MODES)
    print(f"     총 날짜: {len(mode_series)}일  "
          f"약세(BEAR): {bear_days}일  진입차단(HALT/DEF): {skip_days}일")

    # 가격 데이터 선로드
    print("  [2/3] 가격 데이터 로드 중...")
    dfs: dict[str, pd.DataFrame] = {}
    for t in tickers:
        df = _load_price(market, t)
        if not df.empty:
            dfs[t] = df
    print(f"     유효 종목: {len(dfs)}개")

    # 그리드 탐색
    print(f"  [3/3] 그리드 탐색 시작 ({len(grid)}개 조합 × {len(dfs)}개 종목)...\n")
    results = []
    for idx, params in enumerate(grid, 1):
        all_trades: list[dict] = []
        for df in dfs.values():
            trades = _backtest_adaptive(df, sig_fn, params, mode_series, start_date,
                                        strategy=strategy, market=market)
            all_trades.extend(trades)

        if len(all_trades) < 10:
            continue

        stats    = _calc_stats(all_trades)
        by_mode  = _stats_by_mode(all_trades)
        results.append({
            "params":   params,
            "trades":   stats["n_trades"],
            "win_rate": stats["win_rate"],
            "avg_pnl":  stats["avg_pnl"],
            "sharpe":   stats["sharpe"],
            "maxdd":    stats["max_drawdown"],
            "pf":       stats.get("profit_factor"),
            "by_mode":  by_mode,
        })

        if idx % 30 == 0 or idx == len(grid):
            print(f"  진행: {idx}/{len(grid)} ({idx/len(grid)*100:.0f}%)  "
                  f"유효 조합: {len(results)}개")

    if not results:
        print("결과 없음")
        return

    results.sort(key=lambda x: x["sharpe"], reverse=True)

    print(f"\n{'순위':>4}  {'거래':>5}  {'승률':>6}  {'평균PnL':>8}  "
          f"{'Sharpe':>7}  {'MaxDD':>7}  {'PF':>5}  파라미터")
    print("-" * 85)
    for rank, r in enumerate(results[:top_n], 1):
        p = r["params"]
        if strategy == "mean_reversion":
            p_str = (f"rsi={p['rsi_thr']}  bb={p['bb_thr']}  "
                     f"ma60={p['ma60_thr']}  vol<={p['vol_limit']}")
        elif strategy == "volatility_breakout":
            p_str = f"vol_mult={p['vol_mult']}  k={p['k']}"
        elif strategy == "momentum":
            p_str = f"vol_mult={p['vol_mult']}"
        elif strategy == "gap_pullback":
            p_str = f"gap_min={p['gap_min']}  vol_mult={p['vol_mult']}"
        else:
            p_str = str(p)

        pf_str = f"{r['pf']:.2f}" if r["pf"] else "∞"
        print(f"{rank:>4}  {r['trades']:>5}  {r['win_rate']:>5.1f}%  "
              f"{r['avg_pnl']:>+7.3f}%  {r['sharpe']:>7.3f}  "
              f"{r['maxdd']:>+6.1f}%  {pf_str:>5}  {p_str}")

    # Top-1 모드별 상세
    if results:
        print(f"\n  [Top-1 모드별 성과]")
        for mode, ms in sorted(results[0]["by_mode"].items(),
                               key=lambda x: -x[1]["n"]):
            print(f"    {mode:<18} n={ms['n']:>4}  "
                  f"승률={ms['win_rate']:>5.1f}%  avg={ms['avg_pnl']:>+6.3f}%")

    # 저장
    fname = (RESULT_DIR /
             f"adaptive_{market}_{strategy}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(fname, "w", encoding="utf-8") as f:
        # by_mode는 저장용으로 직렬화
        save_results = []
        for r in results[:50]:
            sr = dict(r)
            sr["by_mode"] = {k: v for k, v in r["by_mode"].items()}
            save_results.append(sr)
        json.dump({"market": market, "strategy": strategy,
                   "start_date": start_date,
                   "mode_days": len(mode_series),
                   "bear_days": bear_days,
                   "skip_days": skip_days,
                   "results": save_results},
                  f, ensure_ascii=False, indent=2)
    print(f"\n  저장: {fname}")


if __name__ == "__main__":
    import sys
    import traceback

    parser = argparse.ArgumentParser(description="모드 적응형 파라미터 그리드 서치")
    parser.add_argument("--market",   choices=["KR", "US", "ALL"], default="ALL")
    parser.add_argument("--strategy", default="ALL",
                        help="mean_reversion / volatility_breakout / momentum / gap_pullback / ALL")
    parser.add_argument("--start",    default="2022-01-01")
    parser.add_argument("--top",      type=int, default=20)
    args = parser.parse_args()

    markets    = ["KR", "US"] if args.market == "ALL" else [args.market]
    strategies = list(GRIDS.keys()) if args.strategy == "ALL" else [args.strategy]

    total = len(markets) * len(strategies)
    done  = 0
    failed: list[str] = []

    started_at = datetime.now()
    print(f"\n{'#'*65}")
    print(f"  전체 실행 시작: {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  대상: {markets} × {strategies}  ({total}개 작업)")
    print(f"{'#'*65}\n")

    for mkt in markets:
        for strat in strategies:
            try:
                t0 = datetime.now()
                run_grid(mkt, strat, start_date=args.start, top_n=args.top)
                elapsed = (datetime.now() - t0).seconds
                done += 1
                print(f"\n  ✓ {mkt}/{strat} 완료 ({elapsed}초)  "
                      f"진행: {done}/{total}\n")
            except Exception as e:
                failed.append(f"{mkt}/{strat}")
                print(f"\n  ✗ {mkt}/{strat} 실패: {e}")
                traceback.print_exc()
                print(f"  → 다음 작업으로 계속...\n")

    total_elapsed = (datetime.now() - started_at).seconds // 60
    print(f"\n{'#'*65}")
    print(f"  전체 완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  소요 시간: {total_elapsed}분")
    print(f"  성공: {done}/{total}  실패: {len(failed)}")
    if failed:
        print(f"  실패 목록: {failed}")
    print(f"  결과 위치: {RESULT_DIR}")
    print(f"{'#'*65}\n")
