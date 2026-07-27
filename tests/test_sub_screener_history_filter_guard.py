"""sub_screener triage가 history 필터 제외 종목을 재유입하지 않는지 검증.

배경 (실측 2026-07-13~27):
  _filter_candidates_by_history가 제거한 종목을 sub_screener triage가 모르고
  다시 watchlist에 넣었다. triage 추가분의 83~95%가 방금 제거된 종목이었고,
  judge 호출의 26~63%(7/27 기준 30회 중 16회)를 그 종목들이 소비했다.
  후보 풀에서 빠진 종목은 장중 피처 수집 대상도 아니라 스냅샷이 0건이고,
  judge는 빈 입력을 받아 기권한다 — 구조적으로 플랜이 나올 수 없는 호출이었다.

TradingBot 전체를 띄우지 않고 필요한 표면만 stub으로 붙인다.
"""
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _Bot:
    """_record_history_filtered_out / _history_filtered_out_keys /
    _build_sub_screener_exclude_set가 쓰는 표면만 흉내낸다."""

    def __init__(self, session="2026-07-27"):
        self._session = session
        self.today_tickers = {"KR": [], "US": []}
        self.selection_meta = {"KR": {}, "US": {}}
        self.trade_ready_tickers = {"KR": [], "US": []}
        self.pending_orders = []

    def _current_session_date_str(self, market):
        return self._session

    def _selection_ticker_key(self, market, ticker):
        t = str(ticker or "").strip()
        return t.upper() if str(market).upper() == "US" else t

    def _local_position_keys(self, market):
        return set()


# _build_sub_screener_exclude_set은 self._history_filtered_out_keys를 호출한다.
# 스텁에 실제 메서드를 붙여줘야 실제 호출 경로가 그대로 재현된다.
_REAL_METHODS = (
    "_record_history_filtered_out",
    "_history_filtered_out_keys",
    "_build_sub_screener_exclude_set",
)


def _bind(bot, name):
    import trading_bot
    for attr in _REAL_METHODS:
        if not hasattr(bot, attr):
            setattr(bot, attr, types.MethodType(getattr(trading_bot.TradingBot, attr), bot))
    return getattr(bot, name)


class SubScreenerHistoryFilterGuardTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("SUB_SCREENER_RESPECT_HISTORY_FILTER")
        os.environ.pop("SUB_SCREENER_RESPECT_HISTORY_FILTER", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("SUB_SCREENER_RESPECT_HISTORY_FILTER", None)
        else:
            os.environ["SUB_SCREENER_RESPECT_HISTORY_FILTER"] = self._saved

    def _record(self, bot, market, removed):
        _bind(bot, "_record_history_filtered_out")(market, removed)

    def test_removed_tickers_are_excluded_from_triage(self):
        bot = _Bot()
        self._record(bot, "KR", [("475150", "anti_chase_extreme_spike(MAX=30.0)"),
                                 ("439960", "data_insufficient(0usable)")])
        exclude = _bind(bot, "_build_sub_screener_exclude_set")("KR")
        self.assertIn("475150", exclude)
        self.assertIn("439960", exclude)

    def test_reason_is_preserved(self):
        bot = _Bot()
        self._record(bot, "KR", [("475150", "anti_chase_extreme_spike(MAX=30.0)")])
        keys = _bind(bot, "_history_filtered_out_keys")("KR")
        self.assertEqual(keys["475150"], "anti_chase_extreme_spike(MAX=30.0)")

    def test_toggle_off_restores_previous_behavior(self):
        os.environ["SUB_SCREENER_RESPECT_HISTORY_FILTER"] = "false"
        bot = _Bot()
        self._record(bot, "KR", [("475150", "anti_chase_extreme_spike")])
        exclude = _bind(bot, "_build_sub_screener_exclude_set")("KR")
        self.assertNotIn("475150", exclude)

    def test_session_rollover_clears_previous_day(self):
        bot = _Bot(session="2026-07-27")
        self._record(bot, "KR", [("475150", "anti_chase_extreme_spike")])
        bot._session = "2026-07-28"          # 다음 세션
        self.assertEqual(_bind(bot, "_history_filtered_out_keys")("KR"), {})
        exclude = _bind(bot, "_build_sub_screener_exclude_set")("KR")
        self.assertNotIn("475150", exclude)

    def test_markets_are_isolated(self):
        bot = _Bot()
        self._record(bot, "KR", [("475150", "anti_chase_extreme_spike")])
        self._record(bot, "US", [("NVDA", "data_insufficient")])
        self.assertIn("475150", _bind(bot, "_build_sub_screener_exclude_set")("KR"))
        self.assertNotIn("475150", _bind(bot, "_build_sub_screener_exclude_set")("US"))
        self.assertIn("NVDA", _bind(bot, "_build_sub_screener_exclude_set")("US"))

    def test_us_ticker_key_is_uppercased(self):
        bot = _Bot()
        self._record(bot, "US", [("nvda", "anti_chase_extreme_spike")])
        self.assertIn("NVDA", _bind(bot, "_build_sub_screener_exclude_set")("US"))

    def test_repeated_records_accumulate_within_session(self):
        bot = _Bot()
        self._record(bot, "KR", [("475150", "a")])
        self._record(bot, "KR", [("439960", "b")])
        keys = _bind(bot, "_history_filtered_out_keys")("KR")
        self.assertEqual(set(keys), {"475150", "439960"})

    def test_malformed_entries_do_not_raise(self):
        bot = _Bot()
        self._record(bot, "KR", ["475150", ("439960",), None, ()])
        keys = _bind(bot, "_history_filtered_out_keys")("KR")
        self.assertIn("475150", keys)
        self.assertIn("439960", keys)

    def test_no_record_means_empty_exclusion(self):
        bot = _Bot()
        self.assertEqual(_bind(bot, "_history_filtered_out_keys")("KR"), {})


if __name__ == "__main__":
    unittest.main()
