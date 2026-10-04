# -*- coding: utf-8 -*-
"""격리 실주문 레인 공통 안전장치 (2026-10-05 리뷰 docs/reports/live_tools_review_20261005.md 반영).

- safe_cash_krw: KR orderable_cash는 예수금(dnca_tot_amt)이라 미결제 매수가 빠지지 않는다(H2) → D+1·D+2 중 최소.
- place_safely: place_order 예외(HTTP 500 뒤 복구 실패 등)는 '접수됐는지 모름'이다(H3) → 예외 대신 success=None으로
  돌려주고, 호출 측은 OPEN 포지션 행을 남겨 봇이 그 종목을 일반 포지션으로 청산하지 못하게 한다.
"""
from __future__ import annotations

from typing import Any


def safe_cash_krw(bal: dict) -> float:
    vals = [float(bal.get(k) or 0) for k in ("orderable_cash", "d1_settlement_krw", "d2_settlement_krw")]
    vals = [v for v in vals if v > 0]
    return min(vals) if vals else 0.0


def place_safely(kis: Any, ticker: str, qty: int, price: float, side: str, token: Any, **kw) -> dict:
    try:
        res = kis.place_order(ticker, qty, price, side, token, **kw)
        return res if isinstance(res, dict) else {"success": None, "msg": f"unexpected:{type(res).__name__}"}
    except Exception as exc:  # noqa: BLE001 — 접수 여부 불명
        return {"success": None, "order_no": "", "msg": f"unknown:{type(exc).__name__}:{str(exc)[:160]}"}


def order_state(res: dict) -> str:
    """ok / rejected / unknown — unknown은 포지션을 OPEN으로 남겨 보호하고 운영자 확인 대상으로 둔다."""
    if res.get("success") is True:
        return "ok"
    if res.get("success") is None:
        return "unknown"
    return "rejected"
