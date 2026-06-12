# -*- coding: utf-8 -*-
"""hold advisor 실측 prior + portfolio_context 회귀 테스트 (2026-06-13).

배경: 2026-05~06 청산 원장 전수 재채점에서 HOLD 실패의 ~40%(크기 기준)가
판단 시점에 인지 가능한 신호(시장 급반전, 손실 상태 HOLD)를 갖고 있었다.
실측 기저율을 system prior로 주입하고, 동질 베타 집중(6/3형 메가캡 동시
carry)을 advisor가 인지하도록 portfolio_context를 제공한다.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))


class MeasuredPriorsSystemTests(unittest.TestCase):
    def test_system_prompt_contains_measured_priors(self):
        from minority_report import hold_advisor

        system = hold_advisor._HOLD_ADVISOR_SYSTEM
        # 4개 prior + portfolio_context 지침이 모두 system 블록에 존재해야 한다
        self.assertIn("실측 prior", system)
        self.assertIn("market_sharp_reversal_active", system)
        self.assertIn("loss_deferral", system)
        self.assertIn("next_review_min", system)
        self.assertIn("protective_stop은 갭을 막지 못한다", system)
        self.assertIn("portfolio_context", system)

    def test_priors_do_not_break_korean_response_contract(self):
        from minority_report import hold_advisor

        # 한국어 응답 지시는 prior 뒤에도 유지되어야 한다
        self.assertTrue(hold_advisor._HOLD_ADVISOR_SYSTEM.rstrip().endswith("모든 응답은 한국어로 작성하세요."))

    def test_priors_present_in_all_system_blocks_via_constant(self):
        from minority_report import hold_advisor

        # _MEASURED_PRIORS가 system 합성에 실제 포함되는지 (단순 정의만 있는 상태 방지)
        self.assertIn(hold_advisor._MEASURED_PRIORS, hold_advisor._HOLD_ADVISOR_SYSTEM)


class PortfolioContextTests(unittest.TestCase):
    def _runtime_stub(self, positions):
        from runtime.pathb_runtime import PathBRuntime

        stub = SimpleNamespace()
        stub.bot = SimpleNamespace(risk=SimpleNamespace(positions=positions))
        stub._ticker_market = lambda ticker: "KR" if str(ticker or "").isdigit() else "US"
        return PathBRuntime._pathb_portfolio_context, stub

    def test_portfolio_context_counts_market_scoped_positions(self):
        fn, stub = self._runtime_stub([
            {"ticker": "NVDA", "qty": 2},
            {"ticker": "GOOGL", "qty": 1},
            {"ticker": "AAPL", "qty": 3},
            {"ticker": "005930", "qty": 10},  # KR — US 스냅샷에서 제외
            {"ticker": "MSFT", "qty": 0},     # qty 0 — 제외
        ])
        ctx = fn(stub, "US")
        self.assertEqual(ctx["open_positions_count"], 3)
        self.assertEqual(ctx["open_tickers"], ["AAPL", "GOOGL", "NVDA"])

    def test_portfolio_context_empty_when_no_positions(self):
        fn, stub = self._runtime_stub([])
        self.assertEqual(fn(stub, "US"), {})

    def test_portfolio_context_survives_broken_rows(self):
        fn, stub = self._runtime_stub([
            {"ticker": "NVDA", "qty": "bad"},
            {"ticker": "", "qty": 5},
            {"ticker": "AMD", "qty": 1},
        ])
        ctx = fn(stub, "US")
        self.assertEqual(ctx["open_positions_count"], 1)
        self.assertEqual(ctx["open_tickers"], ["AMD"])

    def test_portfolio_context_handles_missing_risk(self):
        from runtime.pathb_runtime import PathBRuntime

        stub = SimpleNamespace()
        stub.bot = SimpleNamespace()  # risk 속성 없음
        stub._ticker_market = lambda ticker: "US"
        self.assertEqual(PathBRuntime._pathb_portfolio_context(stub, "US"), {})


if __name__ == "__main__":
    unittest.main()
