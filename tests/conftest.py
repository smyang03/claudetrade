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
