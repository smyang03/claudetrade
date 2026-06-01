from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.overnight_us_monitor import (
    _guardian_block_start_causes,
    _hold_advisor_cost_observation,
    _json_digest_summary,
    _news_payload_summary,
    _risk_axes,
)


class OvernightUsMonitorReportTests(unittest.TestCase):
    def test_guardian_block_start_causes_include_action_and_tool(self) -> None:
        causes = _guardian_block_start_causes(
            {
                "gate": "BLOCK_START",
                "findings": [
                    {"code": "db.pathb_stale_active_runs", "message": "stale PathB run"},
                    {"code": "broker_truth.us_stale_state", "message": "US broker truth stale"},
                ],
            },
            {},
            {},
        )

        by_code = {row["code"]: row for row in causes}
        self.assertEqual(by_code["db.pathb_stale_active_runs"]["risk_level"], "P1")
        self.assertTrue(by_code["broker_truth.us_stale_state"]["blocking"])
        self.assertIn("live_preflight", by_code["broker_truth.us_stale_state"]["remediation_tool"])

    def test_hold_advisor_cost_observation_separates_labels_and_bypass_contract(self) -> None:
        payload = _hold_advisor_cost_observation(
            {
                "by_label": {
                    "hold_advisor_bull": 3,
                    "hold_advisor_bear": 2,
                    "selection_rank": 1,
                }
            }
        )

        self.assertEqual(payload["observed_calls"], 5)
        self.assertEqual(payload["by_label"]["hold_advisor_bull"], 3)
        self.assertIn("pathb_auto_sell_hold_cooldown_guard", payload["safety_critical_cache_bypass"])

    def test_risk_axes_summarizes_manual_action_required(self) -> None:
        axes = _risk_axes(
            {
                "open_positions_count": 8,
                "protected_positions": [{"manual_reconciliation_required": True}, {}],
                "pending_sells": [{}],
                "order_unknown_event_count_us_total": 1,
                "guardian": {"gate": "BLOCK_START"},
                "broker_truth": {
                    "stale": True,
                    "missing": False,
                    "error": "",
                    "positions_count": 8,
                    "open_orders_count": 1,
                },
            }
        )

        self.assertEqual(axes["broker_positions"], 8)
        self.assertEqual(axes["broker_open_orders"], 1)
        self.assertEqual(axes["protected_positions"], 2)
        self.assertEqual(axes["manual_action_required"], 3)

    def test_news_payload_summary_counts_corp_news_and_coverage(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "2026-06-01_preopen.json"
            path.write_text(
                """
                {
                  "preopen_snapshot": true,
                  "corp_news": {
                    "NVDA": {"count": 2, "items": [{"title": "A"}, {"title": "B"}]},
                    "TSLA": {"items": [{"title": "C"}]}
                  },
                  "market_news": [{"title": "Market"}],
                  "news_coverage": {"covered_ticker_count": 2, "coverage_ratio": 0.5}
                }
                """,
                encoding="utf-8",
            )

            summary = _news_payload_summary(path)

        self.assertTrue(summary["exists"])
        self.assertTrue(summary["preopen_snapshot"])
        self.assertEqual(summary["corp_news_total"], 3)
        self.assertEqual(summary["corp_news_tickers"], 2)
        self.assertEqual(summary["market_news_count"], 1)
        self.assertEqual(summary["coverage_ratio"], 0.5)

    def test_digest_summary_counts_top_news(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "2026-06-01_US.json"
            path.write_text(
                """
                {
                  "top_news": [{"title": "A"}, {"title": "B"}],
                  "corp_news": {"NVDA": {}, "TSLA": {}},
                  "market_news": [{"title": "Market"}]
                }
                """,
                encoding="utf-8",
            )

            summary = _json_digest_summary(path)

        self.assertTrue(summary["exists"])
        self.assertEqual(summary["top_news_count"], 2)
        self.assertEqual(summary["corp_news_tickers"], 2)
        self.assertEqual(summary["market_news_count"], 1)


if __name__ == "__main__":
    unittest.main()
