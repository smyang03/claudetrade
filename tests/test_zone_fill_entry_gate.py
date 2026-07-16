from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from decision.claude_price_plan import make_price_plan
from execution.claude_price_adapter import EntrySignal
from lifecycle.event_store import EventStore
from runtime.pathb_runtime import PathBRuntime


def _plan(*, market: str = "US", reward_pct: float = 6.0):
    return make_price_plan(
        decision_id=f"dec_{market}",
        ticker="AAPL" if market == "US" else "005930",
        market=market,
        session_date="2026-07-16",
        buy_zone_low=100.0,
        buy_zone_high=110.0,
        sell_target=116.0,
        stop_loss=97.0,
        hold_days=2,
        confidence=0.7,
        reward_pct=reward_pct,
    )


def _runtime(store: EventStore) -> PathBRuntime:
    runtime = PathBRuntime.__new__(PathBRuntime)
    runtime.store = store
    runtime.mode = "live"
    runtime._execution_safety_payload = lambda: {"runtime_mode": "live"}
    runtime._recent_pathb_submit_block = lambda *_: False
    runtime._recorded_blocks = []
    runtime._record_blocked = lambda *args, **kwargs: runtime._recorded_blocks.append((args, kwargs))
    return runtime


def test_us_zone_fill_enforce_wait_uses_observed_hit_price(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATHB_ZONE_FILL_MODE_US", "enforce_wait")
    monkeypatch.setenv("PATHB_ZONE_FILL_TOP_THRESHOLD", "0.67")
    monkeypatch.setenv("PATHB_ZONE_FILL_REWARD_PCT", "5.0")
    store = EventStore(tmp_path / "events.db")
    runtime = _runtime(store)
    plan = _plan()
    runtime.adapter = SimpleNamespace()
    store.create_path_run(
        path_run_id=plan.path_run_id,
        decision_id=plan.decision_id,
        path_type="claude_price",
        market=plan.market,
        runtime_mode="live",
        session_date=plan.session_date,
        ticker=plan.ticker,
        status="WAITING",
        plan=plan.to_dict(),
    )

    assessment = runtime._zone_fill_at_entry_shadow(
        plan,
        EntrySignal(
            True,
            "buy_zone_hit",
            price=108.0,
            limit_price=110.0,
            path_run_id=plan.path_run_id,
        ),
    )

    assert assessment["zone_fill_entry_reference"] == "zone_hit_price"
    assert assessment["zone_fill_entry_price"] == 108.0
    assert assessment["zone_fill_pos"] == 0.8
    assert assessment["zone_fill_wait_price"] == 106.7
    assert runtime._zone_fill_entry_gate_block(plan, assessment) is True


def test_zone_fill_defer_keeps_plan_waiting_and_records_release(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATHB_ZONE_FILL_MODE_US", "enforce_wait")
    store = EventStore(tmp_path / "events.db")
    runtime = _runtime(store)
    plan = _plan()
    store.create_path_run(
        path_run_id=plan.path_run_id,
        decision_id=plan.decision_id,
        path_type="claude_price",
        market=plan.market,
        runtime_mode="live",
        session_date=plan.session_date,
        ticker=plan.ticker,
        status="WAITING",
        plan=plan.to_dict(),
    )
    blocked_signal = EntrySignal(
        True,
        "buy_zone_hit",
        price=108.0,
        limit_price=108.0,
        path_run_id=plan.path_run_id,
    )
    blocked = runtime._zone_fill_at_entry_shadow(plan, blocked_signal)

    runtime._defer_zone_fill_entry(plan, blocked_signal, blocked)

    waiting = store.find_path_run(plan.path_run_id)
    assert waiting["status"] == "WAITING"
    assert waiting["plan"]["last_submit_block_reason"] == "US_ZONE_FILL_WAIT"
    assert waiting["plan"]["zone_fill_wait_count"] == 1
    assert len(runtime._recorded_blocks) == 1

    released = runtime._zone_fill_at_entry_shadow(
        plan,
        EntrySignal(
            True,
            "buy_zone_hit",
            price=106.0,
            limit_price=106.0,
            path_run_id=plan.path_run_id,
        ),
    )

    assert runtime._zone_fill_entry_gate_block(plan, released) is False
    after = store.find_path_run(plan.path_run_id)
    assert after["plan"]["zone_fill_wait_release_price"] == 106.0
    assert after["plan"]["zone_fill_wait_release_pos"] == 0.6


def test_zone_fill_gate_never_blocks_kr(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATHB_ZONE_FILL_MODE_US", "enforce_wait")
    runtime = _runtime(EventStore(tmp_path / "events.db"))
    kr_plan = _plan(market="KR")

    assert runtime._zone_fill_entry_gate_block(
        kr_plan,
        {"zone_fill_mode": "enforce_wait", "zone_fill_worst_cell": True},
    ) is False
