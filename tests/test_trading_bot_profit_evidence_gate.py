from __future__ import annotations

from datetime import datetime, timezone
import os
from unittest.mock import patch

from trading_bot import TradingBot


def _bot() -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot._is_order_allowed_now = lambda market: True
    bot._in_entry_blackout = lambda market: False
    bot._daily_stop_cluster_state = lambda market, ticker="": {}
    bot._analyst_new_buy_block_state = lambda market: {}
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
