"""
phase1_trainer/backtester.py — 전략 백테스트 프레임워크

설계 원칙:
  - 실제 strategy/ 신호 함수 재사용 (trading_bot과 동일 로직)
  - 데이터 부족 시 graceful 스킵 (MIN_ROWS 미만 → 해당 종목/기간 제외)
  - 결과 JSON 저장 → brain 학습 재료로 활용 가능

실행:
  python phase1_trainer/backtester.py [--market KR|US] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
"""

from __future__ import annotations

import sys
import json
import argparse
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from logger import get_trainer_logger
from phase1_trainer.digest_builder import KR_TICKERS, US_TICKERS, PRICE_DIR
from indicators import calc_all

log = get_trainer_logger()

# 최소 데이터 행 수 — 미달이면 해당 티커 스킵 (graceful 처리)
MIN_ROWS = 60


def _load_price(market: str, ticker: str) -> pd.DataFrame:
    """가격 파일 로드 + calc_all() 지표 계산 (strategy 신호 함수와 동일 컬럼)

    중요: calc_all()은 전체 raw 데이터 기준으로 먼저 실행한다.
    이후 backtest_ticker()에서 날짜 슬라이스를 수행하므로
    최근 3개월처럼 짧은 구간을 요청해도 ma60 등 장기 지표가 정상 계산된다.
    """
    raw_path = PRICE_DIR / market.lower() / f"{market.lower()}_{ticker}.csv"
    if not raw_path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(raw_path, parse_dates=["date"])
        df.columns = [c.lower() for c in df.columns]
        df = df.sort_values("date").reset_index(drop=True)
        # calc_all은 날짜 슬라이스 전에 전체 데이터로 실행 (지표 warm-up 확보)
        df = calc_all(df)
        return df
    except Exception as e:
        log.debug(f"[{ticker}] 가격 로드 오류: {e}")
        return pd.DataFrame()

# 전략 임포트 — 없으면 해당 전략 스킵
_STRATEGIES: dict = {}
try:
    from strategy.volatility_breakout import signal as vb_sig, params as vb_params
    _STRATEGIES["volatility_breakout"] = (vb_sig, vb_params)
except ImportError:
    pass
try:
    from strategy.momentum import signal as mom_sig, params as mom_params
    _STRATEGIES["momentum"] = (mom_sig, mom_params)
except ImportError:
    pass
try:
    from strategy.mean_reversion import signal as mr_sig, params as mr_params
    _STRATEGIES["mean_reversion"] = (mr_sig, mr_params)
except ImportError:
    pass
try:
    from strategy.gap_pullback import signal as gap_sig, params as gap_params
    _STRATEGIES["gap_pullback"] = (gap_sig, gap_params)
except ImportError:
    pass

# 결과 저장 경로
RESULT_DIR = Path(__file__).parent.parent / "data" / "backtest"
RESULT_DIR.mkdir(parents=True, exist_ok=True)


# ── 단일 종목 단일 전략 백테스트 ──────────────────────────────────────────────

def backtest_ticker(
    df: pd.DataFrame,
    ticker: str,
    strategy: str,
    market: str = "US",
    mode: str = "MODERATE_BULL",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    tp_pct: float = 0.06,
    sl_pct: float = 0.03,
    max_hold: int = 5,
    params_override: Optional[dict] = None,
) -> dict:
    """
    단일 종목/전략 백테스트.
    params_override: 그리드 서치 등에서 파라미터를 직접 주입할 때 사용.
                     지정 시 params_fn 호출을 건너뛰고 해당 파라미터로 실행.
    Returns: {trades, stats} 딕셔너리.
    데이터 부족 시 빈 결과 반환 (exception 아님).
    """
    if strategy not in _STRATEGIES:
        return {"ticker": ticker, "strategy": strategy, "skip_reason": "strategy_unavailable", "trades": [], "stats": {}}

    sig_fn, params_fn = _STRATEGIES[strategy]
    if params_override is not None:
        params = params_override
    else:
        try:
            params = params_fn(mode, market=market)
        except TypeError:
            params = params_fn(mode)

    # params dict 값이 있으면 함수 인자보다 우선 적용
    tp_pct   = float(params.get("tp_pct",   tp_pct))
    sl_pct   = float(params.get("sl_pct",   sl_pct))
    max_hold = int(params.get("max_hold", max_hold))

    # 날짜 슬라이스 — calc_all() 이후에 수행하므로 지표는 정상 계산된 상태
    work = df.copy()
    if start_date:
        work = work[work["date"] >= pd.Timestamp(start_date)]
    if end_date:
        work = work[work["date"] <= pd.Timestamp(end_date)]
    work = work.reset_index(drop=True)

    # 최소 데이터 체크: 슬라이스 후 거래 구간에 최소 20행 필요
    # (calc_all warm-up은 슬라이스 전 전체 데이터에서 이미 완료됨)
    MIN_TRADE_ROWS = 20
    if len(work) < MIN_TRADE_ROWS:
        log.debug(f"  [{ticker}/{strategy}] 거래 구간 부족 {len(work)}행 < {MIN_TRADE_ROWS} → 스킵")
        return {
            "ticker": ticker, "strategy": strategy,
            "skip_reason": f"insufficient_trade_rows ({len(work)} rows < {MIN_TRADE_ROWS})",
            "trades": [], "stats": {},
        }

    trades = []
    position = None  # {entry_price, entry_date, tp, sl, qty=1}

    for i in range(30, len(work)):
        row = work.iloc[i]
        price = float(row.get("close", 0))
        if price <= 0:
            continue

        # ── 포지션 관리 (보유 중) ──────────────────────────────────────────
        if position is not None:
            held = i - position["entry_idx"]
            exit_price = None
            exit_reason = None

            if price >= position["tp"]:
                exit_price = position["tp"]
                exit_reason = "tp"
            elif price <= position["sl"]:
                exit_price = position["sl"]
                exit_reason = "sl"
            elif held >= max_hold:
                exit_price = price
                exit_reason = "max_hold"

            if exit_price:
                pnl_pct = (exit_price - position["entry"]) / position["entry"] * 100
                trades.append({
                    "entry_date":  position["entry_date"],
                    "exit_date":   row["date"].strftime("%Y-%m-%d"),
                    "entry_price": round(position["entry"], 4),
                    "exit_price":  round(exit_price, 4),
                    "pnl_pct":     round(pnl_pct, 3),
                    "reason":      exit_reason,
                    "held_days":   held,
                })
                position = None

        # ── 신규 진입 신호 체크 ────────────────────────────────────────────
        if position is None:
            try:
                fired = sig_fn(work, i, params)
            except Exception:
                fired = False

            if fired:
                position = {
                    "entry":      price,
                    "entry_date": row["date"].strftime("%Y-%m-%d"),
                    "entry_idx":  i,
                    "tp":         price * (1 + tp_pct),
                    "sl":         price * (1 - sl_pct),
                }

    # 미청산 포지션 마지막 가격으로 강제 청산
    if position is not None and len(work) > 0:
        last = work.iloc[-1]
        ep = float(last.get("close", position["entry"]))
        pnl_pct = (ep - position["entry"]) / position["entry"] * 100
        trades.append({
            "entry_date":  position["entry_date"],
            "exit_date":   last["date"].strftime("%Y-%m-%d"),
            "entry_price": round(position["entry"], 4),
            "exit_price":  round(ep, 4),
            "pnl_pct":     round(pnl_pct, 3),
            "reason":      "end_of_data",
            "held_days":   len(work) - 1 - position["entry_idx"],
        })

    stats = _calc_stats(trades)
    return {"ticker": ticker, "strategy": strategy, "mode": mode, "trades": trades, "stats": stats}


def _calc_stats(trades: list) -> dict:
    """거래 목록 → 성과 통계"""
    if not trades:
        return {
            "n_trades": 0, "win_rate": 0.0, "avg_pnl": 0.0,
            "max_win": 0.0, "max_loss": 0.0, "total_pnl": 0.0,
            "profit_factor": 0.0, "max_drawdown": 0.0, "sharpe": 0.0,
        }

    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_pnl = sum(pnls)
    win_rate = len(wins) / len(pnls) * 100 if pnls else 0

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max Drawdown (누적 수익 기준)
    equity = np.cumsum([0.0] + pnls)
    peak = np.maximum.accumulate(equity)
    drawdown = equity - peak
    max_dd = float(drawdown.min())

    # Sharpe (단순화 — 일별 수익의 평균/표준편차)
    sharpe = 0.0
    if len(pnls) > 1:
        arr = np.array(pnls)
        std = arr.std()
        if std > 0:
            sharpe = round(float(arr.mean() / std * np.sqrt(252 / max(1, len(pnls)))), 3)

    return {
        "n_trades":      len(pnls),
        "win_rate":      round(win_rate, 1),
        "avg_pnl":       round(total_pnl / len(pnls), 3),
        "max_win":       round(max(pnls), 3),
        "max_loss":      round(min(pnls), 3),
        "total_pnl":     round(total_pnl, 3),
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else None,
        "max_drawdown":  round(max_dd, 3),
        "sharpe":        sharpe,
    }


# ── 전체 마켓 백테스트 ─────────────────────────────────────────────────────────

def run_backtest(
    market: str = "US",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    mode: str = "MODERATE_BULL",
    strategies: Optional[list] = None,
) -> dict:
    """
    지정 마켓 전 종목 × 전 전략 백테스트 실행.
    데이터 없는 종목은 자동 스킵.
    """
    ticker_map = KR_TICKERS if market == "KR" else US_TICKERS
    strats = strategies or list(_STRATEGIES.keys())
    if not strats:
        log.error("사용 가능한 전략 없음 — strategy/ 임포트 오류 확인")
        return {}

    log.info(f"[백테스트] {market} | {start_date}~{end_date} | mode={mode} | 전략={strats}")

    results: dict = {
        "market": market, "mode": mode,
        "start_date": start_date, "end_date": end_date,
        "run_at": datetime.now().isoformat(),
        "by_ticker": {}, "by_strategy": {}, "summary": [],
    }

    # 추가 티커 (price 파일 있으면 포함)
    price_dir = PRICE_DIR / market.lower()
    all_tickers = dict(ticker_map)
    for f in price_dir.glob(f"{market.lower()}_*.csv"):
        tk = f.stem.replace(f"{market.lower()}_", "")
        if tk not in all_tickers:
            all_tickers[tk] = tk  # 이름 없으면 코드 그대로

    for ticker in sorted(all_tickers):
        df = _load_price(market, ticker)
        if df.empty:
            log.debug(f"  [{ticker}] 가격 파일 없음 → 스킵")
            continue

        results["by_ticker"][ticker] = {}
        for strat in strats:
            r = backtest_ticker(
                df, ticker, strat, market=market, mode=mode,
                start_date=start_date, end_date=end_date,
            )
            results["by_ticker"][ticker][strat] = r
            # strategy 집계
            if strat not in results["by_strategy"]:
                results["by_strategy"][strat] = []
            if r.get("trades"):
                results["by_strategy"][strat].extend(r["trades"])

    # 전략별 종합 통계
    for strat, all_trades in results["by_strategy"].items():
        stats = _calc_stats(all_trades)
        results["summary"].append({
            "strategy": strat, "n_tickers": len(results["by_ticker"]),
            **stats,
        })
        log.info(
            f"  [{strat}] 거래{stats['n_trades']}건 "
            f"승률{stats['win_rate']:.1f}% "
            f"평균PnL{stats['avg_pnl']:+.2f}% "
            f"Sharpe{stats['sharpe']:.2f} "
            f"MaxDD{stats['max_drawdown']:.2f}%"
        )

    # 결과 저장
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULT_DIR / f"backtest_{market}_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log.info(f"  ✅ 백테스트 저장: {out_path}")

    return results


def print_summary(results: dict):
    """콘솔에 요약 테이블 출력"""
    market = results.get("market", "?")
    print(f"\n{'='*70}")
    print(f"백테스트 결과 [{market}] {results.get('start_date','?')} ~ {results.get('end_date','?')}")
    print(f"{'='*70}")
    print(f"{'전략':<22} {'거래':>6} {'승률':>7} {'평균PnL':>9} {'Sharpe':>8} {'MaxDD':>8}")
    print(f"{'-'*70}")
    for s in sorted(results.get("summary", []), key=lambda x: x.get("avg_pnl", 0), reverse=True):
        pf = s.get("profit_factor")
        pf_str = f"{pf:.2f}" if pf else "∞"
        print(
            f"  {s['strategy']:<20} {s['n_trades']:>6} "
            f"{s['win_rate']:>6.1f}% {s['avg_pnl']:>+8.2f}% "
            f"{s['sharpe']:>8.3f} {s['max_drawdown']:>7.2f}%"
        )
    print(f"{'='*70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ClaudeTrade 백테스트")
    parser.add_argument("--market",  default="US",  choices=["KR", "US"])
    parser.add_argument("--start",   default=None,  help="시작 날짜 YYYY-MM-DD (없으면 전체)")
    parser.add_argument("--end",     default=None,  help="종료 날짜 YYYY-MM-DD")
    parser.add_argument("--mode",    default="MODERATE_BULL")
    parser.add_argument("--strat",   default=None,  nargs="+", help="특정 전략만 테스트")
    args = parser.parse_args()

    results = run_backtest(
        market=args.market,
        start_date=args.start,
        end_date=args.end,
        mode=args.mode,
        strategies=args.strat,
    )
    if results:
        print_summary(results)
