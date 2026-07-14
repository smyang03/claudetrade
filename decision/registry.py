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


# CLAUDE_TRADE_READY 이벤트/결정 payload에 저장할 때 걷어내는 대용량 내부 작업 필드.
# 선정 파이프라인 내부용이며 영속 payload에서 다시 읽는 소비처가 없음을 실측 확인(2026-07-14).
# 각각 종목별 배치에서 selection_meta 전체가 종목마다 중복 저장돼 event_store를 1.4GB로
# 부풀렸다(CLAUDE_TRADE_READY 993건×평균 732KB=694MB). 아래 4개가 1732KB 중 1147KB(66%).
# - _post_open_features_by_ticker / _shadow_overlay_prompt_pool / _strategy_feasibility_by_ticker: 소비처 0건
# - _excluded_from_prompt: backfill_candidate_audit이 raw_calls 원문에서 읽으므로 event에서 제거해도 무해
_SELECTION_META_HEAVY_UNUSED_KEYS = (
    "_post_open_features_by_ticker",
    "_shadow_overlay_prompt_pool",
    "_strategy_feasibility_by_ticker",
    "_excluded_from_prompt",
)


def _compact_selection_meta(selection_meta: dict[str, Any] | None) -> dict[str, Any]:
    """영속 payload용 selection_meta 축소본(원본 비파괴).

    소비되지 않는 대용량 내부 필드만 제거한다. 파이프라인이 계속 쓰는 원본 dict는
    건드리지 않으므로 얕은 복사 후 지정 키만 뺀다.
    """
    if not isinstance(selection_meta, dict):
        return {}
    if not any(k in selection_meta for k in _SELECTION_META_HEAVY_UNUSED_KEYS):
        return selection_meta
    return {k: v for k, v in selection_meta.items() if k not in _SELECTION_META_HEAVY_UNUSED_KEYS}


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
            payload["selection_meta"] = _compact_selection_meta(payload["selection_meta"])
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
        # 종목마다 동일 selection_meta를 저장하므로 대용량 미소비 필드는 한 번만 걷어낸다.
        compact_meta = _compact_selection_meta(selection_meta)
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
                payload={"selection_meta": compact_meta, "ticker_origin": dict(ticker_origin or {})},
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

