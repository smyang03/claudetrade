# -*- coding: utf-8 -*-
"""스크리너 소스 붕괴 판정 하한 계약 테스트 (2026-08-21).

08-20~21 실측: degraded 53건 중 **47건(89%)이 오탐**이었다.

    ASE:0/3   35건  ┐
    NGM:1/4   11건  ├ 소수 거래소 단독 사유 = 정상 변동을 붕괴로 읽음
    NGM:0/4    1건  ┘
    count_collapse + NMS:15~17/37   6건  ← 진짜 붕괴(항상 count_collapse 동반)

급락 종목은 날마다 달라서 3~4종목짜리 소스가 0이 되는 건 정상이다. 그런데 그때마다
이전 스냅샷 후보 평균 41개(풀의 39%)가 낡은 채로 병합됐다.

하한을 8로 올려도 진짜 붕괴는 count_collapse가 잡는다 — 안전망이 이중이다.
이 테스트는 실측 케이스를 그대로 고정한다.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from trading_bot import TradingBot


def _cands(by_source: dict[str, int]) -> list[dict]:
    out = []
    for source, n in by_source.items():
        for i in range(n):
            out.append({"ticker": f"{source}{i}", "source": source})
    return out


class _Bot:
    """_screen_quality_guard만 떼어 쓰는 최소 스텁."""

    def __init__(self, previous: list[dict]):
        self._last_screen_candidates = {"US": previous}
        # degraded 경로가 보존 대상 키를 계산할 때 참조한다. 비워두면 preserve_keys가
        # 빈 set이 되어 previous 전체가 보존되므로 이 테스트의 관심사(degraded 여부)에
        # 영향이 없다.
        self.today_tickers: dict = {}
        self.today_judgment: dict = {}

    _screen_quality_guard = TradingBot._screen_quality_guard
    _screen_count_by_field = TradingBot._screen_count_by_field
    _merge_screen_candidates = TradingBot._merge_screen_candidates
    _candidate_identity_key = TradingBot._candidate_identity_key
    _preservable_screen_keys = TradingBot._preservable_screen_keys
    _load_persisted_screen_baseline = staticmethod(lambda *a, **k: [])
    _selection_ticker_key = TradingBot._selection_ticker_key
    selection_meta: dict = {}


# 실측 그대로: NMS 37 · NGM 4 · ASE 3 = 44 (이전 스냅샷)
PREV = _cands({"NMS": 37, "NGM": 4, "ASE": 3})


class SourceFloorTests(unittest.TestCase):
    def test_small_source_collapse_is_not_degraded_by_default(self):
        """ASE 3->0, NGM 4->1은 정상 변동이다 — 경고가 없어야 한다.

        fresh 총량은 유지(NMS 37)해서 count_collapse가 안 걸리게 한다.
        """
        bot = _Bot(PREV)
        with mock.patch.dict(os.environ, {}, clear=False):
            out = bot._screen_quality_guard(
                "US", _cands({"NMS": 37, "NGM": 1, "ASE": 0}),
                phase="screen_market_candidates",
            )
        # degraded면 previous가 병합돼 44개가 되고, 아니면 fresh 38개 그대로다
        self.assertEqual(len(out), 38, "소수 소스 변동으로 degraded 판정되면 안 된다")

    def test_major_source_collapse_still_detected(self):
        """NMS 37->15는 진짜 붕괴다 — 하한 8로도 잡혀야 한다."""
        bot = _Bot(PREV)
        with mock.patch.dict(os.environ, {}, clear=False):
            out = bot._screen_quality_guard(
                "US", _cands({"NMS": 15, "NGM": 4, "ASE": 3}),
                phase="screen_market_candidates",
            )
        self.assertGreater(len(out), 22, "NMS 붕괴는 degraded로 잡혀 병합되어야 한다")

    def test_floor_is_env_configurable(self):
        """하한을 3으로 되돌리면 예전 동작(소수 소스도 붕괴 판정)이 나와야 한다."""
        bot = _Bot(PREV)
        with mock.patch.dict(os.environ, {"SCREEN_DEGRADED_MIN_SOURCE_PREV": "3"}, clear=False):
            out = bot._screen_quality_guard(
                "US", _cands({"NMS": 37, "NGM": 1, "ASE": 0}),
                phase="screen_market_candidates",
            )
        self.assertGreater(len(out), 38, "하한 3이면 ASE 3->0이 붕괴로 잡혀 병합된다")

    def test_count_collapse_is_independent_safety_net(self):
        """소스 하한과 무관하게 총량 붕괴는 잡힌다 — 실측 진짜 붕괴 6건의 형태."""
        bot = _Bot(PREV)
        with mock.patch.dict(os.environ, {"SCREEN_DEGRADED_MIN_SOURCE_PREV": "99"}, clear=False):
            out = bot._screen_quality_guard(
                "US", _cands({"NMS": 10}), phase="screen_market_candidates",
            )
        self.assertGreater(len(out), 10, "총량 27% 수준이면 count_collapse로 잡혀야 한다")


if __name__ == "__main__":
    unittest.main()
