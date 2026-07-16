from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.candidate_path_prediction_lab import (
    CATEGORICAL_FEATURES,
    FEATURE_GROUPS,
    STOP_PCT,
    _daily_top,
    _metric_summary,
    _selection_stats,
    _tag,
    session_entry_floor,
)


DEFAULT_INPUT = ROOT / "data" / "analysis" / "candidate_path_labels_lag5_v1.csv"
DEFAULT_PRICE_ROOT = ROOT / "data" / "price"
DEFAULT_SCREENER_ROOT = ROOT / "logs" / "screener_quality"
DEFAULT_OUTPUT = ROOT / "reports" / "candidate_universe_enhancement_lab_20260716.json"
DEFAULT_LEDGER = ROOT / "reports" / "candidate_universe_enhancement_picks_20260716.csv"

DAILY_FEATURES = [
    "daily_ret_1d_pct",
    "daily_ret_5d_pct",
    "daily_ret_20d_pct",
    "daily_ret_60d_pct",
    "daily_residual_5d_pct",
    "daily_residual_20d_pct",
    "daily_residual_60d_pct",
    "daily_volatility_20d_pct",
    "daily_downside_volatility_20d_pct",
    "daily_from_high_20d_pct",
    "daily_from_high_60d_pct",
    "daily_from_high_252d_pct",
    "daily_prev_volume_ratio_20d",
    "daily_log10_adv20",
    "daily_amihud_20d",
    "daily_opening_gap_pct",
    "benchmark_ret_5d_pct",
    "benchmark_ret_20d_pct",
    "benchmark_volatility_20d_pct",
]

KR_RICH_FEATURES = [
    "sq_candidate_quality_score",
    "sq_component_liquidity",
    "sq_component_relative_strength",
    "sq_component_trend_quality",
    "sq_component_flow_support",
    "sq_component_risk_adjustment",
    "sq_ret_5d_pct",
    "sq_ret_20d_pct",
    "sq_ret_60d_pct",
    "sq_rs_20d_vs_board",
    "sq_rs_60d_vs_board",
    "sq_volatility_20d_pct",
    "sq_turnover_vs_20d",
    "sq_volume_vs_20d",
    "sq_from_52w_high_pct",
    "sq_drawdown_20d_pct",
    "sq_foreign_net_qty_1d",
    "sq_institution_net_qty_1d",
    "sq_positive_foreign_flow",
    "sq_positive_institution_flow",
    "sq_flow_observed",
]


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _opening_cohort(frame: pd.DataFrame, market: str) -> pd.DataFrame:
    subset = frame[
        (frame["market"] == market)
        & (frame["h60_label_available"] == 1)
    ].copy()
    known = pd.to_datetime(subset["known_at"], utc=True, errors="coerce")
    floor = pd.Series(
        [session_entry_floor(value, market=market, entry_lag_min=5) for value in subset["known_at"]],
        index=subset.index,
    )
    return subset[known.notna() & floor.notna() & (known <= floor)].copy()


def _read_daily(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return (
        frame.dropna(subset=["date", "close"])
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )


def _return_pct(close: pd.Series, sessions: int) -> float | None:
    if len(close) <= sessions:
        return None
    start = _float(close.iloc[-sessions - 1])
    end = _float(close.iloc[-1])
    if start is None or end is None or start <= 0:
        return None
    return (end / start - 1.0) * 100.0


def _daily_snapshot(frame: pd.DataFrame, session_date: str) -> dict[str, float | None]:
    cutoff = pd.Timestamp(session_date)
    history = frame[frame["date"] < cutoff].copy()
    if len(history) < 2:
        return {}
    close = history["close"]
    volume = history["volume"]
    returns = close.pct_change()
    tail20 = returns.tail(20).dropna()
    negative20 = tail20[tail20 < 0]
    latest_close = _float(close.iloc[-1])
    result: dict[str, float | None] = {
        "prev_close": latest_close,
        "ret_1d_pct": _return_pct(close, 1),
        "ret_5d_pct": _return_pct(close, 5),
        "ret_20d_pct": _return_pct(close, 20),
        "ret_60d_pct": _return_pct(close, 60),
        "volatility_20d_pct": (
            float(tail20.std(ddof=1) * math.sqrt(252.0) * 100.0) if len(tail20) >= 10 else None
        ),
        "downside_volatility_20d_pct": (
            float(negative20.std(ddof=1) * math.sqrt(252.0) * 100.0)
            if len(negative20) >= 3
            else None
        ),
    }
    for window in (20, 60, 252):
        high = _float(close.tail(window).max())
        result[f"from_high_{window}d_pct"] = (
            (latest_close / high - 1.0) * 100.0
            if latest_close is not None and high is not None and high > 0
            else None
        )
    prior_volume = volume.iloc[:-1].tail(20).dropna()
    latest_volume = _float(volume.iloc[-1])
    result["prev_volume_ratio_20d"] = (
        latest_volume / float(prior_volume.mean())
        if latest_volume is not None and len(prior_volume) >= 10 and float(prior_volume.mean()) > 0
        else None
    )
    dollars = (close * volume).tail(20).dropna()
    adv20 = float(dollars.mean()) if len(dollars) >= 10 else None
    result["log10_adv20"] = math.log10(adv20) if adv20 is not None and adv20 > 0 else None
    amihud = (returns.abs() / ((close * volume) / 1_000_000.0)).replace([np.inf, -np.inf], np.nan)
    result["amihud_20d"] = float(amihud.tail(20).mean()) if amihud.tail(20).notna().sum() >= 10 else None
    return result


def _benchmark_ticker(market: str, market_type: str) -> str:
    if market == "US":
        return "SPY"
    return "229200" if "KOSDAQ" in str(market_type).upper() else "069500"


def add_daily_features(frame: pd.DataFrame, price_root: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    result = frame.copy()
    cache: dict[tuple[str, str], pd.DataFrame | None] = {}
    snapshot_cache: dict[tuple[str, str, str], dict[str, float | None]] = {}

    def load(market: str, ticker: str) -> pd.DataFrame | None:
        key = (market, ticker)
        if key not in cache:
            prefix = market.lower()
            path = price_root / prefix / f"{prefix}_{ticker}.csv"
            try:
                cache[key] = _read_daily(path) if path.exists() else None
            except Exception:
                cache[key] = None
        return cache[key]

    def snapshot(market: str, ticker: str, date: str) -> dict[str, float | None]:
        key = (market, ticker, date)
        if key not in snapshot_cache:
            daily = load(market, ticker)
            snapshot_cache[key] = _daily_snapshot(daily, date) if daily is not None else {}
        return snapshot_cache[key]

    records: list[dict[str, Any]] = []
    for row in result.itertuples(index=False):
        market = str(row.market).upper()
        ticker = str(row.ticker).upper() if market == "US" else str(row.ticker).zfill(6)
        date = str(row.session_date)
        own = snapshot(market, ticker, date)
        benchmark = snapshot(market, _benchmark_ticker(market, getattr(row, "market_type", "")), date)
        record: dict[str, Any] = {
            f"daily_{key}": value for key, value in own.items() if key != "prev_close"
        }
        for horizon in (5, 20, 60):
            own_return = _float(own.get(f"ret_{horizon}d_pct"))
            benchmark_return = _float(benchmark.get(f"ret_{horizon}d_pct"))
            record[f"daily_residual_{horizon}d_pct"] = (
                own_return - benchmark_return
                if own_return is not None and benchmark_return is not None
                else None
            )
        entry = _float(getattr(row, "entry_price", None))
        previous = _float(own.get("prev_close"))
        record["daily_opening_gap_pct"] = (
            (entry / previous - 1.0) * 100.0
            if entry is not None and previous is not None and previous > 0
            else None
        )
        record["benchmark_ret_5d_pct"] = benchmark.get("ret_5d_pct")
        record["benchmark_ret_20d_pct"] = benchmark.get("ret_20d_pct")
        record["benchmark_volatility_20d_pct"] = benchmark.get("volatility_20d_pct")
        records.append(record)
    enriched = pd.concat([result.reset_index(drop=True), pd.DataFrame(records)], axis=1)
    coverage = {
        column: float(enriched[column].notna().mean()) if column in enriched else 0.0
        for column in DAILY_FEATURES
    }
    return enriched, coverage


def load_kr_rich_features(root: Path) -> pd.DataFrame:
    earliest: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(root.glob("*_KR_candidates.jsonl")):
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    raw = json.loads(line)
                except (TypeError, ValueError):
                    continue
                timestamp = pd.to_datetime(raw.get("timestamp"), errors="coerce")
                if pd.isna(timestamp) or timestamp.hour > 9 or (timestamp.hour == 9 and timestamp.minute > 5):
                    continue
                ticker = str(raw.get("ticker") or "").zfill(6)
                session_date = timestamp.strftime("%Y-%m-%d")
                key = (session_date, ticker)
                previous = earliest.get(key)
                if previous is None or str(raw.get("timestamp")) < str(previous.get("timestamp")):
                    earliest[key] = raw
    rows: list[dict[str, Any]] = []
    for (session_date, ticker), raw in earliest.items():
        components = raw.get("candidate_quality_components")
        if not isinstance(components, dict):
            components = {}
        foreign = _float(raw.get("foreign_net_qty_1d"))
        institution = _float(raw.get("institution_net_qty_1d"))
        rows.append(
            {
                "session_date": session_date,
                "ticker": ticker,
                "sq_candidate_quality_score": _float(raw.get("candidate_quality_score")),
                "sq_component_liquidity": _float(components.get("liquidity")),
                "sq_component_relative_strength": _float(components.get("relative_strength")),
                "sq_component_trend_quality": _float(components.get("trend_quality")),
                "sq_component_flow_support": _float(components.get("flow_support")),
                "sq_component_risk_adjustment": _float(components.get("risk_adjustment")),
                "sq_ret_5d_pct": _float(raw.get("ret_5d_pct")),
                "sq_ret_20d_pct": _float(raw.get("ret_20d_pct")),
                "sq_ret_60d_pct": _float(raw.get("ret_60d_pct")),
                "sq_rs_20d_vs_board": _float(raw.get("rs_20d_vs_board")),
                "sq_rs_60d_vs_board": _float(raw.get("rs_60d_vs_board")),
                "sq_volatility_20d_pct": _float(raw.get("volatility_20d_pct")),
                "sq_turnover_vs_20d": _float(raw.get("turnover_vs_20d")),
                "sq_volume_vs_20d": _float(raw.get("volume_vs_20d")),
                "sq_from_52w_high_pct": _float(raw.get("from_52w_high_pct")),
                "sq_drawdown_20d_pct": _float(raw.get("drawdown_20d_pct")),
                "sq_foreign_net_qty_1d": foreign,
                "sq_institution_net_qty_1d": institution,
                "sq_positive_foreign_flow": int(foreign is not None and foreign > 0),
                "sq_positive_institution_flow": int(institution is not None and institution > 0),
                "sq_flow_observed": int(
                    (foreign is not None and foreign != 0)
                    or (institution is not None and institution != 0)
                ),
            }
        )
    return pd.DataFrame(rows)


def _pipeline(
    features: list[str],
    categorical: list[str],
    *,
    model_kind: str,
    binary: bool,
) -> Pipeline:
    category_set = set(categorical)
    numeric = [column for column in features if column not in category_set]
    categories = [column for column in features if column in category_set]
    transformers: list[tuple[str, Any, list[str]]] = []
    if numeric:
        numeric_steps: list[tuple[str, Any]] = [
            ("impute", SimpleImputer(strategy="median", add_indicator=True))
        ]
        if model_kind == "logistic":
            numeric_steps.append(("scale", StandardScaler()))
        transformers.append(("num", Pipeline(numeric_steps), numeric))
    if categories:
        transformers.append(
            (
                "cat",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categories,
            )
        )
    if model_kind == "forest":
        model: Any = RandomForestClassifier(
            n_estimators=400,
            max_depth=5,
            min_samples_leaf=20,
            max_features=0.6,
            class_weight="balanced_subsample",
            random_state=20260716,
            n_jobs=-1,
        )
    else:
        model = LogisticRegression(
            C=0.25,
            max_iter=2000,
            solver="liblinear" if binary else "lbfgs",
        )
    return Pipeline(
        [
            ("pre", ColumnTransformer(transformers, sparse_threshold=0.3)),
            ("model", model),
        ]
    )


def evaluate_arm(
    frame: pd.DataFrame,
    *,
    market: str,
    features: list[str],
    categorical: list[str],
    target: float,
    holdout_start: str,
    model_kind: str,
    arm_name: str,
    multistate: bool,
) -> tuple[dict[str, Any], pd.DataFrame]:
    prefix = f"h60_t{_tag(target)}_s{_tag(STOP_PCT)}"
    label = f"{prefix}_target_before_stop"
    outcome = f"{prefix}_outcome"
    net = f"{prefix}_policy_net_pct"
    data = _opening_cohort(frame, market).dropna(subset=[label, outcome, net]).copy()
    usable_features = [column for column in features if column in data]
    for column in usable_features:
        if column in categorical:
            data[column] = data[column].fillna("__MISSING__").astype(str)
        else:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    train = data[data["session_date"].astype(str) < holdout_start].copy()
    test = data[data["session_date"].astype(str) >= holdout_start].copy()
    model = _pipeline(
        usable_features,
        categorical,
        model_kind=model_kind,
        binary=not multistate,
    )
    model.fit(
        train[usable_features],
        train[outcome].astype(str) if multistate else train[label].astype(int),
    )
    probabilities = model.predict_proba(test[usable_features])
    classes = [str(value) for value in model.named_steps["model"].classes_]
    class_payoff = train.groupby(outcome)[net].mean().to_dict()
    if multistate:
        rank_score = np.zeros(len(test), dtype=float)
        for index, state in enumerate(classes):
            rank_score += probabilities[:, index] * float(class_payoff[state])
        target_probability = (
            probabilities[:, classes.index("TARGET_FIRST")]
            if "TARGET_FIRST" in classes
            else np.zeros(len(test), dtype=float)
        )
    else:
        positive_index = classes.index("1")
        target_probability = probabilities[:, positive_index]
        rank_score = target_probability
    rows = test[
        ["candidate_key", "session_date", "ticker", "known_at", "entry_ts", "entry_price", outcome, label, net]
    ].rename(columns={outcome: "_outcome", label: "_label", net: "_net"})
    metrics = _metric_summary(
        rows,
        target_probability,
        label="_label",
        net_column="_net",
        rank_score=rank_score,
    )
    scored = rows.copy()
    scored["arm"] = arm_name
    scored["model_kind"] = model_kind
    scored["target_probability"] = target_probability
    scored["rank_score"] = rank_score
    selected = _daily_top(scored, rank_score, top_k=3)
    result = {
        "arm": arm_name,
        "market": market,
        "model_kind": model_kind,
        "ranking_objective": "multistate_expected_net" if multistate else "target_probability",
        "status": "RESEARCH_ONLY_POST_SELECTION",
        "features": usable_features,
        "train_rows": int(len(train)),
        "train_dates": int(train["session_date"].nunique()),
        "test_rows": int(len(test)),
        "test_dates": int(test["session_date"].nunique()),
        "class_payoff_train_pct": {str(key): float(value) for key, value in class_payoff.items()},
        "metrics": metrics,
        "top3": _selection_stats(selected),
    }
    return result, selected


def train_cut_profiles(
    frame: pd.DataFrame,
    *,
    market: str,
    features: list[str],
    holdout_start: str,
    target: float,
) -> dict[str, Any]:
    prefix = f"h60_t{_tag(target)}_s{_tag(STOP_PCT)}"
    net = f"{prefix}_policy_net_pct"
    data = _opening_cohort(frame, market).dropna(subset=[net]).copy()
    train = data[data["session_date"].astype(str) < holdout_start]
    test = data[data["session_date"].astype(str) >= holdout_start]
    result: dict[str, Any] = {}
    for column in features:
        if column not in data:
            continue
        train_values = pd.to_numeric(train[column], errors="coerce").dropna()
        test_values = pd.to_numeric(test[column], errors="coerce")
        if len(train_values) < 50 or test_values.notna().sum() < 20:
            continue
        cuts = sorted(set(float(value) for value in train_values.quantile([0.25, 0.50, 0.75])))
        if len(cuts) < 2:
            continue
        bins = [-np.inf, *cuts, np.inf]
        bucket = pd.cut(test_values, bins=bins, include_lowest=True, duplicates="drop")
        work = pd.DataFrame({"bucket": bucket, "net": pd.to_numeric(test[net], errors="coerce")})
        grouped = []
        for label, group in work.dropna().groupby("bucket", observed=True):
            grouped.append(
                {
                    "bucket": str(label),
                    "n": int(len(group)),
                    "mean_net_pct": float(group["net"].mean()),
                    "median_net_pct": float(group["net"].median()),
                }
            )
        result[column] = {"train_cuts": cuts, "holdout_buckets": grouped}
    return result


def consensus_profiles(
    ledger: pd.DataFrame,
    *,
    arm_features: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    pairs = [
        ("US_BASELINE_LOGIT", "US_DAILY_ONLY_FOREST"),
        ("KR_SYSTEM_SCORES_LOGIT", "KR_DAILY_RICH_FOREST"),
    ]
    feature_map = arm_features or {
        "US_BASELINE_LOGIT": list(FEATURE_GROUPS["combined"]),
        "US_DAILY_ONLY_FOREST": list(DAILY_FEATURES),
        "KR_SYSTEM_SCORES_LOGIT": list(FEATURE_GROUPS["system_scores"]),
        "KR_DAILY_RICH_FOREST": list(DAILY_FEATURES + KR_RICH_FEATURES),
    }
    result: dict[str, Any] = {}
    for left, right in pairs:
        left_rows = ledger[ledger["arm"] == left].copy()
        right_rows = ledger[ledger["arm"] == right].copy()
        right_keys = right_rows[
            ["session_date", "ticker", "target_probability", "rank_score"]
        ].drop_duplicates(["session_date", "ticker"])
        overlap = left_rows.merge(
            right_keys,
            on=["session_date", "ticker"],
            how="inner",
            suffixes=("_left", "_right"),
        )
        key = f"{left}__AND__{right}"
        left_features = set(feature_map.get(left) or [])
        right_features = set(feature_map.get(right) or [])
        shared_features = sorted(left_features & right_features)
        result[key] = {
            "status": "PREREGISTRATION_REQUIRED_EXPLORATORY_CONSENSUS",
            "left_arm": left,
            "right_arm": right,
            "selection_rule": "same_session_same_ticker_top3_intersection_else_abstain",
            "authority": "SHADOW_ONLY_NO_ORDER_AUTHORITY",
            "evidence_contract": {
                "left_feature_count": len(left_features),
                "right_feature_count": len(right_features),
                "shared_features": shared_features,
                "feature_sets_disjoint": not shared_features,
                "different_model_classes_required": True,
            },
            "n": int(len(overlap)),
            "dates": int(overlap["session_date"].nunique()),
            "unique_tickers": int(overlap["ticker"].nunique()),
            "mean_policy_net_pct": float(overlap["_net"].mean()) if len(overlap) else None,
            "median_policy_net_pct": float(overlap["_net"].median()) if len(overlap) else None,
            "positive_rate": float((overlap["_net"] > 0).mean()) if len(overlap) else None,
            "outcome_counts": (
                {str(key): int(value) for key, value in overlap["_outcome"].value_counts().items()}
                if len(overlap)
                else {}
            ),
            "records": overlap[
                [
                    "session_date",
                    "ticker",
                    "_outcome",
                    "_net",
                    "target_probability_left",
                    "rank_score_left",
                    "target_probability_right",
                    "rank_score_right",
                ]
            ].to_dict(orient="records"),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Candidate-universe enhancement research lab")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--price-root", type=Path, default=DEFAULT_PRICE_ROOT)
    parser.add_argument("--screener-root", type=Path, default=DEFAULT_SCREENER_ROOT)
    parser.add_argument("--holdout-start", default="2026-07-01")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ledger-output", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args()

    frame = pd.read_csv(args.input, low_memory=False)
    enriched, daily_coverage = add_daily_features(frame, args.price_root)
    rich = load_kr_rich_features(args.screener_root)
    enriched["ticker"] = enriched.apply(
        lambda row: str(row["ticker"]).upper()
        if str(row["market"]).upper() == "US"
        else str(row["ticker"]).zfill(6),
        axis=1,
    )
    enriched = enriched.merge(rich, on=["session_date", "ticker"], how="left")
    categorical = list(CATEGORICAL_FEATURES)
    baseline = [column for column in FEATURE_GROUPS["combined"] if column in enriched]
    system_scores = [column for column in FEATURE_GROUPS["system_scores"] if column in enriched]
    arms: list[tuple[str, str, list[str], str, bool]] = [
        ("US_BASELINE_LOGIT", "US", baseline, "logistic", True),
        ("US_DAILY_ONLY_LOGIT", "US", DAILY_FEATURES, "logistic", True),
        ("US_DAILY_ONLY_FOREST", "US", DAILY_FEATURES, "forest", True),
        ("US_BASELINE_DAILY_LOGIT", "US", baseline + DAILY_FEATURES, "logistic", True),
        ("US_BASELINE_DAILY_FOREST", "US", baseline + DAILY_FEATURES, "forest", True),
        ("KR_BASELINE_LOGIT", "KR", baseline, "logistic", True),
        ("KR_SYSTEM_SCORES_LOGIT", "KR", system_scores, "logistic", False),
        (
            "KR_SYSTEM_SCORES_DAILY_RICH_LOGIT",
            "KR",
            system_scores + DAILY_FEATURES + KR_RICH_FEATURES,
            "logistic",
            False,
        ),
        (
            "KR_SYSTEM_SCORES_DAILY_RICH_FOREST",
            "KR",
            system_scores + DAILY_FEATURES + KR_RICH_FEATURES,
            "forest",
            False,
        ),
        ("KR_RICH_ONLY_LOGIT", "KR", KR_RICH_FEATURES, "logistic", False),
        ("KR_DAILY_RICH_LOGIT", "KR", DAILY_FEATURES + KR_RICH_FEATURES, "logistic", False),
        ("KR_DAILY_RICH_FOREST", "KR", DAILY_FEATURES + KR_RICH_FEATURES, "forest", False),
        (
            "KR_BASELINE_DAILY_RICH_LOGIT",
            "KR",
            baseline + DAILY_FEATURES + KR_RICH_FEATURES,
            "logistic",
            False,
        ),
        (
            "KR_BASELINE_DAILY_RICH_FOREST",
            "KR",
            baseline + DAILY_FEATURES + KR_RICH_FEATURES,
            "forest",
            False,
        ),
    ]
    results: dict[str, Any] = {}
    ledgers: list[pd.DataFrame] = []
    arm_features: dict[str, list[str]] = {}
    for name, market, features, model_kind, multistate in arms:
        analysis, selected = evaluate_arm(
            enriched,
            market=market,
            features=features,
            categorical=categorical,
            target=3.6,
            holdout_start=args.holdout_start,
            model_kind=model_kind,
            arm_name=name,
            multistate=multistate,
        )
        results[name] = analysis
        arm_features[name] = list(analysis.get("features") or [])
        ledgers.append(selected)
    ledger = pd.concat(ledgers, ignore_index=True) if ledgers else pd.DataFrame()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.ledger_output.parent.mkdir(parents=True, exist_ok=True)
    ledger.to_csv(args.ledger_output, index=False, encoding="utf-8")

    kr_open = _opening_cohort(enriched, "KR")
    report = {
        "schema_version": "candidate_universe_enhancement_lab_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input.resolve()),
        "holdout_start": args.holdout_start,
        "authority": "RESEARCH_ONLY_NO_LIVE_ORDER_AUTHORITY",
        "warnings": [
            "All arms were compared on the same historical holdout during this research pass; the winner is post-selection and must remain shadow.",
            "Minute-price coverage is trigger-oriented rather than a random full-universe sample.",
            "Daily Yahoo-style files may contain retrospectively adjusted prices; split-sensitive rows need a point-in-time corporate-action audit.",
            "STOP_FIRST is booked at the exact stop and therefore excludes adverse stop slippage.",
        ],
        "contracts": {
            "entry": "same-session first complete minute at market open+5m",
            "daily_features": "strictly session_date-1 or earlier",
            "kr_rich_features": "earliest screener_quality row no later than 09:05 KST",
            "target": "60m +3.6% before -2.5%, same-bar ambiguity stop-first",
            "cost_pct": {"US": 0.50, "KR": 0.21},
        },
        "coverage": {
            "daily_feature_non_null_share": daily_coverage,
            "kr_rich_opening_rows": int(kr_open["sq_rs_20d_vs_board"].notna().sum()),
            "kr_opening_rows": int(len(kr_open)),
        },
        "arms": results,
        "cross_model_consensus": consensus_profiles(ledger, arm_features=arm_features),
        "train_cut_holdout_profiles": {
            "US": train_cut_profiles(
                enriched,
                market="US",
                features=DAILY_FEATURES,
                holdout_start=args.holdout_start,
                target=3.6,
            ),
            "KR": train_cut_profiles(
                enriched,
                market="KR",
                features=DAILY_FEATURES + KR_RICH_FEATURES,
                holdout_start=args.holdout_start,
                target=3.6,
            ),
        },
        "ledger": str(args.ledger_output.resolve()),
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "ledger": str(args.ledger_output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
