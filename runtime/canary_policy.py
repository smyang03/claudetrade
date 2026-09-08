# -*- coding: utf-8 -*-
"""캐너리 정책 게이트 (2026-09-08 운영자 승인) — 실주문 브리지가 제출 직전에 호출.

config/canary_policy.json: canary_strategies(캐너리 대상 전략 → forward_gate arm id), 동시 수, 총 손실선, 통과 순서.
- 명시된 기존 전략(us_swing_5d·kr_fallen_5d)만 손실선 공유 검사를 유지한다. 미등록 ID는 차단한다.
- 신규 캐너리는 실행 검증/어댑터가 없는 1단계이므로 전부 차단한다. 연구 판정은 주문 승인이 아니다.
- 손실선: data/shadow/canary_realized.jsonl(실체결 정산 원장, 운영자·정산 배치가 기록) 누적 net_krw ≤ 손실선이면 전부 차단(초기화 금지).
어떤 경우에도 스위치(ORDER_SUBMIT_ENABLED)를 대신 켜지 않는다 — 켜진 뒤의 추가 방어벽이다.
기존 전략용 손실선 검사는 미실현/미체결 위험 엔진이 아니다. 신규 캐너리에는 그 엔진의 별도 검증이 필요하다.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config" / "canary_policy.json"
GATE = ROOT / "state" / "forward_gate_state.json"
REALIZED = ROOT / "data" / "shadow" / "canary_realized.jsonl"
LEGACY_LOSS_GUARD_ONLY = frozenset({"us_swing_5d", "kr_fallen_5d"})


def _load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def realized_krw() -> float:
    tot = 0.0
    if REALIZED.exists():
        for line in REALIZED.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            value = float(row["net_krw"])
            if not math.isfinite(value):
                raise ValueError("nonfinite realized PnL")
            tot += value
    if not math.isfinite(tot):
        raise ValueError("nonfinite realized total")
    return tot


def canary_gate(strategy_id: str) -> tuple[bool, str]:
    pol = _load(POLICY, {})
    if not isinstance(pol, dict) or not pol:
        return False, "no_valid_policy"
    if not str(pol.get("status", "")).upper().startswith("APPROVED"):
        return False, "policy_not_approved"
    try:
        line = -abs(float(pol["total_loss_line_krw"]))
        if not math.isfinite(line) or line == 0:
            raise ValueError("invalid loss line")
        real = realized_krw()
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return False, "invalid_loss_evidence"
    if line and real <= line:
        return False, f"total_loss_line_hit:{real:.0f}<= {line:.0f}"
    mapping = pol.get("canary_strategies") or {}
    if not isinstance(mapping, dict):
        return False, "invalid_strategy_mapping"
    arm = mapping.get(strategy_id)
    if strategy_id in LEGACY_LOSS_GUARD_ONLY and not arm:
        return True, "legacy_loss_guard_only"
    if not arm:
        return False, "unknown_canary_strategy"
    # Stage 1 is research/rehearsal only. A legacy STRONG label (or a manually
    # edited state file) cannot authorize live orders. Stage 2 needs a separately
    # tested adapter, frozen executable contract, fresh MTM/pending-order risk
    # and explicit activation. None of those exist in this gate yet.
    return False, "execution_validation_pending"
