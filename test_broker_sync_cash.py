import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kis_api
import trading_bot as trading_bot_module
from risk_manager import RiskManager


class BrokerSyncCashTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)

        def _mock_grp(*args, **kwargs):
            if args and args[0] == "state":
                fname = args[1] if len(args) > 1 else ""
                return self._tmp_path / fname
            from runtime_paths import get_runtime_path as _orig
            return _orig(*args, **kwargs)

        self._grp_patch1 = patch("bot.state.get_runtime_path", side_effect=_mock_grp)
        self._grp_patch2 = patch("trading_bot.get_runtime_path", side_effect=_mock_grp)
        self._grp_patch1.start()
        self._grp_patch2.start()

    def tearDown(self):
        self._grp_patch1.stop()
        self._grp_patch2.stop()
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

    @patch.object(kis_api, "IS_PAPER_US", False)
    @patch.object(kis_api, "ACCOUNT_NO_US", "12345678-01")
    def test_us_live_cash_snapshot_reads_foreign_margin_usd(self):
        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "rt_cd": "0",
                    "output": [
                        {
                            "natn_name": "미국",
                            "crcy_cd": "USD",
                            "frcr_dncl_amt1": "33.850000",
                            "frcr_gnrl_ord_psbl_amt": "33.66",
                        },
                        {
                            "natn_name": "독일",
                            "crcy_cd": "USD",
                            "frcr_dncl_amt1": "33.850000",
                            "frcr_gnrl_ord_psbl_amt": "33.85",
                        },
                    ],
                }

        with patch.object(kis_api, "_kis_get", return_value=_Resp()):
            snap = kis_api._get_us_cash_snapshot("token")

        self.assertEqual(snap["currency"], "USD")
        self.assertAlmostEqual(snap["cash"], 33.85, places=6)
        self.assertAlmostEqual(snap["orderable_cash"], 33.66, places=6)

    def test_us_order_feasibility_uses_usd_orderable_cash(self):
        us_balance = {
            "stocks": [],
            "cash": 33.85,
            "orderable_cash": 33.66,
            "total_eval": 0.0,
        }
        with patch.object(kis_api, "get_balance", return_value=us_balance):
            ok = kis_api.precheck_order("QQQ", qty=10, price=3.0, side="buy", token="t", market="US")
            fail = kis_api.precheck_order("QQQ", qty=12, price=3.0, side="buy", token="t", market="US")

        self.assertTrue(ok["ok"])
        self.assertEqual(ok["allowed_qty"], 11)
        self.assertFalse(fail["ok"])
        self.assertEqual(fail["reason"], "insufficient_cash")


if __name__ == "__main__":
    unittest.main()
