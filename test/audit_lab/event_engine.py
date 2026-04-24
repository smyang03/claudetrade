"""Standalone event-loop backtest engine.

The engine is intentionally small and isolated. It reads OHLCV frames and
strategy signal functions, then simulates long-only entries without touching
broker, order, Telegram, or live runtime modules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite, sqrt
from statistics import mean, pstdev
from typing import Callable, Iterable

import pandas as pd

from .adapters import available_tickers, load_price_frame, load_strategy, strategy_params
from .config import DISABLED_COMBOS
from .cost_model import CostModel
from .regime_replay import ReplayRegimeClassifier


SignalFunc = Callable[[pd.DataFrame, int, dict], bool]
ProgressFunc = Callable[[str], None]


@dataclass(frozen=True)
class Trade:
    market: str
    ticker: str
    strategy: str
    mode: str
    signal_date: str
    signal_price: float
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    entry_gap_pct: float
    gross_pnl_pct: float
    net_pnl_pct: float
    reason: str
    held_days: int
    cost_bps: float
    entry_timing: str
    entry_day_exit_policy: str


def _date_str(value: object) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def _is_valid_price(value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return isfinite(number) and number > 0


def _pnl_pct(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return round((exit_price / entry_price - 1.0) * 100.0, 6)


def _build_trade(
    *,
    market: str,
    ticker: str,
    strategy: str,
    mode: str,
    signal_row: pd.Series,
    entry_row: pd.Series,
    exit_row: pd.Series,
    entry_price: float,
    exit_price: float,
    reason: str,
    held_days: int,
    cost_model: CostModel,
    entry_timing: str,
    entry_day_exit_policy: str,
) -> Trade:
    signal_price = float(signal_row.get("close", 0) or 0)
    gross = _pnl_pct(entry_price, exit_price)
    return Trade(
        market=market.upper(),
        ticker=ticker,
        strategy=strategy,
        mode=mode,
        signal_date=_date_str(signal_row["date"]),
        signal_price=round(signal_price, 6),
        entry_date=_date_str(entry_row["date"]),
        exit_date=_date_str(exit_row["date"]),
        entry_price=round(float(entry_price), 6),
        exit_price=round(float(exit_price), 6),
        entry_gap_pct=_pnl_pct(signal_price, entry_price) if signal_price > 0 else 0.0,
        gross_pnl_pct=gross,
        net_pnl_pct=cost_model.net_pnl_pct(gross),
        reason=reason,
        held_days=int(held_days),
        cost_bps=round(float(cost_model.round_trip_bps), 3),
        entry_timing=entry_timing,
        entry_day_exit_policy=entry_day_exit_policy,
    )


def run_ticker_backtest(
    df: pd.DataFrame,
    *,
    market: str,
    ticker: str,
    strategy: str,
    params: dict | None = None,
    signal_func: SignalFunc | None = None,
    cost_model: CostModel | None = None,
    regime_classifier: object | None = None,
    start: str = "",
    end: str = "",
    entry_timing: str = "next_open",
    entry_day_exit_policy: str = "allow",
    confidence: float = 0.6,
    respect_disabled_combos: bool = True,
) -> list[dict]:
    """Run a single-ticker long-only simulation and return trade dictionaries."""

    if df is None or df.empty or "date" not in df.columns:
        return []
    if respect_disabled_combos and (strategy, market.upper()) in DISABLED_COMBOS:
        return []

    frame = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    if len(frame) < 3:
        return []

    adapter = None if signal_func else load_strategy(strategy)
    signal = signal_func or adapter.signal
    costs = cost_model or CostModel.from_name(market, "realistic")
    start_ts = pd.to_datetime(start) if start else None
    end_ts = pd.to_datetime(end) if end else None
    trades: list[Trade] = []
    position: dict | None = None

    for i in range(len(frame)):
        row = frame.iloc[i]
        row_date = pd.to_datetime(row["date"])

        if end_ts is not None and row_date > end_ts:
            if position is not None:
                prev_row = frame.iloc[max(i - 1, position["entry_i"])]
                trades.append(
                    _build_trade(
                        market=market,
                        ticker=ticker,
                        strategy=strategy,
                        mode=position["mode"],
                        signal_row=position["signal_row"],
                        entry_row=position["entry_row"],
                        exit_row=prev_row,
                        entry_price=position["entry_price"],
                        exit_price=float(prev_row["close"]),
                        reason="period_end",
                        held_days=max(0, int(i - 1 - position["entry_i"])),
                        cost_model=costs,
                        entry_timing=entry_timing,
                        entry_day_exit_policy=entry_day_exit_policy,
                    )
                )
                position = None
            break

        if position is not None and i >= position["entry_i"]:
            low = float(row.get("low", 0) or 0)
            high = float(row.get("high", 0) or 0)
            close = float(row.get("close", 0) or 0)
            held_days = int(i - position["entry_i"])
            stop_price = position["entry_price"] * (1.0 - position["sl_pct"])
            target_price = position["entry_price"] * (1.0 + position["tp_pct"])
            reason = ""
            exit_price = 0.0

            if not (entry_day_exit_policy == "defer" and held_days == 0):
                # If both stop and target are touched in the same candle, count stop
                # first. This conservative rule avoids optimistic intraday ordering.
                if low > 0 and high > 0 and low <= stop_price and high >= target_price:
                    reason = "stop_loss_same_bar"
                    exit_price = stop_price
                elif low > 0 and low <= stop_price:
                    reason = "stop_loss"
                    exit_price = stop_price
                elif high > 0 and high >= target_price:
                    reason = "take_profit"
                    exit_price = target_price
            if not reason and held_days >= position["max_hold"] and _is_valid_price(close):
                reason = "max_hold"
                exit_price = close

            if reason:
                trades.append(
                    _build_trade(
                        market=market,
                        ticker=ticker,
                        strategy=strategy,
                        mode=position["mode"],
                        signal_row=position["signal_row"],
                        entry_row=position["entry_row"],
                        exit_row=row,
                        entry_price=position["entry_price"],
                        exit_price=exit_price,
                        reason=reason,
                        held_days=held_days,
                        cost_model=costs,
                        entry_timing=entry_timing,
                        entry_day_exit_policy=entry_day_exit_policy,
                    )
                )
                position = None

        if position is not None or i >= len(frame) - 1:
            continue
        if start_ts is not None and row_date < start_ts:
            continue
        if end_ts is not None and row_date > end_ts:
            continue

        mode = "NEUTRAL"
        if regime_classifier is not None and hasattr(regime_classifier, "mode_for"):
            mode = str(regime_classifier.mode_for(row["date"]))

        local_params = dict(params) if params is not None else strategy_params(strategy, mode, market, confidence)
        if local_params.get("disabled") or float(local_params.get("size_mult", 1.0) or 0.0) <= 0.0:
            continue

        try:
            has_signal = bool(signal(frame, i, local_params))
        except Exception as exc:
            raise RuntimeError(f"signal failed: {market.upper()} {ticker} {strategy} index={i}") from exc
        if not has_signal:
            continue

        entry_i = i + 1 if entry_timing == "next_open" else i
        entry_row = frame.iloc[entry_i]
        entry_col = "open" if entry_timing == "next_open" else "close"
        entry_price = float(entry_row.get(entry_col, 0) or 0)
        if not _is_valid_price(entry_price):
            continue

        position = {
            "entry_i": entry_i,
            "signal_row": row,
            "entry_row": entry_row,
            "entry_price": entry_price,
            "mode": mode,
            "tp_pct": float(local_params.get("tp_pct", 0.03) or 0.03),
            "sl_pct": float(local_params.get("sl_pct", 0.02) or 0.02),
            "max_hold": max(1, int(local_params.get("max_hold", 5) or 5)),
        }

    if position is not None:
        exit_row = frame.iloc[-1]
        trades.append(
            _build_trade(
                market=market,
                ticker=ticker,
                strategy=strategy,
                mode=position["mode"],
                signal_row=position["signal_row"],
                entry_row=position["entry_row"],
                exit_row=exit_row,
                entry_price=position["entry_price"],
                exit_price=float(exit_row["close"]),
                reason="data_end",
                held_days=max(0, len(frame) - 1 - position["entry_i"]),
                cost_model=costs,
                entry_timing=entry_timing,
                entry_day_exit_policy=entry_day_exit_policy,
            )
        )

    return [asdict(t) for t in trades]


def calc_stats(trades: Iterable[dict]) -> dict:
    rows = list(trades or [])
    pnl = [float(t.get("net_pnl_pct", 0.0) or 0.0) for t in rows]
    n = len(pnl)
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    loss_streak = 0
    max_loss_streak = 0

    for p in pnl:
        equity *= 1.0 + p / 100.0
        peak = max(peak, equity)
        max_dd = min(max_dd, (equity / peak - 1.0) * 100.0)
        if p < 0:
            loss_streak += 1
            max_loss_streak = max(max_loss_streak, loss_streak)
        else:
            loss_streak = 0

    profit_factor = None
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 4)
    elif gross_profit > 0:
        profit_factor = float("inf")

    trade_std = pstdev(pnl) if n > 1 else 0.0
    return {
        "n_trades": n,
        "win_rate": round(len(wins) / n * 100.0, 3) if n else 0.0,
        "avg_pnl_pct": round(mean(pnl), 6) if n else 0.0,
        "total_pnl_pct": round((equity - 1.0) * 100.0, 6),
        "max_win_pct": round(max(pnl), 6) if n else 0.0,
        "max_loss_pct": round(min(pnl), 6) if n else 0.0,
        "profit_factor": profit_factor,
        "max_drawdown_pct": round(max_dd, 6),
        "trade_sharpe": round(mean(pnl) / trade_std * sqrt(n), 6) if n > 1 and trade_std > 0 else 0.0,
        "max_consecutive_losses": max_loss_streak,
        "stop_loss_rate": round(sum(1 for t in rows if str(t.get("reason", "")).startswith("stop_loss")) / n * 100.0, 3) if n else 0.0,
        "take_profit_rate": round(sum(1 for t in rows if t.get("reason") == "take_profit") / n * 100.0, 3) if n else 0.0,
    }


def run_market_backtest(
    *,
    market: str,
    strategy: str,
    tickers: list[str] | None = None,
    cost_model_name: str = "realistic",
    ticker_limit: int = 0,
    start: str = "",
    end: str = "",
    regime_classifier: object | None = None,
    regime_timing: str = "previous_close",
    entry_timing: str = "next_open",
    entry_day_exit_policy: str = "allow",
    progress: ProgressFunc | None = None,
    progress_interval: int = 10,
) -> dict:
    """Run one strategy over many tickers and return trades plus aggregate stats."""

    selected = tickers if tickers is not None else available_tickers(market, limit=ticker_limit)
    all_trades: list[dict] = []
    total = len(selected)
    interval = max(1, int(progress_interval or 10))
    if progress:
        progress(f"백테스트 시작 | 시장={market.upper()} 전략={strategy} 종목={total}")
    for idx, ticker in enumerate(selected, start=1):
        frame = load_price_frame(market, ticker)
        ticker_regime = regime_classifier or ReplayRegimeClassifier.from_price_frame(frame, timing=regime_timing)
        ticker_trades = run_ticker_backtest(
            frame,
            market=market,
            ticker=ticker,
            strategy=strategy,
            cost_model=CostModel.from_name(market, cost_model_name),
            regime_classifier=ticker_regime,
            start=start,
            end=end,
            entry_timing=entry_timing,
            entry_day_exit_policy=entry_day_exit_policy,
        )
        all_trades.extend(ticker_trades)
        if progress and (idx == 1 or idx % interval == 0 or idx == total):
            progress(
                "백테스트 진행 | "
                f"시장={market.upper()} 전략={strategy} 진행={idx}/{total} "
                f"종목={ticker} 종목거래={len(ticker_trades)} 누적거래={len(all_trades)}"
            )
    stats = calc_stats(all_trades)
    if progress:
        progress(
            "백테스트 완료 | "
            f"시장={market.upper()} 전략={strategy} 거래={stats['n_trades']} "
            f"승률={stats['win_rate']}% 평균수익={stats['avg_pnl_pct']}% PF={stats['profit_factor']}"
        )
    return {"market": market.upper(), "strategy": strategy, "trades": all_trades, "stats": stats}
