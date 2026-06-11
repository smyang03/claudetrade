"""세션 단위 evidence 품질 집계 (session_evidence_degraded) 검증."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_bot import TradingBot


class SessionEvidenceQualityTests(unittest.TestCase):
    def _bot(self):
        bot = TradingBot.__new__(TradingBot)
        bot._write_funnel_event = lambda *a, **k: None
        bot._runtime_float = lambda key, default: float(default)
        return bot

    def test_degraded_after_low_cumulative_ratio(self):
        bot = self._bot()
        # 요청 12 / 성공 3 = 25% < 50% floor, 최소 요청 10 충족
        bot._note_session_evidence_quality("KR", "2026-06-11", 6, 2)
        state = bot._note_session_evidence_quality("KR", "2026-06-11", 6, 1)
        self.assertTrue(state["degraded"])
        self.assertTrue(bot._session_evidence_degraded("KR"))

    def test_not_degraded_below_min_requested(self):
        bot = self._bot()
        state = bot._note_session_evidence_quality("KR", "2026-06-11", 4, 0)
        self.assertFalse(state["degraded"])

    def test_recovers_when_ratio_improves(self):
        bot = self._bot()
        bot._note_session_evidence_quality("US", "2026-06-11", 10, 2)
        self.assertTrue(bot._session_evidence_degraded("US"))
        for _ in range(4):
            bot._note_session_evidence_quality("US", "2026-06-11", 10, 10)
        self.assertFalse(bot._session_evidence_degraded("US"))

    def test_session_change_resets_counters(self):
        bot = self._bot()
        bot._note_session_evidence_quality("KR", "2026-06-10", 20, 2)
        self.assertTrue(bot._session_evidence_degraded("KR"))
        state = bot._note_session_evidence_quality("KR", "2026-06-11", 5, 5)
        self.assertFalse(state["degraded"])
        self.assertEqual(state["requested"], 5)

    def test_markets_tracked_independently(self):
        bot = self._bot()
        bot._note_session_evidence_quality("KR", "2026-06-11", 20, 1)
        self.assertTrue(bot._session_evidence_degraded("KR"))
        self.assertFalse(bot._session_evidence_degraded("US"))


if __name__ == "__main__":
    unittest.main()
