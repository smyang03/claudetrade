from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from runtime_paths import get_runtime_path


_SOURCE_PRIORITY = {
    "lesson_candidates": 400,
    "recent_day": 300,
    "execution_lessons": 250,
    "learned_lessons": 100,
}
_SEVERITY_SCORE = {"high": 90, "medium": 60, "info": 25, "low": 10}
_MANUAL_REVIEW_SOURCES = {"data_analysis", "manual", "postmortem"}
_SYSTEM_TERMS = (
    "kis",
    "token",
    "timeout",
    "firewall",
    "network",
    "broker",
    "stale",
    "balance lag",
    "json",
    "parser",
    "parse",
    "max_tokens",
    "websocket",
    "order_unknown",
    "pathb reconcile",
    "api",
    "실행오염",
    "실행경고",
    "브로커",
    "동기화",
    "토큰",
    "타임아웃",
    "방화벽",
    "네트워크",
    "브로커",
    "파싱",
    "응답 잘림",
    "연결 실패",
    "장애",
)
_PROMPT_SCOPE_ALIASES = {
    "": "selection",
    "select": "selection",
    "selection": "selection",
    "select_tickers": "selection",
    "selection_retry": "selection",
    "retry": "selection",
    "r1": "market_judgment",
    "r2": "market_judgment",
    "analyst": "market_judgment",
    "analysts": "market_judgment",
    "judgment": "market_judgment",
    "market": "market_judgment",
    "market_judgment": "market_judgment",
    "market_debate": "market_judgment",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_lesson_candidates() -> dict[str, Any]:
    return _load_json_file(get_runtime_path("state", "lesson_candidates.json", make_parents=False))


def _load_brain() -> dict[str, Any]:
    return _load_json_file(get_runtime_path("state", "brain.json", make_parents=False))


def _clean_text(value: Any, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "..."
    return text


def _text_key(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "", text.lower())[:90]


def _is_system_text(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in _SYSTEM_TERMS)


def _date_prefix(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else ""


def _lesson_truth_status(item: dict[str, Any]) -> str:
    status = str(item.get("truth_status") or "").strip().lower()
    if status:
        return status
    source = str(item.get("source") or "").strip().lower()
    if source in _MANUAL_REVIEW_SOURCES:
        return "manual_review_required"
    return "fresh"


def _days_old(value: Any, today: date) -> int:
    prefix = _date_prefix(value)
    if not prefix:
        return 30
    try:
        return max(0, (today - date.fromisoformat(prefix)).days)
    except Exception:
        return 30


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_prompt_scope(value: Any) -> str:
    key = str(value or "").strip().lower()
    return _PROMPT_SCOPE_ALIASES.get(key, key or "selection")


def _allowed_prompt_scopes(item: dict[str, Any]) -> list[str]:
    raw_allowed = item.get("allowed_prompt_scopes")
    if isinstance(raw_allowed, (list, tuple, set)):
        scopes = [
            _normalize_prompt_scope(value)
            for value in raw_allowed
            if str(value or "").strip()
        ]
        if scopes:
            return list(dict.fromkeys(scopes))
    target_scope = str(item.get("target_prompt_scope") or "").strip()
    if target_scope:
        return [_normalize_prompt_scope(target_scope)]
    raw_scope = str(item.get("scope") or "").strip().lower()
    if raw_scope in {"market", "market_judgment", "judgment", "analyst", "analysts"}:
        return ["market_judgment"]
    return ["selection"]


def _score_item(item: dict[str, Any], today: date) -> float:
    severity = str(item.get("severity") or "info").lower()
    sample_count = _as_int(item.get("sample_count"), 0)
    confidence = _as_float(item.get("confidence"), 0.0)
    recency = max(0, 20 - _days_old(item.get("generated_at") or item.get("date"), today))
    return (
        _SOURCE_PRIORITY.get(str(item.get("source") or ""), 0)
        + _SEVERITY_SCORE.get(severity, 0)
        + min(sample_count, 100) * 0.4
        + min(max(confidence, 0.0), 1.0) * 30
        + recency
    )


def _make_id(market: str, source: str, raw_id: Any, idx: int = 0) -> str:
    core = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(raw_id or idx)).strip("_") or str(idx)
    return f"{market}_{source}_{core}"


def _append_item(
    items: list[dict[str, Any]],
    ignored: list[dict[str, str]],
    *,
    market: str,
    source: str,
    raw_id: Any,
    text: Any,
    scope: str = "selection",
    severity: str = "info",
    confidence: float = 0.0,
    sample_count: int = 0,
    generated_at: Any = "",
    allowed_prompt_scopes: list[str] | None = None,
    target_prompt_scope: str = "",
    idx: int = 0,
    text_limit: int = 220,
) -> None:
    cleaned = _clean_text(text, limit=text_limit)
    if len(cleaned) < 8:
        ignored.append({"source": source, "reason": "empty_or_too_short"})
        return
    if _is_system_text(cleaned):
        ignored.append({"source": source, "reason": "system_or_infra"})
        return
    items.append(
        {
            "id": _make_id(market, source, raw_id, idx),
            "market": market,
            "source": source,
            "scope": str(scope or "selection"),
            "target_prompt_scope": str(target_prompt_scope or (allowed_prompt_scopes or ["selection"])[0]),
            "allowed_prompt_scopes": list(allowed_prompt_scopes or ["selection"]),
            "text": cleaned,
            "severity": str(severity or "info").lower(),
            "confidence": round(min(max(float(confidence or 0.0), 0.0), 1.0), 2),
            "sample_count": max(0, int(sample_count or 0)),
            "generated_at": str(generated_at or ""),
        }
    )


def _collect_lesson_candidate_items(
    market: str,
    today: date,
    ignored: list[dict[str, str]],
    *,
    prompt_scope: str,
) -> list[dict[str, Any]]:
    payload = _load_lesson_candidates()
    rows = list((payload.get("markets") or {}).get(market, []) or [])
    items: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        if not bool(row.get("breached", False)):
            ignored.append({"source": "lesson_candidates", "reason": "not_breached"})
            continue
        if bool(row.get("ops_flag", False)):
            ignored.append({"source": "lesson_candidates", "reason": "ops_flag"})
            continue
        if row.get("claude_actionable") is False:
            ignored.append({"source": "lesson_candidates", "reason": "not_claude_actionable"})
            continue
        truth_status = _lesson_truth_status(row)
        if truth_status != "fresh":
            ignored.append({"source": "lesson_candidates", "reason": f"truth_status_{truth_status}"})
            continue
        row_scope = str(row.get("scope") or "").lower()
        if row_scope in {"execution", "consensus", "strategy"}:
            ignored.append({"source": "lesson_candidates", "reason": "non_selection_scope"})
            continue
        allowed_scopes = _allowed_prompt_scopes(row)
        if prompt_scope not in allowed_scopes:
            ignored.append({
                "source": "lesson_candidates",
                "reason": f"prompt_scope_excluded_{prompt_scope}",
            })
            continue
        action_hint = str(row.get("action_hint") or "").strip()
        if not action_hint:
            ignored.append({"source": "lesson_candidates", "reason": "missing_action_hint"})
            continue
        sample_count = _as_int(row.get("sample_count"), 0)
        min_sample = _as_int(row.get("min_sample"), 3)
        if sample_count < min_sample:
            ignored.append({"source": "lesson_candidates", "reason": "below_min_sample"})
            continue
        expires_at = _date_prefix(row.get("expires_at"))
        if expires_at:
            try:
                if date.fromisoformat(expires_at) < today:
                    ignored.append({"source": "lesson_candidates", "reason": "expired"})
                    continue
            except Exception:
                pass
        _append_item(
            items,
            ignored,
            market=market,
            source="lesson_candidates",
            raw_id=row.get("id") or row.get("metric_key"),
            text=action_hint,
            scope=row.get("scope") or "selection",
            severity=row.get("severity") or "info",
            confidence=_as_float(row.get("confidence"), 0.0),
            sample_count=sample_count,
            generated_at=row.get("generated_at"),
            allowed_prompt_scopes=allowed_scopes,
            target_prompt_scope=row.get("target_prompt_scope") or allowed_scopes[0],
            idx=idx,
            text_limit=500,
        )
    return items


def _collect_brain_items(
    market: str,
    today: date,
    ignored: list[dict[str, str]],
    *,
    prompt_scope: str,
) -> list[dict[str, Any]]:
    brain = _load_brain()
    market_data = (brain.get("markets") or {}).get(market, {})
    if not isinstance(market_data, dict):
        return []

    items: list[dict[str, Any]] = []
    recent_days = list(market_data.get("recent_days") or [])
    allow_recent_days = _env_bool("ACTIVE_LESSONS_ALLOW_RECENT_DAYS", False)
    for idx, row in enumerate(reversed(recent_days[-5:])):
        if not isinstance(row, dict):
            continue
        if bool(row.get("execution_learning_excluded", False)):
            ignored.append({"source": "recent_day", "reason": "execution_learning_excluded"})
            continue
        if bool(row.get("prompt_policy_excluded", False)):
            ignored.append({"source": "recent_day", "reason": "prompt_policy_excluded"})
            continue
        if bool(row.get("execution_contaminated", False)):
            ignored.append({"source": "recent_day", "reason": "execution_contaminated"})
            continue
        lesson = row.get("key_lesson")
        if not lesson:
            continue
        if not allow_recent_days:
            ignored.append({"source": "recent_day", "reason": "recent_day_disabled"})
            continue
        if prompt_scope != "selection":
            ignored.append({"source": "recent_day", "reason": f"prompt_scope_excluded_{prompt_scope}"})
            continue
        _append_item(
            items,
            ignored,
            market=market,
            source="recent_day",
            raw_id=row.get("date") or idx,
            text=lesson,
            scope="selection",
            allowed_prompt_scopes=["selection"],
            target_prompt_scope="selection",
            severity="medium",
            confidence=0.55,
            sample_count=_as_int(row.get("trades"), 0),
            generated_at=row.get("date"),
            idx=idx,
        )

    for _idx, _lesson in enumerate(list(market_data.get("execution_lessons") or [])[-8:]):
        ignored.append({"source": "execution_lessons", "reason": "execution_scope_excluded"})

    if _env_bool("ACTIVE_LESSONS_ALLOW_LEGACY_BRAIN", False):
        beliefs = market_data.get("current_beliefs") or {}
        for idx, lesson in enumerate(list(beliefs.get("learned_lessons") or [])[-8:]):
            if prompt_scope != "selection":
                ignored.append({"source": "learned_lessons", "reason": f"prompt_scope_excluded_{prompt_scope}"})
                continue
            _append_item(
                items,
                ignored,
                market=market,
                source="learned_lessons",
                raw_id=idx,
                text=lesson,
                scope="selection",
                allowed_prompt_scopes=["selection"],
                target_prompt_scope="selection",
                severity="low",
                confidence=0.35,
                sample_count=0,
                generated_at="",
                idx=idx,
            )
    return items


def _select_items(market: str, max_items: int, *, prompt_scope: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    today = date.today()
    ignored: list[dict[str, str]] = []
    items = _collect_lesson_candidate_items(market, today, ignored, prompt_scope=prompt_scope)
    items.extend(_collect_brain_items(market, today, ignored, prompt_scope=prompt_scope))

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = _text_key(str(item.get("text") or ""))
        if not key:
            ignored.append({"source": str(item.get("source") or ""), "reason": "empty_key"})
            continue
        if key in seen:
            ignored.append({"source": str(item.get("source") or ""), "reason": "duplicate"})
            continue
        seen.add(key)
        item["score"] = round(_score_item(item, today), 2)
        deduped.append(item)

    deduped.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
    source_caps = {"recent_day": 2}
    source_counts: dict[str, int] = {}
    selected: list[dict[str, Any]] = []
    # 승인형 컨베이어 (2026-06-12): 운영자 승인 전 교훈은 프롬프트 주입 금지.
    # 근거: 무승인 권고문 주입 14일간 지표 무변화 실증 (watch_only 49.8% breach 지속)
    try:
        from minority_report.lesson_approvals import approval_required, approval_status
        require_approval = approval_required()
    except Exception:
        require_approval = False
        approval_status = lambda _id: ""  # noqa: E731
    for item in deduped:
        source = str(item.get("source") or "")
        if require_approval:
            status = approval_status(str(item.get("id") or ""))
            if status == "rejected":
                ignored.append({"source": source, "reason": "approval_rejected"})
                continue
            if status != "approved":
                ignored.append({"source": source, "reason": "approval_pending"})
                continue
        cap = source_caps.get(source)
        if cap is not None and source_counts.get(source, 0) >= cap:
            ignored.append({"source": source, "reason": "source_cap"})
            continue
        selected.append(item)
        source_counts[source] = source_counts.get(source, 0) + 1
        if len(selected) >= max_items:
            break
    return selected, ignored


def _format_section(items: list[dict[str, Any]], max_chars: int) -> str:
    if not items:
        return ""
    lines = ["[active lessons]"]
    for item in items:
        sample = int(item.get("sample_count") or 0)
        suffix_parts = []
        if sample > 0:
            suffix_parts.append(f"n={sample}")
        severity = str(item.get("severity") or "").lower()
        if severity:
            suffix_parts.append(f"severity={severity}")
        suffix = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
        line = f"- {item.get('scope', 'selection')}: {item.get('text', '')}{suffix}"
        lines.append(line)
    section = "\n".join(lines)
    if len(section) <= max_chars:
        return section
    trimmed = section[: max_chars - 3].rstrip()
    return trimmed + "..."


def build_active_lesson_context(
    market: str,
    *,
    retry: bool = False,
    prompt_scope: str = "selection",
    max_items: int | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """Build compact active lesson metadata and optional prompt section."""
    normalized_market = str(market or "").upper()
    if normalized_market not in ("KR", "US"):
        normalized_market = str(market or "")
    item_limit = max_items if max_items is not None else _env_int("ACTIVE_LESSONS_MAX_ITEMS", 3, 0, 5)
    char_default = 400 if retry else 700
    char_env = "ACTIVE_LESSONS_RETRY_MAX_CHARS" if retry else "ACTIVE_LESSONS_MAX_CHARS"
    char_limit = max_chars if max_chars is not None else _env_int(char_env, char_default, 120, 3000)
    normalized_prompt_scope = _normalize_prompt_scope(prompt_scope)
    enabled = _env_bool("ACTIVE_LESSONS_ENABLED", False)
    shadow = _env_bool("ACTIVE_LESSONS_SHADOW", True)
    if not enabled:
        return {
            "section": "",
            "preview": "",
            "items": [],
            "ignored": [],
            "metadata": {
                "enabled": enabled,
                "shadow": shadow,
                "retry": bool(retry),
                "prompt_scope": normalized_prompt_scope,
                "injected": False,
                "lesson_injected": False,
                "ids": [],
                "count": 0,
                "lesson_count": 0,
                "chars": 0,
                "lesson_chars": 0,
                "lesson_max_chars": char_limit,
                "ignored_count": 0,
                "ignored_reasons": {},
                "scope_filtered_count": 0,
                "disabled_skipped": True,
            },
        }

    selected, ignored = _select_items(normalized_market, item_limit, prompt_scope=normalized_prompt_scope)
    preview = _format_section(selected, char_limit)
    injected = bool(enabled and not shadow and preview)
    prompt_scope_counts = dict(Counter(str(item.get("target_prompt_scope") or "") for item in selected))
    ignored_reasons = dict(Counter(str(item.get("reason") or "unknown") for item in ignored))
    scope_filtered_count = sum(
        count
        for reason, count in ignored_reasons.items()
        if str(reason).startswith("prompt_scope_excluded_")
    )
    if scope_filtered_count:
        ignored_reasons.setdefault("scope_mismatch", scope_filtered_count)
    metadata = {
        "enabled": enabled,
        "shadow": shadow,
        "retry": bool(retry),
        "prompt_scope": normalized_prompt_scope,
        "injected": injected,
        "lesson_injected": injected,
        "ids": [str(item.get("id")) for item in selected],
        "target_prompt_scopes": [str(item.get("target_prompt_scope") or "") for item in selected],
        "prompt_scope_counts": prompt_scope_counts,
        "count": len(selected),
        "lesson_count": len(selected),
        "chars": len(preview),
        "lesson_chars": len(preview),
        "lesson_max_chars": char_limit,
        "ignored_count": len(ignored),
        "ignored_reasons": ignored_reasons,
        "scope_filtered_count": scope_filtered_count,
    }
    return {
        "section": preview if injected else "",
        "preview": preview,
        "items": selected,
        "ignored": ignored,
        "metadata": metadata,
    }
