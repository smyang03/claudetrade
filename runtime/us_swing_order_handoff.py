from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Mapping

import numpy as np

from runtime.us_swing_authority import evaluate_swing_authority, load_swing_policy


HANDOFF_SCHEMA_VERSION = "us_swing_order_handoff_v1"
FINAL_HANDOFF_STATES = {"SUBMITTED", "ORDERED", "FILLED", "SUBMIT_FAILED", "ORDER_UNKNOWN"}
_HANDOFF_COLUMNS = {
    "reference_close": "REAL",
    "handoff_status": "TEXT",
    "handoff_reason": "TEXT",
    "handoff_attempted_at": "TEXT",
    "handoff_order_no": "TEXT",
    "handoff_quote_price": "REAL",
    "handoff_open_price": "REAL",
    "handoff_qty": "INTEGER",
    "handoff_order_cost_krw": "REAL",
    "handoff_mode": "TEXT",
    "execution_shadow_eligible": "INTEGER",
    "execution_shadow_reason": "TEXT",
    "execution_shadow_qty": "INTEGER",
    "execution_shadow_budget_krw": "REAL",
    "execution_shadow_entry_proxy_usd": "REAL",
    "execution_shadow_entry_fill_usd": "REAL",
    "execution_shadow_entry_price_krw": "REAL",
    "execution_shadow_fx": "REAL",
    "execution_shadow_policy": "TEXT",
    "execution_shadow_evaluated_at": "TEXT",
    "execution_shadow_exit_date": "TEXT",
    "execution_shadow_exit_price": "REAL",
    "execution_shadow_exit_reason": "TEXT",
    "execution_shadow_net_krw_pct": "REAL",
    "execution_shadow_pnl_krw": "REAL",
    # 2026-08-04: 계약(예산·슬롯·소스·TP/SL/보유)이 바뀐 시점을 원장에 남긴다.
    # 계약이 다른 행을 한 평균에 섞으면 forward 판정이 무효가 된다.
    "execution_shadow_contract_id": "TEXT",
    "execution_shadow_max_open_slots": "INTEGER",
    "execution_shadow_allowed_sources": "TEXT",
}


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def ensure_handoff_schema(con: sqlite3.Connection) -> None:
    existing = {str(row[1]) for row in con.execute("PRAGMA table_info(signals)")}
    if not existing:
        raise ValueError("US swing signals table is missing")
    for column, sql_type in _HANDOFF_COLUMNS.items():
        if column not in existing:
            con.execute(f"ALTER TABLE signals ADD COLUMN {column} {sql_type}")
    con.commit()


def _block_lcb(values: np.ndarray, *, seed: int = 20260710) -> float | None:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 5:
        return None
    rng = np.random.default_rng(seed)
    block = min(5, len(values))
    starts = np.arange(max(1, len(values) - block + 1))
    means: list[float] = []
    for _ in range(2000):
        sample: list[float] = []
        while len(sample) < len(values):
            start = int(rng.choice(starts))
            sample.extend(values[start:start + block].tolist())
        means.append(float(np.mean(sample[:len(values)])))
    return float(np.quantile(means, 0.05))


def current_contract_id(con: sqlite3.Connection) -> str:
    """원장에 기록된 가장 최근 실행 계약 지문."""

    row = con.execute(
        """SELECT execution_shadow_contract_id FROM signals
           WHERE execution_shadow_contract_id IS NOT NULL AND execution_shadow_contract_id<>''
           ORDER BY signal_date DESC LIMIT 1"""
    ).fetchone()
    return str(row[0]) if row and row[0] else ""


def summarize_forward_evidence(
    con: sqlite3.Connection, *, contract_id: str | None = None
) -> dict[str, Any]:
    """현재 실행 계약과 **같은 계약**으로 만들어진 행만 forward 근거로 센다.

    2026-08-04 실측: 계약 필터 없이 집계하면 소스 화이트리스트(day_losers) 도입
    이전 행과 5만원 예산 시절 행이 한 평균에 섞인다. 실제로 정산 2건 평균 -1.80%는
    SMCI(most_actives, -14.80%) + NVTS(day_losers, +11.21%)의 혼합이었고,
    SMCI는 현재 계약에서는 후보에도 들어오지 못한다.
    """

    ensure_handoff_schema(con)
    active_contract = str(contract_id or "").strip() or current_contract_id(con)
    if active_contract:
        contract_sql = " AND execution_shadow_contract_id=? "
        params: tuple[Any, ...] = (active_contract,)
    else:
        contract_sql = ""
        params = ()
    missing_outcomes = con.execute(
        f"""SELECT COUNT(*) FROM signals WHERE status='MATURED'
           AND execution_shadow_eligible=1 AND execution_shadow_net_krw_pct IS NULL
           {contract_sql}""",
        params,
    ).fetchone()[0]
    critical_errors = ["execution_shadow_matured_outcome_missing"] if missing_outcomes else []
    rows = con.execute(
        f"""SELECT signal_date,execution_shadow_net_krw_pct FROM signals
           WHERE status='MATURED' AND execution_shadow_eligible=1
             AND execution_shadow_net_krw_pct IS NOT NULL {contract_sql}
           ORDER BY signal_date""",
        params,
    ).fetchall()
    legacy = con.execute(
        """SELECT COUNT(*) FROM signals WHERE status='MATURED' AND execution_shadow_eligible=1
             AND execution_shadow_net_krw_pct IS NOT NULL
             AND COALESCE(execution_shadow_contract_id,'')<>?""",
        (active_contract,),
    ).fetchone()[0]
    if not rows:
        observation_sessions = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        return {
            "sessions": 0,
            "matured": 0,
            "observation_sessions": int(observation_sessions or 0),
            "strategy_matched": True,
            "execution_shadow_policy": "rank1_skip_v1",
            "execution_contract_id": active_contract,
            "excluded_legacy_contract_matured": int(legacy or 0),
            "critical_data_errors": critical_errors,
        }
    by_date: dict[str, list[float]] = {}
    for signal_date, value in rows:
        parsed = _number(value)
        if parsed is not None:
            by_date.setdefault(str(signal_date), []).append(parsed)
    daily = np.asarray([float(np.mean(values)) for _, values in sorted(by_date.items())], dtype=float)
    positive = float(daily[daily > 0].sum())
    negative = float(-daily[daily < 0].sum())
    ordered = np.sort(daily)[::-1]
    observation_sessions = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    return {
        "sessions": int(len(daily)),
        "matured": int(sum(len(values) for values in by_date.values())),
        "observation_sessions": int(observation_sessions or 0),
        "strategy_matched": True,
        "execution_shadow_policy": "rank1_skip_v1",
        "execution_contract_id": active_contract,
        "excluded_legacy_contract_matured": int(legacy or 0),
        "mean_net_pct": float(daily.mean()),
        "profit_factor": float(positive / negative) if negative > 0 else (999.0 if positive > 0 else None),
        "block_lcb_pct": _block_lcb(daily),
        "ex_top3_days_pct": float(ordered[3:].mean()) if len(ordered) > 3 else None,
        "critical_data_errors": critical_errors,
    }


def resolve_handoff_authority(
    *,
    configured_mode: str,
    con: sqlite3.Connection,
    policy_path: Path,
    historical_path: Path,
    execution_path: Path | None = None,
) -> dict[str, Any]:
    policy = load_swing_policy(policy_path)
    decision = evaluate_swing_authority(
        configured_mode=configured_mode,
        historical_evidence=_load_json(historical_path),
        forward_evidence=summarize_forward_evidence(con),
        policy=policy,
        execution_evidence=_load_json(execution_path) if execution_path is not None else {},
    )
    return decision.to_dict()


def load_handoff_signals(
    con: sqlite3.Connection,
    *,
    session_date: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    ensure_handoff_schema(con)
    previous_factory = con.row_factory
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT * FROM signals
            WHERE signal_date=? AND status='PENDING'
              AND COALESCE(handoff_status,'') NOT IN
                  ('SUBMITTED','ORDERED','FILLED','SUBMIT_FAILED','ORDER_UNKNOWN',
                   'REHEARSAL_READY')
            ORDER BY rank,predicted_net_pct DESC LIMIT ?
            """,
            (str(session_date), max(1, int(limit))),
        ).fetchall()
    finally:
        con.row_factory = previous_factory
    return [dict(row) for row in rows]


@dataclass(frozen=True)
class SwingHandoffDecision:
    status: str
    reason: str
    would_submit: bool
    allowed_to_submit: bool
    ticker: str
    signal_date: str
    rank: int
    authority_mode: str
    qty: int
    order_cost_krw: float
    quote_price: float | None
    open_price: float | None
    reference_close: float | None
    gap_pct: float | None
    chase_pct: float | None
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decision(
    status: str,
    reason: str,
    *,
    signal: Mapping[str, Any],
    authority_mode: str = "shadow",
    would_submit: bool = False,
    allowed_to_submit: bool = False,
    qty: int = 0,
    order_cost_krw: float = 0.0,
    quote_price: float | None = None,
    open_price: float | None = None,
    reference_close: float | None = None,
    gap_pct: float | None = None,
    chase_pct: float | None = None,
    details: dict[str, Any] | None = None,
) -> SwingHandoffDecision:
    return SwingHandoffDecision(
        status=status,
        reason=reason,
        would_submit=would_submit,
        allowed_to_submit=allowed_to_submit,
        ticker=str(signal.get("ticker") or "").upper(),
        signal_date=str(signal.get("signal_date") or ""),
        rank=int(signal.get("rank") or 0),
        authority_mode=authority_mode,
        qty=int(qty),
        order_cost_krw=float(order_cost_krw),
        quote_price=quote_price,
        open_price=open_price,
        reference_close=reference_close,
        gap_pct=gap_pct,
        chase_pct=chase_pct,
        details=dict(details or {}),
    )


def evaluate_handoff(
    *,
    signal: Mapping[str, Any],
    authority: Mapping[str, Any],
    now: datetime,
    regular_open: datetime,
    handoff_enabled: bool,
    submit_enabled: bool,
    quote: Mapping[str, Any] | None,
    fx_rate: float,
    base_order_budget_krw: float,
    available_budget_krw: float,
    cash_krw: float,
    broker_trust_level: str,
    already_holding: bool,
    pending_order: bool,
    same_day_reentry_allowed: bool,
    current_open_slots: int = 0,
    min_open_min: int = 5,
    max_open_min: int = 30,
    min_probability: float = 0.55,
    min_predicted_net_pct: float = 0.25,
    absolute_hurdles_enforced: bool = True,
    max_abs_gap_pct: float = 3.0,
    max_reference_deviation_pct: float = 1.0,
    max_chase_pct: float = 0.5,
    max_fade_from_open_pct: float = 2.0,
    max_order_krw: float = 250_000.0,
) -> SwingHandoffDecision:
    mode = str(authority.get("effective_mode") or "shadow").lower()
    base_details = {"schema_version": HANDOFF_SCHEMA_VERSION, "authority": dict(authority)}
    if not handoff_enabled:
        return _decision("DISABLED", "handoff_disabled", signal=signal, authority_mode=mode, details=base_details)
    if mode == "shadow" or not bool(authority.get("allowed_to_emit_orders")):
        return _decision("BLOCKED", "authority_not_eligible", signal=signal, authority_mode=mode, details=base_details)
    max_new = int(authority.get("max_new_per_day") or 0)
    max_slots = int(authority.get("max_open_slots") or 0)
    if max_slots <= 0 or int(current_open_slots or 0) >= max_slots:
        return _decision(
            "BLOCKED",
            "strategy_open_slot_cap_reached",
            signal=signal,
            authority_mode=mode,
            details={**base_details, "current_open_slots": int(current_open_slots or 0), "max_open_slots": max_slots},
        )
    # 2026-08-20 수리: 거래대금 밴드/MAX 재선택이 붙으면 고른 종목의 원 rank가 3·7일 수 있다.
    # 원 rank로 이 게이트를 걸면 재선택이 통째로 무력화된다(08-20 실측: 밴드가 MXL rank3을
    # 골랐는데 여기서 rank_outside_authority_cap으로 차단 → 그날 진입 0건).
    # 브리지가 붙인 밴드 내 순위(_band_position)를 우선 쓰고, 없으면 원 rank로 동작(하위호환).
    # 원 rank는 귀속 태그(us_swing_5d_rank_N)에 그대로 쓰이므로 여기서만 대체한다.
    gate_rank = int(signal.get("_band_position") or signal.get("rank") or 0)
    if gate_rank <= 0 or gate_rank > max_new:
        return _decision("BLOCKED", "rank_outside_authority_cap", signal=signal, authority_mode=mode, details=base_details)
    probability = _number(signal.get("probability"))
    predicted_net = _number(signal.get("predicted_net_pct"))
    absolute_hurdle_reasons: list[str] = []
    if probability is None or probability < min_probability:
        absolute_hurdle_reasons.append("probability_below_hurdle")
    if predicted_net is None or predicted_net < min_predicted_net_pct:
        absolute_hurdle_reasons.append("predicted_net_below_hurdle")
    base_details["absolute_hurdles"] = {
        "mode": "enforce" if absolute_hurdles_enforced else "shadow",
        "would_block": bool(absolute_hurdle_reasons),
        "reasons": absolute_hurdle_reasons,
        "min_probability": float(min_probability),
        "min_predicted_net_pct": float(min_predicted_net_pct),
    }
    if absolute_hurdles_enforced and absolute_hurdle_reasons:
        return _decision(
            "BLOCKED", absolute_hurdle_reasons[0], signal=signal,
            authority_mode=mode, details=base_details,
        )
    elapsed_min = (now - regular_open).total_seconds() / 60.0
    if elapsed_min < float(min_open_min):
        return _decision("WAIT", "entry_window_not_open", signal=signal, authority_mode=mode, details={**base_details, "elapsed_min": elapsed_min})
    if elapsed_min > float(max_open_min):
        return _decision("BLOCKED", "entry_window_expired", signal=signal, authority_mode=mode, details={**base_details, "elapsed_min": elapsed_min})
    trust = str(broker_trust_level or "").lower()
    if trust not in {"trusted", "healthy", "ok"}:
        return _decision("BLOCKED", "broker_truth_not_trusted", signal=signal, authority_mode=mode, details={**base_details, "broker_trust_level": trust})
    if already_holding:
        return _decision("BLOCKED", "already_holding", signal=signal, authority_mode=mode, details=base_details)
    if pending_order:
        return _decision("BLOCKED", "pending_order_exists", signal=signal, authority_mode=mode, details=base_details)
    if not same_day_reentry_allowed:
        return _decision("BLOCKED", "same_day_reentry_blocked", signal=signal, authority_mode=mode, details=base_details)
    current = _number((quote or {}).get("price"))
    opened = _number((quote or {}).get("open"))
    independent_prev_close = _number((quote or {}).get("prev_close"))
    volume = _number((quote or {}).get("volume"))
    reference = _number(signal.get("reference_close"))
    if current is None or current <= 0 or opened is None or opened <= 0 or volume is None or volume <= 0:
        return _decision("BLOCKED", "provider_fresh_quote_incomplete", signal=signal, authority_mode=mode, quote_price=current, open_price=opened, reference_close=reference, details=base_details)
    if reference is None or reference <= 0:
        return _decision("BLOCKED", "reference_close_missing", signal=signal, authority_mode=mode, quote_price=current, open_price=opened, details=base_details)
    if independent_prev_close is None or independent_prev_close <= 0:
        return _decision(
            "BLOCKED", "independent_prev_close_missing", signal=signal, authority_mode=mode,
            quote_price=current, open_price=opened, reference_close=reference, details=base_details,
        )
    reference_deviation_pct = (independent_prev_close / reference - 1.0) * 100.0
    if abs(reference_deviation_pct) > abs(max_reference_deviation_pct):
        return _decision(
            "BLOCKED", "independent_reference_mismatch", signal=signal, authority_mode=mode,
            quote_price=current, open_price=opened, reference_close=reference,
            details={
                **base_details,
                "independent_prev_close": independent_prev_close,
                "reference_deviation_pct": reference_deviation_pct,
            },
        )
    gap_pct = (opened / reference - 1.0) * 100.0
    chase_pct = (current / opened - 1.0) * 100.0
    if abs(gap_pct) > max_abs_gap_pct:
        return _decision("BLOCKED", "open_gap_outside_contract", signal=signal, authority_mode=mode, quote_price=current, open_price=opened, reference_close=reference, gap_pct=gap_pct, chase_pct=chase_pct, details=base_details)
    if chase_pct > max_chase_pct:
        return _decision("BLOCKED", "price_chase_above_contract", signal=signal, authority_mode=mode, quote_price=current, open_price=opened, reference_close=reference, gap_pct=gap_pct, chase_pct=chase_pct, details=base_details)
    if chase_pct < -abs(max_fade_from_open_pct):
        return _decision("BLOCKED", "open_fade_below_contract", signal=signal, authority_mode=mode, quote_price=current, open_price=opened, reference_close=reference, gap_pct=gap_pct, chase_pct=chase_pct, details=base_details)
    fx = _number(fx_rate)
    if fx is None or fx <= 100:
        return _decision("BLOCKED", "fx_rate_invalid", signal=signal, authority_mode=mode, quote_price=current, open_price=opened, reference_close=reference, gap_pct=gap_pct, chase_pct=chase_pct, details=base_details)
    price_krw = current * fx
    size_multiplier = float(authority.get("size_multiplier") or 0.0)
    absolute_order_cap_krw = _number(authority.get("absolute_order_cap_krw"))
    if absolute_order_cap_krw is not None and absolute_order_cap_krw > 0:
        authority_budget_cap_krw = float(absolute_order_cap_krw)
        budget_cap_source = "authority_absolute"
    else:
        authority_budget_cap_krw = float(base_order_budget_krw or 0.0) * size_multiplier
        budget_cap_source = "risk_budget_multiplier"
    spend_caps = [
        authority_budget_cap_krw,
        float(available_budget_krw or 0.0),
        float(cash_krw or 0.0),
        float(max_order_krw or 0.0),
    ]
    budget_details = {
        "base_order_budget_krw": float(base_order_budget_krw or 0.0),
        "size_multiplier": size_multiplier,
        "authority_budget_cap_krw": authority_budget_cap_krw,
        "budget_cap_source": budget_cap_source,
        "configured_max_order_krw": float(max_order_krw or 0.0),
        "spend_caps_krw": spend_caps,
    }
    if any(not math.isfinite(value) or value <= 0 for value in spend_caps):
        return _decision(
            "BLOCKED",
            "order_budget_unavailable",
            signal=signal,
            authority_mode=mode,
            quote_price=current,
            open_price=opened,
            reference_close=reference,
            gap_pct=gap_pct,
            chase_pct=chase_pct,
            details={**base_details, "price_krw": price_krw, **budget_details},
        )
    spend_cap = min(spend_caps)
    qty = int(spend_cap // price_krw) if spend_cap > 0 else 0
    if qty <= 0:
        return _decision(
            "BLOCKED",
            "micro_budget_cannot_buy_one_share",
            signal=signal,
            authority_mode=mode,
            quote_price=current,
            open_price=opened,
            reference_close=reference,
            gap_pct=gap_pct,
            chase_pct=chase_pct,
            details={
                **base_details,
                "price_krw": price_krw,
                "spend_cap_krw": spend_cap,
                **budget_details,
            },
        )
    order_cost = qty * price_krw
    status = "SUBMIT_READY" if submit_enabled else "REHEARSAL_READY"
    return _decision(
        status,
        "contract_passed",
        signal=signal,
        authority_mode=mode,
        would_submit=True,
        allowed_to_submit=bool(submit_enabled),
        qty=qty,
        order_cost_krw=order_cost,
        quote_price=current,
        open_price=opened,
        reference_close=reference,
        gap_pct=gap_pct,
        chase_pct=chase_pct,
        details={
            **base_details,
            "elapsed_min": elapsed_min,
            "price_krw": price_krw,
            "spend_cap_krw": spend_cap,
            **budget_details,
            "independent_prev_close": independent_prev_close,
            "reference_deviation_pct": reference_deviation_pct,
        },
    )


def record_handoff_result(
    con: sqlite3.Connection,
    *,
    decision: SwingHandoffDecision,
    order_no: str = "",
    submitted: bool = False,
) -> None:
    ensure_handoff_schema(con)
    status = "SUBMITTED" if submitted else decision.status
    # 2026-08-11 관측 손실 수정: 창 종료 후 스캔의 entry_window_expired가 창 안의
    # 실질 차단 사유(chase·hurdle 등)를 덮어쓰지 못한다 (AXON 08-07·STEP 08-10 실측).
    # expired는 "창 안에 한 번도 평가되지 못했다"일 때만 기록되는 사유로 남긴다.
    # 실질 사유끼리의 last-write-wins는 유지한다(창 안 후행 사유가 선행을 덮는 건 정당).
    if decision.reason == "entry_window_expired":
        row = con.execute(
            "SELECT COALESCE(handoff_reason,'') FROM signals WHERE signal_date=? AND ticker=?",
            (decision.signal_date, decision.ticker),
        ).fetchone()
        existing_reason = str(row[0]) if row else ""
        if existing_reason and existing_reason != "entry_window_expired":
            return
    con.execute(
        """
        UPDATE signals SET handoff_status=?,handoff_reason=?,handoff_attempted_at=?,
            handoff_order_no=?,handoff_quote_price=?,handoff_open_price=?,handoff_qty=?,
            handoff_order_cost_krw=?,handoff_mode=?
        WHERE signal_date=? AND ticker=?
        """,
        (
            status,
            decision.reason,
            datetime.now().astimezone().isoformat(timespec="seconds"),
            str(order_no or ""),
            decision.quote_price,
            decision.open_price,
            decision.qty,
            decision.order_cost_krw,
            decision.authority_mode,
            decision.signal_date,
            decision.ticker,
        ),
    )
    con.commit()
