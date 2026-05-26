from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

import kis_api


class _Resp:
    def __init__(self, status_code: int, body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


class KisKrOrderSafetyTests(unittest.TestCase):
    def test_kr_ccld_unfilled_row_preserves_order_price(self) -> None:
        row = kis_api._normalize_kr_daily_ccld_row(
            {
                "odno": "0012214400",
                "pdno": "069540",
                "sll_buy_dvsn_cd": "02",
                "ord_qty": "33",
                "tot_ccld_qty": "0",
                "rmn_qty": "33",
                "avg_prvs": "0",
                "ord_unpr": "6780",
            }
        )

        self.assertEqual(row["order_price"], 6_780)
        self.assertEqual(row["fill_price"], 6_780)
        self.assertEqual(row["remaining_qty"], 33)
        self.assertEqual(row["order_status"], "open")

    def test_kr_ccld_rejected_row_is_not_remaining_open(self) -> None:
        row = kis_api._normalize_kr_daily_ccld_row(
            {
                "odno": "0012214400",
                "pdno": "069540",
                "sll_buy_dvsn_cd": "02",
                "ord_qty": "33",
                "tot_ccld_qty": "0",
                "rmn_qty": "0",
                "rjct_qty": "33",
                "cncl_cfrm_qty": "0",
                "avg_prvs": "0",
                "ord_unpr": "6780",
            }
        )

        self.assertEqual(row["remaining_qty"], 0)
        self.assertEqual(row["rejected_qty"], 33)
        self.assertEqual(row["order_status"], "rejected")

    def test_us_ccnl_unfilled_sell_has_remaining_qty(self) -> None:
        # 미체결 US 매도 주문 → remaining_qty > 0 이어야 open_orders 필터 통과
        row = kis_api._normalize_us_inquire_ccnl_row(
            {
                "odno": "0030262408",
                "pdno": "IBM",
                "sll_buy_dvsn": "01",
                "ord_qty": "1",
                "ft_ccld_qty": "0",
                "ovrs_ord_unpr": "254.247",
            }
        )

        self.assertEqual(row["remaining_qty"], 1)
        self.assertEqual(row["side"], "sell")
        self.assertEqual(row["order_no"], "0030262408")

    def test_us_ccnl_unfilled_sell_uses_nccs_qty_when_available(self) -> None:
        # API가 nccs_qty(미체결수량)를 직접 제공하는 경우 우선 사용
        row = kis_api._normalize_us_inquire_ccnl_row(
            {
                "odno": "0030262408",
                "pdno": "IBM",
                "sll_buy_dvsn": "01",
                "ord_qty": "3",
                "ft_ccld_qty": "1",
                "nccs_qty": "2",
                "ovrs_ord_unpr": "254.247",
            }
        )

        self.assertEqual(row["remaining_qty"], 2)

    def test_us_ccnl_filled_order_has_zero_remaining_qty(self) -> None:
        # 완전 체결된 주문 → remaining_qty == 0 → open_orders 필터 제외
        row = kis_api._normalize_us_inquire_ccnl_row(
            {
                "odno": "0030111111",
                "pdno": "AAPL",
                "sll_buy_dvsn": "02",
                "ord_qty": "1",
                "ft_ccld_qty": "1",
                "ovrs_ord_unpr": "200.0",
            }
        )

        self.assertEqual(row["remaining_qty"], 0)

    def test_kr_order_body_uses_integer_strings_and_exchange_code(self) -> None:
        captured: dict = {}

        def fake_post(_url, *, headers, json, timeout):
            captured.update(json)
            return _Resp(200, {"rt_cd": "0", "msg1": "OK", "output": {"ODNO": "12345"}})

        with patch.object(kis_api, "ACCOUNT_NO", "12345678-01"), patch.object(
            kis_api, "IS_PAPER", False
        ), patch.object(kis_api, "get_hashkey", return_value="hash"), patch.object(
            kis_api, "_kis_post", side_effect=fake_post
        ):
            result = kis_api._place_order_kr("005930", 1, 70000.0, "buy", "token")

        self.assertTrue(result["success"])
        self.assertEqual(captured["ORD_QTY"], "1")
        self.assertEqual(captured["ORD_UNPR"], "70000")
        self.assertEqual(captured["EXCG_ID_DVSN_CD"], "KRX")
        self.assertEqual(captured["CNDT_PRIC"], "")

    def test_kr_cancel_order_body_uses_rvsecncl_contract(self) -> None:
        captured: dict = {}

        def fake_post(_url, *, headers, json, timeout):
            captured["url"] = _url
            captured["tr_id"] = headers.get("tr_id")
            captured.update(json)
            return _Resp(200, {"rt_cd": "0", "msg1": "OK", "output": {"ODNO": "54321"}})

        with patch.object(kis_api, "ACCOUNT_NO", "12345678-01"), patch.object(
            kis_api, "IS_PAPER", False
        ), patch.object(kis_api, "get_hashkey", return_value="hash"), patch.object(
            kis_api, "_kis_post", side_effect=fake_post
        ):
            result = kis_api.cancel_order("005930", "12345", 2, "token", market="KR", price=52300.0)

        self.assertTrue(result["success"])
        self.assertIn("/uapi/domestic-stock/v1/trading/order-rvsecncl", captured["url"])
        self.assertEqual(captured["tr_id"], "TTTC0013U")
        self.assertEqual(captured["ORGN_ODNO"], "12345")
        self.assertEqual(captured["RVSE_CNCL_DVSN_CD"], "02")
        self.assertEqual(captured["ORD_QTY"], "2")
        self.assertEqual(captured["QTY_ALL_ORD_YN"], "Y")
        self.assertEqual(captured["EXCG_ID_DVSN_CD"], "KRX")

    def test_us_cancel_order_body_uses_rvsecncl_contract(self) -> None:
        captured: dict = {}

        def fake_post(_url, *, headers, json, timeout):
            captured["url"] = _url
            captured["tr_id"] = headers.get("tr_id")
            captured.update(json)
            return _Resp(200, {"rt_cd": "0", "msg1": "OK", "output": {"ODNO": "us54321"}})

        with patch.object(kis_api, "ACCOUNT_NO_US", "87654321-01"), patch.object(
            kis_api, "IS_PAPER_US", False
        ), patch.object(kis_api, "_get_ovrs_excg_cd", return_value="NASD"), patch.object(
            kis_api, "get_hashkey", return_value="hash"
        ), patch.object(kis_api, "_kis_post", side_effect=fake_post):
            result = kis_api.cancel_order("AAPL", "us123", 1, "token", market="US", price=200.0)

        self.assertTrue(result["success"])
        self.assertIn("/uapi/overseas-stock/v1/trading/order-rvsecncl", captured["url"])
        self.assertEqual(captured["tr_id"], "TTTT1004U")
        self.assertEqual(captured["OVRS_EXCG_CD"], "NASD")
        self.assertEqual(captured["PDNO"], "AAPL")
        self.assertEqual(captured["ORGN_ODNO"], "us123")
        self.assertEqual(captured["RVSE_CNCL_DVSN_CD"], "02")
        self.assertEqual(captured["ORD_QTY"], "1")

    def test_kr_order_500_query_failure_skips_retry(self) -> None:
        first_error = kis_api.KISOrderHTTPError("HTTP 500", status_code=500, response_text="boom")

        with patch.object(kis_api, "_submit_order_kr_once", side_effect=first_error) as submit_mock, patch.object(
            kis_api, "_find_recent_order_truth_kr", side_effect=TimeoutError("query timeout")
        ):
            with self.assertRaises(kis_api.KISOrderHTTPError) as ctx:
                kis_api._place_order_kr("005930", 1, 70000, "buy", "token")

        self.assertEqual(submit_mock.call_count, 1)
        self.assertIn("broker_truth_query_failed", str(ctx.exception))
        self.assertIn("retry_skipped=state_unknown", str(ctx.exception))

    def test_kr_order_500_no_truth_retries_once(self) -> None:
        first_error = kis_api.KISOrderHTTPError("HTTP 500", status_code=500, response_text="boom")
        success = {
            "success": True,
            "msg": "OK",
            "order_no": "12345",
            "market": "KR",
            "side": "buy",
            "ticker": "005930",
            "qty": 1,
            "price": 70000.0,
            "price_type": "limit",
            "raw": {},
        }

        with patch.object(
            kis_api, "_submit_order_kr_once", side_effect=[first_error, success]
        ) as submit_mock, patch.object(kis_api, "_find_recent_order_truth_kr", return_value=None):
            result = kis_api._place_order_kr("005930", 1, 70000, "buy", "token")

        self.assertEqual(submit_mock.call_count, 2)
        self.assertEqual(result["order_no"], "12345")

    def test_kr_order_500_truth_recovery_does_not_retry(self) -> None:
        first_error = kis_api.KISOrderHTTPError("HTTP 500", status_code=500, response_text="boom")
        truth = {
            "order_no": "88888",
            "ticker": "005930",
            "side": "buy",
            "order_qty": 1,
            "filled_qty": 0,
            "fill_price": 0,
            "order_time": "121936",
            "raw": {},
        }

        with patch.object(kis_api, "_submit_order_kr_once", side_effect=first_error) as submit_mock, patch.object(
            kis_api, "_find_recent_order_truth_kr", return_value=truth
        ):
            result = kis_api._place_order_kr("005930", 1, 70000, "buy", "token")

        self.assertEqual(submit_mock.call_count, 1)
        self.assertTrue(result["success"])
        self.assertEqual(result["order_no"], "88888")
        self.assertEqual(result["msg"], "broker_truth_recovered_after_http_500")

    def test_us_order_500_query_failure_skips_retry(self) -> None:
        first_error = kis_api.KISOrderHTTPError("HTTP 500", status_code=500, response_text="boom")

        with patch.object(kis_api, "_submit_order_us_once", side_effect=first_error) as submit_mock, patch.object(
            kis_api, "_find_recent_order_truth_us", side_effect=TimeoutError("query timeout")
        ):
            with self.assertRaises(kis_api.KISOrderHTTPError) as ctx:
                kis_api._place_order_us("AAPL", 1, 200.0, "buy", "token")

        self.assertEqual(submit_mock.call_count, 1)
        self.assertIn("broker_truth_query_failed", str(ctx.exception))
        self.assertIn("retry_skipped=state_unknown", str(ctx.exception))

    def test_us_order_500_no_truth_retries_once(self) -> None:
        first_error = kis_api.KISOrderHTTPError("HTTP 500", status_code=500, response_text="boom")
        success = {
            "success": True,
            "msg": "OK",
            "order_no": "us123",
            "market": "US",
            "side": "buy",
            "ticker": "AAPL",
            "qty": 1,
            "price": 200.0,
            "price_type": "limit",
            "raw": {},
        }

        with patch.object(
            kis_api, "_submit_order_us_once", side_effect=[first_error, success]
        ) as submit_mock, patch.object(kis_api, "_find_recent_order_truth_us", return_value=None):
            result = kis_api._place_order_us("AAPL", 1, 200.0, "buy", "token")

        self.assertEqual(submit_mock.call_count, 2)
        self.assertEqual(result["order_no"], "us123")

    def test_us_order_truth_uses_query_date_for_previous_day_rows(self) -> None:
        submitted_at = datetime(2026, 4, 29, 0, 30, 0)

        def fake_inquire(_token, *, start_date, end_date, ticker, side_code, filled_code):
            self.assertEqual(start_date, end_date)
            if start_date == "20260428":
                return [
                    {
                        "order_no": "old_order",
                        "ticker": "AAPL",
                        "side": "buy",
                        "order_qty": 1,
                        "filled_qty": 0,
                        "fill_price": 0,
                        "order_time": "235900",
                        "raw": {},
                    }
                ]
            return []

        with patch.object(kis_api, "inquire_ccnl_us", side_effect=fake_inquire):
            result = kis_api._find_recent_order_truth_us(
                "token",
                ticker="AAPL",
                side="buy",
                qty=1,
                submitted_at=submitted_at,
            )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
