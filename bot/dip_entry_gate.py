"""US 낙폭베팅 배제 게이트 (dip entry gate) — off/shadow/enforce. 기본 off.

실증(2026-07-21, live closed 5/15~ n=195 US): US 손실은 "낙폭 반등 베팅"에 몰림 —
진입시점 ret_5d<−5% 배제군 net −42.2%p(LC18·TGT 희생 +11.6), 잔존 −13.6%p.
월별 부호역전 없음(5월 −2.2·6월 −45.0). KR은 정반대(손실원=급등추격, anti-chase
25 enforce가 담당)라 **US 전용** — KR 적용 금지(KR에서 ret_5d<−5 배제는 이득 없음).

설계는 anti_chase_gate와 동일 패턴:
- off(no-op)/shadow(would_block 관측만)/enforce(배제). fail-open(피처 없으면 통과).
- ★매수 차단 게이트 = 운영자 확인 필수. enforce는 운영자 승인·두 소스 일치 후.
"""
from __future__ import annotations

from typing import Any

VALID_MODES = ("off", "shadow", "enforce")
DEFAULT_THRESHOLD = -5.0  # ret_5d(%) 이 값 미만이면 낙폭베팅으로 배제
DEFAULT_MARKETS = ("US",)


def normalize_mode(value: Any) -> str:
    text = str(value or "off").strip().lower()
    return text if text in VALID_MODES else "off"


def parse_markets(value: Any) -> tuple[str, ...]:
    if not value:
        return DEFAULT_MARKETS
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value).replace(";", ",").split(",")
    out = tuple(str(x).strip().upper() for x in items if str(x).strip())
    return out or DEFAULT_MARKETS


def evaluate_dip_entry(
    ret_5d_pct: Any,
    mode: Any,
    *,
    market: Any = "US",
    threshold: float = DEFAULT_THRESHOLD,
    markets: Any = None,
) -> dict[str, Any]:
    """낙폭베팅 배제 판정.

    decision: off | not_applicable_market | allow_no_feature | allow | would_block | block
    block(bool): enforce + 대상 시장 + ret_5d<threshold일 때만 True.
    """
    mode = normalize_mode(mode)
    mkts = parse_markets(markets)
    mkt = str(market or "").strip().upper()
    out: dict[str, Any] = {
        "mode": mode,
        "market": mkt,
        "threshold": float(threshold),
        "block": False,
    }
    if mode == "off":
        out["decision"] = "off"
        out["reason"] = "gate_off"
        return out
    if mkt not in mkts:
        out["decision"] = "not_applicable_market"
        out["reason"] = "market_not_targeted"
        return out
    try:
        value = float(str(ret_5d_pct).replace(",", "").strip())
    except (TypeError, ValueError):
        out["decision"] = "allow_no_feature"
        out["reason"] = "ret_5d_unavailable"
        return out
    out["ret_5d_pct"] = round(value, 3)
    if value < float(threshold):
        out["reason"] = "dip_rebound_bet"
        if mode == "enforce":
            out["decision"] = "block"
            out["block"] = True
        else:
            out["decision"] = "would_block"
        return out
    out["decision"] = "allow"
    out["reason"] = "ret_5d_ok"
    return out
