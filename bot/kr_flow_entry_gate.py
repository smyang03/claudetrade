"""KR 수급(외인/기관) 기반 진입 게이트 — shadow/enforce.

가설(2026-06-23 검증): 봇의 매수존/눌림 진입 룰은 역선택을 한다 — 전일 수급
순매도(flow-negative) 종목이 다음 세션에 페이드(중앙 forward 0%)되는데도 잡는다.
이 게이트는 전일 외인+기관 순매도 종목의 신규 진입을 걸러낸다.

설계 원칙:
- 기본 off(완전 no-op). 운영자가 KR_FLOW_ENTRY_GATE_MODE를 shadow/enforce로 켤 때만 동작.
- fail-open: 수급 데이터가 없거나 untrusted면 막지 않는다(데이터 결손으로 진입을
  죽이지 않음 — bad-flow 날에도 진입 보존). flow-negative가 "신뢰 가능"할 때만 차단.
- shadow는 순수 관측(주문/플랜 무영향) — would_skip만 JSONL에 기록.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime_paths import get_runtime_path

VALID_MODES = ("off", "shadow", "enforce")


def normalize_mode(value: Any) -> str:
    text = str(value or "off").strip().lower()
    return text if text in VALID_MODES else "off"


def _to_num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_flow_entry_gate(flow_record: dict[str, Any] | None, mode: str) -> dict[str, Any]:
    """수급 진입 게이트 판정.

    decision: off | allow | allow_no_flow | would_skip | skip
      - off            : 게이트 비활성(no-op)
      - allow          : 수급 신뢰 + 순매도 아님 → 진입 허용
      - allow_no_flow  : 수급 결손/untrusted → fail-open(허용)
      - would_skip     : shadow에서 flow-negative(실제 차단 안 함, 관측만)
      - skip           : enforce에서 flow-negative(실제 진입 차단)
    block(bool): 실제 진입을 막아야 하는가(enforce + flow-negative일 때만 True)
    """
    mode = normalize_mode(mode)
    rec = flow_record or {}
    foreign = _to_num(rec.get("foreign"))
    institution = _to_num(rec.get("institution"))
    trusted = rec.get("flow_values_trusted")
    combined = (foreign or 0.0) + (institution or 0.0)
    out: dict[str, Any] = {
        "mode": mode,
        "foreign": foreign,
        "institution": institution,
        "combined_net": combined,
        "flow_values_trusted": bool(trusted) if trusted is not None else None,
        "flow_reported_date": rec.get("flow_reported_date") or rec.get("flow_source_date"),
        "flow_date_matched": rec.get("flow_date_matched"),
        "block": False,
    }
    if mode == "off":
        out["decision"] = "off"
        out["reason"] = "gate_off"
        return out
    # fail-open: 신뢰 가능한 수급이 없으면 막지 않는다
    if trusted is not True:
        out["decision"] = "allow_no_flow"
        out["reason"] = "flow_untrusted_or_missing"
        return out
    if combined < 0:
        out["reason"] = "flow_negative_combined"
        if mode == "enforce":
            out["decision"] = "skip"
            out["block"] = True
        else:
            out["decision"] = "would_skip"
        return out
    out["decision"] = "allow"
    out["reason"] = "flow_nonneg_combined"
    return out


def shadow_log_path(session_date: str, market: str = "KR") -> Path:
    day = str(session_date or "").replace("-", "")
    return get_runtime_path("logs", "funnel", f"kr_flow_entry_gate_{day}_{market}.jsonl")


def record_flow_entry_gate(
    *,
    session_date: str,
    market: str,
    ticker: str,
    verdict: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> None:
    """게이트 판정을 격리 funnel JSONL에 기록(순수 관측). 실패해도 진입 흐름 무영향."""
    try:
        if normalize_mode(verdict.get("mode")) == "off":
            return
        payload = {
            "event_type": "kr_flow_entry_gate",
            "written_at": datetime.now().isoformat(timespec="seconds"),
            "session_date": session_date,
            "market": market,
            "ticker": ticker,
            **{k: verdict.get(k) for k in (
                "mode", "decision", "reason", "block",
                "foreign", "institution", "combined_net",
                "flow_values_trusted", "flow_reported_date", "flow_date_matched",
            )},
        }
        if extra:
            payload.update(extra)
        path = shadow_log_path(session_date, market)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        # 관측 실패가 진입을 막으면 안 된다
        pass
