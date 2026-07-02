"""트렌드 방어 오버레이 진입 게이트 — 순수 로직(네트워크 無).

스펙: 지수 월말종가 < 10개월 SMA(하락추세)면 PathB 신규 진입을 줄인다(risk-알파).
지수 역사 30년 검증 통과(MaxDD 반토막·Sharpe↑·CAGR 거의 유지, tools/trend_overlay_index_validation.py).

모드:
- off    : 완전 no-op(현행)
- shadow : 하락추세면 would_skip 관측만 기록(진입 그대로 진행)
- enforce: 하락추세면 block=True(신규 진입만 보류, 청산/보유 무관)

fail-open: 신호 파일 결손/stale/시장 누락이면 막지 않는다(데이터 결손으로 진입 죽이지 않음).
신호는 tools/refresh_trend_overlay_signal.py가 state/trend_overlay_signal.json에 캐시(루프 밖).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SIGNAL_PATH = ROOT / "state" / "trend_overlay_signal.json"
FUNNEL_DIR = ROOT / "logs" / "funnel"
# 신호 신선도 한도(일). 초과면 untrusted → fail-open.
SIGNAL_MAX_AGE_DAYS = 7


def normalize_mode(value: str | None) -> str:
    v = str(value or "off").strip().lower()
    return v if v in ("off", "shadow", "enforce") else "off"


def load_trend_signal(path: Path | None = None) -> dict[str, Any]:
    p = path or SIGNAL_PATH
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _age_days(as_of: str | None) -> float | None:
    if not as_of:
        return None
    try:
        dt = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds() / 86400.0
    except Exception:
        return None


def evaluate_trend_overlay_gate(signal: dict[str, Any], mode: str, market: str) -> dict[str, Any]:
    """verdict dict 반환. fail-open이면 block/would_skip 모두 False, trusted=False."""
    mkt = "US" if str(market or "").upper() == "US" else "KR"
    base = {"market": mkt, "mode": mode, "block": False, "would_skip": False,
            "below_sma": None, "trusted": False, "reason": ""}
    # staleness는 '갱신 시각'(generated_at)으로 잰다. 월간 신호의 as_of(월말종가 날짜)는
    # 원래 한 달까지 묵으므로 freshness 판정에 쓰면 안 된다(매번 stale 오판).
    age = _age_days((signal or {}).get("generated_at"))
    if age is not None and age > SIGNAL_MAX_AGE_DAYS:
        base["reason"] = "stale_signal"
        base["age_days"] = round(age, 1)
        return base  # fail-open
    markets = (signal or {}).get("markets") or {}
    ms = markets.get(mkt)
    if not isinstance(ms, dict):
        base["reason"] = "no_signal"
        return base  # fail-open
    below = ms.get("below_sma")
    if below is None:
        base["reason"] = "no_below_sma"
        return base  # fail-open
    base.update({
        "trusted": True,
        "below_sma": bool(below),
        "index_sym": ms.get("index_sym"),
        "index_close": ms.get("index_close"),
        "sma": ms.get("sma"),
        "as_of": ms.get("as_of"),
    })
    if not below:
        base["reason"] = "uptrend_ok"
        return base  # 상승추세 → 진입 허용
    # 하락추세
    base["would_skip"] = True
    base["reason"] = "downtrend"
    if mode == "enforce":
        base["block"] = True
    return base


def record_trend_overlay_gate(*, session_date: str, market: str, ticker: str,
                              verdict: dict[str, Any], extra: dict[str, Any] | None = None) -> None:
    """관측 funnel 기록(JSONL). 실패해도 진입 흐름에 영향 없음."""
    try:
        FUNNEL_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session_date": session_date,
            "market": market,
            "ticker": ticker,
            **{k: verdict.get(k) for k in
               ("mode", "block", "would_skip", "below_sma", "trusted", "reason",
                "index_sym", "index_close", "sma", "as_of")},
        }
        if extra:
            rec.update(extra)
        fp = FUNNEL_DIR / f"trend_overlay_{str(session_date or 'unknown').replace('-', '')}.jsonl"
        with open(fp, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
