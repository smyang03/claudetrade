from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile

from decision.claude_price_plan import make_price_plan
from execution.claude_price_adapter import ClaudePriceAdapter
from lifecycle.event_store import EventStore
from trading_bot import TradingBot


class _Risk:
    def __init__(self, positions: list[dict]) -> None:
        self.positions = positions
        self.cash = 1_000_000.0
        self.total_fee = 0.0
        self.daily_pnl = 0.0


def _pathb_el_position(**overrides) -> dict:
    pos = {
        "ticker": "EL",
        "market": "US",
        "qty": 1,
        "entry": 87_800.0,
        "display_avg_price": 87.8,
        "display_currency": "USD",
        "price_source": "order_fill",
        "strategy": "claude_price",
        "source_strategy": "claude_price",
        "path_type": "claude_price",
        "pathb_path_run_id": "path_20260526_US_EL_claude_price_1f8be6e8",
        "v2_decision_id": "dec_el",
        "order_no": "0030285341",
    }
    pos.update(overrides)
    return pos


def _tsla_position() -> dict:
    return {
        "ticker": "TSLA",
        "market": "US",
        "qty": 1,
        "entry": 360_000.0,
        "display_avg_price": 360.0,
        "display_currency": "USD",
        "price_source": "order_fill",
        "strategy": "claude_price",
        "path_type": "claude_price",
        "pathb_path_run_id": "path_tsla",
    }


def _bot(positions: list[dict], *, broker_truth: dict | None = None, pathb: object | None = None) -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.usd_krw_rate = 1000.0
    bot.is_paper = False
    bot.pending_orders = []
    bot._broker_state = {}
    bot._execution_flags = {"KR": set(), "US": set()}
    bot._session_closed_tickers = {"KR": set(), "US": set()}
    bot.risk = _Risk(positions)
    bot.pathb = pathb
    bot.saved_positions = 0
    bot.live_status_writes = 0

    bot._market_enabled = lambda market: str(market).upper() == "US"  # type: ignore[method-assign]
    bot._token_for_market = lambda market, force_refresh=False: "token"  # type: ignore[method-assign]
    bot._current_session_date_str = lambda market: "2026-05-28"  # type: ignore[method-assign]
    bot._reconcile_pending_orders = lambda broker_kr, broker_us: None  # type: ignore[method-assign]
    bot._lookup_ticker_name = lambda ticker, market: ticker  # type: ignore[method-assign]
    bot._save_positions = lambda: setattr(bot, "saved_positions", bot.saved_positions + 1)  # type: ignore[method-assign]
    bot._write_live_status = lambda market, force=False: setattr(bot, "live_status_writes", bot.live_status_writes + 1)  # type: ignore[method-assign]
    bot._flag_execution_issue = Mock(wraps=TradingBot._flag_execution_issue.__get__(bot, TradingBot))  # type: ignore[method-assign]
    if broker_truth is not None:
        bot._broker_truth_market_snapshot = lambda market, force=True, ttl_sec=15: broker_truth  # type: ignore[method-assign]
    return bot


def _balance(*stocks: dict) -> dict:
    return {"cash": 1000.0, "asset_cash": 1000.0, "stocks": list(stocks)}


def _stock(ticker: str, qty: int, avg: float, current: float | None = None) -> dict:
    return {
        "ticker": ticker,
        "name": ticker,
        "qty": qty,
        "avg_price": avg,
        "eval_price": current if current is not None else avg,
    }


def test_partial_broker_omission_keeps_pathb_metadata_protected() -> None:
    truth = {
        "missing": False,
        "stale": False,
        "error": "",
        "last_success_at": "2026-05-28T01:00:00+09:00",
        "positions": [_stock("TSLA", 1, 360.0)],
        "open_orders": [],
        "today_fills": [],
    }
    bot = _bot([_pathb_el_position(), _tsla_position()], broker_truth=truth)

    with tempfile.TemporaryDirectory() as tmp, patch("trading_bot.get_runtime_path", return_value=Path(tmp) / "live_open_positions.json"), patch(
        "trading_bot.get_balance",
        return_value=_balance(_stock("TSLA", 1, 360.0)),
    ):
        TradingBot._sync_runtime_with_broker(bot)

    by_ticker = {pos["ticker"]: pos for pos in bot.risk.positions}
    assert set(by_ticker) == {"EL", "TSLA"}
    el = by_ticker["EL"]
    assert el["path_type"] == "claude_price"
    assert el["pathb_path_run_id"] == "path_20260526_US_EL_claude_price_1f8be6e8"
    assert el["broker_reconcile_status"] == "broker_missing_unconfirmed"
    assert el["position_integrity"] == "protected"
    assert el["management_protected"] is True
    assert el["broker_missing_seen_count"] == 1
    assert ("US", "broker_position_removed") not in [call.args for call in bot._flag_execution_issue.call_args_list]


def test_second_independent_zero_holding_snapshot_removes_missing_position() -> None:
    truth = {
        "missing": False,
        "stale": False,
        "error": "",
        "last_success_at": "2026-05-28T01:05:00+09:00",
        "positions": [_stock("TSLA", 1, 360.0)],
        "open_orders": [],
        "today_fills": [],
    }
    bot = _bot(
        [
            _pathb_el_position(
                broker_reconcile_status="broker_missing_unconfirmed",
                broker_missing_seen_count=1,
                broker_missing_last_success_at="2026-05-28T01:00:00+09:00",
            ),
            _tsla_position(),
        ],
        broker_truth=truth,
    )

    with tempfile.TemporaryDirectory() as tmp, patch("trading_bot.get_runtime_path", return_value=Path(tmp) / "live_open_positions.json"), patch(
        "trading_bot.get_balance",
        return_value=_balance(_stock("TSLA", 1, 360.0)),
    ):
        TradingBot._sync_runtime_with_broker(bot)

    assert [pos["ticker"] for pos in bot.risk.positions] == ["TSLA"]
    assert ("US", "broker_position_removed") in [call.args for call in bot._flag_execution_issue.call_args_list]


def test_broker_injection_recovers_unique_pathb_metadata_from_event_store() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(Path(tmp) / "events.db")
        adapter = ClaudePriceAdapter(store=store)
        plan = make_price_plan(
            decision_id="dec_el",
            ticker="EL",
            market="US",
            session_date="2026-05-28",
            buy_zone_low=85.5,
            buy_zone_high=91.0,
            sell_target=95.0,
            stop_loss=84.0,
            hold_days=1,
            confidence=0.7,
        )
        adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain")
        adapter.mark_filled(
            plan.path_run_id,
            price=87.8,
            qty=1,
            execution_id="0030285341",
            runtime_mode="live",
            brain_snapshot_id="brain",
        )
        bot = _bot([], pathb=SimpleNamespace(store=store, broker_truth=None))

        with patch("trading_bot.get_runtime_path", return_value=Path(tmp) / "live_open_positions.json"), patch(
            "trading_bot.get_balance",
            return_value=_balance(_stock("EL", 1, 87.8, 88.1)),
        ):
            TradingBot._sync_runtime_with_broker(bot)

    assert len(bot.risk.positions) == 1
    pos = bot.risk.positions[0]
    assert pos["ticker"] == "EL"
    assert pos["strategy"] == "claude_price"
    assert pos["path_type"] == "claude_price"
    assert pos["pathb_path_run_id"] == plan.path_run_id
    assert pos["v2_decision_id"] == "dec_el"
    assert pos["broker_reconcile_status"] == "pathb_metadata_recovered_from_event_store"
    assert pos["position_integrity"] == "trusted"
    assert pos["management_protected"] is False
