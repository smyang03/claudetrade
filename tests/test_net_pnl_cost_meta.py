"""net P&L cost meta: 수수료/환율 반영 청산 기록 검증."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.v2 import V2Config
from execution.claude_price_adapter import ClaudePriceAdapter
from execution.claude_price_sell_manager import ClaudePriceSellManager, _fee_rates_for_market
from decision.claude_price_plan import make_price_plan
from lifecycle.event_store import EventStore


def _register_filled_run(store, adapter, *, market="US", entry=100.0, qty=5, usd_krw_at_fill=0.0):
    plan = make_price_plan(
        decision_id="dec_cost_meta",
        ticker="TEST" if market == "US" else "005930",
        market=market,
        session_date="2026-06-10",
        buy_zone_low=entry * 0.98,
        buy_zone_high=entry,
        sell_target=entry * 1.06,
        stop_loss=entry * 0.94,
        hold_days=1,
        confidence=0.6,
        cancel_if_open_above=entry * 1.1,
    )
    run_id = adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="bs_test")
    adapter.mark_filled(
        run_id,
        price=entry,
        qty=qty,
        execution_id="EX1",
        runtime_mode="live",
        brain_snapshot_id="bs_test",
        usd_krw=usd_krw_at_fill,
    )
    return run_id


class NetPnlCostMetaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.tmp.name) / "events.db")
        self.adapter = ClaudePriceAdapter(self.store, config=V2Config())
        self.manager = ClaudePriceSellManager(self.adapter, config=V2Config())

    def tearDown(self):
        self.tmp.cleanup()

    def test_us_close_records_fee_and_fx_meta(self):
        run_id = _register_filled_run(self.store, self.adapter, entry=100.0, qty=5, usd_krw_at_fill=1350.0)
        with mock.patch.dict(os.environ, {"US_FEE_RATE_PER_SIDE": "0.0025"}):
            self.manager.mark_closed(
                run_id,
                close_reason="CLOSED_CLAUDE_PRICE_TARGET",
                price=105.0,
                pnl_pct=5.0,
                runtime_mode="live",
                brain_snapshot_id="bs_test",
                usd_krw=1360.0,
            )
        run = self.store.find_path_run(run_id)
        plan = run["plan"]
        self.assertEqual(plan["entry_fx"], 1350.0)
        self.assertEqual(plan["exit_fx"], 1360.0)
        self.assertAlmostEqual(plan["fee_pct_round_trip"], 0.5, places=4)
        self.assertAlmostEqual(plan["pnl_pct_net_est"], 4.5, places=4)
        # 진입 100×5×1350=675,000 / 청산 105×5×1360=714,000
        # 수수료 675,000×0.0025 + 714,000×0.0025 = 3,472.5
        self.assertAlmostEqual(plan["fee_krw_est"], 3472.0, delta=1.0)
        self.assertAlmostEqual(plan["pnl_krw_net_est"], 714000 - 675000 - 3472.5, delta=1.0)
        self.assertGreater(plan["fx_change_pct"], 0)

    def test_us_fill_fx_fallback_to_close_fx(self):
        run_id = _register_filled_run(self.store, self.adapter, entry=100.0, qty=5, usd_krw_at_fill=0.0)
        self.manager.mark_closed(
            run_id,
            close_reason="CLOSED_LOSS_CAP",
            price=98.0,
            pnl_pct=-2.0,
            runtime_mode="live",
            brain_snapshot_id="bs_test",
            usd_krw=1350.0,
        )
        plan = self.store.find_path_run(run_id)["plan"]
        self.assertEqual(plan["entry_fx"], 1350.0)
        self.assertEqual(plan["exit_fx"], 1350.0)
        self.assertEqual(plan["fx_change_pct"], 0.0)

    def test_kr_close_uses_kr_fee_rates_without_fx(self):
        run_id = _register_filled_run(self.store, self.adapter, market="KR", entry=70000.0, qty=7)
        self.manager.mark_closed(
            run_id,
            close_reason="CLOSED_PROFIT_LADDER",
            price=71500.0,
            pnl_pct=2.14,
            runtime_mode="live",
            brain_snapshot_id="bs_test",
        )
        plan = self.store.find_path_run(run_id)["plan"]
        self.assertNotIn("entry_fx", plan)
        self.assertAlmostEqual(plan["fee_pct_round_trip"], 0.21, places=4)
        expected_fee = 70000 * 7 * 0.00015 + 71500 * 7 * 0.00195
        self.assertAlmostEqual(plan["fee_krw_est"], round(expected_fee, 0), delta=1.0)

    def test_missing_entry_data_keeps_close_working_without_meta(self):
        plan = make_price_plan(
            decision_id="dec_no_fill",
            ticker="NOFILL",
            market="US",
            session_date="2026-06-10",
            buy_zone_low=98,
            buy_zone_high=100,
            sell_target=105,
            stop_loss=95,
            hold_days=1,
            confidence=0.6,
            cancel_if_open_above=110,
        )
        run_id = self.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="bs_test")
        # fill 없이 직접 close — cost meta 없이도 기존 기록은 유지돼야 한다
        self.manager.mark_closed(
            run_id,
            close_reason="CLOSED_USER_MANUAL",
            price=101.0,
            pnl_pct=0.0,
            runtime_mode="live",
            brain_snapshot_id="bs_test",
            usd_krw=1350.0,
        )
        run = self.store.find_path_run(run_id)
        self.assertEqual(run["status"], "CLOSED")
        self.assertNotIn("pnl_krw_net_est", run["plan"])

    def test_fee_rates_env_override(self):
        with mock.patch.dict(os.environ, {"US_FEE_RATE_PER_SIDE": "0.0007"}):
            buy, sell = _fee_rates_for_market("US")
        self.assertEqual(buy, 0.0007)
        self.assertEqual(sell, 0.0007)


if __name__ == "__main__":
    unittest.main()
