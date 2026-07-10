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
    matched = match_predictions(predictions, outcomes, tolerance_min=args.tolerance_min)
    now = pd.Timestamp(datetime.now(timezone.utc))
    matured = predictions[predictions["prediction_ts"] <= now - pd.Timedelta(minutes=60)] if not predictions.empty else predictions
    report = {
        "ok": True,
        "market": args.market.upper(),
        "prediction_n": int(len(predictions)),
        "matured_prediction_n": int(len(matured)),
        "unmatched_matured_n": max(0, int(len(matured) - len(matched))),
        **summarize(matched, min_matched=args.min_matched, min_sessions=args.min_sessions),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
