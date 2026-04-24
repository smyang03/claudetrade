"""Read-only adapters to existing price files and strategy signal functions."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

from indicators import calc_all

from .config import PRICE_DIR


@dataclass(frozen=True)
class StrategyAdapter:
    name: str
    signal: Callable
    params: Callable


def load_strategy(name: str) -> StrategyAdapter:
    module = importlib.import_module(f"strategy.{name}")
    return StrategyAdapter(name=name, signal=module.signal, params=module.params)


def strategy_params(strategy: str, mode: str, market: str, confidence: float = 0.6) -> dict:
    adapter = load_strategy(strategy)
    try:
        return dict(adapter.params(mode, conf=confidence, market=market))
    except TypeError:
        try:
            return dict(adapter.params(mode, market=market))
        except TypeError:
            return dict(adapter.params(mode))


def available_tickers(market: str, price_dir: Path = PRICE_DIR, limit: int = 0) -> list[str]:
    market = str(market or "").lower()
    root = price_dir / market
    if not root.exists():
        return []
    prefix = f"{market}_"
    tickers = sorted(p.stem[len(prefix):] for p in root.glob(f"{prefix}*.csv"))
    return tickers[:limit] if limit and limit > 0 else tickers


def load_price_frame(market: str, ticker: str, price_dir: Path = PRICE_DIR) -> pd.DataFrame:
    market_l = str(market or "").lower()
    path = price_dir / market_l / f"{market_l}_{ticker}.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["date"])
    df.columns = [str(c).lower() for c in df.columns]
    required = {"date", "open", "high", "low", "close", "volume"}
    if not required.issubset(set(df.columns)):
        return pd.DataFrame()
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    return calc_all(df)

