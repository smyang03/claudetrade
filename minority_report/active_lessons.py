from __future__ import annotations

import json
import os
import re
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
    idx: int = 0,
) -> None:
    cleaned = _clean_text(text)
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
            "text": cleaned,
            "severity": str(severity or "info").lower(),
            "confidence": round(min(max(float(confidence or 0.0), 0.0), 1.0), 2),
            "sample_count": max(0, int(sample_count or 0)),
            "generated_at": str(generated_at or ""),
        }
    )


def _collect_lesson_candidate_items(market: str, today: date, ignored: list[dict[str, str]]) -> list[dict[str, Any]]:
    payload = _load_lesson_candidates()
    rows = list((payload.get("markets") or {}).get(market, []) or [])
    items: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        if not bool(row.get("breached", False)):
            ignored.append({"source": "lesson_candidates", "reason": "not_breached"})
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
            text=row.get("summary"),
            scope=row.get("scope") or "selection",
            severity=row.get("severity") or "info",
            confidence=_as_float(row.get("confidence"), 0.0),
            sample_count=_as_int(row.get("sample_count"), 0),
            generated_at=row.get("generated_at"),
            idx=idx,
        )
    return items


def _collect_brain_items(market: str, today: date, ignored: list[dict[str, str]]) -> list[dict[str, Any]]:
    brain = _load_brain()
    market_data = (brain.get("markets") or {}).get(market, {})
    if not isinstance(market_data, dict):
        return []

    items: list[dict[str, Any]] = []
    recent_days = list(market_data.get("recent_days") or [])
    for idx, row in enumerate(reversed(recent_days[-5:])):
        if not isinstance(row, dict):
            continue
        lesson = row.get("key_lesson")
        if not lesson:
            continue
        _append_item(
            items,
            ignored,
            market=market,
            source="recent_day",
            raw_id=row.get("date") or idx,
            text=lesson,
            scope="selection",
            severity="medium",
            confidence=0.55,
            sample_count=_as_int(row.get("trades"), 0),
            generated_at=row.get("date"),
            idx=idx,
        )

    for idx, lesson in enumerate(list(market_data.get("execution_lessons") or [])[-8:]):
        _append_item(
            items,
            ignored,
            market=market,
            source="execution_lessons",
            raw_id=idx,
            text=lesson,
            scope="execution",
            severity="medium",
            confidence=0.5,
            sample_count=0,
            generated_at="",
            idx=idx,
        )

    if _env_bool("ACTIVE_LESSONS_ALLOW_LEGACY_BRAIN", False):
        beliefs = market_data.get("current_beliefs") or {}
        for idx, lesson in enumerate(list(beliefs.get("learned_lessons") or [])[-8:]):
            _append_item(
                items,
                ignored,
                market=market,
                source="learned_lessons",
                raw_id=idx,
                text=lesson,
                scope="selection",
                severity="low",
                confidence=0.35,
                sample_count=0,
                generated_at="",
                idx=idx,
            )
    return items


def _select_items(market: str, max_items: int) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    today = date.today()
    ignored: list[dict[str, str]] = []
    items = _collect_lesson_candidate_items(market, today, ignored)
    items.extend(_collect_brain_items(market, today, ignored))

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
    return deduped[:max_items], ignored


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
    char_limit = max_chars if max_chars is not None else _env_int(char_env, char_default, 120, 1200)
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
                "injected": False,
                "ids": [],
                "count": 0,
                "chars": 0,
                "ignored_count": 0,
                "disabled_skipped": True,
            },
        }

    selected, ignored = _select_items(normalized_market, item_limit)
    preview = _format_section(selected, char_limit)
    injected = bool(enabled and not shadow and preview)
    metadata = {
        "enabled": enabled,
        "shadow": shadow,
        "retry": bool(retry),
        "injected": injected,
        "ids": [str(item.get("id")) for item in selected],
        "count": len(selected),
        "chars": len(preview),
        "ignored_count": len(ignored),
    }
    return {
        "section": preview if injected else "",
        "preview": preview,
        "items": selected,
        "ignored": ignored,
        "metadata": metadata,
    }
