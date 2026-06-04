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


def _clean_token(value: Any) -> str:
    return str(value or "").strip().upper()


def _candidate_ticker(value: Any, market: str) -> str:
    if not isinstance(value, dict):
        return ""
    ticker = str(value.get("ticker") or value.get("code") or value.get("t") or "").strip()
    if not ticker:
        return ""
    return ticker.upper() if _market_key(market) == "US" else ticker


def _first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return ""


def _score_bucket(value: Any) -> str:
    try:
        score = float(str(value).replace(",", ""))
    except Exception:
        return ""
    if score != score:
        return ""
    score = max(0.0, min(100.0, score))
    return str(int(score // 5) * 5)


def _semantic_candidate(row: dict[str, Any], market: str) -> dict[str, str]:
    live = row.get("live_evidence") if isinstance(row.get("live_evidence"), dict) else {}
    risk = live.get("risk_control_view") if isinstance(live.get("risk_control_view"), dict) else {}
    ticker = _candidate_ticker(row, market)
    evidence_class = _first_value(
        row.get("evidence_class"),
        row.get("selection_evidence_class"),
        live.get("evidence_class"),
    )
    action_ceiling = _first_value(
        row.get("selection_evidence_action_ceiling"),
        row.get("evidence_action_ceiling"),
        row.get("action_ceiling"),
        row.get("discovery_action_ceiling"),
        live.get("action_ceiling"),
        risk.get("action_ceiling"),
    )
    trainer_state = _first_value(
        row.get("trainer_candidate_state"),
        row.get("candidate_pool_role"),
        row.get("candidate_state"),
    )
    source_role = _first_value(
        row.get("candidate_pool_role"),
        row.get("source_type"),
        row.get("candidate_source"),
        row.get("source_file"),
        row.get("discovery_signal_family"),
    )
    score = _first_value(
        row.get("trainer_prompt_score"),
        row.get("candidate_quality_score"),
        row.get("raw_score_current"),
        row.get("score"),
    )
    return {
        "ticker": ticker,
        "evidence_class": _clean_token(evidence_class),
        "action_ceiling": _clean_token(action_ceiling),
        "trainer_state": _clean_token(trainer_state),
        "source_role": _clean_token(source_role),
        "score_bucket": _score_bucket(score),
    }


def semantic_signature(
    *,
    market: str,
    session_date: str,
    consensus_mode: str,
    execution_phase: str,
    candidates: list[dict[str, Any]],
    prompt_contract: str,
    watch_cap: int,
    trade_cap: int,
    session_phase: str = "",
    config_hash: str = "",
    lesson_hash: str = "",
) -> str:
    """Stable smart-skip key that ignores prompt wording and ticker order."""
    market_key = _market_key(market)
    semantic_candidates = [
        item
        for item in (_semantic_candidate(dict(row or {}), market_key) for row in candidates or [])
        if item.get("ticker")
    ]
    semantic_candidates.sort(
        key=lambda item: (
            item.get("ticker", ""),
            item.get("evidence_class", ""),
            item.get("action_ceiling", ""),
            item.get("trainer_state", ""),
            item.get("source_role", ""),
            item.get("score_bucket", ""),
        )
    )
    payload = {
        "schema": "selection_smart_skip.semantic.v1",
        "market": market_key,
        "session_date": str(session_date or "").strip(),
        "consensus_mode": str(consensus_mode or "").strip().upper(),
        "execution_phase": str(execution_phase or "").strip().lower(),
        "prompt_contract": str(prompt_contract or "").strip(),
        "watch_cap": int(watch_cap or 0),
        "trade_cap": int(trade_cap or 0),
        "session_phase": str(session_phase or "").strip().lower(),
        "config_hash": str(config_hash or "").strip(),
        "lesson_hash": str(lesson_hash or "").strip(),
        "candidates": semantic_candidates,
    }
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


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
        "last_reuse": {},
        "last_full_call": {},
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
    if not isinstance(state.get("last_reuse"), dict):
        state["last_reuse"] = {}
    if not isinstance(state.get("last_full_call"), dict):
        state["last_full_call"] = {}
    if not isinstance(state.get("fail_open_reasons"), dict):
        state["fail_open_reasons"] = {}
    return state


def save_state(market: str, state: dict[str, Any], session_date: str | None = None) -> None:
    path = state_path(market, session_date or str(state.get("date") or ""))
    # 이미 load_state()를 통해 정규화된 state를 그대로 저장 — empty_state() 재호출 생략
    payload = dict(state or {})
    payload["market"] = _market_key(market)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
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


_ENTRY_ACTIONS = {"BUY_READY", "PROBE_READY", "ADD_READY"}
_PRICE_PLAN_ACTIONS = {"PULLBACK_WAIT"}
_ACTION_KEYS = ("final_action", "action", "requested_action", "a")
_PRICE_PLAN_KEYS = (
    "price_targets",
    "_pathb_price_targets",
    "_pathb_shadow_price_targets",
    "pt",
    "target",
    "targets",
)
_PRICE_TARGET_FIELDS = {
    "buy_zone_low",
    "buy_zone_high",
    "sell_target",
    "stop_loss",
    "hold_days",
    "confidence",
    "reference_price",
    "current_price",
    "max_entry_price",
    "cancel_if_open_above",
    "lo",
    "hi",
    "tgt",
    "stp",
    "days",
    "conf",
    "cf",
    "ref",
}


def _has_price_plan(value: Any) -> bool:
    if isinstance(value, dict):
        for key in _PRICE_TARGET_FIELDS:
            if key in value and value.get(key) not in (None, "", {}, []):
                return True
        for key in _PRICE_PLAN_KEYS:
            if key in value and _has_price_plan(value.get(key)):
                return True
        if any(str(value.get(key) or "").strip() for key in _ACTION_KEYS):
            return False
        return any(_has_price_plan(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_price_plan(item) for item in value)
    return value not in (None, "")


def _candidate_action_name(item: dict[str, Any]) -> str:
    for key in _ACTION_KEYS:
        action = str(item.get(key) or "").strip().upper()
        if action:
            return action
    return ""


def _iter_candidate_action_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    if any(str(value.get(key) or "").strip() for key in _ACTION_KEYS):
        return [value]
    return [item for item in value.values() if isinstance(item, dict)]


def _candidate_actions_actionable(value: Any) -> bool:
    for item in _iter_candidate_action_items(value):
        action = _candidate_action_name(item)
        if action in _ENTRY_ACTIONS:
            return True
        if action in _PRICE_PLAN_ACTIONS and _has_price_plan(item):
            return True
    return False


def _has_trade_ready_or_price_plan(meta: dict[str, Any]) -> bool:
    if list(meta.get("trade_ready") or []):
        return True
    for key in ("price_targets", "_pathb_price_targets"):
        value = meta.get(key)
        if _has_price_plan(value):
            return True
    for key in ("candidate_actions", "_candidate_action_routes"):
        if _candidate_actions_actionable(meta.get(key)):
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
    # observe 모드에서 early-exit 케이스(force_call/preopen_watch/missing_hash)는
    # disk write 없이 반환 — cache lookup은 이후에 계속 진행해 would_reuse 계측에 사용
    is_observe = mode == "observe"
    if str(os.getenv("SELECTION_SMART_SKIP_FORCE_CALL", "false") or "").strip().lower() in {"1", "true", "yes", "on"}:
        if is_observe:
            return {"reuse": False, "reason": "force_call"}
        return _record_fail_open(
            market=market_key,
            session_date=date_key,
            scope=scope,
            reason="force_call",
            prompt_hash=prompt_hash,
        )
    if preopen_watch:
        if is_observe:
            return {"reuse": False, "reason": "preopen_watch"}
        return _record_fail_open(
            market=market_key,
            session_date=date_key,
            scope=scope,
            reason="preopen_watch",
            prompt_hash=prompt_hash,
        )
    if not prompt_hash:
        if is_observe:
            return {"reuse": False, "reason": "missing_prompt_hash"}
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
    state["last_reuse"] = {
        "at": now,
        "scope": scope,
        "prompt_hash": prompt_hash,
        "prompt_candidate_count": int(prompt_candidate_count or 0),
        "cached_at": str(entry.get("created_at") or ""),
        "full_claude_call_skipped": True,
        "mode": "live",
    }
    history = list(state.get("history") or [])
    history.append({
        "at": now,
        "event": "reuse",
        "scope": scope,
        "prompt_hash": prompt_hash,
        "mode": "live",
        "full_claude_call_skipped": True,
    })
    state["history"] = history[-100:]
    save_state(market_key, state, date_key)
    selection_meta["_smart_skip_reused"] = True
    selection_meta["_smart_skip_mode"] = "live"
    selection_meta["_smart_skip_full_claude_call_skipped"] = True
    selection_meta["_smart_skip_scope"] = scope
    selection_meta["_smart_skip_prompt_hash"] = prompt_hash
    selection_meta["_smart_skip_cached_at"] = str(entry.get("created_at") or "")
    return {
        "reuse": True,
        "reason": "prompt_cache_hit",
        "mode": "live",
        "full_claude_call_skipped": True,
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
    state["last_full_call"] = {
        "at": now,
        "scope": scope,
        "prompt_hash": str(prompt_hash or ""),
        "prompt_candidate_count": int(prompt_candidate_count or 0),
        "mode": _mode(),
        "full_claude_call_skipped": False,
    }
    history = list(state.get("history") or [])
    history.append({
        "at": now,
        "event": "full_call",
        "scope": scope,
        "prompt_hash": str(prompt_hash or ""),
        "mode": _mode(),
        "full_claude_call_skipped": False,
    })
    state["history"] = history[-100:]
    save_state(market_key, state, date_key)
