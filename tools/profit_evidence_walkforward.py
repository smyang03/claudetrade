from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.profit_evidence_gate import evaluate_profit_evidence  # noqa: E402
from tools.profit_evidence_db_replay import _load_start_env, _mode_override, _stats  # noqa: E402


NUMERIC_FEATURES = [
    "price",
    "change_pct",
    "volume_ratio",
    "turnover",
    "prompt_rank",
    "from_high_pct",
    "candidate_age_min",
    "news_score",
    "news_or_earnings_count",
    "raw_score_current",
]
CATEGORICAL_FEATURES = [
    "primary_bucket",
    "liquidity_bucket",
    "market_type",
    "recommended_strategy",
    "candidate_source",
]


def _dataset(con: sqlite3.Connection, market: str) -> pd.DataFrame:
    sql = """
    WITH prompt_rows AS (
      SELECT r.*,
             ROW_NUMBER() OVER (
               PARTITION BY r.market, r.session_date, r.ticker
               ORDER BY COALESCE(r.known_at, r.created_at), r.candidate_key
             ) AS rn
      FROM audit_candidate_rows r
      WHERE r.market=? AND r.session_date IS NOT NULL
        AND (r.actual_prompt_included=1 OR r.final_prompt_included=1 OR r.in_prompt=1)
    ), daily AS (
      SELECT candidate_key, return_pct, max_runup_pct, max_drawdown_pct
      FROM audit_candidate_outcomes
      WHERE horizon_min=1440 AND status='daily_forward'
    )
    SELECT p.market, p.session_date, p.known_at, p.created_at, p.ticker,
           p.price, p.change_pct, p.volume_ratio, p.turnover,
           COALESCE(p.actual_prompt_rank, p.prompt_rank_after_trim, p.prompt_rank) AS prompt_rank,
           p.from_high_pct, p.candidate_age_min, p.news_score, p.news_or_earnings_count,
           p.raw_score_current, p.primary_bucket, p.liquidity_bucket, p.market_type,
           p.recommended_strategy, p.candidate_source,
           d.return_pct, d.max_runup_pct, d.max_drawdown_pct
    FROM prompt_rows p JOIN daily d ON d.candidate_key=p.candidate_key
    WHERE p.rn=1
    ORDER BY p.session_date, p.ticker
    """
    frame = pd.read_sql_query(sql, con, params=(market,))
    for column in NUMERIC_FEATURES + ["return_pct", "max_runup_pct", "max_drawdown_pct"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in CATEGORICAL_FEATURES:
        frame[column] = frame[column].fillna("__MISSING__").astype(str)
    return frame


def _preprocessor() -> ColumnTransformer:
    return ColumnTransformer(
        [
            (
                "num",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
                        ("scale", StandardScaler()),
                    ]
                ),
                NUMERIC_FEATURES,
            ),
            (
                "cat",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore", sparse=False)),
                    ]
                ),
                CATEGORICAL_FEATURES,
            ),
        ],
        sparse_threshold=0.0,
    )


def _ece(y: np.ndarray, probability: np.ndarray, bins: int = 10) -> float:
    if len(y) <= 0:
        return 1.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = float(len(y))
    value = 0.0
    for idx in range(bins):
        right_closed = idx == bins - 1
        mask = (probability >= edges[idx]) & (
            probability <= edges[idx + 1] if right_closed else probability < edges[idx + 1]
        )
        if not mask.any():
            continue
        value += float(mask.sum()) / total * abs(float(y[mask].mean()) - float(probability[mask].mean()))
    return float(value)


def _bootstrap_lcb(values: np.ndarray, *, seed: int, samples: int = 500) -> float:
    clean = np.asarray(values, dtype=float)
    clean = clean[np.isfinite(clean)]
    if len(clean) <= 1:
        return float("-inf")
    rng = np.random.default_rng(seed)
    means = np.empty(samples, dtype=float)
    for idx in range(samples):
        means[idx] = rng.choice(clean, size=len(clean), replace=True).mean()
    return float(np.quantile(means, 0.025))


def _psi(train: pd.Series, validation: pd.Series, bins: int = 8) -> float:
    left = pd.to_numeric(train, errors="coerce").dropna().to_numpy(dtype=float)
    right = pd.to_numeric(validation, errors="coerce").dropna().to_numpy(dtype=float)
    if len(left) < 20 or len(right) < 20:
        return 0.0
    edges = np.unique(np.quantile(left, np.linspace(0.0, 1.0, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    left_hist = np.histogram(left, bins=edges)[0].astype(float)
    right_hist = np.histogram(right, bins=edges)[0].astype(float)
    left_rate = np.maximum(left_hist / max(left_hist.sum(), 1.0), 1e-6)
    right_rate = np.maximum(right_hist / max(right_hist.sum(), 1.0), 1e-6)
    return float(np.sum((right_rate - left_rate) * np.log(right_rate / left_rate)))


def _ood_mask(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    outlier_count = np.zeros(len(test), dtype=int)
    observed_count = np.zeros(len(test), dtype=int)
    for column in NUMERIC_FEATURES:
        train_values = pd.to_numeric(train[column], errors="coerce").dropna()
        test_values = pd.to_numeric(test[column], errors="coerce")
        if len(train_values) < 20:
            continue
        low, high = train_values.quantile([0.005, 0.995]).tolist()
        observed = test_values.notna().to_numpy()
        observed_count += observed.astype(int)
        outlier_count += ((test_values < low) | (test_values > high)).fillna(False).to_numpy().astype(int)
    return (outlier_count >= 3) | (observed_count < max(3, len(NUMERIC_FEATURES) // 2))


def _calibration_uncertainty(y: np.ndarray, probability: np.ndarray, test_probability: np.ndarray) -> np.ndarray:
    edges = np.linspace(0.0, 1.0, 11)
    output = np.ones(len(test_probability), dtype=float)
    for idx, value in enumerate(test_probability):
        bin_idx = min(9, max(0, int(np.searchsorted(edges, value, side="right") - 1)))
        mask = (probability >= edges[bin_idx]) & (
            probability <= edges[bin_idx + 1] if bin_idx == 9 else probability < edges[bin_idx + 1]
        )
        count = int(mask.sum())
        if count <= 0:
            continue
        hit = float(y[mask].mean())
        sampling = 1.96 * math.sqrt(max(hit * (1.0 - hit), 1e-6) / count)
        output[idx] = min(1.0, max(abs(hit - float(value)), sampling))
    return output


def _parse_known_at(value: Any) -> datetime:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _market_cost(market: str) -> float:
    key = f"PROFIT_EVIDENCE_MIN_COST_PCT_{market}"
    default = 0.50 if market == "US" else 0.21
    try:
        return float(os.getenv(key, str(default)) or default)
    except ValueError:
        return default


def _min_expected_net() -> float:
    try:
        return float(os.getenv("PROFIT_EVIDENCE_MIN_EXPECTED_NET_PCT", "0.25") or 0.25)
    except ValueError:
        return 0.25


def walk_forward_market(
    frame: pd.DataFrame,
    *,
    market: str,
    min_train_dates: int,
    calibration_dates: int,
    validation_dates: int,
    purge_dates: int,
    min_selected_validation: int,
    seed: int,
    classifier_kind: str,
) -> dict[str, Any]:
    dates = sorted(str(value) for value in frame["session_date"].dropna().unique())
    feature_columns = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    cost = _market_cost(market)
    min_expected_net = _min_expected_net()
    gross_hurdle = cost + min_expected_net
    test_records: list[dict[str, Any]] = []
    windows: list[dict[str, Any]] = []
    skipped_windows: Counter[str] = Counter()

    first_test_idx = min_train_dates + calibration_dates + validation_dates + purge_dates
    for test_idx in range(first_test_idx, len(dates)):
        test_date = dates[test_idx]
        available_end = test_idx - purge_dates
        available_dates = dates[:available_end]
        validation_set = available_dates[-validation_dates:]
        calibration_set = available_dates[-(calibration_dates + validation_dates):-validation_dates]
        train_set = available_dates[:-(calibration_dates + validation_dates)]
        train = frame[frame["session_date"].isin(train_set)].copy()
        calibration = frame[frame["session_date"].isin(calibration_set)].copy()
        validation = frame[frame["session_date"].isin(validation_set)].copy()
        test = frame[frame["session_date"] == test_date].copy()
        if len(train_set) < min_train_dates or len(train) < 200:
            skipped_windows["train_insufficient"] += 1
            continue
        if len(calibration) < 60 or len(validation) < 60 or len(test) <= 0:
            skipped_windows["calibration_validation_or_test_insufficient"] += 1
            continue

        y_train = (train["return_pct"].to_numpy(dtype=float) > gross_hurdle).astype(int)
        y_cal = (calibration["return_pct"].to_numpy(dtype=float) > gross_hurdle).astype(int)
        y_val = (validation["return_pct"].to_numpy(dtype=float) > gross_hurdle).astype(int)
        if len(np.unique(y_train)) < 2 or len(np.unique(y_cal)) < 2 or len(np.unique(y_val)) < 2:
            skipped_windows["single_class"] += 1
            continue

        preprocessing = _preprocessor()
        if classifier_kind == "random_forest":
            classifier_model = RandomForestClassifier(
                n_estimators=160,
                min_samples_leaf=20,
                max_features=0.7,
                class_weight="balanced",
                n_jobs=-1,
                random_state=seed,
            )
        else:
            classifier_model = LogisticRegression(
                max_iter=1000,
                class_weight="balanced",
                C=0.2,
                random_state=seed,
            )
        classifier = Pipeline([("pre", clone(preprocessing)), ("model", classifier_model)])
        regressor = Pipeline(
            [
                ("pre", clone(preprocessing)),
                (
                    "model",
                    RandomForestRegressor(
                        n_estimators=120,
                        min_samples_leaf=20,
                        max_features=0.7,
                        n_jobs=-1,
                        random_state=seed,
                    ),
                ),
            ]
        )
        classifier.fit(train[feature_columns], y_train)
        regressor.fit(train[feature_columns], train["return_pct"].to_numpy(dtype=float))
        raw_cal = classifier.predict_proba(calibration[feature_columns])[:, 1]
        raw_val = classifier.predict_proba(validation[feature_columns])[:, 1]
        raw_test = classifier.predict_proba(test[feature_columns])[:, 1]
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(raw_cal, y_cal)
        prob_cal = calibrator.transform(raw_cal)
        prob_val = calibrator.transform(raw_val)
        prob_test = calibrator.transform(raw_test)
        gross_val = regressor.predict(validation[feature_columns])
        gross_test = regressor.predict(test[feature_columns])
        net_val_pred = gross_val - cost
        net_test_pred = gross_test - cost
        uncertainty_val = _calibration_uncertainty(y_cal, prob_cal, prob_val)
        uncertainty_test = _calibration_uncertainty(y_cal, prob_cal, prob_test)

        auc = float(roc_auc_score(y_val, raw_val))
        ece = _ece(y_val, prob_val)
        brier = float(brier_score_loss(y_val, prob_val))
        validation_select = (prob_val >= 0.55) & (net_val_pred >= min_expected_net) & (uncertainty_val <= 0.25)
        validation_actual_net = validation["return_pct"].to_numpy(dtype=float)[validation_select] - cost
        net_lcb = _bootstrap_lcb(validation_actual_net, seed=seed + test_idx)
        psi_values = {
            column: _psi(train[column], validation[column])
            for column in ("change_pct", "volume_ratio", "from_high_pct", "raw_score_current")
        }
        max_psi = max(psi_values.values(), default=0.0)
        drift_state = "healthy" if max_psi <= 0.25 else "degraded"
        promoted = bool(
            len(validation) >= 60
            and int(validation_select.sum()) >= min_selected_validation
            and auc >= 0.52
            and ece <= 0.10
            and net_lcb > 0.0
            and drift_state == "healthy"
        )
        model_state = "PROBE" if promoted else "SHADOW"
        ood_test = _ood_mask(train, test)

        allowed_count = 0
        raw_candidate_count = 0
        raw_candidate_actual_net: list[float] = []
        reason_counts: Counter[str] = Counter()
        with _mode_override("enforce"):
            for row_idx, (_, row) in enumerate(test.iterrows()):
                known_at = row.get("known_at") or row.get("created_at")
                evidence = {
                    "schema_version": "profit_evidence_v1",
                    "model_version": f"wf_{market}_{test_date}",
                    "model_state": model_state,
                    "decision_ts": str(known_at or ""),
                    "p_target_before_stop_calibrated": float(prob_test[row_idx]),
                    "expected_gross_pct": float(gross_test[row_idx]),
                    "expected_cost_pct_p75": float(cost),
                    "expected_net_pct": float(net_test_pred[row_idx]),
                    "uncertainty": float(uncertainty_test[row_idx]),
                    "ood": bool(ood_test[row_idx]),
                    "drift_state": drift_state,
                    "validation_sample_n": int(len(validation)),
                    "validation_selected_n": int(validation_select.sum()),
                    "validation_auc": auc,
                    "validation_net_lcb_pct": net_lcb,
                    "calibration_ece": ece,
                }
                raw_candidate = bool(
                    prob_test[row_idx] >= 0.55
                    and net_test_pred[row_idx] >= min_expected_net
                    and uncertainty_test[row_idx] <= 0.25
                    and not bool(ood_test[row_idx])
                )
                raw_candidate_count += int(raw_candidate)
                if raw_candidate:
                    raw_candidate_actual_net.append(float(row["return_pct"] - cost))
                strategy = str(row.get("recommended_strategy") or row.get("primary_bucket") or "")
                decision = evaluate_profit_evidence(
                    market=market,
                    ticker=str(row.get("ticker") or ""),
                    strategy=strategy,
                    evidence=evidence,
                    evidence_source="purged_walk_forward",
                    now=_parse_known_at(known_at),
                )
                allowed_count += int(decision.allowed)
                for reason in decision.reasons:
                    reason_counts[reason] += 1
                test_records.append(
                    {
                        "market": market,
                        "session_date": test_date,
                        "ticker": str(row.get("ticker") or ""),
                        "strategy": strategy,
                        "allowed": bool(decision.allowed),
                        "raw_candidate_before_model_promotion": raw_candidate,
                        "actual_gross_pct": float(row["return_pct"]),
                        "actual_net_proxy_pct": float(row["return_pct"] - cost),
                        "predicted_net_pct": float(net_test_pred[row_idx]),
                        "probability": float(prob_test[row_idx]),
                        "model_state": model_state,
                        "ood": bool(ood_test[row_idx]),
                        "reasons": list(decision.reasons),
                    }
                )
        windows.append(
            {
                "test_date": test_date,
                "train_dates": [train_set[0], train_set[-1]],
                "calibration_dates": [calibration_set[0], calibration_set[-1]],
                "validation_dates": [validation_set[0], validation_set[-1]],
                "purged_dates": dates[available_end:test_idx],
                "train_n": int(len(train)),
                "calibration_n": int(len(calibration)),
                "validation_n": int(len(validation)),
                "test_n": int(len(test)),
                "validation_selected_n": int(validation_select.sum()),
                "validation_auc": round(auc, 6),
                "calibration_ece": round(ece, 6),
                "brier": round(brier, 6),
                "validation_net_lcb_pct": round(net_lcb, 6) if math.isfinite(net_lcb) else None,
                "max_psi": round(max_psi, 6),
                "psi": {key: round(value, 6) for key, value in psi_values.items()},
                "drift_state": drift_state,
                "model_state": model_state,
                "allowed_test_n": allowed_count,
                "raw_candidate_test_n": raw_candidate_count,
                "raw_candidate_test_net_proxy": _stats(raw_candidate_actual_net),
                "reason_counts": dict(reason_counts),
            }
        )

    allowed = [row for row in test_records if row["allowed"]]
    raw_candidates = [row for row in test_records if row["raw_candidate_before_model_promotion"]]
    allowed_net = [float(row["actual_net_proxy_pct"]) for row in allowed]
    raw_candidate_net = [float(row["actual_net_proxy_pct"]) for row in raw_candidates]
    baseline_net = [float(row["actual_net_proxy_pct"]) for row in test_records]
    sorted_allowed = sorted(allowed_net, reverse=True)
    by_month: dict[str, dict[str, Any]] = {}
    for month in sorted({str(row["session_date"])[:7] for row in test_records}):
        month_rows = [row for row in test_records if str(row["session_date"]).startswith(month)]
        month_allowed = [float(row["actual_net_proxy_pct"]) for row in month_rows if row["allowed"]]
        by_month[month] = {
            "test_n": len(month_rows),
            "allowed": _stats(month_allowed),
        }
    return {
        "market": market,
        "date_count": len(dates),
        "tested_windows": len(windows),
        "promoted_windows": sum(window["model_state"] == "PROBE" for window in windows),
        "skipped_windows": dict(skipped_windows),
        "test_rows": len(test_records),
        "allowed_rows": len(allowed),
        "raw_candidate_rows_before_model_promotion": len(raw_candidates),
        "coverage": round(len(allowed) / len(test_records), 6) if test_records else 0.0,
        "cost_pct": cost,
        "gross_hurdle_pct": gross_hurdle,
        "baseline_net_proxy": _stats(baseline_net),
        "allowed_net_proxy": _stats(allowed_net),
        "raw_candidate_net_proxy_before_model_promotion": _stats(raw_candidate_net),
        "allowed_net_ex_top3": _stats(sorted_allowed[3:] if len(sorted_allowed) > 3 else []),
        "allowed_net_lcb_pct": (
            round(_bootstrap_lcb(np.asarray(allowed_net), seed=seed + 9000), 6) if len(allowed_net) > 1 else None
        ),
        "by_month": by_month,
        "windows": windows,
    }


def run_walk_forward(
    db_path: Path,
    *,
    markets: list[str],
    min_train_dates: int = 8,
    calibration_dates: int = 3,
    validation_dates: int = 3,
    purge_dates: int = 1,
    min_selected_validation: int = 20,
    seed: int = 20260710,
    classifier_kind: str = "logistic",
) -> dict[str, Any]:
    con = sqlite3.connect(str(db_path))
    try:
        output = {
            market: walk_forward_market(
                _dataset(con, market),
                market=market,
                min_train_dates=min_train_dates,
                calibration_dates=calibration_dates,
                validation_dates=validation_dates,
                purge_dates=purge_dates,
                min_selected_validation=min_selected_validation,
                seed=seed,
                classifier_kind=classifier_kind,
            )
            for market in markets
        }
    finally:
        con.close()
    return {
        "db_path": str(db_path),
        "method": {
            "split": "expanding_train_then_calibration_fit_then_validation_then_1_session_purge_then_next_session_test",
            "min_train_dates": min_train_dates,
            "calibration_dates": calibration_dates,
            "validation_dates": validation_dates,
            "purge_dates": purge_dates,
            "min_selected_validation": min_selected_validation,
            "classifier": classifier_kind,
            "label": "forward_1d_return_above_cost_p75_plus_min_expected_net",
            "features": NUMERIC_FEATURES + CATEGORICAL_FEATURES,
            "excluded": ["candidate_quality_score", "trainer_prompt_score", "claude_action", "execution/fill fields"],
        },
        "markets": output,
        "limitations": [
            "forward_1d close return is not an exact target-before-stop or broker net label",
            "candidate audit columns may contain backfilled values unless their source pipeline preserves known_at",
            "the available history begins mainly in June 2026, so regime diversity is limited",
            "market-level models are used because strategy-level samples are currently too small",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Purged expanding walk-forward simulation for profit evidence")
    parser.add_argument("--db", default=str(ROOT / "data" / "audit" / "candidate_audit.db"))
    parser.add_argument("--config", default=str(ROOT / "config" / "v2_start_config.json"))
    parser.add_argument("--markets", default="KR,US")
    parser.add_argument("--min-train-dates", type=int, default=8)
    parser.add_argument("--calibration-dates", type=int, default=3)
    parser.add_argument("--validation-dates", type=int, default=3)
    parser.add_argument("--purge-dates", type=int, default=1)
    parser.add_argument("--min-selected-validation", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260710)
    parser.add_argument("--classifier", choices=("logistic", "random_forest"), default="logistic")
    args = parser.parse_args()
    for key, value in _load_start_env(Path(args.config)).items():
        os.environ.setdefault(key, value)
    markets = [value.strip().upper() for value in str(args.markets).split(",") if value.strip()]
    report = run_walk_forward(
        Path(args.db),
        markets=markets,
        min_train_dates=max(3, args.min_train_dates),
        calibration_dates=max(2, args.calibration_dates),
        validation_dates=max(2, args.validation_dates),
        purge_dates=max(1, args.purge_dates),
        min_selected_validation=max(1, args.min_selected_validation),
        seed=args.seed,
        classifier_kind=args.classifier,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
