from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trading_bot import TradingBot


class _GateBot:
    _guardian_market_entry_gate = TradingBot._guardian_market_entry_gate
    _mode = "live"

    def _runtime_bool(self, _key: str, _default: bool) -> bool:
        return True

    def _runtime_float(self, _key: str, default: float) -> float:
        return default


class GuardianMarketEntryGateTests(unittest.TestCase):
    def test_blocked_market_does_not_block_healthy_market(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live_guardian_market_gates.json"
            path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-07-13T22:30:00+09:00",
                        "markets": {
                            "KR": {"ok": False, "gate": "BLOCK_START", "blockers": [{"name": "kr"}]},
                            "US": {"ok": True, "gate": "ALLOW_START", "blockers": []},
                        },
                    }
                ),
                encoding="utf-8",
            )
            bot = _GateBot()
            with patch("trading_bot.get_runtime_path", return_value=path):
                kr = bot._guardian_market_entry_gate("KR")
                us = bot._guardian_market_entry_gate("US")
            self.assertFalse(kr["allowed"])
            self.assertEqual(kr["reason"], "GUARDIAN_MARKET_BLOCK")
            self.assertTrue(us["allowed"])


if __name__ == "__main__":
    unittest.main()
