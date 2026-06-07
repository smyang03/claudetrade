import json
import tempfile
import unittest
from pathlib import Path

from tools.analyze_hold_advisor_latency import analyze_hold_advisor_latency, to_markdown


class AnalyzeHoldAdvisorLatencyTests(unittest.TestCase):
    def test_analyze_files_groups_single_calls_and_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_dir = root / "raw_calls"
            decision_dir = root / "hold_advisor"
            raw_dir.mkdir()
            decision_dir.mkdir()
            raw_rows = [
                (
                    "20260525_KR_hold_advisor_bull_a.json",
                    {
                        "timestamp": "2026-05-25T09:00:00+09:00",
                        "date": "2026-05-25",
                        "market": "KR",
                        "label": "hold_advisor_bull",
                        "call_id": "a",
                        "model": "claude-sonnet-test",
                        "duration_ms": 100,
                        "tokens": {"input": 10, "output": 2},
                        "parsed": {"action": "HOLD"},
                        "extra": {"decision_stage": "INTRADAY_REVIEW", "review_reason": "soft_review"},
                    },
                ),
                (
                    "20260525_KR_hold_advisor_bull_b.json",
                    {
                        "timestamp": "2026-05-25T09:01:00+09:00",
                        "date": "2026-05-25",
                        "market": "KR",
                        "label": "hold_advisor_bull",
                        "call_id": "b",
                        "model": "claude-sonnet-test",
                        "duration_ms": 300,
                        "tokens": {"input": 12, "output": 3},
                        "parsed": {"action": "SELL"},
                        "extra": {"decision_stage": "INTRADAY_REVIEW"},
                    },
                ),
                (
                    "20260525_US_hold_advisor_bear_c.json",
                    {
                        "timestamp": "2026-05-25T23:01:00+09:00",
                        "date": "2026-05-25",
                        "market": "US",
                        "label": "hold_advisor_bear",
                        "call_id": "c",
                        "model": "claude-sonnet-test",
                        "duration_ms": 900,
                        "tokens": {"input": 20, "output": 4},
                        "parsed": {"action": "HOLD"},
                        "extra": {"decision_stage": "TP_REVIEW"},
                    },
                ),
            ]
            for name, payload in raw_rows:
                (raw_dir / name).write_text(json.dumps(payload), encoding="utf-8")
            (decision_dir / "decisions_2026-05-25.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-05-25T09:02:00+09:00",
                        "market": "KR",
                        "ticker": "005930",
                        "decision_stage": "INTRADAY_REVIEW",
                        "decision": "HOLD",
                        "duration_ms": 500,
                        "input_completeness": {"score": 0.7, "missing": ["target_ok"]},
                        "pathb_revenue_path_context": {
                            "is_pathb": True,
                            "exit_reason": "profit_ladder",
                            "path_run_id": "run-1",
                        },
                        "votes": {
                            "bull": {"action": "HOLD", "duration_ms": 100},
                            "bear": {"action": "SELL", "duration_ms": 300},
                            "neutral": {"action": "HOLD", "duration_ms": 100},
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = analyze_hold_advisor_latency(
                raw_dir=raw_dir,
                decision_dir=decision_dir,
                db_path=root / "missing.db",
                start_date="2026-05-25",
                end_date="2026-05-25",
                source="raw",
            )

        by_analyst = {row["analyst_type"]: row for row in payload["single_calls"]["by_analyst"]}
        self.assertEqual(payload["single_calls"]["summary"]["calls"], 3)
        self.assertEqual(by_analyst["bull"]["calls"], 2)
        self.assertEqual(by_analyst["bull"]["p50_ms"], 200.0)
        self.assertEqual(payload["decision_requests"]["summary"]["p95_ms"], 500.0)
        self.assertEqual(payload["decision_requests"]["summary"]["completeness_low_count"], 1)
        path_rows = payload["decision_requests"]["by_pathb_revenue_path_decision"]
        self.assertEqual(path_rows[0]["pathb_revenue_exit_reason"], "profit_ladder")
        self.assertEqual(path_rows[0]["decision"], "HOLD")
        self.assertEqual(payload["decision_votes"]["summary"]["calls"], 3)
        self.assertIn("Hold Advisor Latency Report", to_markdown(payload))
        self.assertIn("By PathB Revenue Path / Decision", to_markdown(payload))


if __name__ == "__main__":
    unittest.main()
