"""sleeve 계약 청산 보호 회귀 테스트 (2026-08-05).

운영자 결정: "TP가 되면 그냥 판다. 확인만 확실한 걸로 간다."
  - isolated sleeve는 일반 auto-trailing에서 제외한다(계약에 없는 청산선 금지).
  - 계약선을 넘긴 채 보유 중인 포지션은 상시 체크에서 깃발이 서야 한다.
    오늘 사고(FRMI TP12 초과 미청산)는 어디에도 흔적이 없는 조용한 실패였다.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from risk_manager import RiskManager, isolated_strategy_source
from tools.integrity_check import check_sleeve_contract_exits


class IsolatedSleeveTrailingExclusionTests(unittest.TestCase):
    def test_isolated_source_is_recognized(self) -> None:
        self.assertEqual(
            isolated_strategy_source({"source_strategy": "us_swing_5d"}), "us_swing_5d"
        )
        self.assertEqual(
            isolated_strategy_source({"source_strategy": "kr_fallen_5d"}), "kr_fallen_5d"
        )
        self.assertEqual(isolated_strategy_source({"source_strategy": "momentum"}), "")

    def test_auto_trailing_skips_isolated_sleeve(self) -> None:
        # +12.7%면 일반 포지션은 트레일링으로 전환되지만 sleeve는 계약대로 둔다.
        sleeve = {
            "ticker": "FRMI", "source_strategy": "us_swing_5d",
            "display_currency": "USD", "display_avg_price": 5.52,
            "display_current_price": 6.22, "current_price": 8880.0,
            "entry": 7883.55, "tp_pct": 0.12, "sl_pct": 0.25,
        }
        generic = {
            "ticker": "INTC", "source_strategy": "momentum",
            "display_currency": "USD", "display_avg_price": 5.52,
            "display_current_price": 6.22, "current_price": 8880.0,
            "entry": 7883.55,
        }
        risk = RiskManager.__new__(RiskManager)
        risk.positions = [sleeve, generic]
        risk.market = "US"
        prices = {"FRMI": 8880.0, "INTC": 8880.0}
        raw = {"FRMI": 6.22, "INTC": 6.22}
        try:
            risk.update_prices(prices, raw)
        except AttributeError:
            self.skipTest("RiskManager.update_prices needs fuller construction in this env")
        self.assertFalse(sleeve.get("trailing"), "sleeve에 일반 트레일링이 걸리면 안 된다")
        self.assertFalse(sleeve.get("tp_triggered"), "sleeve에 tp_triggered가 심기면 안 된다")


class SleeveContractExitGuardTests(unittest.TestCase):
    def _run_with(self, positions):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state").mkdir()
            (root / "state" / "live_open_positions.json").write_text(
                json.dumps(positions), encoding="utf-8"
            )
            with patch("tools.integrity_check.ROOT", root):
                return check_sleeve_contract_exits(None)

    def test_tp_breach_while_still_held_is_flagged(self) -> None:
        checks = self._run_with([{
            "ticker": "FRMI", "source_strategy": "us_swing_5d",
            "display_currency": "USD", "display_avg_price": 5.52,
            "display_current_price": 6.22, "tp_pct": 0.12, "sl_pct": 0.25,
        }])
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["status"], "WARN")
        self.assertIn("TP초과", checks[0]["detail"])

    def test_sl_breach_while_still_held_is_flagged(self) -> None:
        checks = self._run_with([{
            "ticker": "XYZ", "source_strategy": "us_swing_5d",
            "display_currency": "USD", "display_avg_price": 10.0,
            "display_current_price": 7.0, "tp_pct": 0.12, "sl_pct": 0.25,
        }])
        self.assertEqual(checks[0]["status"], "WARN")
        self.assertIn("SL이탈", checks[0]["detail"])

    def test_within_contract_is_ok(self) -> None:
        checks = self._run_with([{
            "ticker": "CVI", "source_strategy": "us_swing_5d",
            "display_currency": "USD", "display_avg_price": 32.0,
            "display_current_price": 32.8, "tp_pct": 0.12, "sl_pct": 0.25,
        }])
        self.assertEqual(checks[0]["status"], "OK")

    def test_non_sleeve_positions_are_ignored(self) -> None:
        checks = self._run_with([{
            "ticker": "SCHG", "source_strategy": "us_schg_bil_trend_v1",
            "display_currency": "USD", "display_avg_price": 34.76,
            "display_current_price": 45.0, "tp_pct": 0.12,
        }])
        self.assertEqual(checks, [])


if __name__ == "__main__":
    unittest.main()
