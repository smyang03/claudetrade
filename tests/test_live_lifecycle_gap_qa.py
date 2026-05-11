from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.live_lifecycle_gap_qa import find_lifecycle_gaps


class LiveLifecycleGapQATests(unittest.TestCase):
    def test_detects_entry_timing_order_missing_from_v2_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry_dir = root / "logs" / "entry_timing"
            entry_dir.mkdir(parents=True)
            (entry_dir / "live_20260511_KR.jsonl").write_text(
                json.dumps(
                    {
                        "event": "order_sent",
                        "market": "KR",
                        "ticker": "012610",
                        "occurred_at": "2026-05-11T12:37:17+09:00",
                        "state": {"order_no": "0029831000", "ticker": "012610"},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            db_dir = root / "data"
            db_dir.mkdir(parents=True)
            conn = sqlite3.connect(db_dir / "v2_event_store.db")
            try:
                conn.execute(
                    """
                    CREATE TABLE lifecycle_events (
                        event_type TEXT,
                        market TEXT,
                        runtime_mode TEXT,
                        session_date TEXT,
                        ticker TEXT,
                        execution_id TEXT,
                        occurred_at TEXT,
                        payload_json TEXT
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            report = find_lifecycle_gaps(root=root, session_date="2026-05-11", market="KR")

        self.assertEqual(report["gap_count"], 1)
        self.assertEqual(report["gaps"][0]["ticker"], "012610")
        self.assertEqual(report["gaps"][0]["event_type"], "ORDER_SENT")

    def test_matching_lifecycle_event_has_no_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry_dir = root / "logs" / "entry_timing"
            entry_dir.mkdir(parents=True)
            (entry_dir / "live_20260511_US.jsonl").write_text(
                json.dumps(
                    {
                        "event": "filled",
                        "market": "US",
                        "ticker": "nvda",
                        "occurred_at": "2026-05-11T23:00:00+09:00",
                        "state": {"fill_order_no": "ABC123", "ticker": "NVDA"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            db_dir = root / "data"
            db_dir.mkdir(parents=True)
            conn = sqlite3.connect(db_dir / "v2_event_store.db")
            try:
                conn.execute(
                    """
                    CREATE TABLE lifecycle_events (
                        event_type TEXT,
                        market TEXT,
                        runtime_mode TEXT,
                        session_date TEXT,
                        ticker TEXT,
                        execution_id TEXT,
                        occurred_at TEXT,
                        payload_json TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO lifecycle_events
                    (event_type, market, runtime_mode, session_date, ticker, execution_id, occurred_at, payload_json)
                    VALUES ('FILLED', 'US', 'live', '2026-05-11', 'NVDA', 'ABC123', '2026-05-11T14:00:00+00:00', '{}')
                    """
                )
                conn.commit()
            finally:
                conn.close()

            report = find_lifecycle_gaps(root=root, session_date="2026-05-11", market="US")

        self.assertEqual(report["gap_count"], 0)

    def test_matches_lifecycle_event_by_payload_broker_order_no_before_execution_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry_dir = root / "logs" / "entry_timing"
            entry_dir.mkdir(parents=True)
            (entry_dir / "live_20260511_KR.jsonl").write_text(
                json.dumps(
                    {
                        "event": "order_sent",
                        "market": "KR",
                        "ticker": "012610",
                        "occurred_at": "2026-05-11T12:37:17+09:00",
                        "state": {"order_no": "0029831000", "ticker": "012610"},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            db_dir = root / "data"
            db_dir.mkdir(parents=True)
            conn = sqlite3.connect(db_dir / "v2_event_store.db")
            try:
                conn.execute(
                    """
                    CREATE TABLE lifecycle_events (
                        event_type TEXT,
                        market TEXT,
                        runtime_mode TEXT,
                        session_date TEXT,
                        ticker TEXT,
                        execution_id TEXT,
                        occurred_at TEXT,
                        payload_json TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO lifecycle_events
                    (event_type, market, runtime_mode, session_date, ticker, execution_id, occurred_at, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "ORDER_SENT",
                        "KR",
                        "live",
                        "2026-05-11",
                        "012610",
                        "exec_internal_012610_1",
                        "2026-05-11T03:37:17+00:00",
                        json.dumps({"order_no": "0029831000"}, ensure_ascii=False),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            report = find_lifecycle_gaps(root=root, session_date="2026-05-11", market="KR")

        self.assertEqual(report["gap_count"], 0)


if __name__ == "__main__":
    unittest.main()
