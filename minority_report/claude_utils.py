# -*- coding: utf-8 -*-
"""minority_report/claude_utils.py — Claude 응답 파싱 공통 유틸"""

from __future__ import annotations

import json
import os
import re
from typing import Any

# ── thinking effort 허용값 ────────────────────────────────────────────────────
_VALID_EFFORT = {"low", "medium", "high", "xhigh", "max"}
_THINKING_ON = {"1", "true", "yes", "on", "adaptive"}
_THINKING_OFF = {"0", "false", "no", "off", "disabled"}


def response_text(resp: Any) -> str:
    """SDK 응답에서 text 블록만 이어붙여 반환.

    thinking 도입 대비 핵심: adaptive thinking이 켜지면 content[0]이 thinking 블록이
    되므로 `resp.content[0].text`는 깨진다(AttributeError/빈값). 이 헬퍼는 type=="text"
    블록만 모으므로 thinking on/off 모두에서 동작하며, thinking off일 때는 기존
    `resp.content[0].text.strip()`와 동일한 결과를 낸다.
    """
    blocks = getattr(resp, "content", None) or []

    def _btype(block):
        bt = getattr(block, "type", None)
        if bt is None and isinstance(block, dict):
            bt = block.get("type")
        return bt

    def _btext(block):
        tx = getattr(block, "text", None)
        if tx is None and isinstance(block, dict):
            tx = block.get("text")
        return tx

    # 1차: type=="text" 블록만 (실제 API 응답 경로)
    parts: list[str] = [str(_btext(b)) for b in blocks if _btype(b) == "text" and _btext(b)]
    if parts:
        return "".join(parts).strip()

    # 2차 fallback: type을 세팅하지 않는 응답(테스트 mock 등) 대비 —
    # thinking 계열만 제외하고 text 속성이 있는 블록을 모은다.
    for b in blocks:
        if _btype(b) in ("thinking", "redacted_thinking"):
            continue
        tx = _btext(b)
        if tx:
            parts.append(str(tx))
    return "".join(parts).strip()


def thinking_extra_body(scope: str = "") -> dict:
    """호출부(scope)별 thinking/effort를 env로 결정해 messages.create(extra_body=...)용 dict 반환.

    ⚠️ sonnet-5 계열은 thinking 미지정 시 adaptive가 기본 ON이다. 우리 응답추출을
    깨지 않으려면 OFF를 원하는 곳도 반드시 thinking:disabled를 명시해야 하므로,
    이 함수는 항상 thinking 키를 채워 반환한다.

    env 규칙:
      - CLAUDE_THINKING_ENABLED (전역 기본, 기본 true)
      - CLAUDE_THINKING_<SCOPE>  (scope 우선, on|off; 없으면 전역)
      - CLAUDE_EFFORT_<SCOPE> 또는 CLAUDE_EFFORT_DEFAULT (기본 medium)
    """
    scope_u = re.sub(r"[^A-Za-z0-9]+", "_", str(scope or "")).strip("_").upper()

    global_on = str(os.getenv("CLAUDE_THINKING_ENABLED", "true")).strip().lower() in _THINKING_ON
    raw = str(os.getenv(f"CLAUDE_THINKING_{scope_u}", "")).strip().lower() if scope_u else ""
    if raw in _THINKING_ON:
        on = True
    elif raw in _THINKING_OFF:
        on = False
    else:
        on = global_on

    if not on:
        return {"thinking": {"type": "disabled"}}

    effort = ""
    if scope_u:
        effort = str(os.getenv(f"CLAUDE_EFFORT_{scope_u}", "")).strip().lower()
    if not effort:
        effort = str(os.getenv("CLAUDE_EFFORT_DEFAULT", "medium")).strip().lower()

    body: dict = {"thinking": {"type": "adaptive"}}
    if effort in _VALID_EFFORT:
        body["output_config"] = {"effort": effort}
    return body


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
