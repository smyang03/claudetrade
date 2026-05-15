from __future__ import annotations

import unittest

from interface.v2_telegram import handle_v2_command


class _PathB:
    def __init__(self) -> None:
        self.enabled = True
        self.killed = False
        self.closed = 0
        self.closed_markets = []

    def status(self) -> dict:
        return {
            "enabled": self.enabled and not self.killed,
            "operator_enabled": self.enabled,
            "emergency_disabled": self.killed,
            "mode": "min_size_live",
            "runtime_mode": "live",
            "fixed_order_krw": 100000,
            "max_positions": 1,
            "max_daily_entries": 1,
            "min_confidence": 0.5,
        }

    def set_enabled(self, enabled: bool, **kwargs):
        self.enabled = enabled

    def emergency_disable(self, **kwargs):
        self.enabled = False
        self.killed = True

    def close_all_open(self, market: str, **kwargs) -> int:
        self.closed += 1
        self.closed_markets.append(market)
        return 1


class _Bot:
    def __init__(self) -> None:
        self.pathb = _PathB()
        self.current_market = "KR"


class PathBControlTests(unittest.TestCase):
    def test_pathb_telegram_controls(self) -> None:
        bot = _Bot()
        self.assertIn("enabled: True", handle_v2_command("/pathb_status", bot))
        self.assertIn("OFF", handle_v2_command("/pathb_off", bot))
        self.assertFalse(bot.pathb.enabled)
        self.assertIn("ON", handle_v2_command("/pathb_on", bot))
        self.assertTrue(bot.pathb.enabled)
        self.assertIn("close-all", handle_v2_command("/pathb_closeall", bot))
        self.assertEqual(bot.pathb.closed, 1)
        self.assertEqual(bot.pathb.closed_markets, ["KR"])
        self.assertIn("KILL", handle_v2_command("/pathb_kill", bot))
        self.assertTrue(bot.pathb.killed)

    def test_pathb_closeall_uses_market_override(self) -> None:
        bot = _Bot()
        bot.current_market = "US"

        response = handle_v2_command("/pathb_closeall", bot, market_override="KR")

        self.assertIn("KR B플랜 전체 청산 요청", response)
        self.assertEqual(bot.pathb.closed_markets, ["KR"])


if __name__ == "__main__":
    unittest.main()
