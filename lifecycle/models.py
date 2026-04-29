from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid


class Market(str, Enum):
    KR = "KR"
    US = "US"


class RuntimeMode(str, Enum):
    LIVE = "live"
    PAPER = "paper"


class PathType(str, Enum):
    TIMING_ADAPTER = "timing_adapter"
    CLAUDE_PRICE = "claude_price"


class DataQuality(str, Enum):
    CLEAN = "CLEAN"
    SUSPECT = "SUSPECT"
    DIRTY = "DIRTY"
    LEGACY_UNKNOWN = "LEGACY_UNKNOWN"


class LifecycleEventType(str, Enum):
    CLAUDE_TRADE_READY = "CLAUDE_TRADE_READY"
    SAFETY_PASSED = "SAFETY_PASSED"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    WAIT_TIMING = "WAIT_TIMING"
    TIMING_UNSUPPORTED = "TIMING_UNSUPPORTED"
    TIMING_EXPIRED = "TIMING_EXPIRED"
    ORDER_SENT = "ORDER_SENT"
    ORDER_ACKED = "ORDER_ACKED"
    PARTIAL_FILLED = "PARTIAL_FILLED"
    FILLED = "FILLED"
    ORDER_REMAINDER_CANCELLED = "ORDER_REMAINDER_CANCELLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_UNKNOWN = "ORDER_UNKNOWN"
    ORDER_RECOVERED = "ORDER_RECOVERED"
    CLOSED = "CLOSED"
    FORWARD_PENDING_DATA = "FORWARD_PENDING_DATA"
    FORWARD_MEASURED = "FORWARD_MEASURED"
    QUALITY_MARKED = "QUALITY_MARKED"
    CLAUDE_PRICE_PLAN_CREATED = "CLAUDE_PRICE_PLAN_CREATED"
    CLAUDE_PRICE_WAITING = "CLAUDE_PRICE_WAITING"
    CLAUDE_PRICE_HIT = "CLAUDE_PRICE_HIT"
    CLAUDE_PRICE_EXPIRED = "CLAUDE_PRICE_EXPIRED"
    CLAUDE_PRICE_CANCELLED = "CLAUDE_PRICE_CANCELLED"
    CLAUDE_PRICE_REVISED = "CLAUDE_PRICE_REVISED"
    CLAUDE_PRICE_TARGET_HIT = "CLAUDE_PRICE_TARGET_HIT"
    CLAUDE_PRICE_STOP_HIT = "CLAUDE_PRICE_STOP_HIT"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_market(value: str | Market) -> str:
    if isinstance(value, Market):
        return value.value
    normalized = str(value or "").strip().upper()
    if normalized not in {item.value for item in Market}:
        raise ValueError(f"unsupported market: {value!r}")
    return normalized


def normalize_runtime_mode(value: str | RuntimeMode) -> str:
    if isinstance(value, RuntimeMode):
        return value.value
    normalized = str(value or "").strip().lower()
    if normalized not in {item.value for item in RuntimeMode}:
        raise ValueError(f"unsupported runtime_mode: {value!r}")
    return normalized


def normalize_event_type(value: str | LifecycleEventType) -> str:
    if isinstance(value, LifecycleEventType):
        return value.value
    normalized = str(value or "").strip().upper()
    if normalized not in {item.value for item in LifecycleEventType}:
        raise ValueError(f"unsupported lifecycle event type: {value!r}")
    return normalized


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def make_decision_id(market: str, session_date: str, ticker: str) -> str:
    safe_ticker = str(ticker or "").strip().upper().replace(" ", "_")
    safe_date = str(session_date or "").replace("-", "")
    return f"dec_{safe_date}_{normalize_market(market)}_{safe_ticker}_{uuid.uuid4().hex[:8]}"


def make_path_run_id(path_type: str | PathType, market: str, session_date: str, ticker: str) -> str:
    path_value = path_type.value if isinstance(path_type, PathType) else str(path_type or "").strip().lower()
    if path_value not in {item.value for item in PathType}:
        raise ValueError(f"unsupported path_type: {path_type!r}")
    safe_ticker = str(ticker or "").strip().upper().replace(" ", "_")
    safe_date = str(session_date or "").replace("-", "")
    return f"path_{safe_date}_{normalize_market(market)}_{safe_ticker}_{path_value}_{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class LifecycleEvent:
    event_type: str | LifecycleEventType
    market: str | Market
    runtime_mode: str | RuntimeMode
    session_date: str
    ticker: str
    decision_id: str
    prompt_version: str
    brain_snapshot_id: str
    execution_id: str | None = None
    position_id: str | None = None
    reason_code: str | None = None
    data_quality: str | DataQuality = DataQuality.LEGACY_UNKNOWN
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: str = field(default_factory=utc_now_iso)
    event_uuid: str = field(default_factory=lambda: make_id("evt"))

    def normalized(self) -> "LifecycleEvent":
        quality = self.data_quality.value if isinstance(self.data_quality, DataQuality) else str(self.data_quality)
        if quality not in {item.value for item in DataQuality}:
            raise ValueError(f"unsupported data_quality: {self.data_quality!r}")
        return LifecycleEvent(
            event_type=normalize_event_type(self.event_type),
            market=normalize_market(self.market),
            runtime_mode=normalize_runtime_mode(self.runtime_mode),
            session_date=str(self.session_date),
            ticker=str(self.ticker or "").strip().upper() if normalize_market(self.market) == "US" else str(self.ticker or "").strip(),
            decision_id=str(self.decision_id or "").strip(),
            prompt_version=str(self.prompt_version or "").strip(),
            brain_snapshot_id=str(self.brain_snapshot_id or "").strip(),
            execution_id=str(self.execution_id).strip() if self.execution_id else None,
            position_id=str(self.position_id).strip() if self.position_id else None,
            reason_code=str(self.reason_code).strip() if self.reason_code else None,
            data_quality=quality,
            payload=dict(self.payload or {}),
            occurred_at=str(self.occurred_at),
            event_uuid=str(self.event_uuid or make_id("evt")),
        )
