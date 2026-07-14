from __future__ import annotations

from datetime import datetime, timedelta
import unittest

import pandas as pd

from bot.session_date import KST
from preopen.yfinance_shadow import (
    PRIMARY_PRICE_SOURCE,
    compare_kr_primary_to_yfinance,
    fetch_kr_yfinance_quote,
    select_fresh_kis_primary_samples,
)


class KrYfinanceShadowTests(unittest.TestCase):
    def test_fetch_uses_kq_after_empty_ks_without_promoting_price(self) -> None:
        calls: list[str] = []
        bar_at = datetime(2026, 7, 14, 10, 5, tzinfo=KST)

        def fetch(symbol: str):
            calls.append(symbol)
            if symbol.endswith(".KS"):
                return pd.DataFrame()
            return pd.DataFrame(
                {"Open": [100.0], "High": [103.0], "Low": [99.0], "Close": [101.0], "Volume": [1234]},
                index=pd.DatetimeIndex([bar_at]),
            )

        quote = fetch_kr_yfinance_quote("123456", history_fetcher=fetch)

        self.assertEqual(calls, ["123456.KS", "123456.KQ"])
        self.assertEqual(quote["status"], "ok")
        self.assertEqual(quote["symbol"], "123456.KQ")
        self.assertEqual(quote["price"], 101.0)
        self.assertFalse(quote.get("execution_eligible", False))

    def test_comparison_marks_stale_and_divergent_quote_without_order_signal(self) -> None:
        now = datetime(2026, 7, 14, 10, 30, tzinfo=KST)
        result = compare_kr_primary_to_yfinance(
            {
                "ticker": "005930",
                "price": 100.0,
                "captured_at": now.isoformat(),
                "price_source": PRIMARY_PRICE_SOURCE,
            },
            {
                "status": "ok",
                "ticker": "005930",
                "symbol": "005930.KS",
                "price": 102.0,
                "bar_at": (now - timedelta(minutes=30)).isoformat(),
            },
            captured_at=now,
            divergence_warn_pct=1.0,
            max_stale_min=20,
        )

        self.assertEqual(result["comparison_status"], "stale_and_divergent")
        self.assertEqual(result["price_diff_pct"], 2.0)
        self.assertFalse(result["execution_eligible"])
        self.assertFalse(result["selection_input"])

    def test_selection_accepts_only_explicitly_fresh_kis_rows(self) -> None:
        rows = [
            {"ticker": "005930", "price": 100, "captured_at": "2026-07-14T10:00:00+09:00", "price_source": "kis_api.get_price"},
            {"ticker": "005930", "price": 101, "captured_at": "2026-07-14T10:05:00+09:00", "price_source": PRIMARY_PRICE_SOURCE},
            {"ticker": "000660", "price": 200, "captured_at": "2026-07-14T10:06:00+09:00", "price_source": PRIMARY_PRICE_SOURCE},
        ]

        selected = select_fresh_kis_primary_samples(rows, rank_by_ticker={"005930": 1, "000660": 2}, max_tickers=10)

        self.assertEqual([row["ticker"] for row in selected], ["005930", "000660"])
        self.assertEqual(selected[0]["price"], 101)


if __name__ == "__main__":
    unittest.main()
