"""mega_gap watch 전용 수용: 스크리너 격리 슬롯 + 진입성 액션 강등 가드."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kis_api
from trading_bot import TradingBot


class SelectUsMegaGapWatchTests(unittest.TestCase):
    def _gainer(self, ticker, chg, price=50.0, volume=2_000_000):
        return {"ticker": ticker, "change_rate": chg, "price": price, "volume": volume}

    def test_over_max_chg_rows_picked_with_tag(self):
        raw = [
            self._gainer("BIG1", 40.0),
            self._gainer("BIG2", 30.0),
            self._gainer("MID", 20.0),  # max_chg 이하 — 일반 경로 대상
        ]
        rows = kis_api._select_us_mega_gap_watch(
            raw, set(), min_price=5.0, max_chg=25.0, min_dollar_vol=15_000_000, slots=3
        )
        tickers = [r["ticker"] for r in rows]
        self.assertEqual(sorted(tickers), ["BIG1", "BIG2"])
        for r in rows:
            self.assertTrue(r["mega_gap_watch"])
            self.assertEqual(r["category"], "mega_gap")

    def test_slots_cap_by_dollar_volume(self):
        raw = [
            self._gainer("A", 30.0, price=50, volume=10_000_000),   # $500M
            self._gainer("B", 30.0, price=50, volume=5_000_000),    # $250M
            self._gainer("C", 30.0, price=50, volume=1_000_000),    # $50M
        ]
        rows = kis_api._select_us_mega_gap_watch(
            raw, set(), min_price=5.0, max_chg=25.0, min_dollar_vol=15_000_000, slots=2
        )
        self.assertEqual([r["ticker"] for r in rows], ["A", "B"])

    def test_seen_low_price_low_volume_excluded(self):
        raw = [
            self._gainer("SEEN", 30.0),
            self._gainer("CHEAP", 30.0, price=3.0),
            self._gainer("THIN", 30.0, price=50.0, volume=100),
        ]
        rows = kis_api._select_us_mega_gap_watch(
            raw, {"SEEN"}, min_price=5.0, max_chg=25.0, min_dollar_vol=15_000_000, slots=3
        )
        self.assertEqual(rows, [])

    def test_zero_slots_disabled(self):
        rows = kis_api._select_us_mega_gap_watch(
            [self._gainer("BIG", 30.0)], set(),
            min_price=5.0, max_chg=25.0, min_dollar_vol=15_000_000, slots=0
        )
        self.assertEqual(rows, [])


class MegaGapWatchGuardTests(unittest.TestCase):
    def _bot(self) -> TradingBot:
        bot = TradingBot.__new__(TradingBot)
        bot.enable_kr_momentum_shrink = True
        bot.enable_continuation_live = False
        bot.pending_orders = []
        bot.today_judgment = {}
        bot._data_insufficient_watch_tickers = {}
        bot.risk = type("Risk", (), {"positions": []})()
        return bot

    def _meta(self, action):
        return {
            "watchlist": ["GAPX", "NORM"],
            "trade_ready": ["GAPX"] if action in ("BUY_READY", "PROBE_READY") else [],
            "recommended_strategy": {"GAPX": "momentum", "NORM": "momentum"},
            "_final_prompt_pool": [
                {"ticker": "GAPX", "mega_gap_watch": True, "change_pct": 32.0},
                {"ticker": "NORM", "change_pct": 8.0},
            ],
            "candidate_actions": [
                {"ticker": "GAPX", "action": action, "strategy": "momentum",
                 "price_targets": {"buy_zone_low": 1, "buy_zone_high": 2}},
                {"ticker": "NORM", "action": "PULLBACK_WAIT", "strategy": "momentum"},
            ],
            "_candidate_action_routes": [
                {"ticker": "GAPX", "final_action": action, "strategy": "momentum"},
                {"ticker": "NORM", "final_action": "PULLBACK_WAIT", "strategy": "momentum"},
            ],
        }

    def test_pullback_wait_demoted_to_watch(self):
        bot = self._bot()
        meta = self._meta("PULLBACK_WAIT")
        with patch.dict(os.environ, {"US_MEGA_GAP_WATCH_GUARD_ENABLED": "true"}, clear=False):
            bot._apply_mega_gap_watch_guard("US", meta)
        gapx = next(a for a in meta["candidate_actions"] if a["ticker"] == "GAPX")
        self.assertEqual(gapx["action"], "WATCH")
        self.assertEqual(gapx["selection_quality_reason"], "mega_gap_watch_only")
        self.assertEqual(gapx["price_targets"], {})
        # 일반 후보의 PULLBACK_WAIT는 불변
        norm = next(a for a in meta["candidate_actions"] if a["ticker"] == "NORM")
        self.assertEqual(norm["action"], "PULLBACK_WAIT")

    def test_buy_ready_demoted_via_runtime_filter(self):
        bot = self._bot()
        meta = self._meta("BUY_READY")
        with patch.dict(os.environ, {"US_MEGA_GAP_WATCH_GUARD_ENABLED": "true"}, clear=False):
            normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="NEUTRAL")
        self.assertNotIn("GAPX", normalized["trade_ready"])
        self.assertEqual(
            normalized["_runtime_filtered_trade_ready"].get("GAPX"),
            "selection_quality:mega_gap_watch_only",
        )

    def test_guard_disabled_keeps_actions(self):
        bot = self._bot()
        meta = self._meta("PULLBACK_WAIT")
        with patch.dict(os.environ, {"US_MEGA_GAP_WATCH_GUARD_ENABLED": "false"}, clear=False):
            bot._apply_mega_gap_watch_guard("US", meta)
        gapx = next(a for a in meta["candidate_actions"] if a["ticker"] == "GAPX")
        self.assertEqual(gapx["action"], "PULLBACK_WAIT")


if __name__ == "__main__":
    unittest.main()
