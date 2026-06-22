"""hold advisor profit_guard 청산 교훈 forward-validation (#4a) 단위 테스트.

collect/backfill_forward는 logs+yfinance(외부) 의존이라 통합 실행으로 검증했고,
여기서는 순수 로직(익절 필터·rescore 방향·regime 폴백·토글)을 격리 검증한다.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from minority_report import hold_advisor_exit_lessons as h

_COLS = ("sell_key,path_run_id,market,ticker,sell_ts,sell_date,entry_price,realized_net,"
         "hold_fwd_price,hold_fwd_net,regime,hold_mode,judge_pnl,forward_days,synced_at")


def _row(key, market, realized, hold_fwd, regime, date="2026-06-17", mode="profit_pullback"):
    return (key, f"run_{key}", market, "AAA", f"{date}T00:00", date, 100.0, realized,
            100 * (1 + hold_fwd / 100), hold_fwd, regime, mode, None, 3, "now")


class IsProfitExitTests(unittest.TestCase):
    def test_profit_modes_included(self):
        self.assertTrue(h._is_profit_exit("profit_pullback", None))
        self.assertTrue(h._is_profit_exit("target_extension", -1.0))

    def test_judge_pnl_positive_included(self):
        self.assertTrue(h._is_profit_exit("", 0.5))

    def test_loss_exits_excluded(self):
        self.assertFalse(h._is_profit_exit("stop_recovery", -1.0))
        self.assertFalse(h._is_profit_exit("loss_deferral", -2.0))
        self.assertFalse(h._is_profit_exit("", -0.5))
        self.assertFalse(h._is_profit_exit("", None))


class ToggleTests(unittest.TestCase):
    def test_enabled_default_false(self):
        os.environ.pop("HOLD_ADVISOR_EXIT_LESSON_ENABLED", None)
        self.assertFalse(h.enabled())

    def test_enabled_true(self):
        os.environ["HOLD_ADVISOR_EXIT_LESSON_ENABLED"] = "true"
        self.addCleanup(lambda: os.environ.pop("HOLD_ADVISOR_EXIT_LESSON_ENABLED", None))
        self.assertTrue(h.enabled())


class RescoreTests(unittest.TestCase):
    def _seed(self, tmp, rows):
        db = str(Path(tmp) / "d.db")
        con = h._connect(db)
        con.executemany(
            f"INSERT INTO hold_advisor_exit_outcome ({_COLS}) VALUES ({','.join('?' * 15)})", rows)
        con.commit()
        con.close()
        return db

    def test_sell_outperforms_hold_gain_positive(self):
        # 익절(SELL) 실현이 HOLD 지속 forward보다 나으면 gain>0 (profit_guard valid 방향)
        with tempfile.TemporaryDirectory() as tmp:
            rows = [_row("k1", "US", 3.0, 0.5, "risk_on"),
                    _row("k2", "US", 4.0, -1.0, "risk_on"),
                    _row("k3", "US", 2.5, -0.5, "risk_on", date="2026-06-18")]
            db = self._seed(tmp, rows)
            cells = h.rescore(store_db=str(Path(tmp) / "s.db"), db_path=db)
            self.assertEqual(len(cells), 1)
            self.assertGreater(cells[0]["counterfactual_gain"], 0)  # median(3,4,2.5) − median(0.5,-1,-0.5)

    def test_hold_outperforms_sell_gain_negative(self):
        # HOLD가 나으면 gain<0 = profit_guard 조기절단(invalid 방향)
        with tempfile.TemporaryDirectory() as tmp:
            rows = [_row("k1", "US", 0.5, 5.0, "risk_on"),
                    _row("k2", "US", 1.0, 6.0, "risk_on")]
            db = self._seed(tmp, rows)
            cells = h.rescore(store_db=str(Path(tmp) / "s.db"), db_path=db)
            self.assertEqual(len(cells), 1)
            self.assertLess(cells[0]["counterfactual_gain"], 0)

    def test_none_regime_falls_back_to_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._seed(tmp, [_row("k1", "US", 3.0, 0.5, None)])
            cells = h.rescore(store_db=str(Path(tmp) / "s.db"), db_path=db)
            self.assertEqual(cells[0]["regime"], "unknown")

    def test_null_forward_excluded(self):
        # hold_fwd_net NULL(미성숙)은 채점 제외
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "d.db")
            con = h._connect(db)
            con.execute(
                f"INSERT INTO hold_advisor_exit_outcome ({_COLS}) VALUES ({','.join('?' * 15)})",
                ("k1", "run_k1", "US", "AAA", "2026-06-17T00:00", "2026-06-17", 100.0, 3.0,
                 None, None, "risk_on", "profit_pullback", None, None, "now"))
            con.commit()
            con.close()
            cells = h.rescore(store_db=str(Path(tmp) / "s.db"), db_path=db)
            self.assertEqual(len(cells), 0)


if __name__ == "__main__":
    unittest.main()
