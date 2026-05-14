from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import kis_api


class UsExchangeResolverTests(unittest.TestCase):
    def test_hardcoded_exchange_overrides_stale_cache_and_saves(self) -> None:
        with patch.dict(kis_api._US_EXCHANGE_CACHE, {"NOK": "NASD"}, clear=True), patch.object(
            kis_api, "_save_exchange_cache"
        ) as save_mock, patch.object(kis_api, "_resolve_us_exchange_finnhub") as finnhub_mock:
            code = kis_api._get_ovrs_excg_cd("NOK", token=None)
            self.assertEqual(code, "NYSE")
            self.assertEqual(kis_api._US_EXCHANGE_CACHE["NOK"], "NYSE")
            save_mock.assert_called_once()
            finnhub_mock.assert_not_called()

    def test_cached_exchange_is_used_when_no_hardcoded_mapping_exists(self) -> None:
        with patch.dict(kis_api._US_EXCHANGE_CACHE, {"COCO": "NASD"}, clear=True), patch.object(
            kis_api, "_save_exchange_cache"
        ) as save_mock, patch.object(kis_api, "_resolve_us_exchange_finnhub") as finnhub_mock:
            code = kis_api._get_ovrs_excg_cd("COCO", token=None)

        self.assertEqual(code, "NASD")
        save_mock.assert_not_called()
        finnhub_mock.assert_not_called()

    def test_exchange_name_mapping_prefers_specific_nyse_markets(self) -> None:
        self.assertEqual(kis_api._map_us_exchange_name("NYSE AMERICAN"), "AMEX")
        self.assertEqual(kis_api._map_us_exchange_name("NYSE ARCA"), "AMEX")
        self.assertEqual(kis_api._map_yahoo_us_exchange("NASDAQ"), "NASD")

    def test_yahoo_exchange_fallback_is_used_after_finnhub_failure(self) -> None:
        with patch.dict(kis_api._US_EXCHANGE_CACHE, {}, clear=True), patch.object(
            kis_api, "_save_exchange_cache"
        ) as save_mock, patch.object(
            kis_api, "_resolve_us_exchange_finnhub", side_effect=ValueError("stale TSX profile")
        ), patch.object(kis_api, "_resolve_us_exchange_yahoo", return_value="NASD") as yahoo_mock:
            code = kis_api._get_ovrs_excg_cd("ALM", token=None)
            self.assertEqual(code, "NASD")
            self.assertEqual(kis_api._US_EXCHANGE_CACHE["ALM"], "NASD")
            save_mock.assert_called_once()
            yahoo_mock.assert_called_once_with("ALM")

    def test_us_post_filter_uses_exchange_metadata_when_present(self) -> None:
        candidates = [
            {
                "ticker": "ALM",
                "price": 19.5,
                "change_rate": -11.0,
                "volume": 5_000_000,
                "exchange": "NCM",
                "fullExchangeName": "NasdaqCM",
            },
            {
                "ticker": "AII",
                "price": 19.5,
                "change_rate": -11.0,
                "volume": 5_000_000,
                "exchange": "TOR",
                "fullExchangeName": "Toronto Stock Exchange",
            },
        ]

        filtered = kis_api._us_post_filter(candidates, "day_losers", 5.0, 25.0, 15_000_000, 20.0)

        self.assertEqual([c["ticker"] for c in filtered], ["ALM"])

    def test_us_websocket_subscription_uses_shared_exchange_resolver(self) -> None:
        with patch.dict(kis_api._US_EXCHANGE_CACHE, {"NOK": "NASD"}, clear=True), patch.object(
            kis_api, "_save_exchange_cache"
        ):
            ws = kis_api.KISWebSocket("token", ["NOK"], market="US")
            ws._ws_key = "approval-key"
            payload = json.loads(ws._sub_us("NOK"))

        self.assertEqual(payload["body"]["input"]["tr_id"], "HDFSASP0")
        self.assertEqual(payload["body"]["input"]["tr_key"], "DNYSNOK")


if __name__ == "__main__":
    unittest.main()
