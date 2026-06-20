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

    def test_us_close_records_fx_spread_net(self):
        run_id = _register_filled_run(self.store, self.adapter, entry=100.0, qty=5, usd_krw_at_fill=1350.0)
        with mock.patch.dict(os.environ, {"US_FEE_RATE_PER_SIDE": "0.0025", "US_FX_SPREAD_RATE_PER_SIDE": "0.001"}):
            self.manager.mark_closed(
                run_id,
                close_reason="CLOSED_CLAUDE_PRICE_TARGET",
                price=105.0,
                pnl_pct=5.0,
                runtime_mode="live",
                brain_snapshot_id="bs_test",
                usd_krw=1360.0,
            )
        plan = self.store.find_path_run(run_id)["plan"]
        # 기존 net(수수료만)은 불변
        self.assertAlmostEqual(plan["pnl_pct_net_est"], 4.5, places=4)
        # 환전 2회 × 0.1% = 0.2%p 추가 차감 → FX 인지 net
        self.assertAlmostEqual(plan["fx_spread_pct_round_trip"], 0.2, places=4)
        self.assertAlmostEqual(plan["pnl_pct_net_after_fx_est"], 4.3, places=4)
        # KRW: (675,000 + 714,000) × 0.001 = 1,389
        self.assertAlmostEqual(plan["fx_spread_krw_est"], 1389.0, delta=1.0)
        self.assertAlmostEqual(plan["pnl_krw_net_after_fx_est"], 714000 - 675000 - 3472.5 - 1389.0, delta=1.0)

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

    def test_broker_entry_override_takes_priority_over_plan_entry(self):
        # 진입가 기준 = KIS 체결가 (2026-06-11 운영자 확정): plan 기록가(주문가)보다 브로커 단가 우선
        run_id = _register_filled_run(self.store, self.adapter, entry=15.645, qty=10, usd_krw_at_fill=1520.0)
        with mock.patch.dict(os.environ, {"US_FEE_RATE_PER_SIDE": "0.0025"}):
            self.manager.mark_closed(
                run_id,
                close_reason="CLOSED_PROFIT_LADDER",
                price=15.55,
                pnl_pct=-1.29,
                runtime_mode="live",
                brain_snapshot_id="bs_test",
                usd_krw=1520.0,
                entry_native_override=15.75,  # 브로커 체결 평균가
                qty_override=10,
            )
        plan = self.store.find_path_run(run_id)["plan"]
        self.assertEqual(plan["entry_price_source"], "broker_position")
        self.assertEqual(plan["entry_native_used"], 15.75)
        # gross = 15.55/15.75-1 = -1.2698% → net = -1.7698% (수수료 0.5%)
        self.assertAlmostEqual(plan["pnl_pct_net_est"], -1.7698, places=3)

    def test_order_acked_close_backfills_entry_from_broker(self):
        # FUN 레이스 재현: fill 미기록(ORDER_ACKED) 상태 청산 → 브로커 단가로 entry 백필 + net 기록
        plan = make_price_plan(
            decision_id="dec_race", ticker="FUN", market="US", session_date="2026-06-10",
            buy_zone_low=23.5, buy_zone_high=24.2, sell_target=26.5, stop_loss=23.0,
            hold_days=1, confidence=0.5, cancel_if_open_above=25.0,
        )
        run_id = self.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="bs_test")
        # mark_filled 없이 바로 청산 (레이스)
        with mock.patch.dict(os.environ, {"US_FEE_RATE_PER_SIDE": "0.0025"}):
            self.manager.mark_closed(
                run_id,
                close_reason="CLOSED_HARD_STOP",
                price=23.81,
                pnl_pct=-1.78,
                runtime_mode="live",
                brain_snapshot_id="bs_test",
                usd_krw=1520.0,
                entry_native_override=24.24,
                qty_override=13,
            )
        stored = self.store.find_path_run(run_id)["plan"]
        self.assertEqual(stored["actual_entry_price"], 24.24)
        self.assertEqual(stored["filled_qty"], 13)
        self.assertEqual(stored["entry_price_source"], "broker_close_backfill")
        self.assertIn("pnl_krw_net_est", stored)

    def test_no_override_keeps_plan_entry_with_source_tag(self):
        run_id = _register_filled_run(self.store, self.adapter, entry=100.0, qty=5, usd_krw_at_fill=1350.0)
        self.manager.mark_closed(
            run_id,
            close_reason="CLOSED_CLAUDE_PRICE_TARGET",
            price=105.0, pnl_pct=5.0,
            runtime_mode="live", brain_snapshot_id="bs_test", usd_krw=1350.0,
        )
        plan = self.store.find_path_run(run_id)["plan"]
        self.assertEqual(plan["entry_price_source"], "plan_recorded")

    def test_fee_rates_env_override(self):
        with mock.patch.dict(os.environ, {"US_FEE_RATE_PER_SIDE": "0.0007"}):
            buy, sell = _fee_rates_for_market("US")
        self.assertEqual(buy, 0.0007)
        self.assertEqual(sell, 0.0007)

    def _closed_event_payload(self, run_id):
        run = self.store.find_path_run(run_id)
        events = self.store.events_for_decision(str(run["decision_id"]))
        closed = [e for e in events if str(e.get("event_type")) == "CLOSED"]
        self.assertTrue(closed, "CLOSED 이벤트가 있어야 한다")
        return closed[-1].get("payload") or {}

    def test_closed_event_carries_mfe_mae_for_learning_sync(self):
        # 배선 버그 회귀: Phase 1c MFE가 CLOSED payload까지 전달돼 학습 sync가 읽을 수 있어야 한다.
        run_id = _register_filled_run(self.store, self.adapter, entry=100.0, qty=5, usd_krw_at_fill=1350.0)
        self.manager.mark_closed(
            run_id,
            close_reason="CLOSED_PROFIT_LADDER",
            price=105.0, pnl_pct=5.0,
            runtime_mode="live", brain_snapshot_id="bs_test", usd_krw=1350.0,
            mfe_pct=8.3, mae_pct=-1.2, entry_market_regime="RISK_ON",
        )
        payload = self._closed_event_payload(run_id)
        self.assertAlmostEqual(payload.get("position_mfe_pct"), 8.3, places=4)
        self.assertAlmostEqual(payload.get("position_mae_pct"), -1.2, places=4)
        self.assertEqual(payload.get("entry_market_regime"), "RISK_ON")

    def test_closed_event_omits_mfe_when_unknown(self):
        # mfe 미제공(reconcile 등 pos 없음)이면 0을 위조하지 않고 키를 생략한다.
        run_id = _register_filled_run(self.store, self.adapter, entry=100.0, qty=5, usd_krw_at_fill=1350.0)
        self.manager.mark_closed(
            run_id,
            close_reason="CLOSED_LOSS_CAP",
            price=98.0, pnl_pct=-2.0,
            runtime_mode="live", brain_snapshot_id="bs_test", usd_krw=1350.0,
        )
        payload = self._closed_event_payload(run_id)
        self.assertNotIn("position_mfe_pct", payload)
        self.assertNotIn("position_mae_pct", payload)
        self.assertNotIn("entry_market_regime", payload)


if __name__ == "__main__":
    unittest.main()
