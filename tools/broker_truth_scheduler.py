from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import psutil
except Exception:  # pragma: no cover - optional dependency
    psutil = None


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.session_date import KST, resolve_session_date_str
from preopen.scheduler import is_trading_day, market_key, regular_close_dt, regular_open_dt
from runtime_paths import get_runtime_path
from tools.live_maintenance import broker_truth_report


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _runtime_mode(value: str) -> str:
    return "paper" if str(value or "").lower() == "paper" else "live"


def _parse_markets(value: str) -> list[str]:
    markets: list[str] = []
    for item in str(value or "KR,US").split(","):
        item = item.strip().upper()
        if not item:
            continue
        markets.append(market_key(item))
    return list(dict.fromkeys(markets)) or ["KR", "US"]


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(KST) if dt.tzinfo else dt.replace(tzinfo=KST)
    except Exception:
        return None


def _load_env(mode: str, env_path: str | Path | None = None) -> str:
    try:
        from dotenv import load_dotenv
    except Exception:
        return ""

    runtime_mode = _runtime_mode(mode)
    path = Path(env_path) if env_path else ROOT / f".env.{runtime_mode}"
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        path = ROOT / ".env"
    if not path.exists():
        return ""
    load_dotenv(dotenv_path=path, override=True)
    _apply_start_config_env()
    return str(path)


def _apply_start_config_env() -> None:
    path = Path(os.getenv("V2_START_CONFIG_PATH", "config/v2_start_config.json"))
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    overrides = data.get("env_overrides") if isinstance(data, dict) else {}
    if not isinstance(overrides, dict):
        return
    for key, value in overrides.items():
        if key:
            os.environ[str(key)] = str(value)


def _state_path(mode: str) -> Path:
    runtime_mode = _runtime_mode(mode)
    name = "broker_truth_scheduler_state.json" if runtime_mode == "live" else f"{runtime_mode}_broker_truth_scheduler_state.json"
    return get_runtime_path("state", name)


def _heartbeat_path(mode: str) -> Path:
    runtime_mode = _runtime_mode(mode)
    name = "broker_truth_scheduler_heartbeat.json" if runtime_mode == "live" else f"{runtime_mode}_broker_truth_scheduler_heartbeat.json"
    return get_runtime_path("state", name)


def _lock_path(mode: str) -> Path:
    runtime_mode = _runtime_mode(mode)
    name = "broker_truth_scheduler.lock.json" if runtime_mode == "live" else f"{runtime_mode}_broker_truth_scheduler.lock.json"
    return get_runtime_path("state", name)


def _event_path(mode: str) -> Path:
    runtime_mode = _runtime_mode(mode)
    day = datetime.now(KST).strftime("%Y%m%d")
    return get_runtime_path("logs", "broker_truth_scheduler", f"{day}_{runtime_mode}.jsonl")


def _load_json(path: Path, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return default


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _append_event(mode: str, event: dict[str, Any]) -> None:
    path = _event_path(mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(event or {})
    payload.setdefault("ts", _now_iso())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if psutil is not None:
        try:
            return bool(psutil.pid_exists(pid))
        except Exception:
            pass
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _read_lock(path: Path) -> dict[str, Any]:
    data = _load_json(path, {})
    return data if isinstance(data, dict) else {}


def _acquire_lock(mode: str) -> tuple[bool, Path, str]:
    runtime_mode = _runtime_mode(mode)
    path = _lock_path(runtime_mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "process": "broker_truth_scheduler",
        "pid": os.getpid(),
        "mode": runtime_mode,
        "started_at": _now_iso(),
        "lock_path": str(path),
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    while True:
        try:
            fd = os.open(str(path), flags)
        except FileExistsError:
            existing = _read_lock(path)
            existing_pid = int(existing.get("pid") or 0)
            if existing_pid and existing_pid != os.getpid() and _pid_alive(existing_pid):
                return False, path, f"another broker truth scheduler is already running pid={existing_pid}"
            try:
                path.unlink()
            except Exception:
                return False, path, f"failed to remove stale broker truth scheduler lock pid={existing_pid}"
            continue
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
        return True, path, "acquired"


def _release_lock(path: Path) -> None:
    try:
        data = _read_lock(path)
        if int(data.get("pid") or 0) == os.getpid() and path.exists():
            path.unlink()
    except Exception:
        pass


def _write_heartbeat(
    mode: str,
    *,
    interval_sec: int,
    status: str,
    last_success_at: str = "",
    last_error: str = "",
    last_result: dict[str, Any] | None = None,
) -> None:
    now_dt = datetime.now(KST)
    payload = {
        "process": "broker_truth_scheduler",
        "pid": os.getpid(),
        "mode": _runtime_mode(mode),
        "last_started_at": now_dt.isoformat(timespec="seconds"),
        "last_tick_at": now_dt.isoformat(timespec="seconds"),
        "last_success_at": last_success_at,
        "last_error_at": now_dt.isoformat(timespec="seconds") if last_error else "",
        "last_error": last_error,
        "next_expected_at": (now_dt + timedelta(seconds=max(10, int(interval_sec or 30)))).isoformat(timespec="seconds"),
        "healthy": not bool(last_error) and status != "error",
        "status": status,
        "last_result": last_result or {},
    }
    _save_json(_heartbeat_path(mode), payload)


def _candidate_session_dates(market: str, now_dt: datetime) -> list[str]:
    dates = {
        resolve_session_date_str(market, now_dt),
        now_dt.date().isoformat(),
        (now_dt.date() - timedelta(days=1)).isoformat(),
    }
    return sorted(dates)


def market_refresh_window(
    market: str,
    *,
    now_dt: datetime | None = None,
    preopen_min: int = 20,
    postclose_min: int = 15,
) -> dict[str, Any]:
    mkt = market_key(market)
    current = now_dt or datetime.now(KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    else:
        current = current.astimezone(KST)
    for session_date in _candidate_session_dates(mkt, current):
        try:
            if not is_trading_day(mkt, session_date):
                continue
            opened = regular_open_dt(mkt, session_date)
            closed = regular_close_dt(mkt, session_date)
        except Exception as exc:
            return {
                "market": mkt,
                "active": False,
                "reason": f"calendar_error:{exc}",
                "session_date": session_date,
            }
        start = opened - timedelta(minutes=max(0, int(preopen_min)))
        end = closed + timedelta(minutes=max(0, int(postclose_min)))
        if start <= current <= end:
            return {
                "market": mkt,
                "active": True,
                "reason": "inside_refresh_window",
                "session_date": session_date,
                "window_start": start.isoformat(timespec="seconds"),
                "regular_open": opened.isoformat(timespec="seconds"),
                "regular_close": closed.isoformat(timespec="seconds"),
                "window_end": end.isoformat(timespec="seconds"),
            }
    return {
        "market": mkt,
        "active": False,
        "reason": "outside_refresh_window",
        "session_dates_checked": _candidate_session_dates(mkt, current),
    }


def _minutes_since(value: Any, now_dt: datetime) -> float | None:
    dt = _parse_dt(value)
    if dt is None:
        return None
    return max(0.0, (now_dt - dt.astimezone(KST)).total_seconds() / 60.0)


def _market_due(
    state: dict[str, Any],
    market: str,
    *,
    now_dt: datetime,
    force: bool,
    refresh_interval_min: int,
    failure_retry_min: int,
    window: dict[str, Any],
) -> tuple[bool, str]:
    mkt = market_key(market)
    if force:
        return True, "force"
    if not bool(window.get("active")):
        return False, str(window.get("reason") or "outside_refresh_window")
    markets = state.get("markets") if isinstance(state.get("markets"), dict) else {}
    item = markets.get(mkt) if isinstance(markets.get(mkt), dict) else {}
    last_status = str(item.get("last_status") or "")
    last_attempt_min = _minutes_since(item.get("last_attempt_at"), now_dt)
    last_success_min = _minutes_since(item.get("last_success_at"), now_dt)
    if last_status and last_status != "ok":
        if last_attempt_min is None or last_attempt_min >= max(1, int(failure_retry_min)):
            return True, "retry_after_failure"
        return False, "recent_failure_retry_wait"
    if last_success_min is None:
        return True, "no_previous_success"
    if last_success_min >= max(1, int(refresh_interval_min)):
        return True, "refresh_interval_elapsed"
    return False, "recent_success"


def _refresh_market(mode: str, market: str, *, ttl_sec: int, dry_run: bool) -> dict[str, Any]:
    mkt = market_key(market)
    if dry_run:
        return {
            "ok": True,
            "mode": _runtime_mode(mode),
            "market": mkt,
            "dry_run": True,
            "positions": [],
            "open_orders": [],
            "today_fills": [],
            "stale": False,
            "missing": False,
            "error": "",
        }
    return broker_truth_report(mode=_runtime_mode(mode), market=mkt, refresh=True, ttl_sec=int(ttl_sec or 30))


def run_scheduler_once(
    *,
    mode: str = "live",
    markets: list[str] | tuple[str, ...] = ("KR", "US"),
    force: bool = False,
    dry_run: bool = False,
    now_dt: datetime | None = None,
    refresh_interval_min: int = 10,
    failure_retry_min: int = 2,
    preopen_min: int = 20,
    postclose_min: int = 15,
    ttl_sec: int = 180,
    interval_sec: int = 30,
) -> dict[str, Any]:
    runtime_mode = _runtime_mode(mode)
    current = now_dt or datetime.now(KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    else:
        current = current.astimezone(KST)
    state_path = _state_path(runtime_mode)
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("mode", runtime_mode)
    state.setdefault("markets", {})
    state["last_tick_at"] = current.isoformat(timespec="seconds")
    state["refresh_interval_min"] = int(refresh_interval_min)
    state["failure_retry_min"] = int(failure_retry_min)
    state["preopen_min"] = int(preopen_min)
    state["postclose_min"] = int(postclose_min)

    summary: dict[str, Any] = {
        "ok": True,
        "mode": runtime_mode,
        "markets": [market_key(item) for item in markets],
        "force": bool(force),
        "dry_run": bool(dry_run),
        "due": 0,
        "refreshed": 0,
        "skipped": 0,
        "failed": 0,
        "results": [],
    }
    last_error = ""
    last_success_at = ""

    for raw_market in markets:
        mkt = market_key(raw_market)
        window = market_refresh_window(
            mkt,
            now_dt=current,
            preopen_min=preopen_min,
            postclose_min=postclose_min,
        )
        due, reason = _market_due(
            state,
            mkt,
            now_dt=current,
            force=force,
            refresh_interval_min=refresh_interval_min,
            failure_retry_min=failure_retry_min,
            window=window,
        )
        market_result: dict[str, Any] = {
            "market": mkt,
            "due": bool(due),
            "reason": reason,
            "window": window,
        }
        if not due:
            summary["skipped"] += 1
            summary["results"].append(market_result)
            continue
        summary["due"] += 1
        attempt_at = current.isoformat(timespec="seconds")
        try:
            report = _refresh_market(runtime_mode, mkt, ttl_sec=ttl_sec, dry_run=dry_run)
            ok = bool(report.get("ok")) and not bool(report.get("stale")) and not bool(report.get("missing"))
            market_result.update(
                {
                    "ok": ok,
                    "last_success_at": report.get("last_success_at", ""),
                    "positions_count": len(report.get("positions") or []),
                    "open_orders_count": len(report.get("open_orders") or []),
                    "today_fills_count": len(report.get("today_fills") or []),
                    "stale": bool(report.get("stale")),
                    "missing": bool(report.get("missing")),
                    "error": str(report.get("error") or ""),
                }
            )
            state["markets"][mkt] = {
                "last_attempt_at": attempt_at,
                "last_success_at": attempt_at if ok else str(report.get("last_success_at") or ""),
                "last_status": "ok" if ok else "failed",
                "last_error": "" if ok else str(report.get("error") or "broker truth refresh returned stale/missing"),
                "last_window": window,
                "last_counts": {
                    "positions": len(report.get("positions") or []),
                    "open_orders": len(report.get("open_orders") or []),
                    "today_fills": len(report.get("today_fills") or []),
                },
            }
            if ok:
                summary["refreshed"] += 1
                last_success_at = attempt_at
            else:
                summary["failed"] += 1
                summary["ok"] = False
                last_error = state["markets"][mkt]["last_error"]
        except Exception as exc:
            market_result.update({"ok": False, "error": str(exc)})
            state["markets"][mkt] = {
                "last_attempt_at": attempt_at,
                "last_success_at": "",
                "last_status": "failed",
                "last_error": str(exc),
                "last_window": window,
            }
            summary["failed"] += 1
            summary["ok"] = False
            last_error = str(exc)
        summary["results"].append(market_result)
        _append_event(runtime_mode, {"event": "refresh", **market_result})

    _save_json(state_path, state)
    _write_heartbeat(
        runtime_mode,
        interval_sec=interval_sec,
        status="success" if summary["ok"] else "error",
        last_success_at=last_success_at,
        last_error=last_error,
        last_result=summary,
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sidecar scheduler for live broker truth snapshot refreshes.")
    parser.add_argument("--mode", choices=["paper", "live"], default="live")
    parser.add_argument("--markets", default="KR,US")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--interval-sec", type=int, default=30)
    parser.add_argument("--refresh-interval-min", type=int, default=10)
    parser.add_argument("--failure-retry-min", type=int, default=2)
    parser.add_argument("--preopen-min", type=int, default=20)
    parser.add_argument("--postclose-min", type=int, default=15)
    parser.add_argument("--ttl-sec", type=int, default=180)
    parser.add_argument("--env-path", default="")
    parser.add_argument("--no-refresh-on-start", action="store_true")
    args = parser.parse_args(argv)

    if not args.once and not args.loop:
        args.once = True
    env_loaded = _load_env(args.mode, args.env_path or None)
    markets = _parse_markets(args.markets)
    locked, lock_path, lock_detail = _acquire_lock(args.mode)
    if not locked:
        print(f"[broker truth scheduler] single-instance lock blocked: {lock_detail}", file=sys.stderr)
        return 2
    try:
        first = True
        while True:
            force = bool(args.force or (first and args.loop and not args.no_refresh_on_start))
            summary = run_scheduler_once(
                mode=args.mode,
                markets=markets,
                force=force,
                dry_run=args.dry_run,
                refresh_interval_min=args.refresh_interval_min,
                failure_retry_min=args.failure_retry_min,
                preopen_min=args.preopen_min,
                postclose_min=args.postclose_min,
                ttl_sec=args.ttl_sec,
                interval_sec=args.interval_sec,
            )
            summary["env_loaded"] = env_loaded
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
            else:
                print(
                    f"[broker truth scheduler] mode={summary['mode']} markets={','.join(markets)} "
                    f"force={force} due={summary['due']} refreshed={summary['refreshed']} "
                    f"skipped={summary['skipped']} failed={summary['failed']} dry_run={summary['dry_run']}"
                )
            if not summary.get("ok", False):
                if not args.loop:
                    return 1
            if not args.loop:
                return 0 if summary.get("ok", False) else 1
            first = False
            time.sleep(max(10, int(args.interval_sec)))
    finally:
        _release_lock(lock_path)


if __name__ == "__main__":
    raise SystemExit(main())
