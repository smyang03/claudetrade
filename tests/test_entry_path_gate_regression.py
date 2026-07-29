"""진입 경로 게이트 회귀 테스트.

2026-07-28 US 세션에서 judge BUY_READY 12건·즉시매수 6회 발동에도 실주문이 0건이었다.
원인은 고점근접 차단(FROM_HIGH_BLOCK_PCT=-2.0)이 BUY_READY까지 막은 것이었고,
그 경로가 log.debug + DB 미기록이라 며칠간 무흔적으로 숨었다.

이 테스트는 그 결함이 되살아나는 것을 막는다. 실제 run_cycle을 태우므로
게이트를 새로 넣거나 순서를 바꿔 BUY_READY 경로가 다시 끊기면 여기서 실패한다.

시뮬 하네스는 tools/sim_entry_path_gates.py 를 재사용한다(중복 유지 방지).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.sim_entry_path_gates import run_case  # noqa: E402


def _reached(**kw) -> bool:
    return "reached_order_loop" in run_case(**kw)["hits"]


def _hits(**kw) -> list:
    return run_case(**kw)["hits"]


BASE = dict(market="US", trade_ready=True, signal_kind="flat")


class BuyReadyReachesOrderLoopTests(unittest.TestCase):
    """BUY_READY(judge 결정 진입)가 주문 루프까지 도달해야 한다."""

    def test_buy_ready_survives_from_high_block_after_grace(self) -> None:
        """★ 2026-07-28 결함의 회귀 방지.

        개장 30분(_OPENING_GRACE_MIN) 이후 + 고점 대비 -0.4%(차단 임계 -2.0보다 위)는
        고점근접 차단의 정면 조건이다. BUY_READY는 여기서 면제되어야 한다.
        """
        hits = _hits(mode="MILD_BULL", elapsed_min=120.0, from_high_pct=-0.4,
                     buy_ready_route=True, **BASE)
        self.assertIn("from_high_EXEMPT", hits,
                      "BUY_READY가 고점근접 차단에서 면제되지 않았다")
        self.assertIn("reached_order_loop", hits,
                      "BUY_READY가 주문 루프에 도달하지 못했다(2026-07-28 결함 재발)")

    def test_buy_ready_within_opening_grace(self) -> None:
        """개장 30분 안에서는 고점근접 차단이 애초에 적용되지 않는다(7/27 VZ 체결 경로)."""
        self.assertTrue(_reached(mode="MILD_BULL", elapsed_min=10.0, from_high_pct=-0.4,
                                 buy_ready_route=True, **BASE))

    def test_buy_ready_deep_pullback(self) -> None:
        """고점 대비 -3.0%는 원래 차단 대상이 아니므로 면제와 무관하게 통과한다."""
        self.assertTrue(_reached(mode="MILD_BULL", elapsed_min=120.0, from_high_pct=-3.0,
                                 buy_ready_route=True, **BASE))

    def test_buy_ready_survives_late_session_score_gate(self) -> None:
        """★ 2026-07-29 발견분 회귀 방지 — 장 후반 score 게이트.

        entry_priority score는 기술신호 품질 점수라 claude_price_a(judge 진입)는
        strat=0.000으로 구조적으로 낮다(실측 0.240 < LATE_SESSION_SCORE_MIN 0.6).
        US 270분(≈03:00 KST) 이후 BUY_READY가 100% 차단되던 것을 면제로 풀었다.
        """
        for elapsed in (270.0, 330.0, 360.0):
            with self.subTest(elapsed=elapsed):
                self.assertTrue(
                    _reached(mode="MILD_BULL", elapsed_min=elapsed, from_high_pct=-0.4,
                             buy_ready_route=True, **BASE),
                    f"장 후반 {elapsed:.0f}분에서 BUY_READY가 주문 루프에 도달하지 못했다",
                )


class OpeningRangePullbackTests(unittest.TestCase):
    """★ 2026-07-29 발견분 — ORP(US live base 주력)와 고점근접 차단의 기준점 충돌.

    ORP는 OR 고점 대비 -0.2~-1.0% 눌림에서 발화하는데, 고점근접 차단은 당일 고점 대비
    -2.0%를 요구한다. ORP의 눌림이 차단선보다 얕아 거의 항상 걸렸다.
    실측(US post_open 11,502건, 개장 30분 이후): ORP 눌림구간 1,320건 중 82.3%가 차단 대상.
    """

    OR = {"high": 101.0, "low": 100.0, "formed": True}

    def test_orp_survives_from_high_block_across_entry_window(self) -> None:
        """설계상 진입창(OR 15분 + 60분) 전체에서 주문 루프에 도달해야 한다."""
        for elapsed in (16.0, 31.0, 40.0, 60.0, 74.0):
            with self.subTest(elapsed=elapsed):
                self.assertTrue(
                    _reached(mode="MILD_BULL", elapsed_min=elapsed, from_high_pct=-0.6,
                             market="US", trade_ready=True, signal_kind="flat",
                             buy_ready_route=False, or_state=self.OR),
                    f"ORP가 경과 {elapsed:.0f}분에서 주문 루프에 도달하지 못했다",
                )

    def test_orp_entry_window_still_expires(self) -> None:
        """진입창(75분)을 넘기면 면제와 무관하게 발화하지 않는다."""
        self.assertFalse(
            _reached(mode="MILD_BULL", elapsed_min=80.0, from_high_pct=-0.6,
                     market="US", trade_ready=True, signal_kind="flat",
                     buy_ready_route=False, or_state=self.OR)
        )


class RiskGatesStillBlockTests(unittest.TestCase):
    """면제가 리스크·국면 게이트까지 뚫으면 안 된다."""

    def test_defensive_mode_blocks(self) -> None:
        hits = _hits(mode="DEFENSIVE", elapsed_min=120.0, from_high_pct=-0.4,
                     buy_ready_route=True, **BASE)
        self.assertNotIn("reached_order_loop", hits, "DEFENSIVE에서 진입이 뚫렸다")

    def test_halt_mode_blocks(self) -> None:
        hits = _hits(mode="HALT", elapsed_min=120.0, from_high_pct=-0.4,
                     buy_ready_route=True, **BASE)
        self.assertNotIn("reached_order_loop", hits, "HALT에서 진입이 뚫렸다")

    def test_late_session_cutoff_blocks(self) -> None:
        hits = _hits(mode="MILD_BULL", elapsed_min=370.0, from_high_pct=-0.4,
                     buy_ready_route=True, **BASE)
        self.assertIn("late_session_cutoff", hits)
        self.assertNotIn("reached_order_loop", hits, "마감 직전인데 진입이 뚫렸다")

    def test_no_route_no_entry(self) -> None:
        """judge route가 없으면 즉시매수가 발동하면 안 된다."""
        hits = _hits(mode="MILD_BULL", elapsed_min=120.0, from_high_pct=-0.4,
                     buy_ready_route=False, **BASE)
        self.assertNotIn("buy_ready_fired", hits)


class HarnessHealthTests(unittest.TestCase):
    """하네스 자체가 깨지면 위 테스트가 조용히 무의미해지므로 함께 지킨다."""

    def test_harness_runs_without_error(self) -> None:
        r = run_case(mode="MILD_BULL", elapsed_min=120.0, from_high_pct=-0.4,
                     buy_ready_route=True, **BASE)
        self.assertEqual(r["err"], "", f"시뮬 하네스 오류: {r['err']}")
        self.assertTrue(r["records"], "로그가 하나도 수집되지 않았다(캡처 배선 파손)")


if __name__ == "__main__":
    unittest.main()
