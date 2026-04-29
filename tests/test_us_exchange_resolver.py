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
