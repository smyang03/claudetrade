from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from bot.kr_investor_flow_cache import (
    effective_flow_source_date,
    flow_for_ticker,
    load_flow_cache,
    rolling_flow_from_caches,
    update_candidate_flow_cache,
)


class KrInvestorFlowCacheTests(unittest.TestCase):
    def test_update_candidate_flow_cache_dedupes_and_persists(self) -> None:
        calls: list[str] = []

        def fetch(ticker: str, target_date: str, token: str) -> dict:
            calls.append(ticker)
            return {"foreign": "10", "institution": "-2", "individual": "3"}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.json"
            cache = update_candidate_flow_cache(
                ["5930", "005930", "000660"],
                session_date="2026-05-10",
                token="token",
                fetch_fn=fetch,
                sleep_sec=0,
                path=path,
                now=datetime(2026, 5, 10, 9, 0, 0),
            )

            self.assertEqual(calls, ["005930", "000660"])
            self.assertEqual(flow_for_ticker(cache, "005930")["foreign"], 10)
            self.assertEqual(flow_for_ticker(cache, "000660")["institution"], -2)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("005930", saved["records"])

    def test_cache_hit_skips_refetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.json"
            path.write_text(
                json.dumps(
                    {
                        "date": "2026-05-10",
                        "records": {
                            "005930": {"status": "ok", "foreign": 1, "institution": 2},
                        },
                    }
                ),
                encoding="utf-8",
            )

            def fetch(_ticker: str, _target_date: str, _token: str) -> dict:
                raise AssertionError("cache hit should not refetch")

            cache = update_candidate_flow_cache(
                ["005930"],
                session_date="2026-05-10",
                token="token",
                fetch_fn=fetch,
                sleep_sec=0,
                path=path,
            )

            self.assertEqual(flow_for_ticker(cache, "005930")["foreign"], 1)

    def test_all_zero_ok_flows_are_flagged_bad_quality(self) -> None:
        def fetch(_ticker: str, _target_date: str, _token: str) -> dict:
            return {"foreign": 0, "institution": 0, "individual": 0}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.json"
            cache = update_candidate_flow_cache(
                [str(1000 + idx) for idx in range(10)],
                session_date="2026-05-10",
                token="token",
                fetch_fn=fetch,
                sleep_sec=0,
                path=path,
            )

            self.assertEqual(cache["data_quality"], "bad_zero_flow_cluster")
            self.assertEqual(cache["ok_record_count"], 10)
            self.assertEqual(cache["zero_flow_record_count"], 10)
            self.assertEqual(cache["untrusted_flow_record_count"], 10)
            self.assertIn("kr_investor_flow_all_zero_cluster", cache["quality_flags"])
            self.assertEqual(flow_for_ticker(cache, "001000")["flow_data_quality"], "bad_zero_flow_cluster")
            self.assertFalse(flow_for_ticker(cache, "001000")["flow_values_trusted"])
            self.assertEqual(flow_for_ticker(cache, "001000")["flow_unavailable_reason"], "all_zero_cluster")
            self.assertIn("kr_investor_flow_all_zero_cluster", flow_for_ticker(cache, "001000")["flow_quality_flags"])
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["data_quality"], "bad_zero_flow_cluster")

    def test_existing_all_zero_cache_is_untrusted_and_refetched(self) -> None:
        records = {
            str(100000 + idx): {
                "status": "ok",
                "foreign": 0,
                "institution": 0,
                "individual": 0,
            }
            for idx in range(10)
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.json"
            path.write_text(
                json.dumps({"date": "2026-05-10", "records": records}),
                encoding="utf-8",
            )
            calls: list[str] = []

            def fetch(ticker: str, _target_date: str, _token: str) -> dict:
                calls.append(ticker)
                return {"foreign": 5, "institution": -1, "individual": -4}

            cache = update_candidate_flow_cache(
                list(records),
                session_date="2026-05-10",
                token="token",
                fetch_fn=fetch,
                sleep_sec=0,
                path=path,
            )

            self.assertEqual(calls, list(records))
            self.assertEqual(cache["data_quality"], "ok")
            self.assertEqual(cache["untrusted_flow_record_count"], 0)
            self.assertTrue(flow_for_ticker(cache, "100000")["flow_values_trusted"])
            self.assertEqual(flow_for_ticker(cache, "100000")["foreign"], 5)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["data_quality"], "ok")

    def test_nonzero_flow_keeps_quality_ok(self) -> None:
        def fetch(ticker: str, _target_date: str, _token: str) -> dict:
            if ticker == "001009":
                return {"foreign": 5, "institution": 0, "individual": -5}
            return {"foreign": 0, "institution": 0, "individual": 0}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.json"
            cache = update_candidate_flow_cache(
                [str(1000 + idx) for idx in range(10)],
                session_date="2026-05-10",
                token="token",
                fetch_fn=fetch,
                sleep_sec=0,
                path=path,
            )

            self.assertEqual(cache["data_quality"], "ok")
            self.assertEqual(cache["ok_record_count"], 10)
            self.assertEqual(cache["zero_flow_record_count"], 9)
            self.assertNotIn("kr_investor_flow_all_zero_cluster", cache["quality_flags"])

    def test_load_corrupt_cache_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.json"
            path.write_text("{broken", encoding="utf-8")

            cache = load_flow_cache("2026-05-10", path=path)

            self.assertEqual(cache["date"], "2026-05-10")
            self.assertEqual(cache["records"], {})

    def test_datetime_session_date_is_reduced_to_date_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.json"
            seen_dates: list[str] = []

            def fetch(_ticker: str, target_date: str, _token: str) -> dict:
                seen_dates.append(target_date)
                return {"foreign": 1}

            cache = update_candidate_flow_cache(
                ["005930"],
                session_date=datetime(2026, 5, 10, 9, 30, 0),
                token="token",
                fetch_fn=fetch,
                sleep_sec=0,
                path=path,
            )

            self.assertEqual(cache["date"], "2026-05-10")
            self.assertEqual(seen_dates, ["2026-05-10"])

    def test_effective_flow_source_date_uses_previous_completed_kr_trading_day(self) -> None:
        self.assertEqual(effective_flow_source_date("2026-06-01"), "2026-05-29")

    def test_explicit_flow_source_date_is_fetch_target_and_cache_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.json"
            seen_dates: list[str] = []

            def fetch(_ticker: str, target_date: str, _token: str) -> dict:
                seen_dates.append(target_date)
                return {"foreign": 1}

            cache = update_candidate_flow_cache(
                ["005930"],
                session_date="2026-06-01",
                flow_source_date="2026-05-29",
                token="token",
                fetch_fn=fetch,
                sleep_sec=0,
                path=path,
            )

            self.assertEqual(cache["date"], "2026-05-29")
            self.assertEqual(cache["flow_source_date"], "2026-05-29")
            self.assertEqual(cache["requested_session_date"], "2026-06-01")
            self.assertEqual(cache["flow_age_trading_days"], 1)
            self.assertEqual(seen_dates, ["2026-05-29"])
            self.assertEqual(flow_for_ticker(cache, "005930")["flow_age_trading_days"], 1)

    def test_rolling_flow_from_caches_orders_by_date(self) -> None:
        caches = [
            {"date": "2026-05-11", "records": {"005930": {"foreign": 3, "institution": 4}}},
            {"date": "2026-05-10", "records": {"005930": {"foreign": 1, "institution": -2}}},
        ]

        features = rolling_flow_from_caches(caches, "5930")

        self.assertEqual(features["flow_window_5d_count"], 2)
        self.assertEqual(features["foreign_net_qty_5d"], 4)
        self.assertEqual(features["institution_net_qty_5d"], 2)


class FetchInvestorFlowDateMatchTests(unittest.TestCase):
    """fetch_investor_flow_kr이 output[0] 맹신 대신 stck_bsop_date로 target_date를 매칭하는지."""

    def _resp(self, output):
        class _R:
            def raise_for_status(self_inner):
                return None

            def json(self_inner):
                return {"output": output}

        return _R()

    def test_matches_target_date_row_not_first(self) -> None:
        from unittest.mock import patch
        from phase1_trainer import supplement_collector as sc

        output = [
            {"stck_bsop_date": "20260601", "frgn_ntby_qty": "0", "orgn_ntby_qty": "0", "prsn_ntby_qty": "0"},
            {"stck_bsop_date": "20260529", "frgn_ntby_qty": "100", "orgn_ntby_qty": "50", "prsn_ntby_qty": "-150"},
        ]
        with patch.object(sc.requests, "get", return_value=self._resp(output)):
            flow = sc.fetch_investor_flow_kr("005930", "2026-05-29", "token")
        self.assertEqual(flow["foreign"], 100)
        self.assertEqual(flow["institution"], 50)
        self.assertEqual(flow["individual"], -150)
        self.assertTrue(flow["flow_date_matched"])
        self.assertEqual(flow["flow_date"], "20260529")

    def test_falls_back_to_first_when_target_absent(self) -> None:
        from unittest.mock import patch
        from phase1_trainer import supplement_collector as sc

        # KIS가 당일행만 반환(target 미포함) → 폴백, 단 미매칭 표시
        output = [{"stck_bsop_date": "20260601", "frgn_ntby_qty": "7", "orgn_ntby_qty": "3", "prsn_ntby_qty": "-10"}]
        with patch.object(sc.requests, "get", return_value=self._resp(output)):
            flow = sc.fetch_investor_flow_kr("005930", "2026-05-29", "token")
        self.assertEqual(flow["foreign"], 7)
        self.assertFalse(flow["flow_date_matched"])
        self.assertEqual(flow["flow_date"], "20260601")

    def test_empty_output_returns_empty(self) -> None:
        from unittest.mock import patch
        from phase1_trainer import supplement_collector as sc

        with patch.object(sc.requests, "get", return_value=self._resp([])):
            flow = sc.fetch_investor_flow_kr("005930", "2026-05-29", "token")
        self.assertEqual(flow, {})


class UnsettledZeroFlowTrustTests(unittest.TestCase):
    """날짜 미매칭 + all-zero(=정산 전 당일값)는 신뢰 안 하고 재시도 유도."""

    def test_unmatched_all_zero_is_untrusted(self) -> None:
        def fetch(_ticker, _target, _token):
            return {"foreign": 0, "institution": 0, "individual": 0,
                    "flow_date": "20260601", "flow_date_matched": False}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.json"
            cache = update_candidate_flow_cache(
                ["005930"], session_date="2026-06-01", flow_source_date="2026-05-29",
                token="t", fetch_fn=fetch, sleep_sec=0, path=path,
            )
            rec = cache["records"]["005930"]
            self.assertFalse(rec["flow_values_trusted"])
            self.assertEqual(rec["flow_unavailable_reason"], "unsettled_zero_unmatched_date")
            self.assertFalse(rec["flow_date_matched"])

    def test_matched_nonzero_is_trusted(self) -> None:
        def fetch(_ticker, _target, _token):
            return {"foreign": 100, "institution": 50, "individual": -150,
                    "flow_date": "20260529", "flow_date_matched": True}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.json"
            cache = update_candidate_flow_cache(
                ["005930"], session_date="2026-06-01", flow_source_date="2026-05-29",
                token="t", fetch_fn=fetch, sleep_sec=0, path=path,
            )
            rec = cache["records"]["005930"]
            self.assertTrue(rec["flow_values_trusted"])
            self.assertTrue(rec["flow_date_matched"])
            self.assertEqual(rec["flow_reported_date"], "20260529")
            self.assertEqual(flow_for_ticker(cache, "005930")["foreign"], 100)

    def test_unmatched_nonzero_stays_trusted_no_regression(self) -> None:
        # 미매칭이라도 non-zero면 기존처럼 trusted(무회귀), 단 관측 필드만 추가
        def fetch(_ticker, _target, _token):
            return {"foreign": 7, "institution": 0, "individual": -7,
                    "flow_date": "20260601", "flow_date_matched": False}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.json"
            cache = update_candidate_flow_cache(
                ["005930"], session_date="2026-06-01", flow_source_date="2026-05-29",
                token="t", fetch_fn=fetch, sleep_sec=0, path=path,
            )
            rec = cache["records"]["005930"]
            self.assertTrue(rec["flow_values_trusted"])
            self.assertFalse(rec["flow_date_matched"])
            self.assertEqual(rec["flow_reported_date"], "20260601")


if __name__ == "__main__":
    unittest.main()
