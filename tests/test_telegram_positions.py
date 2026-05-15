from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import telegram_commander


def _callback_values(markup: dict) -> list[str]:
    return [
        str(button.get("callback_data") or "")
        for row in markup.get("inline_keyboard", [])
        for button in row
    ]


class TelegramPositionsTests(unittest.TestCase):
    def test_positions_merges_local_fallback_for_missing_broker_market(self) -> None:
        bot = SimpleNamespace(
            _mode="live",
            risk=SimpleNamespace(
                positions=[
                    {
                        "market": "US",
                        "ticker": "AAPL",
                        "name": "Apple",
                        "qty": 2,
                        "entry": 200.0,
                        "current_price": 210.0,
                    }
                ]
            ),
        )
        summary = {
            "broker_truth": {
                "markets": {
                    "KR": {"missing": False, "stale": False, "last_success_at": "2026-04-29T09:00:00+09:00"},
                    "US": {"missing": True, "stale": True, "last_success_at": ""},
                }
            },
            "positions": [
                {
                    "market": "KR",
                    "ticker": "005930",
                    "name": "Samsung",
                    "qty": 1,
                    "entry": 70000,
                    "current_price": 71000,
                    "source": "broker_truth",
                }
            ],
        }

        with patch("interface.v2_ops_summary.build_v2_ops_summary", return_value=summary):
            message = telegram_commander._cmd_positions_from_broker_truth(bot)

        self.assertIn("005930", message)
        self.assertIn("AAPL", message)
        self.assertIn("broker_truth", message)
        self.assertIn("local_fallback", message)
        self.assertIn("Local fallback: US", message)

    def test_stop_cluster_command_reports_and_resets_market_counter_only(self) -> None:
        bot = SimpleNamespace(
            current_market="KR",
            claude_control={},
            _daily_sl_count={"KR": 4, "US": 0},
            _v2_same_day_stop_tickers={"KR": {"078150"}, "US": set()},
        )

        def status_payload(market: str) -> dict:
            count = bot._daily_sl_count.get(market, 0)
            stopped = sorted(bot._v2_same_day_stop_tickers.get(market, set()))
            return {
                "allowed": count < 4,
                "blocked": count >= 4,
                "reason": "STOP_CLUSTER_MARKET_BLOCK" if count >= 4 else "",
                "scope": "market" if count >= 4 else "",
                "daily_stop_count": count,
                "hard_block_count": 4,
                "disaster_block_count": 6,
                "first_stop_freeze_minutes": 30,
                "stopped_tickers": stopped,
            }

        def consume(market: str) -> None:
            bot._daily_sl_count[market] = 0
            bot.claude_control["pending_stop_cluster_reset"] = None
            bot.claude_control["last_stop_cluster_reset_market"] = market
            bot.claude_control["last_stop_cluster_reset_count_before"] = 4

        bot._stop_cluster_status_payload = status_payload
        bot._refresh_claude_control = lambda: None
        bot._save_claude_control = lambda: None
        bot._consume_pending_stop_cluster_reset = consume

        status = telegram_commander._handle("/stop_cluster KR", bot)
        reset = telegram_commander._handle("/stop_cluster_reset KR", bot)

        self.assertIn("KR 4/4", status)
        self.assertIn("078150", status)
        self.assertIn("KR 0/4", reset)
        self.assertIn("078150", reset)
        self.assertEqual(bot._daily_sl_count["KR"], 0)
        self.assertEqual(bot._v2_same_day_stop_tickers["KR"], {"078150"})

    def test_claude_command_uses_explicit_active_market(self) -> None:
        calls: list[tuple[str, str]] = []
        bot = SimpleNamespace(
            session_active=True,
            current_market="US",
            today_judgment={"market": "US", "consensus": {"mode": "NEUTRAL"}, "digest_prompt": "digest"},
            _reinvoke_analysts=lambda market, trigger: calls.append((market, trigger)),
        )

        with patch("telegram_commander._send"):
            message = telegram_commander._handle("/claude US", bot)

        self.assertEqual(calls[0][0], "US")
        self.assertEqual(calls[0][1], "수동 명령: /claude")
        self.assertIn("US", message)
        self.assertIn("재판단 완료", message)
        self.assertNotIn("complete", message)

    def test_claude_command_rejects_non_active_market(self) -> None:
        calls: list[tuple[str, str]] = []
        bot = SimpleNamespace(
            session_active=True,
            current_market="US",
            today_judgment={"market": "US", "consensus": {"mode": "NEUTRAL"}, "digest_prompt": "digest"},
            _reinvoke_analysts=lambda market, trigger: calls.append((market, trigger)),
        )

        message = telegram_commander._handle("/claude KR", bot)

        self.assertEqual(calls, [])
        self.assertIn("US", message)

    def test_rescreen_command_uses_explicit_active_market(self) -> None:
        calls: list[str] = []
        bot = SimpleNamespace(
            session_active=True,
            current_market="KR",
            today_judgment={"market": "KR", "consensus": {"mode": "NEUTRAL"}},
            manual_rescreen=lambda market: calls.append(market) or ["005930"],
        )

        with patch("telegram_commander._send"):
            message = telegram_commander._handle("/rescreen KR", bot)

        self.assertEqual(calls, ["KR"])
        self.assertIn("005930", message)

    def test_claude_command_rejects_missing_judgment_payload(self) -> None:
        calls: list[tuple[str, str]] = []
        bot = SimpleNamespace(
            session_active=True,
            current_market="US",
            today_judgment={},
            _reinvoke_analysts=lambda market, trigger: calls.append((market, trigger)),
        )

        message = telegram_commander._handle("/claude US", bot)

        self.assertEqual(calls, [])
        self.assertIn("판단", message)
        self.assertNotIn("judgment", message)

    def test_claude_command_rejects_invalid_market_without_internal_key(self) -> None:
        bot = SimpleNamespace(
            session_active=True,
            current_market="US",
            today_judgment={"market": "US", "consensus": {"mode": "NEUTRAL"}, "digest_prompt": "digest"},
            _reinvoke_analysts=lambda market, trigger: None,
        )

        message = telegram_commander._handle("/claude JP", bot)

        self.assertIn("명령 시장 인자가 잘못되었습니다", message)
        self.assertNotIn("unsupported_market", message)

    def test_main_menu_payload_exposes_hierarchical_buttons(self) -> None:
        text, markup = telegram_commander._handle_callback_data("ct:menu:main", SimpleNamespace())
        callbacks = _callback_values(markup)

        self.assertIn("운영 메뉴", text)
        self.assertIn("ct:menu:info", callbacks)
        self.assertIn("ct:menu:positions", callbacks)
        self.assertIn("ct:menu:ops", callbacks)
        self.assertIn("ct:menu:settings", callbacks)

    def test_command_callback_reuses_existing_handler(self) -> None:
        bot = SimpleNamespace()
        with patch("telegram_commander._handle", return_value="STATUS") as handle:
            text, markup = telegram_commander._handle_callback_data("ct:cmd:status", bot)

        self.assertEqual(text, "STATUS")
        handle.assert_called_once_with("/status", bot)
        self.assertIn("ct:menu:info", _callback_values(markup))

    def test_positions_menu_adds_close_buttons(self) -> None:
        bot = SimpleNamespace(
            risk=SimpleNamespace(
                positions=[
                    {"ticker": "005930", "qty": 10, "name": "Samsung"},
                    {"ticker": "AAPL", "qty": 2, "name": "Apple"},
                ]
            )
        )

        with patch("telegram_commander._safe_handle", return_value="POSITIONS"):
            text, markup = telegram_commander._handle_callback_data("ct:menu:positions", bot)

        callbacks = _callback_values(markup)
        self.assertEqual(text, "POSITIONS")
        self.assertIn("ct:danger:close:005930", callbacks)
        self.assertIn("ct:danger:close:AAPL", callbacks)
        self.assertIn("ct:danger:closeall", callbacks)


if __name__ == "__main__":
    unittest.main()
