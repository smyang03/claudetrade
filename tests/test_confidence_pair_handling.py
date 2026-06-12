"""확신 짝 처리 — 매수(rc 자기고백 강등) / 매도(저확신 HOLD 재검토 단축)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.action_routing import route_candidate_action
from trading_bot import TradingBot


def _route(codes):
    return route_candidate_action(
        {
            "ticker": "TST",
            "action": "PULLBACK_WAIT",
            "confidence": 0.6,
            "price_targets": {
                "buy_zone_low": 40.0, "buy_zone_high": 41.0,
                "sell_target": 44.0, "stop_loss": 39.0,
                "hold_days": 1, "confidence": 0.6,
            },
        },
        market="US",
        execution_context={
            "evidence_pack_ceiling_enabled": True,
            "evidence_data_state": "ok",
            "evidence_action_ceiling": "",
            "claude_self_report_codes": codes,
        },
    )


class ClaudeSelfReportGateTests(unittest.TestCase):
    def test_no_ev_pack_self_report_demotes_pullback_wait(self):
        with patch.dict(os.environ, {"PULLBACK_WAIT_EVIDENCE_GATE_MODE": "live"}):
            decision = _route("WATCH_NO_EV_PACK")
        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.runtime_gate_reason, "pullback_wait_evidence_gate")

    def test_stale_data_self_report_demotes(self):
        with patch.dict(os.environ, {"PULLBACK_WAIT_EVIDENCE_GATE_MODE": "live"}):
            decision = _route("STALE_DATA_UNRESOLVED")
        self.assertEqual(decision.final_action, "WATCH")

    def test_clean_codes_pass_through(self):
        with patch.dict(os.environ, {"PULLBACK_WAIT_EVIDENCE_GATE_MODE": "live"}):
            decision = _route("OR_BREAK_CONFIRMED")
        self.assertEqual(decision.final_action, "PULLBACK_WAIT")


class LowConfHoldRecheckTests(unittest.TestCase):
    def _bot(self):
        bot = TradingBot.__new__(TradingBot)
        bot._runtime_float = lambda key, default: float(default)
        bot._parse_kst_datetime = TradingBot._parse_kst_datetime.__get__(bot)
        return bot

    def test_low_conf_flag_shortens_cooldown(self):
        from datetime import datetime, timedelta
        from trading_bot import KST

        bot = self._bot()
        recent = (datetime.now(KST) - timedelta(minutes=30)).isoformat(timespec="seconds")
        pos_normal = {"intraday_review_last_at": recent}
        pos_lowconf = {"intraday_review_last_at": recent, "intraday_review_low_conf_hold": True}
        bot._intraday_review_count_for_session = lambda pos, sd: 0
        bot._current_session_date_str = lambda mk: "2026-06-12"

        # 일반: 120분 쿨다운 → 30분 경과 = 차단 / 저확신: 15분 쿨다운 → 30분 경과 = 허용
        gate_normal = self._cooldown_only(bot, pos_normal)
        gate_lowconf = self._cooldown_only(bot, pos_lowconf)
        self.assertFalse(gate_normal["allowed"])
        self.assertTrue(gate_lowconf["allowed"])

    def _cooldown_only(self, bot, pos):
        # 쿨다운 분기만 검증 (상위 트리거/한도 분기는 우회)
        from datetime import datetime
        from trading_bot import KST

        cooldown_min = max(0.0, bot._runtime_float("INTRADAY_REVIEW_COOLDOWN_MINUTES", 120.0))
        if bool(pos.get("intraday_review_low_conf_hold")):
            low_conf = max(1.0, bot._runtime_float("INTRADAY_REVIEW_LOW_CONF_COOLDOWN_MINUTES", 15.0))
            cooldown_min = min(cooldown_min, low_conf)
        last_at = bot._parse_kst_datetime(pos.get("intraday_review_last_at"))
        if cooldown_min > 0 and last_at is not None:
            elapsed = (datetime.now(KST) - last_at).total_seconds() / 60.0
            if elapsed < cooldown_min:
                return {"allowed": False}
        return {"allowed": True}


if __name__ == "__main__":
    unittest.main()
