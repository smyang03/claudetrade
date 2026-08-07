from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor


ROOT = Path(__file__).resolve().parents[1]
FEATURES = [
    "rsi",
    "bb_pct",
    "volume_ratio",
    "macd_pct",
    "macd_signal_pct",
    "ma20_distance_pct",
    "ma60_distance_pct",
    "atr_pct",
    "gap_pct",
    "change_pct",
    "mr_fired",
    "vb_fired",
    "mom_fired",
    "gap_fired",
]
YAHOO_FEATURES = [
    "rsi",
    "bb_pct",
    "volume_ratio",
    "macd_pct",
    "macd_signal_pct",
    "ma20_distance_pct",
    "ma60_distance_pct",
    "atr_pct",
    "gap_pct",
    "change_pct",
    "momentum_5d_pct",
    "momentum_20d_pct",
    "momentum_60d_pct",
    "from_high_20d_pct",
    "realized_vol_20d_pct",
    "relative_strength_qqq_5d_pct",
    "relative_strength_qqq_20d_pct",
    "relative_strength_qqq_60d_pct",
    "spy_momentum_5d_pct",
    "spy_momentum_20d_pct",
    "spy_momentum_60d_pct",
    "qqq_momentum_5d_pct",
    "qqq_momentum_20d_pct",
    "qqq_momentum_60d_pct",
    "iwm_momentum_5d_pct",
    "iwm_momentum_20d_pct",
    "iwm_momentum_60d_pct",
]


def load_dataset(
    con: sqlite3.Connection,
    *,
    horizon: int = 3,
    cost_pct: float = 0.50,
    min_price: float = 5.0,
    max_abs_change_pct: float = 25.0,
) -> pd.DataFrame:
    if horizon not in {1, 3, 5}:
        raise ValueError("horizon must be 1, 3, or 5")
    frame = pd.read_sql_query(
        f"""
        SELECT session_date, ticker, price, rsi, bb_pct, vol_ratio, macd, macd_signal,
               ma20, ma60, atr, gap_pct, change_pct, mr_fired, vb_fired, mom_fired,
               gap_fired, strategy_used, forward_{horizon}d AS gross_return_pct
        FROM decisions
        WHERE market='US' AND data_source='backfill' AND is_simulated=1
          AND session_date IS NOT NULL AND forward_{horizon}d IS NOT NULL AND price>0
        ORDER BY session_date, ticker
        """,
        con,
    )
    numeric = [
        "price", "rsi", "bb_pct", "vol_ratio", "macd", "macd_signal", "ma20", "ma60",
        "atr", "gap_pct", "change_pct", "gross_return_pct", "mr_fired", "vb_fired",
        "mom_fired", "gap_fired",
    ]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["volume_ratio"] = frame["vol_ratio"]
    price = frame["price"].replace(0, np.nan)
    frame["macd_pct"] = frame["macd"] / price * 100.0
    frame["macd_signal_pct"] = frame["macd_signal"] / price * 100.0
    frame["ma20_distance_pct"] = (price / frame["ma20"].replace(0, np.nan) - 1.0) * 100.0
    frame["ma60_distance_pct"] = (price / frame["ma60"].replace(0, np.nan) - 1.0) * 100.0
    frame["atr_pct"] = frame["atr"] / price * 100.0
    frame["net_return_pct"] = frame["gross_return_pct"] - float(cost_pct)
    frame["target"] = (frame["net_return_pct"] >= 0.25).astype(int)
    frame["month"] = frame["session_date"].astype(str).str[:7]
    frame["year"] = frame["session_date"].astype(str).str[:4]
    frame = frame.replace([np.inf, -np.inf], np.nan)
    return frame[
        frame["price"].ge(float(min_price))
        & frame["change_pct"].abs().le(float(max_abs_change_pct))
    ].copy()


def load_yahoo_dataset(
    con: sqlite3.Connection,
    *,
    horizon: int = 3,
    stored_cost_pct: float = 0.50,
    cost_pct: float = 0.50,
    as_of: str | None = None,
    purge_sessions: int = 5,
) -> pd.DataFrame:
    """교재 로드. as_of를 주면 학습 as-of 계약을 강제한다 (2026-08-07 사전 등록).

    계약: train.session_date < as_of 이고, as_of 직전 purge_sessions 영업일 구간의
    행은 제외한다 — 그 행들의 forward 라벨(horizon일)이 as_of 이후 데이터를 포함해
    lookahead가 되기 때문이다. 영업일->달력일은 x2 보수 근사(휴일 포함 상회).
    현재 봉인 교재(~2026-04-02)에서는 결과가 불변(no-op)이며, 교재를 최신화하는
    순간부터 이 계약이 발동한다. 라이브 채점 경로는 반드시 as_of=signal_date로 호출.
    """
    if horizon not in {1, 3, 5}:
        raise ValueError("horizon must be 1, 3, or 5")
    columns = ["session_date", "ticker", *YAHOO_FEATURES, f"gross_krw_{horizon}d_pct", f"net_krw_{horizon}d_pct"]
    frame = pd.read_sql_query(
        f"SELECT {','.join(columns)} FROM us_yahoo_point_in_time WHERE net_krw_{horizon}d_pct IS NOT NULL ORDER BY session_date,ticker",
        con,
    )
    if as_of:
        cutoff = pd.Timestamp(str(as_of)[:10]) - pd.Timedelta(days=2 * max(int(purge_sessions), horizon))
        frame = frame[pd.to_datetime(frame["session_date"].astype(str)) < cutoff]
        if frame.empty:
            raise ValueError(f"as_of={as_of} 적용 후 학습 표본 없음 — 교재/컷오프 확인")
    for column in YAHOO_FEATURES:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["gross_return_pct"] = pd.to_numeric(frame[f"gross_krw_{horizon}d_pct"], errors="coerce")
    stored_net = pd.to_numeric(frame[f"net_krw_{horizon}d_pct"], errors="coerce")
    frame["net_return_pct"] = stored_net - (float(cost_pct) - float(stored_cost_pct))
    frame["target"] = (frame["net_return_pct"] >= 0.25).astype(int)
    frame["month"] = frame["session_date"].astype(str).str[:7]
    frame["year"] = frame["session_date"].astype(str).str[:4]
    return frame.replace([np.inf, -np.inf], np.nan)


def expanding_month_splits(
    frame: pd.DataFrame, *, min_train_sessions: int = 120, purge_sessions: int = 5
) -> list[tuple[list[str], list[str], list[str]]]:
    dates = sorted(frame["session_date"].astype(str).unique())
    months = sorted(frame["month"].astype(str).unique())
    output: list[tuple[list[str], list[str], list[str]]] = []
    for month in months:
        test_dates = [value for value in dates if value.startswith(month)]
        if not test_dates:
            continue
        first_idx = dates.index(test_dates[0])
        train_end = max(0, first_idx - purge_sessions)
        train_dates = dates[:train_end]
        purge_dates = dates[train_end:first_idx]
        if len(train_dates) < min_train_sessions:
            continue
        output.append((train_dates, purge_dates, test_dates))
    return output


def _block_bootstrap_lcb(values: np.ndarray, *, seed: int, block: int = 5, samples: int = 2000) -> float | None:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 10:
        return None
    rng = np.random.default_rng(seed)
    starts = np.arange(max(1, len(values) - block + 1))
    means = np.empty(samples, dtype=float)
    blocks_needed = int(np.ceil(len(values) / block))
    for idx in range(samples):
        sampled: list[float] = []
        for start in rng.choice(starts, size=blocks_needed, replace=True):
            sampled.extend(values[int(start): int(start) + block].tolist())
        means[idx] = float(np.mean(sampled[: len(values)]))
    return float(np.quantile(means, 0.05))


def _portfolio_metrics(rows: pd.DataFrame, *, seed: int) -> dict[str, Any]:
    if rows.empty:
        return {"rows": 0, "sessions": 0}
    daily = rows.groupby("session_date", sort=True).agg(
        net_pct=("net_return_pct", "mean"),
        excess_pct=("excess_pct", "mean"),
        names=("ticker", "nunique"),
    )
    net = daily["net_pct"].to_numpy(dtype=float)
    excess = daily["excess_pct"].to_numpy(dtype=float)
    positive = float(net[net > 0].sum())
    negative = float(-net[net < 0].sum())
    ordered = np.sort(net)[::-1]
    return {
        "rows": int(len(rows)),
        "sessions": int(len(daily)),
        "mean_net_pct": float(net.mean()),
        "median_net_pct": float(np.median(net)),
        "mean_excess_vs_candidate_universe_pct": float(excess.mean()),
        "win_rate": float((net > 0).mean()),
        "profit_factor": float(positive / negative) if negative > 0 else None,
        "block_bootstrap_lcb_pct": _block_bootstrap_lcb(net, seed=seed),
        "mean_net_ex_top3_days_pct": float(ordered[3:].mean()) if len(ordered) > 3 else None,
    }


def walk_forward(
    frame: pd.DataFrame,
    *,
    feature_columns: list[str] | None = None,
    min_train_sessions: int = 120,
    purge_sessions: int = 5,
    seed: int = 20260710,
    model_seeds: list[int] | None = None,
    return_scored_frame: bool = False,
) -> dict[str, Any]:
    selected_features = list(feature_columns or FEATURES)
    ensemble_seeds = list(model_seeds or [seed])
    records: list[pd.DataFrame] = []
    windows: list[dict[str, Any]] = []
    for window_idx, (train_dates, purge_dates, test_dates) in enumerate(
        expanding_month_splits(
            frame, min_train_sessions=min_train_sessions, purge_sessions=purge_sessions
        )
    ):
        train = frame[frame["session_date"].isin(train_dates)].copy()
        test = frame[frame["session_date"].isin(test_dates)].copy()
        if train.empty or test.empty or train["target"].nunique() < 2:
            continue
        predicted_parts: list[np.ndarray] = []
        probability_parts: list[np.ndarray] = []
        for model_seed in ensemble_seeds:
            regressor = HistGradientBoostingRegressor(
                loss="squared_error",
                learning_rate=0.05,
                max_iter=160,
                max_leaf_nodes=15,
                min_samples_leaf=35,
                l2_regularization=1.0,
                random_state=model_seed + window_idx,
            )
            classifier = HistGradientBoostingClassifier(
                learning_rate=0.05,
                max_iter=160,
                max_leaf_nodes=15,
                min_samples_leaf=35,
                l2_regularization=1.0,
                random_state=model_seed + window_idx,
            )
            regressor.fit(train[selected_features], train["net_return_pct"])
            classifier.fit(train[selected_features], train["target"])
            predicted_parts.append(regressor.predict(test[selected_features]))
            probability_parts.append(classifier.predict_proba(test[selected_features])[:, 1])
        predicted_net = np.mean(predicted_parts, axis=0)
        probability = np.mean(probability_parts, axis=0)
        scored = test.copy()
        scored["predicted_net_pct"] = predicted_net
        scored["probability"] = probability
        # Both models must agree; ranks remove unstable absolute calibration.
        scored["net_rank"] = scored.groupby("session_date")["predicted_net_pct"].rank(pct=True)
        scored["prob_rank"] = scored.groupby("session_date")["probability"].rank(pct=True)
        scored["alpha_score"] = 0.5 * scored["net_rank"] + 0.5 * scored["prob_rank"]
        baseline = scored.groupby("session_date")["net_return_pct"].transform("mean")
        scored["excess_pct"] = scored["net_return_pct"] - baseline
        records.append(scored)
        windows.append(
            {
                "test_month": str(test_dates[0])[:7],
                "train_start": train_dates[0],
                "train_end": train_dates[-1],
                "purged_dates": purge_dates,
                "train_n": int(len(train)),
                "test_n": int(len(test)),
                "model_seeds": ensemble_seeds,
            }
        )
    if not records:
        return {"windows": [], "test_rows": 0, "cohorts": {}}
    scored = pd.concat(records, ignore_index=True)
    cohorts: dict[str, Any] = {}
    for top_k in (1, 3, 5, 10):
        selected = scored.sort_values(
            ["session_date", "alpha_score", "predicted_net_pct"], ascending=[True, False, False]
        ).groupby("session_date", sort=False).head(top_k)
        cohorts[f"top{top_k}_per_day"] = _portfolio_metrics(selected, seed=seed + top_k)
        cohorts[f"top{top_k}_by_year"] = {
            year: _portfolio_metrics(group, seed=seed + top_k + int(year))
            for year, group in selected.groupby("year")
        }
        confident_pool = scored[
            scored["predicted_net_pct"].ge(0.25) & scored["probability"].ge(0.55)
        ]
        confident = confident_pool.sort_values(
            ["session_date", "alpha_score", "predicted_net_pct"], ascending=[True, False, False]
        ).groupby("session_date", sort=False).head(top_k)
        cohorts[f"confident_top{top_k}_per_day"] = _portfolio_metrics(confident, seed=seed + 100 + top_k)
        cohorts[f"confident_top{top_k}_by_year"] = {
            year: _portfolio_metrics(group, seed=seed + 100 + top_k + int(year))
            for year, group in confident.groupby("year")
        }
    baseline = scored.copy()
    baseline["excess_pct"] = 0.0
    output = {
        "windows": windows,
        "test_rows": int(len(scored)),
        "test_sessions": int(scored["session_date"].nunique()),
        "test_range": [str(scored["session_date"].min()), str(scored["session_date"].max())],
        "candidate_universe": _portfolio_metrics(baseline, seed=seed),
        "cohorts": cohorts,
    }
    if return_scored_frame:
        output["_scored_frame"] = scored
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Purged long-history US daily alpha discovery")
    parser.add_argument("--db", default=str(ROOT / "data" / "ml" / "decisions.db"))
    parser.add_argument("--source", choices=("decisions", "yahoo"), default="yahoo")
    parser.add_argument("--yahoo-db", default=str(ROOT / "data" / "analysis" / "us_yahoo_point_in_time.db"))
    parser.add_argument("--horizon", type=int, choices=(1, 3, 5), default=3)
    parser.add_argument("--cost-pct", type=float, default=0.50)
    parser.add_argument("--min-price", type=float, default=5.0)
    parser.add_argument("--max-abs-change-pct", type=float, default=25.0)
    parser.add_argument("--min-train-sessions", type=int, default=120)
    parser.add_argument("--purge-sessions", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260710)
    args = parser.parse_args()
    source_db = args.yahoo_db if args.source == "yahoo" else args.db
    con = sqlite3.connect(source_db)
    try:
        if args.source == "yahoo":
            frame = load_yahoo_dataset(con, horizon=args.horizon, cost_pct=args.cost_pct)
            feature_columns = YAHOO_FEATURES
        else:
            frame = load_dataset(
                con,
                horizon=args.horizon,
                cost_pct=args.cost_pct,
                min_price=args.min_price,
                max_abs_change_pct=args.max_abs_change_pct,
            )
            feature_columns = FEATURES
    finally:
        con.close()
    result = walk_forward(
        frame,
        feature_columns=feature_columns,
        min_train_sessions=args.min_train_sessions,
        purge_sessions=args.purge_sessions,
        seed=args.seed,
    )
    report = {
        "db": source_db,
        "dataset": {
            "rows": int(len(frame)),
            "sessions": int(frame["session_date"].nunique()),
            "tickers": int(frame["ticker"].nunique()),
            "range": [str(frame["session_date"].min()), str(frame["session_date"].max())],
            "provenance": (
                "point-in-time Yahoo adjusted OHLCV + KRW=X; historical anchor universe"
                if args.source == "yahoo"
                else "US backfill rows; simulated hypothesis-generation only"
            ),
        },
        "method": {
            "horizon_days": args.horizon,
            "cost_pct": args.cost_pct,
            "live_eligibility": {
                "min_price": args.min_price,
                "max_abs_change_pct": args.max_abs_change_pct,
            },
            "purge_sessions": args.purge_sessions,
            "split": "expanding train -> 5-session purge -> next calendar-month test",
            "selection": "cross-sectional average of predicted-net rank and target-probability rank",
            "uncertainty": "5-session moving-block bootstrap over daily portfolio returns",
            "features": feature_columns,
        },
        "result": result,
        "authority": "RESEARCH_ONLY_NOT_LIVE_PROMOTION",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
