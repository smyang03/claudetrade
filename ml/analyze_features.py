"""
Quick exploratory analysis for decisions.db.

Outputs:
- simple feature importance proxies via correlation to forward return
- bucket analysis for core features such as RSI / BB% / vol_ratio

Examples:
  python -m ml.analyze_features
  python -m ml.analyze_features --market US --horizon 5
  python -m ml.analyze_features --market KR --source live --horizon 1
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import pandas as pd

from ml.db_writer import load_for_ml

TARGET_COLS = {
    1: "forward_1d",
    3: "forward_3d",
    5: "forward_5d",
}

NUMERIC_FEATURES = [
    "rsi",
    "bb_pct",
    "vol_ratio",
    "macd",
    "macd_signal",
    "ma20",
    "ma60",
    "atr",
    "gap_pct",
    "change_pct",
    "mr_rsi_miss",
    "mr_bb_miss",
    "vb_close_miss",
    "mode_score",
    "vix",
    "usd_krw",
]

DEFAULT_BUCKETS = {
    "rsi": [0, 20, 25, 30, 32, 35, 40, 50, 70, 100],
    "bb_pct": [-100, 0, 10, 20, 30, 50, 80, 100, 200],
    "vol_ratio": [0, 0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0, 100],
    "mr_rsi_miss": [-100, -10, -5, -2, 0, 2, 5, 10, 100],
    "mr_bb_miss": [-200, -20, -10, -5, 0, 5, 10, 20, 200],
    "vb_close_miss": [-100, -5, -2, -1, 0, 1, 2, 5, 100],
}


def _load_df(
    market: str | None,
    source: str,
    target_col: str,
    decision_filter: str,
    mode_filter: str | None,
) -> pd.DataFrame:
    df = load_for_ml(market=market, with_forward_return=True)
    if df.empty:
        return df
    if source != "all":
        is_sim = 1 if source == "backfill" else 0
        df = df[df["is_simulated"] == is_sim].copy()
    if decision_filter != "all" and "decision" in df.columns:
        df = df[df["decision"] == decision_filter].copy()
    if mode_filter and "mode" in df.columns:
        df = df[df["mode"] == mode_filter].copy()
    df = df[df[target_col].notna()].copy()
    return df


def _feature_scores(df: pd.DataFrame, target_col: str) -> pd.DataFrame:
    rows = []
    for col in NUMERIC_FEATURES:
        if col not in df.columns:
            continue
        ser = pd.to_numeric(df[col], errors="coerce")
        valid = pd.DataFrame({"x": ser, "y": df[target_col]}).dropna()
        if len(valid) < 30:
            continue
        pearson = valid["x"].corr(valid["y"], method="pearson")
        spearman = valid["x"].corr(valid["y"], method="spearman")
        rows.append(
            {
                "feature": col,
                "rows": len(valid),
                "pearson": round(float(pearson), 4) if pd.notna(pearson) else None,
                "spearman": round(float(spearman), 4) if pd.notna(spearman) else None,
                "abs_spearman": round(abs(float(spearman)), 4) if pd.notna(spearman) else None,
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["abs_spearman", "rows"], ascending=[False, False]).reset_index(drop=True)


def _bucket_table(df: pd.DataFrame, feature: str, target_col: str, bins: Iterable[float]) -> pd.DataFrame:
    if feature not in df.columns:
        return pd.DataFrame()
    work = df[[feature, target_col]].copy()
    work[feature] = pd.to_numeric(work[feature], errors="coerce")
    work = work.dropna()
    if len(work) < 20:
        return pd.DataFrame()

    work["bucket"] = pd.cut(work[feature], bins=bins, include_lowest=True)
    grouped = (
        work.groupby("bucket", observed=False)
        .agg(
            rows=(target_col, "size"),
            avg_ret=(target_col, "mean"),
            med_ret=(target_col, "median"),
            hit_rate=(target_col, lambda s: (s > 0).mean()),
        )
        .reset_index()
    )
    grouped = grouped[grouped["rows"] > 0].copy()
    grouped["avg_ret"] = grouped["avg_ret"].round(3)
    grouped["med_ret"] = grouped["med_ret"].round(3)
    grouped["hit_rate"] = (grouped["hit_rate"] * 100).round(1)
    return grouped


def _print_df(title: str, df: pd.DataFrame, limit: int | None = None) -> None:
    print()
    print(title)
    if df.empty:
        print("  (no data)")
        return
    if limit is not None:
        df = df.head(limit)
    print(df.to_string(index=False))


def _safe_name(v: str | None) -> str:
    if not v:
        return "all"
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in v)


def _write_csvs(
    score_df: pd.DataFrame,
    bucket_tables: dict[str, pd.DataFrame],
    market: str | None,
    source: str,
    target_col: str,
    decision_filter: str,
    mode_filter: str | None,
) -> Path:
    out_dir = Path(__file__).parent / "analysis_outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{_safe_name(market)}_{source}_{decision_filter}_{_safe_name(mode_filter)}_{target_col}"
    score_path = out_dir / f"{stem}_feature_scores.csv"
    score_df.to_csv(score_path, index=False, encoding="utf-8-sig")
    for feature, df in bucket_tables.items():
        df = df.copy()
        if "bucket" in df.columns:
            df["bucket"] = df["bucket"].astype(str)
        df.to_csv(out_dir / f"{stem}_bucket_{feature}.csv", index=False, encoding="utf-8-sig")
    return out_dir


def _save_batch_index(rows: list[dict], name: str) -> Path:
    out_dir = Path(__file__).parent / "analysis_outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def _analyze_one(
    market: str | None,
    source: str,
    target_col: str,
    decision_filter: str,
    mode_filter: str | None,
    top: int,
    save_csv: bool,
) -> dict:
    df = _load_df(market, source, target_col, decision_filter, mode_filter)
    if df.empty:
        return {
            "rows": 0,
            "market": market or "ALL",
            "source": source,
            "target": target_col,
            "decision": decision_filter,
            "mode": mode_filter or "ALL",
            "buy_signal": 0,
            "no_signal": 0,
            "saved_dir": None,
        }

    header = {
        "rows": len(df),
        "market": market or "ALL",
        "source": source,
        "target": target_col,
        "decision": decision_filter,
        "mode": mode_filter or "ALL",
        "buy_signal": int((df["decision"] == "BUY_SIGNAL").sum()) if "decision" in df.columns else 0,
        "no_signal": int((df["decision"] == "NO_SIGNAL").sum()) if "decision" in df.columns else 0,
    }
    print("Analysis Summary")
    for k, v in header.items():
        print(f"  {k}: {v}")

    score_df = _feature_scores(df, target_col)
    _print_df("Feature Correlation Ranking", score_df, limit=top)

    bucket_tables: dict[str, pd.DataFrame] = {}
    for feature, bins in DEFAULT_BUCKETS.items():
        bucket_df = _bucket_table(df, feature, target_col, bins)
        bucket_tables[feature] = bucket_df
        _print_df(f"Bucket Analysis: {feature} vs {target_col}", bucket_df)

    saved_dir = None
    if save_csv:
        saved_dir = _write_csvs(
            score_df,
            bucket_tables,
            market,
            source,
            target_col,
            decision_filter,
            mode_filter,
        )
        print()
        print(f"Saved CSV outputs to: {saved_dir}")

    header["saved_dir"] = str(saved_dir) if saved_dir else None
    return header


def main() -> None:
    parser = argparse.ArgumentParser(description="Quick feature/bucket analysis for decisions.db")
    parser.add_argument("--market", choices=["KR", "US"], default=None)
    parser.add_argument("--source", choices=["all", "live", "backfill"], default="all")
    parser.add_argument("--horizon", type=int, choices=[1, 3, 5], default=5)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--decision", choices=["all", "BUY_SIGNAL", "NO_SIGNAL"], default="all")
    parser.add_argument("--mode", default=None)
    parser.add_argument("--save-csv", action="store_true")
    parser.add_argument("--by-mode", action="store_true")
    parser.add_argument("--batch-standard", action="store_true")
    args = parser.parse_args()

    target_col = TARGET_COLS[args.horizon]
    if args.batch_standard:
        batch_rows: list[dict] = []
        for market in ("KR", "US"):
            for decision in ("BUY_SIGNAL", "NO_SIGNAL"):
                for horizon in (1, 3, 5):
                    print()
                    print("#" * 100)
                    print(f"BATCH market={market} decision={decision} horizon={horizon}")
                    print("#" * 100)
                    result = _analyze_one(
                        market,
                        args.source,
                        TARGET_COLS[horizon],
                        decision,
                        None,
                        args.top,
                        args.save_csv,
                    )
                    result["horizon"] = horizon
                    batch_rows.append(result)
        batch_path = _save_batch_index(batch_rows, f"batch_standard_{args.source}.csv")
        print()
        print("Batch Summary Index")
        print(pd.DataFrame(batch_rows).to_string(index=False))
        print()
        print(f"Saved batch summary to: {batch_path}")
        return

    if args.by_mode:
        base_df = _load_df(args.market, args.source, target_col, args.decision, None)
        if base_df.empty:
            print("No rows available for analysis.")
            return
        modes = sorted(str(m) for m in base_df["mode"].dropna().unique().tolist())
        summaries = []
        for mode in modes:
            print()
            print("=" * 80)
            print(f"MODE: {mode}")
            print("=" * 80)
            summaries.append(
                _analyze_one(
                    args.market,
                    args.source,
                    target_col,
                    args.decision,
                    mode,
                    args.top,
                    args.save_csv,
                )
            )
        summary_df = pd.DataFrame(summaries).sort_values("rows", ascending=False)
        print()
        print("Mode Summary Index")
        print(summary_df.to_string(index=False))
        if args.save_csv and not summary_df.empty:
            out_dir = Path(__file__).parent / "analysis_outputs"
            out_dir.mkdir(parents=True, exist_ok=True)
            summary_path = out_dir / f"{_safe_name(args.market)}_{args.source}_{args.decision}_mode_summary_{target_col}.csv"
            summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
            print()
            print(f"Saved mode summary to: {summary_path}")
        return

    result = _analyze_one(
        args.market,
        args.source,
        target_col,
        args.decision,
        args.mode,
        args.top,
        args.save_csv,
    )
    if result["rows"] == 0:
        print("No rows available for analysis.")


if __name__ == "__main__":
    main()
