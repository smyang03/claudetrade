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


def test_diagnostic_threshold_matches_gate_when_early_active() -> None:
    """진단(momentum_wait_window) 임계가 실제 게이트와 동일해야 한다.

    early-entry 활성(RISK_ON+toggle) 시 개장 5~45분 사이에서 momentum이 신호조건
    미충족으로 fire하지 않으면, 게이트는 이미 통과(elapsed>=5)했으므로 rejection은
    '시간 대기(momentum_wait)'가 아니라 신호조건 미충족으로 분류돼야 한다. 진단 블록이
    옛 wait window(45)를 쓰면 momentum_wait_window(10m<45m)가 남아 momentum_wait로
    오분류된다 — 이를 방지하기 위해 진단도 게이트와 동일 임계를 쓴다.
    """
    bot = _bot()
    with patch.dict(os.environ, {"US_MOMENTUM_EARLY_ENTRY_ENABLED": "true", "MOMENTUM_EARLY_ENTRY_MIN_ELAPSED": "5"}, clear=False):
        elapsed = 10.0
        threshold = bot._momentum_entry_min_elapsed("US", "MODERATE_BULL", 45.0)
        # 게이트와 동일 임계(5분) → elapsed 10분은 이미 통과 → wait-window 미발생
        assert threshold == 5.0
        assert not (elapsed < threshold)
    # 비강세에서는 base(45) 유지 → 10분은 여전히 시간 대기로 분류
    with patch.dict(os.environ, {"US_MOMENTUM_EARLY_ENTRY_ENABLED": "true", "MOMENTUM_EARLY_ENTRY_MIN_ELAPSED": "5"}, clear=False):
        threshold_neutral = bot._momentum_entry_min_elapsed("US", "NEUTRAL", 45.0)
        assert threshold_neutral == 45.0
        assert 10.0 < threshold_neutral
