"""
universe_manager.py
Dynamic universe snapshot utilities for runtime and backtest consistency.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
UNIVERSE_DIR = BASE_DIR / "data" / "universe"
UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class UniverseConfig:
    top_n: int = 20
    min_price: float = 1.0
    min_volume: float = 1.0


def _market_dir(market: str) -> Path:
    p = UNIVERSE_DIR / market.upper()
    p.mkdir(parents=True, exist_ok=True)
    return p


def universe_path(market: str, target_date: str) -> Path:
    return _market_dir(market) / f"{target_date}.json"


def load_universe_snapshot(market: str, target_date: str) -> dict:
    path = universe_path(market, target_date)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data
    return {}


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _candidate_score(c: dict) -> float:
    # Liquidity + abnormal participation (vol_ratio) weighted score.
    volume = max(0.0, _safe_float(c.get("volume", 0.0)))
    vol_ratio = max(0.0, _safe_float(c.get("vol_ratio", 0.0)))
    change_rate = abs(_safe_float(c.get("change_rate", 0.0)))
    liquidity = math.log1p(volume)
    return (liquidity * 0.6) + (vol_ratio * 30.0) + (change_rate * 2.0)


def build_universe_from_candidates(
    market: str,
    target_date: str,
    candidates: list[dict],
    config: UniverseConfig | None = None,
    source: str = "runtime_screen",
) -> dict:
    cfg = config or UniverseConfig()
    cleaned = []
    for c in candidates:
        ticker = str(c.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        price = _safe_float(c.get("price", 0.0))
        volume = _safe_float(c.get("volume", 0.0))
        if price < cfg.min_price or volume < cfg.min_volume:
            continue
        item = {
            "ticker": ticker,
            "name": str(c.get("name", ticker)),
            "price": price,
            "change_rate": _safe_float(c.get("change_rate", 0.0)),
            "volume": volume,
            "vol_ratio": _safe_float(c.get("vol_ratio", 0.0)),
        }
        item["score"] = _candidate_score(item)
        cleaned.append(item)

    cleaned.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    selected = cleaned[: max(1, cfg.top_n)]
    snapshot = {
        "date": target_date,
        "market": market.upper(),
        "source": source,
        "config": {
            "top_n": cfg.top_n,
            "min_price": cfg.min_price,
            "min_volume": cfg.min_volume,
        },
        "tickers": [c["ticker"] for c in selected],
        "candidates": selected,
        "count": len(selected),
    }
    return snapshot


def save_universe_snapshot(snapshot: dict) -> Path:
    market = str(snapshot.get("market", "")).upper()
    target_date = str(snapshot.get("date", ""))
    if not market or not target_date:
        raise ValueError("snapshot must include market/date")
    path = universe_path(market, target_date)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return path


def build_universe_from_price_history(
    market: str,
    target_date: str,
    tickers: list[str],
    load_price_fn: Callable[[str, str], object],
    config: UniverseConfig | None = None,
) -> dict:
    """
    Build point-in-time universe for backtest from stored OHLCV history.
    load_price_fn: (market, ticker) -> DataFrame with at least date/close/volume/vol_ratio/change_pct
    """
    cfg = config or UniverseConfig()
    cands: list[dict] = []
    for ticker in tickers:
        df = load_price_fn(market, ticker)
        if df is None or getattr(df, "empty", True):
            continue

        ts = pd.Timestamp(target_date)
        row = df[df["date"] == ts]
        if getattr(row, "empty", True):
            past = df[df["date"] < ts]
            if getattr(past, "empty", True):
                continue
            row = past.iloc[[-1]]

        r = row.iloc[0]
        cands.append(
            {
                "ticker": ticker,
                "name": ticker,
                "price": _safe_float(r.get("close", 0.0)),
                "change_rate": _safe_float(r.get("change_pct", 0.0)),
                "volume": _safe_float(r.get("volume", 0.0)),
                "vol_ratio": _safe_float(r.get("vol_ratio", 1.0)),
            }
        )

    return build_universe_from_candidates(
        market=market,
        target_date=target_date,
        candidates=cands,
        config=cfg,
        source="historical_price",
    )
