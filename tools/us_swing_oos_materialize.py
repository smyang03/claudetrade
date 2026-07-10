from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.us_swing_authority import load_swing_policy
from tools.us_daily_alpha_walkforward import YAHOO_FEATURES, load_yahoo_dataset, walk_forward


OUTCOME_COLUMNS = [
    "session_date", "ticker", "date", "entry_date_5d", "exit_date_5d",
    "entry_open_5d", "exit_close_5d", "gross_usd_5d_pct",
    "gross_krw_5d_pct", "net_krw_5d_pct",
]


def materialize_oos_selection(*, db_path: Path, policy_path: Path, top_k: int = 3) -> tuple[pd.DataFrame, dict]:
    policy = load_swing_policy(policy_path)
    con = sqlite3.connect(db_path)
    try:
        frame = load_yahoo_dataset(con, horizon=5, cost_pct=float(policy.get("cost_pct", 0.5)))
        outcomes = pd.read_sql_query(
            f"SELECT {','.join(OUTCOME_COLUMNS)} FROM us_yahoo_point_in_time",
            con,
        )
    finally:
        con.close()
    result = walk_forward(
        frame,
        feature_columns=YAHOO_FEATURES,
        min_train_sessions=120,
        purge_sessions=7,
        seed=int(policy.get("seeds", [20260710])[0]),
        model_seeds=[int(value) for value in policy.get("seeds", [20260710])],
        return_scored_frame=True,
    )
    scored = result.pop("_scored_frame")
    selected = (
        scored.sort_values(
            ["session_date", "alpha_score", "predicted_net_pct"],
            ascending=[True, False, False],
        )
        .groupby("session_date", sort=False)
        .head(max(1, int(top_k)))
        .copy()
    )
    selected["selection_rank"] = selected.groupby("session_date").cumcount() + 1
    keep = [
        "session_date", "ticker", "selection_rank", "alpha_score", "predicted_net_pct",
        "probability", "net_return_pct", "excess_pct",
    ]
    selected = selected[keep].merge(outcomes, on=["session_date", "ticker"], how="left", validate="one_to_one")
    selected = selected.sort_values(["session_date", "selection_rank"]).reset_index(drop=True)
    metadata = {
        "schema_version": "us_swing_oos_selected_v1",
        "db": str(db_path),
        "policy": str(policy_path),
        "top_k": int(top_k),
        "rows": int(len(selected)),
        "sessions": int(selected["session_date"].nunique()),
        "range": [str(selected["session_date"].min()), str(selected["session_date"].max())],
        "model_result": result,
    }
    return selected, metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize exact sealed US swing OOS selections")
    parser.add_argument("--db", default=str(ROOT / "data" / "analysis" / "us_yahoo_point_in_time.db"))
    parser.add_argument("--policy", default=str(ROOT / "config" / "us_swing_accelerated.json"))
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--output", default=str(ROOT / "reports" / "us_swing_oos_selected_20260711.csv"))
    parser.add_argument("--metadata", default=str(ROOT / "reports" / "us_swing_oos_selected_20260711.json"))
    args = parser.parse_args()
    selected, metadata = materialize_oos_selection(
        db_path=Path(args.db), policy_path=Path(args.policy), top_k=args.top_k
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    selected.to_csv(output, index=False)
    metadata["csv_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    metadata_path = Path(args.metadata)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps({"ok": True, "output": str(output), **metadata}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
