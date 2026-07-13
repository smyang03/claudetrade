from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score


ROOT = Path(__file__).resolve().parents[1]

# runtime/profit_path_predictor.build_runtime_feature_row가 만드는 numeric 피처.
# _ood()가 "관측 numeric < 5"를 OOD로 보므로 같은 기준을 여기서도 쓴다.
NUMERIC_FEATURES = (
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
)
MIN_OBSERVED_FEATURES = 5

ABSTAIN_UNSUPPORTED_COHORT = "unsupported_cohort"
ABSTAIN_FEATURE_COVERAGE = "feature_coverage_insufficient"


def _observed_feature_n(feature_snapshot: Any) -> int:
    if not isinstance(feature_snapshot, dict):
        return 0
    observed = 0
    for key in NUMERIC_FEATURES:
        value = feature_snapshot.get(key)
        if value is None or value == "__MISSING__":
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            observed += 1
    return observed


def classify_prediction(*, ood: Any, observed_feature_n: int) -> tuple[bool, str]:
    """승격 표본 자격 판정.

    OOD 관측은 삭제하지 않는다. 예측을 낼 수는 있지만 그 값은 학습분포 밖의 상수에 가까워
    AUC/ECE/LCB에 섞이면 승격 통계를 오염시킨다. 그래서 보존하되 evaluable에서만 뺀다.
    - unsupported_cohort: 후보 피처가 하나도 없다(Tier2 섹터플레이처럼 스크리너 후보가 아닌 종목).
    - feature_coverage_insufficient: 피처가 일부 있으나 모델이 OOD로 판정했거나 관측 수가 부족하다.
    """
    if observed_feature_n <= 0:
        return False, ABSTAIN_UNSUPPORTED_COHORT
    if bool(ood) or observed_feature_n < MIN_OBSERVED_FEATURES:
        return False, ABSTAIN_FEATURE_COVERAGE
    return True, ""


def _load_predictions(con: sqlite3.Connection, market: str = "") -> pd.DataFrame:
    params: list[Any] = []
    where = "event_type='PROFIT_EVIDENCE_SHADOW'"
    if market:
        where += " AND market=?"
        params.append(market.upper())
    rows = con.execute(
        f"""
        SELECT event_id, market, session_date, ticker, occurred_at, payload_json
        FROM lifecycle_events WHERE {where} ORDER BY event_id
        """,
        params,
    ).fetchall()
    output: list[dict[str, Any]] = []
    for event_id, event_market, session_date, ticker, occurred_at, raw_payload in rows:
        try:
            payload = json.loads(raw_payload or "{}")
        except (TypeError, ValueError):
            continue
        evidence = payload.get("evidence") if isinstance(payload, dict) else None
        if not isinstance(evidence, dict) or not evidence.get("model_version"):
            continue
        observed_n = _observed_feature_n(evidence.get("feature_snapshot"))
        evaluable, abstain_reason = classify_prediction(
            ood=evidence.get("ood"), observed_feature_n=observed_n
        )
        output.append(
            {
                "event_id": event_id,
                "market": str(event_market).upper(),
                "session_date": str(session_date),
                "ticker": str(ticker).upper() if str(event_market).upper() == "US" else str(ticker),
                "prediction_ts": evidence.get("decision_ts") or occurred_at,
                "path_name": str(evidence.get("path_name") or "immediate"),
                "model_version": str(evidence.get("model_version") or ""),
                "probability": evidence.get("p_target_before_stop_calibrated"),
                "expected_net_pct": evidence.get("expected_net_pct"),
                "uncertainty": evidence.get("uncertainty"),
                "ood": evidence.get("ood"),
                "strategy": str(payload.get("strategy") or ""),
                "observed_feature_n": observed_n,
                "evaluable": evaluable,
                "abstain_reason": abstain_reason,
            }
        )
    frame = pd.DataFrame(output)
    if frame.empty:
        return frame
    frame["prediction_ts"] = pd.to_datetime(frame["prediction_ts"], utc=True, errors="coerce")
    for column in ("probability", "expected_net_pct", "uncertainty"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    # One immutable decision per minute/path is sufficient for the forward policy.
    frame["prediction_minute"] = frame["prediction_ts"].dt.floor("min")
    return frame.dropna(subset=["prediction_ts"]).drop_duplicates(
        ["market", "session_date", "ticker", "path_name", "model_version", "prediction_minute"],
        keep="first",
    )


def _load_outcomes(con: sqlite3.Connection, market: str = "") -> pd.DataFrame:
    params: list[Any] = []
    where = "outcome_60m_pct IS NOT NULL AND metadata_quality='runtime_authoritative'"
    if market:
        where += " AND market=?"
        params.append(market.upper())
    frame = pd.read_sql_query(
        f"""
        SELECT id AS path_id, market, session_date, ticker, known_at, path_name,
               outcome_60m_pct, max_drawdown_60m_pct
        FROM candidate_counterfactual_paths WHERE {where}
        """,
        con,
        params=params,
    )
    if frame.empty:
        return frame
    frame["market"] = frame["market"].astype(str).str.upper()
    frame["ticker_key"] = frame["ticker"].astype(str)
    us = frame["market"].eq("US")
    frame.loc[us, "ticker_key"] = frame.loc[us, "ticker_key"].str.upper()
    frame["outcome_ts"] = pd.to_datetime(frame["known_at"], utc=True, errors="coerce")
    frame["outcome_60m_pct"] = pd.to_numeric(frame["outcome_60m_pct"], errors="coerce")
    frame["max_drawdown_60m_pct"] = pd.to_numeric(frame["max_drawdown_60m_pct"], errors="coerce")
    return frame.dropna(subset=["outcome_ts", "outcome_60m_pct"])


def match_predictions(predictions: pd.DataFrame, outcomes: pd.DataFrame, *, tolerance_min: int = 10) -> pd.DataFrame:
    if predictions.empty or outcomes.empty:
        return pd.DataFrame()
    left = predictions.copy()
    left["ticker_key"] = left["ticker"].astype(str)
    us = left["market"].eq("US")
    left.loc[us, "ticker_key"] = left.loc[us, "ticker_key"].str.upper()
    keys = ["market", "session_date", "ticker_key", "path_name"]
    merged = left.merge(outcomes, on=keys, how="inner", suffixes=("", "_outcome"))
    if merged.empty:
        return merged
    merged["match_delta_sec"] = (merged["outcome_ts"] - merged["prediction_ts"]).abs().dt.total_seconds()
    merged = merged[merged["match_delta_sec"] <= max(1, tolerance_min) * 60].copy()
    if merged.empty:
        return merged
    return merged.sort_values(["event_id", "match_delta_sec", "path_id"]).drop_duplicates("event_id", keep="first")


def _ece(labels: np.ndarray, probability: np.ndarray, bins: int = 10) -> float | None:
    if len(labels) <= 0:
        return None
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = 0.0
    for idx in range(bins):
        mask = (probability >= edges[idx]) & (
            probability <= edges[idx + 1] if idx == bins - 1 else probability < edges[idx + 1]
        )
        if mask.any():
            total += float(mask.mean()) * abs(float(labels[mask].mean()) - float(probability[mask].mean()))
    return total


def _bootstrap_lcb(values: np.ndarray, seed: int = 20260710) -> float | None:
    values = values[np.isfinite(values)]
    if len(values) < 2:
        return None
    rng = np.random.default_rng(seed)
    means = np.mean(rng.choice(values, size=(2000, len(values)), replace=True), axis=1)
    return float(np.quantile(means, 0.05))


def summarize(matched: pd.DataFrame, *, min_matched: int = 60, min_sessions: int = 20) -> dict[str, Any]:
    if matched.empty:
        return {"matched_n": 0, "forward_sessions": 0, "promotion_eligible_forward": False}
    frame = matched.dropna(subset=["probability", "outcome_60m_pct"]).copy()
    if frame.empty:
        return {"matched_n": 0, "forward_sessions": 0, "promotion_eligible_forward": False}
    frame["cost_pct"] = np.where(frame["market"].eq("US"), 0.50, 0.21)
    frame["net_pct"] = frame["outcome_60m_pct"] - frame["cost_pct"]
    max_mae = np.where(frame["market"].eq("US"), 2.5, 2.5)
    labels = ((frame["net_pct"] >= 0.25) & (frame["max_drawdown_60m_pct"] > -max_mae)).astype(int).to_numpy()
    probability = frame["probability"].clip(0, 1).to_numpy(dtype=float)
    auc = float(roc_auc_score(labels, probability)) if len(np.unique(labels)) > 1 else None
    ece = _ece(labels, probability)
    lcb = _bootstrap_lcb(frame["net_pct"].to_numpy(dtype=float))
    positive = frame.loc[frame["net_pct"] > 0, "net_pct"].sum()
    negative = -frame.loc[frame["net_pct"] < 0, "net_pct"].sum()
    sessions = int(frame["session_date"].nunique())
    eligible = bool(
        len(frame) >= min_matched
        and sessions >= min_sessions
        and auc is not None
        and auc >= 0.52
        and ece is not None
        and ece <= 0.10
        and lcb is not None
        and lcb > 0.0
    )
    return {
        "matched_n": int(len(frame)),
        "forward_sessions": sessions,
        "mean_net_pct": float(frame["net_pct"].mean()),
        "win_rate": float((frame["net_pct"] > 0).mean()),
        "profit_factor": float(positive / negative) if negative > 0 else None,
        "auc": auc,
        "ece": ece,
        "net_lcb_pct": lcb,
        "promotion_eligible_forward": eligible,
        "promotion_requirements": {"min_matched": min_matched, "min_sessions": min_sessions},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure immutable profit-path shadow predictions against 60m outcomes")
    parser.add_argument("--event-db", default=str(ROOT / "data" / "v2_event_store.db"))
    parser.add_argument("--audit-db", default=str(ROOT / "data" / "audit" / "candidate_audit.db"))
    parser.add_argument("--market", default="KR")
    parser.add_argument("--tolerance-min", type=int, default=10)
    parser.add_argument("--min-matched", type=int, default=60)
    parser.add_argument("--min-sessions", type=int, default=20)
    args = parser.parse_args()
    event_path, audit_path = Path(args.event_db), Path(args.audit_db)
    if not event_path.exists() or not audit_path.exists():
        print(json.dumps({"ok": False, "reason": "database_missing", "event_db": str(event_path), "audit_db": str(audit_path)}, indent=2))
        return 2
    event_con, audit_con = sqlite3.connect(event_path), sqlite3.connect(audit_path)
    try:
        predictions = _load_predictions(event_con, args.market)
        outcomes = _load_outcomes(audit_con, args.market)
    finally:
        event_con.close()
        audit_con.close()
    report = build_report(
        predictions,
        outcomes,
        market=args.market,
        tolerance_min=args.tolerance_min,
        min_matched=args.min_matched,
        min_sessions=args.min_sessions,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    return 0


def build_report(
    predictions: pd.DataFrame,
    outcomes: pd.DataFrame,
    *,
    market: str,
    tolerance_min: int = 10,
    min_matched: int = 60,
    min_sessions: int = 20,
    now: pd.Timestamp | None = None,
) -> dict[str, Any]:
    now = now if now is not None else pd.Timestamp(datetime.now(timezone.utc))
    if predictions.empty:
        evaluable = abstained = predictions
    else:
        evaluable = predictions[predictions["evaluable"]]
        abstained = predictions[~predictions["evaluable"]]

    # 승격 통계(AUC/ECE/LCB/matched_n/sessions)는 evaluable 표본만 쓴다.
    # abstain 관측은 삭제하지 않고 커버리지 부채로 따로 센다.
    matched = match_predictions(evaluable, outcomes, tolerance_min=tolerance_min)
    matured_evaluable = (
        evaluable[evaluable["prediction_ts"] <= now - pd.Timedelta(minutes=60)]
        if not evaluable.empty
        else evaluable
    )
    matured_observed = (
        predictions[predictions["prediction_ts"] <= now - pd.Timedelta(minutes=60)]
        if not predictions.empty
        else predictions
    )
    # 필터가 없었다면 승격 통계에 섞였을 abstain 관측 = 오염 부채의 실제 크기.
    abstain_matchable = match_predictions(abstained, outcomes, tolerance_min=tolerance_min)

    report = {
        "ok": True,
        "market": str(market).upper(),
        "observed_n": int(len(predictions)),
        "evaluable_n": int(len(evaluable)),
        "abstain_n": int(len(abstained)),
        "abstain_by_reason": (
            {str(k): int(v) for k, v in abstained["abstain_reason"].value_counts().items()}
            if not abstained.empty
            else {}
        ),
        "abstain_by_strategy": (
            {str(k): int(v) for k, v in abstained["strategy"].value_counts().items()}
            if not abstained.empty
            else {}
        ),
        "coverage_debt": {
            "abstain_matchable_n": int(len(abstain_matchable)),
            "abstain_sessions": int(abstained["session_date"].nunique()) if not abstained.empty else 0,
        },
        "matured_observed_n": int(len(matured_observed)),
        "matured_evaluable_n": int(len(matured_evaluable)),
        "unmatched_matured_n": max(0, int(len(matured_evaluable) - len(matched))),
        # 하위호환: prediction_n은 관측 전체를 뜻한다(승격 표본이 아니다).
        "prediction_n": int(len(predictions)),
        **summarize(matched, min_matched=min_matched, min_sessions=min_sessions),
    }
    return report


if __name__ == "__main__":
    raise SystemExit(main())
