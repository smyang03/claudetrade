"""cluster_halt_counterfactual 도구 단위 테스트.

cluster halt(당일 손절 N개 누적 후 신규진입 차단) counterfactual 측정의 핵심 로직:
- 손절 임계 도달 시점(N번째 손절 closed_at) 이후 진입을 '막혔을 진입'으로 식별
- 막혔을 진입의 net 합 / right-tail(큰 이익 차단) 계산
- net 컬럼 부재 시 gross fallback
"""

from __future__ import annotations

import unittest

from tools.cluster_halt_counterfactual import analyze, build_report


def _row(market, sdate, filled_at, closed_at, reason, ticker, net=None, gross=0.0):
    return {
        "market": market,
        "session_date": sdate,
        "filled_at": filled_at,
        "closed_at": closed_at,
        "close_reason": reason,
        "ticker": ticker,
        "pnl_pct": gross,
        "pnl_pct_net": net,
    }


def _cluster_session(threshold_met=True):
    """손절 4개 + 이후 진입 2개(손실 -2.0, 이익 +5.0) + halt 이전 진입 1개."""
    rows = [
        _row("US", "2026-06-17", "2026-06-17T09:30", "2026-06-17T10:00", "CLOSED_LOSS_CAP", "AAA", net=-2.0),
        _row("US", "2026-06-17", "2026-06-17T09:35", "2026-06-17T10:10", "CLOSED_HARD_STOP", "BBB", net=-2.0),
        _row("US", "2026-06-17", "2026-06-17T09:40", "2026-06-17T10:20", "CLOSED_LOSS_CAP", "CCC", net=-2.0),
        _row("US", "2026-06-17", "2026-06-17T09:45", "2026-06-17T10:30", "CLOSED_LOSS_CAP", "DDD", net=-2.0),
        # halt_at = 4번째 손절 closed_at = 10:30
        _row("US", "2026-06-17", "2026-06-17T10:45", "2026-06-17T15:00", "CLOSED_CLAUDE_SELL", "EEE", net=-2.0),
        _row("US", "2026-06-17", "2026-06-17T11:00", "2026-06-17T15:30", "CLOSED_CLAUDE_PRICE_TARGET", "FFF", net=5.0),
        # halt 이전 진입(09:50 < 10:30) → 안 막힘
        _row("US", "2026-06-17", "2026-06-17T09:50", "2026-06-17T14:00", "CLOSED_CLAUDE_SELL", "GGG", net=1.0),
    ]
    return rows


class ClusterHaltCounterfactualTests(unittest.TestCase):
    def test_cluster_detected_and_blocked_entries_after_halt(self):
        clusters, blocked = analyze(_cluster_session(), threshold=4)
        self.assertEqual(len(clusters), 1)
        c = clusters[0]
        self.assertEqual(c.halt_at, "2026-06-17T10:30")
        # 막혔을 진입은 EEE(10:45), FFF(11:00)만 — GGG(09:50)는 halt 이전이라 제외
        self.assertEqual(c.blocked_entries, 2)
        self.assertEqual(sorted(c.tickers), ["EEE", "FFF"])
        self.assertEqual(c.blocked_net_sum, 3.0)  # -2.0 + 5.0
        self.assertEqual(c.blocked_winners, 1)
        self.assertEqual(c.blocked_losers, 1)

    def test_right_tail_winner_flagged(self):
        clusters, _ = analyze(_cluster_session(), threshold=4)
        # FFF +5.0이 막혔을 최대 이익 = right-tail 경고
        self.assertEqual(clusters[0].biggest_winner_blocked, 5.0)

    def test_threshold_not_met_no_cluster(self):
        clusters, blocked = analyze(_cluster_session(), threshold=5)
        self.assertEqual(clusters, [])
        self.assertEqual(blocked, [])

    def test_net_falls_back_to_gross_when_missing(self):
        rows = [
            _row("US", "2026-06-18", "2026-06-18T09:30", "2026-06-18T10:00", "CLOSED_LOSS_CAP", "AAA", net=None, gross=-1.5),
            _row("US", "2026-06-18", "2026-06-18T09:31", "2026-06-18T10:01", "CLOSED_LOSS_CAP", "BBB", net=None, gross=-1.5),
            # threshold=2 도달, halt_at=10:01, 이후 진입 net 없음 → gross -3.0 사용
            _row("US", "2026-06-18", "2026-06-18T10:30", "2026-06-18T15:00", "CLOSED_CLAUDE_SELL", "CCC", net=None, gross=-3.0),
        ]
        clusters, _ = analyze(rows, threshold=2)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].blocked_net_sum, -3.0)

    def test_filled_at_missing_excluded_from_blocked(self):
        rows = _cluster_session()
        rows.append(_row("US", "2026-06-17", None, "2026-06-17T16:00", "CLOSED_CLAUDE_SELL", "HHH", net=9.0))
        clusters, _ = analyze(rows, threshold=4)
        # filled_at None인 HHH는 막혔을 진입에서 제외
        self.assertNotIn("HHH", clusters[0].tickers)
        self.assertEqual(clusters[0].blocked_entries, 2)

    def test_build_report_interpretation_positive_is_right_tail_risk(self):
        report = build_report(_cluster_session(), threshold=4)
        self.assertEqual(report["total_blocked_net_sum"], 3.0)
        self.assertIn("right-tail", report["interpretation"])

    def test_build_report_negative_net_is_loss_defense(self):
        rows = [
            _row("US", "2026-06-19", "2026-06-19T09:30", "2026-06-19T10:00", "CLOSED_LOSS_CAP", "AAA", net=-2.0),
            _row("US", "2026-06-19", "2026-06-19T09:31", "2026-06-19T10:01", "CLOSED_LOSS_CAP", "BBB", net=-2.0),
            _row("US", "2026-06-19", "2026-06-19T10:30", "2026-06-19T15:00", "CLOSED_LOSS_CAP", "CCC", net=-2.0),
        ]
        report = build_report(rows, threshold=2)
        self.assertLess(report["total_blocked_net_sum"], 0)
        self.assertIn("손실을 막았을", report["interpretation"])


if __name__ == "__main__":
    unittest.main()
