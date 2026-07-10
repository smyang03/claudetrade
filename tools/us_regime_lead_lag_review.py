from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.us_daily_alpha_walkforward import (
    YAHOO_FEATURES,
    expanding_month_splits,
    load_yahoo_dataset,
)


SEEDS = [20260710, 20260711, 20260712]
WINDOW_DATES = ["2026-04-28", "2026-05-07", "2026-06-17"]


def portfolio_metrics(values: pd.Series | np.ndarray) -> dict[str, Any]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return {"sessions": 0}
    positive = float(array[array > 0].sum())
    negative = float(-array[array < 0].sum())
    ordered = np.sort(array)[::-1]
    return {
        "sessions": int(len(array)),
        "mean_net_pct": float(array.mean()),
        "median_net_pct": float(np.median(array)),
        "p10_net_pct": float(np.quantile(array, 0.10)),
        "worst_net_pct": float(array.min()),
        "profit_factor": float(positive / negative) if negative else None,
        "mean_ex_top3_days_pct": float(ordered[3:].mean()) if len(ordered) > 3 else None,
    }


def exact_oos_daily(
    frame: pd.DataFrame,
    *,
    seeds: list[int] | None = None,
    top_k: int = 3,
) -> pd.DataFrame:
    model_seeds = list(seeds or SEEDS)
    pieces: list[pd.DataFrame] = []
    for window_idx, (train_dates, _purge_dates, test_dates) in enumerate(
        expanding_month_splits(frame, min_train_sessions=120, purge_sessions=7)
    ):
        train = frame[frame["session_date"].isin(train_dates)]
        test = frame[frame["session_date"].isin(test_dates)].copy()
        predicted_parts: list[np.ndarray] = []
        probability_parts: list[np.ndarray] = []
        for seed in model_seeds:
            regressor = HistGradientBoostingRegressor(
                loss="squared_error", learning_rate=0.05, max_iter=160,
                max_leaf_nodes=15, min_samples_leaf=35, l2_regularization=1.0,
                random_state=seed + window_idx,
            )
            classifier = HistGradientBoostingClassifier(
                learning_rate=0.05, max_iter=160, max_leaf_nodes=15,
                min_samples_leaf=35, l2_regularization=1.0,
                random_state=seed + window_idx,
            )
            regressor.fit(train[YAHOO_FEATURES], train["net_return_pct"])
            classifier.fit(train[YAHOO_FEATURES], train["target"])
            predicted_parts.append(regressor.predict(test[YAHOO_FEATURES]))
            probability_parts.append(classifier.predict_proba(test[YAHOO_FEATURES])[:, 1])
        test["predicted_net_pct"] = np.mean(predicted_parts, axis=0)
        test["probability"] = np.mean(probability_parts, axis=0)
        test["net_rank"] = test.groupby("session_date")["predicted_net_pct"].rank(pct=True)
        test["probability_rank"] = test.groupby("session_date")["probability"].rank(pct=True)
        test["alpha_score"] = 0.5 * test["net_rank"] + 0.5 * test["probability_rank"]
        selected = test.sort_values(
            ["session_date", "alpha_score", "predicted_net_pct"],
            ascending=[True, False, False],
        ).groupby("session_date", sort=False).head(top_k)
        pieces.append(selected)
    scored = pd.concat(pieces, ignore_index=True)
    return scored.groupby("session_date", as_index=False).agg(
        net_return_pct=("net_return_pct", "mean"),
        selected_n=("ticker", "nunique"),
    )


def load_context(vix_path: Path, breadth_path: Path, adv_path: Path) -> pd.DataFrame:
    vix = pd.read_csv(vix_path)
    breadth = pd.read_csv(breadth_path)
    adv = pd.read_csv(adv_path)
    for frame in (vix, breadth, adv):
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    breadth = breadth.sort_values("date")
    breadth["spy_return_pct"] = breadth["SPY"].pct_change() * 100.0
    breadth["rsp_return_pct"] = breadth["RSP"].pct_change() * 100.0
    breadth["narrow_excess_pct"] = breadth["rsp_return_pct"] - breadth["spy_return_pct"]
    breadth["rsp_spy_ratio_full"] = breadth["RSP"] / breadth["SPY"]
    breadth["rsp_spy_ratio_5d_pct"] = breadth["rsp_spy_ratio_full"].pct_change(5) * 100.0
    context = breadth.merge(adv, on="date", how="left").merge(vix, on="date", how="left")
    context = context.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    context["session_index"] = np.arange(len(context))
    return context


def attach_strict_prior(dates: pd.DataFrame, context: pd.DataFrame) -> pd.DataFrame:
    left = dates.copy().sort_values("date")
    right = context.copy().sort_values("date")
    return pd.merge_asof(
        left,
        right,
        on="date",
        direction="backward",
        allow_exact_matches=False,
        suffixes=("", "_context"),
    )


def summarize_vix(oos: pd.DataFrame, context: pd.DataFrame) -> dict[str, Any]:
    frame = oos.copy()
    frame["date"] = pd.to_datetime(frame["session_date"])
    frame = frame.merge(context[["date", "VIX", "term_ratio_3m_1m", "session_index"]], on="date", how="left")
    frame["backwardation"] = frame["term_ratio_3m_1m"] < 1.0
    overall = {
        ("backwardation" if bool(key) else "contango"): portfolio_metrics(group["net_return_pct"])
        for key, group in frame.groupby("backwardation")
    }
    frame["vix_bucket"] = pd.cut(frame["VIX"], [0, 20, 25, 30, 40, np.inf], right=False)
    controlled: dict[str, Any] = {}
    for (bucket, backwardation), group in frame.groupby(["vix_bucket", "backwardation"], observed=True):
        controlled[f"{bucket}|{'backwardation' if backwardation else 'contango'}"] = portfolio_metrics(
            group["net_return_pct"]
        )
    backward = frame[frame["backwardation"]].sort_values("session_index").copy()
    backward["episode"] = backward["session_index"].diff().ne(1).cumsum()
    episodes = [
        {
            "start": str(group["date"].min().date()),
            "end": str(group["date"].max().date()),
            "sessions": int(len(group)),
            "mean_net_pct": float(group["net_return_pct"].mean()),
        }
        for _, group in backward.groupby("episode")
    ]
    return {"overall": overall, "vix_controlled": controlled, "episodes": episodes}


def quartile_summary(frame: pd.DataFrame, column: str, value: str) -> dict[str, Any]:
    valid = frame.dropna(subset=[column, value]).copy()
    valid["bucket"] = pd.qcut(valid[column], 4, duplicates="drop")
    return {
        str(bucket): portfolio_metrics(group[value])
        for bucket, group in valid.groupby("bucket", observed=True)
    }


def summarize_oos_breadth(oos: pd.DataFrame, context: pd.DataFrame) -> dict[str, Any]:
    frame = oos.copy()
    frame["date"] = pd.to_datetime(frame["session_date"])
    # OOS decision is made after date D close for entry D+1, so D context is point-in-time valid.
    frame = frame.merge(context, on="date", how="left")
    return {
        "timing": "feature/market context at D close; entry next session open",
        "narrow_excess_quartiles": quartile_summary(frame, "narrow_excess_pct", "net_return_pct"),
        "ratio_5d_quartiles": quartile_summary(frame, "rsp_spy_ratio_5d_pct", "net_return_pct"),
        "adv_pct_quartiles": quartile_summary(frame, "adv_pct", "net_return_pct"),
    }


def load_actual_entry_sessions(db_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = sqlite3.connect(db_path)
    trades = pd.read_sql_query(
        """
        SELECT session_date,ticker,filled_at,closed_at,pnl_pct,pnl_krw,close_reason
        FROM v2_learning_performance
        WHERE market='US' AND filled=1 AND closed=1 AND pnl_pct IS NOT NULL
        """,
        con,
    )
    con.close()
    trades["date"] = pd.to_datetime(trades["filled_at"], utc=True, errors="coerce").dt.tz_convert(None).dt.normalize()
    daily = trades.groupby("date", as_index=False).agg(
        trades=("ticker", "size"),
        mean_net_pct=("pnl_pct", "mean"),
        sum_pnl_krw=("pnl_krw", "sum"),
        winners=("pnl_pct", lambda values: int((values > 0).sum())),
        losers=("pnl_pct", lambda values: int((values < 0).sum())),
    )
    return trades, daily


def permutation_correlation(
    frame: pd.DataFrame,
    *,
    x: str,
    y: str,
    seed: int = 20260711,
    samples: int = 5000,
) -> dict[str, Any]:
    valid = frame[[x, y]].dropna()
    if len(valid) < 8:
        return {"n": int(len(valid)), "correlation": None, "permutation_p_two_sided": None}
    x_values = valid[x].to_numpy(dtype=float)
    y_values = valid[y].to_numpy(dtype=float)
    observed = float(np.corrcoef(x_values, y_values)[0, 1])
    rng = np.random.default_rng(seed)
    null = np.empty(samples)
    for index in range(samples):
        null[index] = np.corrcoef(rng.permutation(x_values), y_values)[0, 1]
    p_value = float((np.abs(null) >= abs(observed)).mean())
    return {"n": int(len(valid)), "correlation": observed, "permutation_p_two_sided": p_value}


def summarize_actual(
    trades: pd.DataFrame,
    daily: pd.DataFrame,
    context: pd.DataFrame,
) -> dict[str, Any]:
    joined = attach_strict_prior(daily, context)
    windows: dict[str, Any] = {}
    for date in WINDOW_DATES:
        target = pd.Timestamp(date)
        rows = trades[trades["date"].eq(target)]
        windows[date] = {
            "entry_trades": int(len(rows)),
            "mean_net_pct": float(rows["pnl_pct"].mean()) if len(rows) else None,
            "winners": int((rows["pnl_pct"] > 0).sum()),
            "losers": int((rows["pnl_pct"] < 0).sum()),
            "sum_pnl_krw": float(rows["pnl_krw"].sum(min_count=1)) if rows["pnl_krw"].notna().any() else None,
        }
    correlations = {
        column: permutation_correlation(joined, x=column, y="mean_net_pct")
        for column in ("narrow_excess_pct", "rsp_spy_ratio_5d_pct", "adv_pct", "spy_return_pct")
    }
    return {
        "timing": "strictly previous available US market close before actual entry date",
        "sessions": int(len(joined)),
        "correlations": correlations,
        "narrow_excess_quartiles": quartile_summary(joined, "narrow_excess_pct", "mean_net_pct"),
        "ratio_5d_quartiles": quartile_summary(joined, "rsp_spy_ratio_5d_pct", "mean_net_pct"),
        "adv_pct_quartiles": quartile_summary(joined, "adv_pct", "mean_net_pct"),
        "window_audit": windows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit VIX term and breadth lead-lag against exact US strategy selections")
    parser.add_argument("--research-db", default=str(ROOT / "data" / "analysis" / "us_yahoo_point_in_time.db"))
    parser.add_argument("--learning-db", default=str(ROOT / "data" / "ml" / "decisions.db"))
    parser.add_argument("--vix", default=str(ROOT / "data" / "analysis" / "vix_term_daily.csv"))
    parser.add_argument("--breadth", default=str(ROOT / "data" / "analysis" / "us_breadth_proxy_daily.csv"))
    parser.add_argument("--adv", default=str(ROOT / "data" / "analysis" / "us_adv_dec_breadth_daily.csv"))
    parser.add_argument("--output", default=str(ROOT / "reports" / "us_regime_lead_lag_review_20260711.json"))
    args = parser.parse_args()
    con = sqlite3.connect(args.research_db)
    try:
        dataset = load_yahoo_dataset(con, horizon=5, cost_pct=0.50)
    finally:
        con.close()
    oos = exact_oos_daily(dataset, seeds=SEEDS, top_k=3)
    context = load_context(Path(args.vix), Path(args.breadth), Path(args.adv))
    trades, actual_daily = load_actual_entry_sessions(Path(args.learning_db))
    report = {
        "schema_version": "us_regime_lead_lag_review_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": {
            "oos": "exact 3-seed ensemble top3, expanding monthly walk-forward, 7-session purge, 0.50% cost",
            "oos_sessions": int(len(oos)),
            "actual_entry_sessions": int(len(actual_daily)),
            "no_lookahead": "OOS uses D close for D+1 entry; actual entries use strict prior available close",
        },
        "s2_vix_term": summarize_vix(oos, context),
        "s3_oos_breadth": summarize_oos_breadth(oos, context),
        "s3_actual_breadth": summarize_actual(trades, actual_daily, context),
        "authority": "RESEARCH_ONLY_NO_LIVE_OR_SHADOW_LEVER",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(output)
    print(json.dumps({"ok": True, "output": str(output), **report}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
