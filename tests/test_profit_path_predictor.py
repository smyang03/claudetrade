from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pytest

from runtime.profit_path_predictor import _uncertainty, build_runtime_feature_row, predict_profit_path_evidence
from tools.profit_evidence_path_walkforward import CATEGORICAL_FEATURES, NUMERIC_FEATURES


def _context() -> dict:
    row = {column: 1.0 for column in NUMERIC_FEATURES}
    row.update({column: "known" for column in CATEGORICAL_FEATURES})
    row.update(
        {
            "price": 100.0,
            "entry_price": 101.0,
            "post_open_features": {
                "market_open_elapsed_min": 15,
                "ret_3m_pct": 0.1,
                "ret_5m_pct": 0.2,
                "ret_10m_pct": 0.3,
                "ret_30m_pct": 0.4,
                "volume_ratio_open": 1.5,
                "vwap_distance_pct": 0.2,
                "pullback_from_high_pct": -0.3,
                "data_quality": "CLEAN",
            },
        }
    )
    return row


def test_numpy_calibration_arrays_do_not_raise_truth_value_error() -> None:
    value = _uncertainty(
        {
            "calibration_probability": np.asarray([0.61, 0.64, 0.68]),
            "calibration_labels": np.asarray([1, 0, 1]),
        },
        0.65,
    )
    assert 0.0 <= value <= 1.0


def test_predictor_emits_shadow_contract_and_feature_snapshot() -> None:
    artifact = {
        "metadata": {
            "model_version": "shadow-v1",
            "features": NUMERIC_FEATURES + CATEGORICAL_FEATURES,
            "cost_pct": 0.21,
            "validation_n": 100,
            "validation_selected_n": 10,
            "validation_auc": 0.55,
            "validation_net_lcb_pct": -0.1,
            "calibration_ece": 0.05,
            "drift_state": "healthy",
            "runtime_format": "portable_linear_v1",
        },
        "portable_model": {
            "numeric": {
                "features": NUMERIC_FEATURES,
                "imputer_statistics": [0.0] * len(NUMERIC_FEATURES),
                "indicator_features": [],
                "scale_mean": [0.0] * len(NUMERIC_FEATURES),
                "scale": [1.0] * len(NUMERIC_FEATURES),
            },
            "categorical": {
                "features": CATEGORICAL_FEATURES,
                "imputer_statistics": ["known"] * len(CATEGORICAL_FEATURES),
                "categories": [["known"] for _ in CATEGORICAL_FEATURES],
            },
            "coefficients": [0.0] * (len(NUMERIC_FEATURES) + len(CATEGORICAL_FEATURES)),
            "intercept": 0.6190392084062235,
        },
        "probability_calibrator": {"x": [0.0, 1.0], "y": [0.60, 0.60]},
        "return_calibrator": {"x": [0.0, 1.0], "y": [0.80, 0.80]},
        "calibration_probability": np.asarray([0.55, 0.60, 0.65, 0.68]),
        "calibration_labels": np.asarray([0, 1, 1, 1]),
        "numeric_bounds": {column: {"low": -1000, "high": 1000} for column in NUMERIC_FEATURES},
    }
    with patch("runtime.profit_path_predictor._enabled", return_value=True), patch(
        "runtime.profit_path_predictor._load_artifact", return_value=(artifact, "model.joblib")
    ):
        evidence = predict_profit_path_evidence(
            market="KR", ticker="005930", strategy="path_b", context=_context()
        )
    assert evidence["schema_version"] == "profit_evidence_v1"
    assert evidence["model_state"] == "SHADOW"
    assert evidence["expected_net_pct"] == pytest.approx(0.59)
    assert evidence["path_name"] == "known"
    assert evidence["ood"] is False
    assert evidence["feature_snapshot"]["ret_5m_pct"] == 0.2


def test_path_b_runtime_feature_defaults_to_pullback_reclaim() -> None:
    feature = build_runtime_feature_row(
        market="KR",
        ticker="005930",
        strategy="path_b",
        context={"signal_reason": "zone_reentry", "entry_price": 100.0},
    )
    assert feature["path_name"] == "pullback_reclaim"
