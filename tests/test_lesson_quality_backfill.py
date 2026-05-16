from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tools import backfill_lesson_candidate_quality as backfill


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LessonQualityBackfillTests(unittest.TestCase):
    def test_dry_run_does_not_modify_file_and_write_creates_backup(self) -> None:
        payload = {
            "markets": {
                "KR": [
                    {
                        "id": "watch_only_missed_runup_review",
                        "scope": "selection",
                        "metric_key": "watch_only_missed_runup_ratio",
                        "metric_value": 66.5,
                        "sample_count": 224,
                        "breached": True,
                        "severity": "high",
                        "confidence": 0.95,
                    },
                    {
                        "id": "affordability_fail_cluster",
                        "scope": "execution",
                        "metric_key": "affordability_fail_count",
                        "metric_value": 3,
                        "sample_count": 3,
                        "breached": True,
                        "severity": "medium",
                        "confidence": 0.8,
                    },
                ]
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lesson_candidates.json"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            before = _sha(path)

            with patch.object(sys, "argv", ["backfill", "--path", str(path), "--dry-run"]), redirect_stdout(StringIO()):
                backfill.main()

            self.assertEqual(_sha(path), before)

            with patch.object(sys, "argv", ["backfill", "--path", str(path), "--write"]), redirect_stdout(StringIO()):
                backfill.main()

            backups = list(Path(tmp).glob("lesson_candidates.backup_*.json"))
            self.assertEqual(len(backups), 1)
            updated = json.loads(path.read_text(encoding="utf-8"))
            rows = updated["markets"]["KR"]
            watch = next(row for row in rows if row["id"] == "watch_only_missed_runup_review")
            affordability = next(row for row in rows if row["id"] == "affordability_fail_cluster")
            self.assertTrue(watch["claude_actionable"])
            self.assertIn("66.5%", watch["action_hint"])
            self.assertTrue(affordability["ops_flag"])
            self.assertFalse(affordability["claude_actionable"])

    def test_conflict_guard_changes_are_reported(self) -> None:
        watch_fields = backfill.lesson_quality_fields("watch_only_missed_runup_ratio", "selection", 70.0, 50)
        trade_fields = backfill.lesson_quality_fields("trade_ready_signal_conversion", "selection", 10.0, 50)
        payload = {
            "markets": {
                "KR": [
                    {
                        "id": "watch",
                        "scope": "selection",
                        "metric_key": "watch_only_missed_runup_ratio",
                        "metric_value": 70.0,
                        "sample_count": 50,
                        "breached": True,
                        "severity": "high",
                        "confidence": 0.9,
                        **watch_fields,
                    },
                    {
                        "id": "trade",
                        "scope": "selection",
                        "metric_key": "trade_ready_signal_conversion",
                        "metric_value": 10.0,
                        "sample_count": 50,
                        "breached": True,
                        "severity": "medium",
                        "confidence": 0.8,
                        **trade_fields,
                    },
                ]
            }
        }

        updated, changes = backfill._backfill_payload(payload)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["id"], "trade")
        trade = next(row for row in updated["markets"]["KR"] if row["id"] == "trade")
        self.assertTrue(trade["quality_conflict_suppressed"])
        self.assertFalse(trade["claude_actionable"])


if __name__ == "__main__":
    unittest.main()
