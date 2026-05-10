from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from bot.kr_investor_flow_cache import (
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

    def test_rolling_flow_from_caches_orders_by_date(self) -> None:
        caches = [
            {"date": "2026-05-11", "records": {"005930": {"foreign": 3, "institution": 4}}},
            {"date": "2026-05-10", "records": {"005930": {"foreign": 1, "institution": -2}}},
        ]

        features = rolling_flow_from_caches(caches, "5930")

        self.assertEqual(features["flow_window_5d_count"], 2)
        self.assertEqual(features["foreign_net_qty_5d"], 4)
        self.assertEqual(features["institution_net_qty_5d"], 2)


if __name__ == "__main__":
    unittest.main()
