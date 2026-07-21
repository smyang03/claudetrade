"""진입시각 관측기 (shadow-only) — 실제 진입의 개장 후 경과분·버킷을 원장에 남긴다.

배경(2026-07-21): 장초반 30분 진입축은 forward +0.17 검증됐으나 soft gate 완화는
매수확대(기각 이력)라 config를 건드리지 않는다. 대신 실제 진입이 어느 시각 버킷에서
발생하고 그 버킷별 우리 net이 어떤지 forward로 잴 수 있게 관측만 한다.

- 매매·게이트 무접촉. 실패해도 진입에 영향 없음(전부 fail-silent).
- 소비: tools/entry_timing_review.py (시장별 분리 집계).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from runtime_paths import get_runtime_path

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")

# 개장 시각(현지): KR 09:00 KST, US 09:30 ET
_OPEN_MINUTES = {"KR": 9 * 60, "US": 9 * 60 + 30}
_TZ = {"KR": KST, "US": ET}

BUCKETS = ((30, "open_0_30"), (60, "open_30_60"), (120, "mid_60_120"), (10 ** 9, "late_120_plus"))


def elapsed_minutes_from_open(market: str, now: datetime | None = None) -> float | None:
    """개장 후 경과분(현지 기준). 개장 전이면 음수, 시장 미상이면 None."""
    mkt = str(market or "").upper()
    if mkt not in _OPEN_MINUTES:
        return None
    local = (now or datetime.now(KST)).astimezone(_TZ[mkt])
    return (local.hour * 60 + local.minute + local.second / 60.0) - _OPEN_MINUTES[mkt]


def bucket_for(elapsed_min: float | None) -> str:
    if elapsed_min is None:
        return "unknown"
    if elapsed_min < 0:
        return "preopen"
    for limit, name in BUCKETS:
        if elapsed_min < limit:
            return name
    return "late_120_plus"


def record_entry_timing(
    *,
    market: str,
    ticker: str,
    strategy: str = "",
    price: float | None = None,
    qty: int | None = None,
    now: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """실진입 1건의 시각 버킷을 격리 funnel JSONL에 기록(관측 전용)."""
    try:
        ts = now or datetime.now(KST)
        mkt = str(market or "").upper()
        elapsed = elapsed_minutes_from_open(mkt, ts)
        payload: dict[str, Any] = {
            "event_type": "entry_timing",
            "written_at": ts.isoformat(timespec="seconds"),
            "market": mkt,
            "ticker": str(ticker or ""),
            "strategy": str(strategy or ""),
            "price": price,
            "qty": qty,
            "elapsed_min": round(elapsed, 1) if elapsed is not None else None,
            "bucket": bucket_for(elapsed),
        }
        if extra:
            payload.update(extra)
        day = ts.strftime("%Y%m%d")
        path: Path = get_runtime_path("logs", "funnel", f"entry_timing_{day}_{mkt or 'NA'}.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
