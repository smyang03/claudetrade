from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Any

from bot.session_date import KST
from kis_api import get_price
from logger import get_trading_logger
from preopen.scheduler import regular_open_dt
from runtime.us_swing_order_handoff import (
    evaluate_handoff,
    load_handoff_signals,
    record_handoff_result,
    resolve_handoff_authority,
)


log = get_trading_logger()


def _current_us_swing_open_slots(bot: Any) -> int:
    tickers: set[str] = set()
    sources = [
        *(getattr(getattr(bot, "risk", None), "positions", []) or []),
        *(getattr(bot, "pending_orders", []) or []),
    ]
    for item in sources:
        if str(item.get("market") or "").upper() != "US":
            continue
        source = str(item.get("source_strategy") or item.get("strategy_used") or "").lower()
        if source != "us_swing_5d":
            continue
        ticker = str(item.get("ticker") or "").upper()
        if ticker:
            tickers.add(ticker)
    return len(tickers)


def _has_broker_truth_open_order(bot: Any, ticker: str) -> bool:
    ticker_key = str(ticker or "").upper()
    try:
        rows = bot._broker_truth_open_buy_orders("US")
    except Exception:
        rows = []
    return any(str(row.get("ticker") or row.get("symbol") or "").upper() == ticker_key for row in rows)


def run_us_swing_handoff(bot: Any) -> dict[str, Any]:
    db_path = Path(str(bot._runtime_value("US_SWING_SHADOW_DB", "data/analysis/us_swing_shadow.db")))
    policy_path = Path(str(bot._runtime_value("US_SWING_POLICY_PATH", "config/us_swing_accelerated.json")))
    historical_path = Path(str(bot._runtime_value(
        "US_SWING_HISTORICAL_EVIDENCE_PATH", "state/us_swing_historical_evidence.json"
    )))
    execution_path = Path(str(bot._runtime_value(
        "US_SWING_EXECUTION_EVIDENCE_PATH", "state/us_swing_execution_evidence.json"
    )))
    if not db_path.exists() or not policy_path.exists() or not historical_path.exists() or not execution_path.exists():
        return {"status": "BLOCKED", "reason": "handoff_artifact_missing"}
    con = sqlite3.connect(db_path)
    try:
        configured_mode = str(bot._runtime_value("US_SWING_AUTHORITY_MODE", "shadow") or "shadow")
        authority = resolve_handoff_authority(
            configured_mode=configured_mode,
            con=con,
            policy_path=policy_path,
            historical_path=historical_path,
            execution_path=execution_path,
        )
        session_date = bot._current_session_date_str("US")
        signals = load_handoff_signals(
            con,
            session_date=session_date,
            limit=max(1, int(authority.get("max_new_per_day") or 1)),
        )
        if not signals:
            return {"status": "SKIPPED", "reason": "no_handoff_signal", "authority": authority}
        raw_submit_enabled = bot._runtime_bool("US_SWING_ORDER_SUBMIT_ENABLED", False)
        live_ack = str(bot._runtime_value("US_SWING_ORDER_LIVE_ACK", "") or "")
        ack_ok = bool(bot.is_paper) or live_ack == "I_ACCEPT_LIVE_US_SWING"
        submit_enabled = bool(raw_submit_enabled and ack_ok)
        if raw_submit_enabled and not ack_ok:
            log.error("[US swing handoff] submit switch ignored: live acknowledgement missing")
        bot._sync_runtime_with_broker()
        current_open_slots = _current_us_swing_open_slots(bot)
        results: list[dict[str, Any]] = []
        for signal in signals:
            ticker = str(signal.get("ticker") or "").upper()
            try:
                quote = get_price(
                    ticker,
                    bot._token_for_market("US"),
                    market="US",
                    allow_fallback=False,
                )
            except Exception as exc:
                quote = {}
                log.warning(f"[US swing handoff] provider-fresh quote failed {ticker}: {exc}")
            reentry = bot._same_day_reentry_state(ticker, "US")
            decision = evaluate_handoff(
                signal=signal,
                authority=authority,
                now=datetime.now(KST),
                regular_open=regular_open_dt("US", session_date),
                handoff_enabled=True,
                submit_enabled=submit_enabled,
                quote=quote,
                fx_rate=float(getattr(bot, "usd_krw_rate", 0) or 0),
                base_order_budget_krw=float(getattr(bot.risk, "max_order_krw", 0) or 0),
                available_budget_krw=float(bot._market_budget_available("US")),
                cash_krw=float(bot._broker_orderable_cash_krw("US")),
                broker_trust_level=str(bot._broker_trust_level("US") or "unknown"),
                already_holding=bool(bot._has_open_position(ticker, "US")),
                pending_order=bool(
                    bot._has_pending_order(ticker, "US")
                    or _has_broker_truth_open_order(bot, ticker)
                ),
                same_day_reentry_allowed=bool(reentry.get("allowed", False)),
                current_open_slots=current_open_slots,
                min_open_min=bot._runtime_int("US_SWING_ORDER_MIN_OPEN_MIN", 5),
                max_open_min=bot._runtime_int("US_SWING_ORDER_MAX_OPEN_MIN", 30),
                min_probability=bot._runtime_float("US_SWING_ORDER_MIN_PROB", 0.55),
                min_predicted_net_pct=bot._runtime_float("US_SWING_ORDER_MIN_PREDICTED_NET_PCT", 0.25),
                absolute_hurdles_enforced=bot._runtime_bool(
                    "US_SWING_ORDER_ABSOLUTE_HURDLES_ENFORCED", False
                ),
                max_abs_gap_pct=bot._runtime_float("US_SWING_ORDER_MAX_ABS_GAP_PCT", 3.0),
                max_reference_deviation_pct=bot._runtime_float("US_SWING_ORDER_MAX_REFERENCE_DEVIATION_PCT", 1.0),
                max_chase_pct=bot._runtime_float("US_SWING_ORDER_MAX_CHASE_PCT", 1.0),
                max_fade_from_open_pct=bot._runtime_float("US_SWING_ORDER_MAX_FADE_PCT", 2.0),
                max_order_krw=bot._runtime_float("US_SWING_ORDER_MAX_KRW", 250000.0),
            )
            if decision.status == "WAIT":
                results.append(decision.to_dict())
                continue
            if decision.would_submit:
                common_gate = bot._new_buy_block_state(
                    "US", ticker, "us_swing_5d", profit_evidence=dict(signal)
                )
                if not bool(common_gate.get("allowed", True)):
                    decision = replace(
                        decision,
                        status="BLOCKED",
                        reason=f"common_buy_gate:{common_gate.get('reason') or 'blocked'}",
                        would_submit=False,
                        allowed_to_submit=False,
                        details={**decision.details, "common_buy_gate": common_gate},
                    )
            if decision.status == "REHEARSAL_READY":
                record_handoff_result(con, decision=decision)
                results.append(decision.to_dict())
                break
            if not decision.allowed_to_submit:
                record_handoff_result(con, decision=decision)
                results.append(decision.to_dict())
                continue
            mode = str((getattr(bot, "today_judgment", {}) or {}).get("consensus", {}).get("mode", "CAUTIOUS"))
            submit_exception = ""
            try:
                order_ok = bot._submit_micro_probe_buy_order(
                    market="US",
                    ticker=ticker,
                    name=str((quote or {}).get("name") or ticker),
                    qty=int(decision.qty),
                    raw_price=float(decision.quote_price or 0.0),
                    risk_price_krw=float(decision.details.get("price_krw") or 0.0),
                    tp_pct=bot._runtime_float("US_SWING_ORDER_TP_DECIMAL", 0.12),
                    sl_pct=bot._runtime_float("US_SWING_ORDER_SL_DECIMAL", 0.25),
                    max_hold=5,
                    mode=mode,
                    selected_reason=f"us_swing_5d_rank_{decision.rank}",
                    source_strategy="us_swing_5d",
                    entry_priority_score=float(signal.get("probability") or 0.0),
                    tsdb_id=-1,
                    isdb_id=0,
                    signal_at=str(signal.get("created_at") or ""),
                    signal_row=dict(signal),
                    probe_meta={
                        "reason": f"us_swing_{decision.authority_mode}",
                        "original_qty": int(decision.qty),
                        "adjusted_qty": int(decision.qty),
                        "original_order_cost_krw": float(decision.order_cost_krw),
                        "adjusted_order_cost_krw": float(decision.order_cost_krw),
                        "order_budget_krw": float(decision.details.get("spend_cap_krw") or 0.0),
                        "min_effective_order_krw": 0.0,
                        "oversize_ratio": 1.0,
                    },
                )
            except Exception as exc:
                order_ok = False
                submit_exception = str(exc)
            order_no = ""
            submit_outcome = dict(getattr(bot, "_last_micro_probe_submit_result", {}) or {})
            if str(submit_outcome.get("order_no") or ""):
                order_no = str(submit_outcome.get("order_no") or "")
            if order_ok and not order_no:
                matches = [
                    item for item in (getattr(bot, "pending_orders", []) or [])
                    if str(item.get("market") or "").upper() == "US"
                    and str(item.get("ticker") or "").upper() == ticker
                ]
                if matches:
                    order_no = str(matches[-1].get("order_no") or "")
            outcome_status = str(submit_outcome.get("status") or "").upper()
            if order_ok and order_no and outcome_status != "UNKNOWN":
                record_handoff_result(con, decision=decision, order_no=order_no, submitted=True)
                results.append({**decision.to_dict(), "submitted": True, "order_no": order_no})
                break
            if outcome_status == "UNKNOWN" or (order_ok and not order_no) or submit_exception:
                unknown = replace(
                    decision,
                    status="ORDER_UNKNOWN",
                    reason="broker_submit_outcome_unknown",
                    allowed_to_submit=False,
                    details={
                        **decision.details,
                        "submit_outcome": submit_outcome,
                        "submit_exception": submit_exception[:240],
                    },
                )
                try:
                    bot._v2_record_order_unknown(
                        "US",
                        ticker,
                        {
                            "ticker": ticker,
                            "market": "US",
                            "qty": int(decision.qty),
                            "order_no": order_no,
                            "source_strategy": "us_swing_5d",
                        },
                        "US swing broker submission outcome unknown",
                    )
                except Exception:
                    pass
                record_handoff_result(con, decision=unknown, order_no=order_no)
                results.append(unknown.to_dict())
                break
            failed = replace(
                decision,
                status="SUBMIT_FAILED",
                reason="existing_order_path_rejected",
                allowed_to_submit=False,
            )
            record_handoff_result(con, decision=failed)
            results.append(failed.to_dict())
        return {"status": "EVALUATED", "authority": authority, "results": results}
    finally:
        con.close()
