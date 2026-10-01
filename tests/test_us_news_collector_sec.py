"""SEC 공시 수집 — 해당 종목 자신의 공시만 붙는지 (2026-09-29 수리 회귀 방지)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import phase1_trainer.us_news_collector as collector
from preopen.news_quality import score_news_item


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


SUBMISSIONS = {
    "name": "Apple Inc.",
    "filings": {"recent": {
        "form": ["8-K", "4", "10-Q", "8-K"],
        "filingDate": ["2026-07-30", "2026-07-30", "2026-07-31", "2026-07-29"],
        "accessionNumber": ["0000320193-26-000018", "0000320193-26-000019", "0000320193-26-000020", "0000320193-26-000017"],
        "primaryDocument": ["aapl-20260730.htm", "x.xml", "aapl-20260627.htm", "y.htm"],
        "items": ["2.02,9.01", "", "", "5.02"],
    }},
}


class SecFilingsTest(unittest.TestCase):
    def setUp(self):
        collector._SEC_TICKER_MAP = {"AAPL": 320193, "BRK-B": 1067983}

    def tearDown(self):
        collector._SEC_TICKER_MAP = None

    def test_only_own_filings_on_target_date(self):
        with patch.object(collector.requests, "get", return_value=_resp(SUBMISSIONS)) as get:
            out = collector.fetch_sec_filings("AAPL", "2026-07-30")
        self.assertIn("CIK0000320193.json", get.call_args[0][0])
        self.assertEqual(len(out), 1)                       # Form 4, 다른 날짜 제외
        item = out[0]
        self.assertEqual(item["form_type"], "8-K")
        self.assertEqual(item["items"], "2.02,9.01")
        self.assertEqual(item["source_type"], "disclosure")
        self.assertEqual(item["ticker"], "AAPL")
        self.assertIn("Apple Inc.", item["title"])
        self.assertTrue(item["url"].endswith("/320193/000032019326000018/aapl-20260730.htm"))

    def test_unknown_ticker_returns_empty_without_request(self):
        with patch.object(collector.requests, "get") as get:
            self.assertEqual(collector.fetch_sec_filings("ZZZZ", "2026-07-30"), [])
        get.assert_not_called()

    def test_dot_ticker_maps_to_dash(self):
        self.assertEqual(collector._sec_ticker_to_cik("BRK.B"), 1067983)

    def test_own_filing_is_prompt_eligible_disclosure(self):
        with patch.object(collector.requests, "get", return_value=_resp(SUBMISSIONS)):
            item = collector.fetch_sec_filings("AAPL", "2026-07-30")[0]
        q = score_news_item("US", "AAPL", "Apple Inc.", item)
        self.assertNotEqual(q["signal_type"], "weak_generic")
        self.assertTrue(q["prompt_eligible"])


if __name__ == "__main__":
    unittest.main()
