import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dashboard.dashboard_server as dashboard


class StrategyCohortProgressTests(unittest.TestCase):
    def test_progress_counts_settlements_and_separates_clean_subset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shadow_db = Path(tmp) / "us_swing_shadow.db"
            con = sqlite3.connect(shadow_db)
            try:
                con.execute(
                    """
                    CREATE TABLE signals (
                        signal_date TEXT,
                        ticker TEXT,
                        execution_shadow_contract_id TEXT,
                        handoff_status TEXT,
                        handoff_reason TEXT,
                        handoff_quote_price REAL,
                        handoff_qty INTEGER,
                        candidate_source TEXT,
                        probability REAL,
                        handoff_order_no TEXT
                    )
                    """
                )
                con.executemany(
                    "INSERT INTO signals VALUES (?, ?, 'contract', 'SUBMITTED', 'contract_passed', 10, 1, 'day_losers', 0.6, ?)",
                    [
                        ("2026-08-03", "CLEAN", "O1"),
                        ("2026-08-04", "SUSPECT", "O2"),
                        ("2026-08-05", "UNFILLED", "O3"),
                        ("2026-08-06", "HOLDING", "O4"),
                    ],
                )
                con.commit()
            finally:
                con.close()

            fills = {
                "O1": {"ticker": "CLEAN", "filled_at": "2026-08-03T13:30:00"},
                "O2": {"ticker": "SUSPECT", "filled_at": "2026-08-04T13:30:00"},
                "O4": {"ticker": "HOLDING", "filled_at": "2026-08-06T13:30:00"},
            }
            canonical = {
                "CLEAN|2026-08-03": {
                    "net_pct": 12.0, "is_net": True, "quality_grade": "CLEAN",
                    "learning_allowed": 1,
                },
                "SUSPECT|2026-08-04": {
                    "net_pct": -2.0, "is_net": True, "quality_grade": "SUSPECT",
                    "learning_allowed": 0,
                },
            }

            with patch.object(dashboard, "US_SWING_SHADOW_DB_PATH", shadow_db), patch.object(
                dashboard, "_strategy_filled_entries", return_value=fills
            ), patch.object(
                dashboard, "_strategy_canonical_nets", return_value=canonical
            ), patch.object(
                dashboard, "_strategy_broker_positions",
                return_value=[{"ticker": "HOLDING"}],
            ):
                cohort = dashboard._strategy_cohort("live")

        self.assertEqual(cohort["submitted"], 4)
        self.assertEqual(cohort["count"], 2, "30건 진행도는 제출이 아니라 정산을 세야 한다")
        self.assertEqual(cohort["settled"], 2)
        self.assertEqual(cohort["strict_settled"], 1)
        self.assertEqual(cohort["holding"], 1)
        self.assertEqual(cohort["not_filled"], 1)
        self.assertEqual(cohort["settled_nets"], [12.0, -2.0])
        self.assertEqual(cohort["strict_settled_nets"], [12.0])

        stats = dashboard._strategy_cohort_stats(cohort)
        self.assertEqual(stats["settled_mean_pct"], 5.0)
        self.assertEqual(stats["settled_win_rate"], 50.0)
        self.assertEqual(stats["strict_mean_pct"], 12.0)
        self.assertEqual(stats["strict_win_rate"], 100.0)


if __name__ == "__main__":
    unittest.main()
