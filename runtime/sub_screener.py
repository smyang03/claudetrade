from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.candidate_prompt_pool import build_trainer_prompt_pool
from runtime.candidate_quality_trainer import normalize_ticker
from runtime_paths import get_runtime_path


@dataclass
class SubScanResult:
    should_trigger: bool
    new_plan_a: list[dict[str, Any]]
    new_plan_b_high: list[dict[str, Any]]
    all_new_scored: list[dict[str, Any]]
    trigger_reason: str


def _market_key(market: str) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


def _ticker_key(ticker: Any, market: str) -> str:
    return normalize_ticker(ticker, _market_key(market))


def _score(row: dict[str, Any]) -> float:
    try:
        return float(row.get("trainer_prompt_score") or 0.0)
    except Exception:
        return 0.0


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _state_dir() -> Path:
    override = str(os.getenv("SUB_SCREENER_STATE_DIR", "") or "").strip()
    if override:
        path = Path(override).expanduser()
    else:
        path = get_runtime_path("state", make_parents=False)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(market: str, date: str) -> Path:
    safe_date = str(date or "").replace("/", "-").strip()
    return _state_dir() / f"sub_screener_{_market_key(market)}_{safe_date}.json"


def _empty_counter(market: str, date: str) -> dict[str, Any]:
    return {
        "market": _market_key(market),
        "date": str(date or ""),
        "scan_count": 0,
        "detection_count": 0,
        "attempt_count": 0,
        "success_count": 0,
        "last_scan_at": "",
        "last_attempt_at": "",
        "last_success_at": "",
        "last_detection": {},
        "last_attempt_fingerprint": "",
        "dedupe_suppressed_count": 0,
        "last_dedupe_suppressed": {},
        "triage_success_count": 0,
        "last_triage": {},
        "attempts": [],
    }


def _normalize_counter(payload: Any, market: str, date: str) -> dict[str, Any]:
    state = _empty_counter(market, date)
    if isinstance(payload, dict):
        state.update(payload)
    state["market"] = _market_key(market)
    state["date"] = str(date or "")
    for key in (
        "scan_count",
        "detection_count",
        "attempt_count",
        "success_count",
        "dedupe_suppressed_count",
        "triage_success_count",
    ):
        try:
            state[key] = max(0, int(state.get(key) or 0))
        except Exception:
            state[key] = 0
    if not isinstance(state.get("last_detection"), dict):
        state["last_detection"] = {}
    if not isinstance(state.get("attempts"), list):
        state["attempts"] = []
    return state


def _write_counter(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_session_counter(market: str, date: str) -> dict[str, Any]:
    path = _state_path(market, date)
    if not path.exists():
        return _empty_counter(market, date)
    try:
        return _normalize_counter(json.loads(path.read_text(encoding="utf-8")), market, date)
    except Exception:
        return _empty_counter(market, date)


def save_session_counter(market: str, date: str, state: dict[str, Any]) -> None:
    path = _state_path(market, date)
    _write_counter(path, _normalize_counter(state, market, date))


def is_rate_limited(
    market: str,
    date: str,
    *,
    max_per_session: int,
    min_interval_sec: float,
) -> bool:
    state = load_session_counter(market, date)
    try:
        max_per = int(max_per_session)
    except Exception:
        max_per = 0
    if max_per <= 0:
        return True
    if int(state.get("attempt_count") or 0) >= max_per:
        return True
    last_attempt = str(state.get("last_attempt_at") or "").strip()
    if last_attempt:
        try:
            elapsed = (datetime.now() - datetime.fromisoformat(last_attempt)).total_seconds()
            if elapsed < float(min_interval_sec or 0.0):
                return True
        except Exception:
            pass
    return False


def _new_tickers(result: SubScanResult) -> list[str]:
    tickers: list[str] = []
    for row in list(result.new_plan_a or []) + list(result.new_plan_b_high or []):
        ticker = str((row or {}).get("ticker") or "").strip()
        if ticker and ticker not in tickers:
            tickers.append(ticker)
    if tickers:
        return tickers
    for row in result.all_new_scored or []:
        ticker = str((row or {}).get("ticker") or "").strip()
        if ticker and ticker not in tickers:
            tickers.append(ticker)
    return tickers


def trigger_fingerprint(market: str, result: SubScanResult) -> str:
    market_key = _market_key(market)
    tickers = sorted(
        {
            _ticker_key(ticker, market_key)
            for ticker in _new_tickers(result)
            if _ticker_key(ticker, market_key)
        }
    )
    return "|".join(tickers)


def is_duplicate_trigger(
    market: str,
    date: str,
    result: SubScanResult,
    *,
    ttl_sec: float,
) -> bool:
    fingerprint = trigger_fingerprint(market, result)
    if not fingerprint:
        return False
    state = load_session_counter(market, date)
    # last_attempt_fingerprint(실제 Claude 호출 시) 또는 last_detected_fingerprint(rate-limit 시) 중 하나와 일치하면 dedupe
    last_fp = str(state.get("last_attempt_fingerprint") or "") or str(state.get("last_detected_fingerprint") or "")
    if last_fp != fingerprint:
        return False
    last_attempt = str(state.get("last_attempt_at") or "").strip()
    if not last_attempt:
        return False
    try:
        elapsed = (datetime.now() - datetime.fromisoformat(last_attempt)).total_seconds()
    except Exception:
        return False
    return elapsed < float(ttl_sec or 0.0)


def record_scan(market: str, date: str, result: SubScanResult) -> None:
    """Record every scan and only count detection when should_trigger is true.

    This function intentionally does not record reinvoke/rescreen attempts. Attempt
    accounting is owned by record_attempt() so shadow scans can be measured without
    consuming the session reinvoke limit.
    """
    state = load_session_counter(market, date)
    state["scan_count"] = int(state.get("scan_count") or 0) + 1
    state["last_scan_at"] = _now_iso()
    if bool(getattr(result, "should_trigger", False)):
        state["detection_count"] = int(state.get("detection_count") or 0) + 1
        state["last_detection"] = {
            "reason": str(getattr(result, "trigger_reason", "") or ""),
            "new_tickers": _new_tickers(result),
        }
        # 감지된 fingerprint를 기록해 rate-limited 스캔 이후에도 dedupe가 작동하게 함
        # record_attempt()가 호출되지 않아도 같은 set이 반복 감지되면 억제 가능
        detected_fp = trigger_fingerprint(market, result)
        if detected_fp:
            state["last_detected_fingerprint"] = detected_fp
    save_session_counter(market, date, state)


def record_attempt(market: str, date: str, result: SubScanResult) -> None:
    state = load_session_counter(market, date)
    now = _now_iso()
    fingerprint = trigger_fingerprint(market, result)
    attempt = {
        "at": now,
        "reason": str(getattr(result, "trigger_reason", "") or ""),
        "new_tickers": _new_tickers(result),
        "fingerprint": fingerprint,
        "success": False,
    }
    state["attempt_count"] = int(state.get("attempt_count") or 0) + 1
    state["last_attempt_at"] = now
    state["last_attempt_fingerprint"] = fingerprint
    attempts = list(state.get("attempts") or [])
    attempts.append(attempt)
    state["attempts"] = attempts[-50:]
    save_session_counter(market, date, state)


def record_dedupe_suppressed(
    market: str,
    date: str,
    result: SubScanResult,
    *,
    ttl_sec: float,
) -> None:
    state = load_session_counter(market, date)
    now = _now_iso()
    fingerprint = trigger_fingerprint(market, result)
    state["dedupe_suppressed_count"] = int(state.get("dedupe_suppressed_count") or 0) + 1
    state["last_dedupe_suppressed"] = {
        "at": now,
        "reason": str(getattr(result, "trigger_reason", "") or ""),
        "new_tickers": _new_tickers(result),
        "fingerprint": fingerprint,
        "ttl_sec": float(ttl_sec or 0.0),
        "last_attempt_at": str(state.get("last_attempt_at") or ""),
    }
    save_session_counter(market, date, state)


def record_success(market: str, date: str) -> None:
    state = load_session_counter(market, date)
    state["success_count"] = int(state.get("success_count") or 0) + 1
    state["last_success_at"] = _now_iso()
    attempts = list(state.get("attempts") or [])
    if attempts:
        attempts[-1] = {**dict(attempts[-1] or {}), "success": True}
        state["attempts"] = attempts
    save_session_counter(market, date, state)


def record_triage_success(
    market: str,
    date: str,
    result: SubScanResult,
    *,
    added_tickers: list[str],
    skipped_tickers: list[str] | None = None,
) -> None:
    state = load_session_counter(market, date)
    now = _now_iso()
    added = [str(ticker or "").strip() for ticker in list(added_tickers or []) if str(ticker or "").strip()]
    skipped = [str(ticker or "").strip() for ticker in list(skipped_tickers or []) if str(ticker or "").strip()]
    state["triage_success_count"] = int(state.get("triage_success_count") or 0) + 1
    state["success_count"] = int(state.get("success_count") or 0) + 1
    state["last_success_at"] = now
    state["last_triage"] = {
        "at": now,
        "reason": str(getattr(result, "trigger_reason", "") or ""),
        "new_tickers": _new_tickers(result),
        "added_tickers": added,
        "skipped_tickers": skipped,
        "fingerprint": trigger_fingerprint(market, result),
    }
    attempts = list(state.get("attempts") or [])
    if attempts:
        attempts[-1] = {
            **dict(attempts[-1] or {}),
            "success": True,
            "triage": True,
            "triage_added_tickers": added,
            "triage_skipped_tickers": skipped,
        }
        state["attempts"] = attempts
    save_session_counter(market, date, state)


def triage_candidates(result: SubScanResult, *, max_add: int = 5) -> list[dict[str, Any]]:
    limit = max(0, int(max_add or 0))
    if limit <= 0:
        return []
    seen: set[str] = set()
    pool: list[dict[str, Any]] = []
    for row in list(result.new_plan_a or []) + list(result.new_plan_b_high or []) + list(result.all_new_scored or []):
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        pool.append(dict(row))
    # score 내림차순 정렬 후 상위 limit개 반환 — PLAN_A/B 순서보다 실제 점수 기준
    pool.sort(key=lambda r: float(r.get("trainer_prompt_score") or r.get("candidate_quality_score") or 0), reverse=True)
    return pool[:limit]


def scan_new_candidates(
    market: str,
    current_exclude: set[str],
    screener_rows: list[dict[str, Any]],
    *,
    plan_a_threshold: int = 1,
    plan_a_min_score: float = 0.0,
    plan_b_threshold: int = 2,
    plan_b_min_score: float = 65.0,
) -> SubScanResult:
    market_key = _market_key(market)
    exclude_keys = {
        _ticker_key(ticker, market_key)
        for ticker in set(current_exclude or set())
        if _ticker_key(ticker, market_key)
    }
    pool = build_trainer_prompt_pool(list(screener_rows or []), market=market_key)
    scored_pool = list(pool.get("scored_pool") or pool.get("full_pool") or [])
    new_scored = [
        dict(row or {})
        for row in scored_pool
        if _ticker_key((row or {}).get("ticker"), market_key)
        and _ticker_key((row or {}).get("ticker"), market_key) not in exclude_keys
    ]
    new_plan_a = [
        row for row in new_scored
        if str(row.get("trainer_candidate_state") or "").upper() == "PLAN_A"
        and _score(row) >= float(plan_a_min_score)
    ]
    new_plan_b_high = [
        row for row in new_scored
        if str(row.get("trainer_candidate_state") or "").upper() == "PLAN_B"
        and _score(row) >= float(plan_b_min_score)
    ]
    plan_a_n = len(new_plan_a)
    plan_b_n = len(new_plan_b_high)
    plan_a_required = max(1, int(plan_a_threshold or 1))
    plan_b_required = max(1, int(plan_b_threshold or 1))
    if plan_a_n >= plan_a_required:
        should_trigger = True
        reason = f"new_plan_a:{plan_a_n}"
    elif plan_b_n >= plan_b_required:
        should_trigger = True
        reason = f"new_plan_b_high:{plan_b_n}"
    else:
        should_trigger = False
        reason = "no_trigger"
    return SubScanResult(
        should_trigger=should_trigger,
        new_plan_a=new_plan_a,
        new_plan_b_high=new_plan_b_high,
        all_new_scored=new_scored,
        trigger_reason=reason,
    )
