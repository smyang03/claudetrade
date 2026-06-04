from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from runtime_paths import get_runtime_path

KST = timezone(timedelta(hours=9))


def _market_key(market: str) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


def _now() -> datetime:
    return datetime.now(KST)


def current_session_date() -> str:
    return _now().date().isoformat()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _enabled() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST") and not str(os.getenv("SELECTION_SMART_SKIP_STATE_DIR", "") or "").strip():
        return False
    return str(os.getenv("SELECTION_SMART_SKIP_ENABLED", "true") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


def _mode() -> str:
    raw = str(os.getenv("SELECTION_SMART_SKIP_MODE", "observe") or "observe").strip().lower()
    if raw in {"live", "reuse"}:
        return "live"
    if raw in {"off", "disabled", "false", "0"}:
        return "off"
    return "observe"


def _state_dir() -> Path:
    raw = str(os.getenv("SELECTION_SMART_SKIP_STATE_DIR", "") or "").strip()
    path = Path(raw).expanduser() if raw else get_runtime_path("state", make_parents=False)
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path(market: str, session_date: str | None = None) -> Path:
    date_key = str(session_date or current_session_date()).replace("/", "-").strip()
    return _state_dir() / f"selection_smart_skip_{_market_key(market)}_{date_key}.json"


def sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def scope_key(*, market: str, consensus_mode: str, execution_phase: str) -> str:
    payload = {
        "market": _market_key(market),
        "consensus_mode": str(consensus_mode or "").strip().upper(),
        "execution_phase": str(execution_phase or "").strip().lower(),
    }
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))[:20]


def empty_state(market: str, session_date: str | None = None) -> dict[str, Any]:
    return {
        "market": _market_key(market),
        "date": str(session_date or current_session_date()),
        "enabled": _enabled(),
        "mode": _mode(),
        "full_call_count": 0,
        "reuse_count": 0,
        "observe_hit_count": 0,
        "fail_open_count": 0,
        "last_full_call_at": "",
        "last_reuse_at": "",
        "last_observe_hit": {},
        "last_fail_open": {},
        "fail_open_reasons": {},
        "last_entry_by_scope": {},
        "history": [],
    }


def load_state(market: str, session_date: str | None = None) -> dict[str, Any]:
    path = state_path(market, session_date)
    if not path.exists():
        return empty_state(market, session_date)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty_state(market, session_date)
    state = empty_state(market, session_date)
    if isinstance(payload, dict):
        state.update(payload)
    state["market"] = _market_key(market)
    state["date"] = str(session_date or state.get("date") or current_session_date())
    for key in ("full_call_count", "reuse_count", "observe_hit_count", "fail_open_count"):
        try:
            state[key] = max(0, int(state.get(key) or 0))
        except Exception:
            state[key] = 0
    if not isinstance(state.get("last_entry_by_scope"), dict):
        state["last_entry_by_scope"] = {}
    if not isinstance(state.get("history"), list):
        state["history"] = []
    if not isinstance(state.get("fail_open_reasons"), dict):
        state["fail_open_reasons"] = {}
    return state


def save_state(market: str, state: dict[str, Any], session_date: str | None = None) -> None:
    path = state_path(market, session_date or str(state.get("date") or ""))
    normalized = empty_state(market, session_date or str(state.get("date") or ""))
    normalized.update(dict(state or {}))
    normalized["market"] = _market_key(market)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _record_observe_hit(
    *,
    market: str,
    session_date: str,
    scope: str,
    prompt_hash: str,
    prompt_candidate_count: int,
    cached_at: str,
) -> dict[str, Any]:
    state = load_state(market, session_date)
    now = _now_iso()
    state["enabled"] = _enabled()
    state["mode"] = _mode()
    state["observe_hit_count"] = int(state.get("observe_hit_count") or 0) + 1
    state["last_observe_hit"] = {
        "at": now,
        "scope": scope,
        "prompt_hash": prompt_hash,
        "prompt_candidate_count": int(prompt_candidate_count or 0),
        "cached_at": cached_at,
        "live_reuse_suppressed": True,
    }
    history = list(state.get("history") or [])
    history.append({"at": now, "event": "observe_hit", "scope": scope, "prompt_hash": prompt_hash})
    state["history"] = history[-100:]
    save_state(market, state, session_date)
    return {
        "reuse": False,
        "would_reuse": True,
        "reason": "observe_only_cache_hit",
        "scope": scope,
        "cached_at": cached_at,
    }


def _record_fail_open(
    *,
    market: str,
    session_date: str,
    scope: str,
    reason: str,
    prompt_hash: str,
) -> dict[str, Any]:
    state = load_state(market, session_date)
    now = _now_iso()
    state["enabled"] = _enabled()
    state["mode"] = _mode()
    state["fail_open_count"] = int(state.get("fail_open_count") or 0) + 1
    reasons = dict(state.get("fail_open_reasons") or {})
    reasons[reason] = int(reasons.get(reason) or 0) + 1
    state["fail_open_reasons"] = reasons
    state["last_fail_open"] = {
        "at": now,
        "scope": scope,
        "reason": reason,
        "prompt_hash": prompt_hash,
    }
    history = list(state.get("history") or [])
    history.append({"at": now, "event": "fail_open", "scope": scope, "reason": reason})
    state["history"] = history[-100:]
    save_state(market, state, session_date)
    return {"reuse": False, "reason": reason}


def _parse_iso(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _has_trade_ready_or_price_plan(meta: dict[str, Any]) -> bool:
    if list(meta.get("trade_ready") or []):
        return True
    for key in ("price_targets", "_pathb_price_targets", "candidate_actions", "_candidate_action_routes"):
        value = meta.get(key)
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, list) and value:
            return True
    return False


def _cacheable_meta(meta: dict[str, Any]) -> dict[str, Any]:
    cached = copy.deepcopy(dict(meta or {}))
    for key in (
        "_final_prompt_pool",
        "_excluded_from_prompt",
        "compact_evidence_shadow_sample",
        "_shadow_overlay_prompt_pool",
    ):
        cached.pop(key, None)
    return cached


def maybe_reuse(
    *,
    market: str,
    consensus_mode: str,
    execution_phase: str,
    prompt_hash: str,
    prompt_candidate_count: int,
    preopen_watch: bool = False,
    session_date: str | None = None,
) -> dict[str, Any]:
    market_key = _market_key(market)
    date_key = str(session_date or current_session_date())
    scope = scope_key(market=market_key, consensus_mode=consensus_mode, execution_phase=execution_phase)
    prompt_hash = str(prompt_hash or "").strip()
    mode = _mode()

    if not _enabled() or mode == "off":
        return {"reuse": False, "reason": "disabled"}
    if str(os.getenv("SELECTION_SMART_SKIP_FORCE_CALL", "false") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return _record_fail_open(
            market=market_key,
            session_date=date_key,
            scope=scope,
            reason="force_call",
            prompt_hash=prompt_hash,
        )
    if preopen_watch:
        return _record_fail_open(
            market=market_key,
            session_date=date_key,
            scope=scope,
            reason="preopen_watch",
            prompt_hash=prompt_hash,
        )
    if not prompt_hash:
        return _record_fail_open(
            market=market_key,
            session_date=date_key,
            scope=scope,
            reason="missing_prompt_hash",
            prompt_hash=prompt_hash,
        )
    min_candidates = max(1, int(float(os.getenv("SELECTION_SMART_SKIP_MIN_PROMPT_CANDIDATES", "1") or 1)))
    if int(prompt_candidate_count or 0) < min_candidates:
        return _record_fail_open(
            market=market_key,
            session_date=date_key,
            scope=scope,
            reason="too_few_candidates",
            prompt_hash=prompt_hash,
        )

    state = load_state(market_key, date_key)
    entry = dict((state.get("last_entry_by_scope") or {}).get(scope) or {})
    if not entry:
        return _record_fail_open(
            market=market_key,
            session_date=date_key,
            scope=scope,
            reason="no_cached_entry",
            prompt_hash=prompt_hash,
        )
    if str(entry.get("prompt_hash") or "") != prompt_hash:
        return _record_fail_open(
            market=market_key,
            session_date=date_key,
            scope=scope,
            reason="prompt_changed",
            prompt_hash=prompt_hash,
        )
    try:
        cached_count = int(entry.get("prompt_candidate_count") or 0)
    except Exception:
        cached_count = 0
    if cached_count != int(prompt_candidate_count or 0):
        return _record_fail_open(
            market=market_key,
            session_date=date_key,
            scope=scope,
            reason="candidate_count_changed",
            prompt_hash=prompt_hash,
        )
    created_at = _parse_iso(str(entry.get("created_at") or ""))
    ttl_min = max(1, int(float(os.getenv("SELECTION_SMART_SKIP_TTL_MIN", "30") or 30)))
    if created_at is None or (_now() - created_at).total_seconds() > ttl_min * 60:
        return _record_fail_open(
            market=market_key,
            session_date=date_key,
            scope=scope,
            reason="ttl_expired",
            prompt_hash=prompt_hash,
        )
    selection_meta = dict(entry.get("selection_meta") or {})
    if not list(selection_meta.get("watchlist") or []):
        return _record_fail_open(
            market=market_key,
            session_date=date_key,
            scope=scope,
            reason="cached_watchlist_empty",
            prompt_hash=prompt_hash,
        )
    if selection_meta.get("_fallback_mode") and not str(os.getenv("SELECTION_SMART_SKIP_REUSE_FALLBACKS", "false")).lower() in {"1", "true", "yes", "on"}:
        return _record_fail_open(
            market=market_key,
            session_date=date_key,
            scope=scope,
            reason="cached_fallback_result",
            prompt_hash=prompt_hash,
        )
    if _has_trade_ready_or_price_plan(selection_meta) and not str(os.getenv("SELECTION_SMART_SKIP_ALLOW_TRADE_READY_REUSE", "false")).lower() in {"1", "true", "yes", "on"}:
        return _record_fail_open(
            market=market_key,
            session_date=date_key,
            scope=scope,
            reason="cached_entry_actionable",
            prompt_hash=prompt_hash,
        )

    if mode != "live":
        return _record_observe_hit(
            market=market_key,
            session_date=date_key,
            scope=scope,
            prompt_hash=prompt_hash,
            prompt_candidate_count=prompt_candidate_count,
            cached_at=str(entry.get("created_at") or ""),
        )

    now = _now_iso()
    state["enabled"] = True
    state["mode"] = mode
    state["reuse_count"] = int(state.get("reuse_count") or 0) + 1
    state["last_reuse_at"] = now
    history = list(state.get("history") or [])
    history.append({"at": now, "event": "reuse", "scope": scope, "prompt_hash": prompt_hash})
    state["history"] = history[-100:]
    save_state(market_key, state, date_key)
    selection_meta["_smart_skip_reused"] = True
    selection_meta["_smart_skip_scope"] = scope
    selection_meta["_smart_skip_prompt_hash"] = prompt_hash
    selection_meta["_smart_skip_cached_at"] = str(entry.get("created_at") or "")
    return {
        "reuse": True,
        "reason": "prompt_cache_hit",
        "selection_meta": selection_meta,
        "reasons": dict(entry.get("reasons") or {}),
        "scope": scope,
        "cached_at": str(entry.get("created_at") or ""),
    }


def record_full_call(
    *,
    market: str,
    consensus_mode: str,
    execution_phase: str,
    prompt_hash: str,
    prompt_candidate_count: int,
    selection_meta: dict[str, Any],
    reasons: dict[str, Any] | None = None,
    session_date: str | None = None,
) -> None:
    if not _enabled() or _mode() == "off":
        return
    market_key = _market_key(market)
    date_key = str(session_date or current_session_date())
    scope = scope_key(market=market_key, consensus_mode=consensus_mode, execution_phase=execution_phase)
    state = load_state(market_key, date_key)
    now = _now_iso()
    entry = {
        "created_at": now,
        "scope": scope,
        "prompt_hash": str(prompt_hash or ""),
        "prompt_candidate_count": int(prompt_candidate_count or 0),
        "consensus_mode": str(consensus_mode or ""),
        "execution_phase": str(execution_phase or ""),
        "selection_meta": _cacheable_meta(selection_meta),
        "reasons": copy.deepcopy(dict(reasons or {})),
    }
    by_scope = dict(state.get("last_entry_by_scope") or {})
    by_scope[scope] = entry
    state["last_entry_by_scope"] = by_scope
    state["enabled"] = True
    state["mode"] = _mode()
    state["full_call_count"] = int(state.get("full_call_count") or 0) + 1
    state["last_full_call_at"] = now
    history = list(state.get("history") or [])
    history.append({"at": now, "event": "full_call", "scope": scope, "prompt_hash": str(prompt_hash or "")})
    state["history"] = history[-100:]
    save_state(market_key, state, date_key)
