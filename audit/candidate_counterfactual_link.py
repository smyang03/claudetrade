"""audit_candidate_rows ↔ candidate_counterfactual_paths 매칭 헬퍼.

두 테이블의 candidate_key는 식별 단위가 다르다.
- audit_candidate_rows: (session_date, market, call_id, ticker) sha1 → "cand_..."
- candidate_counterfactual_paths: (date|market|ticker|known_at|cycle) 복합 문자열, call_id 없음

따라서 candidate_key 직접 조인은 0건이다. 게이트 효과 사후검증(어떤 selection 결정이
어떤 사후 path로 이어졌는가)을 위해, 공통으로 가진 (session_date, market, ticker) +
known_at 근접으로 연결한다. 같은 시장 안에서만 매칭하므로 timezone은 동일하다고 본다.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Optional

_DT_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
)


def _parse_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _epoch(dt: Optional[datetime]) -> Optional[float]:
    if dt is None:
        return None
    try:
        return dt.timestamp()
    except (ValueError, OverflowError):
        # tz-naive에서 timestamp()가 로컬 tz를 쓰지만, 비교는 동일 기준이라 무방
        return dt.replace(microsecond=0).toordinal() * 86400.0


def _norm_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def link_candidate_counterfactual(
    conn: sqlite3.Connection,
    session_date: str,
    market: str,
    ticker: str,
    known_at: Optional[str] = None,
    *,
    tolerance_min: float = 20.0,
) -> dict[str, Any]:
    """한 후보(session_date, market, ticker[, known_at])에 대응하는 counterfactual path를 찾는다.

    반환 dict:
    - matched: 매칭 path 존재 여부
    - match_basis: "known_at_exact" | "known_at_nearest" | "ticker_day" | "none"
    - nearest_known_at / delta_min: known_at 매칭 시 선택된 시점과 분 단위 차이
    - path_count: 선택된 path 수
    - actual_path: 실제 진입 분류("no_entry" 또는 진입 시나리오명)
    - actual_entered: 실제 진입 경로가 no_entry가 아닌지(이론 runup과 현실의 구분 기준)
    - actual_outcome_60m / actual_outcome_close: 실제 진입 경로의 outcome(미진입이면 None)
    - best_runup_path / best_runup_pct: max_runup_60m 최대 path (이론값 — 가정 진입 기준)
    - outcome_coverage: 선택 path 중 outcome 수집 비율(낮으면 forward 미수집 → 신뢰도 낮음)
    - paths: 선택된 path 행들의 요약 리스트
    """
    market_u = str(market or "").upper()
    ticker_u = _norm_ticker(ticker)
    rows = conn.execute(
        """
        SELECT known_at, signal_time, path_name, actual_path, status,
               outcome_30m_pct, outcome_60m_pct, outcome_close_pct,
               max_runup_60m_pct, max_drawdown_60m_pct
        FROM candidate_counterfactual_paths
        WHERE session_date = ? AND UPPER(market) = ? AND UPPER(ticker) = ?
        """,
        (session_date, market_u, ticker_u),
    ).fetchall()

    empty = {
        "matched": False,
        "match_basis": "none",
        "nearest_known_at": None,
        "delta_min": None,
        "path_count": 0,
        "actual_path": None,
        "actual_entered": False,
        "actual_outcome_60m": None,
        "actual_outcome_close": None,
        "best_runup_path": None,
        "best_runup_pct": None,
        "outcome_coverage": 0.0,
        "paths": [],
    }
    if not rows:
        return empty

    cols = (
        "known_at",
        "signal_time",
        "path_name",
        "actual_path",
        "status",
        "outcome_30m_pct",
        "outcome_60m_pct",
        "outcome_close_pct",
        "max_runup_60m_pct",
        "max_drawdown_60m_pct",
    )
    records = [dict(zip(cols, r)) for r in rows]

    match_basis = "ticker_day"
    nearest_known_at: Optional[str] = None
    delta_min: Optional[float] = None
    selected = records

    target_epoch = _epoch(_parse_dt(known_at)) if known_at else None
    if target_epoch is not None:
        # cf known_at별 시점 후보를 모아 가장 가까운 시점 그룹을 선택
        groups: dict[str, float] = {}
        for rec in records:
            ka = rec.get("known_at")
            ep = _epoch(_parse_dt(ka))
            if ep is not None and ka not in groups:
                groups[ka] = ep
        if groups:
            nearest_ka, nearest_ep = min(groups.items(), key=lambda kv: abs(kv[1] - target_epoch))
            delta = abs(nearest_ep - target_epoch) / 60.0
            nearest_known_at = nearest_ka
            delta_min = round(delta, 2)
            selected = [rec for rec in records if rec.get("known_at") == nearest_ka]
            match_basis = "known_at_exact" if delta <= 0.5 else (
                "known_at_nearest" if delta <= tolerance_min else "ticker_day"
            )
            if match_basis == "ticker_day":
                # 허용 오차 초과: 시점 그룹을 신뢰하지 않고 종목-일 전체로 폴백
                selected = records
                nearest_known_at = None
                delta_min = None

    actual_path = next(
        (rec.get("actual_path") for rec in selected if rec.get("actual_path")),
        None,
    )
    actual_entered = bool(actual_path and str(actual_path).strip().lower() != "no_entry")
    actual_outcome_60m = None
    actual_outcome_close = None
    if actual_entered:
        # 실제 진입 경로(actual_path)와 같은 path_name 행의 outcome이 현실 결과
        for rec in selected:
            if str(rec.get("path_name") or "") == str(actual_path):
                actual_outcome_60m = rec.get("outcome_60m_pct")
                actual_outcome_close = rec.get("outcome_close_pct")
                break

    runup_candidates = [
        (rec.get("path_name"), rec.get("max_runup_60m_pct"))
        for rec in selected
        if rec.get("max_runup_60m_pct") is not None
    ]
    best_runup_path = None
    best_runup_pct = None
    if runup_candidates:
        best_runup_path, best_runup_pct = max(runup_candidates, key=lambda kv: kv[1])

    out_present = sum(1 for rec in selected if rec.get("max_runup_60m_pct") is not None)
    outcome_coverage = round(out_present / len(selected), 2) if selected else 0.0

    return {
        "matched": True,
        "match_basis": match_basis,
        "nearest_known_at": nearest_known_at,
        "delta_min": delta_min,
        "path_count": len(selected),
        "actual_path": actual_path,
        "actual_entered": actual_entered,
        "actual_outcome_60m": actual_outcome_60m,
        "actual_outcome_close": actual_outcome_close,
        "best_runup_path": best_runup_path,
        "best_runup_pct": best_runup_pct,
        "outcome_coverage": outcome_coverage,
        "paths": selected,
    }
