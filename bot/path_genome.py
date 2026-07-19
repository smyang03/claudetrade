"""경로 유전자(path genome) — 트레이드의 초기 경로 형태 분류 + shadow 기록.

비전 ② 검증(2026-07-19, six-visions-verified): 초기 경로가 최종 운명을 강하게 예고
(외부 d1→d5 r=0.49). ★단 시뮬레이션 반증: "미확인 컷"은 손실 확정으로 해로움
(baseline +0.167% > 컷 -0.126%). 실익 있는 착취는 **"확인된 승자 연장"**(+0.330%).
그래서 이 모듈은 **관측 전용** — 실제 출구 무변경, 청산 시점 유전자를 funnel JSONL에
기록해 우리 net으로 ride-규칙을 forward 검증할 표본을 만든다.

genome 판정(peak_at/low_at 순서 + mfe/mae):
- shape: dip_then_run(저점 먼저→회복) / run_then_giveback(고점 먼저→반납) / flat
- early_confirmed: mfe >= 확인 임계(녹색 도달)
- ride_candidate: early_confirmed & dip_then_run 아님이 아닌 = 확인된 러너(연장 후보)
lookahead 없음(청산 후 관측).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime_paths import get_runtime_path

CONFIRM_MFE_PCT = 1.0  # 이 이상 녹색 도달 = 확인


def _parse(ts: Any) -> datetime | None:
    text = str(ts or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_path_genome(
    *,
    entry_at: Any = None,
    peak_at: Any = None,
    low_at: Any = None,
    mfe_pct: Any = None,
    mae_pct: Any = None,
    pnl_pct: Any = None,
    confirm_mfe_pct: float = CONFIRM_MFE_PCT,
) -> dict[str, Any]:
    """경로 유전자 분류(관측 전용). 부분 입력에도 가능한 만큼 산출."""
    mfe = _to_float(mfe_pct)
    mae = _to_float(mae_pct)
    pnl = _to_float(pnl_pct)
    e = _parse(entry_at)
    pk = _parse(peak_at)
    lo = _parse(low_at)

    out: dict[str, Any] = {
        "mfe_pct": mfe,
        "mae_pct": mae,
        "pnl_pct": pnl,
    }
    # shape: 고점/저점 도달 순서
    shape = "unknown"
    if pk is not None and lo is not None:
        if lo < pk:
            shape = "dip_then_run"      # 눌렸다가 달림 → 회복형
        elif pk < lo:
            shape = "run_then_giveback"  # 달렸다가 반납 → 반납형
        else:
            shape = "flat"
    out["shape"] = shape

    # 초기 확인 여부(녹색 도달)
    early_confirmed = mfe is not None and mfe >= float(confirm_mfe_pct)
    out["early_confirmed"] = bool(early_confirmed)

    # ride 후보: 확인됐고 순수 반납형이 아닌 것(연장으로 볼록 꼬리 수확)
    out["ride_candidate"] = bool(early_confirmed and shape != "run_then_giveback")

    # 시간축(entry 있으면): 고점/저점까지 분
    if e is not None:
        if pk is not None:
            out["time_to_peak_min"] = round((pk - e).total_seconds() / 60.0, 1)
        if lo is not None:
            out["time_to_low_min"] = round((lo - e).total_seconds() / 60.0, 1)

    # 결과 태그(반납 실패 = 확인됐는데 손실로 끝남 = "놓친 러너")
    if early_confirmed and pnl is not None and pnl <= 0:
        out["outcome_tag"] = "confirmed_but_lost"   # 녹색 갔다 반납한 loser
    elif early_confirmed and pnl is not None and pnl > 0:
        out["outcome_tag"] = "confirmed_win"
    elif pnl is not None and pnl > 0:
        out["outcome_tag"] = "unconfirmed_win"
    else:
        out["outcome_tag"] = "unconfirmed_loss"
    return out


def genome_log_path(session_date: str, market: str = "") -> Path:
    day = str(session_date or "").replace("-", "")
    suffix = f"_{market}" if market else ""
    return get_runtime_path("logs", "funnel", f"path_genome_{day}{suffix}.jsonl")


def record_path_genome(
    *,
    session_date: str,
    market: str,
    ticker: str,
    close_reason: str,
    genome: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> None:
    """경로 유전자를 격리 funnel JSONL에 기록(관측 전용, 실패해도 청산 무영향)."""
    try:
        payload = {
            "event_type": "path_genome",
            "written_at": datetime.now().isoformat(timespec="seconds"),
            "session_date": session_date,
            "market": market,
            "ticker": ticker,
            "close_reason": close_reason,
            **genome,
        }
        if extra:
            payload.update(extra)
        path = genome_log_path(session_date, market)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
