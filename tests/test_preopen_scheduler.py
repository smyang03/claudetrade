from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from bot.session_date import KST
from preopen.scheduler import due_jobs
from preopen.storage import load_preopen_dashboard, load_preopen_scheduler_state
from tools.preopen_scheduler import run_scheduler_once


def _runtime_path(root: Path):
    def _inner(*parts, make_parents=True):
        path = root.joinpath(*parts)
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return _inner


class PreopenSchedulerTests(unittest.TestCase):
    def test_us_collector_due_uses_bucketed_job_id(self) -> None:
        now = datetime(2026, 5, 4, 17, 5, tzinfo=KST)

        with patch("preopen.scheduler.is_trading_day", return_value=True):
            jobs = due_jobs(now_dt=now, markets=["US"], mode="live")

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.market, "US")
        self.assertEqual(job.kind, "collector")
        self.assertEqual(job.session_date, "2026-05-04")
        self.assertIn("collector:000", job.job_id)
        self.assertEqual(job.script, "tools/preopen_collector.py")

    def test_non_trading_day_skips_jobs(self) -> None:
        now = datetime(2026, 5, 5, 8, 30, tzinfo=KST)

        with patch("preopen.scheduler.is_trading_day", return_value=False):
            jobs = due_jobs(now_dt=now, markets=["KR"], mode="live")

        self.assertEqual(jobs, [])

    def test_us_outcome_due_after_regular_open(self) -> None:
        now = datetime(2026, 5, 4, 23, 1, tzinfo=KST)

        with patch("preopen.scheduler.is_trading_day", return_value=True):
            jobs = due_jobs(now_dt=now, markets=["US"], mode="live")

        outcome_ids = [job.job_id for job in jobs if job.kind == "outcome"]
        self.assertIn("live:2026-05-04:US:outcome:5m", outcome_ids)
        self.assertIn("live:2026-05-04:US:outcome:30m", outcome_ids)

    def test_us_outcome_catchup_after_kst_midnight_keeps_us_session_date(self) -> None:
        now = datetime(2026, 5, 5, 1, 0, tzinfo=KST)

        with patch("preopen.scheduler.is_trading_day", return_value=True):
            jobs = due_jobs(now_dt=now, markets=["US"], mode="live")

        outcome_ids = [job.job_id for job in jobs if job.kind == "outcome"]
        self.assertIn("live:2026-05-04:US:outcome:60m", outcome_ids)

    def test_dry_run_records_state_but_does_not_block_real_run(self) -> None:
        now = datetime(2026, 5, 4, 17, 5, tzinfo=KST)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "preopen.scheduler.is_trading_day",
                return_value=True,
            ):
                dry = run_scheduler_once(
                    mode="live",
                    markets=["US"],
                    dry_run=True,
                    now_dt=now,
                    interval_sec=60,
                )
                self.assertEqual(dry["due"], 1)
                self.assertEqual(dry["ran"], 1)
                state = load_preopen_scheduler_state("live")
                self.assertEqual(next(iter(state["runs"].values()))["status"], "dry_run")

                with patch("tools.preopen_scheduler._run_job", return_value={
                    "status": "success",
                    "returncode": 0,
                    "stdout": "ok",
                    "stderr": "",
                    "command": ["python", "tools/preopen_collector.py"],
                }):
                    real = run_scheduler_once(
                        mode="live",
                        markets=["US"],
                        dry_run=False,
                        now_dt=now,
                        interval_sec=60,
                    )
                    again = run_scheduler_once(
                        mode="live",
                        markets=["US"],
                        dry_run=False,
                        now_dt=now,
                        interval_sec=60,
                    )

        self.assertEqual(real["due"], 1)
        self.assertEqual(real["ran"], 1)
        self.assertEqual(again["due"], 0)
        self.assertEqual(again["ran"], 0)

    def test_dry_run_does_not_execute_subprocess(self) -> None:
        now = datetime(2026, 5, 4, 17, 5, tzinfo=KST)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "preopen.scheduler.is_trading_day",
                return_value=True,
            ), patch("tools.preopen_scheduler.subprocess.run") as run_mock:
                summary = run_scheduler_once(
                    mode="live",
                    markets=["US"],
                    dry_run=True,
                    now_dt=now,
                    interval_sec=60,
                )

        self.assertEqual(summary["ran"], 1)
        run_mock.assert_not_called()

    def test_dashboard_payload_includes_scheduler_status(self) -> None:
        now = datetime(2026, 5, 4, 17, 5, tzinfo=KST)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "preopen.scheduler.is_trading_day",
                return_value=True,
            ):
                run_scheduler_once(mode="live", markets=["US"], dry_run=True, now_dt=now, interval_sec=60)
                payload = load_preopen_dashboard("US", session_date="2026-05-04", mode="live")

        self.assertIn("scheduler", payload)
        self.assertEqual(payload["scheduler"]["status"], "active")
        self.assertIn("preopen_scheduler.py", payload["scheduler"]["start_command"])


if __name__ == "__main__":
    unittest.main()
