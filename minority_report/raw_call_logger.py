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
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from runtime_paths import get_runtime_path

_RAW_CALLS_DIR: Optional[Path] = None


def _dir() -> Path:
    global _RAW_CALLS_DIR
    if _RAW_CALLS_DIR is None:
        _RAW_CALLS_DIR = get_runtime_path("logs", "raw_calls", make_parents=True)
        _RAW_CALLS_DIR.mkdir(parents=True, exist_ok=True)
    return _RAW_CALLS_DIR


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
):
    """Claude API raw 호출 1건을 JSON 파일로 저장한다."""
    today = call_date or date.today().isoformat()
    ts    = datetime.now().strftime("%H%M%S")
    mkt   = (market or "XX").upper()

    filename = f"{today.replace('-','')}_{mkt}_{label}_{ts}.json"
    path = _dir() / filename

    record = {
        "timestamp":    datetime.now().isoformat(timespec="seconds"),
        "date":         today,
        "market":       mkt,
        "label":        label,
        "model":        model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        "prompt":       prompt,
        "raw_response": raw_response,
        "parsed":       parsed,
        "tokens":       {"input": input_tokens, "output": output_tokens},
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
