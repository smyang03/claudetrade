from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import live_preflight


class LivePreflightMlAndBrainTests(unittest.TestCase):
    def test_ml_db_schema_missing_is_live_start_blocking_warn(self) -> None:
        health = {
            "exists": True,
            "total_rows": 10,
            "live_rows": 10,
            "gaps": {"known_unrecoverable_ranges": [], "rows_inside_known_gap": 0},
            "warnings": [],
            "errors": [],
        }
        missing = {"decisions": ["data_source", "is_simulated"]}
        with patch("ml.db_health.check_db_health", return_value=health), patch(
            "ml.db_writer.schema_missing_columns",
            return_value=missing,
        ):
            checks = live_preflight._ml_db_health_checks("live")

        self.assertEqual(len(checks), 1)
        check = checks[0]
        self.assertEqual(check.status, "WARN")
        self.assertEqual(check.data["schema_missing_columns"], missing)
        self.assertTrue(check.data["operator_action_required"])
        self.assertTrue(check.data["blocked_if_live_start"])

    def test_brain_memory_dirty_state_warns_without_writing_brain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain.json"
            queue = root / "brain_approval_queue.jsonl"
            brain.write_text(json.dumps({"version": "test", "last_updated": "2026-05-30"}), encoding="utf-8")
            before = brain.read_text(encoding="utf-8")

            with patch(
                "tools.live_preflight._git_porcelain_for_path",
                return_value=([" M state/brain.json"], ""),
            ):
                check = live_preflight._brain_memory_change_check(
                    "live",
                    brain_path=brain,
                    approval_queue_path=queue,
                )

            self.assertEqual(check.status, "WARN")
            self.assertTrue(check.data["git_dirty"])
            self.assertTrue(check.data["operator_action_required"])
            self.assertIn("sha256", check.data)
            self.assertEqual(brain.read_text(encoding="utf-8"), before)

    def test_brain_memory_clean_state_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain.json"
            queue = root / "brain_approval_queue.jsonl"
            brain.write_text(json.dumps({"version": "test"}), encoding="utf-8")

            with patch("tools.live_preflight._git_porcelain_for_path", return_value=([], "")):
                check = live_preflight._brain_memory_change_check(
                    "live",
                    brain_path=brain,
                    approval_queue_path=queue,
                )

            self.assertEqual(check.status, "PASS")
            self.assertFalse(check.data["git_dirty"])


if __name__ == "__main__":
    unittest.main()
