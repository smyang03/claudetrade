from __future__ import annotations

from pathlib import Path


def test_logger_reset_writes_error_log_to_test_runtime(tmp_path):
    from logger import get_trading_logger, reset_logger_runtime_dir_for_tests

    repo_error_dir = Path.cwd() / "logs" / "system"
    log_root = tmp_path / "logs"
    reset_logger_runtime_dir_for_tests(log_root, bot_mode="test")
    logger = get_trading_logger()

    logger.error("test logger isolation sentinel")

    temp_errors = list((log_root / "system").glob("test_error_*.log"))
    assert temp_errors
    assert "test logger isolation sentinel" in temp_errors[0].read_text(encoding="utf-8")
    if repo_error_dir.exists():
        for path in repo_error_dir.glob("error_*.log"):
            assert "test logger isolation sentinel" not in path.read_text(encoding="utf-8", errors="ignore")
