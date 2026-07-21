from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import risk_manager as risk_module
from risk_manager import RiskManager
from runtime.pathb_runtime import PathBRuntime
from runtime.v2_lifecycle_runtime import v2_close_reason
from trading_bot import TradingBot


def _kr_position(**overrides):
    pos = {
        "ticker": "058430",
        "entry": 10_000.0,
        "qty": 10,
        "current_price": 9_690.0,
        "strategy": "claude_price",
        "tp": 12_000.0,
        "sl": 9_400.0,
        "held_days": 0,
        "max_hold": 10,
        "peak_pnl_pct": 0.0,
    }
    pos.update(overrides)
    return pos


class LossCapProfitFloorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patch = patch.dict(
            "os.environ",
            {
                "KR_MAX_SINGLE_LOSS_PCT": "",
                "US_MAX_SINGLE_LOSS_PCT": "",
                "KR_LOSS_CAP_SHADOW_PCT": "",
                "US_LOSS_CAP_SHADOW_PCT": "",
                "LOSS_CAP_SHADOW_PCT": "",
            },
        )
        self._env_patch.start()
        self._old_single_loss = risk_module.HARD_RULES["max_single_loss_pct"]
        self._old_session_cap = risk_module.POSITION_SESSION_LOSS_CAP_PCT
        self._old_auto_trail_pct_kr = risk_module.AUTO_TRAIL_PCT_KR
        risk_module.HARD_RULES["max_single_loss_pct"] = -3.0
        risk_module.POSITION_SESSION_LOSS_CAP_PCT = 0.5
        risk_module.AUTO_TRAIL_PCT_KR = 0.02

    def tearDown(self) -> None:
        risk_module.HARD_RULES["max_single_loss_pct"] = self._old_single_loss
        risk_module.POSITION_SESSION_LOSS_CAP_PCT = self._old_session_cap
        risk_module.AUTO_TRAIL_PCT_KR = self._old_auto_trail_pct_kr
        self._env_patch.stop()

    def test_kr_loss_cap_overlays_wide_strategy_stop(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [_kr_position()]

        candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "loss_cap")
        self.assertAlmostEqual(candidates[0]["loss_cap_price"], 9_700.0)
        self.assertAlmostEqual(candidates[0]["effective_stop_price"], 9_700.0)

    def test_kr_market_loss_cap_override_records_shadow_cap(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [_kr_position(current_price=9_790.0)]

        with patch.dict("os.environ", {"KR_MAX_SINGLE_LOSS_PCT": "-2.0", "KR_LOSS_CAP_SHADOW_PCT": "1.5"}):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "loss_cap")
        self.assertAlmostEqual(candidates[0]["loss_cap_pct"], 2.0)
        self.assertAlmostEqual(candidates[0]["loss_cap_price"], 9_800.0)
        self.assertAlmostEqual(candidates[0]["effective_stop_price"], 9_800.0)
        self.assertAlmostEqual(candidates[0]["loss_cap_shadow_pct"], 1.5)
        self.assertAlmostEqual(candidates[0]["loss_cap_shadow_price"], 9_850.0)
        self.assertTrue(candidates[0]["loss_cap_shadow_triggered"])

    def test_strategy_stop_keeps_reason_when_tighter_than_loss_cap(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [_kr_position(sl=9_800.0, current_price=9_790.0)]

        candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "stop_loss")
        self.assertAlmostEqual(candidates[0]["effective_stop_price"], 9_800.0)

    def test_profit_floor_exits_after_peak_gives_back_to_floor(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [_kr_position(current_price=10_040.0, peak_pnl_pct=2.5)]

        candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "profit_floor")
        self.assertAlmostEqual(candidates[0]["profit_floor_price"], 10_050.0)
        self.assertTrue(candidates[0]["profit_floor_triggered"])

    def test_profit_floor_does_not_exit_while_price_is_above_floor(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [_kr_position(current_price=10_051.0, peak_pnl_pct=2.5)]

        candidates = risk.get_exit_candidates()

        self.assertEqual(candidates, [])

    def test_kr_auto_trailing_uses_tighter_market_default(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="KR")
        risk.reset_daily_state(override_base=1_000_000)
        opened = risk.open_position("010170", price=29_600.0, qty=6, strategy="momentum", tp_pct=0.06, sl_pct=0.03)
        self.assertTrue(opened)

        pos = risk.positions[0]
        risk.update_prices({"010170": 30_500.0})

        self.assertTrue(pos["trailing"])
        self.assertAlmostEqual(pos["trail_pct"], risk_module.AUTO_TRAIL_PCT_KR)
        self.assertAlmostEqual(pos["trail_sl"], 30_500.0 * (1 - risk_module.AUTO_TRAIL_PCT_KR))

    def test_max_hold_no_longer_creates_exit_candidate(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [
            _kr_position(
                current_price=10_100.0,
                sl=9_000.0,
                held_days=30,
                max_hold=1,
                peak_pnl_pct=0.0,
            )
        ]

        candidates = risk.get_exit_candidates()

        self.assertEqual(candidates, [])

    def test_exit_candidate_includes_position_mfe_and_mae(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [_kr_position(peak_pnl_pct=2.5, trough_pnl_pct=-2.2)]

        candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "loss_cap")
        self.assertAlmostEqual(candidates[0]["position_mfe_pct"], 2.5)
        self.assertAlmostEqual(candidates[0]["position_mae_pct"], -2.2)

    def test_kr_position_without_tp_does_not_crash_exit_scan(self) -> None:
        # tp 키 없는 KR 포지션(legacy/외부주입)이 tp_check 분기에 도달해도
        # KeyError 없이 처리돼야 한다(get_exit_candidates 전체 중단 방지).
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        pos = _kr_position(current_price=10_500.0)
        pos.pop("tp", None)
        risk.positions = [pos]

        candidates = risk.get_exit_candidates()

        # tp 미설정 → tp_check 미발동, 손절/플로어도 미해당 → 후보 없음(예외 없이)
        self.assertEqual([c.get("reason") for c in candidates], [])

    def test_us_loss_cap_uses_native_usd_stop(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="US")
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [
            {
                "ticker": "TSLA",
                "entry": 150_000.0,
                "qty": 1,
                "current_price": 145_350.0,
                "display_currency": "USD",
                "display_avg_price": 100.0,
                "display_current_price": 96.9,
                "strategy": "momentum",
                "tp": 180_000.0,
                "sl": 135_000.0,
                "tp_pct": 0.20,
                "sl_pct": 0.10,
                "held_days": 0,
                "max_hold": 10,
                "peak_pnl_pct": 0.0,
            }
        ]

        candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "loss_cap")
        self.assertAlmostEqual(candidates[0]["loss_cap_price"], 97.0)

    def test_pathb_native_loss_cap_stop_uses_risk_manager(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.bot = SimpleNamespace(risk=risk)

        stop = runtime._native_loss_cap_stop(_kr_position(), "KR")

        self.assertAlmostEqual(stop, 9_700.0)

    def test_broker_recovered_position_keeps_saved_mfe_state(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.usd_krw_rate = 1350.0
        bot._current_session_date_str = lambda market: "2026-05-16"
        bot._lookup_ticker_name = lambda ticker, market: ""
        bot._recover_decision_id = lambda ticker, market: None

        pos = bot._make_runtime_position_from_broker(
            "005930",
            "KR",
            {"avg_price": 100.0, "eval_price": 101.0, "qty": 3},
            template={"peak_pnl_pct": 3.4, "trough_pnl_pct": -1.2, "entry_time": "2026-05-16T09:01:00"},
        )

        self.assertAlmostEqual(pos["position_mfe_pct"], 3.4)
        self.assertAlmostEqual(pos["peak_price_native"], 103.4)
        self.assertAlmostEqual(pos["position_mae_pct"], -1.2)
        self.assertTrue(pos["mfe_floor_active"])
        self.assertEqual(pos["mfe_floor_source"], "cap2_mfe_v1")
        self.assertEqual(pos["mfe_recovery_source"], "positions_file")
        self.assertTrue(pos["broker_position_confirmed"])

    def test_broker_injected_position_uses_current_price_as_minimum_mfe_peak(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.usd_krw_rate = 1350.0
        bot._current_session_date_str = lambda market: "2026-05-16"
        bot._lookup_ticker_name = lambda ticker, market: ""
        bot._recover_decision_id = lambda ticker, market: None

        pos = bot._make_runtime_position_from_broker(
            "AAPL",
            "US",
            {"avg_price": 100.0, "eval_price": 103.0, "qty": 1},
        )

        self.assertAlmostEqual(pos["position_mfe_pct"], 3.0)
        self.assertAlmostEqual(pos["peak_price_native"], 103.0)
        self.assertTrue(pos["mfe_floor_active"])
        self.assertEqual(pos["mfe_recovery_source"], "broker_current")
        self.assertTrue(pos["management_protected"])
        self.assertTrue(pos["broker_position_confirmed"])

    def test_v2_close_reason_maps_new_exit_reasons(self) -> None:
        self.assertEqual(v2_close_reason("loss_cap"), "CLOSED_LOSS_CAP")
        self.assertEqual(v2_close_reason("profit_floor"), "CLOSED_PROFIT_FLOOR")
        self.assertEqual(v2_close_reason("soft_exit_floor_price"), "CLOSED_SOFT_EXIT_FLOOR")
        self.assertEqual(v2_close_reason("recovery_micro_time_stop"), "CLOSED_TIME_STOP")
        self.assertEqual(v2_close_reason("mfe_breakeven"), "CLOSED_MFE_BREAKEVEN")
        self.assertEqual(v2_close_reason("CLOSED_LOSS_CAP"), "CLOSED_LOSS_CAP")

    def test_recovery_micro_time_stop_has_explicit_exit_candidate(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [
            _kr_position(
                current_price=10_010.0,
                sl=9_800.0,
                strategy="RECOVERY_MICRO",
                recovery_micro=True,
                recovery_micro_reason="first_stop_recovery_micro",
                recovery_micro_no_carry=True,
                entry_time=(datetime.now(risk_module.KST) - timedelta(minutes=31)).isoformat(timespec="seconds"),
                recovery_micro_hard_loss_pct=1.5,
                recovery_micro_profit_guard_trigger_pct=1.0,
                recovery_micro_profit_guard_floor_pct=0.2,
                recovery_micro_trail_trigger_pct=1.5,
                recovery_micro_trail_pct=0.9,
                recovery_micro_time_stop_minutes=30,
                recovery_micro_time_stop_min_pnl_pct=0.3,
                recovery_micro_force_time_stop_minutes=45,
                recovery_micro_force_time_stop_min_pnl_pct=0.5,
            )
        ]

        candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "recovery_micro_time_stop")
        self.assertEqual(candidates[0]["recovery_micro_exit_trigger"], "recovery_micro_time_stop")
        self.assertTrue(candidates[0]["recovery_micro_no_carry"])

    def test_recovery_micro_profit_guard_uses_profit_floor_reason(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [
            _kr_position(
                current_price=10_015.0,
                sl=9_800.0,
                strategy="RECOVERY_MICRO",
                recovery_micro=True,
                recovery_micro_reason="first_stop_recovery_micro",
                peak_pnl_pct=1.2,
                entry_time=datetime.now(risk_module.KST).isoformat(timespec="seconds"),
                recovery_micro_hard_loss_pct=1.5,
                recovery_micro_profit_guard_trigger_pct=1.0,
                recovery_micro_profit_guard_floor_pct=0.2,
                recovery_micro_trail_trigger_pct=1.5,
                recovery_micro_trail_pct=0.9,
            )
        ]

        candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "profit_floor")
        self.assertEqual(candidates[0]["recovery_micro_exit_trigger"], "recovery_micro_profit_guard")


class PeakTroughTimestampTests(unittest.TestCase):
    """고점·저점 갱신 시각이 남는지 — MFE/MAE '순서' 판정의 전제.

    크기만으로는 봉우리를 만들고 반납한 건과 되돌림 뒤 오른 건을 구분할 수 없다.
    2026-07-22 백필 실측(US n=159): 고점이 먼저 온 89건 승률 4%(평균 -1.70%),
    저점이 먼저 온 66건 승률 61%(평균 +1.13%). observed_peak_at은 PathB 전용이라
    Path A 포지션(즉시매수 claude_price_a 포함)에는 이 필드가 유일한 순서 근거다.
    """

    def _rm(self):
        rm = RiskManager.__new__(RiskManager)
        rm.market = "KR"
        rm.positions = [_kr_position(current_price=10_000.0, trough_pnl_pct=0.0)]
        return rm

    def test_peak_update_records_timestamp(self) -> None:
        rm = self._rm()
        rm.update_prices({"058430": 10_300.0})
        pos = rm.positions[0]
        self.assertEqual(pos["peak_pnl_pct"], 3.0)
        self.assertTrue(pos.get("peak_pnl_at"), "고점 갱신 시각이 없으면 순서 판정이 불가능하다")

    def test_trough_update_records_timestamp(self) -> None:
        rm = self._rm()
        rm.update_prices({"058430": 9_700.0})
        pos = rm.positions[0]
        self.assertEqual(pos["trough_pnl_pct"], -3.0)
        self.assertTrue(pos.get("trough_pnl_at"))

    def test_peak_then_trough_order_is_recoverable(self) -> None:
        rm = self._rm()
        rm.update_prices({"058430": 10_300.0})   # 고점 먼저
        rm.update_prices({"058430": 9_700.0})    # 이후 저점
        pos = rm.positions[0]
        self.assertLessEqual(pos["peak_pnl_at"], pos["trough_pnl_at"])

    def test_timestamps_absent_until_a_new_extreme_occurs(self) -> None:
        """갱신이 없으면 시각을 위조하지 않는다(빈 값은 빈 값으로 남는다)."""
        rm = self._rm()
        rm.update_prices({"058430": 10_000.0})   # 진입가 그대로 — 갱신 없음
        pos = rm.positions[0]
        self.assertIsNone(pos.get("peak_pnl_at"))


class EarlyPeakExitShadowTests(unittest.TestCase):
    """조기고점 정리 후보를 관측만 하는지 — 주문에는 일절 영향이 없어야 한다.

    백필 실측(US n=159): 진입 후 고점이 먼저 온 89건은 승률 4%. "30분 내 고점 + 하락전환
    + MFE>=0.2%" 정리는 +0.3288%p이고 포기이익이 1.56%p뿐이라 러너를 거의 죽이지 않는다.
    다만 그 시뮬은 사후 순서를 썼으므로, 라이브 판별이 서는지를 shadow로 먼저 쌓는다.
    """

    def _pos(self, *, held_min: float, peak_after_min: float, peak_pct: float) -> dict:
        now = datetime.now(risk_module.KST)
        entered = now - timedelta(minutes=held_min)
        return _kr_position(
            entry_time=entered.isoformat(timespec="seconds"),
            peak_pnl_pct=peak_pct,
            peak_pnl_at=(entered + timedelta(minutes=peak_after_min)).isoformat(timespec="seconds"),
            trough_pnl_pct=0.0,
        )

    def _rm(self):
        rm = RiskManager.__new__(RiskManager)
        rm.market = "KR"
        rm.positions = []
        return rm

    def test_marks_when_peak_is_early_and_price_gave_back(self) -> None:
        rm = self._rm()
        pos = self._pos(held_min=45, peak_after_min=10, peak_pct=1.5)
        rm._mark_early_peak_exit_shadow(pos, 0.5)
        mark = pos.get("early_peak_exit_shadow")
        self.assertTrue(mark)
        self.assertEqual(mark["peak_pnl_pct"], 1.5)
        self.assertAlmostEqual(mark["peak_minutes"], 10.0, delta=0.5)

    def test_does_not_mark_before_window_elapses(self) -> None:
        """창이 지나기 전에는 더 오를 수 있으므로 '조기 고점'으로 확정하지 않는다."""
        rm = self._rm()
        pos = self._pos(held_min=20, peak_after_min=10, peak_pct=1.5)
        rm._mark_early_peak_exit_shadow(pos, 0.5)
        self.assertIsNone(pos.get("early_peak_exit_shadow"))

    def test_does_not_mark_when_peak_is_outside_window(self) -> None:
        rm = self._rm()
        pos = self._pos(held_min=60, peak_after_min=45, peak_pct=1.5)
        rm._mark_early_peak_exit_shadow(pos, 0.5)
        self.assertIsNone(pos.get("early_peak_exit_shadow"))

    def test_does_not_mark_without_giveback(self) -> None:
        rm = self._rm()
        pos = self._pos(held_min=45, peak_after_min=10, peak_pct=1.5)
        rm._mark_early_peak_exit_shadow(pos, 1.45)   # 아직 고점 근처
        self.assertIsNone(pos.get("early_peak_exit_shadow"))

    def test_toggle_off_disables_observation(self) -> None:
        rm = self._rm()
        pos = self._pos(held_min=45, peak_after_min=10, peak_pct=1.5)
        with patch.dict("os.environ", {"EARLY_PEAK_EXIT_SHADOW_MODE": "off"}):
            rm._mark_early_peak_exit_shadow(pos, 0.5)
        self.assertIsNone(pos.get("early_peak_exit_shadow"))

    def test_marks_only_once(self) -> None:
        rm = self._rm()
        pos = self._pos(held_min=45, peak_after_min=10, peak_pct=1.5)
        rm._mark_early_peak_exit_shadow(pos, 0.5)
        first = dict(pos["early_peak_exit_shadow"])
        rm._mark_early_peak_exit_shadow(pos, 0.1)
        self.assertEqual(pos["early_peak_exit_shadow"], first)

    def test_shadow_does_not_touch_exit_fields(self) -> None:
        """관측 표식이 tp/sl/trailing 같은 주문 필드를 건드리지 않는다."""
        rm = self._rm()
        pos = self._pos(held_min=45, peak_after_min=10, peak_pct=1.5)
        before = {k: pos.get(k) for k in ("tp", "sl", "trailing", "trail_sl", "qty")}
        rm._mark_early_peak_exit_shadow(pos, 0.5)
        after = {k: pos.get(k) for k in ("tp", "sl", "trailing", "trail_sl", "qty")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
