# -*- coding: utf-8 -*-
"""픽 근거 한 줄(pick_basis) — 대시보드 '픽 근거' 열·관측기 원장·trades.meta.basis (2026-09-03).

계약: 모든 STRATEGIES의 pick 키가 라벨을 갖고, 시장별 근거 문구에 결정 피처가 들어가며,
피처가 없어도 예외 없이 '-'로 표기한다. backfill_basis는 멱등이다.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import virtual_books as vb  # noqa: E402


class PickBasisTest(unittest.TestCase):
    def test_every_pick_key_has_label(self):
        missing = {s["pick"] for s in vb.STRATEGIES} - set(vb.PICK_LABEL)
        self.assertEqual(missing, set())

    def test_kr_basis_has_rule_and_features(self):
        s = {"id": "kr_r4x", "universe": "kr", "pick": "disc_deep"}
        c = {"ticker": "348340", "disc": -19.36, "gap": -4.28, "chg": -8.73, "rv20": 4.43,
             "from_high20": -41.05, "r2": False, "r4x": True}
        b = vb.pick_basis(s, c)
        for token in ("할인 -19.4%", "갭 -4.3%", "전일 -8.7%", "rv20 4.4", "R4", "할인 깊은순"):
            self.assertIn(token, b)
        c["r2"] = True
        self.assertIn("R2", vb.pick_basis(s, c))

    def test_us_basis_pool_and_modifiers(self):
        c = {"ticker": "SN", "chg": -9.1, "dvol": 475.2, "max21": 12.3, "in_pool": 1, "ibs": 5.0}
        live = {"id": "us_live_dvol", "universe": "live", "pick": "dvol_desc"}
        b = vb.pick_basis(live, c)
        for token in ("전일 -9.1%", "거래대금 475M", "MAX21 12.3", "풀 in", "거래대금 큰순"):
            self.assertIn(token, b)
        wide = {"id": "x", "universe": "wide", "pick": "all", "max_floor": False, "no_earnings": True,
                "max_passers": 12, "tp": 20.0}
        c["in_pool"] = 0
        b = vb.pick_basis(wide, c)
        for token in ("풀 wide", "전량", "MAX하한 없음", "어닝 제외", "no-trade(>12)", "TP20"):
            self.assertIn(token, b)

    def test_slow_and_lp(self):
        self.assertIn("5일 누적 -14.2%", vb.pick_basis({"universe": "slowus", "pick": "cum5_deep"}, {"cum5": -14.2}))
        self.assertIn("60일 +33.1%", vb.pick_basis({"universe": "lpus", "pick": "ret60_desc"}, {"ret60": 33.1}))

    def test_missing_features_do_not_raise(self):
        b = vb.pick_basis({"universe": "wide", "pick": "dvol_desc"}, {"ticker": "Z"})
        self.assertIn("전일 -", b)
        self.assertIn("거래대금 -", b)


class BackfillBasisTest(unittest.TestCase):
    def test_backfill_is_idempotent_and_skips_unknown(self):
        con = sqlite3.connect(":memory:")
        vb.ensure_schema(con)
        con.execute("INSERT INTO trades (strategy_id, session_date, ticker, entry_price, notional_krw, backfill, "
                    "pick_pos, status, opened_at) VALUES ('kr_r4x','2026-09-02','348340',16340,220000,0,1,'OPEN','t')")
        con.execute("INSERT INTO trades (strategy_id, session_date, ticker, entry_price, notional_krw, backfill, "
                    "pick_pos, status, opened_at) VALUES ('kr_r4x','2026-09-02','999999',1,220000,0,2,'OPEN','t')")
        con.commit()
        kr = {"2026-09-02": [{"ticker": "348340", "disc": -19.4, "gap": -4.3, "chg": -8.7, "rv20": 4.4,
                              "from_high20": -41.0, "r2": False, "r4x": True}]}
        n = vb.backfill_basis(con, {}, kr, {}, {})
        self.assertEqual(n, 1)
        meta = json.loads(con.execute("SELECT meta FROM trades WHERE ticker='348340'").fetchone()[0])
        self.assertIn("할인 -19.4%", meta["basis"])
        self.assertEqual(meta["feat"]["gap"], -4.3)
        self.assertIsNone(con.execute("SELECT meta FROM trades WHERE ticker='999999'").fetchone()[0])
        self.assertEqual(vb.backfill_basis(con, {}, kr, {}, {}), 0)  # 멱등


if __name__ == "__main__":
    unittest.main()
