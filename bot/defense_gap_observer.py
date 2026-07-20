"""방어 관측기 — 코어 진입 국면(①) + next-open 청산 갭(②). shadow 관측 전용.

2026-07-20 라이브에서 드러난 두 "아쉬운 점"을 매매 무변경으로 데이터화한다:
① 코어 sleeve가 analyst 방향차단을 우회해 급락 국면에도 진입(275280이 급락일 아침
   진입 −2.65%). "코어가 방어국면에 진입한 날 vs 아닌 날 net"을 판정할 원장.
② 손실 코어를 same-day 청산 금지로 next-open 예약 → 밤사이 갭 리스크. 청산예약 시점
   종가 대비 익일 실제 청산가 갭을 축적해 "next-open 갭이 얼마나 손해인가" 판정.

둘 다 순수 관측(주문·청산 무변경, 규칙 무변경). 격리 funnel JSONL. 판정은 우리 net으로
표본 축적 후 운영자. lookahead 없음(진입/예약 시점 관측).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime_paths import get_runtime_path


def _log_path(kind: str, session_date: str, market: str) -> Path:
    day = str(session_date or "").replace("-", "")
    return get_runtime_path("logs", "funnel", f"{kind}_{day}_{market}.jsonl")


def _write(kind: str, session_date: str, market: str, payload: dict[str, Any]) -> None:
    try:
        path = _log_path(kind, session_date, market)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def record_core_entry_regime(
    *,
    session_date: str,
    market: str,
    ticker: str,
    source_strategy: str,
    regime: str,
    regime_size_pct: Any = None,
    analyst_blocked_for_discretionary: Any = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """① 코어 진입 시점의 국면·방어상태 기록(관측 전용).

    코어는 analyst 방향차단을 우회(observed)해 진입하므로, 그 순간 시장이 얼마나
    방어적이었는지(regime, 재량이면 막혔을지)를 남겨 사후 net과 대조한다.
    """
    payload = {
        "event_type": "core_entry_regime",
        "written_at": datetime.now().isoformat(timespec="seconds"),
        "session_date": session_date,
        "market": market,
        "ticker": str(ticker or ""),
        "source_strategy": str(source_strategy or ""),
        "regime": str(regime or ""),
        "regime_size_pct": regime_size_pct,
        # 재량 진입이었다면 이 국면에서 막혔을 것인가(코어만 우회 통과)
        "discretionary_would_block": bool(analyst_blocked_for_discretionary)
        if analyst_blocked_for_discretionary is not None else None,
        "defensive_regime": str(regime or "").upper() in {"DEFENSIVE", "CAUTIOUS_BEAR", "MILD_BEAR", "BEAR"},
    }
    if extra:
        payload.update(extra)
    _write("core_entry_regime", session_date, market, payload)


def record_next_open_sell_scheduled(
    *,
    session_date: str,
    market: str,
    ticker: str,
    close_price: Any,
    pnl_pct_at_schedule: Any = None,
    reason: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """② next-open 청산 예약 시점 기록 — 예약 종가를 남겨 익일 갭 판정 기준으로.

    익일 실제 청산가 - 여기 close_price = next-open 갭(밤사이 노출 비용). 별도 리뷰
    도구가 예약 원장과 익일 체결가를 조인해 갭 분포를 낸다.
    """
    payload = {
        "event_type": "next_open_sell_scheduled",
        "written_at": datetime.now().isoformat(timespec="seconds"),
        "session_date": session_date,
        "market": market,
        "ticker": str(ticker or ""),
        "scheduled_close_price": _num(close_price),
        "pnl_pct_at_schedule": _num(pnl_pct_at_schedule),
        "reason": str(reason or ""),
    }
    if extra:
        payload.update(extra)
    _write("next_open_sell_scheduled", session_date, market, payload)


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
