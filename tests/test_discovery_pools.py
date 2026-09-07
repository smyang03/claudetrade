# -*- coding: utf-8 -*-
"""탐색 풀·탐색 arm (2026-09-07 D1): 특성값 no-lookahead, 풀 통과, 순위, 국면 태그, 출구 계약 격자·경로, 한도 없는 진입·meta 기록."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import discovery_pools as dp  # noqa: E402
import virtual_books as vb  # noqa: E402


def _bars(n: int, base: float = 100.0, drift: float = 0.0, vol: float = 1_000_000.0) -> list[tuple]:
    out = []
    px = base
    for i in range(n):
        d = f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}"
        o = px; c = px * (1 + drift); h = max(o, c) * 1.01; l = min(o, c) * 0.99
        out.append((d, o, h, l, c, vol))
        px = c
    return out


class FeaturizeTest(unittest.TestCase):
    def test_features_use_only_past_bars(self):
        b = _bars(80)
        b[60] = (b[60][0], 100.0, 101.0, 92.0, 94.0, 5_000_000.0)   # −6% 급락, 거래량 5배
        f = dp.featurize(b, 60, "US")
        self.assertAlmostEqual(f["chg"], -6.0, places=6)
        self.assertGreater(f["vol_spike"], 4.9)
        self.assertAlmostEqual(f["dvol"], 94.0 * 5_000_000 / 1e6, places=3)
        self.assertIsNotNone(f["max21"]); self.assertIsNotNone(f["rv20"]); self.assertIsNotNone(f["ret60"])
        f2 = dp.featurize(b, 61, "US")   # 다음 봉 특성은 61봉까지만 본다 → 급락일 chg는 60봉의 것
        self.assertNotAlmostEqual(f2["chg"], -6.0, places=3)
        self.assertIsNone(dp.featurize(b, 5, "US"))

    def test_pool_pass(self):
        f = {"dvol": 80.0, "chg": -3.5, "cum5": -2.0, "min1_in5": -3.5, "hi_break_n": 0, "vol_spike": 1.0, "price": 10.0}
        self.assertEqual(dp.pool_pass(f, "US"), ["xus_fallen3"])
        self.assertEqual(dp.pool_pass({**f, "dvol": 10.0}, "US"), [])                       # 거래대금 미달
        self.assertEqual(dp.pool_pass({**f, "chg": 6.0}, "US"), ["xus_rise5"])
        self.assertEqual(dp.pool_pass({**f, "chg": 0.5, "vol_spike": 4.0}, "US"), ["xus_volspike"])
        self.assertEqual(dp.pool_pass({**f, "chg": -1.0, "cum5": -9.0, "min1_in5": -2.0, "hi_break_n": 250}, "US"), ["xus_slow8", "xus_breakout"])
        self.assertEqual(dp.pool_pass({**f, "chg": -1.0, "cum5": -9.0, "min1_in5": -6.0}, "US"), [])   # 단일 −5% 있으면 slow 아님
        self.assertEqual(dp.pool_pass({"dvol": 30.0, "chg": -4.0, "price": 5000.0, "hi_break_n": 0, "vol_spike": 1.0, "cum5": 0, "min1_in5": 0}, "KR"), ["xkr_fallen3"])
        self.assertEqual(dp.pool_pass({"dvol": 30.0, "chg": -4.0, "price": 900.0, "hi_break_n": 0, "vol_spike": 1.0, "cum5": 0, "min1_in5": 0}, "KR"), [])  # 1,000원 미만

    def test_build_ranks_and_regime_on_temp_dir(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            orig = (dp.US_DIR, dp.DISCOVERY_START)
            dp.US_DIR, dp.DISCOVERY_START = d, "2025-01-01"
            try:
                for t, drop, vol in (("AAA", 0.94, 4_000_000.0), ("BBB", 0.96, 9_000_000.0), ("SPY", 1.0, 1_000_000.0)):
                    b = _bars(70, vol=vol)
                    b[60] = (b[60][0], 100.0, 100.5, 90.0, 100.0 * drop, vol)
                    with (d / f"us_{t}.csv").open("w", encoding="utf-8") as fh:
                        fh.write("date,open,high,low,close,volume\n")
                        for r in b:
                            fh.write(",".join(str(x) for x in r) + "\n")
                sessions, stats = dp.build("US")
                key = _bars(70)[61][0]   # 진입 세션 = 신호 다음 봉
                cands = sessions["xus_fallen3"][key]
                self.assertEqual({c["ticker"] for c in cands}, {"AAA", "BBB"})
                by = {c["ticker"]: c for c in cands}
                self.assertEqual(by["BBB"]["ranks"]["dvol_desc"], 1)      # 거래대금 큰순 1위
                self.assertEqual(by["AAA"]["ranks"]["chg_lo"], 1)         # 더 많이 빠진 순 1위
                self.assertEqual(by["AAA"]["pool_n"], 2)
                self.assertIn("idx_ret20", by["AAA"]["regime"]); self.assertEqual(by["AAA"]["regime"]["universe_n"], 3)
                self.assertEqual(stats[key]["xus_fallen3"], 2); self.assertEqual(stats[key]["_universe"], 3)
            finally:
                dp.US_DIR, dp.DISCOVERY_START = orig


class GridAndEntryTest(unittest.TestCase):
    def test_path_and_grid(self):
        entry = 100.0
        win = [(f"d{i}", 100, h, l, c, 1) for i, (h, l, c) in enumerate([(101, 99, 101), (109, 100, 108), (115, 105, 113), (114, 108, 110), (112, 100, 101),
                                                                            (105, 98, 99), (100, 95, 96), (98, 90, 92), (95, 88, 90), (92, 85, 88)])]
        r = vb._path_and_grid(entry, win, fee=0.5, be_lock_main=True)
        self.assertEqual(len(r["path"]["close"]), 10); self.assertTrue(r["grid_complete"])
        self.assertEqual(r["grid"]["tp12_sl25_d7_be"], [11.5, "TP"])     # D2 고가 115 → TP12 (정본 7봉 계약)
        self.assertEqual(r["grid"]["tp12_sl25_d5_be"], [11.5, "TP"])
        self.assertEqual(r["grid"]["tp8_sl10_d3"][1], "TP")
        self.assertEqual(r["grid"]["hold_d5"], [round(1.0 - 0.5, 3), "D_MAT"])
        self.assertEqual(r["grid"]["hold_d10"], [round(-12.0 - 0.5, 3), "D_MAT"])
        self.assertAlmostEqual(r["mfe"], 15.0, places=3); self.assertAlmostEqual(r["mae"], -15.0, places=3)
        short = vb._path_and_grid(entry, win[:3], fee=0.5, be_lock_main=True)
        self.assertFalse(short["grid_complete"]); self.assertIn("tp6_sl6_d2", short["grid"]); self.assertNotIn("hold_d10", short["grid"])

    def test_discovery_arm_opens_all_passers_without_cap(self):
        s = next(x for x in vb.STRATEGIES if x["id"] == "xus_fallen3")
        cands = [{"ticker": f"T{i}", "chg": -3.0 - i, "dvol": 100 + i, "signal_date": "2025-07-01", "pool_n": 40, "ranks": {"dvol_desc": 40 - i}, "regime": {"idx_ret20": 1.0}}
                 for i in range(40)]
        orig = (vb._x_sessions, vb.entry_of, vb.record_entry_skip)
        vb._x_sessions = lambda m: {"xus_fallen3": {"2025-07-02": cands}}
        vb.entry_of = lambda t, sd, market="US", hold=7: None if t == "T39" else (10.0, [("2025-07-02", 10.0, 10.5, 9.5, 10.2, 1)])
        skips = []
        vb.record_entry_skip = lambda *a, **k: skips.append(a)
        try:
            con = sqlite3.connect(":memory:")
            vb.ensure_schema(con)
            n = vb.open_new_trades(con, {}, {}, {}, {})
            rows = con.execute("SELECT ticker, pick_pos, backfill, meta FROM trades WHERE strategy_id='xus_fallen3'").fetchall()
            self.assertEqual(len(rows), 39)                                 # 한도 없음, 봉 없는 T39만 제외 (n은 다른 arm 포함)
            self.assertEqual(skips, [])                                     # 탐색 arm은 스킵 원장에 쓰지 않는다
            m = json.loads(rows[0][3])
            self.assertEqual(m["pool"], "xus_fallen3"); self.assertIn("ranks", m); self.assertIn("regime", m); self.assertIn("chg", m["feat"])
            self.assertEqual(rows[0][2], 1)                                 # 2025년 = backfill
        finally:
            vb._x_sessions, vb.entry_of, vb.record_entry_skip = orig

    def test_gate_excludes_discovery(self):
        import virtual_gate_eval as ge
        src = Path(ge.__file__).read_text(encoding="utf-8")
        self.assertIn('not s.get("discovery")', src)
        self.assertIn("strategy_id NOT LIKE 'x%'", src)

    def test_realtime_and_phantom_exclude_discovery(self):
        src = (ROOT / "tools" / "observe_arm_picks_realtime.py").read_text(encoding="utf-8")
        self.assertIn('s.get("discovery")', src)
        src2 = (ROOT / "runtime" / "phantom_book.py").read_text(encoding="utf-8")
        self.assertIn('startswith(("xus_", "xkr_"))', src2)

    def test_kr_breakout_window_120(self):
        b = _bars(200, drift=0.0)
        b[199] = (b[199][0], 100.0, 101.0, 99.0, 100.5, 1_000_000.0)   # 직전 최고 종가(100) 돌파
        self.assertEqual(dp.featurize(b, 199, "KR")["hi_break_n"], 120)
        self.assertEqual(dp.featurize(b, 199, "US")["hi_break_n"], 199)


if __name__ == "__main__":
    unittest.main()
