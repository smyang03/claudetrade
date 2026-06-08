from __future__ import annotations

import tempfile
import subprocess
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from bot.session_date import KST
from preopen.scheduler import (
    PreopenJob,
    default_outcome_offsets_min,
    due_jobs,
    is_trading_day,
    outcome_offsets_min_by_interval,
    regular_open_dt,
)
from preopen.storage import load_preopen_dashboard, load_preopen_scheduler_state
from tools.preopen_scheduler import _run_job, _scheduler_heartbeat_path, run_scheduler_once


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

    def test_known_kr_holiday_override_skips_jobs_even_if_calendar_is_stale(self) -> None:
        now = datetime(2026, 7, 17, 8, 30, tzinfo=KST)

        self.assertFalse(is_trading_day("KR", "2026-07-17"))
        self.assertEqual(due_jobs(now_dt=now, markets=["KR"], mode="live"), [])

    def test_us_outcome_due_after_regular_open(self) -> None:
        now = datetime(2026, 5, 4, 23, 1, tzinfo=KST)

        with patch("preopen.scheduler.is_trading_day", return_value=True):
            jobs = due_jobs(now_dt=now, markets=["US"], mode="live")

        outcome_ids = [job.job_id for job in jobs if job.kind == "outcome"]
        self.assertIn("live:2026-05-04:US:outcome:5m", outcome_ids)
        self.assertIn("live:2026-05-04:US:outcome:30m", outcome_ids)

    def test_us_regular_open_dt_tracks_dst_and_non_dst(self) -> None:
        self.assertEqual(regular_open_dt("US", "2026-05-04").strftime("%Y-%m-%d %H:%M"), "2026-05-04 22:30")
        self.assertEqual(regular_open_dt("US", "2026-01-05").strftime("%Y-%m-%d %H:%M"), "2026-01-05 23:30")

    def test_default_outcome_offsets_extend_to_regular_close(self) -> None:
        us_offsets = default_outcome_offsets_min("US", "2026-05-04")
        kr_offsets = default_outcome_offsets_min("KR", "2026-05-04")

        self.assertEqual(us_offsets[:5], (5, 10, 15, 20, 25))
        self.assertEqual(kr_offsets[:5], (5, 10, 15, 20, 25))
        self.assertEqual(us_offsets[-1], 390)
        self.assertEqual(kr_offsets[-1], 390)
        self.assertEqual(len(us_offsets), 78)
        self.assertEqual(len(kr_offsets), 78)

    def test_outcome_offsets_can_still_be_requested_at_wider_interval(self) -> None:
        offsets = outcome_offsets_min_by_interval("US", "2026-05-04", interval_min=30)

        self.assertEqual(offsets[:5], (5, 30, 60, 90, 120))
        self.assertEqual(offsets[-1], 390)

    def test_us_outcome_jobs_continue_after_two_hours(self) -> None:
        now = datetime(2026, 5, 5, 1, 0, tzinfo=KST)

        with patch("preopen.scheduler.is_trading_day", return_value=True):
            jobs = due_jobs(now_dt=now, markets=["US"], mode="live")

        outcome_ids = [job.job_id for job in jobs if job.kind == "outcome"]
        self.assertIn("live:2026-05-04:US:outcome:120m", outcome_ids)
        self.assertIn("live:2026-05-04:US:outcome:150m", outcome_ids)

    def test_us_non_dst_collector_extends_until_2325_kst(self) -> None:
        now = datetime(2026, 1, 5, 23, 20, tzinfo=KST)

        with patch("preopen.scheduler.is_trading_day", return_value=True):
            jobs = due_jobs(now_dt=now, markets=["US"], mode="live")

        self.assertTrue(any(job.kind == "collector" for job in jobs))
        self.assertFalse(any(job.kind == "outcome" for job in jobs))

    def test_news_job_runs_from_preopen_targets_before_regular_open(self) -> None:
        now = datetime(2026, 5, 4, 8, 41, tzinfo=KST)

        with patch("preopen.scheduler.is_trading_day", return_value=True):
            jobs = due_jobs(
                now_dt=now,
                markets=["KR"],
                mode="live",
                completed_job_ids={"live:2026-05-04:KR:collector:002"},
            )

        news_jobs = [job for job in jobs if job.kind == "news"]
        self.assertEqual(len(news_jobs), 1)
        job = news_jobs[0]
        self.assertEqual(job.job_id, "live:2026-05-04:KR:news")
        self.assertEqual(job.due_at, "2026-05-04T08:40:00+09:00")
        self.assertEqual(job.script, "tools/collect_preopen_candidate_news.py")
        self.assertEqual(job.args, ("--market", "KR", "--session-date", "2026-05-04", "--mode", "live"))

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

    def test_real_run_reads_subprocess_output_as_utf8(self) -> None:
        now = datetime(2026, 5, 4, 17, 5, tzinfo=KST)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "preopen.scheduler.is_trading_day",
                return_value=True,
            ), patch("tools.preopen_scheduler.subprocess.run") as run_mock:
                run_mock.return_value = subprocess.CompletedProcess(
                    args=["python", "tools/preopen_collector.py"],
                    returncode=0,
                    stdout="한글 ok",
                    stderr="",
                )
                summary = run_scheduler_once(
                    mode="live",
                    markets=["US"],
                    dry_run=False,
                    now_dt=now,
                    interval_sec=60,
                )

        self.assertEqual(summary["ran"], 1)
        self.assertEqual(run_mock.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run_mock.call_args.kwargs["errors"], "replace")

    def test_news_job_gets_extended_timeout(self) -> None:
        job = PreopenJob(
            market="KR",
            session_date="2026-05-04",
            kind="news",
            job_id="live:2026-05-04:KR:news",
            due_at="2026-05-04T08:55:00+09:00",
            script="tools/collect_preopen_candidate_news.py",
            args=("--market", "KR", "--session-date", "2026-05-04", "--mode", "live"),
        )

        with patch("tools.preopen_scheduler.subprocess.run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(args=["python"], returncode=0, stdout="ok", stderr="")
            result = _run_job(job, timeout_sec=120, dry_run=False)

        self.assertEqual(result["status"], "success")
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 1200)

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
                heartbeat = _scheduler_heartbeat_path("live")
                heartbeat_exists = heartbeat.exists()

        self.assertIn("scheduler", payload)
        self.assertEqual(payload["scheduler"]["status"], "active")
        self.assertIn("preopen_scheduler.py", payload["scheduler"]["start_command"])
        self.assertTrue(heartbeat_exists)

    def test_paper_dashboard_and_jobs_do_not_show_live_commands(self) -> None:
        now = datetime(2026, 5, 4, 23, 1, tzinfo=KST)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "preopen.scheduler.is_trading_day",
                return_value=True,
            ):
                run_scheduler_once(mode="paper", markets=["US"], dry_run=True, now_dt=now, interval_sec=60)
                payload = load_preopen_dashboard("US", session_date="2026-05-04", mode="paper")
                jobs = due_jobs(now_dt=now, markets=["US"], mode="paper")

        self.assertIn("--mode paper", payload["scheduler"]["start_command"])
        self.assertNotIn("--mode live", payload["scheduler"]["start_command"])
        self.assertTrue(any("--mode paper" in command for command in payload["scheduler_guidance"]["commands"]))
        self.assertTrue(all("--mode live" not in command for command in payload["scheduler_guidance"]["commands"]))
        self.assertTrue(any("--mode paper" in action for action in payload["next_actions"] if "preopen_" in action))
        self.assertTrue(all("--mode paper" in job.display_command for job in jobs if job.kind == "outcome"))
        self.assertTrue(all("--mode paper" in job.display_command for job in jobs if job.kind == "news"))


if __name__ == "__main__":
    unittest.main()
