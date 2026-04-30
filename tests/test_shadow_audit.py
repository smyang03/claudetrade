from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from audit.shadow_audit_ids import make_episode_id, make_signal_id, minute_bucket
from audit.shadow_audit_models import ShadowAuditConfig
from audit.shadow_audit_store import ShadowAuditStore
from audit.shadow_audit_writer import ShadowAuditWriter, try_emit
from audit.shadow_outcome_updater import ShadowOutcomeUpdater
from tools.shadow_audit_gap import _group_counts
from trading_bot import TradingBot


class _CountingStore(ShadowAuditStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.write_calls = 0
        self.last_batch_size = 0

    def write_events(self, events) -> int:  # type: ignore[override]
        items = list(events or [])
        self.write_calls += 1
        self.last_batch_size = len(items)
        return super().write_events(items)


class _AuditEpisodeBot:
    _audit_episode_id = TradingBot._audit_episode_id
    _audit_episode_cache_key = TradingBot._audit_episode_cache_key
    _audit_active_episode = TradingBot._audit_active_episode
    _audit_close_active_episode = TradingBot._audit_close_active_episode

    def __init__(self, session_date: str = "2026-04-30") -> None:
        self._mode = "live"
        self.session_date = session_date
        self._shadow_audit_episode_ids: dict[str, str] = {}
        self.events: list[dict] = []

    def _audit_enabled(self) -> bool:
        return True

    def _current_session_date_str(self, market: str) -> str:
        return self.session_date

    def _audit_try_emit(self, event: dict) -> bool:
        self.events.append(dict(event or {}))
        return True


class _SignalCheckTracker:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def mark_signal_check(self, market: str, ticker: str, *, session_date: str, price) -> None:
        self.calls.append(
            {
                "market": market,
                "ticker": ticker,
                "session_date": session_date,
                "price": price,
            }
        )


class _SignalCheckBot:
    _entry_timing_signal_check = TradingBot._entry_timing_signal_check

    def __init__(self) -> None:
        self.entry_timing = _SignalCheckTracker()
        self.samples: list[dict] = []

    def _current_session_date_str(self, market: str) -> str:
        return "2026-04-30"

    def _audit_decision_id(self, market: str, ticker: str) -> str:
        return "dec_1"

    def _audit_emit_price_sample(self, market: str, ticker: str, **kwargs) -> None:
        self.samples.append({"market": market, "ticker": ticker, **kwargs})


class ShadowAuditTests(unittest.TestCase):
    def test_signal_and_episode_ids_are_stable_by_minute_bucket(self) -> None:
        first = make_signal_id(
            runtime_mode="live",
            market="KR",
            session_date="2026-04-30",
            ticker="024840",
            strategy="momentum",
            signal_at="2026-04-30T09:01:05+09:00",
            signal_price=5010.2,
            source="path_a",
        )
        same_bucket = make_signal_id(
            runtime_mode="live",
            market="KR",
            session_date="2026-04-30",
            ticker="024840",
            strategy="momentum",
            signal_at="2026-04-30T09:01:55+09:00",
            signal_price=5010.4,
            source="path_a",
        )
        next_bucket = make_signal_id(
            runtime_mode="live",
            market="KR",
            session_date="2026-04-30",
            ticker="024840",
            strategy="momentum",
            signal_at="2026-04-30T09:02:00+09:00",
            signal_price=5010.4,
            source="path_a",
        )

        self.assertEqual(minute_bucket("2026-04-30T09:01:55+09:00"), "20260430T0901")
        self.assertEqual(first, same_bucket)
        self.assertNotEqual(first, next_bucket)

        episode_market = make_episode_id(
            runtime_mode="live",
            market="KR",
            session_date="2026-04-30",
            episode_type="ORDER_UNKNOWN_PAUSE",
            scope="market",
            started_at="2026-04-30T09:00:10+09:00",
            reason="ORDER_UNKNOWN_UNRESOLVED",
        )
        self.assertTrue(episode_market.startswith("ep_"))

        ep_ticker_a = make_episode_id(
            runtime_mode="live",
            market="KR",
            session_date="2026-04-30",
            episode_type="ORDER_UNKNOWN_PAUSE",
            scope="ticker",
            ticker="024840",
            started_at="2026-04-30T09:00:10+09:00",
            reason="stale",
        )
        ep_ticker_b = make_episode_id(
            runtime_mode="live",
            market="KR",
            session_date="2026-04-30",
            episode_type="ORDER_UNKNOWN_PAUSE",
            scope="ticker",
            ticker="006340",
            started_at="2026-04-30T09:00:10+09:00",
            reason="stale",
        )
        self.assertNotEqual(ep_ticker_a, ep_ticker_b)

    def test_store_writes_isolated_audit_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "shadow.db"
            store = ShadowAuditStore(db)
            written = store.write_events(
                [
                    {
                        "kind": "signal",
                        "signal_id": "sig_1",
                        "decision_id": "dec_1",
                        "market": "KR",
                        "runtime_mode": "live",
                        "session_date": "2026-04-30",
                        "ticker": "024840",
                        "strategy": "momentum",
                        "signal_at": "2026-04-30T09:00:00+09:00",
                        "signal_at_bucket": "20260430T0900",
                        "signal_price": 100.0,
                        "decision": "signal_fired",
                    },
                    {
                        "kind": "signal_event",
                        "signal_id": "sig_1",
                        "event_type": "BLOCKED",
                        "occurred_at": "2026-04-30T09:00:01+09:00",
                        "reason_code": "ORDER_UNKNOWN_UNRESOLVED",
                    },
                    {
                        "kind": "episode",
                        "episode_id": "ep_1",
                        "episode_type": "ORDER_UNKNOWN_PAUSE",
                        "market": "KR",
                        "runtime_mode": "live",
                        "session_date": "2026-04-30",
                        "scope": "market",
                        "started_at": "2026-04-30T09:00:00+09:00",
                        "status": "open",
                    },
                    {"kind": "episode_link", "signal_id": "sig_1", "episode_id": "ep_1", "link_reason": "blocked"},
                    {
                        "kind": "price_sample",
                        "market": "KR",
                        "runtime_mode": "live",
                        "session_date": "2026-04-30",
                        "ticker": "024840",
                        "sampled_at": "2026-04-30T09:05:00+09:00",
                        "price": 105.0,
                        "signal_id": "sig_1",
                    },
                    {
                        "kind": "trade_link",
                        "signal_id": "sig_1",
                        "decision_id": "dec_1",
                        "order_no": "ord_1",
                        "entry_price": 100.0,
                    },
                    {"kind": "health", "event_type": "test", "queued": 1, "written": 1},
                ]
            )

            self.assertEqual(written, 7)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_signals").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_signal_events").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_episodes").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_signal_episode_links").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_price_samples").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_trade_links").fetchone()[0], 1)
            finally:
                conn.close()

    def test_writer_queue_full_drops_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            disabled_path = Path(tmp) / "disabled.db"
            disabled = ShadowAuditWriter(
                ShadowAuditConfig(
                    enabled=False,
                    runtime_mode="live",
                    db_path=disabled_path,
                    queue_max=1,
                    flush_batch=10,
                    db_timeout_sec=1.0,
                    drop_price_on_full=True,
                )
            )
            disabled.start()
            self.assertFalse(disabled.emit_nowait({"kind": "health", "event_type": "disabled"}))
            self.assertFalse(disabled_path.exists())

            config = ShadowAuditConfig(
                enabled=True,
                runtime_mode="live",
                db_path=Path(tmp) / "shadow.db",
                queue_max=1,
                flush_batch=10,
                db_timeout_sec=1.0,
                drop_price_on_full=True,
            )
            writer = ShadowAuditWriter(config)
            self.assertTrue(writer.emit_nowait({"kind": "health", "event_type": "one"}))
            self.assertFalse(writer.emit_nowait({"kind": "health", "event_type": "two"}))
            self.assertEqual(writer.dropped, 1)
            writer.flush()

            conn = sqlite3.connect(config.db_path)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_writer_health").fetchone()[0], 1)
            finally:
                conn.close()
            self.assertFalse(try_emit(None, {"kind": "health"}))

    def test_outcome_updater_computes_observed_and_missing_horizons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "shadow.db"
            store = ShadowAuditStore(db)
            store.write_events(
                [
                    {
                        "kind": "signal",
                        "signal_id": "sig_1",
                        "market": "KR",
                        "runtime_mode": "live",
                        "session_date": "2026-04-30",
                        "ticker": "024840",
                        "strategy": "momentum",
                        "signal_at": "2026-04-30T09:00:00+09:00",
                        "signal_at_bucket": "20260430T0900",
                        "signal_price": 100.0,
                    },
                    {
                        "kind": "price_sample",
                        "market": "KR",
                        "runtime_mode": "live",
                        "session_date": "2026-04-30",
                        "ticker": "024840",
                        "sampled_at": "2026-04-30T09:05:00+09:00",
                        "price": 105.0,
                        "signal_id": "sig_1",
                    },
                    {
                        "kind": "price_sample",
                        "market": "KR",
                        "runtime_mode": "live",
                        "session_date": "2026-04-30",
                        "ticker": "024840",
                        "sampled_at": "2026-04-30T09:30:00+09:00",
                        "price": 110.0,
                        "signal_id": "sig_1",
                    },
                ]
            )

            summary = ShadowOutcomeUpdater(db).update_pending(
                session_date="2026-04-30",
                market="KR",
                force_close=True,
            )

            self.assertEqual(summary["checked"], 1)
            self.assertEqual(summary["written"], 5)
            conn = sqlite3.connect(db)
            try:
                row_5 = conn.execute(
                    "SELECT status, return_pct, max_runup_pct, max_drawdown_pct FROM audit_signal_outcomes WHERE signal_id='sig_1' AND horizon_min=5"
                ).fetchone()
                row_60 = conn.execute(
                    "SELECT status FROM audit_signal_outcomes WHERE signal_id='sig_1' AND horizon_min=60"
                ).fetchone()
                row_close = conn.execute(
                    "SELECT status, observed_price FROM audit_signal_outcomes WHERE signal_id='sig_1' AND horizon_min=-1"
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(row_5[0], "computed")
            self.assertAlmostEqual(row_5[1], 5.0)
            self.assertAlmostEqual(row_5[2], 5.0)
            self.assertAlmostEqual(row_5[3], 0.0)
            self.assertEqual(row_60[0], "missing_price")
            self.assertEqual(row_close[0], "computed")
            self.assertEqual(row_close[1], 110.0)

    def test_outcome_close_requires_post_signal_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "shadow.db"
            ShadowAuditStore(db).write_events(
                [
                    {
                        "kind": "signal",
                        "signal_id": "sig_1",
                        "market": "KR",
                        "runtime_mode": "live",
                        "session_date": "2026-04-30",
                        "ticker": "024840",
                        "signal_at": "2026-04-30T09:00:00+09:00",
                        "signal_price": 100.0,
                    },
                    {
                        "kind": "price_sample",
                        "market": "KR",
                        "runtime_mode": "live",
                        "session_date": "2026-04-30",
                        "ticker": "024840",
                        "sampled_at": "2026-04-30T09:00:00+09:00",
                        "price": 100.0,
                        "signal_id": "sig_1",
                    },
                ]
            )

            summary = ShadowOutcomeUpdater(db).update_pending(
                session_date="2026-04-30",
                market="KR",
                force_close=True,
            )

            self.assertEqual(summary["written"], 5)
            conn = sqlite3.connect(db)
            try:
                row_close = conn.execute(
                    "SELECT status, observed_price, return_pct FROM audit_signal_outcomes WHERE signal_id='sig_1' AND horizon_min=-1"
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row_close[0], "missing_price")
            self.assertIsNone(row_close[1])
            self.assertIsNone(row_close[2])

    def test_outcome_updater_batches_writes_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "shadow.db"
            ShadowAuditStore(db).write_events(
                [
                    {
                        "kind": "signal",
                        "signal_id": "sig_1",
                        "market": "KR",
                        "runtime_mode": "live",
                        "session_date": "2026-04-30",
                        "ticker": "024840",
                        "signal_at": "2026-04-30T09:00:00+09:00",
                        "signal_price": 100.0,
                    },
                    {
                        "kind": "signal",
                        "signal_id": "sig_2",
                        "market": "KR",
                        "runtime_mode": "live",
                        "session_date": "2026-04-30",
                        "ticker": "006340",
                        "signal_at": "2026-04-30T09:00:00+09:00",
                        "signal_price": 200.0,
                    },
                ]
            )
            updater = ShadowOutcomeUpdater(db)
            counting = _CountingStore(db)
            updater.store = counting

            summary = updater.update_pending(session_date="2026-04-30", market="KR", force_close=True)

            self.assertEqual(summary["checked"], 2)
            self.assertEqual(summary["written"], 10)
            self.assertEqual(counting.write_calls, 1)
            self.assertEqual(counting.last_batch_size, 10)

    def test_active_order_unknown_pause_can_be_closed_from_cache(self) -> None:
        bot = _AuditEpisodeBot()
        episode_id = bot._audit_active_episode(
            "KR",
            episode_type="ORDER_UNKNOWN_PAUSE",
            scope="market",
            reason="ORDER_UNKNOWN_UNRESOLVED",
            payload={"stage": "blocked"},
        )

        closed_id = bot._audit_close_active_episode(
            "KR",
            episode_type="ORDER_UNKNOWN_PAUSE",
            scope="market",
            reason="ORDER_UNKNOWN_UNRESOLVED",
            clear_reason="session_open_auto_reconcile",
            payload={"checked": 1},
        )
        skipped_id = bot._audit_close_active_episode(
            "KR",
            episode_type="ORDER_UNKNOWN_PAUSE",
            scope="market",
            reason="ORDER_UNKNOWN_UNRESOLVED",
            clear_reason="session_open_auto_reconcile",
        )
        restarted = _AuditEpisodeBot()
        restart_skip = restarted._audit_close_active_episode(
            "KR",
            episode_type="ORDER_UNKNOWN_PAUSE",
            scope="market",
            reason="ORDER_UNKNOWN_UNRESOLVED",
            clear_reason="session_open_auto_reconcile",
        )

        self.assertTrue(episode_id)
        self.assertEqual(closed_id, episode_id)
        self.assertEqual(skipped_id, "")
        self.assertEqual(restart_skip, "")
        self.assertEqual(len(bot.events), 2)
        self.assertEqual(bot.events[0]["status"], "open")
        self.assertEqual(bot.events[1]["episode_id"], episode_id)
        self.assertEqual(bot.events[1]["status"], "cleared")
        self.assertEqual(bot.events[1]["clear_reason"], "session_open_auto_reconcile")

    def test_episode_cache_is_separated_by_session_date(self) -> None:
        bot = _AuditEpisodeBot(session_date="2026-04-30")
        first = bot._audit_active_episode(
            "KR",
            episode_type="ORDER_UNKNOWN_PAUSE",
            scope="market",
            reason="ORDER_UNKNOWN_UNRESOLVED",
        )
        bot.session_date = "2026-05-01"
        second = bot._audit_active_episode(
            "KR",
            episode_type="ORDER_UNKNOWN_PAUSE",
            scope="market",
            reason="ORDER_UNKNOWN_UNRESOLVED",
        )

        self.assertNotEqual(first, second)
        self.assertEqual(len(bot._shadow_audit_episode_ids), 2)

    def test_path_a_signal_check_emits_passive_price_sample(self) -> None:
        bot = _SignalCheckBot()
        bot._entry_timing_signal_check("KR", "024840", 123.0)

        self.assertEqual(bot.entry_timing.calls[0]["price"], 123.0)
        self.assertEqual(len(bot.samples), 1)
        self.assertEqual(bot.samples[0]["market"], "KR")
        self.assertEqual(bot.samples[0]["ticker"], "024840")
        self.assertEqual(bot.samples[0]["price"], 123.0)
        self.assertEqual(bot.samples[0]["source"], "entry_timing:signal_check")
        self.assertEqual(bot.samples[0]["decision_id"], "dec_1")

    def test_gap_report_group_counts_supports_select_aliases(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("CREATE TABLE events (market TEXT, event_type TEXT, reason_code TEXT)")
            conn.executemany(
                "INSERT INTO events VALUES (?, ?, ?)",
                [("KR", "BLOCKED", "A"), ("KR", "BLOCKED", "A"), ("KR", "BLOCKED", None)],
            )
            rows = _group_counts(
                conn,
                "events",
                ["market", "event_type", "COALESCE(reason_code, '') AS reason_code"],
            )
        finally:
            conn.close()

        counts = {(row["market"], row["event_type"], row["reason_code"]): row["rows"] for row in rows}
        self.assertEqual(counts[("KR", "BLOCKED", "A")], 2)
        self.assertEqual(counts[("KR", "BLOCKED", "")], 1)


if __name__ == "__main__":
    unittest.main()
