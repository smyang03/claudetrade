from __future__ import annotations

import unittest

from runtime.post_open_features import build_post_open_snapshot
from strategy.opening_range_pullback import diagnostics as orp_diagnostics
from trading_bot import TradingBot


def _snapshot(**overrides):
    payload = {
        "market": "US",
        "ticker": "NVDA",
        "known_at": "2026-07-13T23:00:00",
        "anchor_at": "2026-07-13T22:30:00",
        "anchor_price": 100.0,
        "current_price": 104.0,
        "open_high": 105.0,
        "data_quality": "minute_backfill",
        "market_session_date": "2026-07-13",
    }
    payload.update(overrides)
    return build_post_open_snapshot(**payload).to_dict()


class SnapshotCarriesOpeningRangeTests(unittest.TestCase):
    """★누수 봉합 회귀: 스냅샷이 opening_range_high/low를 저장하지 않아 OR이 소실됐다."""

    def test_snapshot_stores_opening_range(self) -> None:
        snapshot = _snapshot(opening_range_high=103.0, opening_range_low=99.0)
        self.assertEqual(snapshot["opening_range_high"], 103.0)
        self.assertEqual(snapshot["opening_range_low"], 99.0)

    def test_snapshot_without_opening_range_stays_none(self) -> None:
        # 하위호환: OR을 안 넘기는 기존 호출부가 깨지면 안 된다.
        snapshot = _snapshot()
        self.assertIsNone(snapshot["opening_range_high"])
        self.assertIsNone(snapshot["opening_range_low"])

    def test_opening_range_break_still_computed(self) -> None:
        snapshot = _snapshot(opening_range_high=103.0, opening_range_low=99.0)
        self.assertTrue(snapshot["opening_range_break"])  # current 104 > OR high 103


class _StubBot:
    """_maybe_update_or_cache_from_post_open_feature만 검증한다(봇 인스턴스 없이)."""

    _selection_ticker_key = TradingBot._selection_ticker_key
    _maybe_update_or_cache_from_post_open_feature = TradingBot._maybe_update_or_cache_from_post_open_feature

    def __init__(self) -> None:
        self._or_high: dict[str, float] = {}
        self._or_low: dict[str, float] = {}
        self._or_formed: dict[str, bool] = {}


class OrCacheChainTests(unittest.TestCase):
    """스냅샷 → _or_formed 체인. 이게 True가 되어야 opening_range_pullback이 평가된다."""

    def test_backfilled_snapshot_forms_opening_range(self) -> None:
        bot = _StubBot()
        snapshot = _snapshot(opening_range_high=103.0, opening_range_low=99.0)
        self.assertTrue(bot._maybe_update_or_cache_from_post_open_feature("US", "NVDA", snapshot))
        self.assertTrue(bot._or_formed["NVDA"])
        self.assertEqual(bot._or_high["NVDA"], 103.0)
        self.assertEqual(bot._or_low["NVDA"], 99.0)

    def test_snapshot_without_or_cannot_form(self) -> None:
        # 봉합 전 상태 재현: OR이 없으면 _or_formed는 영원히 False다.
        bot = _StubBot()
        self.assertFalse(bot._maybe_update_or_cache_from_post_open_feature("US", "NVDA", _snapshot()))
        self.assertEqual(bot._or_formed, {})

    def test_minute_missing_snapshot_is_rejected(self) -> None:
        bot = _StubBot()
        snapshot = _snapshot(opening_range_high=103.0, opening_range_low=99.0, data_quality="minute_missing")
        self.assertFalse(bot._maybe_update_or_cache_from_post_open_feature("US", "NVDA", snapshot))


class OrpEvaluableAfterFixTests(unittest.TestCase):
    """OR이 없으면 ORP는 orp_not_formed(range=0.00%)로 끝난다 — 682/682 신호 0의 뿌리."""

    def _params(self, **overrides):
        params = {
            "session_elapsed_min": 20.0,
            "or_minutes": 15.0,
            "entry_window_min": 60.0,
            "or_formed": False,
            "or_high": 0.0,
            "or_low": 0.0,
        }
        params.update(overrides)
        return params

    def test_no_opening_range_yields_not_formed(self) -> None:
        import pandas as pd

        frame = pd.DataFrame({"close": [100.0] * 20, "volume": [1000] * 20, "vol_avg20": [1000] * 20})
        result = orp_diagnostics(frame, len(frame) - 1, self._params())
        self.assertFalse(result["fired"])
        self.assertEqual(result["reason"], "orp_not_formed")

    def test_opening_range_present_moves_past_not_formed(self) -> None:
        import pandas as pd

        frame = pd.DataFrame({"close": [100.0] * 20, "volume": [1000] * 20, "vol_avg20": [1000] * 20})
        result = orp_diagnostics(
            frame, len(frame) - 1, self._params(or_formed=True, or_high=103.0, or_low=99.0)
        )
        # 발화 여부는 조건에 달렸지만, 더 이상 "레인지가 없어서" 탈락하지는 않아야 한다.
        self.assertNotEqual(result["reason"], "orp_not_formed")


if __name__ == "__main__":
    unittest.main()
