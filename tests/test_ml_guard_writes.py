from __future__ import annotations

from unittest.mock import Mock, patch

import trading_bot


def _bot() -> trading_bot.TradingBot:
    bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
    bot.is_paper = True
    bot.today_judgment = {
        "consensus": {"mode": "CAUTIOUS", "score": 0.42},
        "judgments": {
            "bull": {"stance": "BULLISH", "confidence": 0.7},
            "bear": {"stance": "NEUTRAL", "confidence": 0.4},
            "neutral": {"stance": "NEUTRAL", "confidence": 0.6},
        },
        "digest_raw": {"context": {"vix": 17.2, "usd_krw": 1360.5}},
    }
    bot._current_session_date_str = lambda market: "2026-05-27"
    return bot


def _capture_ml_rows():
    rows = []

    def write(row: dict) -> int:
        rows.append(row)
        return len(rows)

    return rows, write


def test_partial_data_guard_writes_blocked_decision_to_ml_db() -> None:
    bot = _bot()
    bot._bump_runtime_reason = Mock()
    bot._record_decision_event = Mock()
    rows, write = _capture_ml_rows()

    with patch.object(trading_bot, "_ML_DB_ENABLED", True), patch.object(
        trading_bot, "_ml_write", side_effect=write, create=True
    ):
        trading_bot.TradingBot._record_partial_data_entry_block(
            bot,
            "KR",
            "005930",
            "momentum",
            "partial_data_no_price_target",
            price_native=71200.0,
            signal_row={"rsi": 51.0, "macd": 1.2},
            decision={"allowed": False},
        )

    assert len(rows) == 1
    assert rows[0]["market"] == "KR"
    assert rows[0]["ticker"] == "005930"
    assert rows[0]["decision"] == "BLOCKED"
    assert rows[0]["block_reason"] == "partial_data_no_price_target"
    assert rows[0]["strategy_used"] == "momentum"
    assert rows[0]["mom_fired"] is True
    assert rows[0]["diag_json"]["stage"] == "partial_data_guard"


def test_new_buy_gate_writes_blocked_decision_to_ml_db() -> None:
    bot = _bot()
    bot._execution_safety_payload = Mock(return_value={})
    bot._bump_runtime_reason = Mock()
    bot._record_decision_event = Mock()
    bot._v2_record_lifecycle_event = Mock()
    bot._audit_emit_signal = Mock(return_value=101)
    bot._maybe_alert_stop_cluster_block = Mock()
    bot._maybe_alert_new_buy_block = Mock()
    rows, write = _capture_ml_rows()

    with patch.object(trading_bot, "_ML_DB_ENABLED", True), patch.object(
        trading_bot, "_ml_write", side_effect=write, create=True
    ):
        trading_bot.TradingBot._record_new_buy_block(
            bot,
            "US",
            "AAPL",
            "volatility_breakout",
            {"reason": "ANALYST_NEW_BUY_BLOCK", "scope": "market", "details": {"permission": "block"}},
            price_native=190.5,
            signal_row={"rsi": 62.0, "vol_ratio": 1.8},
        )

    assert len(rows) == 1
    assert rows[0]["market"] == "US"
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["decision"] == "BLOCKED"
    assert rows[0]["block_reason"] == "ANALYST_NEW_BUY_BLOCK"
    assert rows[0]["strategy_used"] == "volatility_breakout"
    assert rows[0]["vb_fired"] is True
    assert rows[0]["diag_json"]["permission"] == "block"
