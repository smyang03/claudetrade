from __future__ import annotations

import unittest
from unittest.mock import patch

import kis_api


class _Resp:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


_EGW00201 = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}
_OK = {"rt_cd": "0", "msg1": "OK", "output": {"ODNO": "99999"}}


class Egw00201RateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        # penalty 전역 상태를 테스트 간 격리
        self.addCleanup(lambda: setattr(kis_api, "_KIS_RATE_PENALTY_UNTIL", 0.0))
        kis_api._KIS_RATE_PENALTY_UNTIL = 0.0

    def test_kr_order_egw00201_retries_then_succeeds(self) -> None:
        calls = {"n": 0}
        responses = [_Resp(200, _EGW00201), _Resp(200, _OK)]

        def fake_post(_url, *, headers, json, timeout):
            r = responses[min(calls["n"], len(responses) - 1)]
            calls["n"] += 1
            return r

        with patch.object(kis_api, "ACCOUNT_NO", "12345678-01"), patch.object(
            kis_api, "IS_PAPER", False
        ), patch.object(kis_api, "get_hashkey", return_value="hash"), patch.object(
            kis_api, "_kis_post", side_effect=fake_post
        ), patch.object(kis_api, "KIS_ORDER_RATE_RETRY", 2), patch.object(
            kis_api.time, "sleep", lambda *_a, **_k: None
        ):
            result = kis_api._place_order_kr("005930", 1, 70000.0, "buy", "token")

        self.assertTrue(result["success"])
        self.assertEqual(result["order_no"], "99999")
        self.assertEqual(calls["n"], 2)  # 최초 1 + 재시도 1
        self.assertGreater(kis_api._KIS_RATE_PENALTY_UNTIL, 0.0)  # hit penalty 설정됨

    def test_kr_order_egw00201_exhausts_retries_and_raises(self) -> None:
        with patch.object(kis_api, "ACCOUNT_NO", "12345678-01"), patch.object(
            kis_api, "IS_PAPER", False
        ), patch.object(kis_api, "get_hashkey", return_value="hash"), patch.object(
            kis_api, "_kis_post", return_value=_Resp(200, _EGW00201)
        ), patch.object(kis_api, "KIS_ORDER_RATE_RETRY", 2), patch.object(
            kis_api.time, "sleep", lambda *_a, **_k: None
        ):
            with self.assertRaises(kis_api.KISOrderRateLimitedError):
                kis_api._place_order_kr("005930", 1, 70000.0, "buy", "token")
        # 소진 시 기존 KISOrderHTTPError 계약대로 상위 전파(subclass)
        self.assertTrue(issubclass(kis_api.KISOrderRateLimitedError, kis_api.KISOrderHTTPError))

    def test_us_order_egw00201_retries_then_succeeds(self) -> None:
        calls = {"n": 0}
        responses = [_Resp(200, _EGW00201), _Resp(200, {"rt_cd": "0", "msg1": "OK", "output": {"ODNO": "us777"}})]

        def fake_post(_url, *, headers, json, timeout):
            r = responses[min(calls["n"], len(responses) - 1)]
            calls["n"] += 1
            return r

        with patch.object(kis_api, "ACCOUNT_NO_US", "87654321-01"), patch.object(
            kis_api, "IS_PAPER_US", False
        ), patch.object(kis_api, "_get_ovrs_excg_cd", return_value="NASD"), patch.object(
            kis_api, "get_hashkey", return_value="hash"
        ), patch.object(kis_api, "_kis_post", side_effect=fake_post), patch.object(
            kis_api, "KIS_ORDER_RATE_RETRY", 2
        ), patch.object(kis_api.time, "sleep", lambda *_a, **_k: None):
            result = kis_api._place_order_us("AAPL", 1, 200.0, "buy", "token")

        self.assertTrue(result["success"])
        self.assertEqual(result["order_no"], "us777")
        self.assertEqual(calls["n"], 2)

    def test_query_egw00201_raises_rate_limited_and_sets_penalty(self) -> None:
        with self.assertRaises(kis_api.KISRateLimitedError):
            kis_api._require_kis_success(dict(_EGW00201), "잔고조회")
        self.assertGreater(kis_api._KIS_RATE_PENALTY_UNTIL, 0.0)

    def test_non_egw00201_failure_is_unchanged(self) -> None:
        # 다른 rt_cd 실패는 기존대로 일반 RuntimeError (rate 예외 아님)
        with self.assertRaises(RuntimeError) as ctx:
            kis_api._require_kis_success({"rt_cd": "1", "msg_cd": "APBK0919", "msg1": "잔고부족"}, "주문")
        self.assertNotIsInstance(ctx.exception, kis_api.KISRateLimitedError)


if __name__ == "__main__":
    unittest.main()
