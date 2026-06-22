"""v2_learning_performance.market_regime sync-layer fallback 회귀 테스트.

2026-06-23 무결성 감사: market_regime이 0/757. entry_market_regime이 휘발성 in-memory
pos에만 저장되고 durable store에 영속화되지 않아, rehydrate되는 청산 경로에서 CLOSED
payload까지 전달되지 못함. sync writer가 CLOSED payload 비었을 때 진입 시점 이벤트
(CLAUDE_PRICE_PLAN_CREATED / CLAUDE_TRADE_READY)의 consensus_mode를 fallback으로 읽어
복원한다. 위조하지 않는다(이벤트에도 없으면 빈 값).
"""
from tools.sync_v2_learning_performance import _entry_regime_from_events


def _ev(event_type, payload):
    return {"event_type": event_type, "payload": payload}


def test_regime_from_plan_created_consensus_mode():
    events = [
        _ev("CLAUDE_TRADE_READY", {"selection_meta": {"consensus_mode": "MILD_BULL"}}),
        _ev("CLAUDE_PRICE_PLAN_CREATED",
            {"plan": {"context_components_at_creation": {"consensus_mode": "MODERATE_BULL"}}}),
        _ev("FILLED", {"path_run_id": "p1"}),
        _ev("CLOSED", {"close_reason": "x"}),
    ]
    # PLAN_CREATED이 1순위
    assert _entry_regime_from_events(events) == "MODERATE_BULL"


def test_regime_falls_back_to_trade_ready_when_no_plan_mode():
    events = [
        _ev("CLAUDE_TRADE_READY", {"selection_meta": {"consensus_mode": "MILD_BEAR"}}),
        _ev("FILLED", {"path_run_id": "p1"}),
        _ev("CLOSED", {"close_reason": "x"}),
    ]
    assert _entry_regime_from_events(events) == "MILD_BEAR"


def test_regime_empty_when_no_consensus_mode_anywhere():
    events = [
        _ev("FILLED", {"path_run_id": "p1"}),
        _ev("CLOSED", {"close_reason": "x"}),
    ]
    assert _entry_regime_from_events(events) == ""


def test_regime_uppercased_and_stripped():
    events = [_ev("CLAUDE_PRICE_PLAN_CREATED",
                  {"plan": {"context_components_at_creation": {"consensus_mode": "  neutral  "}}})]
    assert _entry_regime_from_events(events) == "NEUTRAL"


def test_regime_ignores_malformed_payloads():
    events = [
        _ev("CLAUDE_PRICE_PLAN_CREATED", {"plan": "not-a-dict"}),
        _ev("CLAUDE_TRADE_READY", {"selection_meta": None}),
        _ev("CLAUDE_TRADE_READY", {"selection_meta": {"consensus_mode": "CAUTIOUS"}}),
    ]
    assert _entry_regime_from_events(events) == "CAUTIOUS"
