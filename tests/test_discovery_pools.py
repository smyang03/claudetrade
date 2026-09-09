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
        self.assertEqual(r["path"]["dates"][0], "d0"); self.assertEqual(r["path"]["raw_d0"], [100, 101, 99, 101, 1])   # 원본 D0 OHLCV 보존
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
        self.assertIn('startswith(("xus_", "xkr_", "c_"))', src2)

    def test_nearmiss_collected_outside_pools(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            orig = (dp.US_DIR, dp.DISCOVERY_START)
            dp.US_DIR, dp.DISCOVERY_START = d, "2025-01-01"; dp._NEARMISS.clear()
            try:
                b = _bars(70, vol=4_000_000.0)
                b[60] = (b[60][0], 100.0, 100.5, 97.0, 97.5, 4_000_000.0)   # −2.5%: 풀(−3%) 탈락, near-miss 밴드(|chg|≥2) 안
                with (d / "us_CCC.csv").open("w", encoding="utf-8") as fh:
                    fh.write("date,open,high,low,close,volume\n"); [fh.write(",".join(str(x) for x in r) + "\n") for r in b]
                dp.build("US")
                nm = dp._NEARMISS.get("US", {})
                self.assertIn(b[60][0], nm); self.assertEqual(nm[b[60][0]][0][0], "CCC"); self.assertAlmostEqual(nm[b[60][0]][0][1], -2.5, places=2)
            finally:
                dp.US_DIR, dp.DISCOVERY_START = orig; dp._NEARMISS.clear()

    def test_candidate_checks(self):
        import discovery_breakdown as bd
        rows = [{"net": 1.0 + (i % 3), "session": f"2025-0{1 + i % 6}-1{i % 9}", "half": "H1" if i < 12 else "H2", "rank_dvol_raw": 1 if i % 4 == 0 else 3} for i in range(24)]
        chk = bd.candidate_checks(rows)
        self.assertTrue(chk["oos_halves_same_sign"]); self.assertEqual(chk["k1"]["n"], 6); self.assertGreater(chk["k1"]["mean"], 0)

    def test_bar_complete_guard(self):
        from datetime import datetime
        self.assertFalse(dp.bar_complete("2026-09-07", "KR", now=datetime(2026, 9, 7, 11, 0)))
        self.assertTrue(dp.bar_complete("2026-09-07", "KR", now=datetime(2026, 9, 7, 16, 0)))
        self.assertFalse(dp.bar_complete("2026-09-04", "US", now=datetime(2026, 9, 5, 5, 0)))
        self.assertTrue(dp.bar_complete("2026-09-04", "US", now=datetime(2026, 9, 5, 6, 0)))

    def test_candidate_filter_and_forward_start(self):
        s = next(x for x in vb.STRATEGIES if x["id"] == "c_kr_fallen_regime")
        self.assertEqual(s["universe"], "xkr"); self.assertTrue(s["candidate"]); self.assertNotIn("discovery", s)
        ok = {"chg": -5.5, "regime": {"idx_above_ma20": False}}
        self.assertTrue(vb.candidate_filter_pass(ok, s["filter"]))
        self.assertFalse(vb.candidate_filter_pass({"chg": -4.9, "regime": {"idx_above_ma20": False}}, s["filter"]))
        self.assertFalse(vb.candidate_filter_pass({"chg": -6.0, "regime": {"idx_above_ma20": True}}, s["filter"]))
        self.assertFalse(vb.candidate_filter_pass({"chg": -6.0, "regime": {}}, s["filter"]))   # 국면 결측은 통과 금지
        orig = (vb._x_sessions, vb.entry_of)
        cands = [{"ticker": "A", "chg": -6.0, "dvol": 30, "signal_date": "2026-09-05", "pool_n": 2, "ranks": {}, "regime": {"idx_above_ma20": False}},
                 {"ticker": "B", "chg": -6.0, "dvol": 30, "signal_date": "2026-09-05", "pool_n": 2, "ranks": {}, "regime": {"idx_above_ma20": True}}]
        vb._x_sessions = lambda m: {"xkr_fallen3": {"2026-09-05": cands, "2026-09-08": [dict(cands[0], signal_date="2026-09-08")]}}
        vb.entry_of = lambda t, sd, market="US", hold=7: (100.0, [("x", 100.0, 101.0, 99.0, 100.5, 1)])
        try:
            con = sqlite3.connect(":memory:"); vb.ensure_schema(con); vb.open_new_trades(con, {}, {}, {}, {})
            rows = con.execute("SELECT session_date, ticker, backfill FROM trades WHERE strategy_id='c_kr_fallen_regime' ORDER BY 1").fetchall()
            self.assertEqual(rows, [("2026-09-05", "A", 1), ("2026-09-08", "A", 0)])   # B는 국면 위라 제외, 09-08부터 forward
        finally:
            vb._x_sessions, vb.entry_of = orig

    def test_regime_v2_variants(self):
        """C_REGIME_V2 (09-10): 부모 arm 8개 각각에 idx_above_ma20=False만 얹은 _rg 변형 — 풀·계약 동일, forward 09-10부터, id 중복 없음."""
        ids = [x["id"] for x in vb.STRATEGIES]
        self.assertEqual(len(ids), len(set(ids)))
        for pid in vb._REGIME_V2_PARENTS:
            parent = next(x for x in vb.STRATEGIES if x["id"] == pid)
            v = next(x for x in vb.STRATEGIES if x["id"] == pid + "_rg")
            self.assertEqual(v["parent_id"], pid)
            for k in ("pool", "universe", "pick", "daily_cap", "slots", "order_krw", "tp", "sl", "hold", "backfill_start"):
                self.assertEqual(v.get(k), parent.get(k), (pid, k))
            self.assertEqual(v["filter"], {**(parent.get("filter") or {}), "idx_above_ma20": False})
            self.assertEqual(v["forward_start"], "2026-09-10")
            self.assertNotEqual(vb._contract_hash(v), vb._contract_hash(parent))
        v = next(x for x in vb.STRATEGIES if x["id"] == "c_kr_fallen_buyback30_rg")
        below = {"chg": -6.0, "buyback_days": 3, "regime": {"idx_above_ma20": False}}
        self.assertTrue(vb.candidate_filter_pass(below, v["filter"]))
        self.assertFalse(vb.candidate_filter_pass({**below, "regime": {"idx_above_ma20": True}}, v["filter"]))
        self.assertFalse(vb.candidate_filter_pass({**below, "regime": {}}, v["filter"]))          # 국면 결측 fail-closed
        self.assertFalse(vb.candidate_filter_pass({**below, "buyback_days": 40}, v["filter"]))    # 부모 필터도 유지

    def test_exclude_tickers_filter_k1_ex(self):
        parent = next(x for x in vb.STRATEGIES if x["id"] == "c_kr_insider_k1")
        v = next(x for x in vb.STRATEGIES if x["id"] == "c_kr_insider_k1_ex")
        self.assertEqual((v["pool"], v["pick"], v["daily_cap"], v["hold"]), (parent["pool"], "dvol_desc", 1, 10))
        self.assertEqual(v["filter"], {"exclude_tickers": ["005930", "000660"]})
        self.assertFalse(vb.candidate_filter_pass({"ticker": "005930"}, v["filter"]))
        self.assertFalse(vb.candidate_filter_pass({"ticker": "000660"}, v["filter"]))
        self.assertTrue(vb.candidate_filter_pass({"ticker": "035420"}, v["filter"]))
        self.assertTrue(vb.candidate_filter_pass({"ticker": "005930"}, {}))   # 필터 없으면 통과

    def test_breadth_filter(self):
        s = next(x for x in vb.STRATEGIES if x["id"] == "c_us_panic_top5")
        self.assertEqual((s["pick"], s["daily_cap"], s["hold"], s["tp"], s["sl"]), ("dvol_desc", 5, 10, 20.0, -25.0))
        self.assertTrue(vb.candidate_filter_pass({"chg": -6.0, "regime": {"breadth_down_pct": 70.0}}, s["filter"]))
        self.assertFalse(vb.candidate_filter_pass({"chg": -6.0, "regime": {"breadth_down_pct": 60.0}}, s["filter"]))
        self.assertFalse(vb.candidate_filter_pass({"chg": -6.0, "regime": {}}, s["filter"]))

    def test_exclude_sectors_filter(self):
        s = next(x for x in vb.STRATEGIES if x["id"] == "c_kr_fallen5_nobio")
        orig = dict(vb._SECTOR_CACHE); vb._SECTOR_CACHE.clear(); vb._SECTOR_CACHE.update({"KR": {"000100": "제약·바이오", "005930": "전자·반도체"}, "US": {}})
        try:
            self.assertFalse(vb.candidate_filter_pass({"ticker": "000100", "chg": -6.0, "pool": "xkr_fallen3"}, s["filter"]))
            self.assertTrue(vb.candidate_filter_pass({"ticker": "005930", "chg": -6.0, "pool": "xkr_fallen3"}, s["filter"]))
            self.assertTrue(vb.candidate_filter_pass({"ticker": "999999", "chg": -6.0, "pool": "xkr_fallen3"}, s["filter"]))   # 미분류는 통과
        finally:
            vb._SECTOR_CACHE.clear(); vb._SECTOR_CACHE.update(orig)

    def test_attach_open_flow(self):
        with tempfile.TemporaryDirectory() as td:
            led = Path(td) / "flow.jsonl"
            led.write_text(json.dumps({"session_date": "2026-09-08", "signal_date": "2026-09-07", "ticker": "000100", "snap": "09:05",
                                       "flow": {"gap_pct": -1.2, "strength": 130.0, "imbalance": 0.3}}) + "\n", encoding="utf-8")
            orig = vb.OPEN_FLOW_LEDGER; vb.OPEN_FLOW_LEDGER = led
            try:
                con = sqlite3.connect(":memory:"); vb.ensure_schema(con)
                con.execute("INSERT INTO strategies (id, universe) VALUES ('xkr_fallen3','xkr')")
                con.execute("INSERT INTO trades (strategy_id, session_date, ticker, status, meta) VALUES ('xkr_fallen3','2026-09-07','000100','OPEN',?)",
                            (json.dumps({"signal_date": "2026-09-07"}),))
                con.execute("INSERT INTO trades (strategy_id, session_date, ticker, status, meta) VALUES ('xkr_fallen3','2026-09-07','000200','OPEN',?)",
                            (json.dumps({"signal_date": "2026-09-07"}),))
                self.assertEqual(vb.attach_open_flow(con), 1)
                m = json.loads(con.execute("SELECT meta FROM trades WHERE ticker='000100'").fetchone()[0])
                self.assertEqual(m["flow"]["09:05"]["strength"], 130.0); self.assertEqual(m["flow"]["entry_date"], "2026-09-08")
                self.assertEqual(vb.attach_open_flow(con), 0)   # 멱등
            finally:
                vb.OPEN_FLOW_LEDGER = orig

    def test_kr_breakout_window_120(self):
        b = _bars(200, drift=0.0)
        b[199] = (b[199][0], 100.0, 101.0, 99.0, 100.5, 1_000_000.0)   # 직전 최고 종가(100) 돌파
        self.assertEqual(dp.featurize(b, 199, "KR")["hi_break_n"], 120)
        self.assertEqual(dp.featurize(b, 199, "US")["hi_break_n"], 199)


class EventPoolAndFilterTest(unittest.TestCase):
    """2026-09-07 신규 전략: 이벤트 특성 no-lookahead·풀 통과·필터 키(feat_range/require_true/feat_exclude_range)."""

    def test_event_features_only_use_disclosures_on_or_before_signal(self):
        import datetime as _d
        dp._EVT.clear()
        dp._EVT.update({"kr": {"000001": [(_d.date(2026, 1, 10), "buyback"), (_d.date(2026, 1, 20), "major_holder_change")]},
                        "terms": {"000001": [{"kind": "buyback", "method": "market", "date": "2026-01-10", "start": "2026-01-12", "end": "2026-03-10"},
                                             {"kind": "bonus_issue", "date": "2026-01-05", "record_date": "2026-01-16"}]},
                        "ins": {"000001": [(_d.date(2026, 1, 13), "갑", 1000.0), (_d.date(2026, 1, 14), "을", 500.0), (_d.date(2026, 1, 14), "병", -300.0)]},
                        "plans": {}, "earn": {"AAA": [(_d.date(2026, 1, 14), "AMC")]}, "usi": {},
                        "ok": {"dart": True, "terms": True, "ins": True, "plans": False, "earn": True, "usi": False}})
        try:
            dates = ["2026-01-09", "2026-01-12", "2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16"]
            f = dp.event_features("000001", "2026-01-14", "KR", dates, 3)
            self.assertEqual(f["buyback_days"], 4)                  # 01-10 공시 → 01-14 신호
            self.assertIsNone(f["major_holder_change_days"])        # 01-20 공시는 미래 → 모른다
            self.assertTrue(f["buyback_active"])                    # 본문 기간 01-12~03-10
            self.assertEqual(f["insider_buy_n7"], 2)                # 증가(+) 보고자 갑·을, 병(−)은 제외
            self.assertTrue(f["exright_next"])                      # 기준일 01-16 → 권리락일 01-15 = 다음 봉
            self.assertIn("xkr_insider", dp.event_pool_pass(f, "KR")); self.assertIn("xkr_exright", dp.event_pool_pass(f, "KR"))
            f0 = dp.event_features("000001", "2026-01-09", "KR", dates, 0)
            self.assertIsNone(f0["buyback_days"])                   # 공시 전 신호 → 결측(fail-closed)
            fe = dp.event_features("AAA", "2026-01-15", "US", dates, 4)
            self.assertTrue(fe["earn_react"])                       # AMC 01-14 → 반응봉 01-15
            fe2 = dp.event_features("AAA", "2026-01-14", "US", dates, 3)
            self.assertFalse(fe2["earn_react"])                     # 발표 당일 봉은 AMC면 반응봉 아님
            fe.update({"gap": 9.0, "close_pos_up": True})
            self.assertIn("xus_earn_gap", dp.event_pool_pass(fe, "US"))
            old = dp.DART_BACKFILL_START
            dp.DART_BACKFILL_START = "2026-02-01"
            try:
                self.assertFalse(dp.event_features("000001", "2026-01-14", "KR", dates, 3)["dart_ok"])   # 백필 창 밖 → 모름
            finally:
                dp.DART_BACKFILL_START = old
        finally:
            dp._EVT.clear()

    def test_volume_context_first_event_flags(self):
        b = _bars(90)
        b[70] = (b[70][0], 100.0, 101.0, 99.0, 100.5, 5_000_000.0)   # 5배, 최초
        flags, vols = dp.volume_context(b)
        self.assertEqual(flags[70], 1); self.assertEqual(sum(flags[:70]), 0)
        f = dp.featurize(b, 70, "US", (flags, vols))
        self.assertTrue(f["vol_max60"]); self.assertEqual(f["spike_prior20"], 0)
        self.assertIn("xus_volfirst", dp.event_pool_pass({**f, "insider_buy_n7": None, "earn_react": False}, "US"))
        f2 = dp.featurize(b, 71, "US", (flags, vols))   # 다음 봉: 직전 20봉에 3배 사건 1회 → 최초 아님
        self.assertEqual(f2["spike_prior20"], 1)

    def test_candidate_filter_keys(self):
        flt_in = {"chg_le": -5.0, "feat_range": {"buyback_days": [0, 30]}}
        self.assertTrue(vb.candidate_filter_pass({"chg": -6.0, "buyback_days": 12}, flt_in))
        self.assertFalse(vb.candidate_filter_pass({"chg": -6.0, "buyback_days": 45}, flt_in))
        self.assertFalse(vb.candidate_filter_pass({"chg": -6.0, "buyback_days": None}, flt_in))   # 결측 = 불통과
        flt_ex = {"chg_le": -5.0, "require_true": ["dart_ok"], "feat_exclude_range": {"major_holder_change_days": [0, 7]}}
        self.assertFalse(vb.candidate_filter_pass({"chg": -6.0, "dart_ok": True, "major_holder_change_days": 3}, flt_ex))
        self.assertTrue(vb.candidate_filter_pass({"chg": -6.0, "dart_ok": True, "major_holder_change_days": None}, flt_ex))
        self.assertTrue(vb.candidate_filter_pass({"chg": -6.0, "dart_ok": True, "major_holder_change_days": 30}, flt_ex))
        self.assertFalse(vb.candidate_filter_pass({"chg": -6.0, "dart_ok": False}, flt_ex))   # 원장 없음 → 배제 판단 불가 → 불통과

    def test_new_arms_registered_as_candidate_views(self):
        ids = {s["id"]: s for s in vb.STRATEGIES}
        for sid in ("c_kr_fallen_buyback30", "c_kr_fallen_nomajor", "c_kr_insider_cluster", "c_kr_exright", "c_us_earn_gap", "c_us_volfirst"):
            self.assertIn(sid, ids); self.assertTrue(ids[sid].get("candidate")); self.assertIn(ids[sid]["universe"], ("xus", "xkr"))
            self.assertIn(ids[sid]["pool"], {**dp.POOLS_US, **dp.POOLS_KR})
        self.assertEqual(ids["c_kr_fallen_buyback30"]["backfill_start"], "2025-09-08")


if __name__ == "__main__":
    unittest.main()
