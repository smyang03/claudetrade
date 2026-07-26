from __future__ import annotations

from datetime import datetime, timezone
import os
from unittest.mock import patch

import pytest

import runtime.profit_evidence_gate as profit_evidence_gate
from trading_bot import TradingBot


@pytest.fixture(autouse=True)
def _isolate_live_profit_evidence_snapshot():
    """라이브 상태 결합 차단 — 테스트가 실행 위치에 따라 결과가 갈리면 안 된다.

    resolve_profit_evidence()는 명시 evidence가 없으면 실제 파일을 읽는다:
        state/profit_evidence_KR.json / state/profit_evidence_US.json
    저장소 루트에서 돌리면 실측 61KB짜리 KR 스냅샷에 005930이 들어 있어
    evidence_source가 'snapshot'으로 잡히고, shadow 예측기까지 도달하지 못한다.
    "stored evidence 없음"이라는 테스트 전제 자체가 라이브 상태 때문에 깨진 것이라
    스냅샷을 비워 격리한다. 스냅샷 경로를 쓰는 테스트가 새로 생겨도 같은 함정에
    빠지지 않도록 파일 전체에 적용한다.
    """
    with patch.object(profit_evidence_gate, "load_profit_evidence_snapshot", return_value={}):
        yield


def _bot() -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot._is_order_allowed_now = lambda market: True
    bot._in_entry_blackout = lambda market: False
    bot._daily_stop_cluster_state = lambda market, ticker="": {}
    # 코어 analyst 격리 정책이 ignore_direction_block을 넘긴다(trading_bot.py:10969).
    # kwargs를 받지 않으면 게이트 진입 전에 TypeError로 죽는다.
    bot._analyst_new_buy_block_state = lambda market, **kwargs: {}
    bot.v2_order_unknown = None
    bot._v2_order_unknown_block_state = lambda market, ticker: {}
    bot._broker_trust_level = lambda market: "trusted"
    bot.selection_meta = {"KR": {}, "US": {}}
    bot.today_judgment = {}
    bot._v2_record_lifecycle_event = lambda *args, **kwargs: None
    return bot


def _valid_evidence() -> dict:
    return {
        "profit_evidence": {
            "schema_version": "profit_evidence_v1",
            "model_version": "model_v1",
            "model_state": "PROBE",
            "decision_ts": datetime.now(timezone.utc).isoformat(),
            "p_target_before_stop_calibrated": 0.65,
            "expected_gross_pct": 1.20,
            "expected_cost_pct_p75": 0.55,
            "expected_net_pct": 0.60,
            "uncertainty": 0.15,
            "ood": False,
            "drift_state": "healthy",
            "validation_sample_n": 100,
            "validation_net_lcb_pct": 0.05,
            "calibration_ece": 0.05,
        }
    }


def test_common_new_buy_gate_shadow_allows_and_records_would_block() -> None:
    bot = _bot()
    with patch.dict(os.environ, {"PROFIT_EVIDENCE_GATE_MODE": "shadow"}, clear=False):
        state = bot._new_buy_block_state("US", "NVDA", "momentum")
    assert state["allowed"] is True
    assert state["details"]["profit_evidence_gate"]["would_block"] is True


def test_common_new_buy_gate_enforce_abstains_without_evidence() -> None:
    bot = _bot()
    with patch.dict(os.environ, {"PROFIT_EVIDENCE_GATE_MODE": "enforce"}, clear=False):
        state = bot._new_buy_block_state("US", "NVDA", "momentum")
    assert state["allowed"] is False
    assert state["reason"] == "PROFIT_EVIDENCE_ABSTAIN"


def test_common_new_buy_gate_enforce_accepts_valid_signal_evidence() -> None:
    bot = _bot()
    with patch.dict(os.environ, {"PROFIT_EVIDENCE_GATE_MODE": "enforce"}, clear=False):
        state = bot._new_buy_block_state(
            "US",
            "NVDA",
            "momentum",
            profit_evidence=_valid_evidence(),
        )
    assert state["allowed"] is True
    assert state["details"]["profit_evidence_gate"]["passed"] is True


def test_missing_stored_evidence_uses_shadow_path_predictor() -> None:
    bot = _bot()
    evidence = _valid_evidence()["profit_evidence"]
    evidence["model_state"] = "SHADOW"
    with patch.dict(os.environ, {"PROFIT_EVIDENCE_GATE_MODE": "shadow"}, clear=False), patch(
        "runtime.profit_path_predictor.predict_profit_path_evidence", return_value=evidence
    ):
        state = bot._new_buy_block_state("KR", "005930", "path_b", profit_evidence={"entry_price": 70000})
    decision = state["details"]["profit_evidence_gate"]
    assert state["allowed"] is True
    assert decision["evidence_source"] == "profit_path_shadow_model"
    assert decision["evidence"]["model_state"] == "SHADOW"
