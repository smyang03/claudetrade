"""나쁜장 진입 게이트 (regime entry gate) — PathB 국면 진입 억제. off/shadow/enforce.

문제(2026-07-21 파이프라인 스캔): 손실 지배 경로 PathB(claude_price 300+건)에 나쁜장
진입 게이트가 없다. 진입차단은 Path A 룰루프에만 걸리고 PathB는 position cap만 있음.
실측(v2_learning): 나쁜장 3국면(MILD_BULL·MILD_BEAR·CAUTIOUS) 진입만 건너뛰면
net −331,635 → −47,832원. CAUTIOUS는 TARGET 희생 0(무후회).

설계(오늘 anti_chase_gate·kr_flow_entry_gate와 동일 패턴):
- off(no-op)/shadow(would_skip 관측만)/enforce(차단). 기본 off.
- fail-open: 국면 미상이면 막지 않음.
- 차단 국면은 토글로(REGIME_ENTRY_GATE_BLOCK_MODES, 기본 CAUTIOUS만=무후회).
- ★매수 차단 게이트 = CLAUDE.md 운영자 확인 필수. enforce는 운영자 승인·두 소스 일치 후.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime_paths import get_runtime_path

VALID_MODES = ("off", "shadow", "enforce")
# 기본 차단 대상 = CAUTIOUS만(실측 무후회: net −47k·TARGET 0). MILD_BEAR/MILD_BULL은
# TARGET 일부 있어 shadow 관찰 후 운영자가 추가.
DEFAULT_BLOCK_MODES = ("CAUTIOUS",)


def normalize_mode(value: Any) -> str:
    text = str(value or "off").strip().lower()
    return text if text in VALID_MODES else "off"


def parse_block_modes(value: Any) -> tuple[str, ...]:
    if not value:
        return DEFAULT_BLOCK_MODES
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value).replace(";", ",").split(",")
    out = tuple(str(x).strip().upper() for x in items if str(x).strip())
    return out or DEFAULT_BLOCK_MODES


def evaluate_regime_entry_gate(
    regime: Any,
    mode: Any,
    *,
    block_modes: Any = None,
) -> dict[str, Any]:
    """나쁜장 진입 판정.

    decision: off | allow | allow_no_regime | would_skip | skip
    block(bool): enforce + 차단대상 국면일 때만 True.
    """
    mode = normalize_mode(mode)
    modes = parse_block_modes(block_modes)
    reg = str(regime or "").strip().upper()
    out: dict[str, Any] = {
        "mode": mode,
        "regime": reg,
        "block_modes": list(modes),
        "block": False,
    }
    if mode == "off":
        out["decision"] = "off"
        out["reason"] = "gate_off"
        return out
    if not reg:
        out["decision"] = "allow_no_regime"
        out["reason"] = "regime_unknown"
        return out
    if reg in modes:
        out["reason"] = "bad_regime_entry"
        if mode == "enforce":
            out["decision"] = "skip"
            out["block"] = True
        else:
            out["decision"] = "would_skip"
        return out
    out["decision"] = "allow"
    out["reason"] = "regime_allowed"
    return out


def shadow_log_path(session_date: str, market: str = "") -> Path:
    day = str(session_date or "").replace("-", "")
    suffix = f"_{market}" if market else ""
    return get_runtime_path("logs", "funnel", f"regime_entry_gate_{day}{suffix}.jsonl")


def record_regime_entry_gate(
    *,
    session_date: str,
    market: str,
    ticker: str,
    verdict: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> None:
    """게이트 판정을 격리 funnel JSONL에 기록(관측 전용, 실패해도 진입 무영향)."""
    try:
        if normalize_mode(verdict.get("mode")) == "off":
            return
        payload = {
            "event_type": "regime_entry_gate",
            "written_at": datetime.now().isoformat(timespec="seconds"),
            "session_date": session_date,
            "market": market,
            "ticker": ticker,
            **{k: verdict.get(k) for k in ("mode", "decision", "reason", "block", "regime", "block_modes")},
        }
        if extra:
            payload.update(extra)
        path = shadow_log_path(session_date, market)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
