from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.profit_evidence_db_replay import _load_start_env  # noqa: E402
from tools.profit_evidence_path_walkforward import (  # noqa: E402
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    _ood_mask,
    _policy_first,
    _preprocessor,
    build_path_dataset,
)
from tools.profit_evidence_walkforward import (  # noqa: E402
    _bootstrap_lcb,
    _calibration_uncertainty,
    _ece,
    _psi,
)


def _cost(market: str) -> float:
    return float(os.getenv(f"PROFIT_EVIDENCE_MIN_COST_PCT_{market}", "0.50" if market == "US" else "0.21"))


def _labels(frame: pd.DataFrame, market: str) -> np.ndarray:
    cost = _cost(market)
    min_net = float(os.getenv("PROFIT_EVIDENCE_MIN_EXPECTED_NET_PCT", "0.25") or 0.25)
    max_stop = float(os.getenv(f"PROFIT_PATH_LABEL_MAX_MAE_PCT_{market}", "2.5") or 2.5)
    net = frame["outcome_60m_pct"].to_numpy(dtype=float) - cost
    mae_ok = frame["max_drawdown_60m_pct"].to_numpy(dtype=float) > -max_stop
    return ((net >= min_net) & mae_ok).astype(int)


def _numeric_bounds(train: pd.DataFrame) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    for column in NUMERIC_FEATURES:
        values = pd.to_numeric(train[column], errors="coerce").dropna()
        if len(values) < 50:
            continue
        output[column] = {
            "low": float(values.quantile(0.005)),
            "high": float(values.quantile(0.995)),
        }
    return output


def train_market(frame: pd.DataFrame, *, market: str, seed: int) -> tuple[dict[str, Any], dict[str, Any]]:
    dates = sorted(str(value) for value in frame["session_date"].dropna().unique())
    if len(dates) < 10:
        raise ValueError(f"{market}: insufficient dates={len(dates)}")
    purge_set = dates[-1:]
    validation_set = dates[-4:-1]
    calibration_set = dates[-7:-4]
    train_set = dates[:-7]
    train = frame[frame["session_date"].isin(train_set)].copy()
    calibration = frame[frame["session_date"].isin(calibration_set)].copy()
    validation = frame[frame["session_date"].isin(validation_set)].copy()
    features = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    y_train, y_cal, y_val = _labels(train, market), _labels(calibration, market), _labels(validation, market)
    if any(len(np.unique(values)) < 2 for values in (y_train, y_cal, y_val)):
        raise ValueError(f"{market}: single-class split")

    classifier = Pipeline(
        [
            ("pre", _preprocessor()),
            (
                "model",
                SGDClassifier(
                    loss="log",
                    penalty="elasticnet",
                    alpha=0.0005,
                    l1_ratio=0.10,
                    class_weight="balanced",
                    max_iter=2000,
                    tol=1e-4,
                    random_state=seed,
                ),
            ),
        ]
    )
    classifier.fit(train[features], y_train)
    raw_cal = classifier.predict_proba(calibration[features])[:, 1]
    raw_val = classifier.predict_proba(validation[features])[:, 1]
    probability_calibrator = IsotonicRegression(out_of_bounds="clip").fit(raw_cal, y_cal)
    return_calibrator = IsotonicRegression(out_of_bounds="clip", increasing=True).fit(
        raw_cal, calibration["outcome_60m_pct"].to_numpy(dtype=float)
    )
    prob_cal = probability_calibrator.transform(raw_cal)
    prob_val = probability_calibrator.transform(raw_val)
    gross_val = return_calibrator.transform(raw_val)
    cost = _cost(market)
    min_net = float(os.getenv("PROFIT_EVIDENCE_MIN_EXPECTED_NET_PCT", "0.25") or 0.25)
    uncertainty_val = _calibration_uncertainty(y_cal, prob_cal, prob_val)
    ood_val = _ood_mask(train, validation)
    selection = (prob_val >= 0.55) & ((gross_val - cost) >= min_net) & (uncertainty_val <= 0.25) & ~ood_val
    validation_policy = _policy_first(validation.assign(_pred_net=gross_val - cost), selection)
    validation_net = validation_policy["outcome_60m_pct"].to_numpy(dtype=float) - cost
    net_lcb = _bootstrap_lcb(validation_net, seed=seed + 1)
    auc = float(roc_auc_score(y_val, raw_val))
    ece = float(_ece(y_val, prob_val))
    brier = float(brier_score_loss(y_val, prob_val))
    psi_values = {
        column: _psi(train[column], validation[column])
        for column in ("change_pct", "ret_5m_pct", "volume_ratio_open", "vwap_distance_pct")
    }
    max_psi = max(psi_values.values(), default=0.0)
    promotion_eligible = bool(
        len(validation_policy) >= 20
        and auc >= 0.52
        and ece <= 0.10
        and net_lcb > 0.0
        and max_psi <= 0.25
    )
    version = f"profit_path_shadow_{market}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    metadata = {
        "schema_version": "profit_path_model_v1",
        "model_version": version,
        "market": market,
        "model_state": "SHADOW",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_dates": [train_set[0], train_set[-1]],
        "calibration_dates": [calibration_set[0], calibration_set[-1]],
        "validation_dates": [validation_set[0], validation_set[-1]],
        "purged_dates": purge_set,
        "train_n": int(len(train)),
        "calibration_n": int(len(calibration)),
        "validation_n": int(len(validation)),
        "validation_selected_n": int(len(validation_policy)),
        "validation_auc": auc,
        "calibration_ece": ece,
        "brier": brier,
        "validation_net_lcb_pct": net_lcb if math.isfinite(net_lcb) else None,
        "psi": psi_values,
        "max_psi": max_psi,
        "drift_state": "healthy" if max_psi <= 0.25 else "degraded",
        "promotion_eligible_backtest": promotion_eligible,
        "cost_pct": cost,
        "min_expected_net_pct": min_net,
        "features": features,
    }
    artifact = {
        "metadata": metadata,
        "classifier": classifier,
        "probability_calibrator": probability_calibrator,
        "return_calibrator": return_calibrator,
        "calibration_probability": np.asarray(prob_cal, dtype=float),
        "calibration_labels": np.asarray(y_cal, dtype=int),
        "numeric_bounds": _numeric_bounds(train),
    }
    return artifact, metadata


def _atomic_dump(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    joblib.dump(payload, temporary)
    os.replace(temporary, path)


def _portable_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    pipeline = artifact["classifier"]
    pre = pipeline.named_steps["pre"]
    model = pipeline.named_steps["model"]
    numeric = pre.named_transformers_["num"]
    categorical = pre.named_transformers_["cat"]
    numeric_imputer = numeric.named_steps["imputer"]
    scaler = numeric.named_steps["scale"]
    categorical_imputer = categorical.named_steps["imputer"]
    onehot = categorical.named_steps["onehot"]
    probability_calibrator = artifact["probability_calibrator"]
    return_calibrator = artifact["return_calibrator"]
    metadata = {**dict(artifact["metadata"]), "runtime_format": "portable_linear_v1"}
    return {
        "metadata": metadata,
        "portable_model": {
            "numeric": {
                "features": list(NUMERIC_FEATURES),
                "imputer_statistics": np.asarray(numeric_imputer.statistics_, dtype=float).tolist(),
                "indicator_features": np.asarray(numeric_imputer.indicator_.features_, dtype=int).tolist(),
                "scale_mean": np.asarray(scaler.mean_, dtype=float).tolist(),
                "scale": np.asarray(scaler.scale_, dtype=float).tolist(),
            },
            "categorical": {
                "features": list(CATEGORICAL_FEATURES),
                "imputer_statistics": [str(value) for value in categorical_imputer.statistics_],
                "categories": [[str(value) for value in values] for values in onehot.categories_],
            },
            "coefficients": np.asarray(model.coef_[0], dtype=float).tolist(),
            "intercept": float(model.intercept_[0]),
        },
        "probability_calibrator": {
            "x": np.asarray(probability_calibrator.X_thresholds_, dtype=float).tolist(),
            "y": np.asarray(probability_calibrator.y_thresholds_, dtype=float).tolist(),
        },
        "return_calibrator": {
            "x": np.asarray(return_calibrator.X_thresholds_, dtype=float).tolist(),
            "y": np.asarray(return_calibrator.y_thresholds_, dtype=float).tolist(),
        },
        "calibration_probability": np.asarray(artifact["calibration_probability"], dtype=float).tolist(),
        "calibration_labels": np.asarray(artifact["calibration_labels"], dtype=int).tolist(),
        "numeric_bounds": artifact["numeric_bounds"],
    }


def _atomic_json_dump(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train shadow-only counterfactual path profit models")
    parser.add_argument("--db", default=str(ROOT / "data" / "audit" / "candidate_audit.db"))
    parser.add_argument("--config", default=str(ROOT / "config" / "v2_start_config.json"))
    parser.add_argument("--markets", default="KR")
    parser.add_argument("--output-dir", default=str(ROOT / "state" / "models"))
    parser.add_argument("--seed", type=int, default=20260710)
    args = parser.parse_args()
    for key, value in _load_start_env(Path(args.config)).items():
        os.environ.setdefault(key, value)
    markets = [value.strip().upper() for value in args.markets.split(",") if value.strip()]
    con = sqlite3.connect(args.db)
    try:
        datasets = {market: build_path_dataset(con, market)[0] for market in markets}
    finally:
        con.close()
    summaries: dict[str, Any] = {}
    output_dir = Path(args.output_dir)
    for market, frame in datasets.items():
        artifact, metadata = train_market(frame, market=market, seed=args.seed)
        research_output = output_dir / f"profit_path_{market}.joblib"
        runtime_output = output_dir / f"profit_path_{market}.json"
        _atomic_dump(artifact, research_output)
        portable = _portable_artifact(artifact)
        _atomic_json_dump(portable, runtime_output)
        summaries[market] = {
            "artifact": str(runtime_output),
            "research_artifact": str(research_output),
            "runtime_format": "portable_linear_v1",
            **metadata,
        }
    print(json.dumps(summaries, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
