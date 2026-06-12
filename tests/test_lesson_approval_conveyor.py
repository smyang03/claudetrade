"""교훈 승인 컨베이어 — 승인 저장소/주입 게이팅/truth 오버라이드."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minority_report import lesson_approvals as la


class LessonApprovalStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._patch = patch.object(la, "_path", return_value=self.root / "state" / "lesson_approvals.json")
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()

    def test_roundtrip_and_persistence(self):
        self.assertEqual(la.approval_status("watch_only_missed_runup_review"), "")
        self.assertTrue(la.set_approval("watch_only_missed_runup_review", "approved"))
        self.assertEqual(la.approval_status("watch_only_missed_runup_review"), "approved")
        self.assertTrue(la.set_approval("bad_lesson", "rejected"))
        self.assertEqual(la.approval_status("bad_lesson"), "rejected")

    def test_invalid_status_refused(self):
        self.assertFalse(la.set_approval("x", "maybe"))
        self.assertFalse(la.set_approval("", "approved"))


class ActiveLessonGatingTests(unittest.TestCase):
    def test_unapproved_items_are_ignored(self):
        from minority_report.active_lessons import _select_items

        fake_items = [
            {"id": "approved_one", "text": "lesson A", "source": "ops_review"},
            {"id": "pending_one", "text": "lesson B", "source": "ops_review"},
        ]
        with patch("minority_report.active_lessons._collect_lesson_candidate_items", return_value=list(fake_items)),              patch("minority_report.active_lessons._collect_brain_items", return_value=[]),              patch("minority_report.active_lessons._score_item", return_value=1.0),              patch.dict(os.environ, {"LESSONS_REQUIRE_APPROVAL": "true"}),              patch("minority_report.lesson_approvals.approval_status",
                   side_effect=lambda lid: "approved" if lid == "approved_one" else ""):
            selected, ignored = _select_items("KR", 5, prompt_scope="r1")
        self.assertEqual([i["id"] for i in selected], ["approved_one"])
        self.assertIn("approval_pending", [i.get("reason") for i in ignored])


class TruthStatusOverrideTests(unittest.TestCase):
    def test_approved_postmortem_lesson_passes_summary_gate(self):
        import json
        from trading_bot import TradingBot
        import trading_bot as tb

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lesson_candidates.json"
            path.write_text(json.dumps({"markets": {"KR": [{
                "id": "pattern_lesson", "source": "postmortem", "breached": True,
                "scope": "selection", "sample_count": 10, "min_sample": 3,
                "action_hint": "specific rule", "summary": "패턴 교훈",
                "severity": "high", "confidence": 0.9,
            }]}}), encoding="utf-8")
            bot = TradingBot.__new__(TradingBot)
            bot._current_session_date_str = lambda mk: "2026-06-12"
            with patch.object(tb, "_LESSON_CANDIDATES_PATH", path),                  patch("minority_report.lesson_approvals.approval_status", return_value="approved"):
                summary = bot._load_lesson_candidate_summary("KR")
            self.assertIn("specific rule", summary or "패턴" in (summary or ""))

    def test_unapproved_postmortem_lesson_blocked(self):
        import json
        from trading_bot import TradingBot
        import trading_bot as tb

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lesson_candidates.json"
            path.write_text(json.dumps({"markets": {"KR": [{
                "id": "pattern_lesson", "source": "postmortem", "breached": True,
                "scope": "selection", "sample_count": 10, "min_sample": 3,
                "action_hint": "specific rule", "summary": "패턴 교훈",
            }]}}), encoding="utf-8")
            bot = TradingBot.__new__(TradingBot)
            bot._current_session_date_str = lambda mk: "2026-06-12"
            with patch.object(tb, "_LESSON_CANDIDATES_PATH", path),                  patch("minority_report.lesson_approvals.approval_status", return_value=""):
                summary = bot._load_lesson_candidate_summary("KR")
            self.assertEqual(summary, "")


if __name__ == "__main__":
    unittest.main()
