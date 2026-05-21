from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from lifecycle.validation import V2PhaseValidator
from config.v2 import DEFAULT_V2_CONFIG, V2Config
from runtime.risk_factory import create_risk_manager
from runtime.risk_profile import build_risk_profile
from tools.v2_archive_guard import scan_archive_imports
from tools.v2_live_smoke import run_live_smoke


ROOT = Path(__file__).resolve().parent.parent


class V2Phase5Tests(unittest.TestCase):
    def test_risk_profile_separates_market_and_runtime(self):
        kr = build_risk_profile("KR", "live", usd_krw=1400)
        us = build_risk_profile("US", "paper", usd_krw=1400)

        self.assertEqual(kr.market, "KR")
        self.assertEqual(kr.runtime_mode, "live")
        self.assertEqual(kr.currency, "KRW")
        self.assertEqual(kr.fixed_order_krw, DEFAULT_V2_CONFIG.kr_fixed_order_krw)
        self.assertEqual(us.market, "US")
        self.assertEqual(us.runtime_mode, "paper")
        self.assertEqual(us.currency, "USD")
        expected_us_fixed = DEFAULT_V2_CONFIG.us_fixed_order_krw or DEFAULT_V2_CONFIG.us_fixed_order_usd * 1400
        expected_us_min = DEFAULT_V2_CONFIG.us_min_order_krw or DEFAULT_V2_CONFIG.us_min_order_usd * 1400
        self.assertEqual(us.fixed_order_krw, expected_us_fixed)
        self.assertEqual(us.min_order_krw, expected_us_min)

        dynamic_us = build_risk_profile(
            "US",
            "live",
            usd_krw=1400,
            config=V2Config(us_fixed_order_krw=100_000, us_min_order_krw=100_000),
        )
        self.assertEqual(dynamic_us.fixed_order_krw, 100_000)
        self.assertEqual(dynamic_us.min_order_krw, 100_000)
        self.assertAlmostEqual(dynamic_us.fixed_order_native, 100_000 / 1400)

    def test_risk_factory_attaches_profile_to_manager(self):
        profile = build_risk_profile("KR", "live", usd_krw=1400)
        manager = create_risk_manager(profile, init_cash_krw=1_000_000)

        self.assertEqual(manager.market, "KR")
        self.assertEqual(manager.max_order_krw, profile.fixed_order_krw)
        self.assertEqual(manager.v2_risk_profile["runtime_mode"], "live")

    def test_archive_guard_blocks_archive_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad.py").write_text("from archive.legacy_v1 import old\n", encoding="utf-8")
            findings = scan_archive_imports(root)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].target, "archive.legacy_v1")

    def test_live_smoke_for_kr_and_us_has_no_broker_dependency(self):
        kr = run_live_smoke(market="KR", runtime_mode="live", root=ROOT, usd_krw=1400, session_date="2026-05-02")
        us = run_live_smoke(market="US", runtime_mode="live", root=ROOT, usd_krw=1400, session_date="2026-05-02")

        self.assertTrue(kr["ok"], kr)
        self.assertTrue(us["ok"], us)
        self.assertEqual(kr["event_count"], 2)
        self.assertEqual(us["event_count"], 2)
        self.assertEqual(kr["session_date"], "2026-05-02")
        self.assertEqual(us["session_date"], "2026-05-02")
        self.assertEqual(kr["smoke_context"]["runtime_context"], "smoke")
        self.assertFalse(kr["smoke_context"]["broker_call"])
        self.assertFalse(kr["smoke_context"]["order_send"])
        self.assertEqual(kr["log_prefix"], "[SMOKE][NO_BROKER][NO_ORDER]")

    def test_phase5_gate_runs_with_cumulative_previous_phases(self):
        report = V2PhaseValidator(ROOT).validate(5)

        self.assertTrue(report["ok"], report)
        self.assertEqual([phase["phase"] for phase in report["phases"]], [1, 2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
