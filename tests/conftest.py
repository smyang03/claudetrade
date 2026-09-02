from __future__ import annotations

import os
import tempfile

import pytest


_RESTORE_ENV_KEYS = (
    "AUTO_SELL_REVIEW_FORCE_SELL_LOSS_PCT",
    "CLAUDE_REVIEW_ALL_AUTOMATED_SELLS",
    "HOLD_ADVISOR_SOFT_CACHE_ENABLED",
    "KR_DAILY_ENTRY_CAP",
    "US_DAILY_ENTRY_CAP",
    "V2_MAX_DAILY_ENTRIES",
)


def pytest_configure(config) -> None:  # pragma: no cover - pytest hook
    os.environ.setdefault("TRADING_BOT_MODE", "test")
    if os.environ.get("CLAUDETRADE_KEEP_REPO_RUNTIME_FOR_TESTS", "").strip().lower() in {"1", "true", "yes", "y", "on"}:
        return
    os.environ.setdefault("CLAUDETRADE_RUNTIME_DIR", tempfile.mkdtemp(prefix="claudetrade_pytest_"))
    try:
        import runtime_paths

        runtime_paths._RUNTIME_ROOT = None
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _restore_live_control_env_keys():  # pragma: no cover - test hygiene
    snapshot = {key: os.environ.get(key) for key in _RESTORE_ENV_KEYS}
    yield
    for key, value in snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(autouse=True)
def _no_real_telegram_in_tests(monkeypatch):  # pragma: no cover - test hygiene
    """테스트가 실제 텔레그램을 쏘지 못하게 한다 (2026-09-02 사고: REHEARSAL 통보 테스트가
    운영자 채팅에 실제 메시지를 보냄). 토큰을 비우면 telegram_reporter.send()가 즉시 False.
    send() 자체를 검증하는 테스트는 TOKEN/CHAT_ID를 명시적으로 다시 patch한다."""
    import sys as _sys
    monkeypatch.setenv("TELEGRAM_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    mod = _sys.modules.get("telegram_reporter")
    if mod is not None:
        monkeypatch.setattr(mod, "TOKEN", "", raising=False)
        monkeypatch.setattr(mod, "CHAT_ID", "", raising=False)
    yield
