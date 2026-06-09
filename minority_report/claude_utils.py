# -*- coding: utf-8 -*-
"""minority_report/claude_utils.py — Claude 응답 파싱 공통 유틸"""

from __future__ import annotations

import json
import re
from typing import Any


def claude_response_meta(resp: Any) -> dict:
    """API 응답에서 request_id, service_tier, cache 토큰을 안전하게 추출."""
    usage = getattr(resp, "usage", None) or {}
    _g = lambda obj, key: getattr(obj, key, None) if not isinstance(obj, dict) else obj.get(key)
    return {
        "request_id": str(getattr(resp, "id", "") or ""),
        "service_tier": str(_g(usage, "service_tier") or ""),
        "cache_creation_input_tokens": int(_g(usage, "cache_creation_input_tokens") or 0),
        "cache_read_input_tokens": int(_g(usage, "cache_read_input_tokens") or 0),
    }


def is_claude_retryable_error(exc: Exception) -> bool:
    """재시도 가능한 Claude API 에러 여부 (문자열 매칭 대신 typed exception 사용)."""
    try:
        import anthropic
        return isinstance(exc, (
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
        ))
    except ImportError:
        text = str(exc).lower()
        return "529" in text or "overloaded" in text or "rate limit" in text


def extract_json(raw: str) -> dict:
    """Claude 응답 문자열에서 JSON 객체를 추출.

    처리 순서:
      1. 마크다운 코드블록(```json ... ```) 내부 각각 시도
      2. 전체 텍스트 직접 파싱
      3. 정규식으로 최외곽 {...} 추출 후 파싱

    모두 실패하면 ValueError 발생.
    """
    # 1. 마크다운 코드블록 내부 시도
    if "```" in raw:
        for block in raw.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            if block.startswith("{"):
                try:
                    return json.loads(block)
                except json.JSONDecodeError:
                    pass

    # 2. 전체 텍스트 직접 파싱
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 3. 정규식으로 최외곽 {...} 추출
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    # 잘린 응답 감지 (max_tokens 부족 가능성)
    if raw and not raw.rstrip().endswith("}"):
        raise ValueError(f"JSON 응답 잘림 (max_tokens 부족 가능): {raw[:200]!r}")
    raise ValueError(f"JSON 파싱 실패: {raw[:200]!r}")
