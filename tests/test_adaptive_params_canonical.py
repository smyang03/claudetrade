from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from strategy import adaptive_params as adaptive


class AdaptiveParamsCanonicalTests(unittest.TestCase):
    def test_perf_stats_prefers_v2_canonical_closed_live_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "decisions.db"
            conn = sqlite3.connect(db_path)
            try:
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
                conn.execute(
                    """
                    INSERT INTO decisions (
                        market, ticker, session_date, decision, strategy_used,
                        pnl_pct, data_source
                    ) VALUES ('US', 'LEGACY', '2026-05-20', 'BUY_SIGNAL', 'momentum', 10.0, 'live')
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE v2_canonical_performance (
                        market TEXT,
                        runtime_mode TEXT,
                        session_date TEXT,
                        strategy TEXT,
                        path_type TEXT,
                        route TEXT,
                        closed INTEGER,
                        pnl_pct REAL
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO v2_canonical_performance (
                        market, runtime_mode, session_date, strategy, path_type,
                        route, closed, pnl_pct
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("US", "live", "2026-05-20", "momentum", "path_a", "plan_a", 1, 2.0),
                        ("US", "live", "2026-05-20", "momentum", "path_a", "plan_a", 1, -1.0),
                        ("US", "live", "2026-05-20", "momentum", "path_a", "plan_a", 0, 30.0),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            with patch.object(adaptive, "_DB", db_path):
                stats = adaptive.get_perf_stats("momentum", "US", days=9999)

        self.assertEqual(stats["win_rate"], 50.0)
        self.assertEqual(stats["n"], 2)
        self.assertEqual(stats["source"], "v2_canonical_small")


if __name__ == "__main__":
    unittest.main()
