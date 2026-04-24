"""Fixed-parameter walk-forward validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from .adapters import available_tickers, load_price_frame
from .cost_model import CostModel
from .event_engine import ProgressFunc, SignalFunc, calc_stats, run_ticker_backtest
from .regime_replay import ReplayRegimeClassifier


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: str
    train_end: str
    test_start: str
    test_end: str


def _date(value: str | pd.Timestamp) -> pd.Timestamp:
    return pd.to_datetime(value).normalize()


def walk_forward_windows(
    *,
    start: str,
    end: str,
    train_years: int = 3,
    test_years: int = 1,
    step_years: int = 1,
) -> list[WalkForwardWindow]:
    start_ts = _date(start)
    end_ts = _date(end)
    windows: list[WalkForwardWindow] = []
    cursor = start_ts
    while True:
        train_end = cursor + pd.DateOffset(years=train_years) - pd.Timedelta(days=1)
        test_start = train_end + pd.Timedelta(days=1)
        test_end = test_start + pd.DateOffset(years=test_years) - pd.Timedelta(days=1)
        if test_start > end_ts:
            break
        if test_end > end_ts:
            test_end = end_ts
        windows.append(
            WalkForwardWindow(
                train_start=cursor.strftime("%Y-%m-%d"),
                train_end=train_end.strftime("%Y-%m-%d"),
                test_start=test_start.strftime("%Y-%m-%d"),
                test_end=test_end.strftime("%Y-%m-%d"),
            )
        )
        cursor = cursor + pd.DateOffset(years=step_years)
    return windows


def _pf_ratio(train_stats: dict, test_stats: dict) -> float | None:
    train_pf = train_stats.get("profit_factor")
    test_pf = test_stats.get("profit_factor")
    if train_pf in (None, 0) or test_pf is None:
        return None
    if train_pf == float("inf"):
        return None
    return round(float(test_pf) / float(train_pf), 4)


def run_walk_forward_on_frame(
    df: pd.DataFrame,
    *,
    market: str,
    ticker: str,
    strategy: str,
    start: str,
    end: str,
    params: dict | None = None,
    signal_func: SignalFunc | None = None,
    cost_model: CostModel | None = None,
    train_years: int = 3,
    test_years: int = 1,
    step_years: int = 1,
    regime_timing: str = "previous_close",
    entry_timing: str = "next_open",
    entry_day_exit_policy: str = "allow",
) -> list[dict]:
    costs = cost_model or CostModel.from_name(market, "realistic")
    regime_classifier = ReplayRegimeClassifier.from_price_frame(df, timing=regime_timing)
    rows: list[dict] = []
    for number, window in enumerate(
        walk_forward_windows(start=start, end=end, train_years=train_years, test_years=test_years, step_years=step_years),
        start=1,
    ):
        train_trades = run_ticker_backtest(
            df,
            market=market,
            ticker=ticker,
            strategy=strategy,
            params=params,
            signal_func=signal_func,
            cost_model=costs,
            regime_classifier=regime_classifier,
            start=window.train_start,
            end=window.train_end,
            entry_timing=entry_timing,
            entry_day_exit_policy=entry_day_exit_policy,
            respect_disabled_combos=False if signal_func else True,
        )
        test_trades = run_ticker_backtest(
            df,
            market=market,
            ticker=ticker,
            strategy=strategy,
            params=params,
            signal_func=signal_func,
            cost_model=costs,
            regime_classifier=regime_classifier,
            start=window.test_start,
            end=window.test_end,
            entry_timing=entry_timing,
            entry_day_exit_policy=entry_day_exit_policy,
            respect_disabled_combos=False if signal_func else True,
        )
        train_stats = calc_stats(train_trades)
        test_stats = calc_stats(test_trades)
        rows.append(
            {
                "window": number,
                **asdict(window),
                "market": market.upper(),
                "ticker": ticker,
                "strategy": strategy,
                "train_stats": train_stats,
                "test_stats": test_stats,
                "pf_ratio_test_to_train": _pf_ratio(train_stats, test_stats),
                "optimized": False,
            }
        )
    return rows


def run_walk_forward(
    *,
    market: str,
    strategy: str,
    start: str,
    end: str,
    tickers: list[str] | None = None,
    ticker_limit: int = 0,
    cost_model_name: str = "realistic",
    regime_timing: str = "previous_close",
    entry_timing: str = "next_open",
    entry_day_exit_policy: str = "allow",
    progress: ProgressFunc | None = None,
    progress_interval: int = 10,
) -> list[dict]:
    selected = tickers if tickers is not None else available_tickers(market, limit=ticker_limit)
    rows: list[dict] = []
    total = len(selected)
    interval = max(1, int(progress_interval or 10))
    if progress:
        progress(f"워크포워드 시작 | 시장={market.upper()} 전략={strategy} 종목={total}")
    for idx, ticker in enumerate(selected, start=1):
        frame = load_price_frame(market, ticker)
        ticker_rows = run_walk_forward_on_frame(
            frame,
            market=market,
            ticker=ticker,
            strategy=strategy,
            start=start,
            end=end,
            cost_model=CostModel.from_name(market, cost_model_name),
            regime_timing=regime_timing,
            entry_timing=entry_timing,
            entry_day_exit_policy=entry_day_exit_policy,
        )
        rows.extend(ticker_rows)
        if progress and (idx == 1 or idx % interval == 0 or idx == total):
            progress(
                "워크포워드 진행 | "
                f"시장={market.upper()} 전략={strategy} 진행={idx}/{total} "
                f"종목={ticker} 윈도우={len(ticker_rows)} 누적윈도우={len(rows)}"
            )
    if progress:
        progress(f"워크포워드 완료 | 시장={market.upper()} 전략={strategy} 윈도우={len(rows)}")
    return rows
