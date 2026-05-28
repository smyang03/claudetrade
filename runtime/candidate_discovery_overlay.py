from __future__ import annotations

import json
import os
from typing import Any


DISCOVERY_OVERLAY_VERSION = "discovery_overlay_v1"
USEFUL_SIGNAL_FAMILIES = {
    "near_breakout",
    "momentum_now",
    "volume_surge",
    "liquidity_leader",
    "preopen_ignition",
    "source_consensus",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, *, low: int = 0, high: int = 100) -> int:
    raw = os.getenv(name)
    try:
        value = int(float(str(raw).replace(",", ""))) if raw not in (None, "") else int(default)
    except Exception:
        value = int(default)
    return max(low, min(high, value))


def _market_key(market: str) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


def _ticker_key(ticker: Any, market: str) -> str:
    text = str(ticker or "").strip()
    return text.upper() if _market_key(market) == "US" else text


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return float(default)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except Exception:
        return int(default)


def _list_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item or "").strip()]
        if isinstance(parsed, dict):
            return [
                str(key)
                for key, enabled in parsed.items()
                if enabled and str(key or "").strip()
            ] or [str(item) for item in parsed.values() if str(item or "").strip()]
        return [part.strip() for part in text.replace("|", ",").split(",") if part.strip()]
    if isinstance(value, dict):
        return [
            str(key)
            for key, enabled in value.items()
            if enabled and str(key or "").strip()
        ] or [str(item) for item in value.values() if str(item or "").strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)]


def _signal_token(value: Any, market: str) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    prefix = f"{_market_key(market)}:"
    if token.upper().startswith(prefix):
        token = token[len(prefix):]
    return token.strip().lower()


def signal_family(row: dict[str, Any], *, market: str) -> list[str]:
    signals: set[str] = set()
    primary = _signal_token(row.get("primary_bucket") or row.get("category"), market)
    if primary in USEFUL_SIGNAL_FAMILIES:
        signals.add(primary)
    for key in ("secondary_buckets", "secondary_buckets_json", "source_tags", "source_tags_json"):
        for raw in _list_values(row.get(key)):
            token = _signal_token(raw, market)
            if token in USEFUL_SIGNAL_FAMILIES:
                signals.add(token)
    return sorted(signals)


def _is_same_day_stopped(row: dict[str, Any]) -> bool:
    return bool(row.get("same_day_stopped")) or str(row.get("status") or "").strip().lower() == "same_day_stopped"


def _candidate_from_excluded(item: dict[str, Any]) -> dict[str, Any]:
    inner = item.get("candidate")
    row = dict(inner) if isinstance(inner, dict) else dict(item)
    for key in (
        "raw_rank",
        "trainer_score_rank",
        "trainer_prompt_score",
        "trainer_plan_a_score",
        "trainer_pathb_wait_score",
        "trainer_risk_score",
        "trainer_candidate_state",
        "prompt_excluded_reason",
    ):
        if row.get(key) in (None, "") and item.get(key) not in (None, ""):
            row[key] = item.get(key)
    if row.get("prompt_excluded_reason") in (None, ""):
        row["prompt_excluded_reason"] = item.get("prompt_excluded_reason") or item.get("reason") or ""
    return row


def _is_cap_excluded(row: dict[str, Any]) -> bool:
    reason = str(row.get("prompt_excluded_reason") or row.get("reason") or "").strip().lower()
    return reason in {"", "prompt_cap", "hard_cap_cutoff"}


def _eligible(row: dict[str, Any], *, market: str) -> tuple[bool, list[str], str]:
    ticker = _ticker_key(row.get("ticker"), market)
    if not ticker:
        return False, [], "missing_ticker"
    state = str(row.get("trainer_candidate_state") or "").strip().upper()
    if state == "QUARANTINE":
        return False, [], "trainer_quarantine"
    if _is_same_day_stopped(row):
        return False, [], "same_day_stopped"
    if not _is_cap_excluded(row):
        return False, [], "not_cap_excluded"
    signals = signal_family(row, market=market)
    if not signals:
        return False, [], "no_useful_signal"
    primary = _signal_token(row.get("primary_bucket") or row.get("category"), market)
    if _env_bool("DISCOVERY_EXCLUDE_UNCLASSIFIED_ONLY", True) and primary in {"", "unclassified", "unknown", "none"}:
        if not set(signals) - {"near_high"}:
            return False, signals, "unclassified_only"
    if _env_bool("DISCOVERY_EXCLUDE_PULLBACK_ONLY", True) and primary in {"pullback_watch", "gap_pullback", "opening_range_pullback"}:
        if not set(signals) - {primary}:
            return False, signals, "pullback_only"
    return True, signals, ""


def _sort_key(row: dict[str, Any]) -> tuple:
    return (
        _as_int(row.get("trainer_score_rank"), 999999),
        -_as_float(row.get("trainer_prompt_score"), -999999.0),
        _as_float(row.get("trainer_risk_score"), 999999.0),
        _as_int(row.get("raw_rank"), 999999),
        str(row.get("ticker") or ""),
    )


def apply_discovery_overlay(
    prompt_pool: list[dict[str, Any]],
    prompt_pool_meta: dict[str, Any],
    *,
    market: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    market_key = _market_key(market)
    meta = dict(prompt_pool_meta or {})
    core_pool = [dict(row or {}) for row in list(prompt_pool or []) if isinstance(row, dict)]
    base_meta = {
        "_discovery_enabled": False,
        "_discovery_mode": "off",
        "_discovery_max_slots": 0,
        "_discovery_added": 0,
        "_discovery_added_tickers": [],
        "_discovery_role_by_ticker": {},
        "_discovery_action_ceiling_by_ticker": {},
        "_discovery_signal_by_ticker": {},
        "_prompt_pool_core_count": len(core_pool),
        "_prompt_pool_discovery_count": 0,
    }
    meta.update({key: meta.get(key, value) for key, value in base_meta.items()})
    if not _env_bool("DISCOVERY_PROMPT_ENABLED", False):
        return core_pool, meta

    max_slots = _env_int(f"DISCOVERY_MAX_SLOTS_{market_key}", 4 if market_key == "KR" else 3, low=0, high=20)
    meta.update(
        {
            "_discovery_enabled": True,
            "_discovery_mode": "live",
            "_discovery_max_slots": max_slots,
            "_prompt_pool_core_count": len(core_pool),
        }
    )
    if max_slots <= 0:
        return core_pool, meta

    core_keys = {_ticker_key(row.get("ticker"), market_key) for row in core_pool}
    rows: list[tuple[dict[str, Any], list[str]]] = []
    reject_counts: dict[str, int] = {}
    seen: set[str] = set()
    for item in list(meta.get("excluded_from_prompt") or []):
        if not isinstance(item, dict):
            continue
        row = _candidate_from_excluded(item)
        key = _ticker_key(row.get("ticker"), market_key)
        if not key or key in core_keys or key in seen:
            continue
        ok, signals, reason = _eligible(row, market=market_key)
        if not ok:
            reject_counts[reason] = reject_counts.get(reason, 0) + 1
            continue
        row["ticker"] = key
        rows.append((row, signals))
        seen.add(key)

    rows.sort(key=lambda item: _sort_key(item[0]))
    discovery_rows: list[dict[str, Any]] = []
    role_by_ticker: dict[str, str] = {}
    ceiling_by_ticker: dict[str, str] = {}
    signal_by_ticker: dict[str, list[str]] = {}
    for overlay_rank, (row, signals) in enumerate(rows[:max_slots], start=1):
        item = dict(row)
        key = _ticker_key(item.get("ticker"), market_key)
        item["candidate_pool_role"] = "DISCOVERY"
        item["discovery_signal_family"] = ",".join(signals)
        item["discovery_reason"] = "core_cap_signal_candidate"
        item["discovery_action_ceiling"] = "WATCH"
        item["discovery_baseline_trainer_rank"] = item.get("trainer_score_rank")
        item["discovery_overlay_rank"] = overlay_rank
        item["prompt_overlay_added"] = True
        item["prompt_overlay_type"] = "discovery"
        item["final_prompt_included"] = True
        base_version = str(item.get("prompt_pool_version") or meta.get("version") or "")
        item["prompt_pool_version"] = f"{base_version}+{DISCOVERY_OVERLAY_VERSION}" if base_version else DISCOVERY_OVERLAY_VERSION
        discovery_rows.append(item)
        role_by_ticker[key] = "DISCOVERY"
        ceiling_by_ticker[key] = "WATCH"
        signal_by_ticker[key] = signals

    result = core_pool + discovery_rows
    for rank, row in enumerate(result, start=1):
        row["prompt_rank"] = rank
        row["final_prompt_included"] = True

    meta.update(
        {
            "_discovery_added": len(discovery_rows),
            "_discovery_added_tickers": list(role_by_ticker.keys()),
            "_discovery_role_by_ticker": role_by_ticker,
            "_discovery_action_ceiling_by_ticker": ceiling_by_ticker,
            "_discovery_signal_by_ticker": signal_by_ticker,
            "_discovery_reject_counts": reject_counts,
            "_prompt_pool_core_count": len(core_pool),
            "_prompt_pool_discovery_count": len(discovery_rows),
            "prompt_pool": [dict(row or {}) for row in result],
            "prompt_pool_count": len(result),
        }
    )
    metrics = dict(meta.get("metrics") or {})
    metrics["discovery_overlay"] = {
        "version": DISCOVERY_OVERLAY_VERSION,
        "max_slots": max_slots,
        "added": len(discovery_rows),
        "added_tickers": list(role_by_ticker.keys()),
        "reject_counts": reject_counts,
    }
    meta["metrics"] = metrics
    return result, meta
