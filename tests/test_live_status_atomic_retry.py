from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import trading_bot


def test_atomic_json_dump_with_retry_recovers_after_permission_error() -> None:
    calls: list[Path] = []

    def fake_dump(path, payload, *, indent=2, default=str):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError("locked")

    with patch.object(trading_bot, "_atomic_json_dump", side_effect=fake_dump), patch.object(
        trading_bot.time,
        "sleep",
        return_value=None,
    ) as sleep_mock:
        trading_bot._atomic_json_dump_with_retry(Path("state/live_status.json"), {"ok": True}, attempts=2)

    assert calls == [Path("state/live_status.json"), Path("state/live_status.json")]
    sleep_mock.assert_called_once()


def test_atomic_json_dump_with_retry_writes_last_good_after_retries_fail() -> None:
    calls: list[Path] = []

    def fake_dump(path, payload, *, indent=2, default=str):
        calls.append(path)
        if str(path).endswith(".last_good"):
            return
        raise PermissionError("locked")

    with patch.object(trading_bot, "_atomic_json_dump", side_effect=fake_dump), patch.object(
        trading_bot.time,
        "sleep",
        return_value=None,
    ):
        with pytest.raises(PermissionError):
            trading_bot._atomic_json_dump_with_retry(Path("state/live_status.json"), {"ok": True}, attempts=2)

    assert calls[-1] == Path("state/live_status.json.last_good")
