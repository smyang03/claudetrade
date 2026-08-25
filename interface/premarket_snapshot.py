# -*- coding: utf-8 -*-
"""보유 US 종목의 프리마켓 시세 — **표시 전용** (2026-08-21).

운영자 요청: 프리마켓에서 보유 종목이 어떤지 텔레그램·대시보드로 보고 싶다.

⚠️ **이 값은 어떤 판단·기록·원장에도 흘러가지 않는다.** TP/SL/보유만기 판정은 전부 정규장
기준이고, 여기 값은 사람이 보는 화면과 알림에만 쓴다. 반환 dict를 저장하거나
포지션 원장에 병합하지 말 것.

설계 메모:
  - 포지션 소스는 브로커 truth **스냅샷 파일**이다. 브로커 API를 다시 부르지 않는다
    (broker_truth_scheduler가 이미 주기 갱신한다).
  - 시세는 `kis_api.get_price`를 그대로 쓴다. 실측(08-21 20:12 KST)에서 KIS 해외
    현재가가 프리마켓 체결가를 준다(`last`=프리마켓, `base`=전일 종가).
    직접 requests를 만들지 않는 이유: 거래소 코드 자가 교정·Finnhub 폴백을 공짜로 얻는다.
  - 텔레그램과 대시보드가 **이 함수 하나**를 공유한다. 각자 조회하면 값이 어긋난다.

가드:
  - 프리마켓 시간대(ET 04:00~09:30)에만 동작. DST는 ZoneInfo가 처리한다.
  - 60초 캐시 — 대시보드 새로고침 연타가 KIS 호출 연타가 되면 안 된다.
  - fail-silent — 실패는 WARNING 한 줄, 화면에서 빠질 뿐 아무것도 막지 않는다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
PREMARKET_START = dtime(4, 0)    # ET
REGULAR_OPEN = dtime(9, 30)      # ET
_CACHE_TTL_SEC = 60
# 2026-08-23 수리 (Codex 리뷰 P2-11): 캐시를 **모드별로 분리**한다.
# 이전에는 단일 슬롯이라, 같은 프로세스에서 paper를 조회한 뒤 60초 안에 live를 조회하면
# (또는 그 반대) mode를 확인하지 않고 직전 payload를 그대로 돌려줬다. 두 모드는 브로커
# 보유종목·평단이 다르므로 라이브 화면에 paper 포지션·손익이 뜰 수 있다.
_CACHE: dict[str, dict[str, Any]] = {}


def _cache_slot(mode: str) -> dict[str, Any]:
    key = str(mode or "live").strip().lower() or "live"
    return _CACHE.setdefault(key, {"at": None, "payload": None})


def is_premarket(now: datetime | None = None) -> bool:
    """미국 프리마켓 시간대인가 (ET 04:00~09:30, 평일). DST 자동 처리."""
    now_et = (now or datetime.now(KST)).astimezone(ET)
    if now_et.weekday() >= 5:
        return False
    return PREMARKET_START <= now_et.time() < REGULAR_OPEN


def _truth_path(mode: str) -> Path:
    root = Path(__file__).resolve().parents[1]
    name = "live_broker_truth_snapshot.json" if mode == "live" else "paper_broker_truth_snapshot.json"
    return root / "state" / name


def _held_us_tickers(mode: str) -> list[dict[str, Any]]:
    """브로커 truth 스냅샷에서 US 보유 종목. API 재조회 없음."""
    try:
        payload = json.loads(_truth_path(mode).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning(f"[premarket] truth 스냅샷 읽기 실패: {exc}")
        return []
    us = (payload.get("markets") or {}).get("US") or {}
    out = []
    for pos in us.get("positions") or []:
        ticker = str(pos.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        out.append({
            "ticker": ticker,
            "avg_price": float(pos.get("avg_price") or 0),
            "qty": float(pos.get("qty") or 0),
            "regular_price": float(pos.get("current_price") or 0),
            "regular_pnl_pct": float(pos.get("pnl_pct") or 0),
        })
    return out


def premarket_positions(bot: Any = None, *, mode: str = "live") -> dict[str, Any]:
    """보유 US 종목의 프리마켓 시세. 표시 전용.

    Returns:
        {"active": bool, "reason": str, "rows": [...], "as_of": ISO}
        active=False면 프리마켓이 아니거나 조회 불가 — 화면에서 그냥 빼면 된다.
    """
    now = datetime.now(KST)
    if not is_premarket(now):
        return {"active": False, "reason": "not_premarket", "rows": [], "as_of": now.isoformat(timespec="seconds")}

    slot = _cache_slot(mode)
    cached = slot.get("payload")
    at = slot.get("at")
    if cached is not None and at is not None and (now - at).total_seconds() < _CACHE_TTL_SEC:
        return cached

    rows: list[dict[str, Any]] = []
    try:
        from kis_api import get_price
    except Exception as exc:  # pragma: no cover - import 실패는 환경 문제
        log.warning(f"[premarket] kis_api import 실패: {exc}")
        return {"active": False, "reason": "kis_unavailable", "rows": [], "as_of": now.isoformat(timespec="seconds")}

    token = None
    if bot is not None:
        try:
            token = bot._token_for_market("US")
        except Exception:
            token = None

    for pos in _held_us_tickers(mode):
        try:
            quote = get_price(pos["ticker"], token, market="US") or {}
        except Exception as exc:
            log.warning(f"[premarket] {pos['ticker']} 시세 실패: {exc}")
            continue
        price = float(quote.get("price") or 0)
        prev_close = float(quote.get("prev_close") or 0)
        if price <= 0:
            continue
        avg = pos["avg_price"]
        rows.append({
            **pos,
            "premarket_price": round(price, 4),
            "prev_close": round(prev_close, 4) if prev_close > 0 else None,
            "premarket_chg_pct": round((price / prev_close - 1) * 100, 2) if prev_close > 0 else None,
            "premarket_pnl_pct": round((price / avg - 1) * 100, 2) if avg > 0 else None,
        })

    payload = {
        "active": bool(rows),
        "reason": "" if rows else "no_quotes",
        "rows": rows,
        "as_of": now.isoformat(timespec="seconds"),
    }
    slot["at"], slot["payload"] = now, payload
    return payload


def format_premarket(payload: dict[str, Any]) -> str:
    """텔레그램용 한국어 요약. 표시 전용."""
    if not payload.get("active"):
        reason = str(payload.get("reason") or "")
        if reason == "not_premarket":
            return "🌙 지금은 미국 프리마켓 시간이 아닙니다 (ET 04:00~09:30 / KST 17:00~22:30)."
        return "⚠️ 프리마켓 시세를 가져오지 못했습니다."
    lines = ["📈 <b>보유 US 종목 프리마켓</b>", ""]
    for r in payload["rows"]:
        chg = r.get("premarket_chg_pct")
        pnl = r.get("premarket_pnl_pct")
        arrow = "▲" if (chg or 0) > 0 else ("▼" if (chg or 0) < 0 else "―")
        head = f"{arrow} <b>{r['ticker']}</b> ${r['premarket_price']:,.2f}"
        if chg is not None:
            head += f" ({chg:+.2f}%)"
        lines.append(head)
        if pnl is not None:
            lines.append(f"    평단 ${r['avg_price']:,.2f} → 손익 <b>{pnl:+.2f}%</b>")
    lines.append("")
    lines.append(f"<i>{payload['as_of'][11:19]} KST · 표시 전용(매매 판단에 쓰이지 않음)</i>")
    return "\n".join(lines)
