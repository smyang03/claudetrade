from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from trading_bot import TradingBot
from tools.analyze_kr_promotion_candidates import (
    Thresholds,
    analyze_kr_promotion_candidates,
)
from tools import preopen_scheduler


class _FakeAuditStore:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def update_execution_by_ticker(self, **kwargs):
        self.calls.append(kwargs)
        return 1


class _FakeHealthTracker:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record_strategy_cooldown(self, ticker, strategy, **kwargs):
        self.calls.append({"ticker": ticker, "strategy": strategy, **kwargs})
        return {"count": len(self.calls)}


class KrProfitabilityRequirementsTests(unittest.TestCase):
    def test_trade_ready_no_submit_records_one_share_cap_reason(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.is_paper = False
        bot.today_judgment = {"consensus": {"mode": "MILD_BULL"}}
        bot.selection_meta = {"KR": {"recommended_strategy": {"000660": "momentum"}}}
        bot.trade_ready_tickers = {"KR": ["000660"], "US": []}
        bot.risk = SimpleNamespace(cash=1_000_000)
        bot._current_session_date_str = lambda market: "2026-06-01"  # type: ignore[method-assign]
        bot._v2_decision_id_for_ticker = lambda market, ticker: "dec-000660"  # type: ignore[method-assign]
        lifecycle_events: list[dict] = []
        bot._v2_record_lifecycle_event = lambda event_type, market, ticker, **kw: lifecycle_events.append(  # type: ignore[method-assign]
            {"event_type": event_type, "market": market, "ticker": ticker, **kw}
        )
        audit_store = _FakeAuditStore()
        bot._candidate_audit_store = lambda: audit_store  # type: ignore[method-assign]

        payload = bot._record_trade_ready_no_submit(
            "KR",
            "000660",
            reason="one_share_over_budget_max_krw",
            final_action="BUY_READY",
            route="PlanA.buy",
            price_krw=2_394_000,
            fixed_order_krw=450_000,
            cash_krw=1_000_000,
            available_budget_krw=1_000_000,
            signal_flags=TradingBot._plan_a_signal_flags({}, signal_fired=True, strategy="momentum"),
            block_meta={"stage": "plan_a_affordability"},
        )

        self.assertEqual(payload["reason_code"], "ONE_SHARE_OVER_BUDGET_MAX_KRW")
        self.assertEqual(lifecycle_events[0]["event_type"], "TRADE_READY_NO_SUBMIT")
        self.assertEqual(lifecycle_events[0]["reason_code"], "ONE_SHARE_OVER_BUDGET_MAX_KRW")
        self.assertEqual(audit_store.calls[0]["values"]["no_submit_reason_code"], "ONE_SHARE_OVER_BUDGET_MAX_KRW")

    def test_trade_ready_no_signal_records_orp_strategy_cooldown(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.is_paper = False
        bot.today_judgment = {"consensus": {"mode": "MILD_BULL"}}
        bot.selection_meta = {"US": {"recommended_strategy": {"QCOM": "opening_range_pullback"}}}
        bot.trade_ready_tickers = {"KR": [], "US": ["QCOM"]}
        bot.risk = SimpleNamespace(cash=1_000_000)
        bot._current_session_date_str = lambda market: "2026-06-01"  # type: ignore[method-assign]
        bot._v2_decision_id_for_ticker = lambda market, ticker: "dec-qcom"  # type: ignore[method-assign]
        lifecycle_events: list[dict] = []
        bot._v2_record_lifecycle_event = lambda event_type, market, ticker, **kw: lifecycle_events.append(  # type: ignore[method-assign]
            {"event_type": event_type, "market": market, "ticker": ticker, **kw}
        )
        audit_store = _FakeAuditStore()
        tracker = _FakeHealthTracker()
        bot._candidate_audit_store = lambda: audit_store  # type: ignore[method-assign]
        bot._candidate_health_tracker = lambda market: tracker  # type: ignore[method-assign]

        payload = bot._record_trade_ready_no_submit(
            "US",
            "QCOM",
            reason="no_signal",
            reason_detail="OR pullback: reason=orp_entry_window_expired range=1.20% pullback=0.00% vol=1.10 elapsed=82m",
            final_action="BUY_READY",
            route="PlanA.buy",
            strategy_hint="opening_range_pullback",
            signal_flags=TradingBot._plan_a_signal_flags({}, signal_fired=False, strategy="opening_range_pullback"),
            block_meta={
                "stage": "plan_a_signal_check",
                "local_reason": "no_signal",
                "rejection_reason": "orp_entry_window_expired",
                "volume_state": "ok",
                "strategy_order": ["opening_range_pullback"],
            },
        )

        self.assertTrue(payload["block_meta"]["strategy_cooldown_recorded"])
        self.assertEqual(payload["block_meta"]["strategy_cooldown_reason"], "orp_entry_window_expired")
        self.assertTrue(payload["block_meta"]["strategy_cooldown_evidence_hash"])
        self.assertEqual(tracker.calls[0]["ticker"], "QCOM")
        self.assertEqual(tracker.calls[0]["strategy"], "opening_range_pullback")
        self.assertEqual(tracker.calls[0]["reason"], "orp_entry_window_expired")
        self.assertEqual(lifecycle_events[0]["payload"]["block_meta"]["strategy_cooldown_count"], 1)
        self.assertEqual(audit_store.calls[0]["values"]["no_submit_reason_code"], "NO_SIGNAL")

    def test_preopen_scheduler_lock_blocks_live_peer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)

            def fake_state_path(mode: str) -> Path:
                return base / f"{mode}_preopen_scheduler_state.json"

            with patch("tools.preopen_scheduler.scheduler_state_path", side_effect=fake_state_path):
                lock_path = preopen_scheduler._scheduler_lock_path("live")
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text('{"pid": 999999, "mode": "live"}', encoding="utf-8")
                with patch("tools.preopen_scheduler._pid_alive", return_value=True):
                    locked, path, detail = preopen_scheduler._acquire_scheduler_lock("live")

        self.assertFalse(locked)
        self.assertEqual(path.name, "preopen_scheduler.lock.json")
        self.assertIn("already running", detail)

    def test_promotion_report_separates_live_filled_micro_probe_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ml_db = base / "decisions.db"
            audit_db = base / "candidate_audit.db"
            ticker_db = base / "ticker_selection_log.db"
            state_dir = base / "state"
            state_dir.mkdir()
            self._make_empty_ml_db(ml_db)
            self._make_empty_audit_db(audit_db)
            self._make_micro_probe_db(ticker_db)

            payload = analyze_kr_promotion_candidates(
                market="KR",
                runtime_mode="live",
                ml_db=ml_db,
                audit_db=audit_db,
                ticker_db=ticker_db,
                state_dir=state_dir,
                thresholds=Thresholds(min_n=3, min_days=2, max_top_day_share=1.0),
            )

        section = payload["sections"]["micro_probe_live_filled"]
        names = {item["name"] for item in section["items"]}
        self.assertIn("micro_probe:bucket=preopen_ret60_probe", names)
        item = next(item for item in section["items"] if item["name"] == "micro_probe:bucket=preopen_ret60_probe")
        self.assertEqual(item["metrics"]["n"], 3)
        self.assertEqual(item["category"], "micro_probe_live_filled")

    def _make_empty_ml_db(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                """
                CREATE TABLE v2_learning_performance (
                    market TEXT, runtime_mode TEXT, session_date TEXT, ticker TEXT,
                    status TEXT, route TEXT, path_type TEXT, strategy TEXT,
                    origin_action TEXT, filled INTEGER, closed INTEGER, pnl_pct REAL,
                    mfe_pct REAL, mae_pct REAL, close_reason TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _make_empty_audit_db(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                """
                CREATE TABLE audit_candidate_rows (
                    candidate_key TEXT, runtime_mode TEXT, market TEXT, session_date TEXT,
                    known_at TEXT, ticker TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE audit_candidate_outcomes (
                    candidate_key TEXT, horizon_min INTEGER, return_pct REAL,
                    max_runup_pct REAL, max_drawdown_pct REAL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _make_micro_probe_db(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                """
                CREATE TABLE micro_probe_log (
                    bot_mode TEXT, session_date TEXT, market TEXT, ticker TEXT,
                    source_strategy TEXT, reason TEXT, experiment_bucket TEXT,
                    entry_source TEXT, exit_horizon_min INTEGER, pnl_pct REAL,
                    pnl_krw REAL, exit_reason TEXT
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO micro_probe_log (
                    bot_mode, session_date, market, ticker, source_strategy, reason,
                    experiment_bucket, entry_source, exit_horizon_min, pnl_pct,
                    pnl_krw, exit_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("live", "2026-05-01", "KR", "000001", "MICRO_PROBE", "probe", "preopen_ret60_probe", "preopen", 60, 0.2, 1000, "time_exit"),
                    ("live", "2026-05-02", "KR", "000002", "MICRO_PROBE", "probe", "preopen_ret60_probe", "preopen", 60, 0.3, 1000, "time_exit"),
                    ("live", "2026-05-02", "KR", "000003", "MICRO_PROBE", "probe", "preopen_ret60_probe", "preopen", 60, -0.1, -500, "time_exit"),
                ],
            )
            conn.commit()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
