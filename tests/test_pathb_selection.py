from __future__ import annotations

import unittest

from bot.candidate_policy import normalize_selection_result


class PathBSelectionTests(unittest.TestCase):
    def test_price_targets_survive_for_trade_ready_only(self) -> None:
        candidates = [{"ticker": "005930"}, {"ticker": "000660"}]
        parsed = {
            "watchlist": ["005930", "000660"],
            "trade_ready": ["005930"],
            "price_targets": {
                "005930": {"buy_zone_low": 70000, "buy_zone_high": 71000},
                "000660": {"buy_zone_low": 120000, "buy_zone_high": 121000},
            },
        }

        meta = normalize_selection_result(parsed, candidates, "KR")

        self.assertEqual(list(meta["price_targets"].keys()), ["005930"])
        self.assertEqual(meta["price_targets"]["005930"]["buy_zone_low"], 70000)

    def test_missing_price_targets_keeps_existing_path_a_fields(self) -> None:
        candidates = [{"ticker": "NVDA"}, {"ticker": "AAPL"}]
        parsed = {
            "watchlist": ["NVDA", "AAPL"],
            "trade_ready": ["NVDA"],
            "recommended_strategy": {"NVDA": "momentum"},
        }

        meta = normalize_selection_result(parsed, candidates, "US")

        self.assertEqual(meta["trade_ready"], ["NVDA"])
        self.assertEqual(meta["recommended_strategy"]["NVDA"], "momentum")
        self.assertEqual(meta["price_targets"], {})


if __name__ == "__main__":
    unittest.main()
