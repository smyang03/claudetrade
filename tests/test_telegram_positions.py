from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import telegram_commander


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


if __name__ == "__main__":
    unittest.main()
