from __future__ import annotations

import unittest

from runtime.market_resolver import infer_ticker_market, normalize_market, resolve_position_market


class MarketResolverTests(unittest.TestCase):
    def test_normalize_market_accepts_supported_values(self) -> None:
        self.assertEqual(normalize_market("us"), "US")
        self.assertEqual(normalize_market(" KR "), "KR")
        self.assertEqual(normalize_market("JP"), "")

    def test_infer_ticker_market_handles_us_class_symbols_and_kr_codes(self) -> None:
        self.assertEqual(infer_ticker_market("AAPL", unknown=""), "US")
        self.assertEqual(infer_ticker_market("BRK.B", unknown=""), "US")
        self.assertEqual(infer_ticker_market("BRK-B", unknown=""), "US")
        self.assertEqual(infer_ticker_market("005930", unknown=""), "KR")
        self.assertEqual(infer_ticker_market("", unknown=""), "")

    def test_resolve_position_market_prefers_metadata_before_ticker(self) -> None:
        self.assertEqual(resolve_position_market({"ticker": "BRK-B", "market": "US"}, unknown=""), "US")
        self.assertEqual(resolve_position_market({"ticker": "BRK-B", "market": "KR"}, unknown=""), "KR")
        self.assertEqual(resolve_position_market({"ticker": "005930", "display_currency": "USD"}, unknown=""), "US")
        self.assertEqual(resolve_position_market({"ticker": "AAPL", "display_currency": "KRW"}, unknown=""), "KR")


if __name__ == "__main__":
    unittest.main()
