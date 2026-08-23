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
                data_source TEXT,
                -- 2026-08-24: 프로덕션 스키마(ml/db_writer.py)에 있는 컬럼이다.
                -- 픽스처에서 빠져 있으면 is_simulated 필터를 추가하는 순간 쿼리가
                -- "no such column"으로 죽고, adaptive가 조용히 (0,0)을 돌려주는 것을
                -- 테스트가 못 잡는다. 픽스처는 프로덕션과 같아야 한다.
                is_simulated INTEGER DEFAULT 0
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
        is_simulated: int = 0,
    ) -> None:
        conn.execute(
            """
            INSERT INTO decisions (
                market, ticker, session_date, decision, strategy_used,
                pnl_pct, data_source, is_simulated
            ) VALUES ('US', ?, '2026-05-20', 'BUY_SIGNAL', 'momentum', ?, ?, ?)
            """,
            (ticker, pnl_pct, data_source, is_simulated),
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

    def test_simulated_rows_never_reach_adaptive_perf(self) -> None:
        """시뮬 하네스 행은 adaptive 성과에 들어가면 안 된다 (2026-08-24).

        sim_entry_path_gates가 SIMTK로 봇 경로를 태우면 decisions에 data_source='live'로
        들어간다(07-29 835행 실측). db_writer·db_health·dashboard에는 is_simulated 필터가
        있었는데 adaptive._query_perf에만 빠져 있었다. 지금까지는 그 행들의 pnl_pct가
        전부 NULL이라 무해했지만, 시뮬이 체결까지 흉내내면 가짜 성과로 라이브 파라미터가
        움직인다. 그 경로를 막는다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "decisions.db"
            conn = sqlite3.connect(db_path)
            try:
                self._create_decisions_schema(conn)
                # 진짜 라이브 2건: 1승 1패 -> 50%
                self._insert_decision(conn, ticker="REAL_WIN", pnl_pct=5.0)
                self._insert_decision(conn, ticker="REAL_LOSS", pnl_pct=-5.0)
                # 시뮬 3건 전승 — 섞이면 승률이 80%로 부풀어 오른다
                for i in range(3):
                    self._insert_decision(
                        conn, ticker=f"SIMTK{i}", pnl_pct=99.0,
                        data_source="live", is_simulated=1,
                    )
                conn.commit()
            finally:
                conn.close()

            with patch.object(adaptive, "_DB", db_path):
                stats = adaptive.get_perf_stats("momentum", "US", days=9999)

        self.assertEqual(stats["n"], 2, "시뮬 3건이 표본에 섞였다")
        self.assertEqual(stats["win_rate"], 50.0, "시뮬 전승이 승률을 부풀렸다")

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
