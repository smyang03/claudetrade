from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from minority_report import hold_advisor
from runtime.pathb_runtime import PathBRuntime
from trading_bot import TradingBot


class PriceUnitNormalizationTests(unittest.TestCase):
    def test_hold_advisor_us_log_uses_display_usd_prices(self) -> None:
        pos = {
            "ticker": "QCOM",
            "entry": 256_901.1426,
            "current_price": 263_209.2743,
            "display_avg_price": 174.67,
            "display_current_price": 178.79,
            "tp": 276_143.0234,
            "display_tp_price": 187.75,
        }
        votes = {
            "bull": {"action": "SELL", "confidence": 0.8, "reason": "protect profit"},
            "bear": {"action": "SELL", "confidence": 0.8, "reason": "carry risk"},
            "neutral": {"action": "SELL", "confidence": 0.8, "reason": "pre close"},
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def _runtime_path(*parts, make_parents=False):
                path = root.joinpath(*parts)
                if make_parents:
                    path.mkdir(parents=True, exist_ok=True)
                return path

            with patch.object(hold_advisor, "get_runtime_path", side_effect=_runtime_path):
                hold_advisor._log_decision(
                    "QCOM",
                    "US",
                    pos,
                    "SELL",
                    0.03,
                    votes,
                    "PRE_CLOSE_CARRY",
                    "SELL unless carry risk is acceptable.",
                )

            files = list((root / "logs" / "hold_advisor").glob("decisions_*.jsonl"))
            self.assertEqual(len(files), 1)
            row = json.loads(files[0].read_text(encoding="utf-8").strip())

        self.assertEqual(row["price_currency"], "USD")
        self.assertAlmostEqual(row["entry"], 174.67)
        self.assertAlmostEqual(row["current"], 178.79)
        self.assertAlmostEqual(row["pnl_pct"], ((178.79 / 174.67) - 1.0) * 100.0, places=3)

    def test_pathb_position_pnl_pct_keeps_us_native_units(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.bot = SimpleNamespace(usd_krw_rate=1470.0)

        pos = {
            "entry": 174.67 * 1470.0,
            "current_price": 178.79 * 1470.0,
            "display_avg_price": 174.67,
            "display_current_price": 178.79,
        }

        self.assertAlmostEqual(
            runtime._position_pnl_pct(pos, 178.79, "US"),
            ((178.79 / 174.67) - 1.0) * 100.0,
            places=6,
        )

    def test_pathb_position_pnl_pct_keeps_krw_units_for_kr(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.bot = SimpleNamespace(usd_krw_rate=1470.0)

        self.assertAlmostEqual(
            runtime._position_pnl_pct({"entry": 10_000.0}, 10_200.0, "KR"),
            2.0,
            places=6,
        )

    def test_trading_bot_latest_price_context_preserves_us_and_kr_units(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.usd_krw_rate = 1470.0
        bot.price_cache_raw = {"QCOM": 178.79}
        bot.price_cache = {"005930": 71_000.0}

        us_pos = {
            "ticker": "QCOM",
            "entry": 174.67 * 1470.0,
            "current_price": 170.0 * 1470.0,
            "display_avg_price": 174.67,
            "display_current_price": 170.0,
        }
        us_price_pos = bot._position_with_latest_price_context(us_pos, "US")
        self.assertAlmostEqual(us_price_pos["display_current_price"], 178.79)
        self.assertAlmostEqual(us_price_pos["current_price"], 178.79 * 1470.0)
        self.assertAlmostEqual(bot._native_position_price(us_price_pos, "US", current=True), 178.79)

        kr_pos = {"ticker": "005930", "entry": 70_000.0, "current_price": 70_500.0}
        kr_price_pos = bot._position_with_latest_price_context(kr_pos, "KR")
        self.assertAlmostEqual(kr_price_pos["current_price"], 71_000.0)
        self.assertAlmostEqual(kr_price_pos["display_current_price"], 71_000.0)
        self.assertAlmostEqual(bot._native_position_price(kr_price_pos, "KR", current=True), 71_000.0)


if __name__ == "__main__":
    unittest.main()
