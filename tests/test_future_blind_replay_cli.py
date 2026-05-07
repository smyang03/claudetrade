from __future__ import annotations

import unittest

from tools.future_blind_replay import build_replay


class FutureBlindReplayCliTests(unittest.TestCase):
    def test_decision_at_5m_cannot_use_30m_return(self) -> None:
        replay = build_replay(
            {
                "session_date": "2026-05-06",
                "candidates": [
                    {
                        "ticker": "001440",
                        "post_open_5m_return_pct": 2.5,
                        "post_open_30m_return_pct": 8.0,
                        "post_open_360m_return_pct": 14.0,
                    }
                ],
            },
            market="KR",
            decision_minutes=5,
            min_ret_5m=1.0,
            min_ret_30m=None,
        )

        self.assertEqual(replay["selected_count"], 1)
        self.assertIsNone(replay["rows"][0]["known_returns"]["ret_30m_pct"])

    def test_30m_rule_waits_until_30m_known(self) -> None:
        replay = build_replay(
            {
                "session_date": "2026-05-06",
                "candidates": [
                    {
                        "ticker": "001440",
                        "post_open_5m_return_pct": 2.5,
                        "post_open_30m_return_pct": 1.0,
                        "post_open_360m_return_pct": 14.0,
                    }
                ],
            },
            market="KR",
            decision_minutes=30,
            min_ret_5m=1.0,
            min_ret_30m=2.0,
        )

        self.assertEqual(replay["selected_count"], 0)
        self.assertEqual(replay["rows"][0]["known_returns"]["ret_30m_pct"], 1.0)


if __name__ == "__main__":
    unittest.main()
