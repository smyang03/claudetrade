from __future__ import annotations

import os
import tempfile


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
