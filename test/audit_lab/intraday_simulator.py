"""Daily-signal backtest with intraday entry confirmation."""

from __future__ import annotations

from math import isfinite
from statistics import mean
from typing import Callable

import pandas as pd

from .adapters import load_strategy, strategy_params
from .cost_model import CostModel
from .event_engine import ProgressFunc, SignalFunc, calc_stats
from .intraday_entry_models import find_intraday_entry
from .market_data_adapter import available_collected_tickers, load_collected_intraday_frame, load_collected_price_frame
from .regime_replay import ReplayRegimeClassifier


FrameLoader = Callable[[str, str], pd.DataFrame]


def _date_str(value: object) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def _timestamp_str(value: object) -> str:
    return pd.to_datetime(value).isoformat()


def _valid_price(value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return isfinite(number) and number > 0


def _pnl_pct(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return round((exit_price / entry_price - 1.0) * 100.0, 6)


def _intraday_exit_after_entry(
    intraday: pd.DataFrame,
    *,
    entry_timestamp: object,
    stop_price: float,
    target_price: float,
) -> tuple[str, float, str] | None:
    if intraday is None or intraday.empty or "date" not in intraday.columns:
        return None
    frame = intraday.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    entry_ts = pd.to_datetime(entry_timestamp)
    session = frame[
        (frame["date"].dt.date == entry_ts.date())
        & (frame["date"] >= entry_ts)
    ].sort_values("date")
    for _, row in session.iterrows():
        low = float(row.get("low", 0) or 0)
        high = float(row.get("high", 0) or 0)
        if low > 0 and high > 0 and low <= stop_price and high >= target_price:
            return _timestamp_str(row["date"]), stop_price, "stop_loss_same_bar"
        if low > 0 and low <= stop_price:
            return _timestamp_str(row["date"]), stop_price, "stop_loss"
        if high > 0 and high >= target_price:
            return _timestamp_str(row["date"]), target_price, "take_profit"
    return None


def _trade_dict(
    *,
    market: str,
    ticker: str,
    strategy: str,
    mode: str,
    signal_row: pd.Series,
    entry_i: int,
    entry_date: object,
    entry_timestamp: str,
    entry_price: float,
    exit_date: object,
    exit_timestamp: str,
    exit_price: float,
    reason: str,
    held_days: int,
    cost_model: CostModel,
    intraday_entry_model: str,
    entry_reason: str,
) -> dict:
    signal_price = float(signal_row.get("close", 0) or 0)
    gross = _pnl_pct(entry_price, exit_price)
    return {
        "market": market.upper(),
        "ticker": ticker,
        "strategy": strategy,
        "mode": mode,
        "signal_date": _date_str(signal_row["date"]),
        "signal_price": round(signal_price, 6),
        "entry_date": _date_str(entry_date),
        "entry_timestamp": entry_timestamp,
        "entry_price": round(float(entry_price), 6),
        "exit_date": _date_str(exit_date),
        "exit_timestamp": exit_timestamp,
        "exit_price": round(float(exit_price), 6),
        "entry_gap_pct": _pnl_pct(signal_price, entry_price) if signal_price > 0 else 0.0,
        "gross_pnl_pct": gross,
        "net_pnl_pct": cost_model.net_pnl_pct(gross),
        "reason": reason,
        "held_days": int(held_days),
        "cost_bps": round(float(cost_model.round_trip_bps), 3),
        "entry_timing": "intraday",
        "entry_model": intraday_entry_model,
        "intraday_entry_model": intraday_entry_model,
        "intraday_entry_reason": entry_reason,
        "entry_day_sl_breach": 0,
        "entry_i": int(entry_i),
    }


def run_ticker_intraday_entry_backtest(
    daily_df: pd.DataFrame,
    intraday_df: pd.DataFrame,
    *,
    market: str,
    ticker: str,
    strategy: str,
    intraday_entry_model: str = "opening_range_reclaim",
    params: dict | None = None,
    signal_func: SignalFunc | None = None,
    cost_model: CostModel | None = None,
    regime_classifier: object | None = None,
    start: str = "",
    end: str = "",
    opening_minutes: int = 30,
    deadline_minutes: int = 180,
    max_gap_pct: float = 1.5,
    confidence: float = 0.6,
) -> list[dict]:
    if daily_df is None or daily_df.empty or intraday_df is None or intraday_df.empty:
        return []
    frame = daily_df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    if len(frame) < 3:
        return []

    adapter = None if signal_func else load_strategy(strategy)
    signal = signal_func or adapter.signal
    costs = cost_model or CostModel.from_name(market, "realistic")
    regime = regime_classifier or ReplayRegimeClassifier.from_price_frame(frame)
    start_ts = pd.to_datetime(start) if start else None
    end_ts = pd.to_datetime(end) if end else None
    trades: list[dict] = []
    position: dict | None = None

    for i in range(len(frame)):
        row = frame.iloc[i]
        row_date = pd.to_datetime(row["date"])

        if end_ts is not None and row_date > end_ts:
            if position is not None:
                prev = frame.iloc[max(i - 1, position["entry_i"])]
                trades.append(
                    _trade_dict(
                        market=market,
                        ticker=ticker,
                        strategy=strategy,
                        mode=position["mode"],
                        signal_row=position["signal_row"],
                        entry_i=position["entry_i"],
                        entry_date=position["entry_date"],
                        entry_timestamp=position["entry_timestamp"],
                        entry_price=position["entry_price"],
                        exit_date=prev["date"],
                        exit_timestamp=_timestamp_str(prev["date"]),
                        exit_price=float(prev["close"]),
                        reason="period_end",
                        held_days=max(0, int(i - 1 - position["entry_i"])),
                        cost_model=costs,
                        intraday_entry_model=intraday_entry_model,
                        entry_reason=position["entry_reason"],
                    )
                )
                position = None
            break

        if position is not None and i >= position["entry_i"]:
            held_days = int(i - position["entry_i"])
            stop_price = position["entry_price"] * (1.0 - position["sl_pct"])
            target_price = position["entry_price"] * (1.0 + position["tp_pct"])

            if i == position["entry_i"]:
                intraday_exit = _intraday_exit_after_entry(
                    intraday_df,
                    entry_timestamp=position["entry_timestamp"],
                    stop_price=stop_price,
                    target_price=target_price,
                )
                if intraday_exit is not None:
                    exit_timestamp, exit_price, reason = intraday_exit
                    trades.append(
                        _trade_dict(
                            market=market,
                            ticker=ticker,
                            strategy=strategy,
                            mode=position["mode"],
                            signal_row=position["signal_row"],
                            entry_i=position["entry_i"],
                            entry_date=position["entry_date"],
                            entry_timestamp=position["entry_timestamp"],
                            entry_price=position["entry_price"],
                            exit_date=row["date"],
                            exit_timestamp=exit_timestamp,
                            exit_price=exit_price,
                            reason=reason,
                            held_days=held_days,
                            cost_model=costs,
                            intraday_entry_model=intraday_entry_model,
                            entry_reason=position["entry_reason"],
                        )
                    )
                    position = None
                    continue

            low = float(row.get("low", 0) or 0)
            high = float(row.get("high", 0) or 0)
            close = float(row.get("close", 0) or 0)
            reason = ""
            exit_price = 0.0
            if low > 0 and high > 0 and low <= stop_price and high >= target_price:
                reason = "stop_loss_same_bar"
                exit_price = stop_price
            elif low > 0 and low <= stop_price:
                reason = "stop_loss"
                exit_price = stop_price
            elif high > 0 and high >= target_price:
                reason = "take_profit"
                exit_price = target_price
            elif held_days >= position["max_hold"] and _valid_price(close):
                reason = "max_hold"
                exit_price = close

            if reason:
                trades.append(
                    _trade_dict(
                        market=market,
                        ticker=ticker,
                        strategy=strategy,
                        mode=position["mode"],
                        signal_row=position["signal_row"],
                        entry_i=position["entry_i"],
                        entry_date=position["entry_date"],
                        entry_timestamp=position["entry_timestamp"],
                        entry_price=position["entry_price"],
                        exit_date=row["date"],
                        exit_timestamp=_timestamp_str(row["date"]),
                        exit_price=exit_price,
                        reason=reason,
                        held_days=held_days,
                        cost_model=costs,
                        intraday_entry_model=intraday_entry_model,
                        entry_reason=position["entry_reason"],
                    )
                )
                position = None

        if position is not None or i >= len(frame) - 2:
            continue
        if start_ts is not None and row_date < start_ts:
            continue
        if end_ts is not None and row_date > end_ts:
            continue

        mode = "NEUTRAL"
        if hasattr(regime, "mode_for"):
            mode = str(regime.mode_for(row["date"]))
        local_params = dict(params) if params is not None else strategy_params(strategy, mode, market, confidence)
        if local_params.get("disabled") or float(local_params.get("size_mult", 1.0) or 0.0) <= 0.0:
            continue

        if not bool(signal(frame, i, local_params)):
            continue

        entry_i = i + 1
        entry_row = frame.iloc[entry_i]
        sl_pct = float(local_params.get("sl_pct", 0.02) or 0.02)
        entry = find_intraday_entry(
            intraday_df,
            model=intraday_entry_model,
            entry_date=entry_row["date"],
            signal_close=float(row.get("close", 0) or 0),
            stop_loss_pct=sl_pct,
            opening_minutes=opening_minutes,
            deadline_minutes=deadline_minutes,
            max_gap_pct=max_gap_pct,
        )
        if entry is None:
            continue

        position = {
            "entry_i": entry_i,
            "signal_row": row,
            "entry_date": entry_row["date"],
            "entry_timestamp": entry.entry_timestamp,
            "entry_price": float(entry.entry_price),
            "entry_reason": entry.reason,
            "mode": mode,
            "tp_pct": float(local_params.get("tp_pct", 0.03) or 0.03),
            "sl_pct": sl_pct,
            "max_hold": max(1, int(local_params.get("max_hold", 5) or 5)),
        }

    if position is not None:
        exit_row = frame.iloc[-1]
        trades.append(
            _trade_dict(
                market=market,
                ticker=ticker,
                strategy=strategy,
                mode=position["mode"],
                signal_row=position["signal_row"],
                entry_i=position["entry_i"],
                entry_date=position["entry_date"],
                entry_timestamp=position["entry_timestamp"],
                entry_price=position["entry_price"],
                exit_date=exit_row["date"],
                exit_timestamp=_timestamp_str(exit_row["date"]),
                exit_price=float(exit_row["close"]),
                reason="data_end",
                held_days=max(0, len(frame) - 1 - position["entry_i"]),
                cost_model=costs,
                intraday_entry_model=intraday_entry_model,
                entry_reason=position["entry_reason"],
            )
        )
    return trades


def run_market_intraday_entry_backtest(
    *,
    market: str,
    strategy: str,
    tickers: list[str] | None = None,
    intraday_entry_model: str = "opening_range_reclaim",
    timeframe: str = "5m",
    cost_model_name: str = "realistic",
    ticker_limit: int = 0,
    start: str = "",
    end: str = "",
    daily_loader: FrameLoader | None = None,
    intraday_loader: FrameLoader | None = None,
    progress: ProgressFunc | None = None,
    progress_interval: int = 10,
) -> dict:
    selected = tickers if tickers is not None else available_collected_tickers(market, timeframe=timeframe, limit=ticker_limit)
    daily_load = daily_loader or load_collected_price_frame
    intraday_load = intraday_loader or (lambda m, t: load_collected_intraday_frame(m, t, timeframe=timeframe))
    all_trades: list[dict] = []
    error_rows: list[dict] = []
    total = len(selected)
    interval = max(1, int(progress_interval or 10))
    if progress:
        progress(f"장중 진입 백테스트 시작 | 시장={market.upper()} 전략={strategy} 모델={intraday_entry_model} 종목={total}")
    for idx, ticker in enumerate(selected, start=1):
        try:
            daily = daily_load(market, ticker)
            intraday = intraday_load(market, ticker)
            if daily.empty:
                error_rows.append({"ticker": ticker, "reason": "NO_DAILY_DATA"})
            if intraday.empty:
                error_rows.append({"ticker": ticker, "reason": "NO_INTRADAY_DATA"})
            ticker_trades = run_ticker_intraday_entry_backtest(
                daily,
                intraday,
                market=market,
                ticker=ticker,
                strategy=strategy,
                intraday_entry_model=intraday_entry_model,
                cost_model=CostModel.from_name(market, cost_model_name),
                start=start,
                end=end,
            )
        except Exception as exc:
            ticker_trades = []
            error_rows.append({"ticker": ticker, "reason": "SIMULATION_EXCEPTION", "error": repr(exc)})
        all_trades.extend(ticker_trades)
        if progress and (idx == 1 or idx % interval == 0 or idx == total):
            progress(
                "장중 진입 백테스트 진행 | "
                f"시장={market.upper()} 전략={strategy} 진행={idx}/{total} "
                f"종목={ticker} 종목거래={len(ticker_trades)} 누적거래={len(all_trades)}"
            )
    stats = calc_stats(all_trades)
    if progress:
        progress(
            "장중 진입 백테스트 완료 | "
            f"시장={market.upper()} 전략={strategy} 거래={stats['n_trades']} "
            f"승률={stats['win_rate']}% 평균수익={stats['avg_pnl_pct']}% PF={stats['profit_factor']} 에러={len(error_rows)}"
        )
    return {
        "market": market.upper(),
        "strategy": strategy,
        "entry_model": intraday_entry_model,
        "timeframe": timeframe,
        "trades": all_trades,
        "stats": stats,
        "error_rows": error_rows,
    }
