from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any


DEFAULT_ALLOWED_SOURCES = {
    "analyst_reinvoke",
    "session_open",
    "session_reuse_rescreen",
    "manual_rescreen",
    "rescreen",
    "tuning_rescreen",
    "analyst_reinvoke_rescreen",
    "sub_screener_rescreen",
    "opening_fresh_rescreen",
}
DEFAULT_ALLOWED_ACTIONS = {"WATCH", "PROBE_READY", "BUY_READY", "PULLBACK_WAIT"}
REEVAL_SOURCE = "kr_wait60_reeval"


def parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def ticker_key(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


def _lookup_ticker_map(mapping: Any, market: str, ticker: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    key = ticker_key(market, ticker)
    candidates = [str(ticker or ""), key, str(ticker or "").upper(), key.upper()]
    for candidate in candidates:
        if candidate in mapping:
            return mapping.get(candidate)
    for raw_key, raw_value in mapping.items():
        if ticker_key(market, raw_key) == key:
            return raw_value
    return None


def _candidate_map(candidates: list[dict[str, Any]], market: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in candidates or []:
        if not isinstance(row, dict):
            continue
        key = ticker_key(market, row.get("ticker"))
        if not key:
            continue
        out.setdefault(key, copy.deepcopy(row))
    return out


def _action_map(selection_meta: dict[str, Any], market: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in list((selection_meta or {}).get("candidate_actions") or []):
        if not isinstance(raw, dict):
            continue
        key = ticker_key(market, raw.get("ticker"))
        if not key:
            continue
        out[key] = copy.deepcopy(raw)
    return out


def _route_map(selection_meta: dict[str, Any], market: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in list((selection_meta or {}).get("_candidate_action_routes") or []):
        if not isinstance(raw, dict):
            continue
        key = ticker_key(market, raw.get("ticker"))
        if not key:
            continue
        out[key] = copy.deepcopy(raw)
    return out


def _split_csv(value: Any, default: set[str]) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set(default)
    return {part.strip() for part in text.split(",") if part.strip()}


def _selected_keys(market: str, selected: list[str], selection_meta: dict[str, Any]) -> list[str]:
    raw = list(selected or []) or list((selection_meta or {}).get("watchlist") or [])
    return list(dict.fromkeys(ticker_key(market, item) for item in raw if str(item or "").strip()))


def build_wait_reentry_items(
    *,
    market: str,
    phase: str,
    session_date: str,
    selected: list[str],
    selection_meta: dict[str, Any],
    candidates: list[dict[str, Any]],
    now: datetime,
    delay_min: float = 60.0,
    max_candidates: int = 8,
    allowed_sources: set[str] | None = None,
    allowed_actions: set[str] | None = None,
) -> list[dict[str, Any]]:
    market_key = str(market or "").upper()
    if market_key != "KR":
        return []
    phase_key = str(phase or "").strip()
    if not phase_key or phase_key == REEVAL_SOURCE:
        return []
    source_allow = allowed_sources or DEFAULT_ALLOWED_SOURCES
    if phase_key not in source_allow:
        return []
    action_allow = {item.upper() for item in (allowed_actions or DEFAULT_ALLOWED_ACTIONS)}
    by_candidate = _candidate_map(candidates, market_key)
    by_action = _action_map(selection_meta or {}, market_key)
    by_route = _route_map(selection_meta or {}, market_key)
    delay = max(1.0, float(delay_min or 60.0))
    due_at = now + timedelta(minutes=delay)
    max_items = max(0, int(max_candidates or 0))
    items: list[dict[str, Any]] = []
    for rank, key in enumerate(_selected_keys(market_key, selected, selection_meta or {}), start=1):
        if max_items and len(items) >= max_items:
            break
        candidate = by_candidate.get(key)
        if not candidate:
            continue
        action = by_action.get(key) or {}
        route = by_route.get(key) or {}
        original_action = str(
            action.get("action")
            or route.get("requested_action")
            or route.get("claude_action")
            or "WATCH"
        ).strip().upper()
        if original_action not in action_allow:
            continue
        final_action = str(route.get("final_action") or "").strip().upper()
        if final_action == "HARD_BLOCK":
            continue
        item_id = "|".join([market_key, str(session_date or ""), key, phase_key, original_action])
        reeval_candidate = copy.deepcopy(candidate)
        reeval_candidate["_wait_reentry"] = {
            "source_phase": phase_key,
            "queued_at": now.isoformat(timespec="seconds"),
            "due_at": due_at.isoformat(timespec="seconds"),
            "delay_min": delay,
            "original_action": original_action,
            "route_final_action": final_action,
            "route_reason": str(route.get("reason") or ""),
        }
        items.append(
            {
                "id": item_id,
                "market": market_key,
                "ticker": key,
                "session_date": str(session_date or ""),
                "source_phase": phase_key,
                "queued_at": now.isoformat(timespec="seconds"),
                "due_at": due_at.isoformat(timespec="seconds"),
                "delay_min": delay,
                "selected_rank": rank,
                "original_action": original_action,
                "route_final_action": final_action,
                "route_reason": str(route.get("reason") or ""),
                "candidate": reeval_candidate,
            }
        )
    return items


def pop_due_items(
    queue: list[dict[str, Any]],
    *,
    now: datetime,
    max_items: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    due: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    limit = max(1, int(max_items or 1))
    for item in queue or []:
        due_at = parse_dt((item or {}).get("due_at"))
        if due_at is not None and due_at <= now and len(due) < limit:
            due.append(item)
        else:
            pending.append(item)
    return due, pending


def requeue_items(
    pending: list[dict[str, Any]],
    items: list[dict[str, Any]],
    *,
    now: datetime,
    retry_delay_min: float,
    max_attempts: int,
) -> list[dict[str, Any]]:
    out = list(pending or [])
    retry_due = now + timedelta(minutes=max(1.0, float(retry_delay_min or 1.0)))
    for item in items or []:
        clean = copy.deepcopy(item)
        attempts = int(clean.get("attempts") or 0) + 1
        if attempts > max(0, int(max_attempts or 0)):
            continue
        clean["attempts"] = attempts
        clean["due_at"] = retry_due.isoformat(timespec="seconds")
        out.append(clean)
    return out
