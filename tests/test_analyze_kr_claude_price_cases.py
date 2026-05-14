from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.analyze_kr_claude_price_cases import analyze_kr_claude_price_cases


class AnalyzeKrClaudePriceCasesTests(unittest.TestCase):
    def test_groups_entry_and_closed_case_by_path_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live_decisions.jsonl"
            rows = [
                {
                    "type": "entry",
                    "timestamp": "2026-05-01T09:10:00+09:00",
                    "session_date": "2026-05-01",
                    "market": "KR",
                    "ticker": "005930",
                    "strategy": "claude_price",
                    "pathb_path_run_id": "path_1",
                    "entry_price_native": 1000,
                    "broker_fill_source": "pathb_broker_truth",
                },
                {
                    "type": "closed",
                    "timestamp": "2026-05-01T09:40:00+09:00",
                    "session_date": "2026-05-01",
                    "market": "KR",
                    "ticker": "005930",
                    "strategy": "claude_price",
                    "pathb_path_run_id": "path_1",
                    "exit_price_native": 950,
                    "exit_reason": "loss_cap",
                    "pnl_pct": -5.0,
                    "position_mfe_pct": 1.0,
                    "position_mae_pct": -5.0,
                },
            ]
            path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

            summary = analyze_kr_claude_price_cases(decisions_path=path, date_arg="2026-05-01")

        self.assertEqual(summary["case_count"], 1)
        self.assertEqual(summary["loss_count"], 1)
        case = summary["cases"][0]
        self.assertEqual(case["ticker"], "005930")
        self.assertEqual(case["exit_reason"], "loss_cap")
        self.assertEqual(case["mfe_pct"], 1.0)


if __name__ == "__main__":
    unittest.main()
