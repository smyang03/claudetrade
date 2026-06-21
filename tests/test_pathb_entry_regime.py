"""entry_market_regime 배선 복구 회귀 테스트 (2026-06-21).

진단: v2_learning_performance.market_regime이 0/304로 전부 빈 값. 근본 원인은
today_judgment가 시장마다 덮어쓰이고 사이클마다 {}로 리셋되는 단일 dict라, PathB가
진입/청산 시점에 읽으면 비어있던 것. 수정: consensus 확정 finalizer(_apply_consensus_guards)
에서 안정 per-market 캐시(market_consensus_mode)를 seed하고, PathB는 측정 전용
_pathb_entry_market_regime로 그 캐시를 1순위로 읽는다. 라이브 게이팅(_pathb_consensus_mode)은 불변.
"""
import tempfile
import unittest
from pathlib import Path

import trading_bot as tb_module
from trading_bot import TradingBot
from runtime.pathb_runtime import PathBRuntime
from lifecycle.event_store import EventStore


class _Bot:
    def __init__(self) -> None:
        self.is_paper = False
        self.token = ""
        self.today_judgment = {}
        self.market_consensus_mode = {}


def _runtime(tmp: str) -> PathBRuntime:
    bot = _Bot()
    store = EventStore(Path(tmp) / "events.db")
    rt = PathBRuntime(bot, is_paper=False, store=store)
    rt.bot = bot
    return rt


class EntryMarketRegimeConsumerTests(unittest.TestCase):
    def test_reads_stable_cache_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = _runtime(tmp)
            rt.bot.market_consensus_mode = {"US": "MODERATE_BULL", "KR": "MILD_BEAR"}
            # today_judgment 비어있어도(=PathB가 읽는 실제 상황) 캐시에서 잡는다
            self.assertEqual(rt._pathb_entry_market_regime("US"), "MODERATE_BULL")
            self.assertEqual(rt._pathb_entry_market_regime("KR"), "MILD_BEAR")

    def test_per_market_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = _runtime(tmp)
            rt.bot.market_consensus_mode = {"US": "MODERATE_BULL"}
            self.assertEqual(rt._pathb_entry_market_regime("US"), "MODERATE_BULL")
            # KR은 캐시 없음 → today_judgment도 없음 → 빈 값(위조 금지)
            self.assertEqual(rt._pathb_entry_market_regime("KR"), "")

    def test_fallback_to_today_judgment_when_cache_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = _runtime(tmp)
            rt.bot.market_consensus_mode = {}
            rt.bot.today_judgment = {"market": "US", "consensus": {"mode": "CAUTIOUS"}}
            self.assertEqual(rt._pathb_entry_market_regime("US"), "CAUTIOUS")

    def test_empty_when_no_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = _runtime(tmp)
            rt.bot.market_consensus_mode = {}
            rt.bot.today_judgment = {}
            # NEUTRAL 등 기본값으로 위조하지 않는다
            self.assertEqual(rt._pathb_entry_market_regime("US"), "")

    def test_live_gating_path_unchanged(self):
        # _pathb_consensus_mode(라이브 RISK-OFF cap 입력)는 캐시를 안 보고 today_judgment만 본다
        with tempfile.TemporaryDirectory() as tmp:
            rt = _runtime(tmp)
            rt.bot.market_consensus_mode = {"US": "DEFENSIVE"}
            rt.bot.today_judgment = {}
            self.assertEqual(rt._pathb_consensus_mode("US"), "")


class ConsensusGuardSeedsCacheTests(unittest.TestCase):
    def test_apply_consensus_guards_seeds_market_consensus_mode(self):
        # __init__를 우회해 finalizer만 단위 테스트(실제 seeding 코드 경로 검증)
        bot = TradingBot.__new__(TradingBot)
        bot.market_consensus_mode = {}
        guarded = bot._apply_consensus_guards(
            "US", {"bull": {}, "bear": {}, "neutral": {}}, {"mode": "MODERATE_BULL", "size": 50}
        )
        self.assertEqual(guarded.get("mode"), "MODERATE_BULL")
        self.assertEqual(bot.market_consensus_mode.get("US"), "MODERATE_BULL")

    def test_seed_inits_cache_if_missing(self):
        bot = TradingBot.__new__(TradingBot)
        # market_consensus_mode 미설정이어도 seed가 생성
        guarded = bot._apply_consensus_guards("KR", {}, {"mode": "MILD_BULL", "size": 40})
        self.assertEqual(bot.market_consensus_mode.get("KR"), "MILD_BULL")


if __name__ == "__main__":
    unittest.main()
