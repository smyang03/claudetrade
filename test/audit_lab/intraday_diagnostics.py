"""Diagnostics for zero-trade intraday entry simulations."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from .adapters import load_strategy, strategy_params
from .config import MARKET_DATA_DB, RESULT_DIR
from .event_engine import calc_stats
from .intraday_entry_models import diagnose_intraday_entry
from .market_data_adapter import load_collected_intraday_frame, load_collected_price_frame
from .regime_replay import ReplayRegimeClassifier
from .reports import write_csv_report, write_json_report, write_markdown_report


ProgressFunc = Callable[[str], None]


def _date_str(value: object) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def run_intraday_entry_diagnostics(
    *,
    market: str,
    strategy: str,
    tickers: list[str],
    intraday_entry_model: str,
    timeframe: str = "5m",
    start: str = "",
    end: str = "",
    db_path: Path = MARKET_DATA_DB,
    opening_minutes: int = 30,
    deadline_minutes: int = 180,
    max_gap_pct: float = 1.5,
    confidence: float = 0.6,
    progress: ProgressFunc | None = None,
    progress_interval: int = 10,
) -> dict:
    adapter = load_strategy(strategy)
    rows: list[dict] = []
    errors: list[dict] = []
    reason_counter: Counter[str] = Counter()
    signal_count = 0
    entry_count = 0
    no_signal_tickers = 0
    start_ts = pd.to_datetime(start) if start else None
    end_ts = pd.to_datetime(end) if end else None
    interval = max(1, int(progress_interval or 10))

    if progress:
        progress(
            f"장중 진입 진단 시작 | 시장={market.upper()} 전략={strategy} "
            f"모델={intraday_entry_model} 종목={len(tickers)}"
        )

    for idx, ticker in enumerate(tickers, start=1):
        try:
            daily = load_collected_price_frame(market, ticker, db_path=Path(db_path))
            intraday = load_collected_intraday_frame(market, ticker, timeframe=timeframe, db_path=Path(db_path))
            if daily.empty:
                errors.append({"ticker": ticker, "reason": "NO_DAILY_DATA"})
                reason_counter["NO_DAILY_DATA"] += 1
                continue
            if intraday.empty:
                errors.append({"ticker": ticker, "reason": "NO_INTRADAY_DATA"})
                reason_counter["NO_INTRADAY_DATA"] += 1
                continue
            frame = daily.sort_values("date").drop_duplicates("date").reset_index(drop=True)
            regime = ReplayRegimeClassifier.from_price_frame(frame)
            ticker_signals = 0
            for i in range(len(frame) - 1):
                row = frame.iloc[i]
                row_date = pd.to_datetime(row["date"])
                if start_ts is not None and row_date < start_ts:
                    continue
                if end_ts is not None and row_date > end_ts:
                    continue
                mode = str(regime.mode_for(row["date"]))
                params = strategy_params(strategy, mode, market, confidence)
                if params.get("disabled") or float(params.get("size_mult", 1.0) or 0.0) <= 0.0:
                    continue
                if not bool(adapter.signal(frame, i, params)):
                    continue
                ticker_signals += 1
                signal_count += 1
                entry_i = i + 1
                entry_row = frame.iloc[entry_i]
                sl_pct = float(params.get("sl_pct", 0.02) or 0.02)
                diag = diagnose_intraday_entry(
                    intraday,
                    model=intraday_entry_model,
                    entry_date=entry_row["date"],
                    signal_close=float(row.get("close", 0) or 0),
                    stop_loss_pct=sl_pct,
                    opening_minutes=opening_minutes,
                    deadline_minutes=deadline_minutes,
                    max_gap_pct=max_gap_pct,
                )
                reason = str(diag.get("reason") or "UNKNOWN")
                reason_counter[reason] += 1
                entry_count += int(diag.get("entry_created", 0) or 0)
                rows.append(
                    {
                        "market": market.upper(),
                        "ticker": ticker,
                        "strategy": strategy,
                        "intraday_entry_model": intraday_entry_model,
                        "timeframe": timeframe,
                        "signal_date": _date_str(row["date"]),
                        "entry_date": _date_str(entry_row["date"]),
                        "mode": mode,
                        "signal_close": round(float(row.get("close", 0) or 0), 6),
                        "sl_pct": sl_pct,
                        "status": diag.get("status", ""),
                        "reason": reason,
                        "entry_created": int(diag.get("entry_created", 0) or 0),
                        "gap_pct": diag.get("gap_pct", ""),
                        "minutes_from_open": diag.get("minutes_from_open", ""),
                        "entry_price": diag.get("entry_price", ""),
                        "entry_timestamp": diag.get("entry_timestamp", ""),
                        "deadline_minutes": diag.get("deadline_minutes", deadline_minutes),
                    }
                )
            if ticker_signals == 0:
                no_signal_tickers += 1
            if progress and (idx == 1 or idx % interval == 0 or idx == len(tickers)):
                progress(
                    f"장중 진입 진단 진행 | {idx}/{len(tickers)} 종목={ticker} "
                    f"신호={ticker_signals} 누적신호={signal_count} 누적진입={entry_count}"
                )
        except Exception as exc:
            errors.append({"ticker": ticker, "reason": "SIMULATION_EXCEPTION", "error": repr(exc)})
            reason_counter["SIMULATION_EXCEPTION"] += 1

    reason_rows = [
        {"reason": reason, "count": count, "pct_of_signals": round(count / max(signal_count, 1) * 100.0, 3)}
        for reason, count in reason_counter.most_common()
    ]
    if no_signal_tickers:
        reason_rows.append(
            {
                "reason": "NO_SIGNAL_TICKER",
                "count": no_signal_tickers,
                "pct_of_signals": "",
                "pct_of_tickers": round(no_signal_tickers / max(len(tickers), 1) * 100.0, 3),
            }
        )
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "phase": "intraday_entry_diagnostics",
        "market": market.upper(),
        "strategy": strategy,
        "intraday_entry_model": intraday_entry_model,
        "timeframe": timeframe,
        "ticker_count": len(tickers),
        "signal_count": signal_count,
        "entry_count": entry_count,
        "entry_rate_pct": round(entry_count / max(signal_count, 1) * 100.0, 3),
        "no_signal_tickers": no_signal_tickers,
        "summary_rows": reason_rows,
        "diagnostic_rows": rows,
        "error_rows": errors,
        "trade_stats_placeholder": calc_stats([]),
    }
    if progress:
        progress(
            f"장중 진입 진단 완료 | 신호={signal_count} 진입={entry_count} "
            f"진입률={payload['entry_rate_pct']}% 에러={len(errors)}"
        )
    return payload


def write_intraday_diagnostic_bundle(payload: dict, output_dir: Path = RESULT_DIR) -> dict:
    output_dir = Path(output_dir)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"intraday_diagnostics_{stamp}"
    return {
        "json": str(write_json_report(payload, output_dir, base)),
        "summary_csv": str(write_csv_report(payload.get("summary_rows", []), output_dir, base.replace("diagnostics", "diagnostic_summary"))),
        "details_csv": str(write_csv_report(payload.get("diagnostic_rows", []), output_dir, base.replace("diagnostics", "diagnostic_details"))),
        "errors_csv": str(write_csv_report(payload.get("error_rows", []), output_dir, base.replace("diagnostics", "diagnostic_errors"))),
        "markdown": str(write_markdown_report({"summary_rows": payload.get("summary_rows", [])}, output_dir, base)),
    }
