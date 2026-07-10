from __future__ import annotations

from unittest.mock import patch
from types import SimpleNamespace

from runtime.us_swing_order_bridge import _current_us_swing_open_slots
from trading_bot import TradingBot


def test_us_swing_handoff_is_not_imported_or_run_when_disabled() -> None:
    bot = TradingBot.__new__(TradingBot)
    bot._runtime_bool = lambda key, default=False: False

    with patch(
        "runtime.us_swing_order_bridge.run_us_swing_handoff",
        side_effect=AssertionError("disabled bridge must not run"),
    ) as bridge:
        result = bot._maybe_run_us_swing_order_handoff("US")

    assert result == {"status": "DISABLED", "reason": "handoff_disabled"}
    bridge.assert_not_called()


def test_us_swing_handoff_runs_only_for_us_when_enabled() -> None:
    bot = TradingBot.__new__(TradingBot)
    bot._runtime_bool = lambda key, default=False: True

    with patch(
        "runtime.us_swing_order_bridge.run_us_swing_handoff",
        return_value={"status": "EVALUATED"},
    ) as bridge:
        kr_result = bot._maybe_run_us_swing_order_handoff("KR")
        us_result = bot._maybe_run_us_swing_order_handoff("US")

    assert kr_result == {"status": "SKIPPED", "reason": "non_us_market"}
    assert us_result == {"status": "EVALUATED"}
    bridge.assert_called_once_with(bot)


def test_us_swing_open_slots_deduplicate_position_and_pending_order() -> None:
    bot = SimpleNamespace(
        risk=SimpleNamespace(positions=[{
            "market": "US", "ticker": "SMCI", "source_strategy": "us_swing_5d"
        }]),
        pending_orders=[
            {"market": "US", "ticker": "SMCI", "source_strategy": "us_swing_5d"},
            {"market": "US", "ticker": "OTHER", "source_strategy": "momentum"},
        ],
    )

    assert _current_us_swing_open_slots(bot) == 1
