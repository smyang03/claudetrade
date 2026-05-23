from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from trading_bot import TradingBot


def _retry_bot() -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.today_judgment = {
        "consensus": {
            "new_buy_permission": "block",
            "unavailable_analyst_roles": ["macro"],
            "consensus_quality": "partial_consensus",
        }
    }
    bot._analyst_unavail_retry_at = {"KR": 0.0, "US": 1.0}
    bot._analyst_unavail_retry_count = {"KR": 0, "US": 0}
    bot.pending_orders = []
    bot.risk = SimpleNamespace(positions=[])
    bot.pathb = None
    return bot


def test_analyst_retry_skips_reinvoke_when_position_exists() -> None:
    bot = _retry_bot()
    bot.risk.positions = [{"market": "US", "ticker": "AAPL", "qty": 1}]
    calls: list[tuple[str, str]] = []
    bot._reinvoke_analysts = lambda market, trigger: calls.append((market, trigger))

    with patch("trading_bot.time.time", return_value=1000.0):
        TradingBot._maybe_handle_analyst_unavailability_retry(bot, "US")

    assert calls == []
    assert bot._analyst_unavail_retry_count["US"] == 0
    assert bot._analyst_unavail_retry_at["US"] == 1060.0


def test_analyst_retry_skips_reinvoke_when_pending_order_exists() -> None:
    bot = _retry_bot()
    bot.pending_orders = [{"market": "US", "ticker": "AAPL", "qty": 1, "order_no": "pending-1"}]
    calls: list[tuple[str, str]] = []
    bot._reinvoke_analysts = lambda market, trigger: calls.append((market, trigger))

    with patch("trading_bot.time.time", return_value=1000.0):
        TradingBot._maybe_handle_analyst_unavailability_retry(bot, "US")

    assert calls == []
    assert bot._analyst_unavail_retry_count["US"] == 0
    assert bot._analyst_unavail_retry_at["US"] == 1060.0


def test_analyst_retry_invokes_when_no_position_or_pending_order() -> None:
    bot = _retry_bot()
    calls: list[tuple[str, str]] = []
    bot._reinvoke_analysts = lambda market, trigger: calls.append((market, trigger))

    with patch("trading_bot.time.time", return_value=1000.0):
        TradingBot._maybe_handle_analyst_unavailability_retry(bot, "US")

    assert calls == [("US", "analyst_unavailability_retry")]
    assert bot._analyst_unavail_retry_count["US"] == 1


def test_analyst_retry_skips_reinvoke_when_broker_position_exists() -> None:
    bot = _retry_bot()
    bot.pathb = SimpleNamespace(broker_truth=object())
    bot._broker_truth_market_snapshot = lambda market, force=False, ttl_sec=None: {
        "missing": False,
        "stale": False,
        "error": "",
        "positions": [{"ticker": "AAPL", "qty": 1}],
        "open_orders": [],
    }
    calls: list[tuple[str, str]] = []
    bot._reinvoke_analysts = lambda market, trigger: calls.append((market, trigger))

    with patch("trading_bot.time.time", return_value=1000.0):
        TradingBot._maybe_handle_analyst_unavailability_retry(bot, "US")

    assert calls == []
    assert bot._analyst_unavail_retry_count["US"] == 0
    assert bot._analyst_unavail_retry_at["US"] == 1060.0


def test_analyst_retry_skips_reinvoke_when_broker_open_order_exists() -> None:
    bot = _retry_bot()
    bot.pathb = SimpleNamespace(broker_truth=object())
    bot._broker_truth_market_snapshot = lambda market, force=False, ttl_sec=None: {
        "missing": False,
        "stale": False,
        "error": "",
        "positions": [],
        "open_orders": [{"ticker": "AAPL", "remaining_qty": 1, "order_no": "broker-1"}],
    }
    calls: list[tuple[str, str]] = []
    bot._reinvoke_analysts = lambda market, trigger: calls.append((market, trigger))

    with patch("trading_bot.time.time", return_value=1000.0):
        TradingBot._maybe_handle_analyst_unavailability_retry(bot, "US")

    assert calls == []
    assert bot._analyst_unavail_retry_count["US"] == 0
    assert bot._analyst_unavail_retry_at["US"] == 1060.0


def test_analyst_retry_skips_reinvoke_when_stale_broker_snapshot_has_exposure() -> None:
    bot = _retry_bot()
    bot.pathb = SimpleNamespace(broker_truth=object())
    bot._broker_truth_market_snapshot = lambda market, force=False, ttl_sec=None: {
        "missing": False,
        "stale": True,
        "error": "ttl",
        "positions": [{"ticker": "AAPL", "qty": 1}],
        "open_orders": [],
    }
    calls: list[tuple[str, str]] = []
    bot._reinvoke_analysts = lambda market, trigger: calls.append((market, trigger))

    with patch("trading_bot.time.time", return_value=1000.0):
        TradingBot._maybe_handle_analyst_unavailability_retry(bot, "US")

    assert calls == []
    assert bot._analyst_unavail_retry_count["US"] == 0
    assert bot._analyst_unavail_retry_at["US"] == 1060.0
