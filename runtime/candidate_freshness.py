"""후보 신선도 원장 — 풀 체류 나이 기반 좀비 강등 (2026-06-11 운영자 승인).

근거 (US live 4/1~6/10 역산, 청산 플랜 133건):
- 체류 0~2세션 플랜 +0.81% / 3~5세션 +1.42% (황금기) / 6세션+ -0.71%, 승률 36%
- 체결됐던 플랜 직후(3세션 내) 재탕: 실패후 -0.73%(승률 16%), 승리후 -0.41%
  — 미체결(만료/취소) 후 재시도는 +0.10%로 무해하므로 벌하지 않는다
- 재발견 인프라(30분 rescreen·sub_screener·rvol) 덕에 강등 후 복귀 비용이 낮다

설계 원칙: 제거가 아닌 정렬 강등(가역), 면제(보유·활성 플랜·rvol 급증),
강등 종목 forward는 라벨 자동화가 채점 → 임계 자기 보정.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from runtime_paths import get_runtime_path

log = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SEC = 900.0

# 활성(보유/대기) 플랜 상태 — 해당 종목은 면제
_ACTIVE_PLAN_STATUSES = {
    "CLAUDE_PRICE_WAITING", "WAITING", "ORDER_SENT", "ORDER_ACKED",
    "FILLED", "SELL_SENT", "SELL_ACKED", "SELL_PARTIAL_FILLED",
}
# 체결이 일어났던 플랜(재탕 디스카운트 대상) — 미체결 종결(EXPIRED/CANCELLED)은 제외
_TRADED_PLAN_STATUSES = {"CLOSED"}


def _env_int(key: str, default: int) -> int:
    try:
        return int(float(os.getenv(key, "") or default))
    except Exception:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, "") or default)
    except Exception:
        return default


def freshness_enabled() -> bool:
    return str(os.getenv("CANDIDATE_FRESHNESS_ENABLED", "true") or "").strip().lower() in {"1", "true", "yes", "on"}


def _ticker_key(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


def get_freshness_map(market: str, *, force_refresh: bool = False) -> dict[str, dict[str, Any]]:
    """시장별 {ticker: {age, grade, never_planned, retrade, exempt_active}} (15분 캐시)."""
    market_key = "US" if str(market or "").upper() == "US" else "KR"
    now = time.time()
    cached = _CACHE.get(market_key)
    if cached and not force_refresh and (now - cached[0]) < _CACHE_TTL_SEC:
        return cached[1]
    try:
        result = _compute_freshness_map(market_key)
    except Exception as exc:
        log.warning(f"[candidate freshness] {market_key} 계산 실패 — 패널티 미적용: {exc}")
        result = {}
    _CACHE[market_key] = (now, result)
    return result


def _compute_freshness_map(market_key: str) -> dict[str, dict[str, Any]]:
    lookback = _env_int("CANDIDATE_FRESHNESS_LOOKBACK_SESSIONS", 12)
    audit_path = get_runtime_path("data", "audit", "candidate_audit.db")
    event_path = get_runtime_path("data", "v2_event_store.db")
    if not Path(audit_path).exists() or not Path(event_path).exists():
        return {}

    hist: dict[str, set] = defaultdict(set)
    con = sqlite3.connect(f"file:{audit_path}?mode=ro", uri=True, timeout=5)
    try:
        sessions = sorted(
            r[0] for r in con.execute(
                "SELECT DISTINCT session_date FROM audit_candidate_rows "
                "WHERE market=? ORDER BY session_date DESC LIMIT ?",
                (market_key, lookback),
            )
        )
        if not sessions:
            return {}
        for sd, t in con.execute(
            "SELECT session_date, ticker FROM audit_candidate_rows "
            "WHERE market=? AND final_prompt_included=1 AND session_date >= ?",
            (market_key, sessions[0]),
        ):
            key = _ticker_key(market_key, t)
            if key:
                hist[key].add(str(sd)[:10])
    finally:
        con.close()

    plans: dict[str, list[tuple[str, str]]] = defaultdict(list)
    con = sqlite3.connect(f"file:{event_path}?mode=ro", uri=True, timeout=5)
    try:
        for t, sd, st in con.execute(
            "SELECT ticker, session_date, status FROM v2_path_runs "
            "WHERE market=? AND runtime_mode='live' AND session_date >= ?",
            (market_key, sessions[0]),
        ):
            key = _ticker_key(market_key, t)
            if key:
                plans[key].append((str(sd)[:10], str(st or "")))
    finally:
        con.close()

    recent3 = set(sessions[-3:])
    result: dict[str, dict[str, Any]] = {}
    for ticker, seen in hist.items():
        age = 0
        for sd in reversed(sessions):
            if sd in seen:
                age += 1
            else:
                break
        ticker_plans = plans.get(ticker, [])
        never_planned = not ticker_plans
        exempt_active = any(st in _ACTIVE_PLAN_STATUSES for _, st in ticker_plans)
        retrade = any(sd in recent3 and st in _TRADED_PLAN_STATUSES for sd, st in ticker_plans)
        old_min = _env_int("CANDIDATE_FRESHNESS_OLD_MIN_SESSIONS", 6)
        if age >= old_min:
            grade = "OLD"
        elif age >= 3:
            grade = "MATURE"
        else:
            grade = "NEW"
        result[ticker] = {
            "age_sessions": age,
            "grade": grade,
            "never_planned": never_planned,
            "retrade": retrade,
            "exempt_active": exempt_active,
        }
    return result


def annotate_candidate_freshness(rows: list[dict], market: str) -> dict[str, Any]:
    """후보 row에 신선도 필드를 달고 trainer_prompt_score에 패널티를 반영한다.

    패널티: OLD(6세션+) -15, OLD&플랜전무 추가 -15, 체결후재탕 -10.
    면제: 활성 플랜/보유(exempt_active), rel_vol 급증(>=2), 원장 계산 실패 시 전체 무적용.
    """
    summary = {"enabled": freshness_enabled(), "old": 0, "retrade": 0, "exempt": 0, "penalized": 0}
    if not summary["enabled"] or not rows:
        return summary
    fresh = get_freshness_map(market)
    if not fresh:
        return summary
    market_key = "US" if str(market or "").upper() == "US" else "KR"
    penalty_old = _env_float("CANDIDATE_FRESHNESS_PENALTY_OLD", 15.0)
    penalty_never = _env_float("CANDIDATE_FRESHNESS_PENALTY_NEVER_PLANNED", 15.0)
    penalty_retrade = _env_float("CANDIDATE_FRESHNESS_PENALTY_RETRADE", 10.0)
    rel_vol_exempt = _env_float("CANDIDATE_FRESHNESS_REL_VOL_EXEMPT", 2.0)
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _ticker_key(market_key, row.get("ticker"))
        info = fresh.get(key)
        if not info:
            row.setdefault("freshness_grade", "NEW")
            row.setdefault("freshness_age_sessions", 1)
            continue
        row["freshness_grade"] = info["grade"]
        row["freshness_age_sessions"] = info["age_sessions"]
        row["freshness_retrade"] = bool(info["retrade"])
        penalty = 0.0
        if info["grade"] == "OLD":
            summary["old"] += 1
            penalty += penalty_old
            if info["never_planned"]:
                penalty += penalty_never
        if info["retrade"]:
            summary["retrade"] += 1
            penalty += penalty_retrade
        if penalty <= 0:
            continue
        rel_vol = 0.0
        try:
            rel_vol = float(row.get("rel_vol_shadow") or 0)
        except Exception:
            rel_vol = 0.0
        if info["exempt_active"] or rel_vol >= rel_vol_exempt:
            row["freshness_exempt"] = "active_plan" if info["exempt_active"] else "rel_vol_surge"
            summary["exempt"] += 1
            continue
        try:
            score = float(row.get("trainer_prompt_score") or 0)
        except Exception:
            score = 0.0
        row["trainer_prompt_score_raw"] = score
        row["freshness_penalty"] = penalty
        row["trainer_prompt_score"] = max(0.0, score - penalty)
        summary["penalized"] += 1
    return summary
