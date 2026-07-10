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
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.profit_evidence_gate import evaluate_profit_evidence  # noqa: E402
from tools.profit_evidence_db_replay import _load_start_env, _mode_override, _stats  # noqa: E402
from tools.profit_evidence_walkforward import (  # noqa: E402
    _bootstrap_lcb,
    _calibration_uncertainty,
    _ece,
    _psi,
)


NUMERIC_FEATURES = [
    "candidate_price",
    "change_pct",
    "volume_ratio",
    "from_high_pct",
    "raw_score_current",
    "entry_delay_min",
    "entry_vs_candidate_pct",
    "market_open_elapsed_min",
    "ret_3m_pct",
    "ret_5m_pct",
    "ret_10m_pct",
    "ret_30m_pct",
    "volume_ratio_open",
    "vwap_distance_pct",
    "pullback_from_high_pct",
]
CATEGORICAL_FEATURES = [
    "path_name",
    "primary_bucket",
    "recommended_strategy",
    "candidate_source",
    "liquidity_bucket",
    "market_type",
    "consensus_mode",
    "mode_family",
    "route_source",
    "data_quality",
]


def _candidate_frame(con: sqlite3.Connection, market: str) -> pd.DataFrame:
    frame = pd.read_sql_query(
        """
        SELECT candidate_key AS audit_candidate_key, market, session_date, ticker, known_at,
               actual_prompt_included, final_prompt_included, in_prompt,
               price AS candidate_price, change_pct, volume_ratio, from_high_pct,
               raw_score_current, primary_bucket, recommended_strategy, candidate_source,
               liquidity_bucket, market_type
        FROM audit_candidate_rows
        WHERE market=? AND known_at IS NOT NULL
        """,
        con,
        params=(market,),
    )
    frame["prompt_included"] = frame[
        ["actual_prompt_included", "final_prompt_included", "in_prompt"]
    ].fillna(0).max(axis=1)
    return frame


def _path_frame(con: sqlite3.Connection, market: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT id AS path_id, market, session_date, ticker, known_at, signal_time, trigger_time,
               path_name, trigger_reason, entry_price, entry_delay_min,
               outcome_60m_pct, outcome_close_pct, max_runup_60m_pct, max_drawdown_60m_pct,
               metadata_quality, label_source,
               json_extract(metadata_json, '$.consensus_mode') AS consensus_mode,
               json_extract(metadata_json, '$.mode_family') AS mode_family,
               json_extract(metadata_json, '$.context.route_source') AS route_source,
               json_extract(metadata_json, '$.context.data_quality') AS data_quality,
               json_extract(metadata_json, '$.context.market_open_elapsed_min') AS market_open_elapsed_min,
               COALESCE(
                 json_extract(metadata_json, '$.context.ret_3m_pct'),
                 json_extract(metadata_json, '$.context.evidence_pack.post_open_confirmation.ret_3m_pct')
               ) AS ret_3m_pct,
               COALESCE(
                 json_extract(metadata_json, '$.context.ret_5m_pct'),
                 json_extract(metadata_json, '$.context.evidence_pack.post_open_confirmation.ret_5m_pct')
               ) AS ret_5m_pct,
               COALESCE(
                 json_extract(metadata_json, '$.context.ret_10m_pct'),
                 json_extract(metadata_json, '$.context.evidence_pack.post_open_confirmation.ret_10m_pct')
               ) AS ret_10m_pct,
               COALESCE(
                 json_extract(metadata_json, '$.context.ret_30m_pct'),
                 json_extract(metadata_json, '$.context.evidence_pack.post_open_confirmation.ret_30m_pct')
               ) AS ret_30m_pct,
               COALESCE(
                 json_extract(metadata_json, '$.context.volume_ratio_open'),
                 json_extract(metadata_json, '$.context.evidence_pack.post_open_confirmation.volume_ratio_open')
               ) AS volume_ratio_open,
               COALESCE(
                 json_extract(metadata_json, '$.context.vwap_distance_pct'),
                 json_extract(metadata_json, '$.context.evidence_pack.post_open_confirmation.vwap_distance_pct')
               ) AS vwap_distance_pct,
               COALESCE(
                 json_extract(metadata_json, '$.context.pullback_from_high_pct'),
                 json_extract(metadata_json, '$.context.evidence_pack.post_open_confirmation.pullback_from_high_pct')
               ) AS pullback_from_high_pct
        FROM candidate_counterfactual_paths
        WHERE market=? AND entry_price IS NOT NULL AND outcome_60m_pct IS NOT NULL
          AND max_runup_60m_pct IS NOT NULL AND max_drawdown_60m_pct IS NOT NULL
          AND metadata_quality='runtime_authoritative'
        """,
        con,
        params=(market,),
    )


def build_path_dataset(con: sqlite3.Connection, market: str, *, tolerance_min: int = 2) -> tuple[pd.DataFrame, dict[str, Any]]:
    candidates = _candidate_frame(con, market)
    paths = _path_frame(con, market)
    for frame in (candidates, paths):
        frame["market"] = frame["market"].astype(str).str.upper()
        frame["ticker_key"] = frame["ticker"].astype(str)
        if market == "US":
            frame["ticker_key"] = frame["ticker_key"].str.upper()
        frame["known_ts"] = pd.to_datetime(frame["known_at"], utc=True, errors="coerce")
        frame["join_key"] = frame["market"] + "|" + frame["session_date"].astype(str) + "|" + frame["ticker_key"]
    candidates = candidates.dropna(subset=["known_ts"]).sort_values("known_ts")
    paths = paths.dropna(subset=["known_ts"]).sort_values("known_ts")
    candidate_columns = [
        "join_key",
        "known_ts",
        "audit_candidate_key",
        "prompt_included",
        "candidate_price",
        "change_pct",
        "volume_ratio",
        "from_high_pct",
        "raw_score_current",
        "primary_bucket",
        "recommended_strategy",
        "candidate_source",
        "liquidity_bucket",
        "market_type",
    ]
    candidate_side = candidates[candidate_columns].rename(columns={"known_ts": "candidate_known_ts"})
    merged = pd.merge_asof(
        paths,
        candidate_side.sort_values("candidate_known_ts"),
        left_on="known_ts",
        right_on="candidate_known_ts",
        by="join_key",
        direction="backward",
        tolerance=pd.Timedelta(minutes=tolerance_min),
    )
    matched = merged["audit_candidate_key"].notna()
    prompt_matched = matched & merged["prompt_included"].fillna(0).gt(0)
    merged = merged[prompt_matched].copy()
    merged["join_delta_sec"] = (merged["known_ts"] - merged["candidate_known_ts"]).dt.total_seconds()
    merged["entry_ts"] = pd.to_datetime(
        merged["trigger_time"].fillna(merged["signal_time"]).fillna(merged["known_at"]),
        utc=True,
        errors="coerce",
    )
    merged["entry_ts"] = merged["entry_ts"].fillna(merged["known_ts"])
    merged["entry_vs_candidate_pct"] = np.where(
        pd.to_numeric(merged["candidate_price"], errors="coerce") > 0,
        (pd.to_numeric(merged["entry_price"], errors="coerce") / pd.to_numeric(merged["candidate_price"], errors="coerce") - 1.0) * 100.0,
        np.nan,
    )
    for column in NUMERIC_FEATURES + [
        "outcome_60m_pct",
        "outcome_close_pct",
        "max_runup_60m_pct",
        "max_drawdown_60m_pct",
    ]:
        merged[column] = pd.to_numeric(merged[column], errors="coerce")
    for column in CATEGORICAL_FEATURES:
        merged[column] = merged[column].fillna("__MISSING__").astype(str)
    coverage = {
        "path_rows": int(len(paths)),
        "backward_matched_rows": int(matched.sum()),
        "prompt_matched_rows": int(prompt_matched.sum()),
        "usable_rows": int(len(merged)),
        "backward_match_rate": round(float(matched.mean()), 6) if len(matched) else 0.0,
        "prompt_match_rate": round(float(prompt_matched.mean()), 6) if len(prompt_matched) else 0.0,
        "join_delta_sec": {
            "median": round(float(merged["join_delta_sec"].median()), 4) if len(merged) else None,
            "p95": round(float(merged["join_delta_sec"].quantile(0.95)), 4) if len(merged) else None,
            "max": round(float(merged["join_delta_sec"].max()), 4) if len(merged) else None,
        },
    }
    return merged, coverage


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
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                CATEGORICAL_FEATURES,
            ),
        ],
        sparse_threshold=0.3,
    )


def _ood_mask(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    outliers = np.zeros(len(test), dtype=int)
    observed = np.zeros(len(test), dtype=int)
    for column in NUMERIC_FEATURES:
        left = pd.to_numeric(train[column], errors="coerce").dropna()
        right = pd.to_numeric(test[column], errors="coerce")
        if len(left) < 50:
            continue
        low, high = left.quantile([0.005, 0.995]).tolist()
        present = right.notna().to_numpy()
        observed += present.astype(int)
        outliers += ((right < low) | (right > high)).fillna(False).to_numpy().astype(int)
    return (outliers >= 4) | (observed < max(5, len(NUMERIC_FEATURES) // 2))


def _policy_first(rows: pd.DataFrame, mask: np.ndarray) -> pd.DataFrame:
    selected = rows.loc[np.asarray(mask, dtype=bool)].copy()
    if selected.empty:
        return selected
    return selected.sort_values(["session_date", "ticker_key", "entry_ts", "path_id"]).drop_duplicates(
        ["session_date", "ticker_key"], keep="first"
    )


def _known_at(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def walk_forward_path_market(
    frame: pd.DataFrame,
    *,
    market: str,
    min_train_dates: int = 8,
    calibration_dates: int = 3,
    validation_dates: int = 3,
    purge_dates: int = 1,
    min_selected_validation: int = 20,
    seed: int = 20260710,
) -> dict[str, Any]:
    dates = sorted(str(value) for value in frame["session_date"].dropna().unique())
    features = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    cost = float(os.getenv(f"PROFIT_EVIDENCE_MIN_COST_PCT_{market}", "0.50" if market == "US" else "0.21"))
    min_net = float(os.getenv("PROFIT_EVIDENCE_MIN_EXPECTED_NET_PCT", "0.25") or 0.25)
    max_stop = float(os.getenv(f"PROFIT_PATH_LABEL_MAX_MAE_PCT_{market}", "2.5") or 2.5)
    records: list[dict[str, Any]] = []
    windows: list[dict[str, Any]] = []

    first_test_idx = min_train_dates + calibration_dates + validation_dates + purge_dates
    for test_idx in range(first_test_idx, len(dates)):
        test_date = dates[test_idx]
        available_end = test_idx - purge_dates
        available = dates[:available_end]
        validation_set = available[-validation_dates:]
        calibration_set = available[-(calibration_dates + validation_dates):-validation_dates]
        train_set = available[:-(calibration_dates + validation_dates)]
        train = frame[frame["session_date"].isin(train_set)].copy()
        calibration = frame[frame["session_date"].isin(calibration_set)].copy()
        validation = frame[frame["session_date"].isin(validation_set)].copy()
        test = frame[frame["session_date"] == test_date].copy()
        if min(len(train), len(calibration), len(validation), len(test)) <= 0:
            continue

        def labels(rows: pd.DataFrame) -> np.ndarray:
            net = rows["outcome_60m_pct"].to_numpy(dtype=float) - cost
            mae_ok = rows["max_drawdown_60m_pct"].to_numpy(dtype=float) > -max_stop
            return ((net >= min_net) & mae_ok).astype(int)

        y_train, y_cal, y_val = labels(train), labels(calibration), labels(validation)
        if any(len(np.unique(values)) < 2 for values in (y_train, y_cal, y_val)):
            continue
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
        raw_test = classifier.predict_proba(test[features])[:, 1]
        probability_calibrator = IsotonicRegression(out_of_bounds="clip").fit(raw_cal, y_cal)
        return_calibrator = IsotonicRegression(out_of_bounds="clip", increasing=True).fit(
            raw_cal, calibration["outcome_60m_pct"].to_numpy(dtype=float)
        )
        prob_cal = probability_calibrator.transform(raw_cal)
        prob_val = probability_calibrator.transform(raw_val)
        prob_test = probability_calibrator.transform(raw_test)
        gross_val = return_calibrator.transform(raw_val)
        gross_test = return_calibrator.transform(raw_test)
        net_val_pred = gross_val - cost
        net_test_pred = gross_test - cost
        uncertainty_val = _calibration_uncertainty(y_cal, prob_cal, prob_val)
        uncertainty_test = _calibration_uncertainty(y_cal, prob_cal, prob_test)
        ood_val = _ood_mask(train, validation)
        ood_test = _ood_mask(train, test)

        auc = float(roc_auc_score(y_val, raw_val))
        ece = float(_ece(y_val, prob_val))
        brier = float(brier_score_loss(y_val, prob_val))
        raw_val_mask = (prob_val >= 0.55) & (net_val_pred >= min_net) & (uncertainty_val <= 0.25) & ~ood_val
        validation_policy = _policy_first(validation.assign(_pred_net=net_val_pred), raw_val_mask)
        validation_net = validation_policy["outcome_60m_pct"].to_numpy(dtype=float) - cost
        net_lcb = _bootstrap_lcb(validation_net, seed=seed + test_idx)
        psi_values = {
            column: _psi(train[column], validation[column])
            for column in ("change_pct", "ret_5m_pct", "volume_ratio_open", "vwap_distance_pct")
        }
        max_psi = max(psi_values.values(), default=0.0)
        drift_state = "healthy" if max_psi <= 0.25 else "degraded"
        promoted = bool(
            len(validation_policy) >= min_selected_validation
            and auc >= 0.52
            and ece <= 0.10
            and net_lcb > 0.0
            and drift_state == "healthy"
        )
        model_state = "PROBE" if promoted else "SHADOW"

        test = test.assign(
            _raw_score=raw_test,
            _prob=prob_test,
            _gross=gross_test,
            _net_pred=net_test_pred,
            _uncertainty=uncertainty_test,
            _ood=ood_test,
        )
        raw_test_mask = (prob_test >= 0.55) & (net_test_pred >= min_net) & (uncertainty_test <= 0.25) & ~ood_test
        raw_policy_ids = set(_policy_first(test, raw_test_mask)["path_id"].astype(int).tolist())
        gate_allowed_ids: set[int] = set()
        reason_counts: Counter[str] = Counter()
        with _mode_override("enforce"):
            decisions: list[tuple[int, bool]] = []
            for row_idx, (_, row) in enumerate(test.iterrows()):
                evidence = {
                    "schema_version": "profit_evidence_v1",
                    "model_version": f"path_wf_{market}_{test_date}",
                    "model_state": model_state,
                    "decision_ts": str(row.get("known_at") or ""),
                    "p_target_before_stop_calibrated": float(prob_test[row_idx]),
                    "expected_gross_pct": float(gross_test[row_idx]),
                    "expected_cost_pct_p75": cost,
                    "expected_net_pct": float(net_test_pred[row_idx]),
                    "uncertainty": float(uncertainty_test[row_idx]),
                    "ood": bool(ood_test[row_idx]),
                    "drift_state": drift_state,
                    "validation_sample_n": int(len(validation)),
                    "validation_selected_n": int(len(validation_policy)),
                    "validation_auc": auc,
                    "validation_net_lcb_pct": net_lcb,
                    "calibration_ece": ece,
                }
                decision = evaluate_profit_evidence(
                    market=market,
                    ticker=str(row.get("ticker_key") or ""),
                    strategy="path_b" if str(row.get("recommended_strategy") or "") == "claude_price" else "path_a",
                    evidence=evidence,
                    evidence_source="counterfactual_path_walk_forward",
                    now=_known_at(row.get("known_at")),
                )
                decisions.append((int(row["path_id"]), bool(decision.allowed)))
                for reason in decision.reasons:
                    reason_counts[reason] += 1
            allowed_path_ids = {path_id for path_id, allowed in decisions if allowed}
            gate_policy = _policy_first(test, test["path_id"].isin(allowed_path_ids).to_numpy())
            gate_allowed_ids = set(gate_policy["path_id"].astype(int).tolist())

        for _, row in test.iterrows():
            path_id = int(row["path_id"])
            records.append(
                {
                    "session_date": test_date,
                    "ticker": str(row["ticker_key"]),
                    "path_name": str(row["path_name"]),
                    "raw_selected": path_id in raw_policy_ids,
                    "allowed": path_id in gate_allowed_ids,
                    "probability": float(row["_prob"]),
                    "raw_rank_score": float(row["_raw_score"]),
                    "predicted_net_pct": float(row["_net_pred"]),
                    "actual_net60_pct": float(row["outcome_60m_pct"] - cost),
                }
            )
        raw_test_policy = test[test["path_id"].isin(raw_policy_ids)]
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
                "validation_policy_n": int(len(validation_policy)),
                "validation_auc": round(auc, 6),
                "calibration_ece": round(ece, 6),
                "brier": round(brier, 6),
                "validation_net_lcb_pct": round(net_lcb, 6) if math.isfinite(net_lcb) else None,
                "max_psi": round(max_psi, 6),
                "model_state": model_state,
                "raw_test_policy": _stats((raw_test_policy["outcome_60m_pct"] - cost).tolist()),
                "allowed_test_n": len(gate_allowed_ids),
                "reason_counts": dict(reason_counts),
            }
        )

    raw = [row for row in records if row["raw_selected"]]
    allowed = [row for row in records if row["allowed"]]
    raw_net = [float(row["actual_net60_pct"]) for row in raw]
    allowed_net = [float(row["actual_net60_pct"]) for row in allowed]
    path_stats = {
        path_name: _stats([float(row["actual_net60_pct"]) for row in raw if row["path_name"] == path_name])
        for path_name in sorted({row["path_name"] for row in raw})
    }
    rank_cohort_stats: dict[str, Any] = {}
    if records:
        record_frame = pd.DataFrame(records)
        best_path = (
            record_frame.sort_values(
                ["session_date", "ticker", "raw_rank_score", "predicted_net_pct"],
                ascending=[True, True, False, False],
            )
            .drop_duplicates(["session_date", "ticker"], keep="first")
        )
        for label, quantile in (("top_1pct", 0.99), ("top_5pct", 0.95), ("top_10pct", 0.90)):
            selected_parts = []
            for _date, group in best_path.groupby("session_date"):
                threshold = float(group["raw_rank_score"].quantile(quantile))
                selected_parts.append(group[group["raw_rank_score"] >= threshold])
            selected = pd.concat(selected_parts, ignore_index=True) if selected_parts else best_path.iloc[0:0]
            values = selected["actual_net60_pct"].astype(float).tolist()
            sorted_values = sorted(values, reverse=True)
            rank_cohort_stats[label] = {
                **_stats(values),
                "lcb_pct": round(_bootstrap_lcb(np.asarray(values), seed=seed + int(quantile * 1000)), 6)
                if len(values) > 1
                else None,
                "ex_top3": _stats(sorted_values[3:] if len(sorted_values) > 3 else []),
                "by_month": {
                    month: _stats(
                        selected.loc[selected["session_date"].astype(str).str.startswith(month), "actual_net60_pct"]
                        .astype(float)
                        .tolist()
                    )
                    for month in sorted({str(value)[:7] for value in selected["session_date"]})
                },
            }
    return {
        "market": market,
        "date_count": len(dates),
        "tested_windows": len(windows),
        "promoted_windows": sum(window["model_state"] == "PROBE" for window in windows),
        "test_path_rows": len(records),
        "raw_policy_rows": len(raw),
        "allowed_rows": len(allowed),
        "raw_policy_net60": _stats(raw_net),
        "allowed_net60": _stats(allowed_net),
        "raw_policy_net60_lcb_pct": round(_bootstrap_lcb(np.asarray(raw_net), seed=seed + 7000), 6) if len(raw_net) > 1 else None,
        "raw_policy_by_path": path_stats,
        "oos_rank_cohorts_best_path_per_ticker_day": rank_cohort_stats,
        "windows": windows,
    }


def run_path_walk_forward(db_path: Path, *, markets: list[str]) -> dict[str, Any]:
    con = sqlite3.connect(str(db_path))
    try:
        datasets = {market: build_path_dataset(con, market) for market in markets}
    finally:
        con.close()
    return {
        "db_path": str(db_path),
        "method": "candidate feature backward-asof<=2m + path 60m label + train/calibration/validation/purge/test",
        "features": NUMERIC_FEATURES + CATEGORICAL_FEATURES,
        "markets": {
            market: {
                "join_coverage": coverage,
                "result": walk_forward_path_market(frame, market=market),
            }
            for market, (frame, coverage) in datasets.items()
        },
        "limitations": [
            "MFE/MAE do not encode which barrier was hit first, so the label is conservative path quality rather than exact triple barrier",
            "counterfactual entry paths are observational and share candidates, so the policy takes only the first accepted path per ticker-day",
            "rank-cohort diagnostics choose the highest-scored observed path per ticker-day and are an optimistic ranking ceiling, not a live execution replay",
            "60m net subtracts configured cost floor but is not broker-realized account PnL",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Counterfactual-path profit evidence walk-forward")
    parser.add_argument("--db", default=str(ROOT / "data" / "audit" / "candidate_audit.db"))
    parser.add_argument("--config", default=str(ROOT / "config" / "v2_start_config.json"))
    parser.add_argument("--markets", default="KR,US")
    args = parser.parse_args()
    for key, value in _load_start_env(Path(args.config)).items():
        os.environ.setdefault(key, value)
    markets = [value.strip().upper() for value in args.markets.split(",") if value.strip()]
    report = run_path_walk_forward(Path(args.db), markets=markets)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
