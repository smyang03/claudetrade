"""early judge 피처 자격 게이트 / 시장별 시간창 계약 검증.

배경 (실측 2026-07-27):
  judge는 vwap/opening_range가 없으면 반드시 기권한다. 응답이 직접 말한다 —
  "Market open elapsed 0.0min with first_observed snapshot: no VWAP, opening
   range, or pullback structure".
  플랜 산출률 자격O vs 자격X: KR 17.4% vs 4.4%(4배) / US 30.6% vs 5.5%(5.6배).
  7/27 US 개장 15분 시점에 자격 종목이 0개인데 judge를 8회 소모했다.

  hard_skip은 예산을 차감하지 않고 재큐되므로, 자격이 생긴 뒤 다시 평가된다.

시간창은 시장별로 다르다(자격O 첫판정 플랜율 실측):
  KR  ≤90분 37.1% → 90~180분 7.1% → 180분+ 0.0%
  US  0~30분 5.3% → 30~180분 44.5% → 180분+ 23.8%
"""
import inspect
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_ENV_KEYS = (
    "EARLY_JUDGE_REQUIRE_POST_OPEN_FEATURES",
    "KR_EARLY_JUDGE_REQUIRE_POST_OPEN_FEATURES",
    "US_EARLY_JUDGE_REQUIRE_POST_OPEN_FEATURES",
    "KR_EARLY_JUDGE_WINDOW_MIN", "KR_EARLY_JUDGE_WINDOW_MAX",
    "US_EARLY_JUDGE_WINDOW_MIN", "US_EARLY_JUDGE_WINDOW_MAX",
)


class _Bot:
    def __init__(self, elapsed=45.0):
        self._elapsed = elapsed

    def _market_open_elapsed_min(self, market):
        return self._elapsed

    def _runtime_float(self, key, default=0.0):
        raw = os.getenv(key)
        try:
            return float(raw) if raw not in (None, "") else float(default)
        except (TypeError, ValueError):
            return float(default)


def _bind(bot, name):
    """실제 메서드를 스텁에 붙인다. staticmethod는 바인딩하면 self가 끼어드므로 그대로 쓴다."""
    import trading_bot
    for attr in ("_early_judge_require_features", "_early_judge_features_ready",
                 "_early_judge_window_skip"):
        if hasattr(bot, attr):
            continue
        raw = inspect.getattr_static(trading_bot.TradingBot, attr)
        func = getattr(trading_bot.TradingBot, attr)
        setattr(bot, attr, func if isinstance(raw, staticmethod) else types.MethodType(func, bot))
    return getattr(bot, name)


def _feat(**kw):
    base = {"vwap": 100.0, "opening_range_high": 101.0, "opening_range_low": 99.0}
    base.update(kw)
    return base


class EarlyJudgeFeatureGateTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # ---- 자격 판정 ----
    def test_complete_features_are_ready(self):
        self.assertTrue(_bind(_Bot(), "_early_judge_features_ready")(_feat()))

    def test_missing_vwap_axis_is_not_ready(self):
        f = _feat(vwap=None, vwap_distance_pct=None)
        self.assertFalse(_bind(_Bot(), "_early_judge_features_ready")(f))

    def test_derived_vwap_distance_counts_as_ready(self):
        """원값이 없어도 파생 필드로 근거가 실린 경로가 있다 — 축 단위로 판정한다."""
        f = _feat(vwap=None, vwap_distance_pct=1.0)
        self.assertTrue(_bind(_Bot(), "_early_judge_features_ready")(f))

    def test_derived_opening_range_break_counts_as_ready(self):
        f = _feat(opening_range_high=None, opening_range_low=None, opening_range_break=True)
        self.assertTrue(_bind(_Bot(), "_early_judge_features_ready")(f))

    def test_missing_opening_range_axis_is_not_ready(self):
        f = _feat(opening_range_high=None, opening_range_low=None, opening_range_break=None)
        self.assertFalse(_bind(_Bot(), "_early_judge_features_ready")(f))

    def test_only_one_side_of_opening_range_is_enough(self):
        """OR 상단만 있어도 구조 판단은 가능하다 — 양쪽 다 없을 때만 탈락."""
        f = _feat(opening_range_high=None)
        self.assertTrue(_bind(_Bot(), "_early_judge_features_ready")(f))

    def test_empty_string_counts_as_missing(self):
        f = _feat(vwap="", vwap_distance_pct="")
        self.assertFalse(_bind(_Bot(), "_early_judge_features_ready")(f))

    def test_non_dict_is_not_ready(self):
        ready = _bind(_Bot(), "_early_judge_features_ready")
        for bad in (None, "", [], 0):
            self.assertFalse(ready(bad), bad)

    # ---- 토글 ----
    def test_gate_is_on_by_default(self):
        self.assertTrue(_bind(_Bot(), "_early_judge_require_features")("KR"))

    def test_global_toggle_off(self):
        os.environ["EARLY_JUDGE_REQUIRE_POST_OPEN_FEATURES"] = "false"
        self.assertFalse(_bind(_Bot(), "_early_judge_require_features")("KR"))

    def test_market_toggle_overrides_global(self):
        os.environ["EARLY_JUDGE_REQUIRE_POST_OPEN_FEATURES"] = "false"
        os.environ["US_EARLY_JUDGE_REQUIRE_POST_OPEN_FEATURES"] = "true"
        req = _bind(_Bot(), "_early_judge_require_features")
        self.assertTrue(req("US"))
        self.assertFalse(req("KR"))

    # ---- 시간창 ----
    def test_window_disabled_by_default(self):
        self.assertEqual(_bind(_Bot(elapsed=300.0), "_early_judge_window_skip")("KR"), "")

    def test_window_before_lower_bound(self):
        os.environ["US_EARLY_JUDGE_WINDOW_MIN"] = "30"
        self.assertEqual(
            _bind(_Bot(elapsed=10.0), "_early_judge_window_skip")("US"),
            "early_judge_window_before",
        )

    def test_window_after_upper_bound(self):
        os.environ["KR_EARLY_JUDGE_WINDOW_MAX"] = "90"
        self.assertEqual(
            _bind(_Bot(elapsed=120.0), "_early_judge_window_skip")("KR"),
            "early_judge_window_after",
        )

    def test_inside_window_passes(self):
        os.environ["KR_EARLY_JUDGE_WINDOW_MIN"] = "0"
        os.environ["KR_EARLY_JUDGE_WINDOW_MAX"] = "90"
        self.assertEqual(_bind(_Bot(elapsed=45.0), "_early_judge_window_skip")("KR"), "")

    def test_markets_use_own_window(self):
        os.environ["KR_EARLY_JUDGE_WINDOW_MAX"] = "90"
        os.environ["US_EARLY_JUDGE_WINDOW_MAX"] = "180"
        skip = _bind(_Bot(elapsed=120.0), "_early_judge_window_skip")
        self.assertEqual(skip("KR"), "early_judge_window_after")
        self.assertEqual(skip("US"), "")   # US는 120분도 생산 구간(44.5%)

    def test_unknown_elapsed_does_not_skip(self):
        bot = _Bot()
        bot._market_open_elapsed_min = lambda m: None
        os.environ["KR_EARLY_JUDGE_WINDOW_MAX"] = "90"
        self.assertEqual(_bind(bot, "_early_judge_window_skip")("KR"), "")

    # ---- 재큐 계약 ----
    def test_feature_skip_is_requeued_but_window_after_is_not(self):
        """자격/창-이전은 나중에 충족될 수 있어 재큐, 창-이후는 되돌아오지 않는다."""
        import trading_bot
        q = trading_bot.TradingBot._early_judge_queueable_skip
        self.assertTrue(q("post_open_feature_not_ready"))
        self.assertTrue(q("early_judge_window_before"))
        self.assertFalse(q("early_judge_window_after"))


if __name__ == "__main__":
    unittest.main()
