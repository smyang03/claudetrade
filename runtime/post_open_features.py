from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from runtime.funnel_observability import candidate_trace_id
from runtime_paths import get_runtime_path


RETURN_OFFSETS = {
    "ret_3m_pct": 3,
    "ret_5m_pct": 5,
    "ret_10m_pct": 10,
    "ret_30m_pct": 30,
}
OVEREXTENDED_5M_PCT_BY_MARKET = {
    "KR": 6.0,
    "US": 3.0,
}
DEFAULT_OVEREXTENDED_5M_PCT = 5.0
OVEREXTENDED_30M_CONFIRM_PCT = 2.0


@dataclass
class PostOpenFeatureSnapshot:
    snapshot_id: str
    ticker: str
    market: str
    known_at: str
    anchor_at: str
    anchor_price: float
    current_price: float
    ret_3m_pct: float | None = None
    ret_5m_pct: float | None = None
    ret_10m_pct: float | None = None
    ret_30m_pct: float | None = None
    from_open_high_pct: float | None = None
    pullback_from_high_pct: float | None = None
    opening_range_break: bool | None = None
    volume_ratio_open: float | None = None
    spread_bps: float | None = None
    vwap_distance_pct: float | None = None
    momentum_state: str = "unknown"
    data_quality: str = "partial"

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "ticker": self.ticker,
            "market": self.market,
            "known_at": self.known_at,
            "anchor_at": self.anchor_at,
            "anchor_price": self.anchor_price,
            "current_price": self.current_price,
            "ret_3m_pct": self.ret_3m_pct,
            "ret_5m_pct": self.ret_5m_pct,
            "ret_10m_pct": self.ret_10m_pct,
            "ret_30m_pct": self.ret_30m_pct,
            "from_open_high_pct": self.from_open_high_pct,
            "pullback_from_high_pct": self.pullback_from_high_pct,
            "opening_range_break": self.opening_range_break,
            "volume_ratio_open": self.volume_ratio_open,
            "spread_bps": self.spread_bps,
            "vwap_distance_pct": self.vwap_distance_pct,
            "momentum_state": self.momentum_state,
            "data_quality": self.data_quality,
        }


def parse_dt(value: Any) -> datetime:
    text = str(value or "").strip()
    if text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.now()


def pct_change(current: float | None, anchor: float | None) -> float | None:
    if current is None or anchor is None:
        return None
    if float(anchor or 0.0) <= 0:
        return None
    return (float(current) / float(anchor) - 1.0) * 100.0


def returns_from_price_history(
    history: list[dict[str, Any]],
    *,
    anchor_at: Any,
    anchor_price: float,
    known_at: Any,
    max_lag_sec: int = 180,
) -> dict[str, float | None]:
    anchor = parse_dt(anchor_at)
    known = parse_dt(known_at)
    ordered = sorted(
        (
            {"ts": parse_dt(item.get("ts")), "price": float(item.get("price") or 0.0)}
            for item in history or []
            if float(item.get("price") or 0.0) > 0
        ),
        key=lambda item: item["ts"],
    )
    out: dict[str, float | None] = {}
    for key, offset in RETURN_OFFSETS.items():
        target = anchor + timedelta(minutes=offset)
        if known < target:
            out[key] = None
            continue
        sample = next((item for item in ordered if item["ts"] >= target), None)
        if sample is None:
            out[key] = None
            continue
        lag = abs((sample["ts"] - target).total_seconds())
        if lag > max_lag_sec:
            out[key] = None
            continue
        out[key] = pct_change(sample["price"], anchor_price)
    return out


def feature_known_at_allowed(*, known_at: Any, anchor_at: Any, offset_min: int) -> bool:
    known = parse_dt(known_at)
    anchor = parse_dt(anchor_at)
    return known >= anchor + timedelta(minutes=int(offset_min))


def filter_future_returns(
    returns: dict[str, Any],
    *,
    known_at: Any,
    anchor_at: Any,
) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for key, offset in RETURN_OFFSETS.items():
        if feature_known_at_allowed(known_at=known_at, anchor_at=anchor_at, offset_min=offset):
            value = returns.get(key)
            out[key] = None if value is None else float(value)
        else:
            out[key] = None
    return out


def infer_momentum_state(
    *,
    market: str = "",
    ret_3m_pct: float | None = None,
    ret_5m_pct: float | None = None,
    ret_10m_pct: float | None = None,
    ret_30m_pct: float | None = None,
    pullback_from_high_pct: float | None = None,
    overextended_5m_pct: float | None = None,
) -> str:
    ret5 = ret_5m_pct
    ret30 = ret_30m_pct
    pullback = pullback_from_high_pct
    market_key = str(market or "").upper()
    overextended_threshold = (
        float(overextended_5m_pct)
        if overextended_5m_pct is not None
        else float(OVEREXTENDED_5M_PCT_BY_MARKET.get(market_key, DEFAULT_OVEREXTENDED_5M_PCT))
    )
    if pullback is not None and pullback <= -4.0:
        return "fade"
    if ret5 is not None and ret5 >= overextended_threshold and (
        ret30 is None or ret30 < OVEREXTENDED_30M_CONFIRM_PCT
    ):
        return "overextended"
    if ret5 is not None and ret30 is not None and ret5 > 1.0 and ret30 > 2.0:
        return "sustained"
    if ret5 is not None and ret5 > 1.0:
        return "early_strength"
    if ret_3m_pct is not None and ret_3m_pct > 0.5:
        return "early_probe_only"
    if ret30 is not None and ret30 > 2.0:
        return "late_mover"
    return "unknown"


def build_post_open_snapshot(
    *,
    market: str,
    ticker: str,
    known_at: Any,
    anchor_at: Any,
    anchor_price: float,
    current_price: float,
    returns: dict[str, Any] | None = None,
    open_high: float | None = None,
    opening_range_high: float | None = None,
    volume_ratio_open: float | None = None,
    bid: float | None = None,
    ask: float | None = None,
    vwap_distance_pct: float | None = None,
    data_quality: str = "partial",
) -> PostOpenFeatureSnapshot:
    filtered_returns = filter_future_returns(returns or {}, known_at=known_at, anchor_at=anchor_at)
    from_high = pct_change(current_price, open_high) if open_high else None
    from_open_high = pct_change(open_high, anchor_price) if open_high else None
    spread_bps = None
    if bid and ask and bid > 0 and ask >= bid:
        spread_bps = ((ask - bid) / ((ask + bid) / 2.0)) * 10000.0
    opening_range_break = None
    if opening_range_high and current_price:
        opening_range_break = float(current_price) > float(opening_range_high)
    state = infer_momentum_state(
        market=market,
        ret_3m_pct=filtered_returns["ret_3m_pct"],
        ret_5m_pct=filtered_returns["ret_5m_pct"],
        ret_10m_pct=filtered_returns["ret_10m_pct"],
        ret_30m_pct=filtered_returns["ret_30m_pct"],
        pullback_from_high_pct=from_high,
    )
    snapshot_id = candidate_trace_id(
        session_date=str(anchor_at)[:10],
        market=market,
        ticker=ticker,
        first_seen_at=anchor_at,
        cycle_id=f"feature_{str(known_at).replace(':', '').replace('-', '')[:15]}",
    )
    return PostOpenFeatureSnapshot(
        snapshot_id=snapshot_id,
        ticker=str(ticker).upper() if str(market).upper() == "US" else str(ticker),
        market=str(market).upper(),
        known_at=str(known_at),
        anchor_at=str(anchor_at),
        anchor_price=float(anchor_price),
        current_price=float(current_price),
        ret_3m_pct=filtered_returns["ret_3m_pct"],
        ret_5m_pct=filtered_returns["ret_5m_pct"],
        ret_10m_pct=filtered_returns["ret_10m_pct"],
        ret_30m_pct=filtered_returns["ret_30m_pct"],
        from_open_high_pct=from_open_high,
        pullback_from_high_pct=from_high,
        opening_range_break=opening_range_break,
        volume_ratio_open=volume_ratio_open,
        spread_bps=spread_bps,
        vwap_distance_pct=vwap_distance_pct,
        momentum_state=state,
        data_quality=data_quality,
    )


def append_feature_snapshot(snapshot: PostOpenFeatureSnapshot) -> None:
    day = str(snapshot.known_at)[:10].replace("-", "")
    path = get_runtime_path("logs", "funnel", f"post_open_features_{day}_{snapshot.market}.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(snapshot.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
