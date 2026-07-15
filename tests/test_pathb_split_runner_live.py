from __future__ import annotations

from types import SimpleNamespace

from decision.claude_price_plan import PricePlan
from runtime.pathb_runtime import PathBRuntime


class Store:
    def __init__(self) -> None:
        self.plan = {"exit_policy_version": "SPLIT_RUNNER_V1", "split_runner_original_qty": 4}

    def find_path_run(self, _: str) -> dict:
        return {"plan": dict(self.plan)}

    def update_path_run(self, _: str, *, plan: dict, merge_plan: bool = False, **__) -> None:
        assert merge_plan
        self.plan.update(plan)


def _runtime() -> PathBRuntime:
    rt = PathBRuntime.__new__(PathBRuntime)
    rt.mode = "live"
    rt.store = Store()
    rt.bot = SimpleNamespace(_token_for_market=lambda *_, **__: "token")
    rt.adapter = SimpleNamespace(mark_order_unknown=lambda *_, **__: None)
    rt._runtime_bool = lambda key, default=False: key == "PATHB_KR_SPLIT_RUNNER_LIVE_ENABLED"
    rt._runtime_value = lambda key, default="": (
        "I_ACCEPT_LIVE_KR_SPLIT_RUNNER" if key == "PATHB_KR_SPLIT_RUNNER_LIVE_ACK" else default
    )
    rt._position_entry_native = lambda *_: 10000.0
    rt._compute_sell_order_price = lambda *args: float(args[-1])
    rt._save_positions_if_possible = lambda: None
    return rt


def _plan(*, sell_target: float = 11000) -> PricePlan:
    return PricePlan(
        decision_id="decision",
        path_run_id="run",
        ticker="005930",
        market="KR",
        session_date="2026-07-15",
        buy_zone_low=9900,
        buy_zone_high=10000,
        sell_target=sell_target,
        stop_loss=9500,
        hold_days=2,
        confidence=0.6,
    )


def test_split_runner_submits_only_half_and_leaves_runner(monkeypatch) -> None:
    rt = _runtime()
    pos = {"ticker": "005930", "qty": 4, "entry": 10000.0}
    monkeypatch.setattr("runtime.pathb_runtime.precheck_order", lambda *_, **__: {"ok": True})
    monkeypatch.setattr(
        "runtime.pathb_runtime.place_order",
        lambda *args, **__: {"success": True, "order_no": "split-1", "qty": args[1]},
    )
    result = rt._maybe_submit_kr_split_runner_partial(_plan(), pos, 10400.0)
    assert result["owns_profit"] is True
    assert result["status"] == "PENDING"
    assert pos["pending_sell_qty"] == 2
    assert pos["qty"] == 4
    assert rt.store.plan["split_runner_exit_owner"] == "split_runner_partial"


def test_one_share_position_falls_back_to_early_full_without_order(monkeypatch) -> None:
    rt = _runtime()
    called = []
    monkeypatch.setattr("runtime.pathb_runtime.place_order", lambda *_, **__: called.append(True))
    pos = {"ticker": "005930", "qty": 1, "entry": 10000.0}
    result = rt._maybe_submit_kr_split_runner_partial(_plan(), pos, 10400.0)
    assert result["owns_profit"] is False
    assert result["status"] == "A_FALLBACK_QTY1"
    assert called == []


def test_missing_live_ack_fails_closed_to_original_exit_policy() -> None:
    rt = _runtime()
    rt._runtime_value = lambda *_: "wrong"
    policy, _ = rt._pathb_kr_exit_policy(_plan(), {"qty": 4})
    assert policy == "EARLY_FULL_V1"


def test_plan_target_below_split_trigger_falls_back_to_original_exit() -> None:
    rt = _runtime()
    pos = {"ticker": "005930", "qty": 4, "entry": 10000.0}
    result = rt._maybe_submit_kr_split_runner_partial(_plan(sell_target=10300), pos, 10200.0)
    assert result["owns_profit"] is False
    assert result["status"] == "A_FALLBACK_TARGET_BELOW_TRIGGER"
    assert rt.store.plan["split_runner_fallback_reason"] == "PLAN_TARGET_BELOW_SPLIT_TRIGGER"
