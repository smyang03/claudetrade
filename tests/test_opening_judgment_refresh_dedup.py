"""개장 판단 refresh 중복 방지 검증."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_bot import TradingBot, KST


class OpeningJudgmentRefreshDedupTests(unittest.TestCase):
    def _bot(self, *, last_judgment_min_ago=None):
        bot = TradingBot.__new__(TradingBot)
        bot.today_judgment = {"judgment_context_basis": {"phase": "preopen_digest"}}
        bot._digest_payload_built_before_open = lambda market, payload=None: True
        bot._market_after_open_refresh_time = lambda market: True
        bot._current_session_date_str = lambda market: "2026-06-11"
        bot._runtime_float = lambda key, default: float(default)
        bot._reinvoke_analysts = MagicMock()
        bot._opening_judgment_refresh_attempted = set()
        if last_judgment_min_ago is not None:
            now = datetime.now(KST).replace(tzinfo=None)
            bot._last_full_judgment_at = {"KR": now - timedelta(minutes=last_judgment_min_ago)}
        return bot

    def test_skips_refresh_when_judgment_is_fresh(self):
        bot = self._bot(last_judgment_min_ago=2)
        bot._maybe_refresh_opening_judgment("KR")
        bot._reinvoke_analysts.assert_not_called()

    def test_runs_refresh_when_judgment_is_old(self):
        bot = self._bot(last_judgment_min_ago=25)
        bot._maybe_refresh_opening_judgment("KR")
        bot._reinvoke_analysts.assert_called_once()

    def test_runs_refresh_without_prior_judgment_record(self):
        bot = self._bot()
        bot._maybe_refresh_opening_judgment("KR")
        bot._reinvoke_analysts.assert_called_once()


if __name__ == "__main__":
    unittest.main()
