# -*- coding: utf-8 -*-
"""ENTRY_CLAUDE_BUYING_POWER_GATE_<시장> 게이트 테스트 (운영자 결정 2026-07-08).

살 수 없는 상태(현금<주문금액·시장 포지션 max·PathB max)에서 진입측 Claude 콜 스킵.
fail-open: 값 불확실 시 차단하지 않음. 토글 off = 항상 통과.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import trading_bot


class _Stub:
    """게이트가 읽는 최소 인터페이스만 가진 스텁."""

    def __init__(self, *, cash=10_000_000.0, pos=0, pathb=0,
                 env=None):
        self._env = dict(env or {})
        self.risk = SimpleNamespace(cash=cash)
        self._pos = pos
        self.pathb = SimpleNamespace(
            _pathb_open_position_count=lambda mk: pathb
        ) if pathb is not None else None

    def _runtime_bool(self, key, default=False):
        v = self._env.get(key)
        if v is None:
            return default
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    def _runtime_float(self, key, default=0.0):
        v = self._env.get(key)
        try:
            return float(v) if v is not None else float(default)
        except (TypeError, ValueError):
            return float(default)

    def _runtime_int(self, key, default=0):
        v = self._env.get(key)
        try:
            return int(v) if v is not None else int(default)
        except (TypeError, ValueError):
            return int(default)

    def _position_count_by_market(self, market):
        return self._pos


GATE = trading_bot.TradingBot._entry_claude_buying_power_gate


class EntryClaudeBuyingPowerGateTest(unittest.TestCase):
    BASE_ENV = {
        "ENTRY_CLAUDE_BUYING_POWER_GATE_US": "true",
        "ENTRY_CLAUDE_BUYING_POWER_GATE_KR": "true",
        "US_FIXED_ORDER_KRW": "500000",
        "KR_FIXED_ORDER_KRW": "500000",
        "US_MAX_POSITIONS": "20",
        "KR_MAX_POSITIONS": "20",
        "PATHB_MAX_POSITIONS": "15",
    }

    def test_toggle_off_never_blocks(self):
        stub = _Stub(cash=0.0, pos=99, pathb=99, env={})
        blocked, reason = GATE(stub, "US")
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_cash_below_order_blocks(self):
        stub = _Stub(cash=499_999.0, env=self.BASE_ENV)
        blocked, reason = GATE(stub, "US")
        self.assertTrue(blocked)
        self.assertIn("cash_below_order", reason)

    def test_cash_enough_passes(self):
        stub = _Stub(cash=500_000.0, env=self.BASE_ENV)
        blocked, _ = GATE(stub, "US")
        self.assertFalse(blocked)

    def test_market_positions_full_blocks(self):
        stub = _Stub(cash=10_000_000.0, pos=20, env=self.BASE_ENV)
        blocked, reason = GATE(stub, "KR")
        self.assertTrue(blocked)
        self.assertIn("market_positions_full", reason)

    def test_pathb_full_blocks(self):
        stub = _Stub(cash=10_000_000.0, pos=0, pathb=15, env=self.BASE_ENV)
        blocked, reason = GATE(stub, "US")
        self.assertTrue(blocked)
        self.assertIn("pathb_positions_full", reason)

    def test_fail_open_on_unknown_values(self):
        """cash None·포지션 조회 예외·pathb 없음 → 차단하지 않는다."""
        stub = _Stub(cash=None, pos=0, pathb=None, env=self.BASE_ENV)
        stub.risk = SimpleNamespace(cash=None)

        def _boom(market):
            raise RuntimeError("broker unavailable")

        stub._position_count_by_market = _boom
        blocked, reason = GATE(stub, "US")
        self.assertFalse(blocked, f"fail-open 위반: {reason}")

    def test_market_isolation(self):
        """US만 켜면 KR은 통과."""
        env = dict(self.BASE_ENV)
        env["ENTRY_CLAUDE_BUYING_POWER_GATE_KR"] = "false"
        stub = _Stub(cash=0.0, env=env)
        blocked_kr, _ = GATE(stub, "KR")
        blocked_us, _ = GATE(stub, "US")
        self.assertFalse(blocked_kr)
        self.assertTrue(blocked_us)


if __name__ == "__main__":
    unittest.main()
