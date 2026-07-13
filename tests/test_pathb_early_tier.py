from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from decision.claude_price_plan import make_price_plan
from lifecycle.event_store import EventStore
from runtime.pathb_runtime import PathBRuntime


class _Risk:
    def __init__(self) -> None:
        self.positions: list[dict] = []


class _Bot:
    def __init__(self) -> None:
        self.risk = _Risk()
        self.usd_krw_rate = 1350.0
        self.price_cache = {}
        self.price_cache_raw = {}
        self.today_judgment = {"digest_prompt": ""}
        self.session_active = True
        self.current_market = "US"
        self.v2 = SimpleNamespace(brain_snapshot_ids={"US": "brain_us", "KR": "brain_kr"})

    def _current_session_date_str(self, market: str) -> str:
        return "2026-05-13"

    def _save_positions(self) -> None:
        pass

    def _build_intraday_context(self, market: str) -> str:
        return ""

    def _advisor_pos(self, pos: dict, market: str) -> dict:
        return dict(pos)

    def _minutes_to_close(self, market: str) -> float:
        return 120.0


def _us_plan():
    # entry 70.0, target 74.0 → 목표거리 5.714%. fraction 0.4 → act 2.286%
    return make_price_plan(
        decision_id="dec_US",
        ticker="HALO",
        market="US",
        session_date="2026-05-13",
        buy_zone_low=68.5,
        buy_zone_high=71.5,
        sell_target=74.0,
        stop_loss=67.0,
        hold_days=1,
        confidence=0.72,
    )


def _runtime_with_plan(tmp: str):
    bot = _Bot()
    store = EventStore(Path(tmp) / "events.db")
    runtime = PathBRuntime(bot, is_paper=False, store=store)
    plan = _us_plan()
    store.create_path_run(
        path_run_id=plan.path_run_id,
        decision_id=plan.decision_id,
        path_type="claude_price",
        market="US",
        runtime_mode=runtime.mode,
        session_date=plan.session_date,
        ticker=plan.ticker,
        status="FILLED",
        plan={**plan.to_dict(), "actual_entry_price": 70.0},
    )
    return runtime, plan


class PathBEarlyTierTests(unittest.TestCase):
    def test_early_tier_owns_floor_when_higher_than_absolute_tier(self) -> None:
        # MFE 3.0% (peak 72.1) ≥ act 2.286% → early floor 72.1×0.994=71.66 > tier3 floor 72.1×0.990=71.37
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"PATHB_EARLY_TIER_ENABLED": "true"}, clear=False
        ):
            runtime, plan = _runtime_with_plan(tmp)
            pos = {"ticker": "HALO", "entry": 70.0, "peak_pnl_pct": 3.0, "pathb_path_run_id": plan.path_run_id}
            info = runtime._pathb_profit_ladder_floor(plan, pos, 71.5, "US")
            self.assertEqual(info.get("tier"), "early_target")
            self.assertGreater(float(info["floor"]), 71.5)
            self.assertAlmostEqual(float(info["early_tier_act_pct"]), 2.2857, places=3)

    def test_early_tier_disabled_by_default_keeps_absolute_tiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime_with_plan(tmp)
            pos = {"ticker": "HALO", "entry": 70.0, "peak_pnl_pct": 3.0, "pathb_path_run_id": plan.path_run_id}
            info = runtime._pathb_profit_ladder_floor(plan, pos, 71.5, "US")
            self.assertEqual(info.get("tier"), "tier3")

    def test_early_tier_not_active_below_act_threshold(self) -> None:
        # MFE 2.0% < act 2.286% → early 미발동, 절대 tier2가 그대로
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"PATHB_EARLY_TIER_ENABLED": "true"}, clear=False
        ):
            runtime, plan = _runtime_with_plan(tmp)
            pos = {"ticker": "HALO", "entry": 70.0, "peak_pnl_pct": 2.0, "pathb_path_run_id": plan.path_run_id}
            info = runtime._pathb_profit_ladder_floor(plan, pos, 71.0, "US")
            self.assertEqual(info.get("tier"), "tier2")

    def test_early_tier_floor_never_below_entry_and_max_merge(self) -> None:
        # giveback 5%면 early floor는 entry로 클램프(70.0) → 절대 tier3 floor(71.37)가 더 높아 이긴다
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {"PATHB_EARLY_TIER_ENABLED": "true", "PATHB_EARLY_TIER_PEAK_GIVEBACK_PCT": "0.05"},
            clear=False,
        ):
            runtime, plan = _runtime_with_plan(tmp)
            pos = {"ticker": "HALO", "entry": 70.0, "peak_pnl_pct": 3.0, "pathb_path_run_id": plan.path_run_id}
            info = runtime._pathb_profit_ladder_floor(plan, pos, 71.5, "US")
            self.assertEqual(info.get("tier"), "tier3")
            self.assertGreater(float(info["floor"]), 70.0)

    def test_early_tier_market_fraction_override(self) -> None:
        # US fraction 0.8 → act 4.571% > MFE 3.0% → 미발동(절대 tier3 유지)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {"PATHB_EARLY_TIER_ENABLED": "true", "PATHB_EARLY_TIER_TARGET_FRACTION_US": "0.8"},
            clear=False,
        ):
            runtime, plan = _runtime_with_plan(tmp)
            pos = {"ticker": "HALO", "entry": 70.0, "peak_pnl_pct": 3.0, "pathb_path_run_id": plan.path_run_id}
            info = runtime._pathb_profit_ladder_floor(plan, pos, 71.5, "US")
            self.assertEqual(info.get("tier"), "tier3")

    def test_early_tier_signal_fires_full_chain(self) -> None:
        # floor 71.66 위에서 눌려 current 71.6 < floor → profit_ladder SELL 시그널
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {"PATHB_EARLY_TIER_ENABLED": "true", "PATHB_LADDER_MIN_HOLD_SEC": "0"},
            clear=False,
        ):
            runtime, plan = _runtime_with_plan(tmp)
            pos = {"ticker": "HALO", "entry": 70.0, "peak_pnl_pct": 3.0, "pathb_path_run_id": plan.path_run_id}
            signal = runtime._pathb_profit_ladder_signal(plan, pos, 71.6, "US")
            self.assertIsNotNone(signal)
            self.assertEqual(signal.reason, "profit_ladder")
            self.assertEqual(signal.close_reason, "CLOSED_PROFIT_LADDER")


if __name__ == "__main__":
    unittest.main()
