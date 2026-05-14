from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.analyze_broker_sync_cases import analyze_broker_sync_cases


class AnalyzeBrokerSyncCasesTests(unittest.TestCase):
    def test_collects_broker_sync_closed_case_with_review_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live_decisions.jsonl"
            rows = [
                {
                    "type": "auto_sell_review",
                    "timestamp": "2026-05-01T23:00:00+09:00",
                    "session_date": "2026-05-01",
                    "market": "US",
                    "ticker": "AAPL",
                    "auto_sell_review_action": "SELL",
                    "auto_sell_review_detail": "stop broken",
                },
                {
                    "type": "closed",
                    "timestamp": "2026-05-01T23:05:00+09:00",
                    "session_date": "2026-05-01",
                    "market": "US",
                    "ticker": "AAPL",
                    "strategy": "broker_sync",
                    "exit_reason": "trail_stop",
                    "pnl_pct": -1.2,
                    "order_no": "001",
                },
            ]
            path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

            summary = analyze_broker_sync_cases(decisions_path=path, date_arg="2026-05-01", market="US")

        self.assertEqual(summary["case_count"], 1)
        self.assertEqual(summary["loss_count"], 1)
        case = summary["cases"][0]
        self.assertEqual(case["ticker"], "AAPL")
        self.assertEqual(case["auto_sell_review_action"], "SELL")


if __name__ == "__main__":
    unittest.main()
