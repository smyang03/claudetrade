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
    min_dollar_volume: float = 0.0  # price * volume 하한 (0=비활성화)
    category_cap: int = 3
    sector_cap: int = 3
    overextended_cap: int = 3
    low_liquidity_cap: int = 2
    kosdaq_cap: int = 5


# 시장별 Core 종목 — Dynamic Universe에서 항상 보장
US_CORE_TICKERS: list[str] = ["NVDA", "TSLA", "AAPL", "GOOGL", "NFLX"]
KR_CORE_TICKERS: list[str] = [
    "005930",  # 삼성전자
    "068270",  # 셀트리온 (000660 SK하이닉스 → 교체)
    "035420",  # NAVER
    "035720",  # 카카오
    "005380",  # 현대차
    "051910",  # LG화학
]


def get_core_tickers(market: str) -> list[str]:
    return US_CORE_TICKERS if market.upper() == "US" else KR_CORE_TICKERS


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


def _candidate_score(c: dict, vol_weight: float = 4.0) -> float:
    # Liquidity + abnormal participation (vol_ratio) weighted score.
    # vol_weight: KR 장중=4.0 / KR 장전=0.5 / US=4.0 (vol_ratio=1.0 고정이라 사실상 무영향)
    volume = max(0.0, _safe_float(c.get("volume", 0.0)))
    vol_ratio = max(0.0, _safe_float(c.get("vol_ratio", 0.0)))
    change_rate = abs(_safe_float(c.get("change_rate", 0.0)))
    liquidity = math.log1p(volume)
    return (liquidity * 0.6) + (vol_ratio * vol_weight) + (change_rate * 2.0)


def _candidate_pullback_bucket(from_high_pct) -> str:
    value = _safe_float(from_high_pct, 0.0)
    if value <= -5.0:
        return "deep"
    if value <= -2.0:
        return "pullback"
    if value <= -0.5:
        return "near_high"
    return "at_high"


def _candidate_liquidity_bucket(turnover: float) -> str:
    if turnover >= 10_000_000_000:
        return "high"
    if turnover >= 1_000_000_000:
        return "mid"
    return "low"


def _diverse_dynamic_items(
    cleaned: list[dict],
    market: str,
    dynamic_slots: int,
    cfg: UniverseConfig,
    excluded: set[str],
) -> list[dict]:
    if dynamic_slots <= 0:
        return []

    chosen: list[dict] = []
    deferred: list[dict] = []
    category_counts: dict[str, int] = {}
    sector_counts: dict[str, int] = {}
    overextended_count = 0
    low_liquidity_count = 0
    kosdaq_count = 0

    for item in cleaned:
        if item["ticker"] in excluded:
            continue

        category = str(item.get("category", "") or "").strip().lower()
        sector = str(item.get("sector", "") or "").strip().lower()
        pullback = str(item.get("from_high_bucket", "") or "").strip().lower()
        liquidity = str(item.get("liquidity_bucket", "") or "").strip().lower()
        market_type = str(item.get("market_type", "") or "").strip().upper()

        blocked = False
        if category and category_counts.get(category, 0) >= cfg.category_cap:
            blocked = True
        if sector and sector_counts.get(sector, 0) >= cfg.sector_cap:
            blocked = True
        if pullback in {"at_high", "near_high"} and overextended_count >= cfg.overextended_cap:
            blocked = True
        if liquidity == "low" and low_liquidity_count >= cfg.low_liquidity_cap:
            blocked = True
        if market.upper() == "KR" and market_type == "KOSDAQ" and kosdaq_count >= cfg.kosdaq_cap:
            blocked = True

        if blocked:
            deferred.append(item)
            continue

        chosen.append(item)
        if category:
            category_counts[category] = category_counts.get(category, 0) + 1
        if sector:
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if pullback in {"at_high", "near_high"}:
            overextended_count += 1
        if liquidity == "low":
            low_liquidity_count += 1
        if market.upper() == "KR" and market_type == "KOSDAQ":
            kosdaq_count += 1
        if len(chosen) >= dynamic_slots:
            return chosen[:dynamic_slots]

    if len(chosen) < dynamic_slots:
        for item in deferred:
            if item["ticker"] in excluded:
                continue
            chosen.append(item)
            if len(chosen) >= dynamic_slots:
                break

    return chosen[:dynamic_slots]


def build_universe_from_candidates(
    market: str,
    target_date: str,
    candidates: list[dict],
    config: UniverseConfig | None = None,
    source: str = "runtime_screen",
    core_tickers: list[str] | None = None,
) -> dict:
    """
    candidates 점수 정렬 후 top_n 선택.
    core_tickers가 지정되면 항상 앞에 보장하고, 나머지 슬롯을 Dynamic으로 채운다.
    """
    cfg = config or UniverseConfig()
    core = [t.upper() for t in (core_tickers or [])]

    # KR 장전(08:30~09:05 KST) vol_ratio 가중치 거의 제거 — KIS vol_tnrt가 전일 기준이라 신뢰 불가
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    _now_kr = _dt.now(_ZI("Asia/Seoul"))
    _kr_premarket = (
        market.upper() == "KR"
        and (
            (_now_kr.hour == 8 and _now_kr.minute >= 30)
            or (_now_kr.hour == 9 and _now_kr.minute <= 5)
        )
    )
    vol_weight = 0.5 if _kr_premarket else 4.0

    cleaned = []
    for c in candidates:
        ticker = str(c.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        price = _safe_float(c.get("price", 0.0))
        volume = _safe_float(c.get("volume", 0.0))
        if price < cfg.min_price:
            continue
        if cfg.min_dollar_volume > 0 and price * volume < cfg.min_dollar_volume:
            continue
        item = {
            "ticker": ticker,
            "name": str(c.get("name", ticker)),
            "price": price,
            "change_rate": _safe_float(c.get("change_rate", 0.0)),
            "volume": volume,
            "vol_ratio": _safe_float(c.get("vol_ratio", 0.0)),
            "market_type": str(c.get("market_type", "") or "").strip().upper(),
            "category": str(c.get("category", "") or "").strip(),
            "sector": str(c.get("sector", "") or "").strip(),
            "from_high_pct": _safe_float(c.get("from_high_pct", 0.0)),
            "above_ma60": c.get("above_ma60"),
        }
        turnover = price * volume
        item["liquidity_bucket"] = (
            str(c.get("liquidity_bucket", "") or "").strip().lower()
            or _candidate_liquidity_bucket(turnover)
        )
        item["from_high_bucket"] = (
            str(c.get("from_high_bucket", "") or "").strip().lower()
            or _candidate_pullback_bucket(item["from_high_pct"])
        )
        item["score"] = _candidate_score(item, vol_weight=vol_weight)
        cleaned.append(item)

    cleaned.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    # ── Core 우선 배치 ────────────────────────────────────────────────────────
    # Core 종목을 candidates 앞에 고정, 나머지 Dynamic 슬롯으로 채움
    core_items = [c for c in cleaned if c["ticker"] in core]
    core_found = {c["ticker"] for c in core_items}
    # candidates에 없는 Core는 placeholder로 추가 (price/volume=0 허용)
    for t in core:
        if t not in core_found:
            core_items.append({"ticker": t, "name": t, "price": 0.0,
                                "change_rate": 0.0, "volume": 0.0,
                                "vol_ratio": 1.0, "score": 0.0,
                                "market_type": "", "category": "", "sector": "",
                                "from_high_pct": 0.0, "above_ma60": None,
                                "liquidity_bucket": "low", "from_high_bucket": "at_high"})

    dynamic_slots = max(0, cfg.top_n - len(core_items))
    dynamic_items = _diverse_dynamic_items(
        cleaned,
        market,
        dynamic_slots,
        cfg,
        {c["ticker"] for c in core_items},
    )
    selected = core_items + dynamic_items

    snapshot = {
        "date": target_date,
        "market": market.upper(),
        "source": source,
        "config": {
            "top_n": cfg.top_n,
            "min_price": cfg.min_price,
            "min_dollar_volume": cfg.min_dollar_volume,
        },
        "core_tickers": core,
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
