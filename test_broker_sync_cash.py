import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import trading_bot as trading_bot_module
from risk_manager import RiskManager


class BrokerSyncCashTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_positions_file = trading_bot_module.POSITIONS_FILE
        self._orig_daily_baseline_file = trading_bot_module.DAILY_BASELINE_FILE
        trading_bot_module.POSITIONS_FILE = Path(self._tmp.name) / "open_positions.json"
        trading_bot_module.DAILY_BASELINE_FILE = Path(self._tmp.name) / "daily_baseline.json"

    def tearDown(self):
        trading_bot_module.POSITIONS_FILE = self._orig_positions_file
        trading_bot_module.DAILY_BASELINE_FILE = self._orig_daily_baseline_file
        self._tmp.cleanup()

    def _make_bot(self):
        bot = trading_bot_module.TradingBot.__new__(trading_bot_module.TradingBot)
        bot.is_paper = True
        bot.token = "test-token"
        bot.usd_krw_rate = 1460.0
        bot.risk = RiskManager(init_cash=0, max_order_krw=0, market="KR")
        bot.pending_orders = []
        bot._session_closed_tickers = {"KR": set(), "US": set()}
        bot._execution_flags = {"KR": set(), "US": set()}
        bot._broker_state = {
            "KR": {"trust_level": "unknown", "last_ok_at": "", "last_error": "", "last_snapshot": {}, "last_trusted_snapshot": {}},
            "US": {"trust_level": "unknown", "last_ok_at": "", "last_error": "", "last_snapshot": {}, "last_trusted_snapshot": {}},
        }
        bot.today_judgment = {}
        bot._lookup_ticker_name = lambda ticker, market: ticker
        return bot

    def test_injected_us_paper_position_reduces_shared_cash_once(self):
        bot = self._make_bot()
        bot.risk.cash = 53_394_848.0
        bot.risk.positions = []

        kr_balance = {"cash": 53_394_848.0, "total_eval": 0.0, "stocks": []}
        us_balance = {
            "cash": 0.0,
            "total_eval": 4_682.0,
            "stocks": [{"ticker": "VG", "qty": 410, "avg_price": 11.41, "eval_price": 11.43}],
        }

        with patch.object(trading_bot_module, "get_balance", side_effect=[kr_balance, us_balance]):
            bot._sync_runtime_with_broker()

        expected_cost = 410 * 11.41 * 1460.0
        self.assertAlmostEqual(bot.risk.cash, 53_394_848.0 - expected_cost, places=3)
        self.assertEqual(len(bot.risk.positions), 1)
        self.assertEqual(bot.risk.positions[0]["ticker"], "VG")

    def test_us_paper_sell_proceeds_are_not_overwritten_on_next_sync(self):
        bot = self._make_bot()
        bot.risk.cash = 53_394_848.0
        bot.risk.positions = []

        kr_balance = {"cash": 53_394_848.0, "total_eval": 0.0, "stocks": []}
        us_balance_open = {
            "cash": 0.0,
            "total_eval": 4_682.0,
            "stocks": [{"ticker": "VG", "qty": 410, "avg_price": 11.41, "eval_price": 11.43}],
        }
        with patch.object(trading_bot_module, "get_balance", side_effect=[kr_balance, us_balance_open]):
            bot._sync_runtime_with_broker()

        sell_price_krw = 11.43 * 1460.0
        closed = bot.risk.close_position("VG", sell_price_krw, "tp_analyst_sell")
        self.assertIsNotNone(closed)
        cash_after_sell = bot.risk.cash

        us_balance_flat = {"cash": 0.0, "total_eval": 0.0, "stocks": []}
        with patch.object(trading_bot_module, "get_balance", side_effect=[kr_balance, us_balance_flat]):
            bot._sync_runtime_with_broker()

        self.assertAlmostEqual(bot.risk.cash, cash_after_sell, places=3)
        self.assertEqual(bot.risk.positions, [])

    def test_reset_daily_state_accepts_broker_override_base(self):
        rm = RiskManager(init_cash=10_000_000, max_order_krw=0, market="KR")
        rm.cash = 7_000_000
        rm.reset_daily_state(override_base=53_394_848.0)
        self.assertEqual(rm.session_start_equity, 53_394_848.0)

    def test_check_halt_requires_equity_and_realized_breach(self):
        rm = RiskManager(init_cash=10_000_000, max_order_krw=0, market="KR")
        rm.reset_daily_state(override_base=60_236_853.0)
        rm.cash = 53_394_848.0
        rm.daily_pnl = 17_523.0
        self.assertFalse(rm.check_halt())
        self.assertFalse(rm.halted)

    def test_daily_baseline_persists_across_restart(self):
        bot = self._make_bot()
        bot._daily_baseline_by_market = {
            "KR": {},
            "US": {"session_date": "2026-04-18", "base": 53_394_848.0, "source": "broker_total"},
        }
        bot._save_daily_baselines()

        restored = self._make_bot()
        restored._daily_baseline_by_market = {"KR": {}, "US": {}}
        restored._load_daily_baselines()
        self.assertEqual(restored._daily_baseline_by_market["US"]["session_date"], "2026-04-18")
        self.assertEqual(restored._daily_baseline_by_market["US"]["base"], 53_394_848.0)


if __name__ == "__main__":
    unittest.main()
