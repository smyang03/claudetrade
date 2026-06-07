from __future__ import annotations

import os
import sqlite3
import subprocess
import urllib.request
from pathlib import Path

import pytest

from runtime.rehearsal.context import (
    REPO_ROOT,
    RehearsalGuardError,
    apply_direct_path_overrides,
    assert_sandbox_runtime_root,
    create_rehearsal_context,
    install_no_network_guard,
    install_write_guard,
)


def test_sandbox_root_rejects_repo_live_paths(tmp_path: Path) -> None:
    with pytest.raises(RehearsalGuardError):
        assert_sandbox_runtime_root(REPO_ROOT)
    with pytest.raises(RehearsalGuardError):
        assert_sandbox_runtime_root(REPO_ROOT / "state")
    assert_sandbox_runtime_root(tmp_path / "sandbox")


def test_create_context_sets_live_fixture_env(tmp_path: Path) -> None:
    ctx = create_rehearsal_context(scenario="kr_patha_buy", runtime_root=tmp_path / "sandbox")
    assert ctx.profile == "live"
    assert ctx.backend == "fixture"
    assert os.environ["TRADING_BOT_MODE"] == "live"
    assert os.environ["OPS_REHEARSAL"] == "true"
    assert Path(os.environ["CLAUDETRADE_RUNTIME_DIR"]).resolve() == ctx.sandbox_root


def test_write_guard_blocks_repo_writes_and_allows_sandbox(tmp_path: Path) -> None:
    ctx = create_rehearsal_context(scenario="guard", runtime_root=tmp_path / "sandbox")
    with install_write_guard(ctx):
        allowed = ctx.sandbox_root / "state" / "allowed.txt"
        allowed.write_text("ok", encoding="utf-8")
        assert allowed.read_text(encoding="utf-8") == "ok"
        with pytest.raises(RehearsalGuardError):
            (REPO_ROOT / "state" / "ops_rehearsal_forbidden.txt").write_text("bad", encoding="utf-8")
        with pytest.raises(RehearsalGuardError):
            os.open(str(REPO_ROOT / "state" / "ops_rehearsal_forbidden.pid"), os.O_CREAT | os.O_WRONLY)
        with pytest.raises(RehearsalGuardError):
            sqlite3.connect(str(REPO_ROOT / "data" / "ops_rehearsal_forbidden.db"))


def test_no_network_guard_blocks_http_and_subprocess(tmp_path: Path) -> None:
    ctx = create_rehearsal_context(scenario="guard", runtime_root=tmp_path / "sandbox")
    with install_no_network_guard(ctx):
        with pytest.raises(RehearsalGuardError):
            urllib.request.urlopen("https://example.com")
        with pytest.raises(RehearsalGuardError):
            subprocess.run(["python", "--version"])


def test_direct_path_overrides_existing_modules(tmp_path: Path) -> None:
    ctx = create_rehearsal_context(scenario="guard", runtime_root=tmp_path / "sandbox")
    import kis_api
    import ticker_selection_db
    import intraday_strategy_db
    import strategy.param_tuner as param_tuner

    overrides = apply_direct_path_overrides(ctx)
    assert Path(ticker_selection_db.DB_PATH).resolve().is_relative_to(ctx.sandbox_root)
    assert Path(intraday_strategy_db.DB_PATH).resolve().is_relative_to(ctx.sandbox_root)
    assert Path(param_tuner._DB_PATH).resolve().is_relative_to(ctx.sandbox_root)
    assert Path(kis_api._EXCHANGE_CACHE_FILE).resolve().is_relative_to(ctx.sandbox_root)
    assert kis_api._US_EXCHANGE_CACHE.get("NVDA") == "NASD"
    assert overrides
