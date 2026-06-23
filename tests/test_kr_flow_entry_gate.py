from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.kr_flow_entry_gate import (
    evaluate_flow_entry_gate,
    normalize_mode,
    record_flow_entry_gate,
)


class FlowEntryGateEvalTests(unittest.TestCase):
    def test_off_is_noop(self) -> None:
        v = evaluate_flow_entry_gate({"foreign": -100, "institution": -50, "flow_values_trusted": True}, "off")
        self.assertEqual(v["decision"], "off")
        self.assertFalse(v["block"])

    def test_untrusted_flow_fails_open(self) -> None:
        # 수급 결손/untrusted면 enforce여도 막지 않는다(fail-open)
        v = evaluate_flow_entry_gate({"foreign": -100, "institution": -50, "flow_values_trusted": False}, "enforce")
        self.assertEqual(v["decision"], "allow_no_flow")
        self.assertFalse(v["block"])

    def test_missing_flow_fails_open(self) -> None:
        v = evaluate_flow_entry_gate({}, "enforce")
        self.assertEqual(v["decision"], "allow_no_flow")
        self.assertFalse(v["block"])

    def test_negative_flow_shadow_would_skip_no_block(self) -> None:
        v = evaluate_flow_entry_gate({"foreign": -100, "institution": -50, "flow_values_trusted": True}, "shadow")
        self.assertEqual(v["decision"], "would_skip")
        self.assertFalse(v["block"])
        self.assertEqual(v["combined_net"], -150)

    def test_negative_flow_enforce_blocks(self) -> None:
        v = evaluate_flow_entry_gate({"foreign": -100, "institution": -50, "flow_values_trusted": True}, "enforce")
        self.assertEqual(v["decision"], "skip")
        self.assertTrue(v["block"])

    def test_positive_flow_allows(self) -> None:
        v = evaluate_flow_entry_gate({"foreign": 100, "institution": -10, "flow_values_trusted": True}, "enforce")
        self.assertEqual(v["decision"], "allow")
        self.assertFalse(v["block"])

    def test_zero_combined_is_not_negative(self) -> None:
        v = evaluate_flow_entry_gate({"foreign": 50, "institution": -50, "flow_values_trusted": True}, "enforce")
        self.assertEqual(v["decision"], "allow")
        self.assertFalse(v["block"])

    def test_normalize_mode(self) -> None:
        self.assertEqual(normalize_mode("ENFORCE"), "enforce")
        self.assertEqual(normalize_mode("bogus"), "off")
        self.assertEqual(normalize_mode(None), "off")


class FlowEntryGateLogTests(unittest.TestCase):
    def test_off_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("bot.kr_flow_entry_gate.get_runtime_path",
                       lambda *a: Path(tmp) / a[-1]):
                record_flow_entry_gate(
                    session_date="2026-06-23", market="KR", ticker="005930",
                    verdict={"mode": "off", "decision": "off"},
                )
                self.assertEqual(list(Path(tmp).glob("*.jsonl")), [])

    def test_shadow_writes_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("bot.kr_flow_entry_gate.get_runtime_path",
                       lambda *a: Path(tmp) / a[-1]):
                record_flow_entry_gate(
                    session_date="2026-06-23", market="KR", ticker="005930",
                    verdict={"mode": "shadow", "decision": "would_skip", "reason": "flow_negative_combined",
                             "block": False, "combined_net": -150, "flow_values_trusted": True},
                    extra={"limit_price": 1000.0},
                )
                files = list(Path(tmp).glob("*.jsonl"))
                self.assertEqual(len(files), 1)
                rec = json.loads(files[0].read_text(encoding="utf-8").strip())
                self.assertEqual(rec["ticker"], "005930")
                self.assertEqual(rec["decision"], "would_skip")
                self.assertEqual(rec["limit_price"], 1000.0)


if __name__ == "__main__":
    unittest.main()
