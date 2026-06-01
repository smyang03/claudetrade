from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.claude_io_quality_report import build_quality_report, write_markdown


def _write_raw(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class ClaudeIoQualityReportTests(unittest.TestCase):
    def test_selection_quality_flags_non_strict_json_and_schema_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            _write_raw(
                raw_dir / "selection.json",
                {
                    "timestamp": "2026-06-01T22:35:00+09:00",
                    "market": "US",
                    "label": "select_tickers",
                    "model": "claude-test",
                    "prompt": "MACHINE-COMPACT OUTPUT CONTRACT. Return strict JSON only.",
                    "raw_response": "Here is the JSON:\n```json\n{\"wl\":[\"AAPL\",\"AAPL\"],\"tr\":[\"MSFT\"],\"ca\":[]}\n```",
                    "parsed": {"wl": ["AAPL", "AAPL"], "tr": ["MSFT"], "ca": []},
                    "tokens": {"input": 9000, "output": 300},
                    "duration_ms": 1200,
                },
            )

            report = build_quality_report(
                raw_dir=raw_dir,
                market="US",
                start="2026-06-01T22:30:00+09:00",
                end="2026-06-02T07:00:00+09:00",
            )

            self.assertEqual(report["calls"], 1)
            self.assertEqual(report["input_issue_counts"]["prompt_input_tokens_ge_8000"], 1)
            self.assertEqual(report["duration_observed_calls"], 1)
            self.assertEqual(report["duration_missing_calls"], 0)
            self.assertEqual(report["avg_duration_ms"], 1200.0)
            self.assertEqual(report["by_label"][0]["total_tokens"], 9300)
            self.assertEqual(report["output_issue_counts"]["response_not_strict_json"], 1)
            self.assertEqual(report["output_issue_counts"]["response_has_preamble_or_wrapper"], 1)
            self.assertEqual(report["output_issue_counts"]["duplicate_watchlist_ticker"], 1)
            self.assertEqual(report["output_issue_counts"]["trade_ready_not_in_watchlist"], 1)
            self.assertEqual(report["prompt_warning_samples"][0]["label"], "select_tickers")
            self.assertEqual(report["prompt_warning_samples"][0]["input_tokens"], 9000)
            self.assertEqual(report["by_time_bucket"][0]["bucket_start"], "2026-06-01T22:30+09:00")
            self.assertEqual(report["by_time_bucket"][0]["calls"], 1)
            self.assertEqual(report["by_time_bucket"][0]["top_labels"]["select_tickers"], 1)

    def test_hold_advisor_quality_flags_missing_hold_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            _write_raw(
                raw_dir / "hold.json",
                {
                    "timestamp": "2026-06-01T23:10:00",
                    "market": "US",
                    "label": "hold_advisor_bull",
                    "model": "claude-test",
                    "prompt": "Decision contract. Return strict JSON only.",
                    "raw_response": "{\"action\":\"HOLD\",\"confidence\":0.7}",
                    "parsed": {"action": "HOLD", "confidence": 0.7},
                    "tokens": {"input": 1000, "output": 100},
                    "duration_ms": 31000,
                },
            )

            report = build_quality_report(
                raw_dir=raw_dir,
                market="US",
                start="2026-06-01T22:30:00+09:00",
                end="2026-06-02T07:00:00+09:00",
            )

            output = report["output_issue_counts"]
            self.assertEqual(output["hold_boundary_missing_protective_stop"], 1)
            self.assertEqual(output["hold_boundary_missing_invalid_if"], 1)
            self.assertEqual(output["hold_boundary_missing_next_review_min"], 1)
            self.assertEqual(output["slow_call_30s"], 1)

    def test_hold_advisor_accepts_category_contract_and_flags_response_mojibake(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            _write_raw(
                raw_dir / "triage.json",
                {
                    "timestamp": "2026-06-01T23:10:00",
                    "market": "US",
                    "label": "hold_advisor_triage",
                    "model": "claude-test",
                    "prompt": "Decision contract. Return strict JSON only.",
                    "raw_response": "```json\n{\"category\":\"HOLD\",\"confidence\":0.7,\"protective_stop\":100,\"invalid_if\":\"breaks stop\",\"next_review_min\":30,\"reason\":\"N/A ??price review\"}\n```",
                    "parsed": {
                        "category": "HOLD",
                        "confidence": 0.7,
                        "protective_stop": 100,
                        "invalid_if": "breaks stop",
                        "next_review_min": 30,
                        "reason": "N/A ??price review",
                    },
                    "tokens": {"input": 1000, "output": 100},
                },
            )
            _write_raw(
                raw_dir / "challenge.json",
                {
                    "timestamp": "2026-06-01T23:11:00",
                    "market": "US",
                    "label": "hold_advisor_challenge",
                    "model": "claude-test",
                    "prompt": "Decision contract. Return strict JSON only.",
                    "raw_response": "{\"final_category\":\"SELL\",\"confidence\":0.8}",
                    "parsed": {"final_category": "SELL", "confidence": 0.8},
                    "tokens": {"input": 900, "output": 80},
                },
            )

            report = build_quality_report(
                raw_dir=raw_dir,
                market="US",
                start="2026-06-01T22:30:00+09:00",
                end="2026-06-02T07:00:00+09:00",
            )

            output = report["output_issue_counts"]
            self.assertNotIn("hold_advisor_action_missing", output)
            self.assertNotIn("hold_boundary_missing_protective_stop", output)
            self.assertEqual(output["response_mojibake_double_question_mark"], 1)

    def test_recommends_token_cost_for_single_large_prompt_even_when_average_is_lower(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            _write_raw(
                raw_dir / "large.json",
                {
                    "timestamp": "2026-06-01T23:10:00",
                    "market": "US",
                    "label": "select_tickers",
                    "model": "claude-test",
                    "prompt": "MACHINE-COMPACT OUTPUT CONTRACT. Return strict JSON only.",
                    "raw_response": "{\"wl\":[],\"tr\":[],\"ca\":[]}",
                    "parsed": {"wl": [], "tr": [], "ca": []},
                    "tokens": {"input": 9000, "output": 100},
                },
            )
            _write_raw(
                raw_dir / "small.json",
                {
                    "timestamp": "2026-06-01T23:11:00",
                    "market": "US",
                    "label": "tune_30min",
                    "model": "claude-test",
                    "prompt": "Return strict JSON only.",
                    "raw_response": "{}",
                    "parsed": {},
                    "tokens": {"input": 100, "output": 10},
                },
            )

            report = build_quality_report(
                raw_dir=raw_dir,
                market="US",
                start="2026-06-01T22:30:00+09:00",
                end="2026-06-02T07:00:00+09:00",
            )

            self.assertLess(report["avg_input_tokens"], 8000)
            self.assertEqual(report["input_issue_counts"]["prompt_input_tokens_ge_8000"], 1)
            areas = {row["area"] for row in report["recommendations"]}
            self.assertIn("token_cost", areas)

    def test_prompt_warning_samples_describe_large_selection_prompt_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            prompt = "\n".join(
                [
                    "EXECUTABLE OPENING/INTRADAY SELECTION for US",
                    "Candidates:",
                    "NVDA chg=+3.0% rs=(SP+3.1%/NQ+3.0%) p=100 vol=1.0x turn=$1000M ev=FULL_PACK",
                    "MSFT chg=+2.0% rs=(SP+2.1%/NQ+2.0%) p=200 vol=1.0x turn=$900M ev=FULL_PACK",
                    "Runtime evidence pack (use as facts; soft-gate override must match these values):",
                    "NVDA evidence block",
                    "Market context:",
                    "mode=MILD_BULL",
                    "[active lessons]",
                    "- lesson",
                    "MACHINE-COMPACT OUTPUT CONTRACT.",
                    "Return strict JSON only.",
                    "Rules:",
                    "- Choose only from supplied candidates.",
                ]
            )
            _write_raw(
                raw_dir / "large_selection.json",
                {
                    "timestamp": "2026-06-01T23:10:00",
                    "market": "US",
                    "label": "select_tickers",
                    "model": "claude-test",
                    "prompt": prompt,
                    "raw_response": "{\"wl\":[],\"tr\":[],\"ca\":[]}",
                    "parsed": {"wl": [], "tr": [], "ca": []},
                    "tokens": {"input": 12001, "output": 100},
                    "extra": {
                        "evidence_requested_count": 30,
                        "evidence_pack_count": 5,
                        "evidence_omitted_count": 4,
                        "compact_schema_enabled": True,
                        "compact_evidence_pack_enabled": False,
                        "active_lessons": {"count": 1, "chars": 199},
                    },
                },
            )

            report = build_quality_report(
                raw_dir=raw_dir,
                market="US",
                start="2026-06-01T22:30:00+09:00",
                end="2026-06-02T07:00:00+09:00",
            )

            warning = report["prompt_warning_samples"][0]
            self.assertEqual(warning["candidate_lines"], 2)
            self.assertEqual(warning["evidence_requested_count"], 30)
            self.assertEqual(warning["evidence_pack_count"], 5)
            self.assertEqual(warning["active_lesson_chars"], 199)
            self.assertIn("prompt_input_tokens_ge_12000", warning["input_issues"])
            sections = {row["section"] for row in warning["top_prompt_sections"]}
            self.assertIn("candidates", sections)
            self.assertIn("runtime_evidence", sections)

    def test_filters_market_and_time_and_writes_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            _write_raw(
                raw_dir / "in.json",
                {
                    "timestamp": "2026-06-01T22:40:00+09:00",
                    "market": "US",
                    "label": "hold_advisor_bear",
                    "model": "claude-test",
                    "prompt": "Return strict JSON only.",
                    "raw_response": "{\"action\":\"SELL\",\"confidence\":0.8}",
                    "parsed": {"action": "SELL", "confidence": 0.8},
                    "tokens": {"input": 10, "output": 5},
                },
            )
            _write_raw(
                raw_dir / "out_market.json",
                {
                    "timestamp": "2026-06-01T22:40:00+09:00",
                    "market": "KR",
                    "label": "select_tickers",
                    "model": "claude-test",
                    "prompt": "Return strict JSON only.",
                    "raw_response": "{}",
                    "parsed": {},
                    "tokens": {"input": 10, "output": 5},
                },
            )
            _write_raw(
                raw_dir / "out_time.json",
                {
                    "timestamp": "2026-06-01T21:40:00+09:00",
                    "market": "US",
                    "label": "select_tickers",
                    "model": "claude-test",
                    "prompt": "Return strict JSON only.",
                    "raw_response": "{}",
                    "parsed": {},
                    "tokens": {"input": 10, "output": 5},
                },
            )

            report = build_quality_report(
                raw_dir=raw_dir,
                market="US",
                start="2026-06-01T22:30:00+09:00",
                end="2026-06-02T07:00:00+09:00",
            )
            md_path = Path(tmp) / "report.md"
            write_markdown(report, md_path)

            self.assertEqual(report["calls"], 1)
            self.assertEqual(report["input_tokens"], 10)
            self.assertEqual(report["duration_observed_calls"], 0)
            self.assertEqual(report["duration_missing_calls"], 1)
            self.assertIn("Claude I/O Quality Report", md_path.read_text(encoding="utf-8"))
            self.assertIn("Usage Timeline", md_path.read_text(encoding="utf-8"))
            self.assertIn("duration_coverage: observed=0 missing=1", md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
