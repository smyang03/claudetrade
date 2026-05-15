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
            payload=payload or {},
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

