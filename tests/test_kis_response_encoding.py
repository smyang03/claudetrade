from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import requests

import kis_api


class KisResponseEncodingTests(unittest.TestCase):
    def test_kis_get_forces_utf8_before_json_decode(self) -> None:
        response = requests.Response()
        response.status_code = 200
        response.url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/test"
        response.encoding = "cp949"
        response._content = json.dumps(
            {"output": [{"hts_kor_isnm": "삼성전자"}]},
            ensure_ascii=False,
        ).encode("utf-8")

        with (
            patch.object(kis_api, "_rate_limit_wait", return_value=None),
            patch.object(kis_api.requests, "get", return_value=response),
        ):
            actual = kis_api._kis_get(response.url, headers={}, timeout=1)

        self.assertEqual(actual.encoding, "utf-8")
        self.assertEqual(actual.json()["output"][0]["hts_kor_isnm"], "삼성전자")


if __name__ == "__main__":
    unittest.main()
