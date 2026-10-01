"""US 주문 구분(ORD_DVSN) 선택 인자 — T1 2단계(LOO 매수 / MOO 청산) 준비 (2026-09-29).

핵심 계약: 기존 호출(인자 5개)은 body가 바이트 단위로 이전과 같다(ORD_DVSN "00").
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import kis_api

_OK = {"rt_cd": "0", "msg1": "OK", "output": {"ODNO": "us777"}}


class _Resp:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class UsOrdDvsnTests(unittest.TestCase):
    def _run(self, *args, paper=False, responses=None, **kwargs):
        sent: list[dict] = []
        resps = list(responses or [_Resp(200, _OK)])

        def fake_post(_url, *, headers, json, timeout):
            sent.append({"tr_id": headers.get("tr_id"), "body": dict(json)})
            return resps[min(len(sent) - 1, len(resps) - 1)]

        with patch.object(kis_api, "ACCOUNT_NO_US", "87654321-01"), patch.object(
            kis_api, "IS_PAPER_US", paper
        ), patch.object(kis_api, "_get_ovrs_excg_cd", return_value="NASD"), patch.object(
            kis_api, "get_hashkey", return_value="hash"
        ), patch.object(kis_api, "_kis_post", side_effect=fake_post), patch.object(
            kis_api.time, "sleep", lambda *_a, **_k: None
        ):
            result = kis_api.place_order(*args, **kwargs)
        return result, sent

    def test_default_call_body_unchanged(self) -> None:
        result, sent = self._run("AAPL", 1, 200.0, "buy", "token", market="US")
        self.assertTrue(result["success"])
        body = sent[0]["body"]
        self.assertEqual(body["ORD_DVSN"], "00")
        self.assertEqual(body["OVRS_ORD_UNPR"], "200.00")
        self.assertEqual(body["SLL_TYPE"], "")
        self.assertEqual(sent[0]["tr_id"], "TTTT1002U")

    def test_loo_buy_sends_32_with_limit_price(self) -> None:
        _, sent = self._run("AAPL", 1, 230.0, "buy", "token", market="US", ord_dvsn="32")
        self.assertEqual(sent[0]["body"]["ORD_DVSN"], "32")
        self.assertEqual(sent[0]["body"]["OVRS_ORD_UNPR"], "230.00")
        self.assertEqual(sent[0]["tr_id"], "TTTT1002U")

    def test_moo_sell_sends_31_with_zero_price(self) -> None:
        _, sent = self._run("AAPL", 1, 199.0, "sell", "token", market="US", ord_dvsn="31")
        body = sent[0]["body"]
        self.assertEqual(body["ORD_DVSN"], "31")
        self.assertEqual(body["OVRS_ORD_UNPR"], "0.00")
        self.assertEqual(body["SLL_TYPE"], "00")
        self.assertEqual(sent[0]["tr_id"], "TTTT1006U")

    def test_moo_not_allowed_for_buy(self) -> None:
        with self.assertRaises(ValueError):
            self._run("AAPL", 1, 200.0, "buy", "token", market="US", ord_dvsn="31")

    def test_paper_rejects_non_limit(self) -> None:
        with self.assertRaises(ValueError):
            self._run("AAPL", 1, 200.0, "buy", "token", market="US", ord_dvsn="32", paper=True)

    def test_kr_rejects_ord_dvsn(self) -> None:
        with self.assertRaises(ValueError):
            kis_api.place_order("005930", 1, 70000, "buy", "token", market="KR", ord_dvsn="32")

    def test_ord_dvsn_kept_on_http500_retry(self) -> None:
        err_resp = _Resp(500, {"rt_cd": "1", "msg1": "server error"})
        with patch.object(kis_api, "_find_recent_order_truth_us", return_value=None):
            _, sent = self._run("AAPL", 1, 230.0, "buy", "token", market="US", ord_dvsn="32",
                                responses=[err_resp, _Resp(200, _OK)])
        self.assertEqual(len(sent), 2)
        self.assertEqual([s["body"]["ORD_DVSN"] for s in sent], ["32", "32"])


if __name__ == "__main__":
    unittest.main()
