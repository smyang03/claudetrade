from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import credit_tracker
import ticker_selection_db
from minority_report import raw_call_logger
from minority_report import hold_advisor


class RawCallLoggerTests(unittest.TestCase):
    def test_call_id_prevents_collision_and_can_update_same_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_call_logger._RAW_CALLS_DIR = Path(tmp)
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


if __name__ == "__main__":
    unittest.main()
