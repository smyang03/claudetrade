from __future__ import annotations

import unittest

from tools import sync_v2_learning_performance as sync
from tools import v2_forward_measurer as measurer


def _decision(decision_id: str, payload: dict) -> dict:
    import json

    return {
        "decision_id": decision_id,
        "market": "KR",
        "runtime_mode": "live",
        "session_date": "2026-07-13",
        "ticker": "105560",
        "payload_json": json.dumps(payload),
    }


class SyncShadowFilterTests(unittest.TestCase):
    """★계측 오염 차단 회귀.

    profit_evidence shadow 관측이 registry.register_trade_ready()를 타고 v2_decisions에
    실제 결정으로 등록된다(trading_bot.py:10534). 그런데 v2_learning_performance /
    v2_canonical_performance에는 shadow 컬럼도 payload도 없어서, sync를 넘는 순간 하류는
    실제 매수 결정과 구분할 방법이 사라진다(2026-07-13 실측: shadow 4건 유입).
    "미체결이라 안전"은 거짓 — filled=0인데 learning_allowed=1인 행이 이미 146건 있다.
    """

    def test_shadow_only_is_filtered(self) -> None:
        row = _decision("dec_shadow", {"shadow_only": True, "model_version": "v1"})
        self.assertTrue(sync._is_shadow_decision(row))

    def test_profit_evidence_registration_source_is_filtered(self) -> None:
        row = _decision("dec_shadow2", {"registration_source": "profit_evidence_shadow"})
        self.assertTrue(sync._is_shadow_decision(row))

    def test_real_decision_is_kept(self) -> None:
        row = _decision("dec_real", {"selection_meta": {}, "ticker_origin": "screener"})
        self.assertFalse(sync._is_shadow_decision(row))

    def test_empty_payload_is_kept(self) -> None:
        row = _decision("dec_empty", {})
        self.assertFalse(sync._is_shadow_decision(row))

    def test_missing_payload_column_is_kept(self) -> None:
        self.assertFalse(sync._is_shadow_decision({"decision_id": "dec_nopayload"}))
        self.assertFalse(sync._is_shadow_decision(None))


class ForwardMeasurerShadowFilterTests(unittest.TestCase):
    """forward-return 측정 모집단도 오염된다(FORWARD_PENDING_DATA 4건 실측)."""

    def test_shadow_only_is_filtered(self) -> None:
        self.assertTrue(measurer._is_shadow_decision({"payload": {"shadow_only": True}}))

    def test_registration_source_is_filtered(self) -> None:
        self.assertTrue(
            measurer._is_shadow_decision({"payload": {"registration_source": "profit_evidence_shadow"}})
        )

    def test_real_decision_is_kept(self) -> None:
        self.assertFalse(measurer._is_shadow_decision({"payload": {"strategy_hint": "momentum"}}))

    def test_no_payload_is_kept(self) -> None:
        self.assertFalse(measurer._is_shadow_decision({"decision_id": "dec_x"}))


class TrainServeSkewTests(unittest.TestCase):
    """★train/serve skew 봉합: profit_path가 학습한 market_open_elapsed_min이 추론측에 없었다.

    학습 소스(candidate_counterfactual_paths.metadata_json $.context) 335,739/335,739 = 100%
    추론 소스(post_open jsonl) 0/10,538 = 0%  → 상시 OOD 기여.
    """

    def test_snapshot_carries_market_open_elapsed_min(self) -> None:
        from runtime.post_open_features import build_post_open_snapshot

        snapshot = build_post_open_snapshot(
            market="US",
            ticker="NVDA",
            known_at="2026-07-13T23:00:00",
            anchor_at="2026-07-13T22:30:00",
            anchor_price=100.0,
            current_price=104.0,
            market_open_elapsed_min=31.5,
        ).to_dict()
        self.assertEqual(snapshot["market_open_elapsed_min"], 31.5)

    def test_predictor_reads_it_from_post_open(self) -> None:
        from runtime.profit_path_predictor import build_runtime_feature_row

        feature = build_runtime_feature_row(
            market="US",
            ticker="NVDA",
            strategy="momentum",
            context=None,
            sources=({"post_open_features": {"market_open_elapsed_min": 31.5}},),
        )
        self.assertEqual(feature["market_open_elapsed_min"], 31.5)

    def test_absent_stays_none(self) -> None:
        from runtime.post_open_features import build_post_open_snapshot

        snapshot = build_post_open_snapshot(
            market="KR",
            ticker="005930",
            known_at="2026-07-13T10:00:00",
            anchor_at="2026-07-13T09:00:00",
            anchor_price=100.0,
            current_price=101.0,
        ).to_dict()
        self.assertIsNone(snapshot["market_open_elapsed_min"])


if __name__ == "__main__":
    unittest.main()
