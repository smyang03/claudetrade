from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from strategy import adaptive_params as adaptive


class AdaptiveParamsCanonicalTests(unittest.TestCase):
    def _create_decisions_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE decisions (
                market TEXT,
                ticker TEXT,
                session_date TEXT,
                decision TEXT,
                strategy_used TEXT,
                pnl_pct REAL,
                forward_1d REAL,
                data_source TEXT
            )
            """
        )

    def _insert_decision(
        self,
        conn: sqlite3.Connection,
        *,
        ticker: str,
        pnl_pct: float,
        data_source: str = "live",
    ) -> None:
        conn.execute(
            """
            INSERT INTO decisions (
                market, ticker, session_date, decision, strategy_used,
                pnl_pct, data_source
            ) VALUES ('US', ?, '2026-05-20', 'BUY_SIGNAL', 'momentum', ?, ?)
            """,
            (ticker, pnl_pct, data_source),
        )

    def _create_canonical_schema(self, conn: sqlite3.Connection, *, include_learning_allowed: bool = True) -> None:
        learning_allowed_col = ", learning_allowed INTEGER" if include_learning_allowed else ""
        conn.execute(
            f"""
            CREATE TABLE v2_canonical_performance (
                market TEXT,
                runtime_mode TEXT,
                session_date TEXT,
                strategy TEXT,
                path_type TEXT,
                route TEXT,
                closed INTEGER,
                pnl_pct REAL{learning_allowed_col}
            )
            """
        )

    def _insert_canonical(
        self,
        conn: sqlite3.Connection,
        *,
        closed: int,
        pnl_pct: float,
        learning_allowed: int | None = 1,
    ) -> None:
        if learning_allowed is None:
            conn.execute(
                """
                INSERT INTO v2_canonical_performance (
                    market, runtime_mode, session_date, strategy, path_type,
                    route, closed, pnl_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("US", "live", "2026-05-20", "momentum", "path_a", "plan_a", closed, pnl_pct),
            )
            return
        conn.execute(
            """
            INSERT INTO v2_canonical_performance (
                market, runtime_mode, session_date, strategy, path_type,
                route, closed, pnl_pct, learning_allowed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "US",
                "live",
                "2026-05-20",
                "momentum",
                "path_a",
                "plan_a",
                closed,
                pnl_pct,
                learning_allowed,
            ),
        )

    def test_perf_stats_prefers_v2_canonical_closed_live_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "decisions.db"
            conn = sqlite3.connect(db_path)
            try:
                self._create_decisions_schema(conn)
                self._insert_decision(conn, ticker="LEGACY", pnl_pct=10.0)
                self._create_canonical_schema(conn)
                self._insert_canonical(conn, closed=1, pnl_pct=2.0, learning_allowed=1)
                self._insert_canonical(conn, closed=1, pnl_pct=-1.0, learning_allowed=1)
                self._insert_canonical(conn, closed=0, pnl_pct=30.0, learning_allowed=1)
                conn.commit()
            finally:
                conn.close()

            with patch.object(adaptive, "_DB", db_path):
                stats = adaptive.get_perf_stats("momentum", "US", days=9999)

        self.assertEqual(stats["win_rate"], 50.0)
        self.assertEqual(stats["n"], 2)
        self.assertEqual(stats["source"], "v2_canonical_small")

    def test_perf_stats_excludes_non_learning_canonical_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "decisions.db"
            conn = sqlite3.connect(db_path)
            try:
                self._create_decisions_schema(conn)
                self._create_canonical_schema(conn)
                self._insert_canonical(conn, closed=1, pnl_pct=2.0, learning_allowed=1)
                self._insert_canonical(conn, closed=1, pnl_pct=-1.0, learning_allowed=1)
                self._insert_canonical(conn, closed=1, pnl_pct=99.0, learning_allowed=0)
                conn.commit()
            finally:
                conn.close()

            with patch.object(adaptive, "_DB", db_path):
                stats = adaptive.get_perf_stats("momentum", "US", days=9999)

        self.assertEqual(stats["win_rate"], 50.0)
        self.assertEqual(stats["n"], 2)
        self.assertEqual(stats["source"], "v2_canonical_small")

    def test_all_filtered_canonical_rows_do_not_fallback_to_legacy_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "decisions.db"
            conn = sqlite3.connect(db_path)
            try:
                self._create_decisions_schema(conn)
                self._insert_decision(conn, ticker="LEGACY_LIVE", pnl_pct=10.0, data_source="live")
                self._create_canonical_schema(conn)
                self._insert_canonical(conn, closed=1, pnl_pct=99.0, learning_allowed=0)
                conn.commit()
            finally:
                conn.close()

            with patch.object(adaptive, "_DB", db_path):
                stats = adaptive.get_perf_stats("momentum", "US", days=9999)

        self.assertIsNone(stats["win_rate"])
        self.assertEqual(stats["n"], 0)
        self.assertEqual(stats["source"], "v2_canonical_filtered")

    def test_all_filtered_canonical_rows_can_fallback_to_backfill_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "decisions.db"
            conn = sqlite3.connect(db_path)
            try:
                self._create_decisions_schema(conn)
                self._insert_decision(conn, ticker="LEGACY_LIVE", pnl_pct=10.0, data_source="live")
                self._insert_decision(conn, ticker="BACKFILL_WIN", pnl_pct=3.0, data_source="backfill")
                self._insert_decision(conn, ticker="BACKFILL_LOSS", pnl_pct=-2.0, data_source="backfill")
                self._create_canonical_schema(conn)
                self._insert_canonical(conn, closed=1, pnl_pct=99.0, learning_allowed=0)
                conn.commit()
            finally:
                conn.close()

            with patch.object(adaptive, "_DB", db_path), patch.object(
                adaptive,
                "_query_perf",
                wraps=adaptive._query_perf,
            ) as query_perf:
                stats = adaptive.get_perf_stats("momentum", "US", days=9999)

        self.assertEqual(stats["win_rate"], 50.0)
        self.assertEqual(stats["n"], 2)
        self.assertEqual(stats["source"], "backfill")
        self.assertNotIn(
            "= 'live'",
            [call.args[2] for call in query_perf.call_args_list],
        )

    def test_canonical_without_learning_allowed_column_falls_back_to_legacy_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "decisions.db"
            conn = sqlite3.connect(db_path)
            try:
                self._create_decisions_schema(conn)
                self._insert_decision(conn, ticker="LEGACY", pnl_pct=10.0)
                self._create_canonical_schema(conn, include_learning_allowed=False)
                self._insert_canonical(conn, closed=1, pnl_pct=-50.0, learning_allowed=None)
                conn.commit()
            finally:
                conn.close()

            with patch.object(adaptive, "_DB", db_path):
                stats = adaptive.get_perf_stats("momentum", "US", days=9999)

        self.assertEqual(stats["win_rate"], 100.0)
        self.assertEqual(stats["n"], 1)
        self.assertEqual(stats["source"], "live_small")


if __name__ == "__main__":
    unittest.main()
