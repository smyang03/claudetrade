from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import trading_bot
from trading_bot import TradingBot


def _make_tuning_bot(market: str = "KR") -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.session_active = True
    bot.current_market = market
    bot.tuning_count = 0
    bot._index_history = {market: []}
    bot.today_judgment = {
        "consensus": {"mode": "CAUTIOUS", "size": 35},
        "judgments": {},
        "digest_raw": {"breadth_summary": {}},
    }
    bot._last_tune_result = {market: {}}
    bot._tune_maintain_streak = {market: 0}
    bot._session_events = []
    bot.price_cache = {"005930": 70500.0, "HPQ": 42000.0}
    bot.price_cache_raw = {"HPQ": 30.0}
    bot.usd_krw_rate = 1400.0
    bot.is_paper = True

    positions = [
        {
            "ticker": "005930",
            "qty": 2,
            "entry": 70000.0,
            "current_price": 70500.0,
            "strategy": "momentum",
            "sl": 68000.0,
            "tp": 74000.0,
        },
        {
            "ticker": "HPQ",
            "qty": 3,
            "entry": 28.0,
            "current_price": 30.0,
            "strategy": "momentum",
            "sl": 26.0,
            "tp": 32.0,
        },
    ]
    bot.risk = SimpleNamespace(positions=positions, close_position=Mock(return_value=None))

    bot._enter_market_task = Mock(return_value=True)
    bot._leave_market_task = Mock()
    bot._build_current_breadth_summary = Mock(return_value={})
    bot._runtime_overrides = Mock(return_value={})
    bot._build_execution_profile_text = Mock(return_value="ok")
    bot._format_ops_review_context = Mock(return_value="ok")
    bot._brain_context_for_judge = Mock(return_value=("", {}))
    bot._apply_runtime_tuning_adjustments = Mock(return_value={})
    bot._runtime_gate_state_text = Mock(return_value="ok")
    bot._persist_live_judgment = Mock()
    bot._maybe_push_dashboard = Mock()
    bot._should_reinvoke_analysts = Mock(return_value=(False, ""))
    bot._reinvoke_analysts = Mock()
    bot._partial_reselect = Mock()
    bot._lookup_ticker_name = Mock(return_value="")
    return bot


class IntradayTuningMarketScopeTests(TestCase):
    def test_kr_tuning_uses_only_kr_positions_for_prompt_sl_and_report(self) -> None:
        bot = _make_tuning_bot()
        captured: dict[str, object] = {}

        def fake_tune(market, elapsed, current_state, morning_judgment, brain_summary):
            captured["market"] = market
            captured["state"] = current_state
            return {
                "action": "TIGHTEN",
                "mode": "CAUTIOUS",
                "size_adj": 0,
                "sl_adj": 0.02,
                "reason": "tighten KR only",
                "warning": None,
            }

        with (
            patch.object(trading_bot, "get_index_change", return_value=0.0),
            patch.object(trading_bot, "get_market_vol_trend", return_value="normal"),
            patch.object(trading_bot, "tune", side_effect=fake_tune),
            patch.object(trading_bot, "tuning_report") as tuning_report,
        ):
            TradingBot.run_tuning(bot, "KR")

        current_state = captured["state"]
        self.assertEqual([p["ticker"] for p in current_state["positions"]], ["005930"])
        self.assertAlmostEqual(bot.risk.positions[0]["sl"], 69360.0)
        self.assertEqual(bot.risk.positions[1]["sl"], 26.0)
        report_positions = tuning_report.call_args.args[3]
        self.assertEqual([p["ticker"] for p in report_positions], ["005930"])

    def test_us_tuning_uses_only_us_positions_for_prompt_sl_and_report(self) -> None:
        bot = _make_tuning_bot("US")
        captured: dict[str, object] = {}

        def fake_tune(market, elapsed, current_state, morning_judgment, brain_summary):
            captured["market"] = market
            captured["state"] = current_state
            return {
                "action": "TIGHTEN",
                "mode": "CAUTIOUS",
                "size_adj": 0,
                "sl_adj": 0.02,
                "reason": "tighten US only",
                "warning": None,
            }

        with (
            patch.object(trading_bot, "get_index_change", return_value=0.0),
            patch.object(trading_bot, "get_market_vol_trend", return_value="normal"),
            patch.object(trading_bot, "tune", side_effect=fake_tune),
            patch.object(trading_bot, "tuning_report") as tuning_report,
        ):
            TradingBot.run_tuning(bot, "US")

        current_state = captured["state"]
        self.assertEqual([p["ticker"] for p in current_state["positions"]], ["HPQ"])
        self.assertEqual(bot.risk.positions[0]["sl"], 68000.0)
        self.assertAlmostEqual(bot.risk.positions[1]["sl"], 26.52)
        report_positions = tuning_report.call_args.args[3]
        self.assertEqual([p["ticker"] for p in report_positions], ["HPQ"])

    def test_kr_tuning_reverse_closes_only_kr_positions(self) -> None:
        bot = _make_tuning_bot()

        def fake_tune(market, elapsed, current_state, morning_judgment, brain_summary):
            return {
                "action": "REVERSE",
                "mode": "CAUTIOUS",
                "size_adj": 0,
                "sl_adj": 0.0,
                "reason": "reverse KR only",
                "warning": None,
            }

        with (
            patch.object(trading_bot, "get_index_change", return_value=-1.0),
            patch.object(trading_bot, "get_market_vol_trend", return_value="down"),
            patch.object(trading_bot, "tune", side_effect=fake_tune),
            patch.object(trading_bot, "tuning_report"),
        ):
            TradingBot.run_tuning(bot, "KR")

        bot.risk.close_position.assert_called_once_with("005930", 70500.0, "tuner_reverse")

    def test_us_tuning_reverse_closes_only_us_positions(self) -> None:
        bot = _make_tuning_bot("US")

        def fake_tune(market, elapsed, current_state, morning_judgment, brain_summary):
            return {
                "action": "REVERSE",
                "mode": "CAUTIOUS",
                "size_adj": 0,
                "sl_adj": 0.0,
                "reason": "reverse US only",
                "warning": None,
            }

        with (
            patch.object(trading_bot, "get_index_change", return_value=-1.0),
            patch.object(trading_bot, "get_market_vol_trend", return_value="down"),
            patch.object(trading_bot, "tune", side_effect=fake_tune),
            patch.object(trading_bot, "tuning_report"),
        ):
            TradingBot.run_tuning(bot, "US")

        bot.risk.close_position.assert_called_once_with("HPQ", 42000.0, "tuner_reverse")
