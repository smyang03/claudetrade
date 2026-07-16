from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.intraday_minute_cache import IntradayMinuteCache
from runtime.time_normalized_rvol import (
    LocalTimeNormalizedRvolStore,
    compute_time_normalized_rvol,
)


def _rows(day: str, *, volume: float, through: int = 5) -> list[dict]:
    return [
        {
            "ts": f"{day}T09:{minute:02d}:00+09:00",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": volume,
        }
        for minute in range(through + 1)
    ]


class TimeNormalizedRvolTests(unittest.TestCase):
    def test_naive_us_runtime_timestamp_is_interpreted_as_kst(self) -> None:
        current = [
            {
                "ts": f"2026-07-16T22:{minute:02d}:00",
                "volume": 200,
            }
            for minute in range(30, 37)
        ]
        history: list[dict] = []
        for day in ("2026-07-09", "2026-07-10", "2026-07-13", "2026-07-14", "2026-07-15"):
            history.extend(
                {
                    "ts": f"{day}T22:{minute:02d}:00+09:00",
                    "volume": 100,
                }
                for minute in range(30, 37)
            )

        result = compute_time_normalized_rvol(
            current_rows=current,
            historical_rows=history,
            market="US",
            known_at="2026-07-16T22:36:33",
            session_date="2026-07-16",
            min_sessions=5,
        )

        self.assertEqual(result["rvol_profile_elapsed_min"], 6)
        self.assertEqual(result["rvol_profile_status"], "ok")
        self.assertAlmostEqual(result["time_normalized_rvol"], 2.0)

    def test_same_time_profile_excludes_current_and_future_sessions(self) -> None:
        history: list[dict] = []
        for day in ("2026-07-09", "2026-07-10", "2026-07-13", "2026-07-14", "2026-07-15"):
            history.extend(_rows(day, volume=100))
        history.extend(_rows("2026-07-16", volume=10000))
        history.extend(_rows("2026-07-17", volume=10000))

        result = compute_time_normalized_rvol(
            current_rows=_rows("2026-07-16", volume=200),
            historical_rows=history,
            market="KR",
            known_at="2026-07-16T09:05:00+09:00",
            session_date="2026-07-16",
            lookback_sessions=20,
            min_sessions=5,
        )

        self.assertEqual(result["rvol_profile_sessions"], 5)
        self.assertEqual(result["rvol_expected_cumulative_volume"], 600)
        self.assertEqual(result["rvol_current_cumulative_volume"], 1200)
        self.assertAlmostEqual(result["time_normalized_rvol"], 2.0)

    def test_incomplete_historical_session_is_not_used(self) -> None:
        history: list[dict] = []
        for day in ("2026-07-09", "2026-07-10", "2026-07-13", "2026-07-14", "2026-07-15"):
            history.extend(_rows(day, volume=100))
        history.extend(_rows("2026-07-08", volume=100, through=1))

        result = compute_time_normalized_rvol(
            current_rows=_rows("2026-07-16", volume=100),
            historical_rows=history,
            market="KR",
            known_at="2026-07-16T09:05:00+09:00",
            session_date="2026-07-16",
            min_sessions=5,
        )

        self.assertEqual(result["rvol_profile_sessions"], 5)
        self.assertEqual(result["rvol_profile_status"], "ok")

    def test_live_cache_and_replay_use_the_same_profile_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            price_dir = root / "kr"
            price_dir.mkdir(parents=True)
            price_file = price_dir / "kr_005930.csv"
            lines = ["ts,open,high,low,close,volume,source,collected_at"]
            for day in ("2026-07-09", "2026-07-10", "2026-07-13", "2026-07-14", "2026-07-15"):
                for row in _rows(day, volume=100):
                    lines.append(
                        f"{row['ts']},100,101,99,100,{row['volume']},test,2026-07-16T00:00:00+09:00"
                    )
            price_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            store = LocalTimeNormalizedRvolStore(root=root, min_sessions=5)
            current = _rows("2026-07-16", volume=200)
            cache = IntradayMinuteCache(
                provider=lambda **kwargs: current,
                provider_name="fake",
                rvol_store=store,
            )

            live = cache.get_many(
                market="KR",
                tickers=["005930"],
                session_date="2026-07-16",
                token=None,
                regular_open="2026-07-16T09:00:00+09:00",
                known_at="2026-07-16T09:05:00+09:00",
            )["features_by_ticker"]["005930"]
            replay = store.snapshot(
                current_rows=current,
                market="KR",
                ticker="005930",
                known_at="2026-07-16T09:05:00+09:00",
                session_date="2026-07-16",
            )

        self.assertEqual(live["rvol_profile_method"], "median_prior_same_elapsed")
        self.assertEqual(live["time_normalized_rvol"], replay["time_normalized_rvol"])
        self.assertAlmostEqual(live["time_normalized_rvol"], 2.0)


if __name__ == "__main__":
    unittest.main()
