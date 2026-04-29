from __future__ import annotations

import unittest
from pathlib import Path

from tools.live_preflight import _token_checks


ROOT = Path(__file__).resolve().parents[1]


class LiveTokenBalanceTests(unittest.TestCase):
    def test_token_refresh_helper_is_wired_in_startup_balance(self) -> None:
        source = (ROOT / "trading_bot.py").read_text(encoding="utf-8", errors="replace")

        self.assertIn("_get_balance_with_token_refresh", source)
        self.assertIn("get_access_token(force_refresh=True)", source)
        self.assertIn("force_refresh=True", source)

    def test_live_token_file_is_parseable_when_present(self) -> None:
        checks = _token_checks("live")
        failing = [item for item in checks if item.status == "FAIL"]

        self.assertEqual(failing, [])


if __name__ == "__main__":
    unittest.main()
