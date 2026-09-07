# -*- coding: utf-8 -*-
"""캐너리(5만원 실투자) 정책 검사기 — 읽기 전용, 어디에도 배선되지 않음 (2026-09-08, 운영자 승인 전 제안 기본값).

config/canary_policy.json: 동시 캐너리 수·후보별 손실선(총 −33,000원 ÷ 동시 수)·통과 순서 큐·해제 조건.
입력: state/forward_gate_state.json(forward 판정) → "지금 캐너리 ON 가능 후보 k/N"과 사유 출력. 실매수 스위치는 건드리지 않는다.
사용: python tools/canary_policy_check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config" / "canary_policy.json"
GATE = ROOT / "state" / "forward_gate_state.json"


def main() -> int:
    pol = json.loads(POLICY.read_text(encoding="utf-8"))
    try:
        gate = json.loads(GATE.read_text(encoding="utf-8")).get("verdicts", {})
    except (OSError, ValueError):
        gate = {}
    eligible = [k for k, v in gate.items() if v == "CANDIDATE_STRONG"]
    queue = [k for k in pol["priority_queue"] if k in eligible] + [k for k in eligible if k not in pol["priority_queue"]]
    allowed = queue[: int(pol["max_concurrent_canaries"])]
    per_line = -abs(float(pol["total_loss_line_krw"])) / max(1, int(pol["max_concurrent_canaries"]))
    print(f"[CANARY] 정책: 동시 {pol['max_concurrent_canaries']} · 건당 {pol['order_krw']:,}원 · 총 손실선 {pol['total_loss_line_krw']:,}원 → 후보별 {per_line:,.0f}원")
    print(f"[CANARY] CANDIDATE_STRONG {len(eligible)}/{len(gate)} · ON 가능 {len(allowed)}: {allowed or '없음'}")
    for k, v in gate.items():
        print(f"  {k:28s} {v}")
    print("[CANARY] 배선 없음 — 스위치는 운영자 승인 후 수동(ORDER_SUBMIT_ENABLED). 이 출력은 판단 보조.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
