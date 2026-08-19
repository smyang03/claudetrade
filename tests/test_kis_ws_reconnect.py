# -*- coding: utf-8 -*-
"""KIS WS 자동 재연결 계약 테스트.

2026-08-19 US 실측: 서버가 약 38분마다 연결을 강제 종료(WinError 10054).
무음 감지(600초)만으로 복구하면 세션의 33%가 REST 폴백으로 강등되고 TP/SL
틱 감지를 잃는다. run_forever가 반환되면 즉시 다시 붙어야 한다.
"""
from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

import kis_api


class _FakeApp:
    """run_forever가 지정 횟수만큼 즉시 반환(=서버 단절)한 뒤 블록한다."""

    instances: list["_FakeApp"] = []

    def __init__(self, *_a, **kw):
        self.on_open = kw.get("on_open")
        self.closed = False
        _FakeApp.instances.append(self)

    def run_forever(self, *_a, **_kw):
        if self.on_open:
            self.on_open(self)
        if len(_FakeApp.instances) <= 3:
            return  # 단절 시뮬레이션
        while not self.closed:  # 안정화된 연결
            time.sleep(0.01)

    def close(self):
        self.closed = True

    def send(self, *_a, **_kw):
        pass


class KisWsReconnectTests(unittest.TestCase):
    def setUp(self):
        _FakeApp.instances = []

    def _make(self):
        ws = kis_api.KISWebSocket.__new__(kis_api.KISWebSocket)
        ws.token = "t"
        ws.tickers = ["AAPL"]
        ws.market = "US"
        ws.on_tick = lambda d: None
        ws.on_notice = None
        ws.ws = None
        ws._ws_key = "key"
        ws._notice_iv = None
        ws._notice_key = None
        ws._seen_fills = set()
        ws._hts_id = ""
        ws.running = False
        ws.started_at = ""
        ws.last_error = ""
        ws._closing = False
        ws.disconnect_count = 0
        ws.downtime_sec = 0.0
        ws._down_since = 0.0
        return ws

    def test_reconnects_after_server_close_and_counts_downtime(self):
        ws = self._make()
        with mock.patch.dict("sys.modules", {"websocket": mock.MagicMock(WebSocketApp=_FakeApp)}), \
             mock.patch.object(kis_api, "_ws_url", lambda m: "wss://x"), \
             mock.patch.dict("os.environ", {"KIS_WS_RECONNECT_BASE_SEC": "0.01",
                                            "KIS_WS_RECONNECT_MAX_SEC": "0.01"}), \
             mock.patch.object(kis_api.KISWebSocket, "_get_ws_key", lambda self: "key2"):
            ws.start()
            deadline = time.time() + 5
            while len(_FakeApp.instances) < 4 and time.time() < deadline:
                time.sleep(0.02)
            ws._closing = True
            for app in _FakeApp.instances:
                app.close()

        # 단절 3회 → 재연결로 4번째 인스턴스까지 생성됐어야 한다
        self.assertGreaterEqual(len(_FakeApp.instances), 4)
        self.assertGreaterEqual(ws.disconnect_count, 3)
        # 복구 시 다운타임이 누적된다(판정 표본 신뢰도 원장의 근거)
        self.assertGreater(ws.downtime_sec, 0.0)

    def test_intended_stop_does_not_reconnect(self):
        ws = self._make()
        with mock.patch.dict("sys.modules", {"websocket": mock.MagicMock(WebSocketApp=_FakeApp)}), \
             mock.patch.object(kis_api, "_ws_url", lambda m: "wss://x"), \
             mock.patch.dict("os.environ", {"KIS_WS_RECONNECT_BASE_SEC": "0.01"}):
            ws._closing = True  # stop() 이후 상태
            ws.start()
            time.sleep(0.2)
        self.assertEqual(len(_FakeApp.instances), 1)  # 재생성 없음

    def test_stop_sets_closing_flag(self):
        ws = self._make()
        ws.ws = _FakeApp()
        ws.stop()
        self.assertTrue(ws._closing)
        self.assertFalse(ws.running)


if __name__ == "__main__":
    unittest.main()
