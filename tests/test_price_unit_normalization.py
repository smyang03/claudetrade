from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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

    def test_hold_advisor_log_marks_fallback_for_outcome_linkage(self) -> None:
        votes = {
            "bull": {"action": "HOLD", "confidence": 0.0, "reason": "parse fallback", "fallback": True},
            "bear": {"action": "HOLD", "confidence": 0.0, "reason": "parse fallback", "fallback": True},
            "neutral": {"action": "HOLD", "confidence": 0.0, "reason": "parse fallback", "fallback": True},
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
                    "TEST",
                    "US",
                    {"entry": 100.0, "current_price": 101.0, "display_avg_price": 100.0, "display_current_price": 101.0},
                    "HOLD",
                    0.03,
                    votes,
                    "AUTO_SELL_REVIEW",
                    "policy",
                    triage={"action": "HOLD", "triage_parse_error": True, "fallback": True},
                )

            files = list((root / "logs" / "hold_advisor").glob("decisions_*.jsonl"))
            row = json.loads(files[0].read_text(encoding="utf-8").strip())

        self.assertTrue(row["fallback"])
        self.assertFalse(row["cooldown"])
        self.assertEqual(row["decision_source"], "hold_advisor")
        self.assertEqual(row["pending_outcome_label"], "fallback_hold")
        self.assertTrue(row["votes"]["bull"]["fallback"])

    def test_hold_advisor_log_preserves_input_completeness_and_pathb_context(self) -> None:
        votes = {
            "bull": {"action": "HOLD", "confidence": 0.8, "reason": "target intact"},
            "bear": {"action": "SELL", "confidence": 0.5, "reason": "protect"},
            "neutral": {"action": "HOLD", "confidence": 0.7, "reason": "bounded"},
        }
        pos = {
            "ticker": "NVDA",
            "entry": 100.0,
            "current_price": 105.0,
            "tp": 110.0,
            "sl": 98.0,
            "pnl_pct": 5.0,
            "path_type": "claude_price",
            "pathb_path_run_id": "run-1",
            "pathb_origin_action": "PULLBACK_WAIT",
            "pathb_reference_target": 110.0,
            "pathb_reference_stop": 98.0,
            "pathb_profit_ladder_tier": "tier1",
            "auto_sell_reason": "profit_ladder",
            "auto_sell_close_reason": "CLOSED_PROFIT_LADDER",
            "advisor_context_v2": {"pathb_reference_target": 110.0, "pathb_reference_stop": 98.0},
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
                    "NVDA",
                    "US",
                    pos,
                    "HOLD",
                    0.03,
                    votes,
                    "AUTO_SELL_REVIEW",
                    "policy",
                )

            row = json.loads(next((root / "logs" / "hold_advisor").glob("decisions_*.jsonl")).read_text(encoding="utf-8"))

        self.assertGreaterEqual(row["input_completeness"]["score"], 0.8)
        self.assertTrue(row["pathb_revenue_path_context"]["is_pathb"])
        self.assertEqual(row["pathb_revenue_path_context"]["exit_reason"], "profit_ladder")
        self.assertEqual(row["pathb_revenue_path_context"]["profit_ladder_tier"], "tier1")

    def test_hold_advisor_outcome_preserves_fallback_and_cooldown_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "logs" / "hold_advisor"
            log_dir.mkdir(parents=True)
            today = datetime.now().strftime("%Y-%m-%d")
            log_file = log_dir / f"decisions_{today}.jsonl"
            log_file.write_text(
                json.dumps(
                    {
                        "ticker": "TEST",
                        "decision": "HOLD",
                        "decision_stage": "AUTO_SELL_REVIEW",
                        "decision_source": "auto_sell_review_cooldown",
                        "fallback": False,
                        "cooldown": True,
                        "pnl_pct": 1.2,
                        "votes": {},
                        "outcome": None,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            def _runtime_path(*parts, make_parents=False):
                path = root.joinpath(*parts)
                if make_parents:
                    path.mkdir(parents=True, exist_ok=True)
                return path

            bot = TradingBot.__new__(TradingBot)
            bot.price_cache = {}
            bot.usd_krw_rate = 1400.0
            with patch("runtime_paths.get_runtime_path", side_effect=_runtime_path):
                bot._update_hold_advisor_jsonl_outcome("TEST", "HOLD", True, 105.0, 2.5)

            row = json.loads(log_file.read_text(encoding="utf-8").strip())

        self.assertEqual(row["outcome"]["outcome_label"], "cooldown_hold")
        self.assertTrue(row["outcome"]["advisor_cooldown"])
        self.assertFalse(row["outcome"]["advisor_fallback"])
        self.assertEqual(row["outcome"]["decision_source"], "auto_sell_review_cooldown")

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

    def test_trading_bot_kr_native_price_prefers_broker_display_price(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.usd_krw_rate = 1470.0
        bot.price_cache = {}
        bot.price_cache_raw = {}

        pos = {
            "ticker": "128940",
            "entry": 453_500.0,
            "current_price": 454_000.0,
            "display_current_price": 469_000.0,
        }
        price_pos = bot._position_with_latest_price_context(pos, "KR")

        self.assertAlmostEqual(price_pos["current_price"], 469_000.0)
        self.assertAlmostEqual(bot._native_position_price(price_pos, "KR", current=True), 469_000.0)

    def test_refresh_position_prices_from_broker_updates_kr_cache(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.usd_krw_rate = 1470.0
        bot.price_cache = {}
        bot.price_cache_raw = {}
        bot._broker_state = {}
        pos = {
            "ticker": "128940",
            "entry": 453_500.0,
            "current_price": 454_000.0,
            "display_current_price": 454_000.0,
        }
        bot.risk = SimpleNamespace(positions=[pos], update_prices=Mock())
        bot._token_for_market = lambda market, force_refresh=False: "token"  # type: ignore[method-assign]

        with patch(
            "trading_bot.get_balance",
            return_value={
                "cash": 1_000_000,
                "total_eval": 469_000,
                "stocks": [{"ticker": "128940", "qty": 1, "avg_price": 453_500.0, "eval_price": 469_000.0}],
            },
        ):
            summary = bot._refresh_position_prices_from_broker("KR", [pos], reason="test")

        self.assertEqual(summary["updated"], ["128940"])
        self.assertAlmostEqual(pos["current_price"], 469_000.0)
        self.assertAlmostEqual(pos["display_current_price"], 469_000.0)
        self.assertAlmostEqual(bot.price_cache["128940"], 469_000.0)
        bot.risk.update_prices.assert_called_once()


if __name__ == "__main__":
    unittest.main()
