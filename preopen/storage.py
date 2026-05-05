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


_PREOPEN_PIN_SAFE_FIELDS = {
    "ticker",
    "name",
    "market",
    "session_date",
    "source",
    "provider",
    "detected_at",
    "captured_at",
    "first_detected_at",
    "last_detected_at",
    "preopen_score",
    "shadow_preopen_rank",
    "preopen_grade",
    "source_overlap_count",
    "data_quality",
    "stale",
    "risk_tags",
    "quality_tags",
    "pattern_tags",
    "preopen_reason",
    "provider_rank",
    "screen_score",
    "price",
    "volume",
    "change_rate",
    "gap_pct",
    "volume_ratio",
    "extended_price",
    "regular_prev_close",
    "extended_change_pct",
    "extended_volume",
    "extended_dollar_volume",
    "prior_day_traded_value",
    "bid",
    "ask",
    "spread_pct",
    "quote_timestamp",
    "news_or_earnings_flag",
    "open_volume_confirmation",
    "display_enrichment_source",
    "anchor_price",
    "anchor_price_source",
    "anchor_price_at",
    "market_type",
    "category",
    "sector",
    "from_high_pct",
    "above_ma60",
    "liquidity_bucket",
    "from_high_bucket",
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _market_env_float(market: str, suffix: str, default: float) -> float:
    market_key = _market_key(market)
    market_name = f"{market_key}_{suffix}"
    if os.getenv(market_name) is not None:
        return _env_float(market_name, default)
    return _env_float(suffix, default)


def _market_env_bool(market: str, suffix: str, default: bool = False) -> bool:
    market_key = _market_key(market)
    market_name = f"{market_key}_{suffix}"
    if os.getenv(market_name) is not None:
        return _env_bool(market_name, default)
    return _env_bool(suffix, default)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except Exception:
        return None


def _preopen_pin_turnover(row: dict[str, Any]) -> float:
    for key in ("extended_dollar_volume", "dollar_volume", "turnover", "prior_day_traded_value"):
        value = _positive_number(row.get(key))
        if value is not None:
            return float(value)
    price = (
        _positive_number(row.get("price"))
        or _positive_number(row.get("extended_price"))
        or _positive_number(row.get("anchor_price"))
        or 0.0
    )
    volume = _positive_number(row.get("volume")) or _positive_number(row.get("extended_volume")) or 0.0
    return float(price) * float(volume)


def _preopen_pin_seed_only(row: dict[str, Any], state: dict[str, Any]) -> bool:
    values = [
        row.get("provider"),
        row.get("data_quality"),
        row.get("source"),
        state.get("provider"),
        state.get("data_quality"),
        state.get("source_status"),
    ]
    for value in values:
        text = str(value or "").strip().lower()
        if text in {"seed_only", "seed_watchlist"} or "seed_only" in text:
            return True
    for value in list(row.get("quality_tags") or []) + list(state.get("quality_tags") or []):
        text = str(value or "").strip().lower()
        if text in {"seed_only", "seed_watchlist"} or "seed_only" in text:
            return True
    return False


def load_preopen_pin_candidates(
    market: str,
    *,
    session_date: str | None = None,
    mode: str = "live",
    max_age_min: int | None = None,
    min_score: float | None = None,
    max_rank: int | None = None,
    max_count: int | None = None,
    include_soft: bool = False,
    soft_max_rank: int | None = None,
    min_dollar_volume: float | None = None,
) -> list[dict[str, Any]]:
    market_key = _market_key(market)
    max_age = max_age_min if max_age_min is not None else _env_int("PREOPEN_PIN_STATE_MAX_AGE_MIN", 120)
    score_threshold = min_score if min_score is not None else _env_float("PREOPEN_PIN_MIN_SCORE", 0.50)
    rank_threshold = max_rank if max_rank is not None else _env_int("PREOPEN_PIN_MAX_RANK", 3)
    soft_rank_threshold = soft_max_rank if soft_max_rank is not None else _env_int("PREOPEN_PIN_SOFT_MAX_RANK", 5)
    limit = max_count if max_count is not None else _env_int("PREOPEN_PIN_MAX_COUNT", 5)
    soft_limit = _env_int("PREOPEN_PIN_SOFT_MAX_COUNT", 5)
    default_min_turnover = 50_000_000.0 if market_key == "US" else 1_000_000_000.0
    min_turnover = (
        float(min_dollar_volume)
        if min_dollar_volume is not None
        else _market_env_float(market_key, "PREOPEN_PIN_MIN_DOLLAR_VOLUME", default_min_turnover)
    )
    allow_seed_only = _market_env_bool(market_key, "PREOPEN_PIN_ALLOW_SEED_ONLY", False)
    if limit <= 0:
        return []
    state = load_preopen_state(
        market_key,
        session_date=session_date,
        max_age_min=max_age,
        mode=mode,
    )
    candidates = list((state or {}).get("candidates") or [])
    if not candidates:
        return []

    captured_at = str((state or {}).get("captured_at", "") or "")
    hard_selected: list[tuple[int, float, str, dict[str, Any]]] = []
    soft_selected: list[tuple[int, float, str, dict[str, Any]]] = []
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        ticker = _candidate_key(market_key, raw.get("ticker"))
        if not ticker:
            continue
        score = _number_or_none(raw.get("preopen_score"))
        rank = _positive_int(raw.get("shadow_preopen_rank"))
        score_ok = score is not None and score >= float(score_threshold)
        rank_ok = rank is not None and rank <= int(rank_threshold)
        soft_rank_ok = rank is not None and rank <= int(soft_rank_threshold)
        if not soft_rank_ok:
            continue
        turnover = _preopen_pin_turnover(raw)
        turnover_ok = min_turnover <= 0 or turnover >= float(min_turnover)
        seed_only = _preopen_pin_seed_only(raw, state or {})
        seed_ok = allow_seed_only or not seed_only
        hard_ok = bool(rank_ok and score_ok and turnover_ok and seed_ok)
        row = {
            key: value
            for key, value in raw.items()
            if key in _PREOPEN_PIN_SAFE_FIELDS
        }
        row["ticker"] = ticker
        row.setdefault("market", market_key)
        row.setdefault("session_date", str((state or {}).get("session_date") or session_date or ""))
        row["preopen_pinned"] = hard_ok
        row["preopen_pin_tier"] = "HARD" if hard_ok else "SOFT"
        row["preopen_pin_require_confirmation"] = bool(hard_ok)
        reasons: list[str] = []
        if rank_ok:
            reasons.append(f"rank<={int(rank_threshold)}")
        elif soft_rank_ok:
            reasons.append(f"soft_rank<={int(soft_rank_threshold)}")
        if score_ok:
            reasons.append(f"score>={float(score_threshold):.2f}")
        if turnover_ok:
            reasons.append(f"turnover>={float(min_turnover):.0f}")
        if seed_ok:
            reasons.append("not_seed_only")
        row["preopen_pin_reason"] = ",".join(reasons) or "preopen_pin"
        row["preopen_anchor_price"] = (
            _positive_number(raw.get("anchor_price"))
            or _positive_number(raw.get("price"))
            or _positive_number(raw.get("extended_price"))
        )
        row["preopen_captured_at"] = str(raw.get("captured_at") or captured_at)
        row["preopen_state_age_min"] = (state or {}).get("state_age_min")
        row["preopen_pin_turnover"] = round(float(turnover), 2)
        rejected: list[str] = []
        if not rank_ok:
            rejected.append(f"rank>{int(rank_threshold)}")
        if not score_ok:
            rejected.append(f"score<{float(score_threshold):.2f}")
        if not turnover_ok:
            rejected.append(f"turnover<{float(min_turnover):.0f}")
        if not seed_ok:
            rejected.append("seed_only")
        if rejected:
            row["preopen_pin_rejected_reason"] = ",".join(rejected)
        sortable = (rank or 1_000_000, -(float(score or 0.0)), ticker, row)
        if hard_ok:
            hard_selected.append(sortable)
        elif include_soft:
            soft_selected.append(sortable)

    hard_selected.sort(key=lambda item: (item[0], item[1], item[2]))
    soft_selected.sort(key=lambda item: (item[0], item[1], item[2]))
    rows = [row for _, _, _, row in hard_selected[:limit]]
    if include_soft and soft_limit > 0:
        rows.extend(row for _, _, _, row in soft_selected[:soft_limit])
    return rows


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


def _screen_cache_path(market: str) -> Path:
    market_key = _market_key(market)
    filename = "us_screen_cache.json" if market_key == "US" else "kr_screen_cache.json"
    return get_runtime_path("state", filename)


def _candidate_key(market: str, ticker: Any) -> str:
    raw = str(ticker or "").strip()
    return raw.upper() if _market_key(market) == "US" else raw


def _positive_number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        return parsed if parsed > 0 else None
    except Exception:
        return None


def _number_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _load_screen_cache_map(market: str) -> dict[str, dict[str, Any]]:
    path = _screen_cache_path(market)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    rows = payload.get("candidates") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return {}
    market_key = _market_key(market)
    cache: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _candidate_key(market_key, row.get("ticker"))
        if key:
            cache[key] = dict(row)
    return cache


def _screen_cache_candidates_for_display(
    market: str,
    session_date: str,
    *,
    captured_at: str = "",
) -> list[dict[str, Any]]:
    cache = _load_screen_cache_map(market)
    if not cache:
        return []
    market_key = _market_key(market)
    captured = captured_at or datetime.now(KST).isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    for idx, cached in enumerate(cache.values(), start=1):
        price = _positive_number(cached.get("price"))
        volume = _positive_number(cached.get("volume"))
        change = _number_or_none(cached.get("change_rate"))
        dollar_volume = _positive_number(cached.get("dollar_volume"))
        if dollar_volume is None and price is not None and volume is not None:
            dollar_volume = price * volume
        row = {
            "ticker": cached.get("ticker", ""),
            "name": cached.get("name", cached.get("ticker", "")),
            "source": str(cached.get("category") or "screen_cache"),
            "provider": "screen_cache",
            "provider_rank": idx,
            "source_status": "screen_cache_display_fallback",
            "data_quality": "screen_cache_display",
            "stale": False,
            "quality_tags": ["screen_cache_display", str(cached.get("category", "") or "").lower()],
            "risk_tags": [],
            "price": price,
            "extended_price": price,
            "change_rate": change,
            "gap_pct": change,
            "extended_change_pct": change,
            "volume": volume,
            "extended_volume": volume,
            "extended_dollar_volume": dollar_volume,
            "volume_ratio": cached.get("vol_ratio"),
            "display_enrichment_source": "screen_cache",
            "anchor_price": price,
            "anchor_price_source": "screen_cache_price",
            "anchor_price_at": captured,
        }
        if market_key == "KR":
            row["prior_day_traded_value"] = dollar_volume
            row["open_volume_confirmation"] = volume
        rows.append(row)
    try:
        from preopen.models import normalize_candidate
        from preopen.scorer import score_candidates

        normalized = [
            normalize_candidate(row, market=market_key, session_date=session_date, captured_at=captured)
            for row in rows
        ]
        return score_candidates(market_key, normalized)
    except Exception:
        return rows


def _needs_display_enrichment(candidate: dict[str, Any]) -> bool:
    if _positive_number(candidate.get("price")) is None:
        return True
    if _number_or_none(candidate.get("extended_change_pct")) is None and _number_or_none(candidate.get("gap_pct")) is None:
        return True
    return _positive_number(candidate.get("extended_dollar_volume")) is None


def _all_candidates_lack_display_values(candidates: list[dict[str, Any]]) -> bool:
    if not candidates:
        return False
    return all(_needs_display_enrichment(dict(candidate or {})) for candidate in candidates)


def _enrich_candidates_from_screen_cache(market: str, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    cache = _load_screen_cache_map(market)
    if not cache:
        return candidates, 0
    enriched: list[dict[str, Any]] = []
    count = 0
    for candidate in candidates:
        row = dict(candidate or {})
        if not _needs_display_enrichment(row):
            enriched.append(row)
            continue
        cached = cache.get(_candidate_key(market, row.get("ticker")))
        if not cached:
            enriched.append(row)
            continue
        price = _positive_number(cached.get("price"))
        volume = _positive_number(cached.get("volume"))
        change = _number_or_none(cached.get("change_rate"))
        dollar_volume = _positive_number(cached.get("dollar_volume"))
        if dollar_volume is None and price is not None and volume is not None:
            dollar_volume = price * volume
        if row.get("name") in ("", None, row.get("ticker")) and cached.get("name"):
            row["name"] = cached.get("name")
        if row.get("price") is None and price is not None:
            row["price"] = price
        if row.get("extended_price") is None and price is not None:
            row["extended_price"] = price
        if row.get("change_rate") is None and change is not None:
            row["change_rate"] = change
        if row.get("gap_pct") is None and change is not None:
            row["gap_pct"] = change
        if row.get("extended_change_pct") is None and change is not None:
            row["extended_change_pct"] = change
        if row.get("volume") is None and volume is not None:
            row["volume"] = volume
        if row.get("extended_volume") is None and volume is not None:
            row["extended_volume"] = volume
        if row.get("extended_dollar_volume") is None and dollar_volume is not None:
            row["extended_dollar_volume"] = dollar_volume
        if row.get("volume_ratio") is None and cached.get("vol_ratio") is not None:
            row["volume_ratio"] = cached.get("vol_ratio")
        quality_tags = list(row.get("quality_tags") or [])
        if "screen_cache_display_enriched" not in quality_tags:
            quality_tags.append("screen_cache_display_enriched")
        row["quality_tags"] = quality_tags
        row["display_enrichment_source"] = "screen_cache"
        count += 1
        enriched.append(row)
    return enriched, count


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


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except Exception:
        return None


def _dynamic_return_offsets(row: dict[str, Any]) -> list[int]:
    offsets: list[int] = []
    prefix = "post_open_"
    suffix = "m_return_pct"
    for key in row.keys():
        if not isinstance(key, str) or not key.startswith(prefix) or not key.endswith(suffix):
            continue
        raw = key[len(prefix):-len(suffix)]
        if raw.isdigit():
            offsets.append(int(raw))
    return offsets


def _scheduled_outcome_offsets(market: str, session_date: str) -> list[int]:
    try:
        from preopen.scheduler import default_outcome_offsets_min

        return list(default_outcome_offsets_min(_market_key(market), session_date))
    except Exception:
        return [5, 30, 60, 90, 120]


def _outcome_offsets_for_display(
    market: str,
    session_date: str,
    candidates: list[dict[str, Any]],
    outcome: list[dict[str, Any]],
) -> list[int]:
    offsets = set(_scheduled_outcome_offsets(market, session_date))
    for row in list(candidates or []) + list(outcome or []):
        if not isinstance(row, dict):
            continue
        offset = _int_or_none(row.get("offset_min"))
        if offset is not None and offset > 0:
            offsets.add(offset)
        offsets.update(_dynamic_return_offsets(row))
        samples = row.get("outcome_samples")
        if isinstance(samples, list):
            for sample in samples:
                if isinstance(sample, dict):
                    sample_offset = _int_or_none(sample.get("offset_min"))
                    if sample_offset is not None and sample_offset > 0:
                        offsets.add(sample_offset)
    return sorted(offsets)


def _candidate_anchor(candidate: dict[str, Any]) -> tuple[float | None, str, str]:
    for key in ("anchor_price", "initial_candidate_price", "price", "extended_price", "regular_open_price"):
        value = _positive_number(candidate.get(key))
        if value is not None:
            return value, str(candidate.get("anchor_price_source") or key), str(
                candidate.get("anchor_price_at")
                or candidate.get("first_detected_at")
                or candidate.get("captured_at")
                or ""
            )
    return None, "", ""


def _return_pct_from_base(current: Any, base: Any) -> float | None:
    current_f = _number_or_none(current)
    base_f = _positive_number(base)
    if current_f is None or base_f is None:
        return None
    return round(((current_f - base_f) / base_f) * 100.0, 4)


def _display_ticker(candidate: dict[str, Any]) -> str:
    ticker = str(candidate.get("ticker") or "").strip()
    name = str(candidate.get("name") or "").strip()
    if ticker and name and name.upper() != ticker.upper():
        return f"{name} ({ticker})"
    return ticker or name or "-"


def _sample_from_row(row: dict[str, Any], offset: int | None = None, anchor_price: Any = None) -> dict[str, Any] | None:
    row_offset = _int_or_none(row.get("offset_min"))
    sample_offset = int(offset if offset is not None else row_offset or 0)
    if sample_offset <= 0:
        return None
    dynamic_key = f"post_open_{sample_offset}m_return_pct"
    return_pct = _number_or_none(row.get(dynamic_key))
    if return_pct is None and row_offset == sample_offset:
        return_pct = _number_or_none(row.get("post_open_return_pct"))
    price = _positive_number(row.get(f"outcome_{sample_offset}m_price"))
    if price is None and row_offset == sample_offset:
        price = _positive_number(row.get("price"))
    return_basis = str(row.get("return_basis") or "")
    anchor_return = _return_pct_from_base(price, anchor_price)
    if anchor_return is not None and return_basis != "anchor_price":
        return_pct = anchor_return
        return_basis = "anchor_price_recomputed"
    high_value = row.get("high")
    low_value = row.get("low")
    high_return = _return_pct_from_base(high_value, anchor_price) if return_basis == "anchor_price_recomputed" else None
    low_return = _return_pct_from_base(low_value, anchor_price) if return_basis == "anchor_price_recomputed" else None
    sample = {
        "offset_min": sample_offset,
        "captured_at": row.get(f"outcome_{sample_offset}m_captured_at") or row.get("captured_at") or row.get("ts") or "",
        "price": price,
        "return_pct": return_pct,
        "status": row.get("outcome_status", ""),
        "high_return_pct": high_return if high_return is not None else _number_or_none(row.get("open_to_high_pct") or row.get("high_return_pct")),
        "low_return_pct": low_return if low_return is not None else _number_or_none(row.get("max_drawdown_pct") or row.get("low_return_pct")),
        "price_source": row.get("price_source", ""),
        "return_basis": return_basis,
    }
    if return_pct is None and price is None and not sample["status"]:
        return None
    return sample


def _merge_timeline_sample(samples_by_offset: dict[int, dict[str, Any]], sample: dict[str, Any] | None) -> None:
    if not sample:
        return
    offset = _int_or_none(sample.get("offset_min"))
    if offset is None or offset <= 0:
        return
    existing = samples_by_offset.get(offset)
    if existing and existing.get("return_pct") is not None and sample.get("return_pct") is None:
        return
    samples_by_offset[offset] = dict(sample)


def _build_outcome_timeline(
    market: str,
    candidates: list[dict[str, Any]],
    outcome: list[dict[str, Any]],
    offsets: list[int],
) -> list[dict[str, Any]]:
    rows_by_key: dict[str, dict[str, Any]] = {}
    for idx, candidate in enumerate(candidates or [], start=1):
        if not isinstance(candidate, dict):
            continue
        ticker = candidate.get("ticker", "")
        key = _candidate_key(market, ticker)
        if not key:
            continue
        anchor, anchor_source, anchor_at = _candidate_anchor(candidate)
        rows_by_key[key] = {
            "ticker": str(ticker or ""),
            "name": str(candidate.get("name") or ""),
            "display_ticker": _display_ticker(candidate),
            "shadow_preopen_rank": candidate.get("shadow_preopen_rank") or idx,
            "preopen_score": candidate.get("preopen_score"),
            "preopen_grade": candidate.get("preopen_grade", ""),
            "anchor_price": anchor,
            "anchor_price_source": anchor_source,
            "anchor_price_at": anchor_at,
            "regular_open_price": candidate.get("regular_open_price"),
            "last_price": candidate.get("last_price"),
            "last_price_at": candidate.get("last_price_at", ""),
            "samples_by_offset": {},
        }
        samples = candidate.get("outcome_samples")
        if isinstance(samples, list):
            for sample in samples:
                if isinstance(sample, dict):
                    _merge_timeline_sample(rows_by_key[key]["samples_by_offset"], sample)
        for dynamic_offset in _dynamic_return_offsets(candidate):
            _merge_timeline_sample(rows_by_key[key]["samples_by_offset"], _sample_from_row(candidate, dynamic_offset, anchor))

    for outcome_row in outcome or []:
        if not isinstance(outcome_row, dict):
            continue
        key = _candidate_key(market, outcome_row.get("ticker"))
        if not key:
            continue
        row = rows_by_key.setdefault(key, {
            "ticker": str(outcome_row.get("ticker") or ""),
            "name": str(outcome_row.get("name") or ""),
            "display_ticker": _display_ticker(outcome_row),
            "shadow_preopen_rank": None,
            "preopen_score": None,
            "preopen_grade": "",
            "anchor_price": _positive_number(outcome_row.get("anchor_price")),
            "anchor_price_source": str(outcome_row.get("anchor_price_source") or ""),
            "anchor_price_at": str(outcome_row.get("anchor_price_at") or ""),
            "regular_open_price": outcome_row.get("regular_open_price"),
            "last_price": outcome_row.get("price"),
            "last_price_at": outcome_row.get("captured_at", ""),
            "samples_by_offset": {},
        })
        if not row.get("name") and outcome_row.get("name"):
            row["name"] = str(outcome_row.get("name") or "")
            row["display_ticker"] = _display_ticker(row)
        if row.get("anchor_price") is None and _positive_number(outcome_row.get("anchor_price")) is not None:
            row["anchor_price"] = _positive_number(outcome_row.get("anchor_price"))
            row["anchor_price_source"] = str(outcome_row.get("anchor_price_source") or "")
            row["anchor_price_at"] = str(outcome_row.get("anchor_price_at") or "")
        if outcome_row.get("price") is not None:
            row["last_price"] = outcome_row.get("price")
            row["last_price_at"] = outcome_row.get("captured_at", "")
        samples = outcome_row.get("outcome_samples")
        if isinstance(samples, list):
            for sample in samples:
                if isinstance(sample, dict):
                    _merge_timeline_sample(row["samples_by_offset"], sample)
        row_offset = _int_or_none(outcome_row.get("offset_min"))
        if row_offset is not None:
            _merge_timeline_sample(row["samples_by_offset"], _sample_from_row(outcome_row, row_offset, row.get("anchor_price")))
        for dynamic_offset in _dynamic_return_offsets(outcome_row):
            _merge_timeline_sample(row["samples_by_offset"], _sample_from_row(outcome_row, dynamic_offset, row.get("anchor_price")))

    timeline: list[dict[str, Any]] = []
    for row in rows_by_key.values():
        sample_map = row.get("samples_by_offset") or {}
        returns_by_offset: dict[str, float | None] = {}
        prices_by_offset: dict[str, float | None] = {}
        statuses_by_offset: dict[str, str] = {}
        sample_list: list[dict[str, Any]] = []
        for offset in offsets:
            sample = sample_map.get(int(offset), {})
            returns_by_offset[str(offset)] = sample.get("return_pct") if sample else None
            prices_by_offset[str(offset)] = sample.get("price") if sample else None
            statuses_by_offset[str(offset)] = str(sample.get("status") or "") if sample else ""
            if sample:
                sample_list.append(sample)
        sampled_returns = [float(sample.get("return_pct")) for sample in sample_list if sample.get("return_pct") is not None]
        row["returns_by_offset"] = returns_by_offset
        row["prices_by_offset"] = prices_by_offset
        row["statuses_by_offset"] = statuses_by_offset
        row["samples"] = sample_list
        row["sampled_count"] = len(sampled_returns)
        row["missing_count"] = max(0, len(offsets) - len(sampled_returns))
        row["latest_return_pct"] = sampled_returns[-1] if sampled_returns else None
        row["best_return_pct"] = max(sampled_returns) if sampled_returns else None
        row["worst_return_pct"] = min(sampled_returns) if sampled_returns else None
        row.pop("samples_by_offset", None)
        timeline.append(row)
    timeline.sort(key=lambda row: (
        int(row.get("shadow_preopen_rank") or 999999),
        str(row.get("ticker") or ""),
    ))
    return timeline


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
    by_offset: dict[int, list[float]] = {}
    for row in outcome:
        try:
            if row.get("post_open_30m_return_pct") is not None:
                outcome_30m.append(float(row.get("post_open_30m_return_pct")))
            if row.get("post_open_60m_return_pct") is not None:
                outcome_60m.append(float(row.get("post_open_60m_return_pct")))
            offset = _int_or_none(row.get("offset_min"))
            value = _number_or_none(row.get("post_open_return_pct"))
            if offset is not None and value is not None:
                by_offset.setdefault(offset, []).append(value)
            for dynamic_offset in _dynamic_return_offsets(row):
                dynamic_value = _number_or_none(row.get(f"post_open_{dynamic_offset}m_return_pct"))
                if dynamic_value is not None:
                    by_offset.setdefault(dynamic_offset, []).append(dynamic_value)
        except Exception:
            continue
    return {
        "rank_diff_rows": len(rank_diff),
        "outcome_rows": len(outcome),
        "top3_selected": sum(1 for row in top3 if row.get("actual_selected")),
        "top3_trade_ready": sum(1 for row in top3 if row.get("actual_trade_ready")),
        "avg_30m_return_pct": _avg(outcome_30m),
        "avg_60m_return_pct": _avg(outcome_60m),
        "avg_return_by_offset": {str(offset): _avg(values) for offset, values in sorted(by_offset.items())},
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


def _outcome_storage_counts(outcome: list[dict[str, Any]]) -> dict[str, int]:
    sampled = 0
    missing = 0
    errors = 0
    for row in outcome or []:
        status = str(row.get("outcome_status") or "")
        value = _number_or_none(row.get("post_open_return_pct"))
        if value is not None:
            sampled += 1
        elif status == "price_provider_error":
            errors += 1
        else:
            missing += 1
    return {"sampled": sampled, "missing": missing, "errors": errors, "total": len(outcome or [])}


def _operator_status(
    *,
    session_date: str,
    candidate_source: str,
    state: dict[str, Any],
    outcome: list[dict[str, Any]],
    offsets: list[int],
    display_enriched_count: int,
    screen_cache_fallback_count: int,
) -> str:
    if candidate_source == "screen_cache_fallback" or state.get("outcome_source_candidates") == "screen_cache_fallback":
        candidate_label = "후보: 화면 캐시 복구"
    elif candidate_source == "candidate_log":
        candidate_label = "후보: 후보 로그"
    elif str(state.get("provider") or "") == "seed_watchlist":
        candidate_label = "후보: 기본 관심목록"
    elif state:
        candidate_label = "후보: 장전 수집"
    else:
        candidate_label = "후보: 없음"

    if display_enriched_count:
        candidate_label += f" + 값 보강 {display_enriched_count}건"
    if screen_cache_fallback_count:
        candidate_label += f" {screen_cache_fallback_count}건"

    counts = _outcome_storage_counts(outcome)
    result_label = f"결과: 저장 {counts['sampled']}건"
    if counts["missing"]:
        result_label += f", 미수집 {counts['missing']}건"
    if counts["errors"]:
        result_label += f", 오류 {counts['errors']}건"
    return f"세션 {session_date} · {candidate_label} · {result_label} · 표시 시간 {len(offsets)}개"


def _scheduler_guidance(market: str, mode: str = "live", session_date: str | None = None) -> dict[str, Any]:
    market_key = _market_key(market)
    runtime_mode = _runtime_mode(mode)
    automatic_command = f"python tools/preopen_scheduler.py --mode {runtime_mode} --markets KR,US --loop"
    offsets = _scheduled_outcome_offsets(market_key, session_date or resolve_session_date_str(market_key))
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
    outcome_limit = max(1000, int(limit) * 30)
    outcome = read_jsonl_tail(log_path("outcome", market_key, session_date, mode=runtime_mode), outcome_limit)
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
    screen_cache_fallback_count = 0
    if (
        all_candidates
        and isinstance(state, dict)
        and str(state.get("data_quality", "") or "").lower().startswith("seed_only")
        and _all_candidates_lack_display_values(all_candidates)
    ):
        fallback_candidates = _screen_cache_candidates_for_display(
            market_key,
            session_date,
            captured_at=str(state.get("captured_at", "") or ""),
        )
        if fallback_candidates:
            all_candidates = fallback_candidates
            candidate_source = "screen_cache_fallback"
            screen_cache_fallback_count = len(fallback_candidates)
    all_candidates, display_enriched_count = _enrich_candidates_from_screen_cache(market_key, all_candidates)
    candidates = all_candidates[:limit]
    candidate_log_count = _line_count(log_path("candidates", market_key, session_date, mode=runtime_mode))
    candidate_total_count = max(
        int(state.get("candidate_count", 0) or 0) if isinstance(state, dict) else 0,
        len(all_candidates),
        candidate_log_count,
    )
    empty_reason = _empty_reason(state, candidates, rank_diff, outcome)
    display_provider = state.get("provider", "") if state else ""
    display_source_status = state.get("source_status", "") if state else ""
    display_data_quality = state.get("data_quality", "") if state else ""
    if candidate_source == "screen_cache_fallback":
        display_provider = "screen_cache"
        display_source_status = "screen_cache_display_fallback"
        display_data_quality = "screen_cache_display"
    outcome_offsets = _outcome_offsets_for_display(market_key, session_date, all_candidates, outcome)
    outcome_timeline = _build_outcome_timeline(market_key, candidates, outcome, outcome_offsets)
    scheduler_guidance = _scheduler_guidance(market_key, mode=runtime_mode, session_date=session_date)
    operator_status = _operator_status(
        session_date=session_date,
        candidate_source=candidate_source or "none",
        state=state,
        outcome=outcome,
        offsets=outcome_offsets,
        display_enriched_count=display_enriched_count,
        screen_cache_fallback_count=screen_cache_fallback_count,
    )
    raw_status = (
        f"provider={display_provider or '-'} token={state.get('token_status', '') if state else '-'} "
        f"source={display_source_status or '-'} data={display_data_quality or '-'} "
        f"raw_provider={state.get('provider', '') if state else '-'} "
        f"raw_source={state.get('source_status', '') if state else '-'} "
        f"raw_data={state.get('data_quality', '') if state else '-'}"
    )
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
            "display_enriched_count": display_enriched_count,
            "display_enrichment_source": "screen_cache" if display_enriched_count else "",
            "screen_cache_fallback_count": screen_cache_fallback_count,
            "rank_diff_count": len(rank_diff),
            "outcome_count": len(outcome),
            "token_status": state.get("token_status", "") if state else "",
            "source_status": display_source_status,
            "provider": display_provider,
            "data_quality": display_data_quality,
            "raw_source_status": state.get("source_status", "") if state else "",
            "raw_provider": state.get("provider", "") if state else "",
            "raw_data_quality": state.get("data_quality", "") if state else "",
            "operator_status": operator_status,
            "raw_status": raw_status,
            "empty_reason": empty_reason,
            "has_data": bool(candidates or rank_diff or outcome),
        },
        "candidates": candidates,
        "rank_diff": rank_diff,
        "outcome": outcome,
        "outcome_offsets_min": outcome_offsets,
        "outcome_timeline": outcome_timeline,
        "performance_summary": _performance_summary(rank_diff, outcome),
        "scheduler": load_preopen_scheduler_dashboard(mode=runtime_mode, limit=20),
        "recent_sessions": list_preopen_sessions(market_key, limit=20, mode=runtime_mode),
        "next_actions": _next_actions(market_key, empty_reason, mode=runtime_mode),
        "scheduler_guidance": scheduler_guidance,
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
