from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from bot.session_date import KST, resolve_session_date_str, resolve_session_ymd
from runtime_paths import get_runtime_path


def _runtime_mode(mode: str = "live") -> str:
    return "live" if str(mode or "").lower() == "live" else "paper"


def _market_key(market: str) -> str:
    value = str(market or "").upper()
    return "US" if value == "US" else "KR"


def _date_ymd(session_date: str) -> str:
    return str(session_date or "").replace("-", "")


def _mode_suffix(mode: str = "live") -> str:
    runtime_mode = _runtime_mode(mode)
    return "" if runtime_mode == "live" else f"_{runtime_mode}"


def state_path(market: str, session_date: str | None = None, mode: str = "live") -> Path:
    market_key = _market_key(market)
    session_date = session_date or resolve_session_date_str(market_key)
    if _runtime_mode(mode) == "live":
        return get_runtime_path("state", f"preopen_{market_key}_{_date_ymd(session_date)}.json")
    return get_runtime_path("state", f"preopen_{_runtime_mode(mode)}_{market_key}_{_date_ymd(session_date)}.json")


def log_path(kind: str, market: str, session_date: str | None = None, mode: str = "live") -> Path:
    market_key = _market_key(market)
    session_date = session_date or resolve_session_date_str(market_key)
    return get_runtime_path("logs", "preopen", f"{_date_ymd(session_date)}_{market_key}_{kind}{_mode_suffix(mode)}.jsonl")


def scheduler_state_path(mode: str = "live") -> Path:
    return get_runtime_path("state", f"preopen_scheduler_{_runtime_mode(mode)}.json")


def scheduler_event_path(mode: str = "live") -> Path:
    today = datetime.now(KST).strftime("%Y%m%d")
    return get_runtime_path("logs", "preopen", f"{today}_scheduler_{_runtime_mode(mode)}.jsonl")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def save_preopen_state(
    market: str,
    state: dict[str, Any],
    *,
    session_date: str | None = None,
    mode: str = "live",
) -> Path:
    market_key = _market_key(market)
    runtime_mode = _runtime_mode(mode)
    session_date = session_date or state.get("session_date") or resolve_session_date_str(market_key)
    state = dict(state or {})
    state.setdefault("market", market_key)
    state["mode"] = runtime_mode
    state.setdefault("session_date", session_date)
    state.setdefault("captured_at", datetime.now(KST).isoformat(timespec="seconds"))
    path = state_path(market_key, session_date, mode=runtime_mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def save_candidate_records(
    market: str,
    session_date: str,
    candidates: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    mode: str = "live",
) -> None:
    runtime_mode = _runtime_mode(mode)
    path = log_path("candidates", market, session_date, mode=runtime_mode)
    captured_at = state.get("captured_at", "")
    for candidate in candidates:
        append_jsonl(path, {"captured_at": captured_at, **candidate, "mode": runtime_mode})


def load_preopen_state(
    market: str,
    *,
    session_date: str | None = None,
    max_age_min: int | None = None,
    mode: str = "live",
) -> dict[str, Any]:
    market_key = _market_key(market)
    runtime_mode = _runtime_mode(mode)
    session_date = session_date or resolve_session_date_str(market_key)
    path = state_path(market_key, session_date, mode=runtime_mode)
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    captured_at = str(state.get("captured_at", "") or "")
    max_age = max_age_min
    if max_age is None:
        try:
            max_age = int(os.getenv("PREOPEN_STATE_MAX_AGE_MIN", "60"))
        except Exception:
            max_age = 60
    if captured_at and max_age > 0:
        try:
            captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=KST)
            age_min = (datetime.now(KST) - captured.astimezone(KST)).total_seconds() / 60.0
            state["state_age_min"] = round(age_min, 2)
            if age_min > max_age:
                state["stale"] = True
                return {}
        except Exception:
            return {}
    state.setdefault("state_age_min", None)
    state.setdefault("stale", False)
    return state


def save_rank_diff_record(market: str, session_date: str, record: dict[str, Any], *, mode: str = "live") -> None:
    runtime_mode = _runtime_mode(mode)
    payload = dict(record or {})
    payload.setdefault("ts", datetime.now(KST).isoformat(timespec="seconds"))
    payload.setdefault("market", _market_key(market))
    payload["mode"] = runtime_mode
    payload.setdefault("session_date", session_date)
    append_jsonl(log_path("rank_diff", market, session_date, mode=runtime_mode), payload)


def save_outcome_record(market: str, session_date: str, record: dict[str, Any], *, mode: str = "live") -> None:
    runtime_mode = _runtime_mode(mode)
    payload = dict(record or {})
    payload.setdefault("ts", datetime.now(KST).isoformat(timespec="seconds"))
    payload.setdefault("market", _market_key(market))
    payload["mode"] = runtime_mode
    payload.setdefault("session_date", session_date)
    append_jsonl(log_path("outcome", market, session_date, mode=runtime_mode), payload)


def load_preopen_scheduler_state(mode: str = "live") -> dict[str, Any]:
    path = scheduler_state_path(mode)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_preopen_scheduler_state(mode: str, state: dict[str, Any]) -> Path:
    path = scheduler_state_path(mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(state or {})
    payload.setdefault("mode", _runtime_mode(mode))
    payload["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def save_preopen_scheduler_event(mode: str, event: dict[str, Any]) -> None:
    payload = dict(event or {})
    payload.setdefault("ts", datetime.now(KST).isoformat(timespec="seconds"))
    payload.setdefault("mode", _runtime_mode(mode))
    append_jsonl(scheduler_event_path(mode), payload)


def _parse_iso_age_sec(value: str) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return round((datetime.now(KST) - parsed.astimezone(KST)).total_seconds(), 2)
    except Exception:
        return None


def load_preopen_scheduler_dashboard(mode: str = "live", *, limit: int = 20) -> dict[str, Any]:
    runtime_mode = _runtime_mode(mode)
    state = load_preopen_scheduler_state(runtime_mode)
    heartbeat_age = _parse_iso_age_sec(str(state.get("last_tick_at", "") or "")) if state else None
    try:
        interval_sec = int(state.get("interval_sec", 60) or 60)
    except Exception:
        interval_sec = 60
    if not state:
        status = "missing"
    elif heartbeat_age is None:
        status = "unknown"
    elif heartbeat_age <= max(180, interval_sec * 3):
        status = "active"
    else:
        status = "stale"
    events = read_jsonl_tail(scheduler_event_path(runtime_mode), limit=limit)
    last_job = {}
    for event in reversed(events):
        if event.get("event") in {"job_success", "job_failed", "job_timeout", "job_dry_run"}:
            last_job = event
            break
    return {
        "mode": runtime_mode,
        "status": status,
        "state": state,
        "heartbeat_age_sec": heartbeat_age,
        "last_tick_at": state.get("last_tick_at", "") if state else "",
        "last_job": last_job,
        "recent_events": events,
        "state_path": str(scheduler_state_path(runtime_mode)),
        "event_path": str(scheduler_event_path(runtime_mode)),
        "start_command": f"python tools/preopen_scheduler.py --mode {runtime_mode} --markets KR,US --loop",
    }


def read_jsonl_tail(path: Path, limit: int = 100) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return rows[-max(1, int(limit)):]


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open(encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def _state_dir() -> Path:
    return get_runtime_path("state", "_preopen_scan.tmp").parent


def _log_dir() -> Path:
    return get_runtime_path("logs", "preopen", "_preopen_scan.tmp").parent


def _iso_from_ymd(value: str) -> str:
    raw = str(value or "")
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def list_preopen_sessions(market: str, *, limit: int = 20, mode: str = "live") -> list[dict[str, Any]]:
    market_key = _market_key(market)
    runtime_mode = _runtime_mode(mode)
    sessions: dict[str, dict[str, Any]] = {}
    state_dir = _state_dir()
    log_dir = _log_dir()
    state_pattern = f"preopen_{market_key}_*.json" if runtime_mode == "live" else f"preopen_{runtime_mode}_{market_key}_*.json"
    state_prefix = f"preopen_{market_key}_" if runtime_mode == "live" else f"preopen_{runtime_mode}_{market_key}_"
    for path in state_dir.glob(state_pattern):
        ymd = path.stem.replace(state_prefix, "")
        session_date = _iso_from_ymd(ymd)
        item = sessions.setdefault(session_date, {"session_date": session_date})
        item["state_exists"] = True
        item["state_path"] = str(path)
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            item["collector_status"] = state.get("collector_status", "")
            item["captured_at"] = state.get("captured_at", "")
            item["candidate_count"] = int(state.get("candidate_count", len(state.get("candidates") or [])) or 0)
            item["token_status"] = state.get("token_status", "")
        except Exception as exc:
            item["state_error"] = str(exc)
    for kind in ("candidates", "rank_diff", "outcome"):
        log_pattern = f"*_{market_key}_{kind}.jsonl" if runtime_mode == "live" else f"*_{market_key}_{kind}_{runtime_mode}.jsonl"
        for path in log_dir.glob(log_pattern):
            name = path.name
            ymd = name.split("_", 1)[0]
            session_date = _iso_from_ymd(ymd)
            item = sessions.setdefault(session_date, {"session_date": session_date})
            item[f"{kind}_log_exists"] = True
            item[f"{kind}_count"] = _line_count(path)
    ordered = sorted(sessions.values(), key=lambda item: item.get("session_date", ""), reverse=True)
    return ordered[: max(1, int(limit))]


def _avg(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 4)


def _performance_summary(rank_diff: list[dict[str, Any]], outcome: list[dict[str, Any]]) -> dict[str, Any]:
    top3 = []
    for row in rank_diff:
        try:
            if row.get("shadow_preopen_rank") is not None and int(row.get("shadow_preopen_rank") or 999) <= 3:
                top3.append(row)
        except Exception:
            continue
    outcome_30m = []
    outcome_60m = []
    for row in outcome:
        try:
            if row.get("post_open_30m_return_pct") is not None:
                outcome_30m.append(float(row.get("post_open_30m_return_pct")))
            if row.get("post_open_60m_return_pct") is not None:
                outcome_60m.append(float(row.get("post_open_60m_return_pct")))
        except Exception:
            continue
    return {
        "rank_diff_rows": len(rank_diff),
        "outcome_rows": len(outcome),
        "top3_selected": sum(1 for row in top3 if row.get("actual_selected")),
        "top3_trade_ready": sum(1 for row in top3 if row.get("actual_trade_ready")),
        "avg_30m_return_pct": _avg(outcome_30m),
        "avg_60m_return_pct": _avg(outcome_60m),
        "review_status": "collect_5_to_10_sessions_before_enabling_behavior",
    }


def _empty_reason(state: dict[str, Any], candidates: list[dict[str, Any]], rank_diff: list[dict[str, Any]], outcome: list[dict[str, Any]]) -> str:
    if not state:
        return "collector_not_run"
    status = str(state.get("collector_status") or "")
    token_status = str(state.get("token_status") or "")
    if status in {"token_expired", "token_unavailable", "token_invalid"}:
        return status
    if bool(state.get("stale")):
        return "state_stale"
    if not candidates:
        return status or "no_candidates"
    if not rank_diff:
        return "waiting_for_claude_selection"
    if not outcome:
        return "waiting_for_outcome_update"
    return "ready"


def _next_actions(market: str, empty_reason: str, mode: str = "live") -> list[str]:
    market_key = _market_key(market)
    runtime_mode = _runtime_mode(mode)
    collector = f"python tools/preopen_collector.py --market {market_key} --mode {runtime_mode} --once"
    outcome5 = f"python tools/preopen_outcome_updater.py --market {market_key} --mode {runtime_mode} --offset-min 5 --once"
    if empty_reason == "collector_not_run":
        return [collector, "After collector runs, refresh /preopen and verify candidate_count > 0."]
    if empty_reason in {"token_expired", "token_unavailable", "token_invalid"}:
        return ["Refresh or verify KIS token before KR enrichment.", collector]
    if empty_reason == "waiting_for_claude_selection":
        return ["Run the normal bot session selection; rank_diff will be written after Claude selection."]
    if empty_reason == "waiting_for_outcome_update":
        return [outcome5, "Run additional outcome updates at 30m and 60m after regular open."]
    if empty_reason == "state_stale":
        return [collector]
    return []


def _scheduler_guidance(market: str, mode: str = "live") -> dict[str, Any]:
    market_key = _market_key(market)
    runtime_mode = _runtime_mode(mode)
    automatic_command = f"python tools/preopen_scheduler.py --mode {runtime_mode} --markets KR,US --loop"
    try:
        from preopen.scheduler import default_outcome_offsets_min

        offsets = list(default_outcome_offsets_min(market_key, resolve_session_date_str(market_key)))
    except Exception:
        offsets = [5, 30, 60, 90, 120]
    outcome_commands = [
        f"python tools/preopen_outcome_updater.py --market {market_key} --mode {runtime_mode} --offset-min {offset} --once"
        for offset in offsets
    ]
    if market_key == "US":
        return {
            "market": "US",
            "collector_windows_kst": ["DST 17:00-22:25", "non-DST 18:00-23:25"],
            "outcome_offsets_min": offsets,
            "automatic_command": automatic_command,
            "commands": [
                f"python tools/preopen_collector.py --market US --mode {runtime_mode} --once",
                *outcome_commands,
            ],
        }
    return {
        "market": "KR",
        "collector_windows_kst": ["08:00-09:00"],
        "outcome_offsets_min": offsets,
        "automatic_command": automatic_command,
        "commands": [
            f"python tools/preopen_collector.py --market KR --mode {runtime_mode} --once",
            *outcome_commands,
        ],
    }


def load_preopen_dashboard(
    market: str,
    *,
    session_date: str | None = None,
    limit: int = 50,
    mode: str = "live",
) -> dict[str, Any]:
    market_key = _market_key(market)
    runtime_mode = _runtime_mode(mode)
    session_date = session_date or resolve_session_date_str(market_key)
    state = load_preopen_state(market_key, session_date=session_date, max_age_min=24 * 60, mode=runtime_mode) or {}
    candidate_log = read_jsonl_tail(log_path("candidates", market_key, session_date, mode=runtime_mode), limit)
    rank_diff = read_jsonl_tail(log_path("rank_diff", market_key, session_date, mode=runtime_mode), limit)
    outcome = read_jsonl_tail(log_path("outcome", market_key, session_date, mode=runtime_mode), limit)
    state_candidates = list(state.get("candidates") or []) if isinstance(state, dict) else []
    candidate_source = "state" if state_candidates else ""
    all_candidates = state_candidates
    if not all_candidates and candidate_log:
        all_candidates = candidate_log
        candidate_source = "candidate_log"
        if not state:
            state = {
                "market": market_key,
                "session_date": session_date,
                "collector_status": "log_only",
                "source_status": "candidate_log",
                "captured_at": candidate_log[-1].get("captured_at", ""),
                "state_age_min": None,
                "stale": False,
            }
        else:
            state.setdefault("source_status", "candidate_log")
    candidates = all_candidates[:limit]
    candidate_log_count = _line_count(log_path("candidates", market_key, session_date, mode=runtime_mode))
    candidate_total_count = max(
        int(state.get("candidate_count", 0) or 0) if isinstance(state, dict) else 0,
        len(all_candidates),
        candidate_log_count,
    )
    empty_reason = _empty_reason(state, candidates, rank_diff, outcome)
    return {
        "market": market_key,
        "session_date": session_date,
        "state": state,
        "summary": {
            "collector_status": state.get("collector_status", "missing") if state else "missing",
            "captured_at": state.get("captured_at", "") if state else "",
            "state_age_min": state.get("state_age_min") if state else None,
            "stale": bool(state.get("stale", False)) if state else False,
            "candidate_count": candidate_total_count,
            "candidate_total_count": candidate_total_count,
            "candidate_display_count": len(candidates),
            "candidate_log_count": candidate_log_count,
            "candidate_source": candidate_source or "none",
            "rank_diff_count": len(rank_diff),
            "outcome_count": len(outcome),
            "token_status": state.get("token_status", "") if state else "",
            "source_status": state.get("source_status", "") if state else "",
            "provider": state.get("provider", "") if state else "",
            "data_quality": state.get("data_quality", "") if state else "",
            "empty_reason": empty_reason,
            "has_data": bool(candidates or rank_diff or outcome),
        },
        "candidates": candidates,
        "rank_diff": rank_diff,
        "outcome": outcome,
        "performance_summary": _performance_summary(rank_diff, outcome),
        "scheduler": load_preopen_scheduler_dashboard(mode=runtime_mode, limit=20),
        "recent_sessions": list_preopen_sessions(market_key, limit=20, mode=runtime_mode),
        "next_actions": _next_actions(market_key, empty_reason, mode=runtime_mode),
        "scheduler_guidance": _scheduler_guidance(market_key, mode=runtime_mode),
        "paths": {
            "state": str(state_path(market_key, session_date, mode=runtime_mode)),
            "candidates": str(log_path("candidates", market_key, session_date, mode=runtime_mode)),
            "rank_diff": str(log_path("rank_diff", market_key, session_date, mode=runtime_mode)),
            "outcome": str(log_path("outcome", market_key, session_date, mode=runtime_mode)),
        },
    }


def current_session_date_for_storage(market: str) -> str:
    return resolve_session_date_str(_market_key(market))


def current_session_ymd_for_storage(market: str) -> str:
    return resolve_session_ymd(_market_key(market))
