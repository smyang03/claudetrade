"""벤치마크 비교 도구 검증."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.benchmark_compare import benchmark_report


class BenchmarkCompareTests(unittest.TestCase):
    def test_report_has_required_fields_and_runs_on_live_data(self):
        report = benchmark_report(days=7)
        for key in ("window", "system_net_krw", "trades", "spy_pct", "qqq_pct", "alpha_vs_spy_pct"):
            self.assertIn(key, report)

    def test_alpha_is_system_minus_spy(self):
        report = benchmark_report(days=30)
        if report["system_net_pct_of_account"] is not None and report["spy_pct"] is not None:
            self.assertAlmostEqual(
                report["alpha_vs_spy_pct"],
                round(report["system_net_pct_of_account"] - report["spy_pct"], 2),
                places=2,
            )


if __name__ == "__main__":
    unittest.main()
