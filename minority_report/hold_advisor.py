"""
minority_report/hold_advisor.py — TP 도달 시 분석가 3명 HOLD/SELL 합의

TRAILING_ANALYST_ENABLED=true 일 때만 호출됨.
기본값 false → 트레일링 스탑 즉시 활성화.
"""
import os
import json
import time
import anthropic
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_trading_logger
from minority_report.claude_utils import extract_json, claude_response_meta
from credit_tracker import record as credit_record
from runtime_paths import get_runtime_path
from minority_report.raw_call_logger import save as save_raw_call
from minority_report.prompt_contracts import COMMON_DECISION_CONTRACT, HARD_SOFT_RULE_CONTRACT

try:
    from phase1_trainer.digest_builder import build_intraday_advisor_context as _build_rt_ctx
    _RT_CTX_AVAILABLE = True
except Exception:
    _RT_CTX_AVAILABLE = False

log    = get_trading_logger()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
MODEL  = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# 주의: 이 캐시 플래그는 현재 실효 없음(no-op) — _HOLD_ADVISOR_SYSTEM ~434토큰으로
# Sonnet 4.6 캐시 최소 prefix(실측 1,024토큰, 2026-06-09 ACTIVE_WORK 기록) 미달이라
# cache_control이 조용히 무시된다. 추가 과금은 없으나 절감도 없다.
# prefix 확장은 절감 상한(월 ~$2) 대비 본말전도라 보류 (2026-06-10 검토, ACTIVE_WORK P1 참조).
_HOLD_ADVISOR_CACHE_ENABLED = os.getenv("HOLD_ADVISOR_PROMPT_CACHE_ENABLED", "true").lower() == "true"
_HOLD_ADVISOR_SYSTEM = COMMON_DECISION_CONTRACT + "\n\n" + HARD_SOFT_RULE_CONTRACT + "\n\n모든 응답은 한국어로 작성하세요."

PERSONAS = {
    "bull": "당신은 15년 경력의 성장주 모멘텀 트레이더입니다. 추세가 살아있으면 보유를 선호합니다.",
    "bear": "당신은 헤지펀드 리스크 매니저입니다. 이익 실현 타이밍을 중시하고 욕심을 경계합니다.",
    "neutral": "당신은 퀀트 통계 분석가입니다. 데이터 기반으로 냉정하게 판단합니다.",
}

PERSONA_FOCUS = {
    "bull": "Focus on upside continuation, trend persistence, and whether remaining reward justifies holding.",
    "bear": "Focus on downside risk, event risk, and whether open profit should be protected now.",
    "neutral": "Focus on ATR/statistical fit, peak-to-current drawdown, and expected value of holding.",
}

TRAIL_GUIDE = """Trail guide:
- 0.02 = tight protection; use when profit has reached target and momentum is fading or giveback risk is high.
- 0.03 = normal protection; use when signals are mixed and volatility is ordinary.
- 0.04 = wider room; use when trend is intact but normal pullbacks are likely.
- 0.05 = widest room; use only for strong trend continuation with high noise, not for weak positions."""

HOLD_DECISION_STAGES = {
    "TP_REVIEW",
    "PRE_SESSION",
    "INTRADAY_REVIEW",
    "MAX_HOLD",
    "PRE_CLOSE_CARRY",
    "SOFT_EXIT",
    "AUTO_SELL_REVIEW",
    "MANUAL_REVIEW",
}

HOLD_MODES = {
    "target_extension",
    "profit_pullback",
    "stop_recovery",
    "loss_deferral",
}

STAGE_DEFAULT_POLICIES = {
    "TP_REVIEW": "SELL unless a trend-continuation exception justifies trailing.",
    "PRE_SESSION": "HOLD unless overnight or pre-session risk is broken.",
    "INTRADAY_REVIEW": "HOLD unless risk/reward has deteriorated or thesis is invalid.",
    "MAX_HOLD": "SELL unless there is a clear one-review carry exception.",
    "PRE_CLOSE_CARRY": "SELL unless broker-truth is trusted and carry risk is acceptable.",
    "SOFT_EXIT": "SELL unless the soft exit is premature and risk is protected.",
    "AUTO_SELL_REVIEW": "SELL only when the supplied automatic sell reason remains valid after a fresh risk/reward review.",
    "MANUAL_REVIEW": "HOLD unless the supplied review context supports SELL.",
}

STAGE_LEAD_TEXT = {
    "TP_REVIEW": "목표가에 도달한 포지션을 계속 보유할지 판단하세요.",
    "PRE_SESSION": "개장 전 보유 포지션의 이월 리스크와 장초 대응 필요성을 판단하세요.",
    "INTRADAY_REVIEW": "장중 보유 포지션의 기대수익과 하방 리스크를 재평가하세요.",
    "MAX_HOLD": "최대 보유 기간을 초과했습니다. 예외적으로 한 번 더 보유할 근거가 있는지 판단하세요.",
    "PRE_CLOSE_CARRY": "장마감까지 {minutes_to_close}분 남았습니다. 이 포지션을 다음 세션으로 이월할 예외가 있는지 판단하세요.",
    "SOFT_EXIT": "소프트 매도 조건이 발생했습니다. 즉시 청산할지 보류할지 판단하세요.",
    "AUTO_SELL_REVIEW": "자동 매도 조건이 발동됐습니다. 해당 매도 사유가 현재도 유효한지 재검토하세요.",
    "MANUAL_REVIEW": "제공된 검토 사유를 기준으로 보유 또는 청산을 판단하세요.",
}


def _normalize_stage(decision_stage: Optional[str]) -> str:
    stage = str(decision_stage or "TP_REVIEW").strip().upper()
    return stage if stage in HOLD_DECISION_STAGES else "MANUAL_REVIEW"


def _stage_policy(decision_stage: str, default_policy: Optional[str] = None) -> str:
    return str(default_policy or STAGE_DEFAULT_POLICIES.get(decision_stage, STAGE_DEFAULT_POLICIES["MANUAL_REVIEW"]))


def _normalize_hold_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in HOLD_MODES else ""


def _fallback_vote(reason: str, decision_stage: str = "TP_REVIEW", default_policy: str = "") -> dict:
    stage = _normalize_stage(decision_stage)
    return {
        "action": "HOLD",
        "hold_mode": "",
        "confidence": 0.0,
        "trail_pct": 0.03,
        "sell_urgency": "wait",
        "revised_sell_target": 0.0,
        "protective_stop": 0.0,
        "hard_stop": 0.0,
        "recover_above": 0.0,
        "recovery_watch_min": 0,
        "valid_for_min": 0,
        "reask_after_min": 0,
        "reask_drawdown_from_peak_pct": 0.0,
        "reask_if_price_above": 0.0,
        "max_rechecks": 0,
        "next_review_min": 30,
        "invalid_if": "",
        "reason": reason,
        "fallback": True,
        "decision_stage": stage,
        "default_policy": default_policy or _stage_policy(stage),
    }


def _coerce_vote(result: dict, decision_stage: str = "TP_REVIEW", default_policy: str = "") -> dict:
    stage = _normalize_stage(decision_stage)
    action = str((result or {}).get("action", "HOLD") or "HOLD").strip().upper()
    if action not in {"HOLD", "SELL"}:
        action = "HOLD"
    hold_mode = _normalize_hold_mode((result or {}).get("hold_mode"))
    try:
        confidence = float((result or {}).get("confidence", 0.0) or 0.0)
    except Exception:
        confidence = 0.0
    try:
        trail_pct = float((result or {}).get("trail_pct", 0.03) or 0.03)
    except Exception:
        trail_pct = 0.03
    sell_urgency = str((result or {}).get("sell_urgency", "") or "").strip().lower()
    if sell_urgency not in {"now", "next_open", "wait"}:
        sell_urgency = "now" if action == "SELL" else "wait"
    try:
        protective_stop = float((result or {}).get("protective_stop", 0.0) or 0.0)
    except Exception:
        protective_stop = 0.0
    try:
        revised_sell_target = float((result or {}).get("revised_sell_target", 0.0) or 0.0)
    except Exception:
        revised_sell_target = 0.0
    try:
        hard_stop = float((result or {}).get("hard_stop", 0.0) or 0.0)
    except Exception:
        hard_stop = 0.0
    try:
        recover_above = float((result or {}).get("recover_above", 0.0) or 0.0)
    except Exception:
        recover_above = 0.0
    try:
        recovery_watch_min = int(float((result or {}).get("recovery_watch_min", 0) or 0))
    except Exception:
        recovery_watch_min = 0
    try:
        valid_for_min = int(float((result or {}).get("valid_for_min", 0) or 0))
    except Exception:
        valid_for_min = 0
    try:
        reask_after_min = int(float((result or {}).get("reask_after_min", 0) or 0))
    except Exception:
        reask_after_min = 0
    try:
        reask_drawdown_from_peak_pct = float((result or {}).get("reask_drawdown_from_peak_pct", 0.0) or 0.0)
    except Exception:
        reask_drawdown_from_peak_pct = 0.0
    try:
        reask_if_price_above = float((result or {}).get("reask_if_price_above", 0.0) or 0.0)
    except Exception:
        reask_if_price_above = 0.0
    try:
        max_rechecks = int(float((result or {}).get("max_rechecks", 0) or 0))
    except Exception:
        max_rechecks = 0
    try:
        next_review_min = int(float((result or {}).get("next_review_min", 30) or 30))
    except Exception:
        next_review_min = 30
    return {
        "action": action,
        "hold_mode": hold_mode if action == "HOLD" else "",
        "confidence": max(0.0, min(1.0, confidence)),
        "trail_pct": max(0.02, min(0.05, trail_pct)),
        "sell_urgency": sell_urgency,
        "revised_sell_target": max(0.0, revised_sell_target),
        "protective_stop": max(0.0, protective_stop),
        "hard_stop": max(0.0, hard_stop),
        "recover_above": max(0.0, recover_above),
        "recovery_watch_min": max(0, min(30, recovery_watch_min)),
        "valid_for_min": max(0, min(30, valid_for_min)),
        "reask_after_min": max(0, min(30, reask_after_min)),
        "reask_drawdown_from_peak_pct": max(0.0, min(10.0, reask_drawdown_from_peak_pct)),
        "reask_if_price_above": max(0.0, reask_if_price_above),
        "max_rechecks": max(0, min(8, max_rechecks)),
        "next_review_min": max(5, min(240, next_review_min)),
        "invalid_if": str((result or {}).get("invalid_if", "") or "")[:240],
        "reason": str((result or {}).get("reason", "") or ""),
        "fallback": bool((result or {}).get("fallback", False)),
        "decision_stage": stage,
        "default_policy": default_policy or _stage_policy(stage),
    }


TRIAGE_CATEGORIES = {"STOP_LOSS", "HOLD", "SELL"}
TRIAGE_EXIT_DRIVERS = {
    "invalid_if",
    "loss_cap",
    "hard_stop",
    "failed_recovery",
    "profit_protection",
    "time_carry",
    "bounded_hold",
    "other",
}
TRIAGE_CLEAR_STOP_DRIVERS = {"invalid_if", "loss_cap", "hard_stop", "failed_recovery"}
TRIAGE_PROMPT_VERSION = "hold_advisor_triage_v1_1"
CHALLENGE_PROMPT_VERSION = "hold_advisor_challenge_v1_1"
_TRIAGE_SHADOW_CALLS = 0


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default)) or default))
    except Exception:
        return default


def _env_int_bound(name: str, default: int, min_value: int, max_value: int) -> int:
    value = _env_int(name, default)
    return max(min_value, min(max_value, value))


def _triage_max_tokens() -> int:
    return _env_int_bound("HOLD_ADVISOR_TRIAGE_MAX_TOKENS", 1400, 900, 2500)


def _challenge_max_tokens() -> int:
    return _env_int_bound("HOLD_ADVISOR_CHALLENGE_MAX_TOKENS", 1100, 700, 2000)


def _triage_stage_allowlist() -> set[str]:
    raw = os.getenv("HOLD_ADVISOR_TRIAGE_STAGE_ALLOWLIST", "AUTO_SELL_REVIEW")
    stages = set()
    for part in str(raw or "").replace(";", ",").split(","):
        stage = str(part or "").strip().upper()
        if stage in HOLD_DECISION_STAGES:
            stages.add(stage)
    return stages or {"AUTO_SELL_REVIEW"}


def _triage_stage_allowed(decision_stage: str) -> bool:
    return _normalize_stage(decision_stage) in _triage_stage_allowlist()


def _triage_production_enabled(decision_stage: str) -> bool:
    return _env_bool("HOLD_ADVISOR_TRIAGE_ENABLED", False) and _triage_stage_allowed(decision_stage)


def _triage_challenge_enabled(decision_stage: str) -> bool:
    return _env_bool("HOLD_ADVISOR_TRIAGE_CHALLENGE_ENABLED", True) and _triage_stage_allowed(decision_stage)


def _triage_legacy_fallback_enabled() -> bool:
    return _env_bool("HOLD_ADVISOR_TRIAGE_LEGACY_FALLBACK_ENABLED", False)


def _triage_shadow_allowed(decision_stage: str) -> bool:
    global _TRIAGE_SHADOW_CALLS
    if not _env_bool("HOLD_ADVISOR_TRIAGE_SHADOW", False):
        return False
    if not _triage_stage_allowed(decision_stage):
        return False
    max_calls = _env_int("HOLD_ADVISOR_TRIAGE_LIVE_SHADOW_MAX_CALLS", 0)
    if max_calls <= 0 or _TRIAGE_SHADOW_CALLS >= max_calls:
        return False
    _TRIAGE_SHADOW_CALLS += 1
    return True


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _as_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _normalize_triage_category(value) -> str:
    category = str(value or "").strip().upper()
    if category in TRIAGE_CATEGORIES:
        return category
    aliases = {
        "STOP": "STOP_LOSS",
        "LOSS_CUT": "STOP_LOSS",
        "CUT_LOSS": "STOP_LOSS",
        "TAKE_PROFIT": "SELL",
        "PROFIT_TAKE": "SELL",
    }
    return aliases.get(category, "HOLD")


def _normalize_exit_driver(value) -> str:
    driver = str(value or "").strip().lower()
    return driver if driver in TRIAGE_EXIT_DRIVERS else "other"


_TRIAGE_PROMPT_EXCLUDED_KEYS = {
    "messages",
    "parsed",
    "prior_votes",
    "prompt",
    "raw_prompt",
    "raw_response",
    "votes",
}


def _scrub_triage_prompt_value(value, depth: int = 0):
    if depth > 4:
        return str(value)[:500] if value is not None else None
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.strip().lower() in _TRIAGE_PROMPT_EXCLUDED_KEYS:
                continue
            cleaned[key_text] = _scrub_triage_prompt_value(item, depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_scrub_triage_prompt_value(item, depth + 1) for item in list(value)[:20]]
    if isinstance(value, str):
        return value[:1000]
    return value


def _derive_hold_mode(pos: dict, exit_driver: str) -> str:
    mode = _normalize_hold_mode(pos.get("hold_mode"))
    if mode:
        return mode
    text = " ".join(
        str(pos.get(key, "") or "").lower()
        for key in ("auto_sell_reason", "auto_sell_close_reason", "default_policy")
    )
    if exit_driver in {"invalid_if", "loss_cap", "hard_stop", "failed_recovery"}:
        return "stop_recovery"
    if any(token in text for token in ("profit", "trail", "ladder", "floor")):
        return "profit_pullback"
    if any(token in text for token in ("target", "tp")):
        return "target_extension"
    if any(token in text for token in ("loss", "stop")):
        return "stop_recovery"
    return "profit_pullback"


def _input_completeness(
    pos: dict,
    market: str,
    decision_stage: str,
    rt_context: str,
    minutes_to_close: Optional[float] = None,
) -> dict[str, Any]:
    advisor_ctx = pos.get("advisor_context_v2") if isinstance(pos.get("advisor_context_v2"), dict) else {}
    plan = pos.get("pathb_plan") if isinstance(pos.get("pathb_plan"), dict) else {}
    entry = _as_float(pos.get("entry") or pos.get("avg_price") or pos.get("display_avg_price"), 0.0)
    current = _as_float(pos.get("current_price") or pos.get("display_current_price") or pos.get("exit_price"), 0.0)
    target = _as_float(
        pos.get("tp")
        or pos.get("display_tp_price")
        or pos.get("pathb_reference_target")
        or advisor_ctx.get("pathb_reference_target")
        or advisor_ctx.get("selection_reference_target"),
        0.0,
    )
    stop = _as_float(
        pos.get("sl")
        or pos.get("trail_sl")
        or pos.get("pathb_reference_stop")
        or advisor_ctx.get("pathb_reference_stop")
        or advisor_ctx.get("selection_reference_stop")
        or advisor_ctx.get("hard_stop_price"),
        0.0,
    )
    is_pathb = bool(
        str(pos.get("path_type") or pos.get("strategy_used") or pos.get("source_strategy") or "").lower() == "claude_price"
        or pos.get("pathb_plan")
        or pos.get("pathb_path_run_id")
        or pos.get("path_run_id")
    )
    checks = {
        "entry_ok": entry > 0,
        "current_ok": current > 0,
        "pnl_ok": pos.get("pnl_pct") not in (None, "") or (entry > 0 and current > 0),
        "target_ok": target > 0,
        "stop_ok": stop > 0,
        "advisor_context_v2_ok": bool(advisor_ctx),
        "pathb_reference_ok": (not is_pathb) or (
            _as_float(
                pos.get("pathb_reference_target") or advisor_ctx.get("pathb_reference_target") or plan.get("sell_target"),
                0.0,
            ) > 0
            and _as_float(
                pos.get("pathb_reference_stop") or advisor_ctx.get("pathb_reference_stop") or plan.get("stop_loss"),
                0.0,
            ) > 0
        ),
        "market_context_ok": bool(str(rt_context or "").strip()),
        "minutes_to_close_ok": str(decision_stage or "").upper() != "PRE_CLOSE_CARRY"
        or minutes_to_close is not None
        or pos.get("minutes_to_close") not in (None, ""),
    }
    missing = [key for key, ok in checks.items() if not bool(ok)]
    score = (len(checks) - len(missing)) / max(1, len(checks))
    return {**checks, "missing": missing, "score": round(score, 4)}


def _pathb_revenue_path_context(
    pos: dict,
    decision_stage: str,
    default_policy: str,
    minutes_to_close: Optional[float],
) -> dict[str, Any]:
    advisor_ctx = pos.get("advisor_context_v2") if isinstance(pos.get("advisor_context_v2"), dict) else {}
    plan = pos.get("pathb_plan") if isinstance(pos.get("pathb_plan"), dict) else {}
    is_pathb = bool(
        plan
        or pos.get("pathb_path_run_id")
        or pos.get("path_run_id")
        or str(pos.get("path_type") or pos.get("strategy_used") or pos.get("source_strategy") or "").lower() == "claude_price"
    )
    reason_text = " ".join(
        str(value or "").lower()
        for value in (
            decision_stage,
            default_policy,
            pos.get("auto_sell_reason"),
            pos.get("auto_sell_close_reason"),
            advisor_ctx.get("exit_signal_reason"),
            advisor_ctx.get("exit_signal_close_reason"),
        )
    )
    if str(decision_stage or "").upper() == "PRE_CLOSE_CARRY":
        exit_reason = "pre_close"
    elif "profit_ladder" in reason_text or "profit_floor" in reason_text:
        exit_reason = "profit_ladder"
    elif "target" in reason_text or "take_profit" in reason_text:
        exit_reason = "target"
    elif "loss_cap" in reason_text:
        exit_reason = "loss_cap"
    elif "hard_stop" in reason_text or "stop_loss" in reason_text:
        exit_reason = "hard_stop"
    else:
        exit_reason = "other"
    return {
        "is_pathb": is_pathb,
        "path_run_id": str(pos.get("pathb_path_run_id") or pos.get("path_run_id") or plan.get("path_run_id") or ""),
        "origin_action": str(pos.get("pathb_origin_action") or plan.get("origin_action") or ""),
        "exit_reason": exit_reason,
        "reference_target": _as_float(
            pos.get("pathb_reference_target") or advisor_ctx.get("pathb_reference_target") or plan.get("sell_target"),
            0.0,
        ),
        "reference_stop": _as_float(
            pos.get("pathb_reference_stop") or advisor_ctx.get("pathb_reference_stop") or plan.get("stop_loss"),
            0.0,
        ),
        "profit_ladder_tier": str(pos.get("pathb_profit_ladder_tier") or advisor_ctx.get("pathb_profit_ladder_tier") or ""),
        "minutes_to_close": float(minutes_to_close) if minutes_to_close is not None else None,
    }


def _compact_news_prompt_text(value, max_chars: int = 120) -> str:
    text = " ".join(str(value or "").replace("|", " ").split())
    return text[: max(1, int(max_chars))].strip()


def _position_news_payload(pos: dict) -> dict:
    source = pos if isinstance(pos, dict) else {}
    advisor_ctx = source.get("advisor_context_v2") if isinstance(source.get("advisor_context_v2"), dict) else {}

    def _value(key: str):
        value = source.get(key)
        if value not in (None, "", []):
            return value
        return advisor_ctx.get(key)

    try:
        count = int(float(_value("news_or_earnings_count") or 0))
    except Exception:
        count = 0
    raw_sources = _value("news_or_earnings_sources")
    if isinstance(raw_sources, (list, tuple, set)):
        sources = [_compact_news_prompt_text(src, 32) for src in raw_sources]
    elif raw_sources:
        sources = [_compact_news_prompt_text(raw_sources, 80)]
    else:
        sources = []
    sources = [src for src in sources if src][:4]
    sample_title = _compact_news_prompt_text(
        _value("news_or_earnings_sample_title") or _value("news_sample_title"),
        140,
    )
    prompt_summary = _compact_news_prompt_text(_value("news_prompt_summary"), 180)
    risk_summary = _compact_news_prompt_text(_value("risk_news_summary"), 160)
    signal_type = _compact_news_prompt_text(_value("news_signal_type"), 40)
    raw_prompt_ids = _value("prompt_news_ids")
    if isinstance(raw_prompt_ids, (list, tuple, set)):
        prompt_ids = [_compact_news_prompt_text(value, 48) for value in raw_prompt_ids]
    elif raw_prompt_ids:
        prompt_ids = [_compact_news_prompt_text(raw_prompt_ids, 48)]
    else:
        prompt_ids = []
    prompt_ids = [value for value in prompt_ids if value][:4]
    flagged = (
        bool(_value("news_or_earnings_flag"))
        or count > 0
        or bool(sources)
        or bool(sample_title)
        or bool(prompt_summary)
        or bool(risk_summary)
    )
    if not flagged:
        return {}
    payload = {"flag": True}
    if count > 0:
        payload["count"] = count
    if sources:
        payload["sources"] = sources
    quality = _compact_news_prompt_text(_value("news_quality"), 32)
    if quality:
        payload["quality"] = quality
    date_quality = _compact_news_prompt_text(_value("news_date_quality"), 32)
    if date_quality and date_quality != "dated":
        payload["date_quality"] = date_quality
    if signal_type:
        payload["signal_type"] = signal_type
    try:
        news_score = int(float(_value("news_score") or 0))
    except Exception:
        news_score = 0
    if news_score > 0:
        payload["score"] = news_score
    if bool(_value("news_prompt_eligible")):
        payload["prompt_eligible"] = True
    if prompt_summary:
        payload["prompt_summary"] = prompt_summary
    elif sample_title:
        payload["sample_title"] = sample_title
    if risk_summary:
        payload["risk_summary"] = risk_summary
    if prompt_ids:
        payload["prompt_news_ids"] = prompt_ids
    return payload


def _position_news_context_text(pos: dict) -> str:
    payload = _position_news_payload(pos)
    if not payload:
        return ""
    parts = []
    if payload.get("count"):
        parts.append(f"count={payload.get('count')}")
    if payload.get("sources"):
        parts.append("sources=" + ",".join(payload.get("sources") or []))
    if payload.get("quality"):
        parts.append("quality=" + str(payload.get("quality")))
    if payload.get("date_quality"):
        parts.append("date=" + str(payload.get("date_quality")))
    if payload.get("signal_type"):
        parts.append("signal=" + str(payload.get("signal_type")))
    if payload.get("score"):
        parts.append("score=" + str(payload.get("score")))
    if payload.get("prompt_eligible"):
        parts.append("eligible=true")
    if payload.get("prompt_summary"):
        parts.append("summary=" + str(payload.get("prompt_summary")))
    if payload.get("sample_title"):
        parts.append("sample_title=" + str(payload.get("sample_title")))
    if payload.get("risk_summary"):
        parts.append("risk=" + str(payload.get("risk_summary")))
    return "\n━━━ Position news ━━━\n  " + " | ".join(parts or ["flag=true"]) + "\n"


def _triage_case_payload(
    pos: dict,
    market: str,
    digest_prompt: str,
    rt_context: str,
    decision_stage: str,
    default_policy: str,
    minutes_to_close: Optional[float],
    force_exit_window: bool,
) -> dict:
    entry = _as_float(pos.get("entry") or pos.get("avg_price") or pos.get("display_avg_price"), 0.0)
    current = _as_float(pos.get("current_price") or pos.get("display_current_price") or pos.get("exit_price"), 0.0)
    display_entry = _as_float(pos.get("display_avg_price"), 0.0)
    display_current = _as_float(pos.get("display_current_price"), 0.0)
    native_entry = display_entry if str(market or "").upper() == "US" and display_entry > 0 else entry
    native_current = display_current if str(market or "").upper() == "US" and display_current > 0 else current
    pnl_pct = _as_float(pos.get("pnl_pct"), 0.0)
    if not pnl_pct and native_entry > 0 and native_current > 0:
        pnl_pct = (native_current / native_entry - 1.0) * 100.0
    advisor_ctx = pos.get("advisor_context_v2") if isinstance(pos.get("advisor_context_v2"), dict) else {}
    pathb_exit_signal = pos.get("pathb_exit_signal") if isinstance(pos.get("pathb_exit_signal"), dict) else {}
    completeness = _input_completeness(pos, market, decision_stage, rt_context or digest_prompt, minutes_to_close)
    revenue_context = _pathb_revenue_path_context(pos, decision_stage, default_policy, minutes_to_close)
    hard_guard = {
        "breached": bool(pos.get("hard_guard_breached") or pos.get("_hard_guard_breach_detail")),
        "reason": pos.get("hard_guard_reason", ""),
        "source": pos.get("hard_guard_source", ""),
        "current": _as_float(pos.get("hard_guard_current"), 0.0),
        "stop": _as_float(pos.get("hard_guard_stop"), 0.0),
        "detail": pos.get("_hard_guard_breach_detail", ""),
    }
    payload = {
        "ticker": pos.get("ticker", "-"),
        "market": market,
        "strategy": pos.get("strategy", ""),
        "decision_stage": decision_stage,
        "default_policy": default_policy,
        "minutes_to_close": minutes_to_close if minutes_to_close is not None else "unknown",
        "force_exit_window": bool(force_exit_window),
        "entry": native_entry,
        "current": native_current,
        "pnl_pct": round(pnl_pct, 4),
        "peak_pnl_pct": _as_float(pos.get("peak_pnl_pct"), 0.0),
        "held_days": pos.get("held_days", 0),
        "entry_time": pos.get("entry_time", ""),
        "tp": pos.get("tp", pos.get("display_tp_price", 0)),
        "sl": pos.get("sl", 0),
        "trail_sl": pos.get("trail_sl", 0),
        "trailing": bool(pos.get("trailing", False)),
        "tp_triggered": bool(pos.get("tp_triggered", False)),
        "auto_sell_reason": pos.get("auto_sell_reason", ""),
        "auto_sell_close_reason": pos.get("auto_sell_close_reason", ""),
        "pathb_exit_signal": _scrub_triage_prompt_value(pathb_exit_signal),
        "advisor_context_v2": _scrub_triage_prompt_value(advisor_ctx),
        "input_completeness": completeness,
        "pathb_revenue_path_context": revenue_context,
        "market_context": (rt_context or digest_prompt or "")[:1800],
    }
    position_news = _position_news_payload(pos)
    if position_news:
        payload["position_news"] = position_news
    if hard_guard["breached"]:
        payload["hard_guard"] = _scrub_triage_prompt_value(hard_guard)
    return payload


def _build_triage_prompt(
    pos: dict,
    market: str,
    digest_prompt: str,
    rt_context: str,
    decision_stage: str,
    default_policy: str,
    minutes_to_close: Optional[float],
    force_exit_window: bool,
) -> str:
    payload = _triage_case_payload(
        pos,
        market,
        digest_prompt,
        rt_context,
        decision_stage,
        default_policy,
        minutes_to_close,
        force_exit_window,
    )
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return f"""Classify the advisor category for this position:
- STOP_LOSS: exit because loss, stop, loss_cap, hard_stop, invalid_if, failed recovery, or thesis invalidation is valid now.
- HOLD: keep the position only if thesis is intact and risk is bounded by protective_stop, invalid_if, and next_review_min.
- SELL: exit for non-stop reasons such as profit taking, target/profit protection, time decay, pre-close/carry risk, or poor remaining reward.

Important:
- category is the exit reason class, not just the final order action.
- For HOLD, protective_stop, invalid_if, and next_review_min are mandatory.
- For PathB profit_ladder/profit-protection HOLD, if advisor_context_v2.profit_ladder_hold_min_protective_stop is present, protective_stop must be below current and at or above that minimum; otherwise the system may preserve HOLD but ignore the stop update.
- Do not repeat a distant plan stop as protective_stop for profit-protection HOLD.

Case data:
{payload_text}

Return strict JSON only:
- Return one compact JSON object only. No markdown fences, no prose, no comments.
- Keep reason and invalid_if <= 120 chars.
- primary_evidence and counter_evidence: max 2 strings each, <= 80 chars per string.
{{
  "category": "STOP_LOSS|HOLD|SELL",
  "confidence": 0.0,
  "urgency": "now|next_open|wait",
  "exit_driver": "invalid_if|loss_cap|hard_stop|failed_recovery|profit_protection|time_carry|bounded_hold|other",
  "hold_mode": "target_extension|profit_pullback|stop_recovery|loss_deferral",
  "protective_stop": null,
  "hard_stop": null,
  "recover_above": null,
  "revised_sell_target": 0.0,
  "valid_for_min": 0,
  "reask_after_min": 0,
  "next_review_min": null,
  "invalid_if": "",
  "needs_second_opinion": false,
  "primary_evidence": ["", ""],
  "counter_evidence": ["", ""],
  "reason": ""
}}"""


def _build_challenge_prompt(
    pos: dict,
    market: str,
    digest_prompt: str,
    rt_context: str,
    decision_stage: str,
    default_policy: str,
    minutes_to_close: Optional[float],
    force_exit_window: bool,
    triage: Optional[dict],
) -> str:
    payload = _triage_case_payload(
        pos,
        market,
        digest_prompt,
        rt_context,
        decision_stage,
        default_policy,
        minutes_to_close,
        force_exit_window,
    )
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    triage_text = json.dumps(
        {
            "category": triage.get("exit_category", ""),
            "action": triage.get("action", ""),
            "confidence": triage.get("confidence", 0.0),
            "exit_driver": triage.get("exit_driver", ""),
            "protective_stop": triage.get("protective_stop", 0.0),
            "next_review_min": triage.get("next_review_min", 0),
            "invalid_if": triage.get("invalid_if", ""),
            "reason": triage.get("reason", ""),
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    return f"""You are challenging a first-pass hold-advisor decision.

Challenge focus:
- If first pass is SELL, check whether this is premature profit-taking or time/carry overreaction.
- If first pass is HOLD, check whether risk is actually bounded.
- If first pass is STOP_LOSS, check whether invalid_if, failed recovery, loss_cap, or hard_stop is truly triggered.
- If final_category is HOLD, return protective_stop, invalid_if, and next_review_min. Do not leave HOLD unbounded.
- For PathB profit_ladder/profit-protection HOLD, respect advisor_context_v2.profit_ladder_hold_min_protective_stop when present; do not present a distant stop as bounded protection.

Case data:
{payload_text}

First-pass result:
{triage_text}

Return strict JSON only:
- Return one compact JSON object only. No markdown fences, no prose, no comments.
- Keep reason, invalid_if, risk_if_wrong, and minimum_condition_to_hold <= 120 chars.
{{
  "confirm": true,
  "final_category": "STOP_LOSS|HOLD|SELL",
  "confidence": 0.0,
  "hold_mode": "target_extension|profit_pullback|stop_recovery|loss_deferral",
  "sell_urgency": "now|next_open|wait",
  "protective_stop": null,
  "hard_stop": null,
  "recover_above": null,
  "next_review_min": null,
  "invalid_if": "",
  "risk_if_wrong": "",
  "minimum_condition_to_hold": "",
  "reason": ""
}}"""


def _coerce_challenge_vote(result: dict) -> dict:
    raw = result or {}
    gaps: list[str] = []
    raw_hold_mode = str(raw.get("hold_mode", "") or "").strip()
    hold_mode = _normalize_hold_mode(raw.get("hold_mode"))
    if not raw_hold_mode or not hold_mode:
        gaps.append("challenge_missing_hold_mode")
    raw_urgency = str(raw.get("sell_urgency", "") or "").strip().lower()
    urgency = raw_urgency
    if urgency not in {"now", "next_open", "wait"}:
        gaps.append("challenge_missing_sell_urgency")
        urgency = "wait"
    protective_stop = max(0.0, _as_float(raw.get("protective_stop"), 0.0))
    invalid_if = str(raw.get("invalid_if", "") or "")[:240]
    if protective_stop <= 0:
        gaps.append("challenge_missing_protective_stop")
    if not invalid_if.strip() and not str(raw.get("minimum_condition_to_hold", "") or "").strip():
        gaps.append("challenge_missing_invalid_if")
    return {
        "confirm": bool(raw.get("confirm", False)),
        "final_category": _normalize_triage_category(raw.get("final_category")),
        "confidence": max(0.0, min(1.0, _as_float(raw.get("confidence"), 0.0))),
        "hold_mode": hold_mode,
        "sell_urgency": urgency,
        "protective_stop": protective_stop,
        "hard_stop": max(0.0, _as_float(raw.get("hard_stop"), 0.0)),
        "recover_above": max(0.0, _as_float(raw.get("recover_above"), 0.0)),
        "next_review_min": max(5, min(240, _as_int(raw.get("next_review_min"), 30))),
        "invalid_if": invalid_if,
        "risk_if_wrong": str(raw.get("risk_if_wrong", "") or "")[:500],
        "minimum_condition_to_hold": str(raw.get("minimum_condition_to_hold", "") or "")[:500],
        "reason": str(raw.get("reason", "") or "")[:500],
        "challenge_field_gaps": sorted(set(gaps)),
        "challenge_prompt_version": CHALLENGE_PROMPT_VERSION,
        "parse_error": bool(raw.get("parse_error", False)),
    }


def _ask_challenge(
    pos: dict,
    market: str,
    digest_prompt: str,
    rt_context: str,
    decision_stage: str,
    default_policy: str,
    minutes_to_close: Optional[float],
    force_exit_window: bool,
    triage: dict,
) -> dict:
    prompt = _build_challenge_prompt(
        pos,
        market,
        digest_prompt,
        rt_context,
        decision_stage,
        default_policy,
        minutes_to_close,
        force_exit_window,
        triage,
    )
    completeness = _input_completeness(pos, market, decision_stage, rt_context or digest_prompt, minutes_to_close)
    revenue_context = _pathb_revenue_path_context(pos, decision_stage, default_policy, minutes_to_close)
    call_started = time.perf_counter()
    try:
        max_tokens = _challenge_max_tokens()
        _create_kwargs: dict = {"model": MODEL, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
        # 공유 계약은 항상 system으로 전달 (user 프롬프트의 인라인 중복은 제거됨).
        # cache_control은 플래그로만 제어 — 현재 prefix가 최소 토큰 미달이라 no-op (상단 주석 참고).
        _system_block: dict = {"type": "text", "text": _HOLD_ADVISOR_SYSTEM}
        if _HOLD_ADVISOR_CACHE_ENABLED:
            _system_block["cache_control"] = {"type": "ephemeral"}
        _create_kwargs["system"] = [_system_block]
        resp = client.messages.create(**_create_kwargs)
        duration_ms = int(max(0.0, (time.perf_counter() - call_started) * 1000.0))
        raw = resp.content[0].text.strip()
        result = extract_json(raw)
        credit_record(
            resp.usage.input_tokens, resp.usage.output_tokens, "hold_advisor_challenge", model=MODEL,
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
        _cm = claude_response_meta(resp)
        save_raw_call(
            label="hold_advisor_challenge",
            prompt=prompt,
            raw_response=raw,
            parsed=result,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            market=market,
            model=MODEL,
            prompt_version=CHALLENGE_PROMPT_VERSION,
            duration_ms=duration_ms,
            cache_creation_input_tokens=_cm["cache_creation_input_tokens"],
            cache_read_input_tokens=_cm["cache_read_input_tokens"],
            request_id=_cm["request_id"],
            service_tier=_cm["service_tier"],
            extra={
                "decision_stage": decision_stage,
                "default_policy": default_policy,
                "input_completeness": completeness,
                "pathb_revenue_path_context": revenue_context,
                "triage_category": triage.get("exit_category", ""),
                "triage_driver": triage.get("exit_driver", ""),
                "token_budget": {"max_tokens": max_tokens},
            },
        )
        vote = _coerce_challenge_vote(result)
        vote["duration_ms"] = duration_ms
        return vote
    except Exception as exc:
        duration_ms = int(max(0.0, (time.perf_counter() - call_started) * 1000.0))
        log.warning(f"[hold_advisor:challenge] error -> safe HOLD fallback: {exc}")
        return {
            "confirm": False,
            "final_category": "HOLD",
            "confidence": 0.0,
            "hold_mode": "",
            "sell_urgency": "wait",
            "protective_stop": 0.0,
            "hard_stop": 0.0,
            "recover_above": 0.0,
            "next_review_min": 30,
            "invalid_if": "",
            "risk_if_wrong": "",
            "minimum_condition_to_hold": "",
            "reason": "challenge_error",
            "challenge_field_gaps": ["challenge_parse_error"],
            "challenge_prompt_version": CHALLENGE_PROMPT_VERSION,
            "parse_error": True,
            "duration_ms": duration_ms,
            "error": str(exc)[:200],
        }


def _coerce_triage_vote(result: dict, pos: dict, decision_stage: str, default_policy: str) -> dict:
    stage = _normalize_stage(decision_stage)
    category = _normalize_triage_category((result or {}).get("category"))
    exit_driver = _normalize_exit_driver((result or {}).get("exit_driver"))
    action = "HOLD" if category == "HOLD" else "SELL"
    confidence = max(0.0, min(1.0, _as_float((result or {}).get("confidence"), 0.0)))
    urgency = str((result or {}).get("urgency", "") or "").strip().lower()
    if urgency not in {"now", "next_open", "wait"}:
        urgency = "wait" if action == "HOLD" else "now"
    protective_stop = max(0.0, _as_float((result or {}).get("protective_stop"), 0.0))
    hard_stop = max(0.0, _as_float((result or {}).get("hard_stop"), 0.0))
    recover_above = max(0.0, _as_float((result or {}).get("recover_above"), 0.0))
    valid_for_min = max(0, min(30, _as_int((result or {}).get("valid_for_min"), 0)))
    reask_after_min = max(0, min(30, _as_int((result or {}).get("reask_after_min"), 0)))
    next_review_min = max(5, min(240, _as_int((result or {}).get("next_review_min"), 30)))
    hold_mode = _normalize_hold_mode((result or {}).get("hold_mode")) or _derive_hold_mode(pos, exit_driver)
    return {
        "action": action,
        "hold_mode": hold_mode if action == "HOLD" else "",
        "confidence": confidence,
        "trail_pct": 0.03,
        "sell_urgency": urgency,
        "revised_sell_target": max(0.0, _as_float((result or {}).get("revised_sell_target"), 0.0)),
        "protective_stop": protective_stop,
        "hard_stop": hard_stop,
        "recover_above": recover_above,
        "recovery_watch_min": 0,
        "valid_for_min": valid_for_min,
        "reask_after_min": reask_after_min,
        "reask_drawdown_from_peak_pct": 0.0,
        "reask_if_price_above": 0.0,
        "max_rechecks": 0,
        "next_review_min": next_review_min,
        "invalid_if": str((result or {}).get("invalid_if", "") or "")[:240],
        "reason": str((result or {}).get("reason", "") or "")[:500],
        "fallback": bool((result or {}).get("fallback", False)),
        "decision_stage": stage,
        "default_policy": default_policy or _stage_policy(stage),
        "exit_category": category,
        "exit_driver": exit_driver,
        "triage_confidence": confidence,
        "needs_second_opinion_model": bool((result or {}).get("needs_second_opinion", False)),
        "primary_evidence": list((result or {}).get("primary_evidence") or [])[:4],
        "counter_evidence": list((result or {}).get("counter_evidence") or [])[:4],
        "triage_prompt_version": TRIAGE_PROMPT_VERSION,
        "triage_parse_error": bool((result or {}).get("parse_error", False)),
    }


def _fallback_triage(reason: str, decision_stage: str, default_policy: str) -> dict:
    vote = _fallback_vote(reason, decision_stage=decision_stage, default_policy=default_policy)
    vote.update(
        {
            "exit_category": "HOLD",
            "exit_driver": "other",
            "triage_confidence": 0.0,
            "needs_second_opinion_model": True,
            "primary_evidence": [],
            "counter_evidence": [],
            "triage_prompt_version": TRIAGE_PROMPT_VERSION,
            "triage_parse_error": True,
        }
    )
    return vote


def _triage_hold_boundary_valid(triage: dict) -> bool:
    payload = triage or {}
    category = str(payload.get("exit_category") or "").upper()
    action = str(payload.get("action") or "").upper()
    if category != "HOLD" and action != "HOLD":
        return True
    return (
        _as_float(payload.get("protective_stop"), 0.0) > 0
        and bool(str(payload.get("invalid_if") or "").strip())
        and 5 <= _as_int(payload.get("next_review_min"), 0) <= 240
    )


def _triage_second_opinion_reason(triage: dict, decision_stage: str) -> str:
    category = str((triage or {}).get("exit_category") or "")
    driver = str((triage or {}).get("exit_driver") or "other")
    confidence = _as_float((triage or {}).get("confidence"), 0.0)
    if bool((triage or {}).get("triage_parse_error", False)):
        return "parse_error"
    if category == "HOLD" and not _triage_hold_boundary_valid(triage):
        return "hold_boundary_missing"
    if bool((triage or {}).get("needs_second_opinion_model", False)):
        return "model_requested_second_opinion"
    if category == "SELL" and confidence < _env_float("HOLD_ADVISOR_TRIAGE_MIN_SELL_CONFIDENCE", 0.85):
        return "sell_confidence_below_threshold"
    if category == "SELL" and driver in {"profit_protection", "time_carry", "other"}:
        if not _env_bool("HOLD_ADVISOR_TRIAGE_NON_STOP_SELL_ENABLED", False):
            return "non_stop_sell_escalation"
    if category == "HOLD" and confidence < _env_float("HOLD_ADVISOR_TRIAGE_MIN_HOLD_CONFIDENCE", 0.72):
        return "hold_confidence_below_threshold"
    if category == "STOP_LOSS" and driver not in TRIAGE_CLEAR_STOP_DRIVERS:
        return "stop_loss_driver_not_clear"
    if category == "STOP_LOSS" and confidence < _env_float("HOLD_ADVISOR_TRIAGE_MIN_STOP_CONFIDENCE", 0.72):
        return "stop_loss_confidence_below_threshold"
    if _normalize_stage(decision_stage) == "PRE_CLOSE_CARRY":
        return "pre_close_carry_requires_challenge"
    return ""


def _triage_direct_allowed(triage: dict, decision_stage: str) -> bool:
    if not triage or bool(triage.get("fallback")):
        return False
    category = str(triage.get("exit_category") or "")
    reason = _triage_second_opinion_reason(triage, decision_stage)
    if category == "STOP_LOSS" and not reason:
        return True
    if (
        category == "HOLD"
        and _env_bool("HOLD_ADVISOR_TRIAGE_BOUNDED_HOLD_ENABLED", False)
        and not reason
    ):
        return True
    if (
        category == "SELL"
        and _env_bool("HOLD_ADVISOR_TRIAGE_NON_STOP_SELL_ENABLED", False)
        and not reason
    ):
        return True
    return False


def _ask_triage(
    pos: dict,
    market: str,
    digest_prompt: str,
    rt_context: str,
    decision_stage: str,
    default_policy: str,
    minutes_to_close: Optional[float],
    force_exit_window: bool,
    prompt_mode: str = "production",
) -> dict:
    prompt = _build_triage_prompt(
        pos,
        market,
        digest_prompt,
        rt_context,
        decision_stage,
        default_policy,
        minutes_to_close,
        force_exit_window,
    )
    completeness = _input_completeness(pos, market, decision_stage, rt_context or digest_prompt, minutes_to_close)
    revenue_context = _pathb_revenue_path_context(pos, decision_stage, default_policy, minutes_to_close)
    call_started = time.perf_counter()
    try:
        max_tokens = _triage_max_tokens()
        _create_kwargs: dict = {"model": MODEL, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
        # 공유 계약은 항상 system으로 전달 (user 프롬프트의 인라인 중복은 제거됨).
        # cache_control은 플래그로만 제어 — 현재 prefix가 최소 토큰 미달이라 no-op (상단 주석 참고).
        _system_block: dict = {"type": "text", "text": _HOLD_ADVISOR_SYSTEM}
        if _HOLD_ADVISOR_CACHE_ENABLED:
            _system_block["cache_control"] = {"type": "ephemeral"}
        _create_kwargs["system"] = [_system_block]
        resp = client.messages.create(**_create_kwargs)
        duration_ms = int(max(0.0, (time.perf_counter() - call_started) * 1000.0))
        raw = resp.content[0].text.strip()
        result = extract_json(raw)
        credit_record(
            resp.usage.input_tokens, resp.usage.output_tokens, "hold_advisor_triage", model=MODEL,
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
        _cm = claude_response_meta(resp)
        save_raw_call(
            label="hold_advisor_triage",
            prompt=prompt,
            raw_response=raw,
            parsed=result,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            market=market,
            model=MODEL,
            prompt_version=TRIAGE_PROMPT_VERSION,
            duration_ms=duration_ms,
            cache_creation_input_tokens=_cm["cache_creation_input_tokens"],
            cache_read_input_tokens=_cm["cache_read_input_tokens"],
            request_id=_cm["request_id"],
            service_tier=_cm["service_tier"],
            extra={
                "decision_stage": decision_stage,
                "default_policy": default_policy,
                "prompt_mode": prompt_mode,
                "input_completeness": completeness,
                "pathb_revenue_path_context": revenue_context,
                "token_budget": {"max_tokens": max_tokens},
            },
        )
        vote = _coerce_triage_vote(result, pos, decision_stage, default_policy)
        vote["duration_ms"] = duration_ms
        vote["prompt_mode"] = prompt_mode
        return vote
    except Exception as exc:
        duration_ms = int(max(0.0, (time.perf_counter() - call_started) * 1000.0))
        log.warning(f"[hold_advisor:triage] error -> safe HOLD fallback: {exc}")
        vote = _fallback_triage("triage_error", decision_stage, default_policy)
        vote["duration_ms"] = duration_ms
        vote["prompt_mode"] = prompt_mode
        vote["error"] = str(exc)[:200]
        return vote


def _positive_or_fallback(primary: object, fallback: object = 0.0) -> float:
    value = max(0.0, _as_float(primary, 0.0))
    return value if value > 0 else max(0.0, _as_float(fallback, 0.0))


def _challenge_action_vote(challenge: dict, final_vote: dict, decision_stage: str, default_policy: str) -> dict:
    vote = _fallback_vote("challenge_error", decision_stage=decision_stage, default_policy=default_policy)
    vote.update(
        {
            "action": str(final_vote.get("action") or "HOLD").upper(),
            "hold_mode": str(final_vote.get("hold_mode", "") or ""),
            "confidence": max(0.0, min(1.0, _as_float((challenge or {}).get("confidence"), final_vote.get("confidence", 0.0)))),
            "trail_pct": float(final_vote.get("trail_pct", 0.03) or 0.03),
            "sell_urgency": str(final_vote.get("sell_urgency", "wait") or "wait"),
            "revised_sell_target": float(final_vote.get("revised_sell_target", 0.0) or 0.0),
            "protective_stop": float(final_vote.get("protective_stop", 0.0) or 0.0),
            "hard_stop": float(final_vote.get("hard_stop", 0.0) or 0.0),
            "recover_above": float(final_vote.get("recover_above", 0.0) or 0.0),
            "recovery_watch_min": int(final_vote.get("recovery_watch_min", 0) or 0),
            "valid_for_min": int(final_vote.get("valid_for_min", 0) or 0),
            "reask_after_min": int(final_vote.get("reask_after_min", 0) or 0),
            "reask_drawdown_from_peak_pct": float(final_vote.get("reask_drawdown_from_peak_pct", 0.0) or 0.0),
            "reask_if_price_above": float(final_vote.get("reask_if_price_above", 0.0) or 0.0),
            "max_rechecks": int(final_vote.get("max_rechecks", 0) or 0),
            "next_review_min": int(final_vote.get("next_review_min", 30) or 30),
            "invalid_if": str(final_vote.get("invalid_if", "") or "")[:240],
            "reason": str(final_vote.get("reason", "") or "")[:500],
            "fallback": bool((challenge or {}).get("parse_error", False)),
            "duration_ms": int((challenge or {}).get("duration_ms", 0) or 0),
        }
    )
    return vote


def _triage_compatibility_votes(
    final_vote: dict,
    triage: dict,
    challenge: Optional[dict],
    decision_stage: str,
    default_policy: str,
) -> dict:
    source = "triage_challenge" if challenge is not None else "triage"
    vote = _fallback_vote("triage_shim", decision_stage=decision_stage, default_policy=default_policy)
    vote.update(
        {
            "action": str((final_vote or {}).get("action") or "HOLD").upper(),
            "hold_mode": str((final_vote or {}).get("hold_mode", "") or ""),
            "confidence": max(0.0, min(1.0, _as_float((final_vote or {}).get("confidence"), 0.0))),
            "trail_pct": float((final_vote or {}).get("trail_pct", 0.03) or 0.03),
            "sell_urgency": str((final_vote or {}).get("sell_urgency", "wait") or "wait"),
            "revised_sell_target": float((final_vote or {}).get("revised_sell_target", 0.0) or 0.0),
            "protective_stop": float((final_vote or {}).get("protective_stop", 0.0) or 0.0),
            "hard_stop": float((final_vote or {}).get("hard_stop", 0.0) or 0.0),
            "recover_above": float((final_vote or {}).get("recover_above", 0.0) or 0.0),
            "recovery_watch_min": int((final_vote or {}).get("recovery_watch_min", 0) or 0),
            "valid_for_min": int((final_vote or {}).get("valid_for_min", 0) or 0),
            "reask_after_min": int((final_vote or {}).get("reask_after_min", 0) or 0),
            "reask_drawdown_from_peak_pct": float((final_vote or {}).get("reask_drawdown_from_peak_pct", 0.0) or 0.0),
            "reask_if_price_above": float((final_vote or {}).get("reask_if_price_above", 0.0) or 0.0),
            "max_rechecks": int((final_vote or {}).get("max_rechecks", 0) or 0),
            "next_review_min": int((final_vote or {}).get("next_review_min", 30) or 30),
            "invalid_if": str((final_vote or {}).get("invalid_if", "") or "")[:240],
            "reason": str((final_vote or {}).get("reason", "") or "")[:500],
            "fallback": bool((final_vote or {}).get("fallback", False)),
            "duration_ms": int((final_vote or {}).get("duration_ms", 0) or 0),
            "exit_category": str((final_vote or {}).get("exit_category", "") or ""),
            "exit_driver": str((final_vote or {}).get("exit_driver", "") or ""),
            "source": "triage_shim",
            "triage_shim": True,
            "triage_vote_source": source,
            "triage_confidence": max(0.0, min(1.0, _as_float((triage or {}).get("confidence"), 0.0))),
            "challenge_confidence": max(0.0, min(1.0, _as_float((challenge or {}).get("confidence"), 0.0))) if challenge is not None else 0.0,
            "challenge_field_gaps": list((final_vote or {}).get("challenge_field_gaps") or []),
        }
    )
    return {
        role: {**vote, "analyst_role": role, "triage_shim_role": role}
        for role in ("bull", "bear", "neutral")
    }


def _merge_triage_challenge_vote(
    triage: dict,
    challenge: Optional[dict],
    decision_stage: str,
    default_policy: str,
    second_opinion_reason: str,
) -> dict:
    if not challenge:
        final_vote = dict(triage or {})
        final_vote["second_opinion_used"] = False
        final_vote["second_opinion_reason"] = ""
        return final_vote

    if bool((challenge or {}).get("parse_error", False)):
        final_vote = _fallback_triage("challenge_error", decision_stage, default_policy)
        final_vote.update(
            {
                "second_opinion_used": True,
                "second_opinion_reason": second_opinion_reason or "challenge_error",
                "challenge_confirm": False,
                "challenge_final_category": "HOLD",
                "challenge_confidence": 0.0,
                "challenge_prompt_version": (challenge or {}).get("challenge_prompt_version", CHALLENGE_PROMPT_VERSION),
                "challenge_parse_error": True,
                "challenge_field_gaps": list((challenge or {}).get("challenge_field_gaps") or ["challenge_parse_error"]),
                "challenge_reason": str((challenge or {}).get("reason", "") or "")[:500],
                "triage": triage,
                "challenge": challenge,
            }
        )
        return final_vote

    final_category = _normalize_triage_category((challenge or {}).get("final_category") or (triage or {}).get("exit_category"))
    action = "HOLD" if final_category == "HOLD" else "SELL"
    challenge_confidence = max(0.0, min(1.0, _as_float((challenge or {}).get("confidence"), 0.0)))
    triage_confidence = max(0.0, min(1.0, _as_float((triage or {}).get("confidence"), 0.0)))
    confidence = challenge_confidence if challenge_confidence > 0 else triage_confidence
    hold_mode = _normalize_hold_mode((challenge or {}).get("hold_mode")) or str((triage or {}).get("hold_mode", "") or "")
    sell_urgency = str((challenge or {}).get("sell_urgency") or (triage or {}).get("sell_urgency") or "wait").strip().lower()
    if sell_urgency not in {"now", "next_open", "wait"}:
        sell_urgency = "wait" if action == "HOLD" else "now"
    reason = str((challenge or {}).get("reason") or (triage or {}).get("reason") or "")[:500]
    invalid_if = str((challenge or {}).get("invalid_if") or (triage or {}).get("invalid_if") or "")[:240]
    if action == "HOLD" and not invalid_if:
        invalid_if = str((challenge or {}).get("minimum_condition_to_hold") or "")[:240]

    final_vote = dict(triage or {})
    final_vote.update(
        {
            "action": action,
            "hold_mode": hold_mode if action == "HOLD" else "",
            "confidence": confidence,
            "sell_urgency": sell_urgency if action == "SELL" else "wait",
            "protective_stop": _positive_or_fallback((challenge or {}).get("protective_stop"), (triage or {}).get("protective_stop")),
            "hard_stop": _positive_or_fallback((challenge or {}).get("hard_stop"), (triage or {}).get("hard_stop")),
            "recover_above": _positive_or_fallback((challenge or {}).get("recover_above"), (triage or {}).get("recover_above")),
            "next_review_min": max(5, min(240, _as_int((challenge or {}).get("next_review_min"), (triage or {}).get("next_review_min", 30)))),
            "invalid_if": invalid_if,
            "reason": reason,
            "exit_category": final_category,
            "triage_confidence": triage_confidence,
            "second_opinion_used": True,
            "second_opinion_reason": second_opinion_reason,
            "challenge_confirm": bool((challenge or {}).get("confirm", False)),
            "challenge_final_category": final_category,
            "challenge_confidence": challenge_confidence,
            "challenge_prompt_version": (challenge or {}).get("challenge_prompt_version", CHALLENGE_PROMPT_VERSION),
            "challenge_parse_error": False,
            "challenge_field_gaps": list((challenge or {}).get("challenge_field_gaps") or []),
            "challenge_reason": str((challenge or {}).get("reason", "") or "")[:500],
            "risk_if_wrong": str((challenge or {}).get("risk_if_wrong", "") or "")[:500],
            "minimum_condition_to_hold": str((challenge or {}).get("minimum_condition_to_hold", "") or "")[:500],
            "triage": triage,
            "challenge": challenge,
        }
    )
    return final_vote


def _origin_sell_category(final_vote: dict) -> str:
    triage_payload = (final_vote or {}).get("triage")
    if not isinstance(triage_payload, dict):
        triage_payload = {}
    category = str(triage_payload.get("exit_category") or "").upper()
    action = str(triage_payload.get("action") or "").upper()
    if category in {"SELL", "STOP_LOSS"}:
        return category
    if action == "SELL":
        return "SELL"
    return ""


def _hold_boundary_invalid_fallback(final_vote: dict, decision_stage: str, default_policy: str) -> dict:
    guarded = dict(final_vote or {})
    triage_payload = guarded.get("triage") if isinstance(guarded.get("triage"), dict) else {}
    original_reason = str(guarded.get("reason", "") or "")[:500]
    origin_category = _origin_sell_category(guarded) or "SELL"
    guarded.update(
        {
            "action": "SELL",
            "hold_mode": "",
            "sell_urgency": "now",
            "exit_category": origin_category,
            "exit_driver": str(triage_payload.get("exit_driver") or guarded.get("exit_driver") or "other"),
            "reason": "hold_boundary_invalid",
            "hold_boundary_invalid": True,
            "boundary_invalid_original_action": "HOLD",
            "boundary_invalid_original_reason": original_reason,
            "decision_stage": _normalize_stage(decision_stage),
            "default_policy": default_policy or _stage_policy(decision_stage),
        }
    )
    return guarded


def _result_from_triage(
    ticker: str,
    market: str,
    pos: dict,
    triage: dict,
    decision_stage: str,
    default_policy: str,
    duration_ms: int,
    challenge: Optional[dict] = None,
    second_opinion_reason: str = "",
) -> dict:
    triage_missing = triage is None
    triage_payload = dict(triage or {})
    if triage_missing:
        triage_payload = _fallback_triage("triage_missing", decision_stage, default_policy)
        triage_payload["triage_missing"] = True
    challenge_payload = dict(challenge or {}) if challenge is not None else None
    final_vote = _merge_triage_challenge_vote(
        triage_payload,
        challenge_payload,
        decision_stage,
        default_policy,
        second_opinion_reason,
    )
    if (
        _normalize_stage(decision_stage) == "AUTO_SELL_REVIEW"
        and not _triage_hold_boundary_valid(final_vote)
        and (_origin_sell_category(final_vote) or not bool(final_vote.get("triage_parse_error", False)))
    ):
        final_vote = _hold_boundary_invalid_fallback(final_vote, decision_stage, default_policy)
    action = str(final_vote.get("action") or "HOLD").upper()
    votes = _triage_compatibility_votes(final_vote, triage_payload, challenge_payload, decision_stage, default_policy)
    advisor_fallback = bool(
        final_vote.get("fallback", False)
        or triage_payload.get("fallback", False)
        or triage_payload.get("triage_parse_error", False)
        or final_vote.get("triage_parse_error", False)
        or final_vote.get("challenge_parse_error", False)
        or (challenge_payload is not None and bool(challenge_payload.get("parse_error", False)))
        or triage_missing
    )
    decision_source = (
        "hold_advisor_fallback"
        if advisor_fallback
        else "hold_advisor_triage_challenge"
        if challenge_payload is not None
        else "hold_advisor_triage"
    )
    _log_decision(
        ticker,
        market,
        pos,
        action,
        float(final_vote.get("trail_pct", 0.03) or 0.03),
        votes,
        decision_stage,
        default_policy,
        duration_ms=duration_ms,
        triage=final_vote,
    )
    return {
        "action": action,
        "hold_mode": str(final_vote.get("hold_mode", "") or "") if action == "HOLD" else "",
        "trail_pct": round(float(final_vote.get("trail_pct", 0.03) or 0.03), 3),
        "votes": votes,
        "confidence": round(float(final_vote.get("confidence", 0.0) or 0.0), 4),
        "sell_urgency": str(final_vote.get("sell_urgency", "wait") or "wait"),
        "revised_sell_target": float(final_vote.get("revised_sell_target", 0.0) or 0.0),
        "protective_stop": float(final_vote.get("protective_stop", 0.0) or 0.0),
        "hard_stop": float(final_vote.get("hard_stop", 0.0) or 0.0),
        "recover_above": float(final_vote.get("recover_above", 0.0) or 0.0),
        "recovery_watch_min": int(final_vote.get("recovery_watch_min", 0) or 0),
        "valid_for_min": int(final_vote.get("valid_for_min", 0) or 0),
        "reask_after_min": int(final_vote.get("reask_after_min", 0) or 0),
        "reask_drawdown_from_peak_pct": float(final_vote.get("reask_drawdown_from_peak_pct", 0.0) or 0.0),
        "reask_if_price_above": float(final_vote.get("reask_if_price_above", 0.0) or 0.0),
        "max_rechecks": int(final_vote.get("max_rechecks", 0) or 0),
        "next_review_min": int(final_vote.get("next_review_min", 30) or 30),
        "reason": str(final_vote.get("reason", "") or "")[:500],
        "invalid_if": str(final_vote.get("invalid_if", "") or "")[:240],
        "decision_stage": decision_stage,
        "default_policy": default_policy,
        "duration_ms": duration_ms,
        "exit_category": str(final_vote.get("exit_category", "") or ""),
        "exit_driver": str(final_vote.get("exit_driver", "") or ""),
        "triage_confidence": round(float(final_vote.get("triage_confidence", triage_payload.get("confidence", 0.0)) or 0.0), 4),
        "second_opinion_used": bool(final_vote.get("second_opinion_used", False)),
        "second_opinion_reason": str(final_vote.get("second_opinion_reason", "") or ""),
        "triage_prompt_version": TRIAGE_PROMPT_VERSION,
        "triage_parse_error": bool(triage_payload.get("triage_parse_error", False)),
        "challenge": challenge_payload,
        "challenge_prompt_version": final_vote.get("challenge_prompt_version", ""),
        "challenge_parse_error": bool(final_vote.get("challenge_parse_error", False)),
        "challenge_final_category": final_vote.get("challenge_final_category", ""),
        "challenge_confidence": round(float(final_vote.get("challenge_confidence", 0.0) or 0.0), 4),
        "challenge_field_gaps": list(final_vote.get("challenge_field_gaps") or []),
        "triage": triage_payload,
        "triage_missing": bool(final_vote.get("triage_missing", triage_payload.get("triage_missing", False))),
        "fallback": advisor_fallback,
        "hold_advisor_fallback": advisor_fallback,
        "decision_source": decision_source,
        "hold_advisor_decision_source": decision_source,
        "fallback_reason": str(final_vote.get("reason", "") or "")[:200] if advisor_fallback else "",
        "triage_vote_trace": {
            "vote_contract": "triage_shim_v1",
            "triage": triage_payload,
            "challenge": challenge_payload,
            "final": final_vote,
        },
        "hold_boundary_invalid": bool(final_vote.get("hold_boundary_invalid", False)),
        "boundary_invalid_original_reason": str(final_vote.get("boundary_invalid_original_reason", "") or "")[:500],
    }


def _ask_one(analyst_type: str, pos: dict, market: str,
             digest_prompt: str, rt_context: str = "",
             decision_stage: str = "TP_REVIEW",
             default_policy: Optional[str] = None,
             minutes_to_close: Optional[float] = None,
             force_exit_window: bool = False) -> dict:
    decision_stage = _normalize_stage(decision_stage)
    default_policy_text = _stage_policy(decision_stage, default_policy)
    # entry: open_positions(KRW) 우선, 없으면 display_avg_price(USD) 폴백
    entry = float(pos.get("entry", 0) or 0)
    if entry <= 0:
        entry = float(pos.get("avg_price", 0) or pos.get("display_avg_price", 0) or 0)
    if entry <= 0:
        raise ValueError(f"[hold_advisor] entry=0 — 진입가 미확정, 호출 불가 ({pos.get('ticker','-')})")

    # US: display_avg_price(USD) 기준으로 표시, KRW 환산값을 괄호에 병기
    # KR: KRW 단위 그대로 표시
    disp_entry = float(pos.get("display_avg_price", 0) or 0)
    disp_cp    = float(pos.get("display_current_price", 0) or 0)
    # USD/KRW 환율: entry(KRW) / disp_entry(USD)로 역산
    fx_rate = (entry / disp_entry) if (market == "US" and disp_entry > 0) else 0.0

    if market == "US" and disp_entry > 0:
        show_entry = disp_entry
        show_cp    = disp_cp if disp_cp > 0 else float(pos.get("current_price", entry) or entry) / fx_rate
        show_tp    = round(float(pos.get("tp", 0) or 0) / fx_rate, 2)
        show_sl    = round(float(pos.get("sl", 0) or 0) / fx_rate, 2)
        show_trail = round(float(pos.get("trail_sl", 0) or 0) / fx_rate, 2)
        ccy = "USD"
        # KRW 환산값 (괄호 병기용)
        krw_entry  = int(entry)
        krw_cp     = int(show_cp * fx_rate)
        krw_tp     = int(float(pos.get("tp", 0) or 0))
        krw_sl     = int(float(pos.get("sl", 0) or 0))
        krw_trail  = int(float(pos.get("trail_sl", 0) or 0))
        def _p(usd, krw): return f"${usd:,.2f} (≈{krw:,}원)" if krw > 0 else f"${usd:,.2f}"
    else:
        show_entry = entry
        show_cp    = float(pos.get("current_price", entry) or entry)
        show_tp    = float(pos.get("tp", 0) or 0)
        show_sl    = float(pos.get("sl", 0) or 0)
        show_trail = float(pos.get("trail_sl", 0) or 0)
        ccy = "KRW"
        krw_entry = krw_cp = krw_tp = krw_sl = krw_trail = 0
        def _p(val, _krw=0): return f"{val:,.0f}원"

    cp      = show_cp
    pnl_pct = (show_cp / show_entry - 1) * 100 if show_entry else 0
    ticker  = pos.get("ticker", "-")
    strat   = pos.get("strategy", "-")
    held    = pos.get("held_days", 0)
    # 장중 보유시간(분) 계산
    held_min: Optional[int] = None
    _entry_time = pos.get("entry_time")
    if _entry_time:
        try:
            from datetime import datetime as _dt
            _et = _dt.fromisoformat(_entry_time)
            _now = _dt.now(_et.tzinfo) if _et.tzinfo is not None else _dt.now()
            held_min = max(0, int((_now - _et).total_seconds() / 60))
        except Exception:
            pass
    peak_pnl_pct = float(pos.get("peak_pnl_pct") or 0)
    mode_str = pos.get("mode", "")
    tp      = show_tp
    sl      = show_sl
    trailing = bool(pos.get("trailing", False))
    trail_sl = show_trail
    tp_triggered = bool(pos.get("tp_triggered", False))
    status_bits = []
    if tp > 0:
        status_bits.append(f"TP={_p(tp, krw_tp)}")
    if sl > 0:
        status_bits.append(f"SL={_p(sl, krw_sl)}")
    if tp_triggered:
        status_bits.append("TP 도달 상태")
    if trailing:
        _tr_str = f"트레일링 활성(trail_sl={_p(trail_sl, krw_trail)})" if trail_sl > 0 else "트레일링 활성"
        status_bits.append(_tr_str)
    status_line = " / ".join(status_bits) if status_bits else "별도 TP/SL 상태 정보 없음"

    # 진입가/현재가 표시 (US: USD + KRW 병기)
    if market == "US":
        entry_str = _p(show_entry, krw_entry)
        cp_str    = _p(show_cp, krw_cp)
    else:
        entry_str = f"{show_entry:,.0f}원"
        cp_str    = f"{show_cp:,.0f}원"

    # 보유시간 표시: 장중이면 분 단위, 아니면 일 단위
    if held_min is not None:
        held_str = f"{held_min}분" if held_min < 1440 else f"{held}일 {held_min % 1440}분"
    else:
        held_str = f"{held}일"
    # 고점 대비 현재 이격
    drawdown_str = ""
    if peak_pnl_pct > 0 and pnl_pct < peak_pnl_pct:
        dd = peak_pnl_pct - pnl_pct
        drawdown_str = f"  고점 수익률: {peak_pnl_pct:+.2f}%  (현재 고점 대비 -{dd:.2f}%p 하락)\n"
    elif peak_pnl_pct > 0:
        drawdown_str = f"  고점 수익률: {peak_pnl_pct:+.2f}%  (현재 고점 유지)\n"
    mode_line = f"  시장 모드: {mode_str}\n" if mode_str else ""
    advisor_ctx = pos.get("advisor_context_v2") or {}
    advisor_context_text = ""
    if isinstance(advisor_ctx, dict) and advisor_ctx:
        ctx_lines = []

        def _ctx_add(label: str, value) -> None:
            if value in (None, "", []):
                return
            ctx_lines.append(f"  {label}: {value}")

        _ctx_add("entry thesis", advisor_ctx.get("selected_reason"))
        _ctx_add("original entry thesis", advisor_ctx.get("original_selected_reason"))
        _ctx_add("source type", advisor_ctx.get("source_type"))
        _ctx_add("entry route", advisor_ctx.get("entry_route") or advisor_ctx.get("route_source"))
        _ctx_add("session phase", advisor_ctx.get("session_phase"))
        _ctx_add("regular open at", advisor_ctx.get("regular_open_at"))
        _ctx_add("minutes to regular open", advisor_ctx.get("minutes_to_regular_open"))
        _ctx_add("OR status reason", advisor_ctx.get("or_status_reason"))
        _ctx_add("premarket quote quality", advisor_ctx.get("premarket_quote_quality"))
        _ctx_add("bid ask spread pct", advisor_ctx.get("bid_ask_spread_pct"))
        _ctx_add("last quote age sec", advisor_ctx.get("last_quote_age_sec"))
        _ctx_add("hold minutes", advisor_ctx.get("hold_min_since_entry"))
        _ctx_add("OR formed", advisor_ctx.get("or_formed"))
        if advisor_ctx.get("or_high") or advisor_ctx.get("or_low"):
            _ctx_add("OR high/low", f"{advisor_ctx.get('or_high')} / {advisor_ctx.get('or_low')}")
        _ctx_add("entry vs OR high pct", advisor_ctx.get("entry_vs_or_high_pct"))
        _ctx_add("hard stop distance pct", advisor_ctx.get("hard_stop_distance_pct"))
        _ctx_add("exit signal severity", advisor_ctx.get("exit_signal_severity"))
        _ctx_add("exit signal reason", advisor_ctx.get("exit_signal_reason"))
        _ctx_add("exit signal stop/distance", f"{advisor_ctx.get('exit_signal_stop_price')} / {advisor_ctx.get('exit_signal_stop_distance_pct')}")
        _ctx_add("recover above", advisor_ctx.get("recover_above"))
        _ctx_add("opening recheck deadline", advisor_ctx.get("opening_recheck_deadline"))
        if advisor_ctx.get("selection_reference_target") or advisor_ctx.get("selection_reference_stop"):
            _ctx_add("selection target/stop", f"{advisor_ctx.get('selection_reference_target')} / {advisor_ctx.get('selection_reference_stop')}")
        if advisor_ctx.get("pathb_reference_target") or advisor_ctx.get("pathb_reference_stop"):
            _ctx_add("pathb target/stop", f"{advisor_ctx.get('pathb_reference_target')} / {advisor_ctx.get('pathb_reference_stop')}")
        if advisor_ctx.get("pathb_plan_target") or advisor_ctx.get("pathb_plan_stop"):
            _ctx_add("pathb plan target/stop", f"{advisor_ctx.get('pathb_plan_target')} / {advisor_ctx.get('pathb_plan_stop')}")
        _ctx_add("invalid if", advisor_ctx.get("invalid_if"))
        _ctx_add("pending intraday recheck", advisor_ctx.get("pending_intraday_recheck"))
        if ctx_lines:
            advisor_context_text = "\n━━━ Execution Context V2 ━━━\n" + "\n".join(ctx_lines) + "\n"

    position_news_text = _position_news_context_text(pos)
    context_text = rt_context or (digest_prompt[:300] if digest_prompt else "  (정보 없음)")
    if minutes_to_close is None:
        minutes_to_close_text = "unknown"
    else:
        try:
            minutes_to_close_text = f"{float(minutes_to_close):.1f}".rstrip("0").rstrip(".")
        except Exception:
            minutes_to_close_text = "unknown"
    lead_text = STAGE_LEAD_TEXT.get(decision_stage, STAGE_LEAD_TEXT["MANUAL_REVIEW"]).format(
        minutes_to_close=minutes_to_close_text
    )
    if force_exit_window:
        lead_text += " 현재 force-exit window이면 HOLD는 시스템 강제청산을 override하지 못합니다."

    prompt = f"""{PERSONAS[analyst_type]}

{COMMON_DECISION_CONTRACT}
{HARD_SOFT_RULE_CONTRACT}

{lead_text}

━━━ 포지션 ━━━
  종목: {ticker} ({market}, {ccy})  전략: {strat}
  진입가: {entry_str}  현재가: {cp_str}  수익률: {pnl_pct:+.2f}%
  보유시간: {held_str}
{drawdown_str}{mode_line}  포지션 상태: {status_line}
{advisor_context_text}{position_news_text}

━━━ 현재 시장 (실시간) ━━━
{context_text}

HOLD(보유) 또는 SELL(청산) 중 하나를 선택하고,
HOLD 시 트레일링 폭(trail_pct: 0.02~0.05)을 제안하세요.

Decision stage:
- decision_stage: {decision_stage}
- default_policy: {default_policy_text}
- minutes_to_close: {minutes_to_close if minutes_to_close is not None else "unknown"}
- force_exit_window: {bool(force_exit_window)}
- Catastrophic exits such as daily loss halt, broker mismatch, operator kill, and emergency close override HOLD.
- Reviewable exits such as loss_cap, stop_loss, trail_stop, and profit_floor should be re-judged from the current thesis and risk/reward.
- HOLD is valid only when the thesis is still intact, risk is bounded, and protective_stop / invalid_if / next_review_min are explicit.
- If action is HOLD during AUTO_SELL_REVIEW, set hold_mode to one of:
  target_extension, profit_pullback, stop_recovery, loss_deferral.
- Prefer target_extension for take-profit target extension, profit_pullback for profitable trail/profit-floor pullbacks,
  stop_recovery for stop/loss-cap recovery attempts, and loss_deferral only for weak loss-side deferrals.

Perspective focus:
{PERSONA_FOCUS.get(analyst_type, "")}

{TRAIL_GUIDE}

JSON으로만 응답:
{{
  "action": "HOLD" or "SELL",
  "hold_mode": "target_extension|profit_pullback|stop_recovery|loss_deferral",
  "confidence": 0.0~1.0,
  "sell_urgency": "now|next_open|wait",
  "trail_pct": 0.03,
  "revised_sell_target": 0.0,
  "protective_stop": 0.0,
  "hard_stop": 0.0,
  "recover_above": 0.0,
  "recovery_watch_min": 0,
  "valid_for_min": 10,
  "reask_after_min": 0,
  "reask_drawdown_from_peak_pct": 0.8,
  "reask_if_price_above": 0.0,
  "max_rechecks": 2,
  "next_review_min": 30,
  "invalid_if": "price loses VWAP",
  "reason": "한 문장"
}}"""

    call_started = time.perf_counter()
    completeness = _input_completeness(pos, market, decision_stage, rt_context or digest_prompt, minutes_to_close)
    revenue_context = _pathb_revenue_path_context(pos, decision_stage, default_policy_text, minutes_to_close)
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        duration_ms = int(max(0.0, (time.perf_counter() - call_started) * 1000.0))
        raw = resp.content[0].text.strip()
        result = extract_json(raw)
        credit_record(
            resp.usage.input_tokens, resp.usage.output_tokens, "hold_advisor", model=MODEL,
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
        _cm = claude_response_meta(resp)
        save_raw_call(
            label=f"hold_advisor_{analyst_type}",
            prompt=prompt, raw_response=raw, parsed=result,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market,
            model=MODEL,
            prompt_version="hold_advisor_v3",
            duration_ms=duration_ms,
            cache_creation_input_tokens=_cm["cache_creation_input_tokens"],
            cache_read_input_tokens=_cm["cache_read_input_tokens"],
            request_id=_cm["request_id"],
            service_tier=_cm["service_tier"],
            extra={
                "decision_stage": decision_stage,
                "default_policy": default_policy_text,
                "advisor_context_v2": advisor_ctx if isinstance(advisor_ctx, dict) else {},
                "input_completeness": completeness,
                "pathb_revenue_path_context": revenue_context,
            },
        )
        vote = _coerce_vote(result, decision_stage=decision_stage, default_policy=default_policy_text)
        vote["duration_ms"] = duration_ms
        return vote
    except Exception as e:
        duration_ms = int(max(0.0, (time.perf_counter() - call_started) * 1000.0))
        log.warning(f"[hold_advisor:{analyst_type}] 오류 → HOLD fallback: {e}")
        vote = _fallback_vote("error", decision_stage=decision_stage, default_policy=default_policy_text)
        vote["duration_ms"] = duration_ms
        return vote


def ask(
    pos: dict,
    market: str,
    digest_prompt: str = "",
    delay: float = 0.5,
    decision_stage: str = "TP_REVIEW",
    default_policy: Optional[str] = None,
    minutes_to_close: Optional[float] = None,
    force_exit_window: bool = False,
) -> dict:
    """
    분석가 3명 합의 → HOLD/SELL 결정.

    Returns
    -------
    {
        "action": "HOLD" | "SELL",
        "trail_pct": 0.03,
        "votes": {"bull": ..., "bear": ..., "neutral": ...},
    }
    """
    ticker  = pos.get("ticker", "-")
    decision_stage = _normalize_stage(decision_stage or pos.get("decision_stage"))
    default_policy_text = _stage_policy(decision_stage, default_policy or pos.get("default_policy"))
    if minutes_to_close is None and pos.get("minutes_to_close") not in (None, ""):
        try:
            minutes_to_close = float(pos.get("minutes_to_close"))
        except Exception:
            minutes_to_close = None

    # entry=0이면 Claude가 "데이터 오류"로 일관되게 SELL 판단 → 의미없는 호출 차단
    _entry = float(pos.get("entry", 0) or 0)
    if _entry <= 0:
        _entry = float(pos.get("avg_price", 0) or pos.get("display_avg_price", 0) or 0)
    if _entry <= 0:
        log.warning(f"[hold_advisor] {ticker} entry=0 → 호출 차단 (진입가 미확정), HOLD 반환")
        return {
            "action": "HOLD",
            "hold_mode": "",
            "trail_pct": 0.03,
            "votes": {},
            "confidence": 0.0,
            "decision_stage": decision_stage,
            "default_policy": default_policy_text,
        }

    # 실시간 컨텍스트 1회만 조회 (3명이 공유)
    rt_ctx = ""
    if _RT_CTX_AVAILABLE:
        try:
            result = _build_rt_ctx(market)
            if isinstance(result, dict) and result.get("ok"):
                rt_ctx = result["text"]
        except Exception:
            pass

    advisor_started = time.perf_counter()
    triage_vote = None
    if _triage_production_enabled(decision_stage):
        triage_vote = _ask_triage(
            pos,
            market,
            digest_prompt,
            rt_ctx,
            decision_stage,
            default_policy_text,
            minutes_to_close,
            force_exit_window,
            prompt_mode="production",
        )
        if _triage_direct_allowed(triage_vote, decision_stage):
            advisor_duration_ms = int(max(0.0, (time.perf_counter() - advisor_started) * 1000.0))
            _skip_reason = "non_stop_sell_high_conf" if (
                str(triage_vote.get("exit_category") or "") == "SELL"
                and str(triage_vote.get("exit_driver") or "") in {"profit_protection", "time_carry", "other"}
                and _env_bool("HOLD_ADVISOR_TRIAGE_NON_STOP_SELL_ENABLED", False)
            ) else "direct_allowed"
            log.info(
                f"[hold_advisor triage direct] {ticker} -> {triage_vote.get('exit_category')} "
                f"action={triage_vote.get('action')} driver={triage_vote.get('exit_driver')} "
                f"conf={float(triage_vote.get('confidence', 0.0) or 0.0):.2f} skip_reason={_skip_reason}"
            )
            return _result_from_triage(
                ticker,
                market,
                pos,
                triage_vote,
                decision_stage,
                default_policy_text,
                advisor_duration_ms,
            )
        second_opinion_reason = _triage_second_opinion_reason(triage_vote, decision_stage) or "triage_requires_challenge"
        challenge_vote = None
        triage_parse_error = bool(triage_vote.get("triage_parse_error", False) or triage_vote.get("fallback", False))
        if not triage_parse_error and _triage_challenge_enabled(decision_stage):
            challenge_vote = _ask_challenge(
                pos,
                market,
                digest_prompt,
                rt_ctx,
                decision_stage,
                default_policy_text,
                minutes_to_close,
                force_exit_window,
                triage_vote,
            )
            if not bool(challenge_vote.get("parse_error", False)) or not _triage_legacy_fallback_enabled():
                advisor_duration_ms = int(max(0.0, (time.perf_counter() - advisor_started) * 1000.0))
                log.info(
                    f"[hold_advisor triage+challenge] {ticker} -> "
                    f"triage={triage_vote.get('exit_category')} final={challenge_vote.get('final_category')} "
                    f"reason={second_opinion_reason}"
                )
                return _result_from_triage(
                    ticker,
                    market,
                    pos,
                    triage_vote,
                    decision_stage,
                    default_policy_text,
                    advisor_duration_ms,
                    challenge=challenge_vote,
                    second_opinion_reason=second_opinion_reason,
                )
        if not _triage_legacy_fallback_enabled():
            advisor_duration_ms = int(max(0.0, (time.perf_counter() - advisor_started) * 1000.0))
            if triage_parse_error:
                safe_vote = triage_vote
            else:
                safe_vote = _fallback_triage(f"challenge_unavailable:{second_opinion_reason}", decision_stage, default_policy_text)
                safe_vote["triage"] = triage_vote
            return _result_from_triage(
                ticker,
                market,
                pos,
                safe_vote,
                decision_stage,
                default_policy_text,
                advisor_duration_ms,
            )

    shadow_triage = None
    if triage_vote is None and _triage_shadow_allowed(decision_stage):
        shadow_triage = _ask_triage(
            pos,
            market,
            digest_prompt,
            rt_ctx,
            decision_stage,
            default_policy_text,
            minutes_to_close,
            force_exit_window,
            prompt_mode="shadow",
        )

    votes   = {}
    for atype in ("bull", "bear", "neutral"):
        votes[atype] = _ask_one(
            atype,
            pos,
            market,
            digest_prompt,
            rt_ctx,
            decision_stage=decision_stage,
            default_policy=default_policy_text,
            minutes_to_close=minutes_to_close,
            force_exit_window=force_exit_window,
        )
        time.sleep(delay)

    hold_score = sum(
        v["confidence"] for v in votes.values() if v["action"] == "HOLD"
    )
    sell_score = sum(
        v["confidence"] for v in votes.values() if v["action"] == "SELL"
    )
    action = "SELL" if sell_score > hold_score and sell_score >= 0.7 else "HOLD"

    # trail_pct: HOLD 투표한 분석가들의 평균
    hold_voters = [v for v in votes.values() if v["action"] == "HOLD"]
    trail_pct   = (
        sum(v["trail_pct"] for v in hold_voters) / len(hold_voters)
        if hold_voters else 0.03
    )
    action_voters = [v for v in votes.values() if v["action"] == action]
    confidence = max((float(v.get("confidence", 0.0) or 0.0) for v in action_voters), default=0.0)
    sell_urgency = "wait"
    if action == "SELL":
        urgencies = [str(v.get("sell_urgency", "") or "") for v in action_voters]
        sell_urgency = "now" if "now" in urgencies else ("next_open" if "next_open" in urgencies else "wait")
    def _positive_float_votes(field, source=None):
        values = []
        for vote in source or list(votes.values()):
            try:
                value = float(vote.get(field, 0.0) or 0.0)
            except Exception:
                value = 0.0
            if value > 0:
                values.append(value)
        return values

    def _positive_int_votes(field, source=None):
        values = []
        for vote in source or list(votes.values()):
            try:
                value = int(float(vote.get(field, 0) or 0))
            except Exception:
                value = 0
            if value > 0:
                values.append(value)
        return values

    # Lower revised targets are the conservative aggregation: they lock gains earlier
    # when analysts disagree on how far to extend a target.
    revised_target_values = _positive_float_votes("revised_sell_target", hold_voters or action_voters)
    revised_sell_target = min(revised_target_values) if revised_target_values else 0.0
    protective_stop = max((float(v.get("protective_stop", 0.0) or 0.0) for v in votes.values()), default=0.0)
    hard_stop = max(_positive_float_votes("hard_stop"), default=0.0)
    recover_above = max(_positive_float_votes("recover_above"), default=0.0)
    recovery_watch_min = min(_positive_int_votes("recovery_watch_min"), default=0)
    valid_for_min = min(_positive_int_votes("valid_for_min"), default=0)
    reask_after_min = min(_positive_int_votes("reask_after_min"), default=0)
    reask_drawdown_from_peak_pct = min(_positive_float_votes("reask_drawdown_from_peak_pct"), default=0.0)
    reask_if_price_above = min(_positive_float_votes("reask_if_price_above"), default=0.0)
    max_rechecks = min(_positive_int_votes("max_rechecks"), default=0)
    next_review_min = min((int(v.get("next_review_min", 30) or 30) for v in votes.values()), default=30)
    reason = ""
    invalid_if = ""
    hold_mode = ""
    for vote in action_voters:
        if action == "HOLD" and not hold_mode and vote.get("hold_mode"):
            hold_mode = _normalize_hold_mode(vote.get("hold_mode"))
        if not reason and vote.get("reason"):
            reason = str(vote.get("reason", ""))[:500]
        if not invalid_if and vote.get("invalid_if"):
            invalid_if = str(vote.get("invalid_if", ""))[:240]

    log.info(
        f"[hold_advisor] {ticker} → {action} "
        f"(HOLD {hold_score:.2f} vs SELL {sell_score:.2f}) trail={trail_pct:.2f}"
    )

    # ── 결정 시점 JSONL 기록 ──────────────────────────────────────────────────
    advisor_duration_ms = int(max(0.0, (time.perf_counter() - advisor_started) * 1000.0))
    _log_decision(
        ticker,
        market,
        pos,
        action,
        trail_pct,
        votes,
        decision_stage,
        default_policy_text,
        duration_ms=advisor_duration_ms,
        triage=triage_vote,
        shadow_triage=shadow_triage,
    )

    result_payload = {
        "action": action,
        "hold_mode": hold_mode if action == "HOLD" else "",
        "trail_pct": round(trail_pct, 3),
        "votes": votes,
        "confidence": round(confidence, 4),
        "sell_urgency": sell_urgency,
        "revised_sell_target": revised_sell_target,
        "protective_stop": protective_stop,
        "hard_stop": hard_stop,
        "recover_above": recover_above,
        "recovery_watch_min": recovery_watch_min,
        "valid_for_min": valid_for_min,
        "reask_after_min": reask_after_min,
        "reask_drawdown_from_peak_pct": reask_drawdown_from_peak_pct,
        "reask_if_price_above": reask_if_price_above,
        "max_rechecks": max_rechecks,
        "next_review_min": next_review_min,
        "reason": reason,
        "invalid_if": invalid_if,
        "decision_stage": decision_stage,
        "default_policy": default_policy_text,
        "duration_ms": advisor_duration_ms,
    }
    if triage_vote is not None:
        result_payload["triage"] = triage_vote
        result_payload["second_opinion_reason"] = _triage_second_opinion_reason(triage_vote, decision_stage)
    if shadow_triage is not None:
        result_payload["triage_shadow"] = shadow_triage
    return result_payload


def _log_decision(ticker: str, market: str, pos: dict,
                  action: str, trail_pct: float, votes: dict,
                  decision_stage: str = "TP_REVIEW",
                  default_policy: str = "",
                  duration_ms: int = 0,
                  triage: Optional[dict] = None,
                  shadow_triage: Optional[dict] = None):
    """hold_advisor 결정을 JSONL 파일에 기록"""
    try:
        log_dir = get_runtime_path("logs", "hold_advisor", make_parents=False)
        log_dir.mkdir(parents=True, exist_ok=True)
        today   = datetime.now().strftime("%Y-%m-%d")
        log_file = log_dir / f"decisions_{today}.jsonl"

        entry_price = float(pos.get("entry", 0) or 0)
        current_price = float(pos.get("current_price", 0) or 0)
        tp_price = float(pos.get("tp", 0) or 0)
        price_currency = "KRW"
        if str(market or "").upper() == "US":
            display_entry = float(pos.get("display_avg_price", 0) or 0)
            display_current = float(pos.get("display_current_price", 0) or 0)
            if display_entry > 0:
                fx_rate = (entry_price / display_entry) if entry_price > 0 else 0.0
                entry_price = display_entry
                if display_current > 0:
                    current_price = display_current
                elif current_price > 1000 and fx_rate > 0:
                    current_price = current_price / fx_rate
                display_tp = float(pos.get("display_tp_price", 0) or 0)
                if display_tp > 0:
                    tp_price = display_tp
                elif tp_price > 1000 and fx_rate > 0:
                    tp_price = tp_price / fx_rate
                price_currency = "USD"

        vote_rows = {}
        vote_fallback = False
        for key, value in votes.items():
            vote_fallback = vote_fallback or bool(value.get("fallback", False))
            vote_rows[key] = {
                "action": value.get("action", ""),
                "confidence": value.get("confidence", 0.0),
                "duration_ms": int(value.get("duration_ms", 0) or 0),
                "reason": value.get("reason", ""),
                "revised_sell_target": value.get("revised_sell_target", 0.0),
                "protective_stop": value.get("protective_stop", 0.0),
                "hard_stop": value.get("hard_stop", 0.0),
                "valid_for_min": value.get("valid_for_min", 0),
                "reask_after_min": value.get("reask_after_min", 0),
                "hold_mode": value.get("hold_mode", ""),
                "invalid_if": value.get("invalid_if", ""),
                "fallback": bool(value.get("fallback", False)),
                "source": value.get("source", ""),
                "exit_category": value.get("exit_category", ""),
                "exit_driver": value.get("exit_driver", ""),
            }
        triage_fallback = bool((triage or {}).get("fallback", False) or (triage or {}).get("triage_parse_error", False))
        advisor_cooldown = bool(pos.get("hold_advisor_cooldown", False) or pos.get("auto_sell_review_cooldown_active", False))
        advisor_fallback = bool(pos.get("hold_advisor_fallback", False) or vote_fallback or triage_fallback)
        decision_source = str(
            pos.get("hold_advisor_decision_source")
            or ("auto_sell_review_cooldown" if advisor_cooldown else "hold_advisor")
        )
        action_key = str(action or "").strip().lower() or "unknown"
        pending_outcome_label = (
            "cooldown_hold"
            if advisor_cooldown
            else f"fallback_{action_key}"
            if advisor_fallback
            else action_key
        )
        try:
            minutes_to_close = float(pos.get("minutes_to_close")) if pos.get("minutes_to_close") not in (None, "") else None
        except Exception:
            minutes_to_close = None
        input_completeness = _input_completeness(pos, market, decision_stage, "", minutes_to_close)
        revenue_context = _pathb_revenue_path_context(pos, decision_stage, default_policy, minutes_to_close)

        entry = {
            "ts":         datetime.now().isoformat(timespec="seconds"),
            "ticker":     ticker,
            "market":     market,
            "entry":      entry_price,
            "tp_price":   tp_price,
            "current":    current_price,
            "price_currency": price_currency,
            "pnl_pct":    round((current_price / entry_price - 1) * 100, 3) if entry_price and current_price else 0.0,
            "held_days":  pos.get("held_days", 0),
            "decision":   action,
            "decision_stage": decision_stage,
            "decision_source": decision_source,
            "fallback": advisor_fallback,
            "cooldown": advisor_cooldown,
            "pending_outcome_label": pending_outcome_label,
            "default_policy": default_policy,
            "duration_ms": int(duration_ms or 0),
            "advisor_context_v2": pos.get("advisor_context_v2", {}) if isinstance(pos, dict) else {},
            "input_completeness": input_completeness,
            "pathb_revenue_path_context": revenue_context,
            "hold_boundary_invalid": bool((triage or {}).get("hold_boundary_invalid", False)),
            "trail_pct":  trail_pct,
            "votes": vote_rows,
            "outcome":    None,   # 청산 후 채워짐
        }
        if triage is not None:
            entry["triage"] = {
                "action": triage.get("action", ""),
                "exit_category": triage.get("exit_category", ""),
                "exit_driver": triage.get("exit_driver", ""),
                "confidence": triage.get("confidence", 0.0),
                "second_opinion_reason": _triage_second_opinion_reason(triage, decision_stage),
                "prompt_version": triage.get("triage_prompt_version", TRIAGE_PROMPT_VERSION),
                "parse_error": bool(triage.get("triage_parse_error", False)),
            }
        if shadow_triage is not None:
            entry["triage_shadow"] = {
                "action": shadow_triage.get("action", ""),
                "exit_category": shadow_triage.get("exit_category", ""),
                "exit_driver": shadow_triage.get("exit_driver", ""),
                "confidence": shadow_triage.get("confidence", 0.0),
                "second_opinion_reason": _triage_second_opinion_reason(shadow_triage, decision_stage),
                "prompt_version": shadow_triage.get("triage_prompt_version", TRIAGE_PROMPT_VERSION),
                "parse_error": bool(shadow_triage.get("triage_parse_error", False)),
            }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"[hold_advisor] 결정 로그 기록 실패: {e}")
