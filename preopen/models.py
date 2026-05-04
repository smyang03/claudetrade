from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


def utcish_now_iso() -> str:
    # The rest of the bot stores KST-local ISO strings in most runtime files.
    from bot.session_date import KST

    return datetime.now(KST).isoformat(timespec="seconds")


@dataclass
class PreopenCandidate:
    ticker: str
    name: str = ""
    market: str = ""
    session_date: str = ""
    source: str = ""
    provider: str = ""
    detected_at: str = ""
    captured_at: str = ""
    first_detected_at: str = ""
    last_detected_at: str = ""
    preopen_score: float = 0.0
    shadow_preopen_rank: int | None = None
    preopen_grade: str = "C"
    actual_selection_rank: int | None = None
    rank_delta: int | None = None
    actual_selected: bool | None = None
    actual_trade_ready: bool | None = None
    actual_ordered: bool | None = None
    actual_rejection_reason: str | None = None
    source_overlap_count: int = 1
    data_quality: str = "unknown"
    stale: bool = False
    risk_tags: list[str] = field(default_factory=list)
    quality_tags: list[str] = field(default_factory=list)
    pattern_tags: list[str] = field(default_factory=list)
    preopen_reason: list[str] = field(default_factory=list)
    provider_rank: int | None = None
    screen_score: float | None = None
    price: float | None = None
    volume: float | None = None
    change_rate: float | None = None
    gap_pct: float | None = None
    volume_ratio: float | None = None
    extended_price: float | None = None
    regular_prev_close: float | None = None
    extended_change_pct: float | None = None
    extended_volume: float | None = None
    extended_dollar_volume: float | None = None
    prior_day_traded_value: float | None = None
    bid: float | None = None
    ask: float | None = None
    spread_pct: float | None = None
    quote_timestamp: str = ""
    news_or_earnings_flag: bool | None = None
    open_volume_confirmation: float | None = None
    regular_open_price: float | None = None
    last_price: float | None = None
    last_price_at: str = ""
    post_open_5m_return_pct: float | None = None
    post_open_30m_return_pct: float | None = None
    post_open_60m_return_pct: float | None = None
    post_open_90m_return_pct: float | None = None
    post_open_120m_return_pct: float | None = None
    post_open_mfe_pct: float | None = None
    post_open_mae_pct: float | None = None
    max_runup_pct: float | None = None
    max_drawdown_pct: float | None = None
    open_to_high_pct: float | None = None
    open_to_close_pct: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_candidate(raw: dict[str, Any], *, market: str, session_date: str, captured_at: str) -> dict[str, Any]:
    ticker = str(raw.get("ticker", "") or "").strip().upper() if market == "US" else str(raw.get("ticker", "") or "").strip()
    candidate = PreopenCandidate(
        ticker=ticker,
        name=str(raw.get("name", "") or ticker),
        market=market,
        session_date=session_date,
        source=str(raw.get("source", "") or "unknown"),
        provider=str(raw.get("provider", "") or raw.get("source", "") or "unknown"),
        detected_at=str(raw.get("detected_at", "") or captured_at),
        captured_at=str(raw.get("captured_at", "") or captured_at),
        first_detected_at=str(raw.get("first_detected_at", "") or captured_at),
        last_detected_at=str(raw.get("last_detected_at", "") or captured_at),
    ).to_dict()
    for key, value in raw.items():
        if key in candidate:
            candidate[key] = value
    if candidate.get("provider") in ("", None):
        candidate["provider"] = candidate.get("source", "unknown")
    if candidate.get("source") in ("", None):
        candidate["source"] = candidate.get("provider", "unknown")
    if candidate.get("price") is None and candidate.get("extended_price") is not None:
        candidate["price"] = candidate.get("extended_price")
    if candidate.get("extended_price") is None and candidate.get("price") is not None:
        candidate["extended_price"] = candidate.get("price")
    if candidate.get("gap_pct") is None and candidate.get("extended_change_pct") is not None:
        candidate["gap_pct"] = candidate.get("extended_change_pct")
    if candidate.get("extended_change_pct") is None and candidate.get("gap_pct") is not None:
        candidate["extended_change_pct"] = candidate.get("gap_pct")
    candidate["ticker"] = ticker
    candidate["market"] = market
    candidate["session_date"] = session_date
    candidate["captured_at"] = captured_at
    return candidate
