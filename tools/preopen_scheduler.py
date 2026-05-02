from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.session_date import KST
from preopen.scheduler import PreopenJob, due_jobs, market_key
from preopen.storage import (
    load_preopen_scheduler_state,
    save_preopen_scheduler_event,
    save_preopen_scheduler_state,
)


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _tail(text: str, limit: int = 900) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _parse_markets(value: str) -> list[str]:
    markets = []
    for item in str(value or "KR,US").split(","):
        item = item.strip().upper()
        if not item:
            continue
        markets.append(market_key(item))
    return list(dict.fromkeys(markets)) or ["KR", "US"]


def _completed_job_ids(state: dict[str, Any]) -> set[str]:
    runs = state.get("runs") if isinstance(state, dict) else {}
    if not isinstance(runs, dict):
        return set()
    return {
        str(job_id)
        for job_id, run in runs.items()
        if isinstance(run, dict) and str(run.get("status", "")) == "success"
    }


def _trim_state(state: dict[str, Any], *, max_runs: int = 500, max_events: int = 100) -> dict[str, Any]:
    runs = state.get("runs")
    if isinstance(runs, dict) and len(runs) > max_runs:
        ordered = sorted(runs.items(), key=lambda item: str((item[1] or {}).get("finished_at", "")))
        state["runs"] = dict(ordered[-max_runs:])
    events = state.get("recent_events")
    if isinstance(events, list) and len(events) > max_events:
        state["recent_events"] = events[-max_events:]
    return state


def _record_event(mode: str, state: dict[str, Any], event: dict[str, Any]) -> None:
    payload = dict(event or {})
    payload.setdefault("ts", _now_iso())
    state.setdefault("recent_events", [])
    if isinstance(state["recent_events"], list):
        state["recent_events"].append(payload)
    save_preopen_scheduler_event(mode, payload)


def _command_for_job(job: PreopenJob) -> list[str]:
    return [sys.executable, str(ROOT / job.script), *job.args]


def _run_job(job: PreopenJob, *, timeout_sec: int, dry_run: bool) -> dict[str, Any]:
    command = _command_for_job(job)
    if dry_run:
        return {
            "status": "dry_run",
            "returncode": 0,
            "stdout": job.display_command,
            "stderr": "",
            "command": command,
        }
    kwargs: dict[str, Any] = {
        "cwd": str(ROOT),
        "capture_output": True,
        "text": True,
        "timeout": max(10, int(timeout_sec)),
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(command, **kwargs)
        return {
            "status": "success" if proc.returncode == 0 else "failed",
            "returncode": int(proc.returncode),
            "stdout": _tail(proc.stdout),
            "stderr": _tail(proc.stderr),
            "command": command,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "returncode": None,
            "stdout": _tail(exc.stdout or ""),
            "stderr": _tail(exc.stderr or f"timeout after {timeout_sec}s"),
            "command": command,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "command": command,
        }


def run_scheduler_once(
    *,
    mode: str,
    markets: list[str],
    dry_run: bool = False,
    force: bool = False,
    timeout_sec: int = 120,
    interval_sec: int = 60,
    collector_interval_min: int | None = None,
    outcome_catchup_min: int = 180,
    now_dt: datetime | None = None,
) -> dict[str, Any]:
    runtime_mode = "live" if str(mode or "").lower() == "live" else "paper"
    state = load_preopen_scheduler_state(runtime_mode) or {}
    state.setdefault("mode", runtime_mode)
    state.setdefault("started_at", _now_iso())
    state["last_tick_at"] = _now_iso()
    state["interval_sec"] = int(interval_sec)
    state["markets"] = markets
    state["dry_run"] = bool(dry_run)
    state.setdefault("runs", {})

    jobs = due_jobs(
        now_dt=now_dt,
        markets=markets,
        mode=runtime_mode,
        collector_interval_override_min=collector_interval_min,
        outcome_catchup_min=outcome_catchup_min,
        force=force,
        completed_job_ids=_completed_job_ids(state),
    )
    summary = {
        "mode": runtime_mode,
        "markets": markets,
        "due": len(jobs),
        "ran": 0,
        "dry_run": bool(dry_run),
        "jobs": [job.to_dict() for job in jobs],
    }
    if not jobs:
        _record_event(runtime_mode, state, {
            "event": "no_due_jobs",
            "markets": markets,
            "dry_run": bool(dry_run),
        })
        save_preopen_scheduler_state(runtime_mode, _trim_state(state))
        return summary

    for job in jobs:
        started_at = _now_iso()
        _record_event(runtime_mode, state, {
            "event": "job_start",
            "job_id": job.job_id,
            "market": job.market,
            "kind": job.kind,
            "session_date": job.session_date,
            "command": job.display_command,
            "dry_run": bool(dry_run),
        })
        result = _run_job(job, timeout_sec=timeout_sec, dry_run=dry_run)
        finished_at = _now_iso()
        run_record = {
            "job": job.to_dict(),
            "status": result["status"],
            "returncode": result["returncode"],
            "started_at": started_at,
            "finished_at": finished_at,
            "stdout_tail": result["stdout"],
            "stderr_tail": result["stderr"],
            "command": result["command"],
        }
        state["runs"][job.job_id] = run_record
        event_name = {
            "success": "job_success",
            "dry_run": "job_dry_run",
            "timeout": "job_timeout",
        }.get(str(result["status"]), "job_failed")
        _record_event(runtime_mode, state, {
            "event": event_name,
            "job_id": job.job_id,
            "market": job.market,
            "kind": job.kind,
            "session_date": job.session_date,
            "returncode": result["returncode"],
            "stdout_tail": result["stdout"],
            "stderr_tail": result["stderr"],
            "command": job.display_command,
        })
        summary["ran"] += 1

    save_preopen_scheduler_state(runtime_mode, _trim_state(state))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Automatic sidecar scheduler for shadow-only preopen collection")
    parser.add_argument("--mode", choices=["paper", "live"], default="live")
    parser.add_argument("--markets", default="KR,US")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--interval-sec", type=int, default=60)
    parser.add_argument("--collector-interval-min", type=int, default=0)
    parser.add_argument("--outcome-catchup-min", type=int, default=180)
    parser.add_argument("--timeout-sec", type=int, default=120)
    args = parser.parse_args()

    if not args.once and not args.loop:
        args.once = True
    markets = _parse_markets(args.markets)
    collector_interval = args.collector_interval_min if args.collector_interval_min > 0 else None

    while True:
        summary = run_scheduler_once(
            mode=args.mode,
            markets=markets,
            dry_run=args.dry_run,
            force=args.force,
            timeout_sec=args.timeout_sec,
            interval_sec=args.interval_sec,
            collector_interval_min=collector_interval,
            outcome_catchup_min=args.outcome_catchup_min,
        )
        print(
            f"[preopen scheduler] mode={summary['mode']} markets={','.join(summary['markets'])} "
            f"due={summary['due']} ran={summary['ran']} dry_run={summary['dry_run']}"
        )
        if not args.loop:
            return 0
        time.sleep(max(10, int(args.interval_sec)))


if __name__ == "__main__":
    raise SystemExit(main())
