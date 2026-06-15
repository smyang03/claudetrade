"""PathB 약한 포지션 조기정리(weak-MFE early cut) 단위 테스트.

진입 후 관찰창 동안 MFE가 임계 미만이고 손실 중인 포지션을 loss_cap까지 끌지 않고
조기 청산하는 신호를 검증한다. 하드스톱/loss_cap 도달 구간은 기존 경로가 처리하도록
우회하고, KR/US 임계가 분리되며, 토글 off면 무동작인지 확인한다.
"""

import os
import types
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from runtime.pathb_runtime import KST, PathBRuntime
from execution.claude_price_sell_manager import ExitSignal


def _plan(market="US"):
    return types.SimpleNamespace(path_run_id="run1", market=market)


def _aged_pos(entry=100.0, mfe_pct=0.3, age_min=31.0):
    """관찰창을 넘긴(age_min 경과) 포지션. observed_mfe_pct를 직접 세팅."""
    filled = (datetime.now(KST) - timedelta(minutes=age_min)).isoformat(timespec="seconds")
    return {
        "entry": entry,
        "filled_at": filled,
        "observed_mfe_pct": mfe_pct,
    }


_ON = {
    "US_PATHB_WEAK_MFE_CUT_ENABLED": "true",
    "KR_PATHB_WEAK_MFE_CUT_ENABLED": "true",
    "PATHB_WEAK_MFE_CUT_MIN_AGE_MIN": "30",
    "PATHB_WEAK_MFE_CUT_MFE_MAX_PCT": "0.5",
    "PATHB_WEAK_MFE_CUT_MIN_LOSS_PCT": "0.0",
}


class WeakMfeCutTests(unittest.TestCase):
    def setUp(self):
        self.rt = PathBRuntime.__new__(PathBRuntime)
        # _position_entry_native가 US에서 _usd_krw()(self.bot)를 호출한다. entry<1000이라 fx 변환은
        # 안 타지만 호출 자체는 발생하므로 최소 stub을 둔다. runtime_config 미보유 → env fallback.
        self.rt.bot = types.SimpleNamespace(usd_krw_rate=1350)

    def test_fires_when_weak_mfe_and_loss_after_window(self):
        with patch.dict(os.environ, _ON, clear=False):
            pos = _aged_pos(entry=100.0, mfe_pct=0.3, age_min=31.0)
            sig = self.rt._pathb_weak_mfe_cut_signal(_plan("US"), pos, 98.5, "US")
        self.assertIsNotNone(sig)
        self.assertEqual(sig.reason, "weak_mfe_cut")
        self.assertEqual(sig.close_reason, "CLOSED_WEAK_MFE")

    def test_no_signal_before_observation_window(self):
        with patch.dict(os.environ, _ON, clear=False):
            pos = _aged_pos(entry=100.0, mfe_pct=0.3, age_min=10.0)  # 10분 < 30분
            sig = self.rt._pathb_weak_mfe_cut_signal(_plan("US"), pos, 98.5, "US")
        self.assertIsNone(sig)

    def test_no_signal_when_mfe_above_threshold(self):
        with patch.dict(os.environ, _ON, clear=False):
            pos = _aged_pos(entry=100.0, mfe_pct=1.2, age_min=31.0)  # mfe 1.2% >= 0.5%
            sig = self.rt._pathb_weak_mfe_cut_signal(_plan("US"), pos, 98.5, "US")
        self.assertIsNone(sig)

    def test_no_signal_when_currently_in_profit(self):
        with patch.dict(os.environ, _ON, clear=False):
            pos = _aged_pos(entry=100.0, mfe_pct=0.3, age_min=31.0)
            sig = self.rt._pathb_weak_mfe_cut_signal(_plan("US"), pos, 100.5, "US")  # 현재 +0.5%
        self.assertIsNone(sig)

    def test_bypassed_when_loss_cap_already_breached(self):
        with patch.dict(os.environ, _ON, clear=False):
            pos = _aged_pos(entry=100.0, mfe_pct=0.3, age_min=31.0)
            # 현재가 <= loss_cap → 기존 loss_cap 경로가 처리해야 함
            sig = self.rt._pathb_weak_mfe_cut_signal(
                _plan("US"), pos, 97.0, "US", loss_cap_price=97.5
            )
        self.assertIsNone(sig)

    def test_bypassed_when_hard_stop_already_breached(self):
        with patch.dict(os.environ, _ON, clear=False):
            pos = _aged_pos(entry=100.0, mfe_pct=0.3, age_min=31.0)
            sig = self.rt._pathb_weak_mfe_cut_signal(
                _plan("US"), pos, 97.0, "US", hard_stop_price=97.5
            )
        self.assertIsNone(sig)

    def test_disabled_when_toggle_off(self):
        env = {**_ON, "US_PATHB_WEAK_MFE_CUT_ENABLED": "false", "PATHB_WEAK_MFE_CUT_ENABLED": "false"}
        with patch.dict(os.environ, env, clear=False):
            pos = _aged_pos(entry=100.0, mfe_pct=0.3, age_min=31.0)
            sig = self.rt._pathb_weak_mfe_cut_signal(_plan("US"), pos, 98.5, "US")
        self.assertIsNone(sig)

    def test_per_market_independent_toggle(self):
        env = {**_ON, "US_PATHB_WEAK_MFE_CUT_ENABLED": "true", "KR_PATHB_WEAK_MFE_CUT_ENABLED": "false",
               "PATHB_WEAK_MFE_CUT_ENABLED": "false"}
        with patch.dict(os.environ, env, clear=False):
            us_pos = _aged_pos(entry=100.0, mfe_pct=0.3, age_min=31.0)
            kr_pos = _aged_pos(entry=100.0, mfe_pct=0.3, age_min=31.0)
            us_sig = self.rt._pathb_weak_mfe_cut_signal(_plan("US"), us_pos, 98.5, "US")
            kr_sig = self.rt._pathb_weak_mfe_cut_signal(_plan("KR"), kr_pos, 98.5, "KR")
        self.assertIsNotNone(us_sig)
        self.assertIsNone(kr_sig)

    def test_no_signal_when_mfe_not_tracked_yet(self):
        with patch.dict(os.environ, _ON, clear=False):
            pos = _aged_pos(entry=100.0, mfe_pct=0.3, age_min=31.0)
            pos.pop("observed_mfe_pct")  # 아직 추적 전
            sig = self.rt._pathb_weak_mfe_cut_signal(_plan("US"), pos, 98.5, "US")
        self.assertIsNone(sig)


if __name__ == "__main__":
    unittest.main()
