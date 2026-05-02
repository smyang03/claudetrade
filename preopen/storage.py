from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from bot.session_date import KST, resolve_session_date_str, resolve_session_ymd
from runtime_paths import get_runtime_path


def _market_key(market: str) -> str:
    value = str(market or "").upper()
    return "US" if value == "US" else "KR"


def _date_ymd(session_date: str) -> str:
    return str(session_date or "").replace("-", "")


def state_path(market: str, session_date: str | None = None) -> Path:
    market_key = _market_key(market)
    session_date = session_date or resolve_session_date_str(market_key)
    return get_runtime_path("state", f"preopen_{market_key}_{_date_ymd(session_date)}.json")


def log_path(kind: str, market: str, session_date: str | None = None) -> Path:
    market_key = _market_key(market)
    session_date = session_date or resolve_session_date_str(market_key)
    return get_runtime_path("logs", "preopen", f"{_date_ymd(session_date)}_{market_key}_{kind}.jsonl")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def save_preopen_state(market: str, state: dict[str, Any], *, session_date: str | None = None) -> Path:
    market_key = _market_key(market)
    session_date = session_date or state.get("session_date") or resolve_session_date_str(market_key)
    state = dict(state or {})
    state.setdefault("market", market_key)
    state.setdefault("session_date", session_date)
    state.setdefault("captured_at", datetime.now(KST).isoformat(timespec="seconds"))
    path = state_path(market_key, session_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def save_candidate_records(market: str, session_date: str, candidates: list[dict[str, Any]], state: dict[str, Any]) -> None:
    path = log_path("candidates", market, session_date)
    captured_at = state.get("captured_at", "")
    for candidate in candidates:
        append_jsonl(path, {"captured_at": captured_at, **candidate})


def load_preopen_state(
    market: str,
    *,
    session_date: str | None = None,
    max_age_min: int | None = None,
) -> dict[str, Any]:
    market_key = _market_key(market)
    session_date = session_date or resolve_session_date_str(market_key)
    path = state_path(market_key, session_date)
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


def save_rank_diff_record(market: str, session_date: str, record: dict[str, Any]) -> None:
    payload = dict(record or {})
    payload.setdefault("ts", datetime.now(KST).isoformat(timespec="seconds"))
    payload.setdefault("market", _market_key(market))
    payload.setdefault("session_date", session_date)
    append_jsonl(log_path("rank_diff", market, session_date), payload)


def save_outcome_record(market: str, session_date: str, record: dict[str, Any]) -> None:
    payload = dict(record or {})
    payload.setdefault("ts", datetime.now(KST).isoformat(timespec="seconds"))
    payload.setdefault("market", _market_key(market))
    payload.setdefault("session_date", session_date)
    append_jsonl(log_path("outcome", market, session_date), payload)


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


def list_preopen_sessions(market: str, *, limit: int = 20) -> list[dict[str, Any]]:
    market_key = _market_key(market)
    sessions: dict[str, dict[str, Any]] = {}
    state_dir = _state_dir()
    log_dir = _log_dir()
    for path in state_dir.glob(f"preopen_{market_key}_*.json"):
        ymd = path.stem.replace(f"preopen_{market_key}_", "")
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
        for path in log_dir.glob(f"*_{market_key}_{kind}.jsonl"):
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


def _next_actions(market: str, empty_reason: str) -> list[str]:
    market_key = _market_key(market)
    collector = f"python tools/preopen_collector.py --market {market_key} --mode live --once"
    outcome5 = f"python tools/preopen_outcome_updater.py --market {market_key} --offset-min 5 --once"
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


def _scheduler_guidance(market: str) -> dict[str, Any]:
    market_key = _market_key(market)
    if market_key == "US":
        return {
            "market": "US",
            "collector_windows_kst": ["17:00-22:25"],
            "outcome_offsets_min": [5, 30, 60],
            "commands": [
                "python tools/preopen_collector.py --market US --mode live --once",
                "python tools/preopen_outcome_updater.py --market US --offset-min 5 --once",
                "python tools/preopen_outcome_updater.py --market US --offset-min 30 --once",
                "python tools/preopen_outcome_updater.py --market US --offset-min 60 --once",
            ],
        }
    return {
        "market": "KR",
        "collector_windows_kst": ["08:00-09:00"],
        "outcome_offsets_min": [5, 30, 60],
        "commands": [
            "python tools/preopen_collector.py --market KR --mode live --once",
            "python tools/preopen_outcome_updater.py --market KR --offset-min 5 --once",
            "python tools/preopen_outcome_updater.py --market KR --offset-min 30 --once",
            "python tools/preopen_outcome_updater.py --market KR --offset-min 60 --once",
        ],
    }


def load_preopen_dashboard(market: str, *, session_date: str | None = None, limit: int = 50) -> dict[str, Any]:
    market_key = _market_key(market)
    session_date = session_date or resolve_session_date_str(market_key)
    state = load_preopen_state(market_key, session_date=session_date, max_age_min=24 * 60) or {}
    rank_diff = read_jsonl_tail(log_path("rank_diff", market_key, session_date), limit)
    outcome = read_jsonl_tail(log_path("outcome", market_key, session_date), limit)
    candidates = list((state.get("candidates") or [])[:limit]) if isinstance(state, dict) else []
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
            "candidate_count": len(candidates),
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
        "recent_sessions": list_preopen_sessions(market_key, limit=20),
        "next_actions": _next_actions(market_key, empty_reason),
        "scheduler_guidance": _scheduler_guidance(market_key),
        "paths": {
            "state": str(state_path(market_key, session_date)),
            "candidates": str(log_path("candidates", market_key, session_date)),
            "rank_diff": str(log_path("rank_diff", market_key, session_date)),
            "outcome": str(log_path("outcome", market_key, session_date)),
        },
    }


def current_session_date_for_storage(market: str) -> str:
    return resolve_session_date_str(_market_key(market))


def current_session_ymd_for_storage(market: str) -> str:
    return resolve_session_ymd(_market_key(market))
