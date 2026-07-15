from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from runtime.pathb_paired_exit_shadow import PairedExitShadowObserver


def _observer(root: Path) -> PairedExitShadowObserver:
    return PairedExitShadowObserver(
        state_path=root / "state.json",
        event_path=root / "events.jsonl",
        heartbeat_path=root / "heartbeat.json",
        now_func=lambda: "2026-07-15T01:00:00+00:00",
    )


def _snapshot(*prices: float) -> dict:
    return {
        "stale": False,
        "watermark": f"2026-07-15T{9 + len(prices):02d}:00:00+09:00",
        "bars": [
            {"ts": f"2026-07-15T{9 + idx:02d}:00:00+09:00", "close": price}
            for idx, price in enumerate(prices)
        ],
    }


def test_arm_a_early_exit_and_arm_b_split_runner_diverge() -> None:
    with TemporaryDirectory() as tmp, patch.dict(
        "os.environ", {
            "PATHB_KR_PAIRED_EXIT_SHADOW_ENABLED": "true",
            "PATHB_EARLY_TIER_ENABLED": "true",
        }, clear=False
    ):
        observer = _observer(Path(tmp))
        assert observer.register_position(
            path_run_id="run1",
            ticker="005930",
            session_date="2026-07-15",
            entry_price=100.0,
            qty=10,
            target_price=104.0,
            filled_at="2026-07-15T09:00:00+09:00",
        )

        observer.consume_snapshot("005930", _snapshot(100.0, 102.0, 100.0, 104.0, 105.0, 103.0))
        state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
        row = state["positions"]["run1"]

        assert row["arms"]["A"]["status"] == "CLOSED"
        assert row["arms"]["A"]["exit_owner"] == "early_target"
        assert row["arms"]["B"]["status"] == "CLOSED"
        assert row["arms"]["B"]["events"] == 2
        assert row["arms"]["B"]["split_triggered"] is True
        summary = observer.summary()
        assert summary["paired_triggered_total"] == 1
        assert summary["gate_sample_total"] == 0
        assert summary["clock_status"] == "RUNNING"
        assert summary["statistical_gate_pass"] is False
        assert summary["enforce_ready"] is False
        events = [json.loads(line) for line in (Path(tmp) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        assert any(event["exit_owner"] == "split_runner_partial" for event in events)
        assert all(event["cache_watermark"] for event in events if event["event"] == "VIRTUAL_FILL")
        assert all(event["authority"] == "SHADOW_ONLY_NO_ORDER_EFFECT" for event in events)


def test_one_share_falls_back_to_a_and_is_not_eligible() -> None:
    with TemporaryDirectory() as tmp, patch.dict(
        "os.environ", {
            "PATHB_KR_PAIRED_EXIT_SHADOW_ENABLED": "true",
            "PATHB_EARLY_TIER_ENABLED": "true",
        }, clear=False
    ):
        observer = _observer(Path(tmp))
        observer.register_position(
            path_run_id="run1",
            ticker="005930",
            session_date="2026-07-15",
            entry_price=100.0,
            qty=1,
            target_price=104.0,
            filled_at="2026-07-15T09:00:00+09:00",
        )
        observer.consume_snapshot("005930", _snapshot(100.0, 102.0, 100.0))
        state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
        row = state["positions"]["run1"]

        assert row["eligible"] is False
        assert row["fallback_reason"] == "A_FALLBACK_QTY1"
        assert row["arms"]["B"]["exit_owner"] == "A_FALLBACK_QTY1"
        assert observer.summary()["paired_eligible_total"] == 0
        assert observer.summary()["paired_triggered_total"] == 0
        assert observer.summary()["gate_sample_total"] == 0


def test_stale_snapshot_only_updates_shadow_heartbeat() -> None:
    with TemporaryDirectory() as tmp, patch.dict(
        "os.environ", {
            "PATHB_KR_PAIRED_EXIT_SHADOW_ENABLED": "true",
            "PATHB_EARLY_TIER_ENABLED": "true",
        }, clear=False
    ):
        observer = _observer(Path(tmp))
        observer.register_position(
            path_run_id="run1",
            ticker="005930",
            session_date="2026-07-15",
            entry_price=100.0,
            qty=2,
            target_price=104.0,
        )
        result = observer.consume_snapshot(
            "005930", {"stale": True, "reason": "cache_stale", "watermark": "", "bars": []}
        )
        heartbeat = json.loads((Path(tmp) / "heartbeat.json").read_text(encoding="utf-8"))

        assert result["stale"] is True
        assert heartbeat["status"] == "stale"
        assert heartbeat["last_error"] == "cache_stale"


def test_confirmed_live_fill_overrides_arm_a_but_profit_exit_keeps_b_running() -> None:
    with TemporaryDirectory() as tmp, patch.dict(
        "os.environ", {
            "PATHB_KR_PAIRED_EXIT_SHADOW_ENABLED": "true",
            "PATHB_EARLY_TIER_ENABLED": "true",
        }, clear=False
    ):
        observer = _observer(Path(tmp))
        observer.register_position(
            path_run_id="run1",
            ticker="005930",
            session_date="2026-07-15",
            entry_price=100.0,
            qty=10,
            target_price=104.0,
            filled_at="2026-07-15T09:00:00+09:00",
        )
        observer.consume_snapshot("005930", _snapshot(100.0, 102.0, 100.0))
        assert observer.record_live_exit(
            path_run_id="run1",
            price=101.0,
            close_reason="CLOSED_PROFIT_LADDER",
            filled_at="2026-07-15T11:30:00+09:00",
            execution_id="sell-1",
        )

        state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
        row = state["positions"]["run1"]
        assert row["arms"]["A"]["baseline_source"] == "broker_truth"
        assert row["arms"]["A"]["exit_owner"] == "live:CLOSED_PROFIT_LADDER"
        assert round(row["arms"]["A"]["realized_net_contribution_pct"], 2) == 0.79
        assert row["arms"]["B"]["status"] == "PRE_SPLIT"


def test_confirmed_live_safety_fill_closes_both_arms() -> None:
    with TemporaryDirectory() as tmp, patch.dict(
        "os.environ", {
            "PATHB_KR_PAIRED_EXIT_SHADOW_ENABLED": "true",
            "PATHB_EARLY_TIER_ENABLED": "true",
        }, clear=False
    ):
        observer = _observer(Path(tmp))
        observer.register_position(
            path_run_id="run1",
            ticker="005930",
            session_date="2026-07-15",
            entry_price=100.0,
            qty=10,
            target_price=104.0,
            hard_stop=98.0,
        )
        observer.record_live_exit(
            path_run_id="run1",
            price=97.8,
            close_reason="CLOSED_HARD_STOP",
            filled_at="2026-07-15T10:00:00+09:00",
        )

        state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
        row = state["positions"]["run1"]
        assert row["arms"]["A"]["status"] == "CLOSED"
        assert row["arms"]["B"]["status"] == "CLOSED"
        assert row["arms"]["B"]["exit_owner"] == "shared_safety:CLOSED_HARD_STOP"


def test_forward_gate_requires_completed_robust_paired_samples() -> None:
    with TemporaryDirectory() as tmp, patch.dict(
        "os.environ", {
            "PATHB_KR_PAIRED_EXIT_SHADOW_ENABLED": "true",
            "PATHB_EARLY_TIER_ENABLED": "true",
        }, clear=False
    ):
        observer = _observer(Path(tmp))
        session_dates = ["2026-06-29", "2026-07-06", "2026-07-13"]
        for idx in range(15):
            ticker = f"{100000 + idx:06d}"
            observer.register_position(
                path_run_id=f"run{idx}",
                ticker=ticker,
                session_date=session_dates[idx % len(session_dates)],
                entry_price=100.0,
                qty=10,
                target_price=104.0,
                filled_at="2026-07-01T09:00:00+09:00",
            )
            observer.consume_snapshot(ticker, _snapshot(100.0, 102.0, 100.0, 104.0, 105.0, 103.0))
            observer.record_live_exit(
                path_run_id=f"run{idx}",
                price=101.0,
                close_reason="CLOSED_PROFIT_LADDER",
                filled_at="2026-07-15T14:30:00+09:00",
                execution_id=f"sell-{idx}",
            )

        summary = observer.summary()
        assert summary["gate_sample_total"] == 15
        assert summary["weekly_block_count"] == 3
        assert summary["paired_mean_delta_pct"] > 0
        assert summary["weekly_block_lcb_5pct"] > 0
        assert summary["ex_top3_total_delta_pct"] > 0
        assert summary["statistical_gate_pass"] is True
        # Statistical evidence never grants order authority automatically.
        assert summary["operator_review_candidate"] is True
        assert summary["enforce_ready"] is False
