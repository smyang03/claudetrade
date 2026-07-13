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


def policy_viability(portable: dict[str, Any], *, market: str) -> dict[str, Any]:
    """이 아티팩트의 정책이 실전에서 발화할 수 있는가.

    확률 캘리브레이터는 IsotonicRegression(out_of_bounds="clip")이라 출력이 학습 y의 최댓값으로
    클립된다. 그 상한이 게이트 임계(PROFIT_EVIDENCE_MIN_PROB)보다 낮으면 어떤 입력에도
    p >= 임계가 성립하지 않는다 → selection 0건 → validation_net_lcb 계산 불가 →
    promotion_eligible_backtest 영구 False. 즉 배포해도 "죽은 모델"이다.
    (2026-07-13 실측: KR 상한 0.4917 / US 0.2975 < 임계 0.55 → selected_n=0인 채 조용히 배포됐다.)
    """
    metadata = dict(portable.get("metadata") or {})
    hurdle = float(
        os.getenv(f"PROFIT_EVIDENCE_MIN_PROB_{market}", os.getenv("PROFIT_EVIDENCE_MIN_PROB", "0.55"))
    )
    y_values = list((portable.get("probability_calibrator") or {}).get("y") or [])
    ceiling = float(max(y_values)) if y_values else 0.0
    selected_n = int(metadata.get("validation_selected_n") or 0)
    blockers: list[str] = []
    if ceiling < hurdle:
        blockers.append("calibrated_probability_ceiling_below_hurdle")
    if selected_n <= 0:
        blockers.append("validation_policy_selects_nothing")
    return {
        "market": market,
        "probability_hurdle": hurdle,
        "calibrated_probability_ceiling": ceiling,
        "validation_selected_n": selected_n,
        "policy_can_fire": not blockers,
        "blockers": blockers,
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
    parser.add_argument(
        "--allow-dead-policy",
        action="store_true",
        help="정책이 발화 불가(selected_n=0 또는 확률 상한<임계)여도 아티팩트를 기록한다. 연구용.",
    )
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
    dead_markets: list[str] = []
    for market, frame in datasets.items():
        artifact, metadata = train_market(frame, market=market, seed=args.seed)
        research_output = output_dir / f"profit_path_{market}.joblib"
        runtime_output = output_dir / f"profit_path_{market}.json"
        portable = _portable_artifact(artifact)
        viability = policy_viability(portable, market=market)
        summaries[market] = {
            "artifact": str(runtime_output),
            "research_artifact": str(research_output),
            "runtime_format": "portable_linear_v1",
            "policy_viability": viability,
            **metadata,
        }
        # fail-fast: 발화 불가 정책을 런타임에 배포하지 않는다. 조용히 배포되면
        # 게이트가 영구 abstain하고, enforce로 켜는 순간 매수가 100% 차단된다.
        if not viability["policy_can_fire"] and not args.allow_dead_policy:
            dead_markets.append(market)
            summaries[market]["deployed"] = False
            summaries[market]["skip_reason"] = "dead_policy_not_deployed"
            continue
        _atomic_dump(artifact, research_output)
        _atomic_json_dump(portable, runtime_output)
        summaries[market]["deployed"] = True
    print(json.dumps(summaries, ensure_ascii=False, indent=2, sort_keys=True))
    if dead_markets:
        print(
            "[FAIL] 정책 발화 불가로 배포 중단: "
            + ", ".join(
                f"{market}(blockers={summaries[market]['policy_viability']['blockers']}, "
                f"ceiling={summaries[market]['policy_viability']['calibrated_probability_ceiling']:.4f} < "
                f"hurdle={summaries[market]['policy_viability']['probability_hurdle']:.2f})"
                for market in dead_markets
            ),
            file=sys.stderr,
        )
        print(
            "[HINT] 라벨/임계 재설계 없이 재학습해도 같은 결과가 나온다. "
            "연구용으로 강제 기록하려면 --allow-dead-policy.",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
