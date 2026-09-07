# -*- coding: utf-8 -*-
"""실주문 브리지 입력 가드 (2026-09-08, Codex P0 "입력 계약 격리").

가상 북(x*/c_*)·유령·탐색 원장의 행이 실주문 브리지로 흘러드는 경로를 닫는다. 두 브리지의 후보 로더 직후에 호출.
허용 = 승인된 실전 계약만(KR kr_fallen R2/R4·blind, US us_swing_5d day_losers). 거절 사유는 status 파일에 남긴다.
"""
from __future__ import annotations

from typing import Any

VIRTUAL_PREFIXES = ("xus_", "xkr_", "c_", "x_", "phantom", "rehearsal")
META_KEYS = ("arm", "strategy_id", "strategy", "pool", "contract", "source", "source_strategy", "universe", "authority")
APPROVED_KR = {"kr_fallen_5d", "kr_fallen", "R2", "R4", "R2b", "R4b", "r2", "r4", "r4x", "blind", "kr_fallen_blindspot"}
APPROVED_US = {"us_swing_5d", "day_losers", "us_swing", "band", "dvol_desc"}


def input_rejection(row: dict[str, Any], market: str) -> str | None:
    """거절이면 사유 문자열, 허용이면 None. 값이 없는 메타는 통과(기존 원장 호환) — 가상 표식이 있을 때만 거절."""
    if not isinstance(row, dict):
        return "row_not_dict"
    approved = APPROVED_KR if str(market).upper() == "KR" else APPROVED_US
    for k in META_KEYS:
        v = row.get(k)
        if v is None or v == "":
            continue
        s = str(v).strip()
        low = s.lower()
        if any(low.startswith(p) for p in VIRTUAL_PREFIXES):
            return f"virtual_input_rejected:{k}={s[:24]}"
        if "SHADOW_ONLY" in s or "NO_ORDER" in s:
            return f"shadow_authority_rejected:{k}"
        if k in ("strategy_id", "arm", "pool") and s not in approved:
            return f"unapproved_contract:{k}={s[:24]}"
    return None


def filter_rows(rows: list[dict[str, Any]], market: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept, rejected = [], []
    for r in rows or []:
        why = input_rejection(r, market)
        (rejected if why else kept).append({**r, "_reject": why} if why else r)
    return kept, rejected
