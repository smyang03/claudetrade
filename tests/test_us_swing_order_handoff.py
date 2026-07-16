from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

from runtime.us_swing_order_handoff import (
    ensure_handoff_schema,
    evaluate_handoff,
    load_handoff_signals,
    record_handoff_result,
)


def _signal(**overrides):
    signal = {
        "signal_date": "2026-07-10",
        "ticker": "TEST",
        "rank": 1,
        "probability": 0.60,
        "predicted_net_pct": 1.0,
        "reference_close": 100.0,
    }
    signal.update(overrides)
    return signal


def _authority(**overrides):
    authority = {
        "effective_mode": "micro",
        "allowed_to_emit_orders": True,
        "size_multiplier": 0.10,
        "max_new_per_day": 1,
        "max_open_slots": 1,
    }
    authority.update(overrides)
    return authority


def _evaluate(**overrides):
    opened = datetime(2026, 7, 10, 13, 30, tzinfo=timezone.utc)
    args = {
        "signal": _signal(),
        "authority": _authority(),
        "now": opened + timedelta(minutes=10),
        "regular_open": opened,
        "handoff_enabled": True,
        "submit_enabled": False,
        "quote": {"price": 100.5, "open": 100.0, "prev_close": 100.0, "volume": 1000},
        "fx_rate": 1400.0,
        "base_order_budget_krw": 2_000_000.0,
        "available_budget_krw": 1_000_000.0,
        "cash_krw": 1_000_000.0,
        "broker_trust_level": "trusted",
        "already_holding": False,
        "pending_order": False,
        "same_day_reentry_allowed": True,
    }
    args.update(overrides)
    return evaluate_handoff(**args)


def test_disabled_handoff_never_requests_quote_or_submit() -> None:
    result = _evaluate(handoff_enabled=False, quote=None)
    assert result.status == "DISABLED"
    assert result.would_submit is False
    assert result.allowed_to_submit is False


def test_rehearsal_can_be_ready_without_submit_permission() -> None:
    result = _evaluate()
    assert result.status == "REHEARSAL_READY"
    assert result.would_submit is True
    assert result.allowed_to_submit is False
    assert result.qty == 1


def test_double_lock_is_required_for_submit_ready() -> None:
    result = _evaluate(submit_enabled=True)
    assert result.status == "SUBMIT_READY"
    assert result.allowed_to_submit is True


def test_shadow_authority_and_price_chase_fail_closed() -> None:
    shadow = _evaluate(authority=_authority(effective_mode="shadow", allowed_to_emit_orders=False))
    chase = _evaluate(quote={"price": 102.0, "open": 100.0, "prev_close": 100.0, "volume": 1000})
    assert shadow.reason == "authority_not_eligible"
    assert chase.reason == "price_chase_above_contract"


def test_micro_budget_does_not_round_up_to_one_share() -> None:
    result = _evaluate(base_order_budget_krw=500_000.0)
    assert result.reason == "micro_budget_cannot_buy_one_share"
    assert result.qty == 0


def test_operator_absolute_cap_is_not_scaled_twice() -> None:
    result = _evaluate(
        authority=_authority(absolute_order_cap_krw=300_000.0),
        base_order_budget_krw=500_000.0,
        max_order_krw=300_000.0,
        quote={"price": 100.0, "open": 100.0, "prev_close": 100.0, "volume": 1000},
    )

    assert result.status == "REHEARSAL_READY"
    assert result.qty == 2
    assert result.details["spend_cap_krw"] == 300_000.0
    assert result.details["budget_cap_source"] == "authority_absolute"


def test_zero_cash_or_exhausted_strategy_slot_fails_closed() -> None:
    no_cash = _evaluate(cash_krw=0.0)
    full_slot = _evaluate(current_open_slots=1)
    assert no_cash.reason == "order_budget_unavailable"
    assert full_slot.reason == "strategy_open_slot_cap_reached"


def test_independent_previous_close_is_required_and_must_match_reference() -> None:
    missing = _evaluate(quote={"price": 100.5, "open": 100.0, "volume": 1000})
    mismatch = _evaluate(
        quote={"price": 100.5, "open": 100.0, "prev_close": 102.0, "volume": 1000}
    )
    assert missing.reason == "independent_prev_close_missing"
    assert mismatch.reason == "independent_reference_mismatch"


def test_absolute_prediction_hurdles_can_be_shadowed_without_disabling_rank_enforcement() -> None:
    result = _evaluate(
        signal=_signal(probability=0.40, predicted_net_pct=-0.5),
        absolute_hurdles_enforced=False,
    )

    assert result.status == "REHEARSAL_READY"
    assert result.details["absolute_hurdles"]["would_block"] is True
    assert result.details["absolute_hurdles"]["mode"] == "shadow"


def test_handoff_state_is_persisted_and_submitted_signal_is_not_reloaded() -> None:
    con = sqlite3.connect(":memory:")
    con.execute(
        """CREATE TABLE signals(
            signal_date TEXT,ticker TEXT,rank INTEGER,predicted_net_pct REAL,probability REAL,
            reference_close REAL,status TEXT,net_krw_pct REAL
        )"""
    )
    con.execute(
        "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?)",
        ("2026-07-10", "TEST", 1, 1.0, 0.6, 100.0, "PENDING", None),
    )
    ensure_handoff_schema(con)
    signals = load_handoff_signals(con, session_date="2026-07-10")
    assert len(signals) == 1
    decision = _evaluate(signal=signals[0], submit_enabled=True)
    record_handoff_result(con, decision=decision, order_no="O1", submitted=True)
    assert load_handoff_signals(con, session_date="2026-07-10") == []
    row = con.execute("SELECT handoff_status,handoff_order_no FROM signals").fetchone()
    assert row == ("SUBMITTED", "O1")
