from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bot.candidate_policy import normalize_selection_result
from config.v2 import V2Config
from lifecycle.event_store import EventStore
from runtime.broker_truth_snapshot import BrokerTruthSnapshot
from runtime.market_resolver import infer_ticker_market
from runtime.pathb_runtime import PathBControlState, PathBRuntime


class _Risk:
    def __init__(self) -> None:
        self.cash = 1_000_000
        self.positions = []
        self.daily_pnl = 0
        self.halted = False
        self.halt_reason = ""

    def close_position(self, ticker: str, exit_price: float, reason: str):
        for idx, pos in enumerate(self.positions):
            if pos.get("ticker") == ticker:
                return self.positions.pop(idx)
        return None


class _V2:
    brain_snapshot_ids = {"KR": "brain_kr"}


class _Bot:
    def __init__(self) -> None:
        self.is_paper = False
        self.token = "dummy"
        self.risk = _Risk()
        self.pending_orders = []
        self.v2 = _V2()
        self.session_active = True
        self.current_market = "KR"
        self.usd_krw_rate = 1350
        self.price_cache_raw = {}
        self.price_cache = {}
        self._v2_same_day_stop_tickers = {"KR": set(), "US": set()}
        self.lifecycle_events = []

    def _current_session_date_str(self, market: str) -> str:
        return "2026-04-27"

    def _v2_decision_id_for_ticker(self, market: str, ticker: str) -> str:
        return f"dec_{market}_{ticker}"

    def _lookup_ticker_name(self, ticker: str, market: str) -> str:
        return ticker

    def _price_to_krw(self, price: float, market: str) -> float:
        return float(price) if market == "KR" else float(price) * self.usd_krw_rate

    def _market_daily_return_pct(self, market: str) -> float:
        return 0.0

    def _minutes_to_close(self, market: str) -> float:
        return 120.0

    def _ticker_market(self, ticker: str) -> str:
        return infer_ticker_market(ticker, unknown="KR")

    def _add_pending_order(self, order: dict) -> None:
        self.pending_orders.append(order)

    def _block_entry(self, *args, **kwargs) -> None:
        return None

    def _v2_record_lifecycle_event(self, event_type: str, market: str, ticker: str, **kwargs) -> None:
        self.lifecycle_events.append({"event_type": event_type, "market": market, "ticker": ticker, **kwargs})

    def _execute_sell(self, cand: dict, market: str, reason: str):
        self.risk.close_position(cand["ticker"], cand["exit_price"], reason)


class _Control:
    def load(self) -> PathBControlState:
        return PathBControlState(enabled=True, emergency_disabled=False)


def _runtime(bot: _Bot, store: EventStore) -> PathBRuntime:
    config = V2Config(pathb_fixed_order_krw=120_000, kr_min_order_krw=100_000, us_min_order_krw=100_000)
    runtime = PathBRuntime(bot, is_paper=False, store=store, config=config)
    runtime.control_store = _Control()
    return runtime


class PathBClaudeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._pathb_env = patch.dict(
            "os.environ",
            {
                "CANDIDATE_ACTIONS_V2_ENABLED": "false",
                "PATHB_KR_LIVE_ENABLED": "true",
                "PATHB_ENTRY_SCAN_BROKER_TRUTH_REFRESH_ENABLED": "false",
                "KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK": "false",
            },
        )
        self._pathb_env.start()

    def tearDown(self) -> None:
        self._pathb_env.stop()

    def test_claude_like_valid_payload_buy_and_sell_path_no_crash(self) -> None:
        candidates = [{"ticker": "005930"}, {"ticker": "000660"}]
        claude_payload = {
            "watchlist": ["005930", "000660"],
            "trade_ready": ["005930"],
            "price_targets": {
                "005930": {
                    "buy_zone_low": "52,000",
                    "buy_zone_high": "52,500",
                    "sell_target": "54,500",
                    "stop_loss": "51,000",
                    "hold_days": 1,
                    "confidence": 0.72,
                    "cancel_if_open_above": "53,500",
                    "entry_rationale": "support pullback",
                    "exit_rationale": "resistance",
                    "rationale": "support pullback",
                    "entry_basis_tags": ["support"],
                    "exit_basis_tags": ["resistance"],
                    "invalidation_conditions": ["gap_above_cancel"],
                },
                "000660": {
                    "buy_zone_low": 120000,
                    "buy_zone_high": 121000,
                    "sell_target": 125000,
                    "stop_loss": 118000,
                    "hold_days": 1,
                    "confidence": 0.9,
                },
            },
        }
        meta = normalize_selection_result(claude_payload, candidates, "KR")
        self.assertIn("005930", meta["price_targets"])
        self.assertNotIn("000660", meta["price_targets"])
        meta["v2_decision_ids"] = {"005930": "dec1"}

        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = _runtime(bot, store)
            runs = runtime.register_from_selection_meta("KR", meta)
            self.assertEqual(len(runs), 1)

            bot.price_cache_raw["005930"] = 52_100
            bot.price_cache["005930"] = 52_100
            with patch("runtime.pathb_runtime.precheck_order", return_value={"ok": True}), patch(
                "runtime.pathb_runtime.place_order",
                return_value={"success": True, "order_no": "ord1"},
            ), patch("runtime.pathb_runtime.buy_order_alert"):
                runtime.scan_waiting_entries("KR", force=True)

            self.assertEqual(len(bot.pending_orders), 1)
            self.assertEqual(bot.pending_orders[0]["qty"], 2)
            self.assertEqual(store.find_path_run(runs[0])["status"], "ORDER_ACKED")

            order = dict(bot.pending_orders[0])
            order["filled_price_native"] = 52_200
            runtime.on_buy_fill(order, position={"ticker": "005930"}, partial=False)
            self.assertEqual(store.find_path_run(runs[0])["status"], "FILLED")

            bot.risk.positions.append({
                "ticker": "005930",
                "qty": 2,
                "entry": 52_200,
                "display_avg_price": 52_200,
                "display_current_price": 54_600,
                "current_price": 54_600,
                "sl": 51_000,
                "path_type": "claude_price",
                "pathb_path_run_id": runs[0],
                "v2_decision_id": "dec1",
                "v2_execution_id": "ord1",
                "position_id": "pos1",
            })
            bot.price_cache_raw["005930"] = 54_600
            bot.price_cache["005930"] = 54_600
            with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.9}), patch(
                "runtime.pathb_runtime.precheck_order", return_value={"ok": True}
            ), patch(
                "runtime.pathb_runtime.place_order",
                return_value={"success": True, "order_no": "sell1"},
            ):
                runtime.scan_exits("KR", force=True)
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda: "token",
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [
                    {"ticker": "005930", "side": "sell", "order_no": "sell1", "order_qty": 2, "filled_qty": 2, "remaining_qty": 0, "avg_price": 54_600}
                ],
                date_provider=lambda market: "2026-04-27",
            )
            runtime.reconcile_sell_pending("KR", force=True)
            self.assertEqual(store.find_path_run(runs[0])["status"], "CLOSED")

    def test_claude_like_bad_payload_is_blocked_not_crashed(self) -> None:
        candidates = [{"ticker": "005930"}]
        bad_payload = {
            "watchlist": ["005930"],
            "trade_ready": ["005930"],
            "price_targets": {
                "005930": {
                    "buy_zone_low": 52_000,
                    "buy_zone_high": 52_500,
                    "sell_target": 51_000,
                    "stop_loss": 53_000,
                    "hold_days": 1,
                    "confidence": 0.9,
                }
            },
        }
        meta = normalize_selection_result(bad_payload, candidates, "KR")
        meta["v2_decision_ids"] = {"005930": "dec_bad"}

        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            runtime = _runtime(bot, EventStore(Path(tmp) / "events.db"))
            runs = runtime.register_from_selection_meta("KR", meta)

        self.assertEqual(runs, [])
        self.assertEqual(bot.lifecycle_events[-1]["reason_code"], "CLAUDE_PRICE_INVALID")

    def test_buy_order_exception_marks_order_unknown_without_crashing(self) -> None:
        candidates = [{"ticker": "005930"}]
        meta = normalize_selection_result(
            {
                "watchlist": ["005930"],
                "trade_ready": ["005930"],
                "price_targets": {
                    "005930": {
                        "buy_zone_low": 52_000,
                        "buy_zone_high": 52_500,
                        "sell_target": 54_500,
                        "stop_loss": 51_000,
                        "hold_days": 1,
                        "confidence": 0.7,
                    }
                },
            },
            candidates,
            "KR",
        )
        meta["v2_decision_ids"] = {"005930": "dec_exception"}
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = _runtime(bot, store)
            runs = runtime.register_from_selection_meta("KR", meta)
            bot.price_cache_raw["005930"] = 52_100
            bot.price_cache["005930"] = 52_100
            with patch("runtime.pathb_runtime.precheck_order", return_value={"ok": True}), patch(
                "runtime.pathb_runtime.place_order",
                side_effect=RuntimeError("broker down"),
            ):
                runtime.scan_waiting_entries("KR", force=True)

            self.assertEqual(store.find_path_run(runs[0])["status"], "ORDER_UNKNOWN")
            self.assertIn("buy_order_exception", store.find_path_run(runs[0])["plan"]["order_unknown_detail"])

    def test_recovered_or_missing_price_targets_keeps_path_a_safe(self) -> None:
        candidates = [{"ticker": "005930"}]
        recovered = normalize_selection_result({"_parse_recovered": True, "tickers": ["005930"]}, candidates, "KR")
        self.assertEqual(recovered["trade_ready"], [])
        self.assertEqual(recovered["price_targets"], {})

        legacy = normalize_selection_result({"tickers": ["005930"]}, candidates, "KR")
        self.assertEqual(legacy["trade_ready"], [])
        self.assertTrue(legacy["_legacy_auto_ready_blocked"])

        legacy_allowed = normalize_selection_result(
            {"tickers": ["005930"]},
            candidates,
            "KR",
            allow_legacy_auto_ready=True,
        )
        self.assertEqual(legacy_allowed["trade_ready"], ["005930"])
        self.assertEqual(legacy_allowed["price_targets"], {})

        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            runtime = _runtime(bot, EventStore(Path(tmp) / "events.db"))
            self.assertEqual(runtime.register_from_selection_meta("KR", legacy_allowed), [])


if __name__ == "__main__":
    unittest.main()
