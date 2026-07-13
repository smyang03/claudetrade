from __future__ import annotations

import unittest

import pandas as pd

from tools import profit_path_forward_monitor as monitor


NOW = pd.Timestamp("2026-07-13T05:00:00Z")
BASE_TS = pd.Timestamp("2026-07-13T00:10:00Z")


def _prediction(ticker: str, *, probability: float, evaluable: bool, reason: str, strategy: str, observed: int):
    return {
        "event_id": abs(hash(ticker)) % 100000,
        "market": "KR",
        "session_date": "2026-07-13",
        "ticker": ticker,
        "prediction_ts": BASE_TS,
        "prediction_minute": BASE_TS.floor("min"),
        "path_name": "immediate",
        "model_version": "profit_path_shadow_KR_test",
        "probability": probability,
        "expected_net_pct": 0.3,
        "uncertainty": 0.05,
        "ood": not evaluable,
        "strategy": strategy,
        "observed_feature_n": observed,
        "evaluable": evaluable,
        "abstain_reason": reason,
    }


def _outcome(ticker: str, *, outcome_60m: float):
    return {
        "path_id": abs(hash(ticker)) % 100000,
        "market": "KR",
        "session_date": "2026-07-13",
        "ticker": ticker,
        "ticker_key": ticker,
        "known_at": BASE_TS,
        "outcome_ts": BASE_TS,
        "path_name": "immediate",
        "outcome_60m_pct": outcome_60m,
        "max_drawdown_60m_pct": -0.5,
    }


class ClassifyPredictionTests(unittest.TestCase):
    def test_no_features_is_unsupported_cohort(self) -> None:
        # Tier2 섹터플레이처럼 스크리너 후보가 아닌 종목 → 피처가 애초에 없다.
        evaluable, reason = monitor.classify_prediction(ood=True, observed_feature_n=0)
        self.assertFalse(evaluable)
        self.assertEqual(reason, monitor.ABSTAIN_UNSUPPORTED_COHORT)

    def test_ood_with_features_is_coverage_insufficient(self) -> None:
        evaluable, reason = monitor.classify_prediction(ood=True, observed_feature_n=9)
        self.assertFalse(evaluable)
        self.assertEqual(reason, monitor.ABSTAIN_FEATURE_COVERAGE)

    def test_missing_ood_is_fail_closed(self) -> None:
        evaluable, reason = monitor.classify_prediction(ood=None, observed_feature_n=11)
        self.assertFalse(evaluable)
        self.assertEqual(reason, monitor.ABSTAIN_FEATURE_COVERAGE)

    def test_string_false_is_not_accepted_as_runtime_boolean(self) -> None:
        evaluable, reason = monitor.classify_prediction(ood="false", observed_feature_n=11)
        self.assertFalse(evaluable)
        self.assertEqual(reason, monitor.ABSTAIN_FEATURE_COVERAGE)

    def test_too_few_observed_features_is_coverage_insufficient(self) -> None:
        evaluable, reason = monitor.classify_prediction(ood=False, observed_feature_n=3)
        self.assertFalse(evaluable)
        self.assertEqual(reason, monitor.ABSTAIN_FEATURE_COVERAGE)

    def test_covered_non_ood_is_evaluable(self) -> None:
        evaluable, reason = monitor.classify_prediction(ood=False, observed_feature_n=11)
        self.assertTrue(evaluable)
        self.assertEqual(reason, "")

    def test_observed_feature_count_ignores_missing_sentinel(self) -> None:
        snapshot = {
            "change_pct": 1.2,
            "volume_ratio": 2.0,
            "raw_score_current": None,
            "from_high_pct": "__MISSING__",
            "candidate_price": 1000,
        }
        self.assertEqual(monitor._observed_feature_n(snapshot), 3)


class AbstainDoesNotPollutePromotionTests(unittest.TestCase):
    """★오염 방지 회귀: abstain 관측이 승격 통계(matched_n·AUC·LCB)에 들어가면 안 된다."""

    def setUp(self) -> None:
        self.predictions = pd.DataFrame(
            [
                _prediction("000100", probability=0.40, evaluable=True, reason="", strategy="kr_momentum", observed=11),
                _prediction("000200", probability=0.20, evaluable=True, reason="", strategy="kr_momentum", observed=10),
                # 섹터플레이 abstain — outcome 원장에 행이 있어서 필터가 없으면 매칭된다.
                _prediction(
                    "055550", probability=0.4917, evaluable=False,
                    reason=monitor.ABSTAIN_UNSUPPORTED_COHORT, strategy="kr_sector_play", observed=0,
                ),
                _prediction(
                    "105560", probability=0.4917, evaluable=False,
                    reason=monitor.ABSTAIN_FEATURE_COVERAGE, strategy="kr_sector_play", observed=2,
                ),
            ]
        )
        self.outcomes = pd.DataFrame(
            [
                _outcome("000100", outcome_60m=1.5),
                _outcome("000200", outcome_60m=-0.8),
                _outcome("055550", outcome_60m=3.0),
                _outcome("105560", outcome_60m=2.5),
            ]
        )

    def _report(self) -> dict:
        return monitor.build_report(
            self.predictions, self.outcomes, market="KR", min_matched=60, min_sessions=20, now=NOW
        )

    def test_promotion_sample_excludes_abstain(self) -> None:
        report = self._report()
        self.assertEqual(report["observed_n"], 4)
        self.assertEqual(report["evaluable_n"], 2)
        self.assertEqual(report["abstain_n"], 2)
        # 승격 표본은 evaluable 2건만 — abstain 2건은 outcome이 있어도 들어가지 않는다.
        self.assertEqual(report["matched_n"], 2)

    def test_abstain_reasons_and_strategy_are_preserved(self) -> None:
        report = self._report()
        self.assertEqual(
            report["abstain_by_reason"],
            {monitor.ABSTAIN_UNSUPPORTED_COHORT: 1, monitor.ABSTAIN_FEATURE_COVERAGE: 1},
        )
        self.assertEqual(report["abstain_by_strategy"], {"kr_sector_play": 2})

    def test_coverage_debt_counts_what_would_have_polluted(self) -> None:
        # abstain 2건 모두 outcome과 매칭 가능 → 필터가 없었다면 승격 통계에 섞였을 건수.
        report = self._report()
        self.assertEqual(report["coverage_debt"]["abstain_matchable_n"], 2)

    def test_unmatched_matured_is_evaluable_based(self) -> None:
        # abstain 관측이 unmatched로 잡혀 "표본 유실"처럼 보이면 안 된다.
        report = self._report()
        self.assertEqual(report["matured_observed_n"], 4)
        self.assertEqual(report["matured_evaluable_n"], 2)
        self.assertEqual(report["unmatched_matured_n"], 0)

    def test_abstain_only_ledger_yields_zero_evaluable(self) -> None:
        # 2026-07-13 실제 상태: 13건 전부 sector_play abstain → 승격 표본 0.
        self.predictions = self.predictions[~self.predictions["evaluable"]].copy()
        report = self._report()
        self.assertEqual(report["evaluable_n"], 0)
        self.assertEqual(report["matched_n"], 0)
        self.assertFalse(report["promotion_eligible_forward"])


if __name__ == "__main__":
    unittest.main()
