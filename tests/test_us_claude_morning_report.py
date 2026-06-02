from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.us_claude_morning_report import build_morning_report, write_markdown


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class UsClaudeMorningReportTests(unittest.TestCase):
    def test_combines_monitor_and_quality_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_json(
                base / "final_report.json",
                {
                    "status": "completed",
                    "start_at": "2026-06-01T22:30:00+09:00",
                    "end_at": "2026-06-02T07:00:00+09:00",
                    "mode": "live",
                    "market": "US",
                    "session_date": "2026-06-01",
                    "latest_snapshot": {
                        "api_usage_delta_since_start": {
                            "calls": 2,
                            "input_tokens": 2000,
                            "output_tokens": 400,
                            "cost_usd": 0.12,
                        },
                        "guardian": {
                            "gate": "PASS",
                            "ok": True,
                            "block_start_causes": [
                                {"code": "db.pathb_stale_active_runs", "risk_level": "P1", "message": "stale active rows"}
                            ],
                        },
                        "broker_truth": {
                            "missing": False,
                            "stale": False,
                            "error": "",
                            "positions_count": 3,
                            "open_orders_count": 1,
                            "today_fills_count": 4,
                        },
                    },
                    "claude_usage_since_start": {
                        "calls_since_start_observed_from_raw_files": 2,
                        "by_label": {"select_tickers": 1, "hold_advisor_bull": 1},
                        "by_model": {"claude-test": 2},
                        "tokens_observed_from_raw_files": {
                            "input_tokens": 2000,
                            "output_tokens": 400,
                            "duration_ms": 7000,
                        },
                    },
                    "hold_advisor_cost_observation": {
                        "observed_calls": 1,
                        "by_label": {"hold_advisor_bull": 1},
                    },
                    "decision_events_since_start": [{"ticker": "AAPL"}],
                    "log_issue_counts_since_start": {},
                    "log_issue_samples": [
                        {
                            "at": "2026-06-01T23:00:00+09:00",
                            "kind": "log_warning",
                            "level": "WARNING",
                            "message": "sample warning",
                            "path": "logs/system/live.log",
                        }
                    ],
                    "risk_axes": {"manual_action_required": 0},
                },
            )
            _write_json(
                base / "claude_io_quality.json",
                {
                    "calls": 2,
                    "input_tokens": 2000,
                    "output_tokens": 400,
                    "parse_errors": 1,
                    "avg_input_tokens": 1000.0,
                    "avg_output_tokens": 200.0,
                    "avg_duration_ms": 3500.0,
                    "duration_observed_calls": 1,
                    "duration_missing_calls": 1,
                    "input_issue_counts": {"prompt_input_tokens_ge_8000": 1},
                    "output_issue_counts": {"parse_error": 1},
                    "by_label": [
                        {
                            "label": "select_tickers",
                            "calls": 1,
                            "input_tokens": 9000,
                            "output_tokens": 300,
                            "total_tokens": 9300,
                            "avg_input_tokens": 9000.0,
                            "avg_output_tokens": 300.0,
                            "avg_duration_ms": 0.0,
                            "duration_observed_calls": 0,
                            "duration_missing_calls": 1,
                            "parse_errors": 1,
                            "input_issues": {"prompt_input_tokens_ge_8000": 1},
                            "output_issues": {"parse_error": 1},
                        }
                    ],
                    "by_time_bucket": [
                        {
                            "bucket_start": "2026-06-01T22:30+09:00",
                            "calls": 1,
                            "input_tokens": 9000,
                            "output_tokens": 300,
                            "total_tokens": 9300,
                            "avg_input_tokens": 9000.0,
                            "avg_output_tokens": 300.0,
                            "top_labels": {"select_tickers": 1},
                            "input_issues": {"prompt_input_tokens_ge_8000": 1},
                            "output_issues": {"parse_error": 1},
                        }
                    ],
                    "issue_samples": [
                        {
                            "timestamp": "2026-06-01T23:01:00+09:00",
                            "label": "select_tickers",
                            "input_issues": ["prompt_input_tokens_ge_8000"],
                            "output_issues": ["parse_error"],
                            "path": "logs/raw_calls/sample.json",
                        }
                    ],
                    "prompt_warning_samples": [
                        {
                            "timestamp": "2026-06-01T23:01:00+09:00",
                            "label": "select_tickers",
                            "input_tokens": 9000,
                            "prompt_chars": 25000,
                            "candidate_lines": 35,
                            "evidence_requested_count": 30,
                            "evidence_pack_count": 5,
                            "active_lesson_chars": 199,
                            "top_prompt_sections": [{"section": "candidates", "chars": 14000}],
                            "path": "logs/raw_calls/sample.json",
                        }
                    ],
                    "slow_call_samples": [
                        {
                            "timestamp": "2026-06-01T23:02:00+09:00",
                            "label": "hold_advisor_triage",
                            "duration_ms": 31000,
                            "path": "logs/raw_calls/slow.json",
                        }
                    ],
                    "recommendations": [
                        {
                            "priority": "P1",
                            "area": "parser_safety",
                            "recommendation": "Review parse-error samples.",
                        }
                    ],
                },
            )

            report = build_morning_report(out_dir=base)
            md_path = base / "morning_review.md"
            write_markdown(report, md_path)

            self.assertEqual(report["claude_usage"]["api_usage_delta"]["calls"], 2)
            self.assertEqual(report["claude_usage"]["raw_call_files"], 2)
            self.assertEqual(report["claude_io_quality"]["quality_parse_errors"], 1)
            self.assertEqual(report["claude_io_quality"]["duration_observed_calls"], 1)
            self.assertEqual(report["claude_io_quality"]["duration_missing_calls"], 1)
            self.assertTrue(report["consistency_checks"]["calls_match"])
            self.assertTrue(report["consistency_checks"]["input_tokens_match"])
            self.assertTrue(report["consistency_checks"]["output_tokens_match"])
            self.assertFalse(report["consistency_checks"]["api_negative_delta_detected"])
            self.assertEqual(report["consistency_checks"]["usage_source_for_final_review"], "api_raw_quality_consensus")
            self.assertEqual(report["claude_usage"]["review_usage"]["source"], "api_raw_quality_consensus")
            self.assertEqual(report["claude_usage"]["review_usage"]["calls"], 2)
            self.assertEqual(report["claude_usage"]["review_usage"]["total_tokens"], 2400)
            self.assertTrue(report["claude_usage"]["review_usage"]["api_cost_trusted"])
            self.assertEqual(report["claude_io_quality"]["by_label"][0]["label"], "select_tickers")
            self.assertEqual(report["claude_io_quality"]["by_label"][0]["total_tokens"], 9300)
            self.assertEqual(report["claude_io_quality"]["by_label"][0]["input_issues"]["prompt_input_tokens_ge_8000"], 1)
            self.assertEqual(report["claude_io_quality"]["usage_timeline"][0]["bucket_start"], "2026-06-01T22:30+09:00")
            self.assertEqual(report["claude_io_quality"]["usage_timeline"][0]["top_labels"]["select_tickers"], 1)
            self.assertEqual(report["claude_io_quality"]["usage_timeline_summary"]["status"], "single_bucket")
            self.assertEqual(report["claude_io_quality"]["usage_timeline_summary"]["total_tokens"], 9300)
            self.assertEqual(report["claude_io_quality"]["usage_timeline_summary"]["high_input_bucket_count"], 1)
            self.assertEqual(report["claude_lightweighting"]["status"], "needs_attention")
            self.assertEqual(report["claude_lightweighting"]["selection_share_pct"], 100.0)
            self.assertIn("large_prompt_input_observed", report["claude_lightweighting"]["concerns"])
            self.assertIn("token_lightweighting", {row["area"] for row in report["recommendations"]})
            self.assertEqual(report["evidence_samples"]["quality_issue_samples"][0]["path"], "logs/raw_calls/sample.json")
            self.assertEqual(report["evidence_samples"]["prompt_warning_samples"][0]["candidate_lines"], "35")
            self.assertEqual(report["evidence_samples"]["log_issue_samples"][0]["message"], "sample warning")
            self.assertEqual(report["evidence_samples"]["guardian_block_causes"][0]["code"], "db.pathb_stale_active_runs")
            self.assertEqual(report["recommendations"][0]["area"], "parser_safety")
            markdown = md_path.read_text(encoding="utf-8")
            self.assertIn("US Claude Morning Review", markdown)
            self.assertIn("한국어 요약", markdown)
            self.assertIn("Claude 사용량(검토 기준)", markdown)
            self.assertIn("review_usage: source=api_raw_quality_consensus", markdown)
            self.assertIn("duration_coverage: observed=1 missing=1", markdown)
            self.assertIn("Consistency Checks", markdown)
            self.assertIn("match=True", markdown)
            self.assertIn("Claude Lightweighting", markdown)
            self.assertIn("selection_token_share_high", markdown)
            self.assertIn("Claude I/O By Label", markdown)
            self.assertIn("Claude Usage Timeline", markdown)
            self.assertIn("timeline_summary: status=single_bucket", markdown)
            self.assertIn("select_tickers", markdown)
            self.assertIn("Evidence Samples", markdown)
            self.assertIn("Prompt Warning Samples", markdown)
            self.assertIn("candidates", markdown)
            self.assertIn("logs/raw_calls/sample.json", markdown)
            self.assertIn("sample warning", markdown)

    def test_lightweighting_assessment_separates_front_load_and_light_followups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_json(
                base / "final_report.json",
                {
                    "status": "completed",
                    "latest_snapshot": {
                        "api_usage_delta_since_start": {"calls": 10, "input_tokens": 100000, "output_tokens": 10000},
                        "guardian": {"gate": "PASS", "ok": True},
                        "broker_truth": {"missing": False, "stale": False, "error": ""},
                    },
                    "claude_usage_since_start": {
                        "calls_since_start_observed_from_raw_files": 10,
                        "tokens_observed_from_raw_files": {"input_tokens": 100000, "output_tokens": 10000},
                    },
                },
            )
            _write_json(
                base / "claude_io_quality.json",
                {
                    "calls": 10,
                    "input_tokens": 100000,
                    "output_tokens": 10000,
                    "input_issue_counts": {"prompt_input_tokens_ge_12000": 2},
                    "recommendations": [],
                    "by_label": [
                        {"label": "select_tickers", "calls": 2, "input_tokens": 38000, "output_tokens": 2000, "total_tokens": 40000, "avg_input_tokens": 19000},
                        {"label": "analyst_bull_r1", "calls": 1, "input_tokens": 20000, "output_tokens": 500, "total_tokens": 20500, "avg_input_tokens": 20000},
                        {"label": "analyst_bull_r2", "calls": 1, "input_tokens": 6000, "output_tokens": 500, "total_tokens": 6500, "avg_input_tokens": 6000},
                        {"label": "hold_advisor_triage", "calls": 6, "input_tokens": 8000, "output_tokens": 2000, "total_tokens": 10000, "avg_input_tokens": 1333},
                        {"label": "misc_observer", "calls": 1, "input_tokens": 30000, "output_tokens": 3000, "total_tokens": 33000, "avg_input_tokens": 30000},
                    ],
                },
            )

            report = build_morning_report(out_dir=base)
            assessment = report["claude_lightweighting"]

            self.assertEqual(assessment["status"], "mixed")
            self.assertEqual(assessment["selection_share_pct"], 36.4)
            self.assertEqual(assessment["analyst_r1_share_pct"], 18.6)
            self.assertIn("hold_advisor_cost_control_ok", assessment["positive_signals"])
            self.assertIn("analyst_r2_reduced_vs_r1", assessment["positive_signals"])
            self.assertIn("selection_token_share_high", assessment["concerns"])

    def test_adds_operational_recommendations_for_blocking_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_json(
                base / "final_report.json",
                {
                    "status": "completed",
                    "latest_snapshot": {
                        "guardian": {"gate": "BLOCK_START", "ok": False},
                        "broker_truth": {"missing": False, "stale": True, "error": "", "positions_count": 1},
                    },
                    "risk_axes": {"manual_action_required": 2},
                    "log_issue_counts_since_start": {"order_unknown": 1},
                },
            )
            _write_json(base / "claude_io_quality.json", {"recommendations": []})

            report = build_morning_report(out_dir=base)
            areas = {row["area"] for row in report["recommendations"]}

            self.assertIn("operations", areas)
            self.assertIn("broker_truth", areas)
            self.assertIn("reconciliation", areas)
            self.assertIn("order_state", areas)

    def test_adds_operational_recommendations_from_window_issue_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_json(
                base / "final_report.json",
                {
                    "status": "running",
                    "latest_snapshot": {
                        "guardian": {"gate": "", "ok": None},
                        "broker_truth": {"missing": False, "stale": False, "error": "", "positions_count": 1},
                    },
                    "risk_axes": {"manual_action_required": 0},
                    "log_issue_counts_since_start": {
                        "guardian_block_start": 4,
                        "broker_truth_untrusted": 3,
                    },
                },
            )
            _write_json(base / "claude_io_quality.json", {"recommendations": []})

            report = build_morning_report(out_dir=base)
            areas = {row["area"] for row in report["recommendations"]}

            self.assertIn("operations", areas)
            self.assertIn("broker_truth", areas)

    def test_surfaces_guardian_alert_gate_when_top_level_gate_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_json(
                base / "final_report.json",
                {
                    "status": "running",
                    "latest_snapshot": {
                        "guardian": {"alert": {"gate": "BLOCK_START"}, "ok": None},
                        "broker_truth": {"missing": False, "stale": False, "error": "", "positions_count": 1},
                    },
                    "log_issue_counts_since_start": {},
                },
            )
            _write_json(base / "claude_io_quality.json", {"recommendations": []})

            report = build_morning_report(out_dir=base)
            areas = {row["area"] for row in report["recommendations"]}

            self.assertEqual(report["operations"]["guardian_gate"], "BLOCK_START")
            self.assertIn("operations", areas)

    def test_adds_observability_recommendation_for_usage_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_json(
                base / "final_report.json",
                {
                    "status": "completed",
                    "latest_snapshot": {
                        "api_usage_delta_since_start": {"calls": 3, "input_tokens": 300, "output_tokens": 30},
                        "guardian": {"gate": "PASS", "ok": True},
                        "broker_truth": {"missing": False, "stale": False, "error": ""},
                    },
                    "claude_usage_since_start": {
                        "calls_since_start_observed_from_raw_files": 2,
                        "tokens_observed_from_raw_files": {"input_tokens": 200, "output_tokens": 20},
                    },
                },
            )
            _write_json(
                base / "claude_io_quality.json",
                {
                    "calls": 2,
                    "input_tokens": 200,
                    "output_tokens": 20,
                    "recommendations": [],
                },
            )

            report = build_morning_report(out_dir=base)
            areas = {row["area"] for row in report["recommendations"]}

            self.assertFalse(report["consistency_checks"]["calls_match"])
            self.assertFalse(report["consistency_checks"]["input_tokens_match"])
            self.assertFalse(report["consistency_checks"]["output_tokens_match"])
            self.assertIn("observability", areas)

    def test_flags_negative_api_usage_delta_and_prefers_quality_raw_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_json(
                base / "final_report.json",
                {
                    "status": "running",
                    "latest_snapshot": {
                        "api_usage_delta_since_start": {"calls": -10, "input_tokens": -1000, "output_tokens": -100},
                        "guardian": {"gate": "PASS", "ok": True},
                        "broker_truth": {"missing": False, "stale": False, "error": ""},
                    },
                    "claude_usage_since_start": {
                        "calls_since_start_observed_from_raw_files": 2,
                        "tokens_observed_from_raw_files": {"input_tokens": 200, "output_tokens": 20},
                    },
                },
            )
            _write_json(
                base / "claude_io_quality.json",
                {
                    "calls": 2,
                    "input_tokens": 200,
                    "output_tokens": 20,
                    "recommendations": [],
                },
            )

            report = build_morning_report(out_dir=base)
            consistency = report["consistency_checks"]
            areas = {row["area"] for row in report["recommendations"]}

            self.assertTrue(consistency["api_negative_delta_detected"])
            self.assertEqual(consistency["api_negative_fields"], ["calls", "input_tokens", "output_tokens"])
            self.assertTrue(consistency["raw_quality_calls_match"])
            self.assertTrue(consistency["raw_quality_input_tokens_match"])
            self.assertTrue(consistency["raw_quality_output_tokens_match"])
            self.assertEqual(consistency["usage_source_for_final_review"], "quality_report_raw_call_scan")
            self.assertEqual(report["claude_usage"]["review_usage"]["source"], "quality_report_raw_call_scan")
            self.assertEqual(report["claude_usage"]["review_usage"]["calls"], 2)
            self.assertEqual(report["claude_usage"]["review_usage"]["total_tokens"], 220)
            self.assertFalse(report["claude_usage"]["review_usage"]["api_cost_trusted"])
            self.assertIn("observability", areas)


if __name__ == "__main__":
    unittest.main()
