from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import trading_bot
from tools.live_preflight import _pid_lock_check


def _runtime_path(root: Path):
    def _inner(*parts, make_parents=True):
        path = root.joinpath(*parts)
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return _inner


class BotPidLockTests(unittest.TestCase):
    def test_write_pid_lock_replaces_stale_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid_file = root / "state" / "live_trading_bot.pid"
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(json.dumps({"pid": 123456, "mode": "live"}), encoding="utf-8")

            with patch("trading_bot.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "trading_bot._process_alive",
                return_value=False,
            ):
                trading_bot._write_bot_pid_file(is_paper=False)

            data = json.loads(pid_file.read_text(encoding="utf-8"))
            self.assertEqual(data["pid"], os.getpid())
            self.assertEqual(data["mode"], "live")

    def test_write_pid_lock_blocks_active_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid_file = root / "state" / "live_trading_bot.pid"
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(json.dumps({"pid": 123456, "mode": "live"}), encoding="utf-8")

            with patch("trading_bot.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "trading_bot._process_alive",
                return_value=True,
            ):
                with self.assertRaises(RuntimeError):
                    trading_bot._write_bot_pid_file(is_paper=False)

    def test_clear_pid_lock_only_removes_owned_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid_file = root / "state" / "paper_trading_bot.pid"
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(json.dumps({"pid": 123456, "mode": "paper"}), encoding="utf-8")

            with patch("trading_bot.get_runtime_path", side_effect=_runtime_path(root)):
                trading_bot._clear_bot_pid_file(is_paper=True)
                self.assertTrue(pid_file.exists())
                pid_file.write_text(json.dumps({"pid": os.getpid(), "mode": "paper"}), encoding="utf-8")
                trading_bot._clear_bot_pid_file(is_paper=True)
                self.assertFalse(pid_file.exists())

    def test_preflight_pid_check_reports_stale_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "state" / "live_trading_bot.pid"
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(json.dumps({"pid": 123456, "mode": "live"}), encoding="utf-8")

            with patch("tools.live_preflight._pid_alive", return_value=False):
                check = _pid_lock_check("runtime.bot_pid_lock", pid_file, expected_mode="live")

            self.assertEqual(check.status, "WARN")
            self.assertTrue(check.data["auto_fix"])
            self.assertEqual(check.data["category"], "runtime_pid_lock")


if __name__ == "__main__":
    unittest.main()
