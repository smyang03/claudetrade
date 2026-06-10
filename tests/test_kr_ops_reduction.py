"""KR 운영 축소: 시장별 rescreen 주기 분리 검증."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import trading_bot


class RescreenIntervalPerMarketTests(unittest.TestCase):
    def test_kr_override_used_when_set(self):
        with mock.patch.dict(os.environ, {"KR_RESCREEN_INTERVAL_MIN": "240"}):
            self.assertEqual(trading_bot.TradingBot._rescreen_interval_min("KR"), 240)

    def test_us_falls_back_to_global_when_unset(self):
        env = dict(os.environ)
        env.pop("US_RESCREEN_INTERVAL_MIN", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                trading_bot.TradingBot._rescreen_interval_min("US"),
                trading_bot._RESCREEN_INTERVAL_MIN,
            )

    def test_invalid_override_falls_back_to_global(self):
        with mock.patch.dict(os.environ, {"KR_RESCREEN_INTERVAL_MIN": "abc"}):
            self.assertEqual(
                trading_bot.TradingBot._rescreen_interval_min("KR"),
                trading_bot._RESCREEN_INTERVAL_MIN,
            )

    def test_us_override_independent_of_kr(self):
        with mock.patch.dict(
            os.environ,
            {"KR_RESCREEN_INTERVAL_MIN": "240", "US_RESCREEN_INTERVAL_MIN": "90"},
        ):
            self.assertEqual(trading_bot.TradingBot._rescreen_interval_min("US"), 90)
            self.assertEqual(trading_bot.TradingBot._rescreen_interval_min("KR"), 240)


if __name__ == "__main__":
    unittest.main()
