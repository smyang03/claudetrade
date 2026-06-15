from __future__ import annotations

from trading_bot import TradingBot


def _shadow_bot() -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.today_judgment = {
        "consensus": {"mode": "MODERATE_BULL"},
        "digest_raw": {"context": {"sp500": {"change_pct": 0.42}, "kospi": {"change_pct": -0.1}}},
    }
    bot._selection_ticker_key = lambda market, ticker: str(ticker or "").strip().upper()
    bot._funnel_calls = []
    bot._write_funnel_event = lambda event_type, market, payload: bot._funnel_calls.append(
        (event_type, market, payload)
    )
    return bot


def _candidate(price: float = 100.0) -> dict:
    return {
        "market": "US",
        "ticker": "ABCD",
        "row": {"ticker": "ABCD", "price": price, "previous_blocker": "pullback_wait"},
        "features": {"current_price": price},
    }


def test_shadow_records_wait_recheck(monkeypatch) -> None:
    monkeypatch.delenv("INTRADAY_ENTRY_SHADOW_MODE", raising=False)
    bot = _shadow_bot()
    normalized = {"action": "WAIT_RECHECK", "reason": "wait more", "confidence": 0.6}
    TradingBot._record_intraday_entry_shadow(
        bot, "US", "ABCD", normalized, _candidate(123.5), source="early_judge", applied=False
    )
    assert len(bot._funnel_calls) == 1
    event_type, market, payload = bot._funnel_calls[0]
    assert event_type == "intraday_entry_shadow"
    assert market == "US"
    assert payload["ticker"] == "ABCD"
    assert payload["action"] == "WAIT_RECHECK"
    assert payload["would_entry_price"] == 123.5
    assert payload["entry_market_regime"] == "MODERATE_BULL"
    assert payload["index_change_pct"] == 0.42
    assert payload["applied_live"] is False


def test_shadow_records_pullback_wait_and_reject(monkeypatch) -> None:
    monkeypatch.delenv("INTRADAY_ENTRY_SHADOW_MODE", raising=False)
    for action in ("PULLBACK_WAIT", "REJECT"):
        bot = _shadow_bot()
        normalized = {"action": action, "reference_price": 50.0, "confidence": 0.5}
        TradingBot._record_intraday_entry_shadow(
            bot, "US", "ABCD", normalized, _candidate(), source="planb_bridge", applied=False
        )
        assert len(bot._funnel_calls) == 1
        assert bot._funnel_calls[0][2]["action"] == action
        # reference_price 우선 사용
        assert bot._funnel_calls[0][2]["would_entry_price"] == 50.0


def test_shadow_skips_live_entry_actions(monkeypatch) -> None:
    monkeypatch.delenv("INTRADAY_ENTRY_SHADOW_MODE", raising=False)
    for action in ("BUY_READY", "PROBE_READY", ""):
        bot = _shadow_bot()
        normalized = {"action": action, "confidence": 0.7}
        TradingBot._record_intraday_entry_shadow(
            bot, "US", "ABCD", normalized, _candidate(), source="early_judge", applied=True
        )
        assert bot._funnel_calls == []


def test_shadow_off_mode_skips(monkeypatch) -> None:
    monkeypatch.setenv("INTRADAY_ENTRY_SHADOW_MODE", "off")
    bot = _shadow_bot()
    normalized = {"action": "WAIT_RECHECK", "confidence": 0.6}
    TradingBot._record_intraday_entry_shadow(
        bot, "US", "ABCD", normalized, _candidate(), source="early_judge", applied=False
    )
    assert bot._funnel_calls == []


def test_shadow_no_ticker_skips(monkeypatch) -> None:
    monkeypatch.delenv("INTRADAY_ENTRY_SHADOW_MODE", raising=False)
    bot = _shadow_bot()
    bot._selection_ticker_key = lambda market, ticker: ""
    normalized = {"action": "REJECT", "confidence": 0.5}
    TradingBot._record_intraday_entry_shadow(
        bot, "US", "", normalized, _candidate(), source="early_judge", applied=False
    )
    assert bot._funnel_calls == []
