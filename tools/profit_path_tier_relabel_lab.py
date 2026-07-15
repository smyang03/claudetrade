from __future__ import annotations

"""Walk-forward tier-reach relabel experiment for the existing profit_path data.

The current production challenger was trained on distant target attainment and
can never clear its configured probability hurdle.  This read-only experiment
uses the already deployed early-tier levels, purged expanding date folds and a
daily capacity cap.  It writes research artifacts only.
"""

import argparse
import json
import sqlite3
import statistics as st
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import SGDClassifier
from sklearn.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.profit_evidence_path_walkforward import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    _preprocessor,
    build_path_dataset,
)

AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES
CONTRACTS = {
    "US": {"tier_pct": 2.3, "cost_pct": 0.50, "stop_pct": 2.5, "giveback_pct": 0.6},
    "KR": {"tier_pct": 3.6, "cost_pct": 0.21, "stop_pct": 2.5, "giveback_pct": 0.6},
}


def model(seed: int) -> Pipeline:
    return Pipeline(
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


def add_outcomes(frame: pd.DataFrame, market: str) -> pd.DataFrame:
    cfg = CONTRACTS[market]
    output = frame.copy()
    output["tier_success"] = (
        (output["max_runup_60m_pct"] >= cfg["tier_pct"])
        & (output["max_drawdown_60m_pct"] > -cfg["stop_pct"])
    )
    # Conservative lower-bound proxy: if both barriers appear in summary data,
    # loss is assumed first because ordered bars are unavailable here.
    output["tier_cf_net_pct"] = np.where(
        output["max_drawdown_60m_pct"] <= -cfg["stop_pct"],
        -cfg["stop_pct"] - cfg["cost_pct"],
        np.where(
            output["max_runup_60m_pct"] >= cfg["tier_pct"],
            cfg["tier_pct"] - cfg["giveback_pct"] - cfg["cost_pct"],
            output["outcome_60m_pct"] - cfg["cost_pct"],
        ),
    )
    output["raw_60m_net_pct"] = output["outcome_60m_pct"] - cfg["cost_pct"]
    return output


def folds(dates: list[str], min_train: int = 15, test_size: int = 6) -> list[tuple[list[str], list[str], str]]:
    result: list[tuple[list[str], list[str], str]] = []
    start = min_train
    while start < len(dates):
        purge = dates[start - 1]
        train = dates[: start - 1]
        test = dates[start : min(len(dates), start + test_size)]
        if train and test:
            result.append((train, test, purge))
        start += test_size
    return result


def top_daily(frame: pd.DataFrame, n: int) -> pd.DataFrame:
    if frame.empty:
        return frame
    per_ticker = frame.sort_values("prediction", ascending=False).drop_duplicates(
        ["session_date", "ticker_key"], keep="first"
    )
    return (
        per_ticker.sort_values(["session_date", "prediction"], ascending=[True, False])
        .groupby("session_date", group_keys=False)
        .head(n)
        .copy()
    )


def sequential_threshold(frame: pd.DataFrame, threshold: float, cap: int = 3) -> pd.DataFrame:
    """Executable arm: accept qualifying signals in timestamp order, no end-of-day ranking."""
    if frame.empty:
        return frame
    selected: list[pd.DataFrame] = []
    for _, day in frame.sort_values(["session_date", "entry_ts", "path_id"]).groupby("session_date"):
        chosen: list[int] = []
        seen: set[str] = set()
        for index, row in day.iterrows():
            ticker = str(row["ticker_key"])
            if ticker in seen or float(row["prediction"]) < threshold:
                continue
            chosen.append(index)
            seen.add(ticker)
            if len(chosen) >= cap:
                break
        if chosen:
            selected.append(day.loc[chosen])
    return pd.concat(selected, ignore_index=True) if selected else frame.iloc[0:0].copy()


def stats(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"n": 0}
    raw = frame["raw_60m_net_pct"].astype(float).tolist()
    cf = frame["tier_cf_net_pct"].astype(float).tolist()
    trimmed_raw = sorted(raw)[:-3] if len(raw) > 3 else raw
    by_day = frame.groupby("session_date")["raw_60m_net_pct"].mean().astype(float).tolist()
    return {
        "n": len(frame),
        "days": int(frame["session_date"].nunique()),
        "tier_success_rate": round(float(frame["tier_success"].mean()), 4),
        "raw_60m_net_mean": round(st.mean(raw), 4),
        "raw_60m_net_sum": round(sum(raw), 3),
        "tier_cf_net_mean": round(st.mean(cf), 4),
        "tier_cf_net_sum": round(sum(cf), 3),
        "raw_ex_top3_sum": round(sum(trimmed_raw), 3),
        "positive_day_rate": round(sum(1 for value in by_day if value > 0) / len(by_day), 4),
        "worst_day_mean": round(min(by_day), 4),
    }


def run_market(frame: pd.DataFrame, market: str, seed: int) -> tuple[dict[str, Any], pd.DataFrame]:
    before_hygiene = len(frame)
    clean = frame[
        frame["data_quality"].astype(str).eq("minute_complete")
        & frame["liquidity_bucket"].astype(str).isin(["high", "mid"])
        & frame["entry_vs_candidate_pct"].abs().le(1.0)
    ].copy()
    data = add_outcomes(clean, market)
    dates = sorted(data["session_date"].astype(str).unique())
    ledgers: list[pd.DataFrame] = []
    fold_meta: list[dict[str, Any]] = []
    for index, (train_dates, test_dates, purge_date) in enumerate(folds(dates)):
        train = data[data["session_date"].astype(str).isin(train_dates)].copy()
        test = data[data["session_date"].astype(str).isin(test_dates)].copy()
        y = train["tier_success"].astype(int)
        if y.nunique() < 2 or test.empty:
            continue
        estimator = model(seed + index)
        estimator.fit(train[FEATURES], y)
        test["prediction"] = estimator.predict_proba(test[FEATURES])[:, 1]
        test["fold"] = index + 1
        test["market"] = market
        ledgers.append(test)
        fold_meta.append(
            {
                "fold": index + 1,
                "train_dates": [train_dates[0], train_dates[-1]],
                "purge_date": purge_date,
                "test_dates": [test_dates[0], test_dates[-1]],
                "train_n": len(train),
                "test_n": len(test),
                "train_base_rate": round(float(y.mean()), 4),
                "test_base_rate": round(float(test["tier_success"].mean()), 4),
            }
        )
    ledger = pd.concat(ledgers, ignore_index=True) if ledgers else pd.DataFrame()
    result: dict[str, Any] = {
        "market": market,
        "pre_hygiene_n": before_hygiene,
        "dataset_n": len(data),
        "date_n": len(dates),
        "tier_pct": CONTRACTS[market]["tier_pct"],
        "folds": fold_meta,
        "oos_all_paths": stats(ledger),
        "arms": {},
    }
    selected_ledgers: list[pd.DataFrame] = []
    for n in (1, 3, 5):
        selected = top_daily(ledger, n)
        selected["arm"] = f"top{n}_per_day_upper_bound"
        result["arms"][f"top{n}_per_day_upper_bound"] = stats(selected)
        selected_ledgers.append(selected)
    for threshold in (0.70, 0.80, 0.90):
        selected = sequential_threshold(ledger, threshold, cap=3)
        arm = f"sequential_p{int(threshold * 100)}_cap3"
        selected["arm"] = arm
        result["arms"][arm] = stats(selected)
        selected_ledgers.append(selected)
    selected_output = pd.concat(selected_ledgers, ignore_index=True) if selected_ledgers else pd.DataFrame()
    return result, selected_output


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Profit-path early-tier relabel walk-forward — 2026-07-15",
        "",
        "모든 신호는 진입 시점 피처만 사용하고, expanding train 뒤 하루 purge 후 다음 날짜 블록을 평가했다. "
        "ordered bar가 없어 stop과 tier가 모두 관측되면 stop-first로 계산한 보수적 합성 순익을 병기한다.",
        "",
        "| 시장/arm | N | tier 성공률 | 60분 net 평균 | 합성 net 평균 | ex-top3 합 | 양수일 비율 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for market in ("US", "KR"):
        item = report["markets"][market]
        for arm, values in item["arms"].items():
            lines.append(
                f"| {market} {arm} | {values.get('n', 0)} | {values.get('tier_success_rate', 0):.1%} | "
                f"{values.get('raw_60m_net_mean', 0):+.3f}% | {values.get('tier_cf_net_mean', 0):+.3f}% | "
                f"{values.get('raw_ex_top3_sum', 0):+.2f}%p | {values.get('positive_day_rate', 0):.1%} |"
            )
    lines += [
        "",
        "`topN_per_day_upper_bound`는 그날 뒤에 올 후보를 아는 비실행 상한이라 승격 대상이 아니다. "
        "실행 후보는 timestamp 순으로 확률기준을 넘는 첫 3개만 받는 `sequential_*` arm이다.",
        "",
        "이 결과는 실제 주문 권한이 없는 `SHADOW_ONLY` 발견 결과다. 합성 순익과 실제 60분 net이 동시에 "
        "양수이고, 상위 3건 제거·날짜 블록에서도 살아남는 arm만 다음 forward 후보가 된다.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260715)
    parser.add_argument("--json", default=str(ROOT / "reports" / "profit_path_tier_relabel_lab_20260715.json"))
    parser.add_argument("--ledger", default=str(ROOT / "reports" / "profit_path_tier_relabel_ledger_20260715.csv"))
    parser.add_argument("--md", default=str(ROOT / "docs" / "reports" / "profit_path_tier_relabel_lab_20260715.md"))
    args = parser.parse_args()

    con = sqlite3.connect(f"file:{AUDIT_DB.resolve().as_posix()}?mode=ro", uri=True)
    markets: dict[str, Any] = {}
    ledgers: list[pd.DataFrame] = []
    for market in ("US", "KR"):
        frame, diagnostics = build_path_dataset(con, market)
        result, ledger = run_market(frame, market, args.seed)
        result["join_diagnostics"] = diagnostics
        markets[market] = result
        if not ledger.empty:
            ledgers.append(ledger)
    con.close()
    report = {
        "as_of": "2026-07-15",
        "authority": "SHADOW_ONLY",
        "markets": markets,
    }
    Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ledger = pd.concat(ledgers, ignore_index=True) if ledgers else pd.DataFrame()
    keep = [
        "market", "fold", "arm", "session_date", "ticker_key", "path_id", "path_name", "entry_ts",
        "entry_price", "candidate_price", "entry_vs_candidate_pct", "market_open_elapsed_min",
        "liquidity_bucket", "market_type", "data_quality", "volume_ratio", "prediction",
        "tier_success", "raw_60m_net_pct", "tier_cf_net_pct", "max_runup_60m_pct", "max_drawdown_60m_pct",
    ]
    ledger[[column for column in keep if column in ledger.columns]].to_csv(args.ledger, index=False)
    Path(args.md).write_text(markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"WROTE {args.json}\nWROTE {args.ledger}\nWROTE {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
