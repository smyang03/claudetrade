from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.candidate_path_prediction_lab import (
    DEFAULT_AUDIT_DB,
    DEFAULT_PRICE_ROOT,
    FEATURE_GROUPS,
    STOP_PCT,
    _daily_top,
    _metric_summary,
    _multiclass_pipeline,
    _pipeline,
    _selection_stats,
    _tag,
    load_first_candidates,
    session_entry_floor,
    walk_forward,
    walk_forward_multistate,
)


DEFAULT_INPUT = ROOT / "data" / "analysis" / "candidate_path_labels_lag5_v1.csv"
DEFAULT_OUTPUT = ROOT / "reports" / "candidate_path_prediction_validation_20260716.json"
DEFAULT_LEDGER = ROOT / "reports" / "candidate_path_prediction_holdout_picks_20260716.csv"


def _prepare(frame: pd.DataFrame, *, market: str, target: float) -> tuple[pd.DataFrame, str, str, str]:
    prefix = f"h60_t{_tag(target)}_s{_tag(STOP_PCT)}"
    label = f"{prefix}_target_before_stop"
    outcome = f"{prefix}_outcome"
    net = f"{prefix}_policy_net_pct"
    subset = frame[(frame["market"] == market) & (frame["h60_label_available"] == 1)].copy()
    subset = subset.dropna(subset=[label, outcome, net])
    known_at = pd.to_datetime(subset["known_at"], utc=True, errors="coerce")
    entry_floor = pd.Series(
        [session_entry_floor(value, market=market, entry_lag_min=5) for value in subset["known_at"]],
        index=subset.index,
    )
    subset = subset[known_at.notna() & entry_floor.notna() & (known_at <= entry_floor)].copy()
    return subset, label, outcome, net


def _clean_categories(train: pd.DataFrame, test: pd.DataFrame, features: list[str]) -> None:
    for column in features:
        if train[column].dtype == "object":
            train[column] = train[column].fillna("__MISSING__").astype(str)
            test[column] = test[column].fillna("__MISSING__").astype(str)


def fixed_holdout(
    frame: pd.DataFrame,
    *,
    market: str,
    feature_group: str,
    target: float,
    holdout_start: str,
    multistate: bool,
    recommended_top_k: int,
    status: str,
) -> dict[str, Any]:
    data, label, outcome, net = _prepare(frame, market=market, target=target)
    features = [value for value in FEATURE_GROUPS[feature_group] if value in data.columns]
    train = data[data["session_date"].astype(str) < holdout_start].copy()
    test = data[data["session_date"].astype(str) >= holdout_start].copy()
    _clean_categories(train, test, features)
    rows = test[
        ["candidate_key", "session_date", "ticker", "known_at", "entry_ts", "entry_price", outcome, label, net]
    ].rename(columns={label: "_label", net: "_net", outcome: "_outcome"})
    if multistate:
        model = _multiclass_pipeline(features)
        model.fit(train[features], train[outcome].astype(str))
        probabilities = model.predict_proba(test[features])
        classes = [str(value) for value in model.named_steps["model"].classes_]
        class_payoff = train.groupby(outcome)[net].mean().to_dict()
        rank_score = np.zeros(len(test), dtype=float)
        for index, state in enumerate(classes):
            rank_score += probabilities[:, index] * float(class_payoff[state])
        target_probability = probabilities[:, classes.index("TARGET_FIRST")]
    else:
        model = _pipeline(features)
        model.fit(train[features], train[label].astype(int))
        target_probability = model.predict_proba(test[features])[:, 1]
        rank_score = target_probability
    metrics = _metric_summary(
        rows,
        target_probability,
        label="_label",
        net_column="_net",
        rank_score=rank_score,
    )
    top_k: dict[str, Any] = {}
    for count in range(1, 11):
        top_k[str(count)] = _selection_stats(_daily_top(rows, rank_score, top_k=count))
    recommended = top_k[str(recommended_top_k)]
    scored = rows.copy()
    scored["target_probability"] = target_probability
    scored["rank_score"] = rank_score
    selected = _daily_top(scored, rank_score, top_k=recommended_top_k)
    score_diagnostics = []
    for session_date, group in scored.groupby("session_date", sort=True):
        ordered = group["rank_score"].sort_values(ascending=False)
        boundary = float(ordered.iloc[min(recommended_top_k, len(ordered)) - 1])
        rounded = group["rank_score"].round(12)
        score_diagnostics.append(
            {
                "session_date": str(session_date),
                "candidate_count": len(group),
                "unique_score_count": int(rounded.nunique()),
                "score_range": float(group["rank_score"].max() - group["rank_score"].min()),
                "top_k_boundary_tie_count": int((rounded >= round(boundary, 12)).sum()),
            }
        )
    ticker_counts = selected["ticker"].astype(str).value_counts()
    selected_records = selected[
        [
            "candidate_key",
            "session_date",
            "ticker",
            "known_at",
            "entry_ts",
            "entry_price",
            "_outcome",
            "_label",
            "_net",
            "target_probability",
            "rank_score",
        ]
    ].to_dict(orient="records")
    return {
        "market": market,
        "feature_group": feature_group,
        "target_pct": target,
        "stop_pct": STOP_PCT,
        "multistate_expected_net": multistate,
        "opening_cohort_only": True,
        "status": status,
        "holdout_start": holdout_start,
        "train_rows": len(train),
        "train_dates": int(train["session_date"].nunique()),
        "test_rows": len(test),
        "test_dates": int(test["session_date"].nunique()),
        "metrics": metrics,
        "top_k_sensitivity": top_k,
        "recommended_top_k": recommended_top_k,
        "recommended_selection": recommended,
        "score_diagnostics": {
            "sessions": score_diagnostics,
            "zero_range_sessions": sum(value["score_range"] <= 1e-12 for value in score_diagnostics),
            "boundary_tie_sessions": sum(
                value["top_k_boundary_tie_count"] > recommended_top_k for value in score_diagnostics
            ),
        },
        "selection_concentration": {
            "unique_tickers": int(selected["ticker"].nunique()),
            "max_ticker_count": int(ticker_counts.max()) if len(ticker_counts) else 0,
            "max_ticker_share": float(ticker_counts.max() / len(selected)) if len(selected) else None,
        },
        "selection_ledger": selected_records,
        "extra_cost_stress_pct": {
            str(extra): {
                "recommended_mean": recommended["mean_policy_net_pct"] - extra,
                "recommended_session_lcb": recommended["session_block_lcb_pct"] - extra,
            }
            for extra in (0.25, 0.50)
        },
    }


def expanding_candidates(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for market, group, target, multistate in (
        ("US", "combined", 3.6, True),
        ("KR_CLAUDE_REJECTED", "claude", 3.6, False),
        ("KR_SYSTEM_SCORES_OBSERVE", "system_scores", 3.6, False),
    ):
        actual_market = "KR" if market.startswith("KR_") else market
        data, label, outcome, net = _prepare(frame, market=actual_market, target=target)
        features = [value for value in FEATURE_GROUPS[group] if value in data.columns]
        if multistate:
            analysis = walk_forward_multistate(
                data,
                market=actual_market,
                features=features,
                outcome_column=outcome,
                label=label,
                net_column=net,
            )
        else:
            analysis = walk_forward(data, market=actual_market, features=features, label=label, net_column=net)
        result[market] = analysis
    return result


def universe_coverage(audit_db: Path, price_root: Path) -> dict[str, Any]:
    candidates = load_first_candidates(audit_db, markets=["KR", "US"])
    result: dict[str, Any] = {}
    for market, group in candidates.groupby("market"):
        market_dir = str(market).lower()
        exists = []
        for ticker in group["ticker"]:
            key = str(ticker).upper() if market == "US" else str(ticker).zfill(6)
            exists.append((price_root / market_dir / f"{market_dir}_{key}.csv").exists())
        work = group.copy()
        work["price_file_exists"] = exists
        feature_shift = {}
        for column in ("raw_rank", "prompt_rank", "candidate_quality_score", "change_pct", "from_high_pct"):
            available = pd.to_numeric(work.loc[work["price_file_exists"], column], errors="coerce").dropna()
            missing = pd.to_numeric(work.loc[~work["price_file_exists"], column], errors="coerce").dropna()
            feature_shift[column] = {
                "covered_mean": float(available.mean()) if len(available) else None,
                "missing_mean": float(missing.mean()) if len(missing) else None,
            }
        result[str(market)] = {
            "candidate_rows": len(work),
            "unique_tickers": int(work["ticker"].nunique()),
            "row_coverage": float(work["price_file_exists"].mean()),
            "ticker_coverage": float(work.groupby("ticker")["price_file_exists"].max().mean()),
            "daily_coverage_min": float(work.groupby("session_date")["price_file_exists"].mean().min()),
            "daily_coverage_median": float(work.groupby("session_date")["price_file_exists"].mean().median()),
            "feature_shift": feature_shift,
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Reproduce candidate path prediction validation")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT_DB)
    parser.add_argument("--price-root", type=Path, default=DEFAULT_PRICE_ROOT)
    parser.add_argument("--holdout-start", default="2026-07-01")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ledger-output", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args()
    frame = pd.read_csv(args.input, low_memory=False)
    report = {
        "schema_version": "candidate_path_prediction_validation_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input.resolve()),
        "post_selection_warning": (
            "Feature families, target, and top-k were inspected on this historical sample; "
            "results are shadow-challenger evidence, not live promotion evidence."
        ),
        "universe_coverage": universe_coverage(args.audit_db, args.price_root),
        "expanding_walk_forward": expanding_candidates(frame),
        "fixed_holdout": {
            "US": fixed_holdout(
                frame,
                market="US",
                feature_group="combined",
                target=3.6,
                holdout_start=args.holdout_start,
                multistate=True,
                recommended_top_k=3,
                status="SHADOW_CANDIDATE",
            ),
            "KR_CLAUDE_REJECTED": fixed_holdout(
                frame,
                market="KR",
                feature_group="claude",
                target=3.6,
                holdout_start=args.holdout_start,
                multistate=False,
                recommended_top_k=1,
                status="REJECT_TIE_DOMINATED",
            ),
            "KR_SYSTEM_SCORES_OBSERVE": fixed_holdout(
                frame,
                market="KR",
                feature_group="system_scores",
                target=3.6,
                holdout_start=args.holdout_start,
                multistate=False,
                recommended_top_k=3,
                status="OBSERVE_ONLY_NEGATIVE_TOP3_LCB",
            ),
        },
    }
    ledger_rows = []
    for arm_name, arm in report["fixed_holdout"].items():
        for row in arm.get("selection_ledger", []):
            ledger_rows.append({"arm": arm_name, "status": arm["status"], **row})
    args.ledger_output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(ledger_rows).to_csv(args.ledger_output, index=False, encoding="utf-8")
    report["selection_ledger_csv"] = str(args.ledger_output.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
