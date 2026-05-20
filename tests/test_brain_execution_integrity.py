from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from filelock import FileLock

from claude_memory import brain as BrainDB
from tools.reconcile_brain_execution_flags import run as reconcile_execution_flags


def _minimal_brain() -> dict:
    return {
        "meta": {"version": 0, "trained_days_kr": 0, "trained_days_us": 0},
        "markets": {
            "KR": {"trained_days": 0, "recent_days": [], "current_beliefs": {}},
            "US": {"trained_days": 0, "recent_days": [], "current_beliefs": {}},
        },
        "correction_guide": {"KR": {}, "US": {}},
    }


class BrainExecutionIntegrityTests(unittest.TestCase):
    def test_brain_save_is_atomic_and_removes_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "brain.json"
            original_path = BrainDB.BRAIN_PATH
            original_lock = BrainDB._BRAIN_LOCK
            try:
                BrainDB.BRAIN_PATH = target
                BrainDB._BRAIN_LOCK = FileLock(str(target) + ".lock", timeout=1)
                BrainDB.save(_minimal_brain())
            finally:
                BrainDB.BRAIN_PATH = original_path
                BrainDB._BRAIN_LOCK = original_lock

            saved = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(saved["meta"]["version"], 1)
            self.assertFalse(list(Path(tmp).glob(".brain.json.*.tmp")))

    def test_reconcile_execution_flags_dry_run_does_not_write_brain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain_path = root / "brain.json"
            log_dir = root / "logs" / "daily_judgment"
            log_dir.mkdir(parents=True)
            payload = _minimal_brain()
            payload["markets"]["KR"]["recent_days"] = [
                {"date": "2026-05-08", "key_lesson": "old", "issue_type": "selection"}
            ]
            brain_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            (log_dir / "live_20260508_KR.json").write_text(
                json.dumps(
                    {
                        "market": "KR",
                        "session_date": "2026-05-08",
                        "actual_result": {
                            "execution_contaminated": True,
                            "execution_issues": ["broker_sync_trade"],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            summary = reconcile_execution_flags(brain_path=brain_path, log_dir=log_dir, apply=False)

            self.assertEqual(summary["change_count"], 1)
            unchanged = json.loads(brain_path.read_text(encoding="utf-8"))
            row = unchanged["markets"]["KR"]["recent_days"][0]
            self.assertEqual(row["key_lesson"], "old")
            self.assertNotIn("execution_learning_excluded", row)

    def test_reconcile_execution_flags_apply_backs_up_and_updates_matching_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain_path = root / "brain.json"
            log_dir = root / "logs" / "daily_judgment"
            log_dir.mkdir(parents=True)
            payload = _minimal_brain()
            payload["markets"]["US"]["recent_days"] = [
                {"date": "2026-05-08", "key_lesson": "old", "issue_type": "execution"}
            ]
            brain_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            (log_dir / "live_20260508_US.json").write_text(
                json.dumps(
                    {
                        "market": "US",
                        "session_date": "2026-05-08",
                        "actual_result": {
                            "execution_contaminated": True,
                            "execution_issues": ["broker_sync_trade"],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            summary = reconcile_execution_flags(brain_path=brain_path, log_dir=log_dir, apply=True)

            self.assertEqual(summary["change_count"], 1)
            self.assertTrue(Path(summary["backup_path"]).exists())
            self.assertIn("brain.json.backup_", Path(summary["backup_path"]).name)
            updated = json.loads(brain_path.read_text(encoding="utf-8"))
            row = updated["markets"]["US"]["recent_days"][0]
            self.assertTrue(row["execution_contaminated"])
            self.assertTrue(row["execution_learning_excluded"])
            self.assertTrue(row["prompt_policy_excluded"])
            self.assertEqual(row["policy_exclusion_reason"], "execution_learning_excluded")
            self.assertEqual(row["execution_issues"], ["broker_sync_trade"])
            self.assertEqual(row["key_lesson"], "")
            self.assertEqual(row["issue_type"], "")

    def test_reconcile_warning_only_issue_is_not_learning_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain_path = root / "brain.json"
            log_dir = root / "logs" / "daily_judgment"
            log_dir.mkdir(parents=True)
            payload = _minimal_brain()
            payload["markets"]["KR"]["recent_days"] = [
                {"date": "2026-05-07", "key_lesson": "keep", "issue_type": "selection"}
            ]
            brain_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            (log_dir / "live_20260507_KR.json").write_text(
                json.dumps(
                    {
                        "market": "KR",
                        "session_date": "2026-05-07",
                        "actual_result": {
                            "execution_contaminated": True,
                            "execution_issues": ["broker_position_removed"],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            reconcile_execution_flags(brain_path=brain_path, log_dir=log_dir, apply=True)

            updated = json.loads(brain_path.read_text(encoding="utf-8"))
            row = updated["markets"]["KR"]["recent_days"][0]
            self.assertTrue(row["execution_warning"])
            self.assertFalse(row["execution_learning_excluded"])
            self.assertTrue(row["prompt_policy_excluded"])
            self.assertEqual(row["policy_exclusion_reason"], "execution_contaminated")
            self.assertEqual(row["key_lesson"], "keep")


if __name__ == "__main__":
    unittest.main()
