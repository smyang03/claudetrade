from __future__ import annotations

from typing import Any

from lifecycle.event_store import EventStore
from lifecycle.models import (
    LifecycleEvent,
    LifecycleEventType,
    make_decision_id,
    normalize_market,
    normalize_runtime_mode,
)


# CLAUDE_TRADE_READY는 종목마다 한 건씩 영속된다. 전체 후보 작업공간을
# 복제하지 않고 실제 lifecycle/learning 소비 필드만 종목별로 투영한다.
_PERSISTED_SCALAR_KEYS = (
    "consensus_mode",
    "_selection_raw_schema",
    "_selection_schema_version",
    "_selection_stop_reason",
    "_candidate_actions_source",
    "_candidate_actions_missing_contract",
    "_fallback_mode",
    "generated_at",
)
_PERSISTED_TICKER_MAP_KEYS = (
    "price_targets",
    "_pathb_price_targets",
    "recommended_strategy",
    "timing_style",
    "reasons",
    "veto",
    "_discovery_role_by_ticker",
    "_discovery_action_ceiling_by_ticker",
)
_PERSISTED_PROMPT_ROW_KEYS = (
    "ticker",
    "candidate_pool_role",
    "discovery_action_ceiling",
    "discovery_signal_family",
    "discovery_reason",
    "discovery_overlay_rank",
)


def _ticker_key(market: str, value: Any) -> str:
    text = str(value or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


def _map_item(value: Any, *, market: str, ticker: str) -> tuple[str, Any] | None:
    if not isinstance(value, dict):
        return None
    wanted = _ticker_key(market, ticker)
    for raw_key, raw_value in value.items():
        if _ticker_key(market, raw_key) == wanted:
            return str(raw_key), raw_value
    return None


def _compact_selection_meta(
    selection_meta: dict[str, Any] | None,
    *,
    ticker: str = "",
    market: str = "",
) -> dict[str, Any]:
    """Return a non-mutating, per-ticker lifecycle projection."""
    if not isinstance(selection_meta, dict):
        return {}
    compact = {
        key: selection_meta[key]
        for key in _PERSISTED_SCALAR_KEYS
        if key in selection_meta
    }
    ticker_text = str(ticker or "").strip()
    wanted = _ticker_key(market, ticker_text)
    for key in ("trade_ready", "watchlist", "_raw_trade_ready"):
        values = list(selection_meta.get(key) or [])
        if ticker_text:
            values = [value for value in values if _ticker_key(market, value) == wanted]
        if values:
            compact[key] = values
    if ticker_text:
        for key in _PERSISTED_TICKER_MAP_KEYS:
            item = _map_item(selection_meta.get(key), market=market, ticker=ticker_text)
            if item is not None:
                raw_key, raw_value = item
                compact[key] = {raw_key: raw_value}
        for raw in list(selection_meta.get("_final_prompt_pool") or []):
            if not isinstance(raw, dict) or _ticker_key(market, raw.get("ticker")) != wanted:
                continue
            compact["_final_prompt_pool"] = [{
                key: raw[key] for key in _PERSISTED_PROMPT_ROW_KEYS if key in raw
            }]
            break
    return compact


class DecisionRegistry:
    def __init__(self, store: EventStore | None = None):
        self.store = store or EventStore()

    def register_trade_ready(
        self,
        *,
        market: str,
        runtime_mode: str,
        session_date: str,
        ticker: str,
        prompt_version: str,
        brain_snapshot_id: str,
        strategy_hint: str = "",
        timing_style: str = "momentum_timing",
        payload: dict[str, Any] | None = None,
        reuse_existing: bool = True,
    ) -> str:
        market_value = normalize_market(market)
        mode_value = normalize_runtime_mode(runtime_mode)
        ticker_value = str(ticker or "").strip().upper() if market_value == "US" else str(ticker or "").strip()
        if reuse_existing:
            existing = self.store.find_decision(
                market=market_value,
                runtime_mode=mode_value,
                session_date=session_date,
                ticker=ticker_value,
            )
            if existing:
                return str(existing["decision_id"])

        decision_id = make_decision_id(market_value, session_date, ticker_value)
        # 방어: 어떤 호출자가 전체 selection_meta를 넘겨도 영속 payload는 축소본으로 저장한다.
        payload = dict(payload or {})
        if isinstance(payload.get("selection_meta"), dict):
            payload["selection_meta"] = _compact_selection_meta(
                payload["selection_meta"], ticker=ticker_value, market=market_value
            )
        self.store.create_decision(
            decision_id=decision_id,
            market=market_value,
            runtime_mode=mode_value,
            session_date=session_date,
            ticker=ticker_value,
            prompt_version=prompt_version,
            brain_snapshot_id=brain_snapshot_id,
            strategy_hint=strategy_hint,
            timing_style=timing_style,
            status=LifecycleEventType.CLAUDE_TRADE_READY.value,
            payload=payload,
        )
        self.store.append(
            LifecycleEvent(
                event_type=LifecycleEventType.CLAUDE_TRADE_READY,
                market=market_value,
                runtime_mode=mode_value,
                session_date=session_date,
                ticker=ticker_value,
                decision_id=decision_id,
                prompt_version=prompt_version,
                brain_snapshot_id=brain_snapshot_id,
                payload={
                    **(payload or {}),
                    "strategy_hint": strategy_hint,
                    "timing_style": timing_style,
                },
            )
        )
        return decision_id

    def register_trade_ready_batch(
        self,
        *,
        market: str,
        runtime_mode: str,
        session_date: str,
        tickers: list[str],
        prompt_version: str,
        brain_snapshot_id: str,
        selection_meta: dict[str, Any] | None = None,
        reuse_existing: bool = True,
    ) -> dict[str, str]:
        selection_meta = selection_meta or {}
        # register_trade_ready에서 종목별 영속 allowlist로 축소한다.
        strategy_map = selection_meta.get("recommended_strategy") or {}
        timing_map = selection_meta.get("timing_style") or {}
        origin_map = selection_meta.get("_pathb_wait_origins") if isinstance(selection_meta.get("_pathb_wait_origins"), dict) else {}
        decision_ids: dict[str, str] = {}
        for ticker in tickers:
            strategy_hint = ""
            timing_style = "momentum_timing"
            ticker_key = str(ticker or "").strip().upper() if str(market or "").upper() == "US" else str(ticker or "").strip()
            if isinstance(strategy_map, dict):
                strategy_hint = str(strategy_map.get(ticker) or strategy_map.get(str(ticker).upper()) or "")
            if isinstance(timing_map, dict):
                timing_style = str(timing_map.get(ticker) or timing_map.get(str(ticker).upper()) or timing_style)
            ticker_origin = origin_map.get(ticker) or origin_map.get(ticker_key) or origin_map.get(str(ticker).upper()) or {}
            decision_ids[ticker] = self.register_trade_ready(
                market=market,
                runtime_mode=runtime_mode,
                session_date=session_date,
                ticker=ticker,
                prompt_version=prompt_version,
                brain_snapshot_id=brain_snapshot_id,
                strategy_hint=strategy_hint,
                timing_style=timing_style,
                payload={"selection_meta": selection_meta, "ticker_origin": dict(ticker_origin or {})},
                reuse_existing=reuse_existing,
            )
        return decision_ids

    def record_event(
        self,
        *,
        event_type: str | LifecycleEventType,
        market: str,
        runtime_mode: str,
        session_date: str,
        ticker: str,
        decision_id: str,
        prompt_version: str,
        brain_snapshot_id: str,
        execution_id: str | None = None,
        position_id: str | None = None,
        reason_code: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        return self.store.append(
            LifecycleEvent(
                event_type=event_type,
                market=market,
                runtime_mode=runtime_mode,
                session_date=session_date,
                ticker=ticker,
                decision_id=decision_id,
                prompt_version=prompt_version,
                brain_snapshot_id=brain_snapshot_id,
                execution_id=execution_id,
                position_id=position_id,
                reason_code=reason_code,
                payload=payload or {},
            )
        )

