"""시장 급반전 감지 shadow 검증."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_bot import TradingBot


class MarketSharpReversalTests(unittest.TestCase):
    def _bot(self, changes):
        bot = TradingBot.__new__(TradingBot)
        seq = iter(changes)
        bot._get_market_change_pct = lambda market, digest=None: next(seq)
        bot._current_session_date_str = lambda market: "2026-06-11"
        bot._write_funnel_event = lambda *a, **k: None
        return bot

    def test_drop_from_session_peak_triggers(self):
        # +0.8% 고점 → -0.3% = 고점 대비 -1.1%p (기준 -1.0%p 초과)
        bot = self._bot([0.8, -0.3])
        with patch.dict(os.environ, {"MARKET_SHARP_REVERSAL_GUARD_MODE": "shadow"}):
            first = bot._update_market_sharp_reversal_shadow("US")
            second = bot._update_market_sharp_reversal_shadow("US")
        self.assertFalse(first["triggered"])
        self.assertTrue(second["triggered"])
        self.assertAlmostEqual(second["drop_from_peak_pct"], -1.1, places=3)

    def test_absolute_drop_triggers_without_peak(self):
        bot = self._bot([-1.6])
        with patch.dict(os.environ, {"MARKET_SHARP_REVERSAL_GUARD_MODE": "shadow"}):
            result = bot._update_market_sharp_reversal_shadow("US")
        self.assertTrue(result["triggered"])

    def test_mild_drift_does_not_trigger(self):
        bot = self._bot([0.5, 0.1, -0.2])
        with patch.dict(os.environ, {"MARKET_SHARP_REVERSAL_GUARD_MODE": "shadow"}):
            results = [bot._update_market_sharp_reversal_shadow("US") for _ in range(3)]
        self.assertFalse(any(r["triggered"] for r in results))

    def test_disabled_mode_skips(self):
        bot = self._bot([-5.0])
        with patch.dict(os.environ, {"MARKET_SHARP_REVERSAL_GUARD_MODE": "off"}):
            result = bot._update_market_sharp_reversal_shadow("US")
        self.assertFalse(result["triggered"])

    def test_no_index_data_is_safe(self):
        bot = self._bot([None])
        with patch.dict(os.environ, {"MARKET_SHARP_REVERSAL_GUARD_MODE": "shadow"}):
            result = bot._update_market_sharp_reversal_shadow("US")
        self.assertFalse(result["triggered"])
        self.assertEqual(result["reason"], "no_index_data")


if __name__ == "__main__":
    unittest.main()
