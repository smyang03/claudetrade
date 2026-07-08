# -*- coding: utf-8 -*-
"""SELECTION_RULE_DIRECT_<시장> enforce 토글 테스트.

운영자 결정(2026-07-08): Claude selection(멀티티커 랭킹) 콜 제거 — 룰 컷 직결.
- 토글 on: Claude API 콜 0회, 룰 컷 상위가 watchlist, trade_ready=[].
- 토글 off: 기존 경로 무변경(Claude 콜 발생).
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch


def _fake_create_ok(**kwargs):
    return SimpleNamespace(
        content=[SimpleNamespace(text='{"watchlist":["AAPL"],"trade_ready":[],"reasons":{},"veto":{}}')],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        stop_reason="end_turn",
    )


def _fail_create(**kwargs):
    raise AssertionError("rule_direct 켜짐 상태에서 Claude selection 콜이 발생했다")


def _candidates(n=8, market="US"):
    return [
        {
            "ticker": f"T{idx}" if market == "US" else f"00{idx}000",
            "market": market,
            "primary_bucket": "momentum_now",
            "liquidity_bucket": "high",
        }
        for idx in range(n)
    ]


class SelectionRuleDirectTest(unittest.TestCase):
    def test_rule_direct_on_skips_claude_and_returns_rule_watch(self) -> None:
        from minority_report import analysts

        env = {
            "SELECTION_RULE_DIRECT_US": "true",
            "US_WATCHLIST_MAX": "5",
        }
        with patch.dict("os.environ", env, clear=False), \
                patch.object(analysts.client.messages, "create", side_effect=_fail_create):
            tickers, reasons = analysts.select_tickers(
                "US", "digest", "NEUTRAL", _candidates(8)
            )

        self.assertTrue(tickers, "rule_direct watchlist가 비었다")
        self.assertLessEqual(len(tickers), 5, "watch_max 캡 미준수")
        for i, t in enumerate(tickers):
            reason = reasons.get(t) or ""
            self.assertTrue(reason.startswith("rule_direct(rank="), f"사유 구조화 누락: {reason}")
            self.assertIn(f"rank={i+1}", reason, "룰 랭크 기록 불일치")

        meta = analysts.get_last_selection_meta() if hasattr(analysts, "get_last_selection_meta") else analysts._LAST_SELECTION_META
        self.assertTrue(meta.get("_selection_rule_direct"))
        self.assertEqual(list(meta.get("trade_ready") or []), [])
        self.assertEqual(list(meta.get("watchlist") or []), list(tickers))

    def test_rule_direct_off_uses_claude_path(self) -> None:
        from minority_report import analysts

        called = {"n": 0}

        def _count_create(**kwargs):
            called["n"] += 1
            return _fake_create_ok(**kwargs)

        env = {"SELECTION_RULE_DIRECT_US": "false"}
        with patch.dict("os.environ", env, clear=False), \
                patch.object(analysts.client.messages, "create", side_effect=_count_create):
            analysts.select_tickers("US", "digest", "NEUTRAL", _candidates(6))

        self.assertGreaterEqual(called["n"], 1, "토글 off인데 Claude 콜이 없다(경로 변경 회귀)")

    def test_rule_direct_market_isolation(self) -> None:
        """US만 켜면 KR은 기존 경로."""
        from minority_report import analysts

        called = {"n": 0}

        def _count_create(**kwargs):
            called["n"] += 1
            return _fake_create_ok(**kwargs)

        env = {
            "SELECTION_RULE_DIRECT_US": "true",
            "SELECTION_RULE_DIRECT_KR": "false",
        }
        with patch.dict("os.environ", env, clear=False), \
                patch.object(analysts.client.messages, "create", side_effect=_count_create):
            analysts.select_tickers("KR", "digest", "NEUTRAL", _candidates(6, market="KR"))

        self.assertGreaterEqual(called["n"], 1, "KR 토글 off인데 Claude 콜이 없다")


if __name__ == "__main__":
    unittest.main()
