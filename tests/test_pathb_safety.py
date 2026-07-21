from __future__ import annotations

import unittest

from config.v2 import V2Config
from decision.claude_price_plan import make_price_plan
from execution.safety_gate import PathBSafetyGate, SafetyContext, SafetyGate


def _ctx() -> SafetyContext:
    return SafetyContext(
        market="KR",
        runtime_mode="live",
        ticker="005930",
        price_krw=52_000,
        qty=1,
        order_cost_krw=52_000,
        cash_krw=1_000_000,
        min_order_krw=50_000,
        market_open=True,
        broker_trust_level="trusted",
    )


def _plan(confidence: float = 0.7):
    return make_price_plan(
        decision_id="dec1",
        ticker="005930",
        market="KR",
        session_date="2026-04-27",
        buy_zone_low=52_000,
        buy_zone_high=52_500,
        sell_target=54_500,
        stop_loss=51_000,
        hold_days=1,
        confidence=confidence,
    )


class PathBSafetyTests(unittest.TestCase):
    def test_blocks_disabled_invalid_and_duplicate(self) -> None:
        self.assertEqual(
            PathBSafetyGate(V2Config(pathb_mode="disabled")).evaluate(_ctx(), plan=_plan()).reason_code,
            "PATHB_DISABLED",
        )
        self.assertEqual(PathBSafetyGate().evaluate(_ctx(), plan=None).reason_code, "CLAUDE_PRICE_INVALID")
        self.assertEqual(
            PathBSafetyGate().evaluate(_ctx(), plan=_plan(), patha_holding=True).reason_code,
            "PATH_DUPLICATE_HOLDING",
        )

    def test_blocks_daily_limit_confidence_and_base_gate(self) -> None:
        self.assertEqual(
            PathBSafetyGate(V2Config(pathb_max_daily_entries=1)).evaluate(
                _ctx(),
                plan=_plan(),
                pathb_daily_count=1,
            ).reason_code,
            "PATHB_MAX_DAILY_ENTRIES",
        )
        self.assertEqual(
            PathBSafetyGate().evaluate(_ctx(), plan=_plan(0.3)).reason_code,
            "CLAUDE_PRICE_INVALID",
        )
        bad_ctx = SafetyContext(**{**_ctx().__dict__, "cash_krw": 10})
        self.assertEqual(PathBSafetyGate().evaluate(bad_ctx, plan=_plan()).reason_code, "INSUFFICIENT_CASH")

    def test_claude_price_invalid_exposes_reason_detail(self) -> None:
        # confidence 미달은 일반 CLAUDE_PRICE_INVALID 뒤에 가려지지 않고
        # reason_detail/errors 로 노출돼야 한다 (운영자 가시 로그 축).
        low_conf = PathBSafetyGate().evaluate(_ctx(), plan=_plan(0.3))
        self.assertEqual(low_conf.reason_code, "CLAUDE_PRICE_INVALID")
        self.assertIn("confidence_below_minimum", low_conf.details.get("errors", []))
        self.assertIn("confidence_below_minimum", low_conf.details.get("reason_detail", ""))
        # plan 부재 케이스도 구체 사유를 남긴다.
        missing = PathBSafetyGate().evaluate(_ctx(), plan=None)
        self.assertEqual(missing.reason_code, "CLAUDE_PRICE_INVALID")
        self.assertEqual(missing.details.get("reason_detail"), "plan_missing")

    def test_daily_loss_limit_uses_realized_pnl_basis(self) -> None:
        gate = SafetyGate(V2Config(daily_loss_limit_pct=-2.0))
        ctx = SafetyContext(
            **{
                **_ctx().__dict__,
                "daily_pnl_pct": 0.0,
                "daily_pnl_basis": "realized",
                "realized_daily_pnl_pct": 0.0,
                "equity_daily_pnl_pct": -3.0,
            }
        )

        decision = gate.evaluate(ctx)

        self.assertTrue(decision.passed, decision)
        self.assertEqual(decision.details["daily_pnl_basis"], "realized")
        self.assertEqual(decision.details["realized_daily_pnl_pct"], 0.0)
        self.assertEqual(decision.details["equity_daily_pnl_pct"], -3.0)

    def test_daily_loss_limit_blocks_realized_loss(self) -> None:
        gate = SafetyGate(V2Config(daily_loss_limit_pct=-2.0))
        ctx = SafetyContext(
            **{
                **_ctx().__dict__,
                "daily_pnl_pct": -2.1,
                "daily_pnl_basis": "realized",
                "realized_daily_pnl_pct": -2.1,
                "equity_daily_pnl_pct": 0.5,
            }
        )

        decision = gate.evaluate(ctx)

        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason_code, "DAILY_LOSS_LIMIT")
        self.assertEqual(decision.details["daily_pnl_basis"], "realized")

    def test_passes_when_all_conditions_ok(self) -> None:
        decision = PathBSafetyGate().evaluate(_ctx(), plan=_plan())
        self.assertTrue(decision.passed, decision)


class MarketScopedDailyEntryCapTests(unittest.TestCase):
    """일일 진입 상한의 시장별 주입 — 최적값이 시장마다 다르다.

    2026-07-22 실측(국면게이트 통과 건, 시간순 앞 N건 시뮬):
      US 세션당 5건: +2.22% -> +28.73% (+26.52%p). 4~6건이 안정 구간, 3건은 -9.57%로 과도.
      US 세션당 7건 이상 구간은 거래 95건에 -44.65% — "하루에 많이 살수록 진다".
      KR은 n=38로 표본이 부족해 글로벌 값을 유지한다.
    """

    def test_injected_cap_overrides_config(self) -> None:
        gate = PathBSafetyGate(V2Config(pathb_max_daily_entries=40))
        blocked = gate.evaluate(_ctx(), plan=_plan(), pathb_daily_count=5, max_daily_entries=5)
        self.assertEqual(blocked.reason_code, "PATHB_MAX_DAILY_ENTRIES")
        self.assertEqual(blocked.details.get("max_daily_entries"), 5)

    def test_injected_cap_allows_below_limit(self) -> None:
        gate = PathBSafetyGate(V2Config(pathb_max_daily_entries=40))
        ok = gate.evaluate(_ctx(), plan=_plan(), pathb_daily_count=4, max_daily_entries=5)
        self.assertNotEqual(ok.reason_code, "PATHB_MAX_DAILY_ENTRIES")

    def test_without_injection_falls_back_to_config(self) -> None:
        """인자가 없으면 기존 config 값을 그대로 쓴다(현행 동작 보존)."""
        gate = PathBSafetyGate(V2Config(pathb_max_daily_entries=2))
        self.assertEqual(
            gate.evaluate(_ctx(), plan=_plan(), pathb_daily_count=2).reason_code,
            "PATHB_MAX_DAILY_ENTRIES",
        )
        ok = gate.evaluate(_ctx(), plan=_plan(), pathb_daily_count=1)
        self.assertNotEqual(ok.reason_code, "PATHB_MAX_DAILY_ENTRIES")

    def test_zero_injection_is_ignored(self) -> None:
        """0/None은 미설정으로 보고 config로 후퇴한다(실수로 진입을 전면 차단하지 않는다)."""
        gate = PathBSafetyGate(V2Config(pathb_max_daily_entries=40))
        ok = gate.evaluate(_ctx(), plan=_plan(), pathb_daily_count=10, max_daily_entries=0)
        self.assertNotEqual(ok.reason_code, "PATHB_MAX_DAILY_ENTRIES")


if __name__ == "__main__":
    unittest.main()
