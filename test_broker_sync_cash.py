import tempfile
import unittest
import json
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
        bot._daily_baseline_by_market = {"KR": {}, "US": {}}
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

    def test_market_daily_return_pct_uses_market_snapshot_not_shared_cash(self):
        bot = self._make_bot()
        bot.is_paper = False
        bot.risk.cash = 2_170_428.0
        bot.risk.positions = [
            {"ticker": "OKLO", "qty": 1, "current_price": 112_607.0, "entry": 112_607.0},
        ]
        bot._daily_baseline_by_market["US"] = {
            "session_date": "2026-04-23",
            "base": 1_078_800.0,
            "source": "broker_total",
        }
        bot._broker_state["US"] = {
            "trust_level": "trusted",
            "last_ok_at": "",
            "last_error": "",
            "last_snapshot": {"market": "US", "cash_krw": 1_000_000.0, "eval_krw": 50_000.0, "total_krw": 1_050_000.0},
            "last_trusted_snapshot": {"market": "US", "cash_krw": 1_000_000.0, "eval_krw": 50_000.0, "total_krw": 1_050_000.0},
        }
        bot._broker_state["KR"] = {
            "trust_level": "trusted",
            "last_ok_at": "",
            "last_error": "",
            "last_snapshot": {"market": "KR", "cash_krw": 1_120_428.0, "eval_krw": 0.0, "total_krw": 1_120_428.0},
            "last_trusted_snapshot": {"market": "KR", "cash_krw": 1_120_428.0, "eval_krw": 0.0, "total_krw": 1_120_428.0},
        }

        daily_return = bot._market_daily_return_pct("US")

        self.assertAlmostEqual(daily_return, (1_050_000.0 - 1_078_800.0) / 1_078_800.0 * 100.0, places=6)

    def test_market_halt_does_not_auto_release_from_shared_pool_gain(self):
        bot = self._make_bot()
        bot.is_paper = False
        bot.risk.halted = True
        bot.risk.halt_reason = "daily_loss"
        bot.risk.daily_pnl = -9_151.0
        bot.risk.cash = 2_170_428.0
        bot._daily_baseline_by_market["US"] = {
            "session_date": "2026-04-23",
            "base": 1_078_800.0,
            "source": "broker_total",
        }
        bot._broker_state["US"] = {
            "trust_level": "trusted",
            "last_ok_at": "",
            "last_error": "",
            "last_snapshot": {"market": "US", "cash_krw": 960_000.0, "eval_krw": 0.0, "total_krw": 960_000.0},
            "last_trusted_snapshot": {"market": "US", "cash_krw": 960_000.0, "eval_krw": 0.0, "total_krw": 960_000.0},
        }

        halted = bot._check_market_halt("US", allow_auto_release=True)

        self.assertTrue(halted)
        self.assertTrue(bot.risk.halted)
        self.assertEqual(bot.risk.halt_reason, "daily_loss")

    def test_market_broker_total_equity_reads_single_market_balance(self):
        bot = self._make_bot()
        bot.is_paper = False
        kr_balance = {"cash": 1_010_738.0, "total_eval": 0.0, "stocks": []}

        with patch.object(trading_bot_module, "get_balance", return_value=kr_balance):
            total = bot._market_broker_total_equity_krw("KR")

        self.assertEqual(total, 1_010_738.0)
        self.assertEqual(bot._broker_state["KR"]["trust_level"], "trusted")
        self.assertEqual(bot._broker_state["KR"]["last_snapshot"]["total_krw"], 1_010_738.0)

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

    def test_startup_mid_session_bypasses_startup_guard(self):
        bot = self._make_bot()
        with patch.object(bot, "_seconds_until_session_close", return_value=300.0):
            self.assertEqual(bot._compute_startup_guard_sec("KR", "startup_mid_session"), 0.0)
            self.assertEqual(
                bot._compute_startup_guard_sec("KR", "schedule"),
                trading_bot_module._STARTUP_GUARD_SEC,
            )

    def test_restore_daily_pnl_from_decisions_uses_closed_records_of_today_only(self):
        bot = self._make_bot()
        today = trading_bot_module._market_session_date("KR").isoformat()
        records = [
            {
                "type": "closed",
                "timestamp": f"{today}T13:13:39+09:00",
                "market": "KR",
                "ticker": "047040",
                "order_no": "0001",
                "pnl_krw": 5944.8,
            },
            {
                "type": "closed",
                "timestamp": f"{today}T13:13:39+09:00",
                "market": "KR",
                "ticker": "047040",
                "order_no": "0001",
                "pnl_krw": 5944.8,
            },
            {
                "type": "closed",
                "timestamp": f"{today}T15:13:04+09:00",
                "market": "KR",
                "ticker": "009150",
                "order_no": "0002",
                "pnl_krw": 9012.3,
            },
            {
                "type": "closed",
                "timestamp": f"{today}T15:13:04+09:00",
                "market": "US",
                "ticker": "QQQ",
                "order_no": "0003",
                "pnl_krw": 7777.0,
            },
            {
                "type": "open",
                "timestamp": f"{today}T15:13:04+09:00",
                "market": "KR",
                "ticker": "AAA",
                "order_no": "0004",
                "pnl_krw": 9999.0,
            },
        ]
        decisions_path = self._tmp_path / "paper_decisions.jsonl"
        decisions_path.write_text(
            "\n".join(json.dumps(rec, ensure_ascii=False) for rec in records) + "\n",
            encoding="utf-8",
        )

        with patch.object(trading_bot_module, "DECISIONS_FILE", decisions_path):
            bot._restore_daily_pnl_from_decisions("KR")

        self.assertAlmostEqual(bot.risk.daily_pnl, 5944.8 + 9012.3, places=3)

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
