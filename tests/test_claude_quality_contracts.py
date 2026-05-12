from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import credit_tracker
import ticker_selection_db
from audit.agent_call_event_store import AgentCallEventStore
from minority_report import raw_call_logger
from minority_report import hold_advisor
from tools.clean_agent_call_event_store import run as clean_agent_call_events


class RawCallLoggerTests(unittest.TestCase):
    def tearDown(self) -> None:
        raw_call_logger._RAW_CALLS_DIR = None
        raw_call_logger._AGENT_EVENT_STORE = None

    def test_call_id_prevents_collision_and_can_update_same_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_call_logger._RAW_CALLS_DIR = Path(tmp)
            with patch.dict("os.environ", {"ENABLE_AGENT_CALL_EVENT_STORE": "false"}, clear=False):
                first = raw_call_logger.save(
                    label="same_label",
                    prompt="p1",
                    raw_response="raw1",
                    parsed={},
                    input_tokens=1,
                    output_tokens=2,
                    market="KR",
                    model="claude-haiku-test",
                )
                second = raw_call_logger.save(
                    label="same_label",
                    prompt="p2",
                    raw_response="raw2",
                    parsed={},
                    input_tokens=3,
                    output_tokens=4,
                    market="KR",
                    model="claude-sonnet-test",
                )

                self.assertIsNotNone(first)
                self.assertIsNotNone(second)
                self.assertNotEqual(first, second)
                self.assertEqual(len(list(Path(tmp).glob("*.json"))), 2)

                updated = raw_call_logger.save(
                    label="same_label",
                    prompt="p3",
                    raw_response="raw3",
                    parsed={"ok": True},
                    input_tokens=5,
                    output_tokens=6,
                    market="KR",
                    model="claude-sonnet-test",
                    call_id="stable",
                    parse_error=False,
                    parse_stage="parsed",
                )
                updated_again = raw_call_logger.save(
                    label="same_label",
                    prompt="p4",
                    raw_response="raw4",
                    parsed={"ok": False},
                    input_tokens=7,
                    output_tokens=8,
                    market="KR",
                    model="claude-sonnet-test",
                    call_id="stable",
                    parse_error=True,
                    parse_stage="parse_failed",
                )

            self.assertEqual(updated, updated_again)
            data = json.loads(Path(updated_again).read_text(encoding="utf-8"))
            self.assertEqual(data["call_id"], "stable")
            self.assertTrue(data["parse_error"])
            self.assertEqual(data["parse_stage"], "parse_failed")

    def test_pytest_guard_blocks_default_agent_event_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_call_logger._RAW_CALLS_DIR = root / "raw"
            raw_call_logger._AGENT_EVENT_STORE = None
            default_db = root / "data" / "audit" / "agent_call_events.db"

            def fake_runtime_path(*parts, make_parents=False):
                path = root.joinpath(*parts)
                if make_parents:
                    path.mkdir(parents=True, exist_ok=True)
                return path

            with patch.object(raw_call_logger, "get_runtime_path", side_effect=fake_runtime_path), \
                 patch.dict(
                     "os.environ",
                     {
                         "ENABLE_AGENT_CALL_EVENT_STORE": "true",
                         "PYTEST_CURRENT_TEST": "tests/test_raw.py::case (call)",
                     },
                     clear=False,
                 ):
                os.environ.pop("AGENT_CALL_EVENT_DB_PATH", None)
                path = raw_call_logger.save(
                    label="same_label",
                    prompt="prompt",
                    raw_response="raw",
                    parsed={},
                    input_tokens=1,
                    output_tokens=1,
                    market="KR",
                    model="claude-sonnet-test",
                )

            self.assertIsNotNone(path)
            self.assertFalse(default_db.exists())

    def test_raw_call_can_write_agent_event_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_call_logger._RAW_CALLS_DIR = Path(tmp) / "raw"
            raw_call_logger._AGENT_EVENT_STORE = None
            db_path = Path(tmp) / "agent_call_events.db"
            with patch.dict(
                "os.environ",
                {
                    "ENABLE_AGENT_CALL_EVENT_STORE": "true",
                    "AGENT_CALL_EVENT_DB_PATH": str(db_path),
                },
                clear=False,
            ):
                path = raw_call_logger.save(
                    label="select_tickers",
                    prompt="prompt",
                    raw_response='{"ok": true}',
                    parsed={"ok": True},
                    input_tokens=11,
                    output_tokens=7,
                    market="US",
                    model="claude-test",
                    call_id="call_1",
                    parse_error=False,
                    parse_stage="parsed",
                    prompt_version="v2",
                )

            self.assertIsNotNone(path)
            store = AgentCallEventStore(db_path)
            event = store.event("call_1")
            self.assertIsNotNone(event)
            self.assertEqual(event["market"], "US")
            self.assertEqual(event["label"], "select_tickers")
            self.assertEqual(event["input_tokens"], 11)


class AgentCallEventCleanupTests(unittest.TestCase):
    def test_cleanup_dry_run_then_apply_removes_test_call_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "agent_call_events.db"
            store = AgentCallEventStore(db_path)
            store.upsert_event(
                {
                    "call_id": "test_call",
                    "label": "same_label",
                    "market": "KR",
                    "call_date": "2026-05-12",
                    "known_at": "2026-05-12T01:00:00",
                    "model": "claude-sonnet-test",
                    "prompt_hash": "p1",
                    "response_hash": "r1",
                    "config_hash": "c1",
                    "raw_call_path": r"C:\Users\Public\Documents\ESTsoft\CreatorTemp\tmp123.json",
                    "parsed": {"ok": True},
                }
            )
            store.upsert_event(
                {
                    "call_id": "live_call",
                    "label": "select_tickers",
                    "market": "KR",
                    "call_date": "2026-05-12",
                    "known_at": "2026-05-12T09:00:00",
                    "model": "claude-sonnet-4-6",
                    "prompt_hash": "p2",
                    "response_hash": "r2",
                    "config_hash": "c2",
                    "raw_call_path": r"E:\code\claudetrade\logs\raw_calls\live.json",
                    "parsed": {"ok": True},
                }
            )

            dry_run = clean_agent_call_events(db_path=db_path, apply=False)
            self.assertEqual(dry_run["matched_event_count"], 1)
            self.assertEqual(dry_run["deleted_event_count"], 0)
            self.assertIsNotNone(store.event("test_call"))

            applied = clean_agent_call_events(db_path=db_path, apply=True)
            self.assertEqual(applied["deleted_event_count"], 1)
            self.assertTrue(Path(applied["backup_path"]).exists())
            self.assertIsNone(store.event("test_call"))
            self.assertIsNotNone(store.event("live_call"))


class CreditTrackerTests(unittest.TestCase):
    def test_record_preserves_legacy_totals_and_adds_model_buckets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            usage_path = Path(tmp) / "usage.json"
            with patch.object(credit_tracker, "USAGE_PATH", usage_path):
                credit_tracker.record(1_000_000, 1_000_000, "r1", model="claude-haiku-test")
                data = json.loads(usage_path.read_text(encoding="utf-8"))

            self.assertIn("total", data)
            self.assertIn("daily", data)
            self.assertIn("sessions", data)
            self.assertIn("by_model", data)
            self.assertIn("claude-haiku-test", data["by_model"])
            self.assertEqual(data["total"]["input_tokens"], 1_000_000)
            self.assertEqual(data["sessions"][-1]["model"], "claude-haiku-test")


class HoldAdvisorTests(unittest.TestCase):
    def test_ask_one_failure_returns_hold_fallback(self) -> None:
        pos = {"ticker": "TEST", "entry": 100.0, "current_price": 110.0, "tp": 105.0}
        with patch.object(hold_advisor.client.messages, "create", side_effect=RuntimeError("boom")):
            vote = hold_advisor._ask_one("bull", pos, "KR", "", "")

        self.assertEqual(vote["action"], "HOLD")
        self.assertEqual(vote["confidence"], 0.0)
        self.assertEqual(vote["trail_pct"], 0.03)
        self.assertTrue(vote["fallback"])

    def test_all_failed_votes_cannot_sell(self) -> None:
        pos = {"ticker": "TEST", "entry": 100.0, "current_price": 110.0, "tp": 105.0}
        with patch.object(hold_advisor, "_ask_one", return_value=hold_advisor._fallback_vote("error")):
            result = hold_advisor.ask(pos, "KR", delay=0)

        self.assertEqual(result["action"], "HOLD")
        self.assertEqual(result["trail_pct"], 0.03)


class TickerSelectionDbTests(unittest.TestCase):
    def test_connection_uses_timeout_and_existing_schema_still_initializes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "ticker_selection_log.db")
            with patch.object(ticker_selection_db, "DB_PATH", db_path):
                ticker_selection_db.init()
                with ticker_selection_db._conn() as conn:
                    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
                    tables = {
                        row[0]
                        for row in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }

            self.assertEqual(timeout, 10000)
            self.assertIn("ticker_selection_log", tables)

    def test_watch_only_preopen_row_is_not_marked_traded_without_execution_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "ticker_selection_log.db")
            with patch.object(ticker_selection_db, "DB_PATH", db_path):
                ticker_selection_db.init()
                row_ids = ticker_selection_db.insert_batch(
                    "2026-05-12",
                    "KR",
                    "preopen_watch",
                    ["005930"],
                    [{"ticker": "005930", "change_pct": 1.2}],
                    {"005930": "watch only"},
                    "preopen",
                    selection_meta={"trade_ready": []},
                )
                original_id = row_ids["005930"]
                updated = ticker_selection_db.update_traded(
                    original_id,
                    "2026-05-12T09:01:00+09:00",
                    execution_source_type="signal_entry",
                    execution_strategy="momentum",
                )
                execution_id = ticker_selection_db.insert_execution_row_from_selection(
                    original_id,
                    "2026-05-12T09:01:00+09:00",
                    execution_source_type="signal_entry",
                    execution_decision_id="decision_1",
                    execution_strategy="momentum",
                    execution_reason="order_sent",
                )
                with ticker_selection_db._conn() as conn:
                    rows = conn.execute(
                        """
                        SELECT id, source_type, trade_ready, traded, execution_source_type,
                               execution_decision_id, execution_strategy
                        FROM ticker_selection_log
                        ORDER BY id
                        """
                    ).fetchall()

            self.assertFalse(updated)
            self.assertNotEqual(execution_id, original_id)
            self.assertEqual(rows[0][1], "preopen_watch")
            self.assertEqual(rows[0][2], 0)
            self.assertEqual(rows[0][3], 0)
            self.assertEqual(rows[1][1], "signal_entry")
            self.assertEqual(rows[1][2], 1)
            self.assertEqual(rows[1][3], 1)
            self.assertEqual(rows[1][4], "signal_entry")
            self.assertEqual(rows[1][5], "decision_1")
            self.assertEqual(rows[1][6], "momentum")


if __name__ == "__main__":
    unittest.main()
