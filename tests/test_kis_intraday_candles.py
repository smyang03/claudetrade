from __future__ import annotations

import time
import unittest
from unittest.mock import patch

import pandas as pd

import kis_api


class _Resp:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class KISIntradayCandleTests(unittest.TestCase):
    def setUp(self) -> None:
        kis_api._INTRADAY_CACHE.clear()

    def test_kr_kis_intraday_normalizes_rows(self) -> None:
        payload = {
            "output2": [
                {
                    "stck_bsop_date": "20260513",
                    "stck_cntg_hour": "090100",
                    "stck_oprc": "101",
                    "stck_hgpr": "102",
                    "stck_lwpr": "100",
                    "stck_prpr": "101",
                    "cntg_vol": "20",
                },
                {
                    "stck_bsop_date": "20260513",
                    "stck_cntg_hour": "090000",
                    "stck_oprc": "100",
                    "stck_hgpr": "101",
                    "stck_lwpr": "99",
                    "stck_prpr": "100",
                    "cntg_vol": "10",
                },
            ]
        }

        with patch("kis_api._kis_get", return_value=_Resp(payload)):
            df = kis_api._intraday_ohlcv_kr_kis(
                "005930",
                "token",
                session_date="2026-05-13",
                start_at="2026-05-13T09:00:00",
                end_at="2026-05-13T09:02:00",
            )

        self.assertEqual(list(df["close"]), [100.0, 101.0])
        self.assertEqual(list(df["volume"]), [10.0, 20.0])
        self.assertEqual(str(df.iloc[0]["ts"]), "2026-05-13 09:00:00")

    def test_kr_kis_intraday_does_not_use_cumulative_volume_as_bar_volume(self) -> None:
        payload = {
            "output2": [
                {
                    "stck_bsop_date": "20260513",
                    "stck_cntg_hour": "090000",
                    "stck_oprc": "100",
                    "stck_hgpr": "101",
                    "stck_lwpr": "99",
                    "stck_prpr": "100",
                    "cntg_vol": "",
                    "acml_vol": "999999",
                }
            ]
        }

        with patch("kis_api._kis_get", return_value=_Resp(payload)):
            df = kis_api._intraday_ohlcv_kr_kis(
                "005930",
                "token",
                session_date="2026-05-13",
                start_at="2026-05-13T09:00:00",
                end_at="2026-05-13T09:02:00",
            )

        self.assertEqual(df.iloc[0]["volume"], 0.0)

    def test_kr_kis_intraday_deadline_prevents_page_request(self) -> None:
        with patch("kis_api._kis_get") as mocked:
            with self.assertRaisesRegex(TimeoutError, "provider_timeout"):
                kis_api._intraday_ohlcv_kr_kis(
                    "005930",
                    "token",
                    session_date="2026-05-13",
                    start_at="2026-05-13T09:00:00",
                    end_at="2026-05-13T09:02:00",
                    deadline=time.time() - 0.1,
                )

        mocked.assert_not_called()

    def test_retry_kis_zero_retries_means_single_attempt(self) -> None:
        calls = 0

        def _fail():
            nonlocal calls
            calls += 1
            raise RuntimeError("boom")

        with patch("kis_api.time.sleep") as mocked_sleep:
            with self.assertRaisesRegex(RuntimeError, "boom"):
                kis_api._retry_kis("unit", _fail, retries=0, delay_sec=0.01)

        self.assertEqual(calls, 1)
        mocked_sleep.assert_not_called()

    def test_us_provider_disabled_is_fail_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "provider disabled"):
            kis_api.get_intraday_candles(
                "AAPL",
                market="US",
                session_date="2026-05-13",
                start_at="2026-05-13T22:30:00",
                end_at="2026-05-13T22:35:00",
                provider="disabled",
            )

    def test_us_yfinance_provider_dispatches_and_caches(self) -> None:
        frame = pd.DataFrame(
            {
                "ts": pd.to_datetime(["2026-05-13T22:30:00"]),
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "volume": [1000.0],
                "source": ["yfinance_intraday"],
            }
        )
        with patch("kis_api._intraday_ohlcv_us_yf", return_value=frame.copy()) as mocked:
            first = kis_api.get_intraday_candles(
                "aapl",
                market="US",
                session_date="2026-05-13",
                start_at="2026-05-13T22:30:00",
                end_at="2026-05-13T22:35:00",
                provider="yfinance",
            )
            second = kis_api.get_intraday_candles(
                "AAPL",
                market="US",
                session_date="2026-05-13",
                start_at="2026-05-13T22:30:00",
                end_at="2026-05-13T22:35:00",
                provider="yfinance",
            )

        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(first.iloc[0]["close"], second.iloc[0]["close"])

    def test_us_kis_intraday_normalizes_desc_rows_to_ascending_kst(self) -> None:
        payload = {
            "rt_cd": "0",
            "output2": [
                {
                    "kymd": "20260514",
                    "khms": "013200",
                    "open": "101",
                    "high": "102",
                    "low": "100",
                    "last": "101.5",
                    "evol": "20",
                },
                {
                    "kymd": "20260514",
                    "khms": "013100",
                    "open": "100",
                    "high": "101",
                    "low": "99",
                    "last": "100.5",
                    "evol": "10",
                },
            ],
        }

        with patch("kis_api._get_us_quote_codes", return_value=("NASD", "NAS")), patch(
            "kis_api._kis_get",
            return_value=_Resp(payload),
        ) as mocked:
            df = kis_api._intraday_ohlcv_us_kis(
                "tsla",
                "token",
                session_date="2026-05-13",
                start_at="2026-05-14T01:30:00+09:00",
                end_at="2026-05-14T01:33:00+09:00",
            )

        params = mocked.call_args_list[0].kwargs["params"]
        self.assertEqual(params["EXCD"], "NAS")
        self.assertEqual(params["NMIN"], "1")
        self.assertEqual(params["PINC"], "0")
        self.assertEqual(list(df["close"]), [100.5, 101.5])
        self.assertEqual(list(df["volume"]), [10.0, 20.0])
        self.assertEqual(list(df["source"]), ["kis_us_intraday", "kis_us_intraday"])
        self.assertEqual(str(df.iloc[0]["ts"]), "2026-05-14 01:31:00")

    def test_us_kis_intraday_paginates_older_rows_with_keyb(self) -> None:
        first = {
            "rt_cd": "0",
            "output2": [
                {
                    "kymd": "20260514",
                    "khms": "013200",
                    "xymd": "20260513",
                    "xhms": "123200",
                    "open": "102",
                    "high": "103",
                    "low": "101",
                    "last": "102",
                    "evol": "12",
                },
                {
                    "kymd": "20260514",
                    "khms": "013100",
                    "xymd": "20260513",
                    "xhms": "123100",
                    "open": "101",
                    "high": "102",
                    "low": "100",
                    "last": "101",
                    "evol": "11",
                },
            ],
        }
        second = {
            "rt_cd": "0",
            "output2": [
                {
                    "kymd": "20260514",
                    "khms": "013000",
                    "xymd": "20260513",
                    "xhms": "123000",
                    "open": "100",
                    "high": "101",
                    "low": "99",
                    "last": "100",
                    "evol": "10",
                },
                {
                    "kymd": "20260514",
                    "khms": "012900",
                    "xymd": "20260513",
                    "xhms": "122900",
                    "open": "99",
                    "high": "100",
                    "low": "98",
                    "last": "99",
                    "evol": "9",
                },
            ],
        }

        with patch.dict("os.environ", {"US_INTRADAY_KIS_MAX_PAGES": "2", "US_INTRADAY_KIS_PAGE_SLEEP_SEC": "0"}), patch(
            "kis_api._get_us_quote_codes",
            return_value=("NASD", "NAS"),
        ), patch("kis_api._kis_get", side_effect=[_Resp(first), _Resp(second)]) as mocked:
            df = kis_api._intraday_ohlcv_us_kis(
                "TSLA",
                "token",
                session_date="2026-05-13",
                start_at="2026-05-14T01:29:00+09:00",
                end_at="2026-05-14T01:33:00+09:00",
            )

        second_params = mocked.call_args_list[1].kwargs["params"]
        self.assertEqual(second_params["NEXT"], "1")
        self.assertEqual(second_params["PINC"], "1")
        self.assertEqual(second_params["KEYB"], "20260513123000")
        self.assertEqual(list(df["close"]), [99.0, 100.0, 101.0, 102.0])

    def test_us_kis_provider_dispatches_without_yfinance_fallback(self) -> None:
        with patch("kis_api._intraday_ohlcv_us_kis", side_effect=RuntimeError("kis failed")):
            with self.assertRaisesRegex(RuntimeError, "kis failed"):
                kis_api.get_intraday_candles(
                    "AAPL",
                    token="token",
                    market="US",
                    session_date="2026-05-13",
                    start_at="2026-05-13T22:30:00+09:00",
                    end_at="2026-05-13T22:35:00+09:00",
                    provider="kis",
                )


if __name__ == "__main__":
    unittest.main()
