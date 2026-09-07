# -*- coding: utf-8 -*-
"""2026-09-08 N2: KR WS 관측 전용 틱은 매매 경로에 들어가지 않는다 — 라우팅 순수 함수 + KISWebSocket 관측 목록 분리 + sink 버퍼."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kis_api import KISWebSocket, route_kr_tick  # noqa: E402
from runtime import ws_tick_ledger as wl  # noqa: E402


class RoutingTest(unittest.TestCase):
    def test_route(self):
        self.assertEqual(route_kr_tick("005930", frozenset({"000660"})), "trade")
        self.assertEqual(route_kr_tick("000660", frozenset({"000660"})), "observe")
        self.assertEqual(route_kr_tick("000660", None), "trade")
        self.assertEqual(route_kr_tick("000660", frozenset()), "trade")

    def test_observe_excludes_trade_tickers(self):
        ws = KISWebSocket("tok", ["005930", "000660"], market="KR", observe_tickers=["000660", "035420", ""])
        self.assertEqual(ws.observe_tickers, ["035420"]); self.assertEqual(route_kr_tick("000660", ws.observe_set), "trade")
        self.assertEqual(route_kr_tick("035420", ws.observe_set), "observe")

    def test_sink_buffer_and_window(self):
        with tempfile.TemporaryDirectory() as td:
            old = wl.TICK_DIR; wl.TICK_DIR = Path(td)
            try:
                wl._BUF.clear()
                wl.tick_sink("035420^090512^10000^2^100^1.0^0^0^0^0^10050^10000^10^1000")   # 09:05
                wl.tick_sink("035420^101512^10000^2^100^1.0^0^0^0^0^10050^10000^10^1000")   # 10:15 → 창 밖, 버림
                wl.flush(force=True)
                files = list(Path(td).glob("*.jsonl")); self.assertEqual(len(files), 1)
                rows = [json.loads(l) for l in files[0].read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(rows), 1); self.assertEqual(rows[0]["raw"].split("^")[0], "035420"); self.assertIn("rx", rows[0])
            finally:
                wl.TICK_DIR = old

    def test_observe_list_date_guard(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ws_observe_kr.json"; old = wl.OBSERVE_FILE; wl.OBSERVE_FILE = p
            try:
                p.write_text(json.dumps({"date": "2000-01-01", "tickers": ["035420"]}), encoding="utf-8")
                self.assertEqual(wl.load_observe_list(), [])   # 날짜 불일치 → 0
                from datetime import date
                p.write_text(json.dumps({"date": date.today().isoformat(), "tickers": ["035420", "000660"]}), encoding="utf-8")
                self.assertEqual(wl.load_observe_list(), ["035420", "000660"])
            finally:
                wl.OBSERVE_FILE = old


if __name__ == "__main__":
    unittest.main()
