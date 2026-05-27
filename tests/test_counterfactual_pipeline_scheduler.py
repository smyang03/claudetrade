from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from bot.session_date import KST
from tools.run_counterfactual_pipeline import build_due_jobs, run_pipeline_once


def _runtime_path(root: Path):
    def _inner(*parts, make_parents=True):
        path = root.joinpath(*parts)
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return _inner


def test_post_close_phase_only_runs_inside_schedule_window() -> None:
    with patch("tools.run_counterfactual_pipeline.is_trading_day", return_value=True):
        outside = build_due_jobs(
            markets=["KR"],
            phase="post-close",
            now_dt=datetime(2026, 5, 27, 15, 20, tzinfo=KST),
        )
        inside = build_due_jobs(
            markets=["KR"],
            phase="post-close",
            now_dt=datetime(2026, 5, 27, 15, 45, tzinfo=KST),
        )

    assert outside == []
    assert len(inside) == 1
    job = inside[0]
    assert job.market == "KR"
    assert job.phase == "post-close"
    assert job.session_date == "2026-05-27"
    assert job.job_id == "2026-05-27:KR:post-close"
    assert any("collect_counterfactual_minutes.py" in item for item in job.commands[0])
    assert any("update_counterfactual_outcomes.py" in item for item in job.commands[1])
    assert any("analyze_counterfactual_paths.py" in item for item in job.commands[2])


def test_us_post_close_uses_new_york_session_date_after_kst_midnight() -> None:
    with patch.dict("os.environ", {"INTRADAY_EVIDENCE_PROVIDER_US": ""}), patch(
        "tools.run_counterfactual_pipeline.is_trading_day",
        return_value=True,
    ):
        jobs = build_due_jobs(
            markets=["US"],
            phase="post-close",
            now_dt=datetime(2026, 5, 27, 5, 20, tzinfo=KST),
        )

    assert len(jobs) == 1
    job = jobs[0]
    assert job.market == "US"
    assert job.session_date == "2026-05-26"
    assert job.job_id == "2026-05-26:US:post-close"
    assert "--provider" in job.commands[0]
    assert "yfinance" in job.commands[0]


def test_us_collect_provider_uses_env_override() -> None:
    with patch.dict("os.environ", {"INTRADAY_EVIDENCE_PROVIDER_US": "kis"}), patch(
        "tools.run_counterfactual_pipeline.is_trading_day",
        return_value=True,
    ):
        jobs = build_due_jobs(
            markets=["US"],
            phase="post-close",
            now_dt=datetime(2026, 5, 27, 5, 20, tzinfo=KST),
        )

    command = jobs[0].commands[0]
    assert "--provider" in command
    assert "kis" in command
    assert "yfinance" not in command


def test_us_collect_provider_reads_env_file_when_process_env_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env_path = Path(tmp) / ".env.live"
        env_path.write_text("INTRADAY_EVIDENCE_PROVIDER_US=kis\n", encoding="utf-8")
        with patch.dict("os.environ", {"INTRADAY_EVIDENCE_PROVIDER_US": ""}), patch(
            "tools.run_counterfactual_pipeline.is_trading_day",
            return_value=True,
        ):
            jobs = build_due_jobs(
                markets=["US"],
                phase="post-close",
                now_dt=datetime(2026, 5, 27, 5, 20, tzinfo=KST),
                env_path=str(env_path),
            )

    command = jobs[0].commands[0]
    assert "--provider" in command
    assert "kis" in command


def test_cli_provider_override_wins_over_env() -> None:
    with patch.dict("os.environ", {"INTRADAY_EVIDENCE_PROVIDER_US": "kis"}), patch(
        "tools.run_counterfactual_pipeline.is_trading_day",
        return_value=True,
    ):
        jobs = build_due_jobs(
            markets=["US"],
            phase="post-close",
            now_dt=datetime(2026, 5, 27, 5, 20, tzinfo=KST),
            provider="finnhub",
        )

    command = jobs[0].commands[0]
    assert "--provider" in command
    assert "finnhub" in command
    assert "kis" not in command


def test_preopen_targets_previous_trading_session() -> None:
    def is_open(_market: str, session_date: str) -> bool:
        return session_date != "2026-05-25"

    with patch("tools.run_counterfactual_pipeline.is_trading_day", side_effect=is_open):
        jobs = build_due_jobs(
            markets=["KR"],
            phase="preopen",
            now_dt=datetime(2026, 5, 27, 8, 40, tzinfo=KST),
        )

    assert len(jobs) == 1
    assert jobs[0].session_date == "2026-05-26"
    assert "--retry-missing" in jobs[0].commands[1]


def test_intraday_jobs_are_bucketed_and_do_not_run_twice_after_success() -> None:
    now = datetime(2026, 5, 27, 9, 15, tzinfo=KST)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch("tools.run_counterfactual_pipeline.get_runtime_path", side_effect=_runtime_path(root)), patch(
            "tools.run_counterfactual_pipeline.is_trading_day",
            return_value=True,
        ), patch(
            "tools.run_counterfactual_pipeline._run_command",
            return_value={"status": "success", "returncode": 0, "stdout": "ok", "stderr": ""},
        ) as runner:
            first = run_pipeline_once(markets=["KR"], phase="intraday", now_dt=now)
            second = run_pipeline_once(markets=["KR"], phase="intraday", now_dt=now)

    assert first["due"] == 1
    assert first["ran"] == 1
    assert second["due"] == 0
    assert second["ran"] == 0
    assert runner.call_count == 1
    assert first["jobs"][0]["job"]["job_id"] == "2026-05-27:KR:intraday:001"


def test_dry_run_does_not_block_later_real_run() -> None:
    now = datetime(2026, 5, 27, 15, 45, tzinfo=KST)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch("tools.run_counterfactual_pipeline.get_runtime_path", side_effect=_runtime_path(root)), patch(
            "tools.run_counterfactual_pipeline.is_trading_day",
            return_value=True,
        ):
            dry = run_pipeline_once(markets=["KR"], phase="post-close", now_dt=now, dry_run=True)

        with patch("tools.run_counterfactual_pipeline.get_runtime_path", side_effect=_runtime_path(root)), patch(
            "tools.run_counterfactual_pipeline.is_trading_day",
            return_value=True,
        ), patch(
            "tools.run_counterfactual_pipeline._run_command",
            return_value={"status": "success", "returncode": 0, "stdout": "ok", "stderr": ""},
        ) as runner:
            real = run_pipeline_once(markets=["KR"], phase="post-close", now_dt=now)

    assert dry["ran"] == 1
    assert real["ran"] == 1
    assert runner.call_count == 3
