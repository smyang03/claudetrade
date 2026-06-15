from __future__ import annotations

import os
from unittest.mock import patch

from trading_bot import TradingBot


def _bot() -> TradingBot:
    # runtime_config 미설정 → _runtime_bool/_runtime_float가 os.environ fallback
    return TradingBot.__new__(TradingBot)


def test_us_riskon_toggle_on_shortens_to_early() -> None:
    bot = _bot()
    with patch.dict(os.environ, {"US_MOMENTUM_EARLY_ENTRY_ENABLED": "true", "MOMENTUM_EARLY_ENTRY_MIN_ELAPSED": "5"}, clear=False):
        assert bot._momentum_entry_min_elapsed("US", "MODERATE_BULL", 45.0) == 5.0


def test_kr_riskon_toggle_on_shortens() -> None:
    bot = _bot()
    with patch.dict(os.environ, {"KR_MOMENTUM_EARLY_ENTRY_ENABLED": "true", "MOMENTUM_EARLY_ENTRY_MIN_ELAPSED": "5"}, clear=False):
        assert bot._momentum_entry_min_elapsed("KR", "MILD_BULL", 45.0) == 5.0


def test_toggle_off_keeps_base() -> None:
    bot = _bot()
    with patch.dict(os.environ, {"US_MOMENTUM_EARLY_ENTRY_ENABLED": "false", "MOMENTUM_EARLY_ENTRY_ENABLED": "false"}, clear=False):
        assert bot._momentum_entry_min_elapsed("US", "MODERATE_BULL", 45.0) == 45.0


def test_non_bullish_mode_keeps_base() -> None:
    bot = _bot()
    with patch.dict(os.environ, {"US_MOMENTUM_EARLY_ENTRY_ENABLED": "true", "MOMENTUM_EARLY_ENTRY_MIN_ELAPSED": "5"}, clear=False):
        # NEUTRAL → RISK_ON 아님 → base 유지(약세 추격 방지)
        assert bot._momentum_entry_min_elapsed("US", "NEUTRAL", 45.0) == 45.0
        assert bot._momentum_entry_min_elapsed("US", "CAUTIOUS_BEAR", 45.0) == 45.0


def test_early_floored_to_5() -> None:
    bot = _bot()
    with patch.dict(os.environ, {"US_MOMENTUM_EARLY_ENTRY_ENABLED": "true", "MOMENTUM_EARLY_ENTRY_MIN_ELAPSED": "2"}, clear=False):
        assert bot._momentum_entry_min_elapsed("US", "AGGRESSIVE", 45.0) == 5.0


def test_never_extends_beyond_base() -> None:
    bot = _bot()
    with patch.dict(os.environ, {"US_MOMENTUM_EARLY_ENTRY_ENABLED": "true", "MOMENTUM_EARLY_ENTRY_MIN_ELAPSED": "100"}, clear=False):
        # early > base → base 유지(연장 금지)
        assert bot._momentum_entry_min_elapsed("US", "MODERATE_BULL", 45.0) == 45.0


def test_per_market_independent() -> None:
    bot = _bot()
    with patch.dict(
        os.environ,
        {
            "US_MOMENTUM_EARLY_ENTRY_ENABLED": "true",
            "KR_MOMENTUM_EARLY_ENTRY_ENABLED": "false",
            "MOMENTUM_EARLY_ENTRY_ENABLED": "false",
            "MOMENTUM_EARLY_ENTRY_MIN_ELAPSED": "5",
        },
        clear=False,
    ):
        assert bot._momentum_entry_min_elapsed("US", "MODERATE_BULL", 45.0) == 5.0
        assert bot._momentum_entry_min_elapsed("KR", "MODERATE_BULL", 45.0) == 45.0
