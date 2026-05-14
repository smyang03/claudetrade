from __future__ import annotations

import threading
import time
import unittest

from runtime.intraday_minute_cache import IntradayMinuteCache


def _candles(base: str = "2026-05-13T09") -> list[dict]:
    return [
        {"ts": f"{base}:00:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 100},
        {"ts": f"{base}:01:00", "open": 100, "high": 102, "low": 100, "close": 101, "volume": 100},
        {"ts": f"{base}:02:00", "open": 101, "high": 103, "low": 101, "close": 102, "volume": 100},
        {"ts": f"{base}:03:00", "open": 102, "high": 104, "low": 102, "close": 103, "volume": 100},
        {"ts": f"{base}:04:00", "open": 103, "high": 105, "low": 103, "close": 104, "volume": 100},
        {"ts": f"{base}:05:00", "open": 104, "high": 106, "low": 104, "close": 105, "volume": 100},
        {"ts": f"{base}:06:00", "open": 105, "high": 107, "low": 105, "close": 107, "volume": 100},
    ]


class IntradayMinuteCacheTests(unittest.TestCase):
    def test_get_many_records_partial_failures_and_counts(self) -> None:
        calls: list[str] = []

        def provider(**kwargs):
            ticker = kwargs["ticker"]
            calls.append(ticker)
            if ticker == "BBB":
                raise RuntimeError("provider failed")
            return _candles()

        cache = IntradayMinuteCache(provider=provider, provider_name="fake", ttl_sec=30, max_workers=1)

        result = cache.get_many(
            market="KR",
            tickers=["AAA", "BBB"],
            session_date="2026-05-13",
            token=None,
            regular_open="2026-05-13T09:00:00",
            known_at="2026-05-13T09:06:00",
            avg_daily_volume_by_ticker={"AAA": 39000},
            opening_range_min=3,
        )

        self.assertEqual(calls, ["AAA", "BBB"])
        self.assertEqual(result["requested"], 2)
        self.assertEqual(result["fetched"], 1)
        self.assertEqual(result["complete"], 1)
        self.assertEqual(result["missing"], 1)
        self.assertIn("BBB", result["errors_by_ticker"])
        self.assertEqual(result["features_by_ticker"]["AAA"]["data_quality"], "minute_complete")

    def test_cache_ttl_reuses_provider_result(self) -> None:
        now = [1000.0]
        calls = 0

        def provider(**kwargs):
            nonlocal calls
            calls += 1
            return _candles()

        cache = IntradayMinuteCache(
            provider=provider,
            provider_name="fake",
            ttl_sec=30,
            max_workers=1,
            now_func=lambda: now[0],
        )

        first = cache.get_many(
            market="KR",
            tickers=["AAA"],
            session_date="2026-05-13",
            token=None,
            regular_open="2026-05-13T09:00:00",
            known_at="2026-05-13T09:06:00",
            avg_daily_volume_by_ticker={"AAA": 39000},
            opening_range_min=3,
        )
        now[0] += 10
        second = cache.get_many(
            market="KR",
            tickers=["AAA"],
            session_date="2026-05-13",
            token=None,
            regular_open="2026-05-13T09:00:00",
            known_at="2026-05-13T09:06:00",
            avg_daily_volume_by_ticker={"AAA": 39000},
            opening_range_min=3,
        )

        self.assertEqual(calls, 1)
        self.assertEqual(second["used_cache"], 1)
        self.assertEqual(first["features_by_ticker"]["AAA"]["current_price"], second["features_by_ticker"]["AAA"]["current_price"])

    def test_missing_features_are_not_cached_by_default(self) -> None:
        calls = 0

        def provider(**kwargs):
            nonlocal calls
            calls += 1
            return []

        cache = IntradayMinuteCache(provider=provider, provider_name="fake", ttl_sec=30, max_workers=1)
        for _ in range(2):
            cache.get_many(
                market="KR",
                tickers=["AAA"],
                session_date="2026-05-13",
                token=None,
                regular_open="2026-05-13T09:00:00",
                known_at="2026-05-13T09:06:00",
                avg_daily_volume_by_ticker={"AAA": 39000},
                opening_range_min=10,
            )

        self.assertEqual(calls, 2)
        self.assertFalse(cache._cache)

    def test_partial_features_use_shorter_cache_ttl(self) -> None:
        now = [1000.0]
        calls = 0

        def provider(**kwargs):
            nonlocal calls
            calls += 1
            return _candles()

        cache = IntradayMinuteCache(
            provider=provider,
            provider_name="fake",
            ttl_sec=30,
            max_workers=1,
            now_func=lambda: now[0],
        )
        cache.get_many(
            market="KR",
            tickers=["AAA"],
            session_date="2026-05-13",
            token=None,
            regular_open="2026-05-13T09:00:00",
            known_at="2026-05-13T09:06:00",
            avg_daily_volume_by_ticker={"AAA": 39000},
            opening_range_min=10,
        )
        now[0] += 6
        second = cache.get_many(
            market="KR",
            tickers=["AAA"],
            session_date="2026-05-13",
            token=None,
            regular_open="2026-05-13T09:00:00",
            known_at="2026-05-13T09:06:00",
            avg_daily_volume_by_ticker={"AAA": 39000},
            opening_range_min=10,
        )

        self.assertEqual(calls, 2)
        self.assertEqual(second["used_cache"], 0)

    def test_session_date_change_does_not_reuse_cache(self) -> None:
        calls = 0

        def provider(**kwargs):
            nonlocal calls
            calls += 1
            return _candles()

        cache = IntradayMinuteCache(provider=provider, provider_name="fake", ttl_sec=300, max_workers=1)
        for session in ("2026-05-13", "2026-05-14"):
            cache.get_many(
                market="KR",
                tickers=["AAA"],
                session_date=session,
                token=None,
                regular_open="2026-05-13T09:00:00",
                known_at="2026-05-13T09:06:00",
                avg_daily_volume_by_ticker={"AAA": 39000},
                opening_range_min=3,
            )

        self.assertEqual(calls, 2)

    def test_us_tickers_are_normalized(self) -> None:
        cache = IntradayMinuteCache(provider=lambda **kwargs: _candles("2026-05-13T22"), provider_name="fake")

        result = cache.get_many(
            market="US",
            tickers=["aapl", "AAPL"],
            session_date="2026-05-13",
            token=None,
            regular_open="2026-05-13T22:00:00",
            known_at="2026-05-13T22:06:00",
            avg_daily_volume_by_ticker={"AAPL": 39000},
            opening_range_min=3,
        )

        self.assertEqual(result["requested"], 1)
        self.assertIn("AAPL", result["features_by_ticker"])

    def test_sequential_prefetch_respects_timeout(self) -> None:
        now = [1000.0]
        calls: list[str] = []

        def provider(**kwargs):
            calls.append(kwargs["ticker"])
            now[0] += 5.0
            return _candles()

        cache = IntradayMinuteCache(
            provider=provider,
            provider_name="fake",
            ttl_sec=30,
            max_workers=1,
            timeout_sec=3,
            now_func=lambda: now[0],
        )

        result = cache.get_many(
            market="KR",
            tickers=["AAA", "BBB", "CCC"],
            session_date="2026-05-13",
            token=None,
            regular_open="2026-05-13T09:00:00",
            known_at="2026-05-13T09:06:00",
            avg_daily_volume_by_ticker={"AAA": 39000, "BBB": 39000, "CCC": 39000},
            opening_range_min=3,
        )

        self.assertEqual(calls, ["AAA"])
        self.assertIn("BBB", result["errors_by_ticker"])
        self.assertIn("CCC", result["errors_by_ticker"])
        self.assertEqual(result["missing"], 2)

    def test_single_worker_provider_call_is_bounded_by_timeout(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def provider(**kwargs):
            started.set()
            release.wait(2.0)
            return _candles()

        cache = IntradayMinuteCache(
            provider=provider,
            provider_name="fake",
            ttl_sec=30,
            max_workers=1,
            timeout_sec=0.1,
        )

        start = time.monotonic()
        result = cache.get_many(
            market="KR",
            tickers=["AAA"],
            session_date="2026-05-13",
            token=None,
            regular_open="2026-05-13T09:00:00",
            known_at="2026-05-13T09:06:00",
            avg_daily_volume_by_ticker={"AAA": 39000},
            opening_range_min=3,
        )
        elapsed = time.monotonic() - start
        release.set()

        self.assertTrue(started.is_set())
        self.assertLess(elapsed, 1.0)
        self.assertNotIn("AAA", result["features_by_ticker"])
        self.assertEqual(result["errors_by_ticker"]["AAA"], "provider_timeout")
        self.assertFalse(cache._cache)

    def test_multi_worker_timeout_returns_completed_and_marks_unfinished(self) -> None:
        release = threading.Event()

        def provider(**kwargs):
            if kwargs["ticker"] == "SLOW":
                release.wait(2.0)
            return _candles()

        cache = IntradayMinuteCache(
            provider=provider,
            provider_name="fake",
            ttl_sec=30,
            max_workers=2,
            timeout_sec=0.15,
        )

        start = time.monotonic()
        result = cache.get_many(
            market="KR",
            tickers=["FAST", "SLOW"],
            session_date="2026-05-13",
            token=None,
            regular_open="2026-05-13T09:00:00",
            known_at="2026-05-13T09:06:00",
            avg_daily_volume_by_ticker={"FAST": 39000, "SLOW": 39000},
            opening_range_min=3,
        )
        elapsed = time.monotonic() - start
        release.set()

        self.assertLess(elapsed, 1.0)
        self.assertIn("FAST", result["features_by_ticker"])
        self.assertNotIn("SLOW", result["features_by_ticker"])
        self.assertIn("SLOW", result["errors_by_ticker"])


if __name__ == "__main__":
    unittest.main()
