from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.session_date import KST
from preopen.scheduler import is_trading_day, market_key, regular_close_dt, regular_open_dt
from runtime_paths import get_runtime_path


PHASES = ("preopen", "intraday", "post-close", "retry")
DEFAULT_PRICE_ROOT = "data/price"
DEFAULT_REPORT_DIR = "data/v2_reports/counterfactual"


@dataclass(frozen=True)
class PipelineJob:
    market: str
    phase: str
    session_date: str
    job_id: str
    due_at: str
    window_end: str
    commands: tuple[tuple[str, ...], ...]
    env_updates: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["commands"] = [list(command) for command in self.commands]
        payload["env_updates"] = dict(self.env_updates or {})
        return payload


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _to_kst(value: datetime | None) -> datetime:
    dt = value or datetime.now(KST)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def _parse_markets(value: str | Iterable[str]) -> list[str]:
    if isinstance(value, str):
        raw = value.split(",")
    else:
        raw = list(value)
    markets = [market_key(item.strip()) for item in raw if str(item).strip()]
    return list(dict.fromkeys(markets)) or ["KR", "US"]


def _parse_phase(value: str) -> str:
    text = str(value or "due").strip().lower().replace("_", "-")
    aliases = {"postclose": "post-close", "post_close": "post-close", "all": "due"}
    text = aliases.get(text, text)
    if text != "due" and text not in PHASES:
        raise ValueError(f"unsupported phase: {value}")
    return text


def _state_path() -> Path:
    return get_runtime_path("state", "counterfactual_pipeline_state.json")


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    runs = state.get("runs")
    if isinstance(runs, dict) and len(runs) > 800:
        ordered = sorted(runs.items(), key=lambda item: str((item[1] or {}).get("finished_at", "")))
        state["runs"] = dict(ordered[-800:])
    events = state.get("recent_events")
    if isinstance(events, list) and len(events) > 200:
        state["recent_events"] = events[-200:]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _completed_job_ids(state: dict[str, Any]) -> set[str]:
    runs = state.get("runs")
    if not isinstance(runs, dict):
        return set()
    return {
        str(job_id)
        for job_id, run in runs.items()
        if isinstance(run, dict) and str(run.get("status") or "") == "success"
    }


def _append_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    payload = dict(event)
    payload.setdefault("ts", _now_iso())
    state.setdefault("recent_events", [])
    if isinstance(state["recent_events"], list):
        state["recent_events"].append(payload)


def _previous_trading_session(market: str, session_date: str) -> str:
    day = datetime.fromisoformat(session_date).date() - timedelta(days=1)
    for _ in range(10):
        candidate = day.isoformat()
        if is_trading_day(market, candidate):
            return candidate
        day -= timedelta(days=1)
    return day.isoformat()


def _market_session_date(market: str, now_dt: datetime) -> str:
    mkt = market_key(market)
    if mkt == "US":
        return now_dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    return now_dt.astimezone(KST).date().isoformat()


def _target_session_date(market: str, phase: str, now_dt: datetime) -> str:
    current_session = _market_session_date(market, now_dt)
    if phase == "preopen":
        return _previous_trading_session(market, current_session)
    return current_session


def _schedule_anchor_session(market: str, phase: str, now_dt: datetime) -> str:
    mkt = market_key(market)
    if phase == "preopen":
        return _market_session_date(mkt, now_dt)
    return _target_session_date(mkt, phase, now_dt)


def _phase_window(
    *,
    market: str,
    phase: str,
    anchor_session: str,
    now_dt: datetime,
    intraday_interval_min: int,
) -> tuple[datetime, datetime, int | None]:
    mkt = market_key(market)
    opened = regular_open_dt(mkt, anchor_session)
    closed = regular_close_dt(mkt, anchor_session)
    if phase == "preopen":
        lead = 20 if mkt == "KR" else 40
        due = opened - timedelta(minutes=lead)
        return due, due + timedelta(minutes=10), None
    if phase == "post-close":
        due = closed + timedelta(minutes=15 if mkt == "KR" else 20)
        return due, due + timedelta(minutes=15), None
    if phase == "retry":
        due = closed + timedelta(minutes=40 if mkt == "KR" else 90)
        return due, due + timedelta(minutes=20), None

    start = opened + timedelta(minutes=5)
    end = closed - timedelta(minutes=5)
    if now_dt < start:
        bucket = 0
    else:
        bucket = int((now_dt - start).total_seconds() // 60) // max(1, int(intraday_interval_min))
    due = start + timedelta(minutes=bucket * max(1, int(intraday_interval_min)))
    return due, due + timedelta(minutes=max(1, int(intraday_interval_min))), bucket


def _window_contains(now_dt: datetime, start: datetime, end: datetime) -> bool:
    return start <= now_dt <= end


def _command_base() -> tuple[str, ...]:
    return (sys.executable,)


def _env_file_value(env_path: str, key: str) -> str:
    path = Path(str(env_path or ""))
    if not path.exists():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            name, value = raw.split("=", 1)
            if name.strip() == key:
                return value.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _collect_provider_for_market(market: str, provider: str = "", env_path: str = "") -> str:
    mkt = market_key(market)
    override = str(provider or "").strip()
    if override:
        return override
    env_key = f"INTRADAY_EVIDENCE_PROVIDER_{mkt}"
    configured = str(os.getenv(env_key, "") or "").strip()
    if configured:
        return configured
    configured = _env_file_value(env_path, env_key)
    if configured:
        return configured
    return "yfinance" if mkt == "US" else ""


def _date_window_args(market: str, session_date: str, phase: str, now_dt: datetime) -> tuple[str, ...]:
    opened = regular_open_dt(market, session_date)
    closed = regular_close_dt(market, session_date)
    if phase == "intraday":
        end = min(now_dt, closed)
    else:
        end = closed
    return ("--start-at", opened.isoformat(timespec="seconds"), "--end-at", end.isoformat(timespec="seconds"))


def _collect_command(
    *,
    market: str,
    session_date: str,
    phase: str,
    now_dt: datetime,
    env_path: str,
    price_root: str,
    max_tickers: int,
    provider: str = "",
) -> tuple[str, ...]:
    command: list[str] = [
        *_command_base(),
        str(ROOT / "tools" / "collect_counterfactual_minutes.py"),
        "--market",
        market,
        "--from-counterfactual-db",
        "--date",
        session_date,
        "--price-root",
        price_root,
        "--json",
        *_date_window_args(market, session_date, phase, now_dt),
    ]
    if env_path:
        command.extend(["--env", env_path])
    provider_key = _collect_provider_for_market(market, provider=provider, env_path=env_path)
    if provider_key:
        command.extend(["--provider", provider_key])
    if max_tickers > 0:
        command.extend(["--max-tickers", str(max_tickers)])
    return tuple(command)


def _updater_command(*, market: str, session_date: str, price_root: str, retry_missing: bool) -> tuple[str, ...]:
    command = [
        *_command_base(),
        str(ROOT / "tools" / "update_counterfactual_outcomes.py"),
        "--date",
        session_date,
        "--market",
        market,
        "--price-root",
        price_root,
        "--minute-root",
        price_root,
    ]
    if retry_missing:
        command.append("--retry-missing")
    return tuple(command)


def _analyzer_command(*, market: str, session_date: str, phase: str, report_dir: str) -> tuple[str, ...]:
    stamp = f"{session_date.replace('-', '')}_{market.lower()}_{phase.replace('-', '_')}"
    return (
        *_command_base(),
        str(ROOT / "tools" / "analyze_counterfactual_paths.py"),
        "--date",
        session_date,
        "--market",
        market,
        "--stamp",
        stamp,
        "--output-dir",
        report_dir,
    )


def build_due_jobs(
    *,
    markets: Iterable[str],
    phase: str = "due",
    now_dt: datetime | None = None,
    force: bool = False,
    completed_job_ids: set[str] | None = None,
    env_path: str = "",
    price_root: str = DEFAULT_PRICE_ROOT,
    report_dir: str = DEFAULT_REPORT_DIR,
    intraday_interval_min: int = 10,
    max_tickers: int = 0,
    provider: str = "",
) -> list[PipelineJob]:
    now = _to_kst(now_dt)
    wanted_phase = _parse_phase(phase)
    completed = completed_job_ids or set()
    phases = PHASES if wanted_phase == "due" else (wanted_phase,)
    jobs: list[PipelineJob] = []
    for raw_market in _parse_markets(markets):
        market = market_key(raw_market)
        for item_phase in phases:
            anchor_session = _schedule_anchor_session(market, item_phase, now)
            if not is_trading_day(market, anchor_session):
                continue
            session_date = _target_session_date(market, item_phase, now)
            if not is_trading_day(market, session_date):
                continue
            due_at, window_end, bucket = _phase_window(
                market=market,
                phase=item_phase,
                anchor_session=anchor_session,
                now_dt=now,
                intraday_interval_min=15 if market == "US" else intraday_interval_min,
            )
            if not force and not _window_contains(now, due_at, window_end):
                continue
            bucket_suffix = f":{bucket:03d}" if item_phase == "intraday" and bucket is not None else ""
            job_id = f"{session_date}:{market}:{item_phase}{bucket_suffix}"
            if not force and job_id in completed:
                continue
            env_updates: dict[str, str] = {}
            if item_phase == "intraday" and market == "KR":
                env_updates["KR_INTRADAY_KIS_MAX_PAGES"] = os.getenv("KR_INTRADAY_KIS_MAX_PAGES", "2")
            commands: list[tuple[str, ...]] = [
                _collect_command(
                    market=market,
                    session_date=session_date,
                    phase=item_phase,
                    now_dt=now,
                    env_path=env_path,
                    price_root=price_root,
                    max_tickers=max_tickers,
                    provider=provider,
                )
            ]
            if item_phase != "intraday":
                commands.append(
                    _updater_command(
                        market=market,
                        session_date=session_date,
                        price_root=price_root,
                        retry_missing=item_phase in {"preopen", "retry"},
                    )
                )
                commands.append(_analyzer_command(market=market, session_date=session_date, phase=item_phase, report_dir=report_dir))
            jobs.append(
                PipelineJob(
                    market=market,
                    phase=item_phase,
                    session_date=session_date,
                    job_id=job_id,
                    due_at=due_at.isoformat(timespec="seconds"),
                    window_end=window_end.isoformat(timespec="seconds"),
                    commands=tuple(commands),
                    env_updates=env_updates,
                )
            )
    return jobs


def _tail(text: Any, limit: int = 1200) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[-limit:]


def _run_command(command: tuple[str, ...], *, timeout_sec: int, dry_run: bool, env_updates: dict[str, str] | None) -> dict[str, Any]:
    if dry_run:
        return {"status": "dry_run", "returncode": 0, "stdout": " ".join(command), "stderr": ""}
    env = os.environ.copy()
    env.update(env_updates or {})
    kwargs: dict[str, Any] = {
        "cwd": str(ROOT),
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": max(30, int(timeout_sec)),
        "env": env,
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(list(command), **kwargs)
        return {
            "status": "success" if proc.returncode == 0 else "failed",
            "returncode": int(proc.returncode),
            "stdout": _tail(proc.stdout),
            "stderr": _tail(proc.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "returncode": None,
            "stdout": _tail(exc.stdout),
            "stderr": _tail(exc.stderr or f"timeout after {timeout_sec}s"),
        }
    except Exception as exc:
        return {"status": "failed", "returncode": None, "stdout": "", "stderr": str(exc)}


def run_pipeline_once(
    *,
    markets: Iterable[str],
    phase: str = "due",
    now_dt: datetime | None = None,
    force: bool = False,
    dry_run: bool = False,
    env_path: str = "",
    price_root: str = DEFAULT_PRICE_ROOT,
    report_dir: str = DEFAULT_REPORT_DIR,
    intraday_interval_min: int = 10,
    max_tickers: int = 0,
    timeout_sec: int = 900,
    provider: str = "",
) -> dict[str, Any]:
    state = _load_state()
    state.setdefault("started_at", _now_iso())
    state["last_tick_at"] = _now_iso()
    state.setdefault("runs", {})
    jobs = build_due_jobs(
        markets=markets,
        phase=phase,
        now_dt=now_dt,
        force=force,
        completed_job_ids=_completed_job_ids(state),
        env_path=env_path,
        price_root=price_root,
        report_dir=report_dir,
        intraday_interval_min=intraday_interval_min,
        max_tickers=max_tickers,
        provider=provider,
    )
    summary: dict[str, Any] = {
        "ok": True,
        "phase": _parse_phase(phase),
        "markets": _parse_markets(markets),
        "due": len(jobs),
        "ran": 0,
        "dry_run": bool(dry_run),
        "force": bool(force),
        "jobs": [],
        "state_path": str(_state_path()),
    }
    if not jobs:
        _append_event(state, {"event": "no_due_jobs", "phase": _parse_phase(phase), "markets": summary["markets"]})
        _save_state(state)
        return summary

    for job in jobs:
        started_at = _now_iso()
        command_results: list[dict[str, Any]] = []
        job_ok = True
        _append_event(state, {"event": "job_start", "job_id": job.job_id, "market": job.market, "phase": job.phase})
        for command in job.commands:
            result = _run_command(command, timeout_sec=timeout_sec, dry_run=dry_run, env_updates=job.env_updates)
            command_results.append(
                {
                    "command": list(command),
                    "status": result["status"],
                    "returncode": result["returncode"],
                    "stdout_tail": result["stdout"],
                    "stderr_tail": result["stderr"],
                }
            )
            if result["status"] not in {"success", "dry_run"}:
                job_ok = False
        status = "dry_run" if dry_run else ("success" if job_ok else "failed")
        finished_at = _now_iso()
        run_record = {
            "job": job.to_dict(),
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "commands": command_results,
        }
        state["runs"][job.job_id] = run_record
        _append_event(state, {"event": "job_finish", "job_id": job.job_id, "status": status})
        summary["jobs"].append(run_record)
        summary["ran"] += 1
        if status == "failed":
            summary["ok"] = False
    _save_state(state)
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run counterfactual minute collection only inside approved schedule windows.")
    parser.add_argument("--phase", default="due", help="due, preopen, intraday, post-close, retry")
    parser.add_argument("--market", "--markets", dest="markets", default="KR,US")
    parser.add_argument("--env", default=str(ROOT / ".env.live"))
    parser.add_argument("--price-root", default=DEFAULT_PRICE_ROOT)
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)
    parser.add_argument("--intraday-interval-min", type=int, default=10)
    parser.add_argument("--max-tickers", type=int, default=0)
    parser.add_argument("--provider", default="", help="override minute provider for collection jobs")
    parser.add_argument("--timeout-sec", type=int, default=900)
    parser.add_argument("--loop", action="store_true", help="keep checking schedule windows until interrupted")
    parser.add_argument("--interval-sec", type=int, default=300, help="loop sleep interval")
    parser.add_argument("--max-loops", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true", help="run selected phase even outside its schedule window")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _print_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str), flush=True)
        return
    print(
        f"ok={payload['ok']} due={payload['due']} ran={payload['ran']} "
        f"phase={payload['phase']} markets={','.join(payload['markets'])}",
        flush=True,
    )
    for job in payload.get("jobs", []):
        info = job.get("job") or {}
        print(
            f"{info.get('market')} {info.get('session_date')} {info.get('phase')} "
            f"status={job.get('status')} id={info.get('job_id')}",
            flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    loops = 0
    last_ok = True
    while True:
        payload = run_pipeline_once(
            markets=_parse_markets(args.markets),
            phase=args.phase,
            force=bool(args.force),
            dry_run=bool(args.dry_run),
            env_path=args.env,
            price_root=args.price_root,
            report_dir=args.report_dir,
            intraday_interval_min=int(args.intraday_interval_min),
            max_tickers=int(args.max_tickers),
            timeout_sec=int(args.timeout_sec),
            provider=args.provider,
        )
        _print_payload(payload, as_json=bool(args.json))
        last_ok = bool(payload.get("ok"))
        loops += 1
        if not args.loop:
            break
        if int(args.max_loops or 0) > 0 and loops >= int(args.max_loops):
            break
        time.sleep(max(10, int(args.interval_sec or 300)))
    return 0 if last_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
