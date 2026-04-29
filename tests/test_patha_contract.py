from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from bot.candidate_policy import normalize_selection_result
from config.v2 import V2Config
from decision.registry import DecisionRegistry
from execution.safety_gate import SafetyContext, SafetyGate
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEventType
from runtime.v2_lifecycle_runtime import V2LifecycleRuntime


class PathAContractTests(unittest.TestCase):
    def test_price_targets_do_not_change_path_a_trade_ready_contract(self) -> None:
        candidates = [{"ticker": "005930"}, {"ticker": "000660"}, {"ticker": "035420"}]
        meta = normalize_selection_result(
            {
                "watchlist": ["005930", "000660", "035420"],
                "trade_ready": ["005930", "000660"],
                "price_targets": {
                    "005930": {
                        "buy_zone_low": 52_000,
                        "buy_zone_high": 52_500,
                        "sell_target": 54_500,
                        "stop_loss": 51_000,
                    },
                    "035420": {
                        "buy_zone_low": 180_000,
                        "buy_zone_high": 181_000,
                        "sell_target": 188_000,
                        "stop_loss": 176_000,
                    },
                },
            },
            candidates,
            "KR",
        )

        self.assertEqual(meta["trade_ready"], ["005930", "000660"])
        self.assertIn("005930", meta["price_targets"])
        self.assertNotIn("035420", meta["price_targets"])

    def test_pathb_disable_flags_do_not_block_path_a_safety_gate(self) -> None:
        gate = SafetyGate(V2Config(pathb_enabled=False, pathb_emergency_disable=True))
        decision = gate.evaluate(
            SafetyContext(
                market="KR",
                runtime_mode="live",
                ticker="005930",
                price_krw=52_000,
                qty=2,
                order_cost_krw=104_000,
                cash_krw=1_000_000,
                min_order_krw=100_000,
                market_open=True,
                broker_trust_level="trusted",
            )
        )

        self.assertTrue(decision.passed, decision)

    def test_path_a_lifecycle_does_not_overwrite_path_b_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain1",
            )
            store.create_path_run(
                path_run_id="path_b_1",
                decision_id=decision_id,
                path_type="claude_price",
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                status="WAITING",
                plan={"buy_zone_low": 52_000, "buy_zone_high": 52_500},
            )

            for event_type in (
                LifecycleEventType.SAFETY_PASSED,
                LifecycleEventType.ORDER_SENT,
                LifecycleEventType.ORDER_ACKED,
                LifecycleEventType.FILLED,
            ):
                registry.record_event(
                    event_type=event_type,
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id=decision_id,
                    prompt_version="v2",
                    brain_snapshot_id="brain1",
                    execution_id="path_a_order_1" if event_type != LifecycleEventType.SAFETY_PASSED else None,
                    payload={"path_type": "timing_adapter"},
                )

            events = store.events_for_decision(decision_id)
            self.assertEqual(events[-1]["event_type"], "FILLED")
            self.assertEqual(store.find_path_run("path_b_1")["status"], "WAITING")

    def test_path_a_safety_uses_market_daily_return_for_loss_limit(self) -> None:
        class _Risk:
            cash = 1_000_000
            positions = []
            trade_log = []

        class _Bot:
            _mode = "live"
            is_paper = False
            risk = _Risk()
            pending_orders = []
            session_active = True
            current_market = "KR"
            _broker_state = {"KR": {"trust_level": "trusted"}}

            def _current_session_date_str(self, market: str) -> str:
                return "2026-04-28"

            def _ticker_market(self, ticker: str) -> str:
                return "KR"

            def _market_daily_return_pct(self, market: str) -> float:
                return -2.5

            def _daily_pnl_pct(self, market: str) -> float:
                return 0.0

        runtime = V2LifecycleRuntime(_Bot(), is_paper=False)
        decision = runtime.safety_decision(
            "KR",
            "005930",
            risk_price_krw=52_000,
            qty=2,
            order_cost_krw=104_000,
            min_order_krw=100_000,
        )

        self.assertIsNotNone(decision)
        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason_code, "DAILY_LOSS_LIMIT")


    def test_path_a_and_path_b_share_same_daily_loss_source(self) -> None:
        """Path A와 Path B가 같은 _market_daily_return_pct 값을 daily_pnl_pct로 사용하는지 통합 검증.
        한쪽이 DAILY_LOSS_LIMIT로 막히면 다른 쪽도 같은 기준으로 막혀야 한다."""
        LOSS_RETURN = -2.5  # daily_loss_limit_pct 기본값(-2.0)보다 낮음

        class _Risk:
            cash = 1_000_000
            positions = []

        class _Bot:
            _mode = "live"
            is_paper = False
            risk = _Risk()
            pending_orders = []
            session_active = True
            current_market = "KR"
            _broker_state = {"KR": {"trust_level": "trusted"}}

            def _current_session_date_str(self, market: str) -> str:
                return "2026-04-28"

            def _ticker_market(self, ticker: str) -> str:
                return "KR"

            def _market_daily_return_pct(self, market: str) -> float:
                return LOSS_RETURN

            def _daily_pnl_pct(self, market: str) -> float:
                return 0.0  # 실현손익 기준으로는 손실 없음 — 이 값만 보면 통과됨

        # Path A: V2LifecycleRuntime.safety_decision()
        path_a_runtime = V2LifecycleRuntime(_Bot(), is_paper=False)
        path_a_decision = path_a_runtime.safety_decision(
            "KR",
            "001440",
            risk_price_krw=10_000,
            qty=5,
            order_cost_krw=50_000,
            min_order_krw=10_000,
        )

        # Path B: SafetyGate.evaluate()에 동일한 equity return 기반 daily_pnl_pct 전달
        gate = SafetyGate(V2Config())
        path_b_decision = gate.evaluate(
            SafetyContext(
                market="KR",
                runtime_mode="live",
                ticker="001440",
                price_krw=10_000,
                qty=5,
                order_cost_krw=50_000,
                cash_krw=1_000_000,
                market_open=True,
                broker_trust_level="trusted",
                daily_pnl_pct=LOSS_RETURN,
            )
        )

        self.assertIsNotNone(path_a_decision)
        self.assertFalse(path_a_decision.passed, "Path A must block on equity-based daily loss limit")
        self.assertEqual(path_a_decision.reason_code, "DAILY_LOSS_LIMIT")
        self.assertFalse(path_b_decision.passed, "Path B must block on same equity-based daily loss limit")
        self.assertEqual(path_b_decision.reason_code, "DAILY_LOSS_LIMIT")

    def test_20260428_path_a_not_bypassing_daily_loss_when_path_b_blocked(self) -> None:
        """20260428 사고 재현 회귀 테스트: 001440/047040 케이스.
        Path B가 시장 equity return 기준으로 DAILY_LOSS_LIMIT에 막혔는데
        Path A는 실현손익이 0%라서 통과하는 문제. 이제 Path A도 막혀야 한다."""

        class _Risk:
            cash = 1_000_000
            positions = []

        class _Bot:
            _mode = "live"
            is_paper = False
            risk = _Risk()
            pending_orders = []
            session_active = True
            current_market = "KR"
            _broker_state = {"KR": {"trust_level": "trusted"}}

            def _current_session_date_str(self, market: str) -> str:
                return "2026-04-28"

            def _ticker_market(self, ticker: str) -> str:
                return "KR"

            def _market_daily_return_pct(self, market: str) -> float:
                # 시장 equity 기준 손실 — Path B는 이 값으로 차단
                return -2.5

            def _daily_pnl_pct(self, market: str) -> float:
                # 실현손익 기준 0% — 구버전 Path A는 이 값을 보고 통과시켰음
                return 0.0

        runtime = V2LifecycleRuntime(_Bot(), is_paper=False)

        for ticker in ("001440", "047040"):
            decision = runtime.safety_decision(
                "KR",
                ticker,
                risk_price_krw=10_000,
                qty=5,
                order_cost_krw=50_000,
                min_order_krw=10_000,
            )
            self.assertIsNotNone(decision, f"{ticker}: safety_decision should not be None")
            self.assertFalse(decision.passed, f"{ticker}: Path A must block when equity return is below limit")
            self.assertEqual(
                decision.reason_code,
                "DAILY_LOSS_LIMIT",
                f"{ticker}: block reason must be DAILY_LOSS_LIMIT, not {decision.reason_code}",
            )


if __name__ == "__main__":
    unittest.main()
