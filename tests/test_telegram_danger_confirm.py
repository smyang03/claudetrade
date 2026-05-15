import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import telegram_commander


class _Bot:
    def __init__(self):
        self.risk = SimpleNamespace(
            positions=[
                {"ticker": "005930", "qty": 10, "name": "Samsung"},
                {"ticker": "AAPL", "qty": 2, "name": "Apple"},
            ]
        )
        self.current_market = "KR"

    def _ticker_market(self, ticker):
        return "US" if str(ticker).isalpha() else "KR"


def _nonce(text: str) -> str:
    match = re.search(r"CONFIRM_[A-F0-9]+", text)
    assert match, text
    return match.group(0)


class TelegramDangerConfirmTest(unittest.TestCase):
    def setUp(self):
        with telegram_commander._pending_danger_lock:
            telegram_commander._pending_danger_confirms.clear()
        self.bot = _Bot()

    def test_closeall_first_input_prompts_without_execution(self):
        with patch("telegram_commander._cmd_closeall_snapshot") as closeall:
            response = telegram_commander._handle("/closeall", self.bot)

        closeall.assert_not_called()
        self.assertIn("확인 필요", response)
        self.assertIn("/closeall CONFIRM_", response)

    def test_closeall_confirm_executes_snapshot_once(self):
        first = telegram_commander._handle("/closeall", self.bot)
        nonce = _nonce(first)
        with patch("telegram_commander._cmd_closeall_snapshot", return_value="closed") as closeall:
            response = telegram_commander._handle(f"/closeall {nonce}", self.bot)

        self.assertEqual(response, "closed")
        closeall.assert_called_once()
        snapshot = closeall.call_args.args[1]
        self.assertEqual([item["ticker"] for item in snapshot["positions"]], ["005930", "AAPL"])

    def test_panic_first_input_does_not_call_v2_or_close(self):
        with patch("interface.v2_telegram.handle_v2_command") as v2, patch(
            "telegram_commander._cmd_closeall_snapshot"
        ) as closeall:
            response = telegram_commander._handle("/panic", self.bot)

        v2.assert_not_called()
        closeall.assert_not_called()
        self.assertIn("/panic CONFIRM_", response)

    def test_panic_confirm_runs_v2_then_snapshot_close(self):
        first = telegram_commander._handle("/panic", self.bot)
        nonce = _nonce(first)
        with patch("interface.v2_telegram.handle_v2_command", return_value="panic") as v2, patch(
            "telegram_commander._cmd_closeall_snapshot", return_value="closed"
        ) as closeall:
            response = telegram_commander._handle(f"/panic {nonce}", self.bot)

        v2.assert_called_once_with("/panic", self.bot)
        closeall.assert_called_once()
        self.assertEqual(response, "panic\n\nclosed")

    def test_close_confirm_requires_same_ticker(self):
        first = telegram_commander._handle("/close 005930", self.bot)
        nonce = _nonce(first)
        with patch("telegram_commander._cmd_close") as close:
            response = telegram_commander._handle(f"/close AAPL {nonce}", self.bot)

        close.assert_not_called()
        self.assertIn("현재 명령과 일치하지 않습니다", response)

    def test_expired_confirm_does_not_execute(self):
        first = telegram_commander._handle("/closeall", self.bot)
        nonce = _nonce(first)
        with telegram_commander._pending_danger_lock:
            telegram_commander._pending_danger_confirms[nonce]["expires_at"] = 0
        with patch("telegram_commander._cmd_closeall_snapshot") as closeall:
            response = telegram_commander._handle(f"/closeall {nonce}", self.bot)

        closeall.assert_not_called()
        self.assertIn("만료", response)

    def test_confirm_nonce_is_single_use(self):
        first = telegram_commander._handle("/closeall", self.bot)
        nonce = _nonce(first)
        with patch("telegram_commander._cmd_closeall_snapshot", return_value="closed"):
            self.assertEqual(telegram_commander._handle(f"/closeall {nonce}", self.bot), "closed")
        with patch("telegram_commander._cmd_closeall_snapshot") as closeall:
            response = telegram_commander._handle(f"/closeall {nonce}", self.bot)

        closeall.assert_not_called()
        self.assertIn("일치하지 않습니다", response)

    def test_halt_is_not_confirm_gated(self):
        with patch("interface.v2_telegram.handle_v2_command", return_value="HALT") as v2:
            response = telegram_commander._handle("/halt", self.bot)

        v2.assert_called_once_with("/halt", self.bot)
        self.assertEqual(response, "HALT")


if __name__ == "__main__":
    unittest.main()
