# -*- coding: utf-8 -*-
"""minority_report/claude_utils.py — Claude 응답 파싱 공통 유틸"""

from __future__ import annotations

import json
import re


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

    raise ValueError(f"JSON 파싱 실패: {raw[:200]!r}")
