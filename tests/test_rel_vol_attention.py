"""rel_vol 주의 배분 연결: early_judge 우선 정렬 + 프롬프트 rvol 표기."""

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
from minority_report import analysts


class EarlyJudgePrioritySortTests(unittest.TestCase):
    def _bot(self) -> TradingBot:
        bot = TradingBot.__new__(TradingBot)
        bot.today_judgment = {}
        bot.risk = type("Risk", (), {"positions": []})()
        return bot

    def test_us_rows_sorted_by_rel_vol_desc(self):
        bot = self._bot()
        rows = [
            {"ticker": "OLD1"},                              # 결측 → 0
            {"ticker": "SURGE", "rel_vol_shadow": 6.2},
            {"ticker": "OLD2"},                              # 결측 → 0
            {"ticker": "WARM", "rel_vol_shadow": 2.1},
        ]
        with patch.dict(os.environ, {"EARLY_JUDGE_REL_VOL_PRIORITY_ENABLED": "true"}, clear=False):
            out = bot._early_judge_priority_sort("US", rows, {})
        self.assertEqual([r["ticker"] for r in out], ["SURGE", "WARM", "OLD1", "OLD2"])

    def test_missing_rel_vol_preserves_original_order(self):
        bot = self._bot()
        rows = [{"ticker": "A"}, {"ticker": "B"}, {"ticker": "C"}]
        with patch.dict(os.environ, {"EARLY_JUDGE_REL_VOL_PRIORITY_ENABLED": "true"}, clear=False):
            out = bot._early_judge_priority_sort("US", rows, {})
        self.assertEqual([r["ticker"] for r in out], ["A", "B", "C"])

    def test_kr_and_disabled_passthrough(self):
        bot = self._bot()
        rows = [{"ticker": "A"}, {"ticker": "B", "rel_vol_shadow": 9.0}]
        out_kr = bot._early_judge_priority_sort("KR", list(rows), {})
        self.assertEqual([r["ticker"] for r in out_kr], ["A", "B"])
        with patch.dict(os.environ, {"EARLY_JUDGE_REL_VOL_PRIORITY_ENABLED": "false"}, clear=False):
            out_off = bot._early_judge_priority_sort("US", list(rows), {})
        self.assertEqual([r["ticker"] for r in out_off], ["A", "B"])


class PromptRvolTokenTests(unittest.TestCase):
    def _line(self, candidate, market="US"):
        return analysts._format_selection_candidate_line(candidate, market, [])

    def test_us_line_uses_rvol_and_drops_placeholder_vol(self):
        line = self._line({
            "ticker": "TGTX", "name": "TGTX", "change_rate": 12.6,
            "price": 50.0, "volume": 1_000_000, "vol_ratio": 1.0,
            "rel_vol_shadow": 6.2,
        })
        self.assertIn("rvol=6.2x", line)
        self.assertNotIn("vol=1.0x", line)

    def test_us_line_without_rel_vol_has_no_vol_token(self):
        line = self._line({
            "ticker": "AAPL", "name": "AAPL", "change_rate": 1.0,
            "price": 200.0, "volume": 1_000_000, "vol_ratio": 1.0,
        })
        self.assertNotIn("rvol=", line)
        self.assertNotIn("vol=1.0x", line)

    def test_kr_line_keeps_real_vol_ratio(self):
        line = self._line({
            "ticker": "005930", "name": "삼성전자", "change_rate": 2.0,
            "price": 70000, "volume": 1_000_000, "vol_ratio": 2.3,
        }, market="KR")
        self.assertIn("vol=2.3x", line)


if __name__ == "__main__":
    unittest.main()
