#!/usr/bin/env python3
from __future__ import annotations

"""Reproducible strategy discovery on the actual KR/US live candidate stream.

The lab is deliberately isolated from live routing and order code.  A candidate
observed during session D may use only information known by D close, enters at
the next tradable session open, and exits after a fixed number of sessions.
Results include costs, USD/KRW FX, whole-share affordability, time splits,
moving-block uncertainty, and top-contributor removal.
"""

import argparse
from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Callable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SELECTION_DB = ROOT / "data" / "ticker_selection_log.db"
PRICE_ROOT = ROOT / "data" / "price"

COST_PCT = {"US": 0.70, "KR": 0.21}
ORDER_BUDGET_KRW = {"US": 200_000.0, "KR": 500_000.0}
BENCHMARK = {"US": "SPY", "KR": "069500"}

# Known leveraged/inverse US products observed in the system or common in the
# screened universe.  They are excluded from core stock arms and tested only in
# a dedicated arm.  The exact list is written to the output contract.
US_LEVERAGED_OR_INVERSE = {
    "BOIL", "CONL", "DPST", "DRV", "ERY", "FAS", "FAZ", "FNGD", "FNGU",
    "GUSH", "JDST", "JNUG", "LABD", "LABU", "MSTU", "NVDL", "NVDU", "NVDQ",
    "QLD", "SDS", "SOXL", "SOXS", "SPXL", "SPXS", "SQQQ", "TECL", "TECS",
    "TMF", "TMV", "TNA", "TQQQ", "TSLL", "TZA", "UDOW", "UPRO", "UVXY",
    "YINN", "YANG",
}
LEVERAGE_WORDS = ("leveraged", "inverse", "2x", "3x", "레버리지", "인버스", "곱버스")


@dataclass(frozen=True)
class StrategySpec:
    name: str
    market: str
    hold_sessions: int
    score_column: str
    score_descending: bool
    thesis: str
    predicate_name: str
    outcome_key: str = "fixed"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def load_price_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"date", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        raise ValueError(f"price schema mismatch: {path}")
    frame = frame[list(required)].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "open", "high", "low", "close", "volume"])
    frame = frame.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    valid = (
        frame["open"].gt(0)
        & frame["high"].ge(frame[["open", "close"]].max(axis=1))
        & frame["low"].le(frame[["open", "close"]].min(axis=1))
        & frame["low"].gt(0)
        & frame["volume"].ge(0)
    )
    frame = frame[valid].copy().reset_index(drop=True)
    previous_close = frame["close"].shift(1)
    frame["ret1_pct"] = (frame["close"] / previous_close - 1.0) * 100.0
    frame["gap_pct_daily"] = (frame["open"] / previous_close - 1.0) * 100.0
    frame["mom5_pct"] = (frame["close"] / frame["close"].shift(5) - 1.0) * 100.0
    frame["mom20_pct"] = (frame["close"] / frame["close"].shift(20) - 1.0) * 100.0
    frame["mom60_pct"] = (frame["close"] / frame["close"].shift(60) - 1.0) * 100.0
    frame["ma20"] = frame["close"].rolling(20, min_periods=20).mean()
    frame["ma60"] = frame["close"].rolling(60, min_periods=60).mean()
    frame["ma200"] = frame["close"].rolling(200, min_periods=200).mean()
    previous_volume_median = frame["volume"].shift(1).rolling(20, min_periods=10).median()
    frame["volume_ratio20"] = frame["volume"] / previous_volume_median.replace(0, np.nan)
    high20 = frame["high"].rolling(20, min_periods=20).max()
    frame["from_high20_pct"] = (frame["close"] / high20 - 1.0) * 100.0
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr20_pct"] = true_range.rolling(20, min_periods=20).mean() / frame["close"] * 100.0
    frame["realized_vol20_pct"] = (
        frame["close"].pct_change().rolling(20, min_periods=20).std() * math.sqrt(252.0) * 100.0
    )
    frame["calendar_index"] = np.arange(len(frame), dtype=int)
    return frame


def load_fx(start: str, end: str, cache_path: Path) -> tuple[pd.Series, str]:
    if cache_path.exists():
        cached = pd.read_csv(cache_path)
        cached["date"] = pd.to_datetime(cached["date"]).dt.strftime("%Y-%m-%d")
        series = pd.Series(cached["usdkrw"].astype(float).values, index=cached["date"])
        return series.sort_index(), "cache"

    import yfinance as yf

    raw = yf.download(
        "KRW=X",
        start=start,
        end=end,
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if raw.empty:
        raise RuntimeError("KRW=X download returned no rows")
    close = raw["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    output = pd.DataFrame(
        {
            "date": pd.to_datetime(close.index).strftime("%Y-%m-%d"),
            "usdkrw": pd.to_numeric(close, errors="coerce").to_numpy(),
        }
    ).dropna()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(cache_path, index=False)
    return pd.Series(output["usdkrw"].values, index=output["date"]).sort_index(), "yfinance_KRW=X"


def _fx_value(fx: pd.Series, session_date: str) -> float | None:
    if session_date in fx.index:
        return _safe_number(fx.loc[session_date])
    eligible = fx[fx.index <= session_date]
    return _safe_number(eligible.iloc[-1]) if len(eligible) else None


def load_candidates(db_path: Path) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        frame = pd.read_sql_query(
            """
            SELECT id,date,market,ticker,consensus_mode,selection_rank,source_type,
                   selected_at,created_at,change_pct,vol_ratio,gap_pct,from_high_pct,
                   signal_fired,trade_ready,traded,selected_reason,veto_reason
            FROM ticker_selection_log
            WHERE bot_mode='live' AND market IN ('US','KR') AND date IS NOT NULL
            ORDER BY date,market,ticker,COALESCE(selected_at,created_at),id
            """,
            con,
        )
    finally:
        con.close()
    frame["ticker"] = frame["ticker"].astype(str)
    frame.loc[frame["market"].eq("US"), "ticker"] = frame.loc[
        frame["market"].eq("US"), "ticker"
    ].str.upper()
    for column in ("selection_rank", "change_pct", "vol_ratio", "gap_pct", "from_high_pct"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in ("signal_fired", "trade_ready", "traded"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0).astype(int)
    frame["known_order"] = frame["selected_at"].fillna(frame["created_at"]).fillna("")
    frame = frame.sort_values(["date", "market", "ticker", "known_order", "id"])

    def aggregate(group: pd.DataFrame) -> pd.Series:
        reasons = " | ".join(str(v) for v in group["selected_reason"].dropna().unique())
        return pd.Series(
            {
                "selection_rank": group["selection_rank"].min(),
                "change_pct_seen": group["change_pct"].dropna().iloc[-1] if group["change_pct"].notna().any() else np.nan,
                "vol_ratio_seen": group["vol_ratio"].dropna().iloc[-1] if group["vol_ratio"].notna().any() else np.nan,
                "gap_pct_seen": group["gap_pct"].dropna().iloc[-1] if group["gap_pct"].notna().any() else np.nan,
                "from_high_pct_seen": group["from_high_pct"].dropna().iloc[-1] if group["from_high_pct"].notna().any() else np.nan,
                "signal_fired": int(group["signal_fired"].max()),
                "trade_ready": int(group["trade_ready"].max()),
                "traded": int(group["traded"].max()),
                "consensus_mode": str(group["consensus_mode"].dropna().iloc[-1]) if group["consensus_mode"].notna().any() else "UNKNOWN",
                "source_type": str(group["source_type"].dropna().iloc[0]) if group["source_type"].notna().any() else "unknown",
                "selected_reason": reasons,
                "vetoed": int(group["veto_reason"].notna().any()),
                "observations": int(len(group)),
            }
        )

    aggregated: list[dict[str, Any]] = []
    for (session_date, market, ticker), group in frame.groupby(
        ["date", "market", "ticker"], sort=True
    ):
        row = aggregate(group).to_dict()
        row.update({"date": session_date, "market": market, "ticker": ticker})
        aggregated.append(row)
    return pd.DataFrame(aggregated)


def _price_path(market: str, ticker: str) -> Path:
    prefix = "us_" if market == "US" else "kr_"
    root = PRICE_ROOT / market.lower()
    direct = root / f"{prefix}{ticker}.csv"
    if direct.exists():
        return direct
    alternate = root / f"{prefix}{ticker.replace('.', '-').replace('/', '-')}.csv"
    return alternate


def _is_leveraged_or_inverse(row: pd.Series) -> bool:
    ticker = str(row["ticker"]).upper()
    reason = str(row.get("selected_reason") or "").lower()
    if str(row["market"]) == "US" and ticker in US_LEVERAGED_OR_INVERSE:
        return True
    return any(word in reason for word in LEVERAGE_WORDS)


def _forward_window(frame: pd.DataFrame, signal_index: int, hold_sessions: int) -> pd.DataFrame | None:
    start = signal_index + 1
    stop = start + hold_sessions
    if start < 1 or stop > len(frame):
        return None
    return frame.iloc[start:stop].copy()


def _trade_return(
    *,
    market: str,
    entry_price: float,
    exit_price: float,
    entry_date: str,
    exit_date: str,
    fx: pd.Series,
    cost_pct: float,
) -> tuple[float, float] | None:
    if min(entry_price, exit_price) <= 0:
        return None
    gross_local = (exit_price / entry_price - 1.0) * 100.0
    if market == "US":
        entry_fx, exit_fx = _fx_value(fx, entry_date), _fx_value(fx, exit_date)
        if not entry_fx or not exit_fx:
            return None
        gross_local = ((exit_price / entry_price) * (exit_fx / entry_fx) - 1.0) * 100.0
    return gross_local, gross_local - cost_pct


def barrier_exit(
    window: pd.DataFrame,
    *,
    entry_price: float,
    take_profit_pct: float,
    stop_loss_pct: float,
) -> tuple[str, float, str]:
    """Conservative daily-OHLC barrier fill: stop wins same-bar ties."""
    target = entry_price * (1.0 + take_profit_pct / 100.0)
    stop = entry_price * (1.0 - stop_loss_pct / 100.0)
    for _, row in window.iterrows():
        stop_hit = float(row["low"]) <= stop
        target_hit = float(row["high"]) >= target
        if stop_hit:
            return str(row["date"]), stop, "STOP"
        if target_hit:
            return str(row["date"]), target, "TARGET"
    final = window.iloc[-1]
    return str(final["date"]), float(final["close"]), "TIME"


def build_event_frame(
    candidates: pd.DataFrame,
    *,
    fx: pd.Series,
    as_of: str,
) -> tuple[pd.DataFrame, dict[str, Any], list[Path]]:
    price_cache: dict[tuple[str, str], pd.DataFrame | None] = {}
    used_files: set[Path] = set()

    def price(market: str, ticker: str) -> pd.DataFrame | None:
        key = (market, ticker)
        if key in price_cache:
            return price_cache[key]
        path = _price_path(market, ticker)
        if not path.exists():
            price_cache[key] = None
            return None
        try:
            price_cache[key] = load_price_frame(path)
            used_files.add(path)
        except (OSError, ValueError, pd.errors.ParserError):
            price_cache[key] = None
        return price_cache[key]

    for market, benchmark in BENCHMARK.items():
        price(market, benchmark)

    output: list[dict[str, Any]] = []
    rejects: dict[str, int] = {}

    def reject(reason: str) -> None:
        rejects[reason] = rejects.get(reason, 0) + 1

    for _, candidate in candidates[candidates["date"].le(as_of)].iterrows():
        market, ticker, signal_date = str(candidate["market"]), str(candidate["ticker"]), str(candidate["date"])
        bars = price(market, ticker)
        benchmark = price(market, BENCHMARK[market])
        if bars is None or benchmark is None:
            reject("missing_price_file")
            continue
        matches = bars.index[bars["date"].eq(signal_date)].tolist()
        bench_matches = benchmark.index[benchmark["date"].eq(signal_date)].tolist()
        if not matches or not bench_matches:
            reject("signal_date_missing")
            continue
        idx, bench_idx = int(matches[-1]), int(bench_matches[-1])
        signal = bars.iloc[idx]
        bench_signal = benchmark.iloc[bench_idx]
        if signal["volume"] <= 0:
            reject("signal_zero_volume")
            continue
        row: dict[str, Any] = candidate.to_dict()
        row.update(
            {
                "signal_close": float(signal["close"]),
                "day_change_pct": _safe_number(signal["ret1_pct"]),
                "day_gap_pct": _safe_number(signal["gap_pct_daily"]),
                "mom5_pct": _safe_number(signal["mom5_pct"]),
                "mom20_pct": _safe_number(signal["mom20_pct"]),
                "mom60_pct": _safe_number(signal["mom60_pct"]),
                "above_ma60": bool(signal["ma60"] > 0 and signal["close"] > signal["ma60"]),
                "above_ma200": bool(signal["ma200"] > 0 and signal["close"] > signal["ma200"]),
                "volume_ratio20": _safe_number(signal["volume_ratio20"]),
                "from_high20_pct": _safe_number(signal["from_high20_pct"]),
                "atr20_pct": _safe_number(signal["atr20_pct"]),
                "realized_vol20_pct": _safe_number(signal["realized_vol20_pct"]),
                "bench_mom20_pct": _safe_number(bench_signal["mom20_pct"]),
                "bench_above_ma60": bool(
                    bench_signal["ma60"] > 0 and bench_signal["close"] > bench_signal["ma60"]
                ),
                "leveraged_or_inverse": _is_leveraged_or_inverse(candidate),
            }
        )
        row["rs20_pct"] = (
            row["mom20_pct"] - row["bench_mom20_pct"]
            if row["mom20_pct"] is not None and row["bench_mom20_pct"] is not None
            else None
        )
        one = _forward_window(bars, idx, 1)
        if one is None:
            reject("no_next_session")
            continue
        entry = one.iloc[0]
        entry_date, entry_price = str(entry["date"]), float(entry["open"])
        if entry["volume"] <= 0 or entry_price <= 0:
            reject("entry_not_tradable")
            continue
        entry_fx = _fx_value(fx, entry_date) if market == "US" else 1.0
        if not entry_fx:
            reject("fx_missing")
            continue
        row["entry_date"] = entry_date
        row["entry_price"] = entry_price
        row["entry_gap_pct"] = (entry_price / float(signal["close"]) - 1.0) * 100.0
        row["whole_share_cost_krw"] = entry_price * float(entry_fx)
        row["affordable"] = row["whole_share_cost_krw"] <= ORDER_BUDGET_KRW[market]
        row["entry_calendar_index"] = int(entry["calendar_index"])

        for hold in (1, 3, 5):
            window = _forward_window(bars, idx, hold)
            bench_window = _forward_window(benchmark, bench_idx, hold)
            if window is None or bench_window is None:
                row[f"available_{hold}d"] = False
                continue
            exit_row, bench_entry, bench_exit = window.iloc[-1], bench_window.iloc[0], bench_window.iloc[-1]
            exit_date = str(exit_row["date"])
            if str(bench_exit["date"]) != exit_date:
                row[f"available_{hold}d"] = False
                continue
            overnight = window["open"] / window["close"].shift(1) - 1.0
            if len(window) > 1 and overnight.iloc[1:].abs().max() > 0.35:
                row[f"available_{hold}d"] = False
                row[f"hygiene_{hold}d"] = "corporate_action_or_bad_gap"
                continue
            result = _trade_return(
                market=market,
                entry_price=entry_price,
                exit_price=float(exit_row["close"]),
                entry_date=entry_date,
                exit_date=exit_date,
                fx=fx,
                cost_pct=COST_PCT[market],
            )
            bench_result = _trade_return(
                market=market,
                entry_price=float(bench_entry["open"]),
                exit_price=float(bench_exit["close"]),
                entry_date=entry_date,
                exit_date=exit_date,
                fx=fx,
                cost_pct=COST_PCT[market],
            )
            if result is None or bench_result is None:
                row[f"available_{hold}d"] = False
                continue
            gross, net = result
            bench_gross, bench_net = bench_result
            row.update(
                {
                    f"available_{hold}d": True,
                    f"exit_date_{hold}d": exit_date,
                    f"gross_{hold}d_pct": gross,
                    f"net_{hold}d_pct": net,
                    f"benchmark_net_{hold}d_pct": bench_net,
                    f"excess_{hold}d_pct": net - bench_net,
                    f"mfe_{hold}d_pct": (window["high"].max() / entry_price - 1.0) * 100.0,
                    f"mae_{hold}d_pct": (window["low"].min() / entry_price - 1.0) * 100.0,
                    f"hygiene_{hold}d": "ok",
                }
            )
            barrier_contracts = {
                3: (("tp3_sl2_3d", 3.0, 2.0),),
                5: (
                    ("tp5_sl2_5d", 5.0, 2.0),
                    ("tp8_sl4_5d", 8.0, 4.0),
                    ("tp10_sl8_5d", 10.0, 8.0),
                ),
            }
            for contract_name, take_profit, stop_loss in barrier_contracts.get(hold, ()):
                contract_date, contract_price, contract_reason = barrier_exit(
                    window,
                    entry_price=entry_price,
                    take_profit_pct=take_profit,
                    stop_loss_pct=stop_loss,
                )
                contract_result = _trade_return(
                    market=market,
                    entry_price=entry_price,
                    exit_price=contract_price,
                    entry_date=entry_date,
                    exit_date=contract_date,
                    fx=fx,
                    cost_pct=COST_PCT[market],
                )
                if contract_result is None:
                    continue
                contract_gross, contract_net = contract_result
                row.update(
                    {
                        f"{contract_name}_gross_pct": contract_gross,
                        f"{contract_name}_net_pct": contract_net,
                        f"{contract_name}_exit_date": contract_date,
                        f"{contract_name}_exit_reason": contract_reason,
                    }
                )
        output.append(row)

    events = pd.DataFrame(output)
    if not events.empty:
        events["atr_rank_daily"] = events.groupby(["market", "date"])["atr20_pct"].rank(pct=True)
        events["selection_rank"] = pd.to_numeric(events["selection_rank"], errors="coerce").fillna(9999)
    coverage = {
        "candidate_rows_deduplicated": int(len(candidates[candidates["date"].le(as_of)])),
        "event_rows_built": int(len(events)),
        "reject_reasons": dict(sorted(rejects.items())),
        "markets": {
            market: {
                "rows": int((events["market"] == market).sum()) if not events.empty else 0,
                "dates": int(events.loc[events["market"] == market, "date"].nunique()) if not events.empty else 0,
                "tickers": int(events.loc[events["market"] == market, "ticker"].nunique()) if not events.empty else 0,
            }
            for market in ("US", "KR")
        },
    }
    return events, coverage, sorted(used_files)


def strategy_specs() -> tuple[list[StrategySpec], dict[str, Callable[[pd.DataFrame], pd.Series]]]:
    def base(frame: pd.DataFrame, hold: int) -> pd.Series:
        return (
            frame[f"available_{hold}d"].fillna(False)
            & frame["affordable"].fillna(False)
            & frame["entry_gap_pct"].le(0.5)
            & frame["vetoed"].eq(0)
        )

    def core(frame: pd.DataFrame, hold: int) -> pd.Series:
        return base(frame, hold) & ~frame["leveraged_or_inverse"].fillna(False)

    predicates: dict[str, Callable[[pd.DataFrame], pd.Series]] = {
        "all": lambda f: base(f, 1),
        "all3": lambda f: base(f, 3),
        "all5": lambda f: base(f, 5),
        "system_fired": lambda f: core(f, 3) & f["signal_fired"].eq(1),
        "trade_ready": lambda f: core(f, 3) & f["trade_ready"].eq(1),
        "riskon_rank": lambda f: core(f, 5) & f["bench_mom20_pct"].gt(0) & f["bench_above_ma60"],
        "trend_rs": lambda f: (
            core(f, 5)
            & f["bench_mom20_pct"].gt(0)
            & f["above_ma60"]
            & f["mom5_pct"].gt(0)
            & f["mom20_pct"].gt(0)
            & f["rs20_pct"].gt(0)
        ),
        "trend_rs_lowvol": lambda f: (
            core(f, 5)
            & f["bench_mom20_pct"].gt(0)
            & f["above_ma60"]
            & f["mom5_pct"].gt(0)
            & f["mom20_pct"].gt(0)
            & f["rs20_pct"].gt(0)
            & f["atr_rank_daily"].le(0.5)
        ),
        "trend_volume": lambda f: (
            core(f, 3)
            & f["bench_mom20_pct"].gt(0)
            & f["above_ma60"]
            & f["mom20_pct"].gt(0)
            & f["volume_ratio20"].ge(1.5)
        ),
        "near_high_breakout": lambda f: (
            core(f, 5)
            & f["bench_mom20_pct"].gt(0)
            & f["above_ma60"]
            & f["mom20_pct"].gt(0)
            & f["from_high20_pct"].ge(-3.0)
            & f["day_change_pct"].between(0.0, 8.0, inclusive="both")
        ),
        "overheat_control": lambda f: (
            core(f, 3)
            & f["above_ma60"]
            & f["day_change_pct"].between(-1.0, 8.0, inclusive="both")
            & f["day_gap_pct"].le(3.0)
            & f["from_high20_pct"].ge(-10.0)
        ),
        "pullback_trend": lambda f: (
            core(f, 3)
            & f["bench_mom20_pct"].gt(0)
            & f["above_ma60"]
            & f["mom20_pct"].gt(0)
            & f["day_change_pct"].between(-5.0, 0.0, inclusive="both")
        ),
        "volume_breakout": lambda f: (
            core(f, 3)
            & f["bench_mom20_pct"].gt(0)
            & f["above_ma60"]
            & f["day_change_pct"].between(2.0, 8.0, inclusive="both")
            & f["volume_ratio20"].ge(2.0)
            & f["from_high20_pct"].ge(-5.0)
        ),
        "overnight_reset": lambda f: (
            core(f, 1)
            & f["above_ma60"]
            & f["day_change_pct"].between(2.0, 8.0, inclusive="both")
            & f["volume_ratio20"].ge(1.2)
            & f["entry_gap_pct"].between(-3.0, 0.5, inclusive="both")
        ),
        "gap_fade_reset": lambda f: (
            core(f, 1)
            & f["above_ma60"]
            & f["day_change_pct"].ge(5.0)
            & f["entry_gap_pct"].between(-5.0, 0.0, inclusive="both")
        ),
        "bear_inverse": lambda f: (
            base(f, 1)
            & f["leveraged_or_inverse"].fillna(False)
            & f["bench_mom20_pct"].lt(0)
        ),
        "leveraged_riskon": lambda f: (
            base(f, 3)
            & f["leveraged_or_inverse"].fillna(False)
            & f["bench_mom20_pct"].gt(0)
            & f["bench_above_ma60"]
        ),
    }
    specs: list[StrategySpec] = []
    for market in ("US", "KR"):
        specs.extend(
            [
                StrategySpec("ALL_TOP_RANK_1D", market, 1, "selection_rank", False, "Actual candidate baseline; next-open to close", "all"),
                StrategySpec("ALL_TOP_RANK_5D", market, 5, "selection_rank", False, "Actual candidate baseline; five-session hold", "all5"),
                StrategySpec("SYSTEM_FIRED_3D", market, 3, "selection_rank", False, "Existing fired signals only", "system_fired"),
                StrategySpec("TRADE_READY_3D", market, 3, "selection_rank", False, "Existing trade-ready candidates only", "trade_ready"),
                StrategySpec("RISKON_TOP_RANK_5D", market, 5, "selection_rank", False, "Cash outside positive benchmark trend", "riskon_rank"),
                StrategySpec("TREND_RS_5D", market, 5, "rs20_pct", True, "Market-confirmed relative-strength continuation", "trend_rs"),
                StrategySpec("TREND_RS_LOWVOL_5D", market, 5, "rs20_pct", True, "Relative strength with lower candidate volatility", "trend_rs_lowvol"),
                StrategySpec("TREND_VOLUME_3D", market, 3, "rs20_pct", True, "Trend plus independently confirmed volume", "trend_volume"),
                StrategySpec("NEAR_HIGH_BREAKOUT_5D", market, 5, "rs20_pct", True, "Controlled breakout near a 20-session high", "near_high_breakout"),
                StrategySpec("OVERHEAT_CONTROL_3D", market, 3, "selection_rank", False, "Keep trend candidates but remove extreme chase", "overheat_control"),
                StrategySpec("PULLBACK_TREND_3D", market, 3, "rs20_pct", True, "Buy a down day inside an established uptrend", "pullback_trend"),
                StrategySpec("VOLUME_BREAKOUT_3D", market, 3, "volume_ratio20", True, "Moderate price breakout with >=2x volume", "volume_breakout"),
                StrategySpec("OVERNIGHT_RESET_1D", market, 1, "volume_ratio20", True, "Re-evaluate moderate momentum at next open", "overnight_reset"),
                StrategySpec("GAP_FADE_RESET_1D", market, 1, "rs20_pct", True, "Strong day followed by a non-chasing down/flat open", "gap_fade_reset"),
            ]
        )
    specs.extend(
        [
            StrategySpec("KR_BEAR_INVERSE_1D", "KR", 1, "selection_rank", False, "Candidate inverse products only in negative benchmark trend", "bear_inverse"),
            StrategySpec("US_LEVERAGED_RISKON_3D", "US", 3, "selection_rank", False, "Leveraged products isolated from stock arms", "leveraged_riskon"),
        ]
    )
    for market in ("US", "KR"):
        specs.extend(
            [
                StrategySpec(
                    "TOP_RANK_TP3_SL2_3D", market, 3, "selection_rank", False,
                    "Convert next-session run-up with a conservative fixed bracket", "all3", "tp3_sl2_3d",
                ),
                StrategySpec(
                    "RISKON_TP5_SL2_5D", market, 5, "selection_rank", False,
                    "Market-confirmed candidate with asymmetric 5/2 bracket", "riskon_rank", "tp5_sl2_5d",
                ),
                StrategySpec(
                    "RISKON_TP8_SL4_5D", market, 5, "selection_rank", False,
                    "Market-confirmed candidate with wider 8/4 bracket", "riskon_rank", "tp8_sl4_5d",
                ),
                StrategySpec(
                    "TREND_RS_TP10_SL8_5D", market, 5, "rs20_pct", True,
                    "Relative-strength swing with a catastrophe-style 10/8 bracket", "trend_rs", "tp10_sl8_5d",
                ),
            ]
        )
    return specs, predicates


def select_trades(
    events: pd.DataFrame,
    spec: StrategySpec,
    predicate: Callable[[pd.DataFrame], pd.Series],
    *,
    cooldown_sessions: int = 20,
    max_open_slots: int = 5,
) -> pd.DataFrame:
    market_rows = events[events["market"].eq(spec.market)].copy()
    eligible = market_rows[predicate(market_rows)].copy()
    if eligible.empty:
        return eligible
    score = pd.to_numeric(eligible[spec.score_column], errors="coerce")
    eligible = eligible[score.notna()].copy()
    eligible["_score"] = score[score.notna()]
    eligible = eligible.sort_values(
        ["date", "_score", "ticker"],
        ascending=[True, not spec.score_descending, True],
    )
    selected: list[pd.Series] = []
    last_entry_index: dict[str, int] = {}
    active_exits: list[str] = []
    for signal_date, group in eligible.groupby("date", sort=True):
        entry_date = str(group["entry_date"].iloc[0])
        active_exits = [value for value in active_exits if value >= entry_date]
        if len(active_exits) >= max_open_slots:
            continue
        chosen: pd.Series | None = None
        for _, row in group.iterrows():
            ticker = str(row["ticker"])
            entry_index = int(row["entry_calendar_index"])
            if ticker in last_entry_index and entry_index - last_entry_index[ticker] < cooldown_sessions:
                continue
            chosen = row
            last_entry_index[ticker] = entry_index
            break
        if chosen is None:
            continue
        chosen = chosen.copy()
        chosen["strategy"] = spec.name
        chosen["hold_sessions"] = spec.hold_sessions
        if spec.outcome_key == "fixed":
            chosen["net_pct"] = chosen[f"net_{spec.hold_sessions}d_pct"]
            chosen["gross_pct"] = chosen[f"gross_{spec.hold_sessions}d_pct"]
            chosen["exit_date"] = chosen[f"exit_date_{spec.hold_sessions}d"]
            chosen["exit_reason_sim"] = "TIME"
        else:
            chosen["net_pct"] = chosen[f"{spec.outcome_key}_net_pct"]
            chosen["gross_pct"] = chosen[f"{spec.outcome_key}_gross_pct"]
            chosen["exit_date"] = chosen[f"{spec.outcome_key}_exit_date"]
            chosen["exit_reason_sim"] = chosen[f"{spec.outcome_key}_exit_reason"]
        chosen["excess_pct"] = chosen[f"excess_{spec.hold_sessions}d_pct"]
        chosen["mfe_pct"] = chosen[f"mfe_{spec.hold_sessions}d_pct"]
        chosen["mae_pct"] = chosen[f"mae_{spec.hold_sessions}d_pct"]
        chosen["stress_net_pct"] = float(chosen["net_pct"]) - 0.50
        selected.append(chosen)
        active_exits.append(str(chosen["exit_date"]))
    return pd.DataFrame(selected)


def moving_block_lcb(values: np.ndarray, *, seed: int, block: int = 5, samples: int = 2000) -> float | None:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 10:
        return None
    rng = np.random.default_rng(seed)
    starts = np.arange(max(1, len(values) - block + 1))
    means = np.empty(samples, dtype=float)
    blocks_needed = int(math.ceil(len(values) / block))
    for sample_idx in range(samples):
        sample: list[float] = []
        for start in rng.choice(starts, size=blocks_needed, replace=True):
            sample.extend(values[int(start): int(start) + block].tolist())
        means[sample_idx] = float(np.mean(sample[: len(values)]))
    return float(np.quantile(means, 0.05))


def trade_metrics(frame: pd.DataFrame, *, seed: int) -> dict[str, Any]:
    if frame.empty:
        return {"trades": 0, "sessions": 0}
    values = pd.to_numeric(frame["net_pct"], errors="coerce").dropna().to_numpy(dtype=float)
    if not len(values):
        return {"trades": 0, "sessions": 0}
    positive, negative = float(values[values > 0].sum()), float(-values[values < 0].sum())
    ordered = np.sort(values)[::-1]
    compounded = np.cumprod(1.0 + values / 100.0)
    peak = np.maximum.accumulate(compounded)
    drawdown = (compounded / peak - 1.0) * 100.0
    return {
        "trades": int(len(values)),
        "sessions": int(frame["date"].nunique()),
        "start": str(frame["date"].min()),
        "end": str(frame["date"].max()),
        "mean_net_pct": float(np.mean(values)),
        "median_net_pct": float(np.median(values)),
        "net_sum_pct": float(np.sum(values)),
        "win_rate": float(np.mean(values > 0)),
        "profit_factor": float(positive / negative) if negative > 0 else None,
        "block_lcb_5pct": moving_block_lcb(values, seed=seed),
        "mean_ex_top3_trades_pct": float(np.mean(ordered[3:])) if len(ordered) > 3 else None,
        "stress_plus_0_5_cost_mean_pct": float(np.mean(values - 0.50)),
        "mean_excess_vs_benchmark_pct": float(pd.to_numeric(frame["excess_pct"], errors="coerce").mean()),
        "mean_mfe_pct": float(pd.to_numeric(frame["mfe_pct"], errors="coerce").mean()),
        "mean_mae_pct": float(pd.to_numeric(frame["mae_pct"], errors="coerce").mean()),
        "worst_trade_pct": float(np.min(values)),
        "best_trade_pct": float(np.max(values)),
        "sequential_full_notional_max_drawdown_pct": float(np.min(drawdown)) if len(drawdown) else None,
    }


def verdict(discovery: dict[str, Any], oos: dict[str, Any]) -> str:
    if int(oos.get("trades", 0)) < 10 or int(oos.get("sessions", 0)) < 8:
        return "INSUFFICIENT_FORWARD"
    oos_positive = (
        float(oos.get("mean_net_pct", -999)) > 0
        and float(oos.get("profit_factor") or 0) >= 1.10
        and float(oos.get("mean_ex_top3_trades_pct") or -999) > 0
        and float(oos.get("stress_plus_0_5_cost_mean_pct", -999)) > 0
    )
    stable = int(discovery.get("trades", 0)) >= 10 and float(discovery.get("mean_net_pct", -999)) > 0
    lcb_positive = oos.get("block_lcb_5pct") is not None and float(oos["block_lcb_5pct"]) > 0
    if oos_positive and stable and lcb_positive:
        return "SHADOW_READY"
    if oos_positive and stable:
        return "RESEARCH_SURVIVOR"
    if oos_positive:
        return "FORWARD_LEAD_REGIME_DEPENDENT"
    return "REJECT_CURRENT_FORM"


def evaluate(events: pd.DataFrame, *, seed: int = 20260715) -> tuple[dict[str, Any], pd.DataFrame]:
    specs, predicates = strategy_specs()
    results: dict[str, Any] = {}
    all_trades: list[pd.DataFrame] = []
    for idx, spec in enumerate(specs):
        trades = select_trades(events, spec, predicates[spec.predicate_name])
        if not trades.empty:
            all_trades.append(trades)
        discovery = trades[trades["date"].lt("2026-06-01")] if not trades.empty else trades
        oos = trades[trades["date"].ge("2026-06-01")] if not trades.empty else trades
        monthly = {
            month: trade_metrics(group, seed=seed + idx + int(month.replace("-", "")))
            for month, group in trades.groupby(trades["date"].astype(str).str[:7])
        } if not trades.empty else {}
        discovery_metrics = trade_metrics(discovery, seed=seed + idx)
        oos_metrics = trade_metrics(oos, seed=seed + 1000 + idx)
        results[f"{spec.market}:{spec.name}"] = {
            "contract": asdict(spec),
            "all": trade_metrics(trades, seed=seed + 2000 + idx),
            "discovery_2026_04_05": discovery_metrics,
            "oos_2026_06_07": oos_metrics,
            "monthly": monthly,
            "verdict": verdict(discovery_metrics, oos_metrics),
        }
    return results, pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()


def combined_file_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).replace("\\", "/").encode("utf-8"))
        digest.update(sha256_file(path).encode("ascii"))
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Actual-candidate system strategy profitability lab")
    parser.add_argument("--selection-db", default=str(DEFAULT_SELECTION_DB))
    parser.add_argument("--as-of", default=str(date.today()))
    parser.add_argument("--output-dir", default=str(ROOT / "reports"))
    parser.add_argument("--seed", type=int, default=20260715)
    args = parser.parse_args()

    selection_db = Path(args.selection_db)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    tag = str(args.as_of).replace("-", "")
    fx_path = output_dir / f"system_strategy_lab_fx_{tag}.csv"
    fx, fx_source = load_fx("2026-04-01", "2026-07-31", fx_path)
    candidates = load_candidates(selection_db)
    events, coverage, used_files = build_event_frame(candidates, fx=fx, as_of=args.as_of)
    results, trades = evaluate(events, seed=args.seed)

    event_path = output_dir / f"system_strategy_lab_events_{tag}.csv"
    trade_path = output_dir / f"system_strategy_lab_trades_{tag}.csv"
    json_path = output_dir / f"system_strategy_lab_{tag}.json"
    events.to_csv(event_path, index=False)
    trades.to_csv(trade_path, index=False)
    manifest = {
        "selection_db": str(selection_db.resolve()),
        "selection_db_sha256": sha256_file(selection_db),
        "fx_cache": str(fx_path.resolve()),
        "fx_cache_sha256": sha256_file(fx_path),
        "fx_source": fx_source,
        "price_files_used": len(used_files),
        "price_files_combined_sha256": combined_file_hash(used_files),
    }
    report = {
        "schema_version": "system_strategy_lab_v1",
        "as_of": args.as_of,
        "authority": "RESEARCH_ONLY_NO_LIVE_CONFIG_OR_ORDER_EFFECT",
        "contract": {
            "candidate_clock": "all D observations aggregated; features through D close only",
            "entry": "next tradable session open",
            "exit": "fixed 1/3/5 session close including entry session",
            "cost_pct": COST_PCT,
            "order_budget_krw": ORDER_BUDGET_KRW,
            "anti_chase_max_entry_gap_pct": 0.5,
            "max_new_per_signal_session": 1,
            "max_open_slots": 5,
            "same_ticker_cooldown_sessions": 20,
            "split": "discovery=2026-04/05; OOS=2026-06/07",
            "uncertainty": "5-session moving-block bootstrap over ordered trades",
            "stress": "additional 0.50 percentage-point round-trip cost",
            "hygiene": "positive OHLCV, next-open volume, <=35% intermediate overnight gaps",
            "us_leveraged_or_inverse": sorted(US_LEVERAGED_OR_INVERSE),
        },
        "coverage": coverage,
        "manifest": manifest,
        "results": results,
    }
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    summary = {
        "json": str(json_path),
        "events": str(event_path),
        "trades": str(trade_path),
        "coverage": coverage,
        "verdict_counts": pd.Series([value["verdict"] for value in results.values()]).value_counts().to_dict(),
        "leads": [key for key, value in results.items() if value["verdict"] != "REJECT_CURRENT_FORM"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
