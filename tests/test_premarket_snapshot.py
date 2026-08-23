# -*- coding: utf-8 -*-
"""프리마켓 표시 계약 테스트 (2026-08-21).

이 기능은 **표시 전용**이다. 매매 판단·기록·원장에 흘러가면 안 된다.
테스트가 지키는 것: 시간 게이트 · 캐시 · fail-silent · 텔레그램 라우팅.
"""
from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from interface import premarket_snapshot as pm

KST = ZoneInfo("Asia/Seoul")

TRUTH = {
    "markets": {
        "US": {
            "positions": [
                {"ticker": "AXTI", "avg_price": 82.09, "qty": 8,
                 "current_price": 73.12, "pnl_pct": -10.93},
            ]
        }
    }
}


def _quote(price, prev):
    return {"price": price, "prev_close": prev}


class TimeGateTests(unittest.TestCase):
    def test_premarket_window_et_0400_to_0930(self):
        """ET 04:00~09:30만 프리마켓. 서머타임은 ZoneInfo가 처리한다."""
        cases = [
            (datetime(2026, 8, 21, 17, 0, tzinfo=KST), True),   # ET 04:00
            (datetime(2026, 8, 21, 21, 0, tzinfo=KST), True),   # ET 08:00
            (datetime(2026, 8, 21, 22, 29, tzinfo=KST), True),  # ET 09:29
            (datetime(2026, 8, 21, 22, 31, tzinfo=KST), False),  # 정규장
            (datetime(2026, 8, 21, 10, 0, tzinfo=KST), False),  # 한국장
            (datetime(2026, 8, 21, 16, 59, tzinfo=KST), False),  # 프리마켓 직전
        ]
        for when, expected in cases:
            with self.subTest(kst=when.strftime("%H:%M")):
                self.assertEqual(pm.is_premarket(when), expected)

    def test_weekend_is_not_premarket(self):
        sat = datetime(2026, 8, 22, 20, 0, tzinfo=KST)
        self.assertFalse(pm.is_premarket(sat))

    def test_outside_premarket_returns_inactive_without_quotes(self):
        """정규장에는 시세를 아예 조회하지 않는다 — 불필요한 KIS 호출 금지."""
        with mock.patch.object(pm, "is_premarket", return_value=False):
            with mock.patch("kis_api.get_price") as gp:
                out = pm.premarket_positions(None)
        self.assertFalse(out["active"])
        self.assertEqual(out["reason"], "not_premarket")
        gp.assert_not_called()


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        # 2026-08-23: 캐시가 모드별 슬롯 dict로 바뀌었다(P2-11). 슬롯 하나를 비우는 게
        # 아니라 전체를 비운다 — 픽스처가 프로덕션 구조와 어긋나면 캐시가 남은 채로
        # 테스트가 통과한다.
        pm._CACHE.clear()

    def test_builds_rows_from_truth_snapshot(self):
        with mock.patch.object(pm, "is_premarket", return_value=True), \
                mock.patch.object(pm, "_held_us_tickers", return_value=[
                    {"ticker": "AXTI", "avg_price": 82.09, "qty": 8,
                     "regular_price": 73.12, "regular_pnl_pct": -10.93}]), \
                mock.patch("kis_api.get_price", return_value=_quote(75.35, 73.12)):
            out = pm.premarket_positions(None)
        self.assertTrue(out["active"])
        row = out["rows"][0]
        self.assertEqual(row["premarket_price"], 75.35)
        self.assertAlmostEqual(row["premarket_chg_pct"], 3.05, places=1)
        self.assertAlmostEqual(row["premarket_pnl_pct"], -8.21, places=1)

    def test_cache_prevents_repeat_quotes(self):
        """대시보드 새로고침 연타가 KIS 호출 연타가 되면 안 된다."""
        with mock.patch.object(pm, "is_premarket", return_value=True), \
                mock.patch.object(pm, "_held_us_tickers", return_value=[
                    {"ticker": "AXTI", "avg_price": 82.09, "qty": 8,
                     "regular_price": 73.12, "regular_pnl_pct": -10.93}]), \
                mock.patch("kis_api.get_price", return_value=_quote(75.35, 73.12)) as gp:
            pm.premarket_positions(None)
            pm.premarket_positions(None)
            pm.premarket_positions(None)
        self.assertEqual(gp.call_count, 1, "60초 안에는 한 번만 조회해야 한다")

    def test_quote_failure_is_silent(self):
        """조회가 터져도 예외를 밖으로 던지지 않는다 — 화면이 죽으면 안 된다."""
        with mock.patch.object(pm, "is_premarket", return_value=True), \
                mock.patch.object(pm, "_held_us_tickers", return_value=[
                    {"ticker": "AXTI", "avg_price": 82.09, "qty": 8,
                     "regular_price": 73.12, "regular_pnl_pct": -10.93}]), \
                mock.patch("kis_api.get_price", side_effect=RuntimeError("boom")):
            out = pm.premarket_positions(None)
        self.assertFalse(out["active"])
        self.assertEqual(out["rows"], [])

    def test_broken_truth_file_is_silent(self):
        with mock.patch.object(pm.Path, "read_text", side_effect=OSError("no file")):
            self.assertEqual(pm._held_us_tickers("live"), [])


class FormatTests(unittest.TestCase):
    def test_not_premarket_message(self):
        text = pm.format_premarket({"active": False, "reason": "not_premarket", "rows": []})
        self.assertIn("프리마켓 시간이 아닙니다", text)

    def test_rows_render_with_disclaimer(self):
        payload = {
            "active": True, "reason": "", "as_of": "2026-08-21T21:14:44+09:00",
            "rows": [{"ticker": "AXTI", "avg_price": 82.09, "premarket_price": 75.35,
                      "premarket_chg_pct": 3.05, "premarket_pnl_pct": -8.21}],
        }
        text = pm.format_premarket(payload)
        self.assertIn("AXTI", text)
        self.assertIn("+3.05%", text)
        self.assertIn("-8.21%", text)
        self.assertIn("표시 전용", text)

    def test_missing_chg_does_not_crash(self):
        payload = {"active": True, "reason": "", "as_of": "2026-08-21T21:14:44+09:00",
                   "rows": [{"ticker": "X", "avg_price": 0, "premarket_price": 1.0,
                             "premarket_chg_pct": None, "premarket_pnl_pct": None}]}
        self.assertIn("X", pm.format_premarket(payload))


class TelegramRoutingTests(unittest.TestCase):
    def test_premarket_command_skips_ops_summary(self):
        """무거운 ops 집계 없이 바로 처리되어야 한다."""
        from interface import v2_telegram

        bot = SimpleNamespace(_mode="live")
        with mock.patch.object(v2_telegram, "build_v2_ops_summary") as summary, \
                mock.patch.object(pm, "premarket_positions",
                                  return_value={"active": False, "reason": "not_premarket", "rows": []}):
            out = v2_telegram.handle_v2_command("/premarket", bot)
        summary.assert_not_called()
        self.assertIn("프리마켓", out)

    def test_command_is_registered(self):
        from interface.v2_ops_summary import V2_TELEGRAM_COMMANDS

        self.assertIn("/premarket", V2_TELEGRAM_COMMANDS)



class DigestScheduleTests(unittest.TestCase):
    """텔레그램 자동 발송 — 시간대·간격·스위치 가드."""

    def _bot(self, **over):
        import trading_bot as tb

        bot = SimpleNamespace()
        bot._mode = "live"
        bot._runtime_bool = lambda k, d=False: over.get(k, d)
        bot._runtime_int = lambda k, d=0: over.get(k, d)
        bot._send_premarket_digest = tb.TradingBot._send_premarket_digest.__get__(bot)
        return bot

    def test_disabled_switch_skips_everything(self):
        bot = self._bot(PREMARKET_DIGEST_ENABLED=False)
        with mock.patch.object(pm, "is_premarket") as gate:
            bot._send_premarket_digest()
        gate.assert_not_called()

    def test_outside_premarket_does_not_send(self):
        bot = self._bot(PREMARKET_DIGEST_ENABLED=True, PREMARKET_DIGEST_INTERVAL_MIN=30)
        with mock.patch.object(pm, "is_premarket", return_value=False), \
                mock.patch("telegram_reporter.send") as send:
            bot._send_premarket_digest()
        send.assert_not_called()

    def test_interval_throttles_repeat_sends(self):
        bot = self._bot(PREMARKET_DIGEST_ENABLED=True, PREMARKET_DIGEST_INTERVAL_MIN=30)
        payload = {"active": True, "reason": "", "as_of": "2026-08-21T21:00:00+09:00",
                   "rows": [{"ticker": "AXTI", "avg_price": 82.09, "premarket_price": 75.35,
                             "premarket_chg_pct": 3.05, "premarket_pnl_pct": -8.21}]}
        with mock.patch.object(pm, "is_premarket", return_value=True), \
                mock.patch.object(pm, "premarket_positions", return_value=payload), \
                mock.patch("telegram_reporter.send") as send:
            bot._send_premarket_digest()
            bot._send_premarket_digest()
            bot._send_premarket_digest()
        self.assertEqual(send.call_count, 1, "간격 안에는 한 번만 보내야 한다")

    def test_send_failure_is_swallowed(self):
        bot = self._bot(PREMARKET_DIGEST_ENABLED=True, PREMARKET_DIGEST_INTERVAL_MIN=30)
        with mock.patch.object(pm, "is_premarket", return_value=True), \
                mock.patch.object(pm, "premarket_positions", side_effect=RuntimeError("boom")):
            bot._send_premarket_digest()  # 예외가 밖으로 나오면 실패

    def test_command_passes_commander_whitelist(self):
        """V2_TELEGRAM_COMMANDS 등록만으로는 부족하다 — commander 화이트리스트도 통과해야."""
        text = (Path(__file__).resolve().parents[1] / "telegram_commander.py").read_text(encoding="utf-8")
        self.assertIn('"/premarket"', text)

if __name__ == "__main__":
    unittest.main()
