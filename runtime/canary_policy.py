# -*- coding: utf-8 -*-
"""캐너리 정책 게이트 (2026-09-08 운영자 승인) — 실주문 브리지가 제출 직전에 호출.

config/canary_policy.json: canary_strategies(캐너리 대상 전략 → forward_gate arm id), 동시 수, 총 손실선, 통과 순서.
- 대상이 아닌 전략(현행 us_swing_5d·kr_fallen_5d)은 손실선만 공유 검사(총 손실선 = 신규 전략 실체결 합산).
- 대상 전략은 forward_gate_state.json에서 CANDIDATE_STRONG이어야 하고, 통과 순서 큐의 앞 후보가 먼저다(동시 수 한도).
- 손실선: data/shadow/canary_realized.jsonl(실체결 정산 원장, 운영자·정산 배치가 기록) 누적 net_krw ≤ 손실선이면 전부 차단(초기화 금지).
어떤 경우에도 스위치(ORDER_SUBMIT_ENABLED)를 대신 켜지 않는다 — 켜진 뒤의 추가 방어벽이다.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config" / "canary_policy.json"
GATE = ROOT / "state" / "forward_gate_state.json"
REALIZED = ROOT / "data" / "shadow" / "canary_realized.jsonl"


def _load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def realized_krw() -> float:
    tot = 0.0
    if REALIZED.exists():
        for line in REALIZED.read_text(encoding="utf-8").splitlines():
            try:
                tot += float(json.loads(line).get("net_krw") or 0.0)
            except (ValueError, TypeError):
                continue
    return tot


def canary_gate(strategy_id: str) -> tuple[bool, str]:
    pol = _load(POLICY, {})
    if not pol:
        return True, "no_policy"
    if str(pol.get("status", "")).upper().startswith("PROPOSAL"):
        return True, "policy_not_approved"   # 승인 전엔 관측만
    line = -abs(float(pol.get("total_loss_line_krw") or 0))
    real = realized_krw()
    if line and real <= line:
        return False, f"total_loss_line_hit:{real:.0f}<= {line:.0f}"
    mapping = pol.get("canary_strategies") or {}
    arm = mapping.get(strategy_id)
    if not arm:
        return True, "not_canary_strategy"
    verdicts = (_load(GATE, {}) or {}).get("verdicts", {})
    if verdicts.get(arm) != "CANDIDATE_STRONG":
        return False, f"forward_not_strong:{arm}={verdicts.get(arm)}"
    queue = [k for k in (pol.get("priority_queue") or []) if verdicts.get(k) == "CANDIDATE_STRONG"]
    allowed = queue[: int(pol.get("max_concurrent_canaries") or 1)]
    if arm not in allowed:
        return False, f"queue_position:{arm} not in {allowed}"
    return True, "canary_allowed"
