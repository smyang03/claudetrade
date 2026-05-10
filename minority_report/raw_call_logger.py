"""
raw_call_logger.py — Claude API raw 호출 저장 유틸리티

저장 위치: logs/raw_calls/YYYYMMDD_{market}_{label}_{seq}.json
포맷:
  {
    "timestamp": "2026-03-27T22:30:00",
    "date": "2026-03-27",
    "market": "KR",
    "label": "analyst_bull_r1",
    "model": "claude-sonnet-4-6",
    "prompt": "...(전문)...",
    "raw_response": "...(전문)...",
    "parsed": {...},
    "tokens": {"input": 1234, "output": 456}
  }
"""
import json
import logging
import os
import re
import uuid
import hashlib
from datetime import datetime, date
from pathlib import Path
from typing import Any, Optional

from audit.agent_call_event_store import AgentCallEventStore
from runtime_paths import get_runtime_path

_RAW_CALLS_DIR: Optional[Path] = None
_AGENT_EVENT_STORE: Optional[AgentCallEventStore] = None
log = logging.getLogger(__name__)


def _dir() -> Path:
    global _RAW_CALLS_DIR
    if _RAW_CALLS_DIR is None:
        _RAW_CALLS_DIR = get_runtime_path("logs", "raw_calls", make_parents=True)
    _RAW_CALLS_DIR.mkdir(parents=True, exist_ok=True)
    return _RAW_CALLS_DIR


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _agent_event_store_enabled() -> bool:
    return str(os.getenv("ENABLE_AGENT_CALL_EVENT_STORE", "false") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


def _agent_event_store() -> Optional[AgentCallEventStore]:
    global _AGENT_EVENT_STORE
    if not _agent_event_store_enabled():
        return None
    raw = str(os.getenv("AGENT_CALL_EVENT_DB_PATH", "") or "").strip()
    path = Path(raw) if raw else get_runtime_path("data", "audit", "agent_call_events.db")
    if raw and not path.is_absolute():
        path = get_runtime_path(raw, make_parents=True)
    if _AGENT_EVENT_STORE is not None and _AGENT_EVENT_STORE.path == path:
        return _AGENT_EVENT_STORE
    try:
        _AGENT_EVENT_STORE = AgentCallEventStore(path, timeout=1.0)
        return _AGENT_EVENT_STORE
    except Exception as exc:
        log.warning("[raw_call_logger] agent event store open failed: %s", exc)
        return None


def _config_hash(*, model: str, prompt_mode: str, prompt_version: str, extra: Optional[dict[str, Any]]) -> str:
    explicit = ""
    if isinstance(extra, dict):
        explicit = str(extra.get("config_hash") or "")
    if explicit:
        return explicit
    payload = {
        "model": model,
        "prompt_mode": prompt_mode,
        "prompt_version": prompt_version,
    }
    return _sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def save(
    label: str,
    prompt: str,
    raw_response: str,
    parsed: dict,
    input_tokens: int,
    output_tokens: int,
    market: str = "",
    call_date: Optional[str] = None,
    model: str = "",
    call_id: str = "",
    parse_error: Optional[bool] = None,
    parse_stage: str = "",
    duration_ms: Optional[int] = None,
    prompt_mode: str = "",
    prompt_version: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> Optional[Path]:
    """Claude API raw 호출 1건을 JSON 파일로 저장한다."""
    today = call_date or date.today().isoformat()
    now   = datetime.now()
    ts    = now.strftime("%H%M%S%f")
    mkt   = (market or "XX").upper()
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label or "call")).strip("_") or "call"
    stable_call_id = str(call_id or f"{ts}_{uuid.uuid4().hex[:10]}")

    filename = f"{today.replace('-','')}_{mkt}_{safe_label}_{stable_call_id}.json"
    path = _dir() / filename

    record = {
        "timestamp":    now.isoformat(timespec="microseconds"),
        "date":         today,
        "market":       mkt,
        "label":        label,
        "call_id":      stable_call_id,
        "model":        model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        "prompt":       prompt,
        "raw_response": raw_response,
        "parsed":       parsed,
        "tokens":       {"input": input_tokens, "output": output_tokens},
    }
    if parse_error is not None:
        record["parse_error"] = bool(parse_error)
    if parse_stage:
        record["parse_stage"] = parse_stage
    if duration_ms is not None:
        record["duration_ms"] = int(duration_ms)
    if prompt_mode:
        record["prompt_mode"] = prompt_mode
    if prompt_version:
        record["prompt_version"] = prompt_version
    if extra:
        record["extra"] = dict(extra)

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        store = _agent_event_store()
        if store is not None:
            try:
                store.upsert_event(
                    {
                        "call_id": stable_call_id,
                        "label": label,
                        "market": mkt,
                        "call_date": today,
                        "known_at": record["timestamp"],
                        "model": record["model"],
                        "prompt_hash": _sha256_text(prompt),
                        "response_hash": _sha256_text(raw_response),
                        "config_hash": _config_hash(
                            model=record["model"],
                            prompt_mode=prompt_mode,
                            prompt_version=prompt_version,
                            extra=extra,
                        ),
                        "raw_call_path": str(path),
                        "parse_stage": parse_stage,
                        "parse_error": bool(parse_error),
                        "duration_ms": duration_ms,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "parsed": parsed,
                        "payload": {
                            "prompt_mode": prompt_mode,
                            "prompt_version": prompt_version,
                            "extra": dict(extra or {}),
                        },
                    }
                )
            except Exception as event_exc:
                log.warning("[raw_call_logger] agent event write failed call_id=%s: %s", stable_call_id, event_exc)
        return path
    except Exception as exc:
        log.warning("[raw_call_logger] save failed label=%s call_id=%s: %s", label, stable_call_id, exc)
        return None
