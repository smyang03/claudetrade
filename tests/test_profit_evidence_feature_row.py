from __future__ import annotations

import unittest

from runtime.profit_path_predictor import build_runtime_feature_row
from trading_bot import TradingBot


class _StubBot:
    """TradingBot 인스턴스를 만들지 않고 후보 행 조회만 검증한다."""

    _selection_ticker_key = TradingBot._selection_ticker_key
    _profit_feature_candidate_row = TradingBot._profit_feature_candidate_row

    def __init__(self, screen_candidates: dict, selection_meta: dict | None = None) -> None:
        self._last_screen_candidates = screen_candidates
        self.selection_meta = selection_meta or {"KR": {}, "US": {}}


SCREEN_ROW = {
    "ticker": "073240",
    "change_pct": 3.2,
    "volume_ratio": 2.4,
    "price": 41500,
    "raw_score_current": 71.0,
    "from_high_pct": -1.8,
    "liquidity_bucket": "high",
    "primary_bucket": "momentum",
    "market_type": "KOSPI",
    "candidate_source": "screener",
    "data_quality": "ok",
}


def _missing(feature: dict) -> list[str]:
    return [key for key, value in feature.items() if value is None or value == "__MISSING__"]


class ProfitEvidenceFeatureRowTests(unittest.TestCase):
    def test_candidate_row_found_in_screen_candidates(self) -> None:
        bot = _StubBot({"KR": [dict(SCREEN_ROW)], "US": []})
        row = bot._profit_feature_candidate_row("KR", "073240")
        self.assertEqual(row.get("change_pct"), 3.2)
        self.assertEqual(row.get("primary_bucket"), "momentum")

    def test_selection_meta_pool_fills_gaps(self) -> None:
        bot = _StubBot(
            {"KR": [{"ticker": "073240", "change_pct": 3.2}], "US": []},
            {"KR": {"candidate_actions": [{"ticker": "073240", "liquidity_bucket": "high"}]}, "US": {}},
        )
        row = bot._profit_feature_candidate_row("KR", "073240")
        self.assertEqual(row.get("change_pct"), 3.2)
        self.assertEqual(row.get("liquidity_bucket"), "high")

    def test_us_ticker_is_case_normalized(self) -> None:
        bot = _StubBot({"KR": [], "US": [{"ticker": "nvda", "change_pct": 1.1}]})
        self.assertEqual(bot._profit_feature_candidate_row("US", "NVDA").get("change_pct"), 1.1)

    def test_unknown_ticker_returns_empty(self) -> None:
        # KR Tier2 섹터 플레이 종목은 스크리너 후보가 아니라서 피처가 존재하지 않는다.
        # 이때는 빈 dict를 돌려주고, 모델이 ood로 abstain하게 두는 게 맞다(가짜 피처 금지).
        bot = _StubBot({"KR": [dict(SCREEN_ROW)], "US": []})
        self.assertEqual(bot._profit_feature_candidate_row("KR", "055550"), {})

    def test_candidate_row_restores_model_features(self) -> None:
        # 배선 전: 시장 레벨 dict만 넘어가 모델이 상수 예측(ood)을 냈다.
        before = build_runtime_feature_row(
            market="KR", ticker="073240", strategy="kr_sector_play",
            context=None, sources=({"mode": "MILD_BULL"},),
        )
        after = build_runtime_feature_row(
            market="KR", ticker="073240", strategy="kr_sector_play",
            context=None, sources=({"mode": "MILD_BULL"}, dict(SCREEN_ROW)),
        )
        self.assertLess(len(_missing(after)), len(_missing(before)))
        for key in ("change_pct", "volume_ratio", "raw_score_current", "primary_bucket", "liquidity_bucket"):
            self.assertIn(key, _missing(before))
            self.assertNotIn(key, _missing(after))


if __name__ == "__main__":
    unittest.main()
