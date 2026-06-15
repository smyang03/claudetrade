from __future__ import annotations

from trading_bot import TradingBot


def _bot() -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot._selection_ticker_key = lambda market, ticker: str(ticker or "").strip().upper()
    return bot


def _meta(actions: list[dict]) -> dict:
    return {"candidate_actions": actions}


def test_sort_orders_by_confidence_desc() -> None:
    bot = _bot()
    meta = _meta([
        {"ticker": "AAA", "confidence": 0.40},
        {"ticker": "BBB", "confidence": 0.90},
        {"ticker": "CCC", "confidence": 0.65},
    ])
    out = bot._sort_trade_ready_by_priority("US", ["AAA", "BBB", "CCC"], meta)
    assert out == ["BBB", "CCC", "AAA"]


def test_sort_is_stable_for_ties() -> None:
    bot = _bot()
    meta = _meta([
        {"ticker": "AAA", "confidence": 0.5},
        {"ticker": "BBB", "confidence": 0.5},
        {"ticker": "CCC", "confidence": 0.5},
    ])
    out = bot._sort_trade_ready_by_priority("US", ["CCC", "AAA", "BBB"], meta)
    assert out == ["CCC", "AAA", "BBB"]  # 동률 → 입력(Claude) 순서 유지


def test_missing_score_sinks_but_preserves_order() -> None:
    bot = _bot()
    meta = _meta([
        {"ticker": "AAA", "confidence": 0.8},
        {"ticker": "CCC", "confidence": 0.3},
    ])
    # BBB는 점수 없음 → 0.0으로 뒤로
    out = bot._sort_trade_ready_by_priority("US", ["AAA", "BBB", "CCC"], meta)
    assert out == ["AAA", "CCC", "BBB"]


def test_no_actions_returns_input_order() -> None:
    bot = _bot()
    out = bot._sort_trade_ready_by_priority("US", ["AAA", "BBB"], {"candidate_actions": []})
    assert out == ["AAA", "BBB"]


def test_handles_t_key_and_bad_items() -> None:
    bot = _bot()
    meta = _meta([
        {"t": "AAA", "confidence": 0.2},
        "not_a_dict",
        {"ticker": "BBB", "confidence": "bad"},
        {"ticker": "CCC", "confidence": 0.9},
    ])
    out = bot._sort_trade_ready_by_priority("US", ["AAA", "BBB", "CCC"], meta)
    assert out[0] == "CCC"  # 최고 confidence 선두
    assert set(out) == {"AAA", "BBB", "CCC"}
