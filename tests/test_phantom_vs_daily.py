# -*- coding: utf-8 -*-
"""유령 vs 일봉 대조 원장 — 비교 기준 수리 (2026-09-06).

Codex 리뷰 지적 2건 고정:
- 유령 gross에서 장부 net을 빼면 비용만큼 유령이 항상 유리해 보인다 → 같은 왕복 비용을 차감한 phantom_net으로 비교
- 실전 출구 코드(strategy_fixed_take_profit)와 장부 코드(TP)가 같은 익절인데 불일치로 집계됐다 → 정규화 후 비교
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import phantom_vs_daily as pvd  # noqa: E402


class ReasonAndNetTest(unittest.TestCase):
    def test_reason_normalization(self):
        self.assertEqual(pvd.normalize_reason("strategy_fixed_take_profit"), "TP")
        self.assertEqual(pvd.normalize_reason("strategy_breakeven_lock"), "BE")
        self.assertEqual(pvd.normalize_reason("strategy_horizon_exit"), "D_MAT")
        self.assertEqual(pvd.normalize_reason("strategy_catastrophe_stop"), "SL")
        self.assertEqual(pvd.normalize_reason("weird_code"), "WEIRD_CODE")

    def test_build_row_uses_net_and_normalized_reason(self):
        c = {"arm": "b2_leader_pb", "book_session_date": "2026-09-02", "ticker": "ANF", "entry_usd": 137.92,
             "gross_pct": 8.0445, "reason": "strategy_fixed_take_profit", "held_days": 1, "retro": False}
        row = pvd.build_row(c, ("CLOSED", 137.7, 7.5, "TP"), fee_pct=0.50, stamp="t")
        self.assertEqual(row["phantom_reason_norm"], "TP")
        self.assertTrue(row["reason_match"])
        self.assertAlmostEqual(row["phantom_net_pct"], 7.5445, places=4)
        self.assertAlmostEqual(row["net_diff_pct"], 0.045, places=3)   # 이전 기준(gross−net)이면 0.544였다
        self.assertEqual(row["fee_pct"], 0.50)
        self.assertAlmostEqual(row["entry_diff_pct"], 0.16, places=2)

    def test_mismatch_still_detected(self):
        c = {"arm": "us_wide_dvol", "book_session_date": "2026-09-03", "ticker": "FRVO", "entry_usd": 17.515,
             "gross_pct": -0.0285, "reason": "strategy_breakeven_lock"}
        row = pvd.build_row(c, ("CLOSED", 18.36, -5.0, "D_MAT"), fee_pct=0.50, stamp="t")
        self.assertEqual(row["phantom_reason_norm"], "BE")
        self.assertFalse(row["reason_match"])
        self.assertAlmostEqual(row["net_diff_pct"], 4.4715, places=2)


class MainRebuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.orig = (pvd.PHANTOM, pvd.BOOK, pvd.OUT)
        pvd.PHANTOM, pvd.BOOK, pvd.OUT = d / "phantom.jsonl", d / "book.db", d / "out.jsonl"
        con = sqlite3.connect(pvd.BOOK)
        con.execute("CREATE TABLE trades(strategy_id, session_date, ticker, entry_price, status, exit_reason, net_pct)")
        con.execute("INSERT INTO trades VALUES('b2_leader_pb','2026-09-02','ANF',137.7,'CLOSED','TP',7.5)")
        con.execute("INSERT INTO trades VALUES('us_wide_dvol','2026-09-04','WIX',80.0,'OPEN',NULL,NULL)")
        con.commit(); con.close()
        rows = [{"event": "OPEN", "arm": "b2_leader_pb", "ticker": "ANF"},
                {"event": "CLOSE", "arm": "b2_leader_pb", "book_session_date": "2026-09-02", "ticker": "ANF",
                 "entry_usd": 137.92, "gross_pct": 8.0445, "reason": "strategy_fixed_take_profit", "held_days": 1},
                {"event": "CLOSE", "arm": "us_wide_dvol", "book_session_date": "2026-09-04", "ticker": "WIX",
                 "entry_usd": 80.5, "gross_pct": 1.0, "reason": "strategy_breakeven_lock"}]
        pvd.PHANTOM.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        # 옛 기준으로 적힌 행(gross−net, 사유 불일치)
        pvd.OUT.write_text(json.dumps({"arm": "b2_leader_pb", "book_session_date": "2026-09-02", "ticker": "ANF",
                                       "net_diff_pct": 0.544, "reason_match": False}) + "\n", encoding="utf-8")

    def tearDown(self):
        pvd.PHANTOM, pvd.BOOK, pvd.OUT = self.orig
        self.tmp.cleanup()

    def _rows(self):
        return [json.loads(l) for l in pvd.OUT.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_append_is_idempotent_and_skips_open_book(self):
        pvd.main([])
        rows = self._rows()
        self.assertEqual(len(rows), 1)            # ANF는 이미 있음, WIX는 장부 미정산 → 추가 없음
        self.assertFalse(rows[0]["reason_match"])  # 옛 행 그대로

    def test_rebuild_rewrites_with_new_basis(self):
        pvd.main(["--rebuild"])
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["reason_match"])
        self.assertIn("phantom_net_pct", rows[0])
        self.assertLess(abs(rows[0]["net_diff_pct"]), 0.1)


if __name__ == "__main__":
    unittest.main()
