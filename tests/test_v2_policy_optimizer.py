from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from research.v2_policy_optimizer import OptimizerConfig, build_policy_optimization_report, evaluate_candidates


class V2PolicyOptimizerTests(unittest.TestCase):
    def test_candidate_accepts_positive_validation(self):
        trades = []
        for i in range(100):
            trades.append(
                {
                    "source": "s1" if i < 50 else "s2",
                    "market": "US",
                    "strategy": "mean_reversion",
                    "entry_timing": "next_open",
                    "entry_day_exit_policy": "defer",
                    "mode": "NEUTRAL",
                    "ticker": f"T{i}",
                    "entry_date": f"2026-01-{(i % 28) + 1:02d}",
                    "signal_date": f"2026-01-{(i % 28) + 1:02d}",
                    "exit_date": f"2026-01-{(i % 28) + 1:02d}",
                    "net_pnl_pct": 0.5 if i % 3 else -0.2,
                }
            )
        candidates = evaluate_candidates(
            trades,
            OptimizerConfig(
                min_trades=30,
                min_validation_trades=10,
                min_validation_pf=1.01,
                min_validation_avg_pct=0.01,
                min_positive_source_ratio=0.5,
            ),
        )

        self.assertTrue(any(candidate["accepted"] for candidate in candidates), candidates)

    def test_report_writes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "data" / "backtest_audit" / "runs" / "sample"
            run_dir.mkdir(parents=True)
            path = run_dir / "audit_trades_20260426.csv"
            path.write_text(
                "market,strategy,entry_timing,entry_day_exit_policy,mode,ticker,signal_date,entry_date,exit_date,net_pnl_pct\n"
                + "\n".join(
                    f"US,mean_reversion,next_open,defer,NEUTRAL,T{i},2026-01-{(i%28)+1:02d},2026-01-{(i%28)+1:02d},2026-01-{(i%28)+1:02d},{0.5 if i % 3 else -0.2}"
                    for i in range(80)
                ),
                encoding="utf-8",
            )
            paths = build_policy_optimization_report(
                root,
                config=OptimizerConfig(
                    min_trades=30,
                    min_validation_trades=10,
                    min_validation_pf=1.01,
                    min_validation_avg_pct=0.01,
                    min_positive_source_ratio=0.0,
                ),
            )
            self.assertTrue(Path(paths["json"]).exists())
            self.assertTrue(Path(paths["markdown"]).exists())


if __name__ == "__main__":
    unittest.main()
