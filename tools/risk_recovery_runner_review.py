"""Risk-recovery runner eligibility audit (read-only, fail-closed).

Initial risk must come from the stop known at entry. Realized MAE is future
information and is never accepted as R. This tool intentionally does not emit a
counterfactual return until ordered minute-path replay can model the trigger,
integer partial sale, breakeven stop and residual exit.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ml" / "decisions.db"
DEFAULT_EVENT_DB = ROOT / "data" / "v2_event_store.db"


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def load_entry_risk(event_db: Path) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    con = sqlite3.connect(f"file:{event_db.resolve().as_posix()}?mode=ro", uri=True)
    for decision_id, plan_json in con.execute(
        "SELECT decision_id,plan_json FROM v2_path_runs WHERE decision_id IS NOT NULL AND plan_json IS NOT NULL"
    ):
        try:
            plan = json.loads(plan_json or "{}")
            entry = float(plan.get("actual_entry_price") or plan.get("entry_order_price") or 0.0)
            stop = float(plan.get("stop_loss") or 0.0)
        except Exception:
            continue
        if decision_id and entry > 0 and 0 < stop < entry:
            out[str(decision_id)] = {
                "entry_price": entry,
                "stop_loss": stop,
                "initial_risk_pct": (entry - stop) / entry * 100.0,
            }
    con.close()
    return out


def review(
    db: Path,
    event_db: Path = DEFAULT_EVENT_DB,
    trigger_r: float = 2.0,
    min_qty: int = 4,
) -> dict[str, Any]:
    risk_by_decision = load_entry_risk(event_db)
    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    rows = con.execute(
        """
        SELECT v2_decision_id,market,qty,mfe_pct,mfe_time,mae_time,pnl_pct_net
        FROM v2_learning_performance
        WHERE closed=1 AND pnl_pct_net IS NOT NULL
        """
    ).fetchall()
    con.close()
    ordered = 0
    known_risk = 0
    trigger_candidates = 0
    missing_risk = 0
    for decision_id, _market, qty, mfe, mfe_time, mae_time, _net in rows:
        risk = risk_by_decision.get(str(decision_id or ""))
        if not risk:
            missing_risk += 1
            continue
        known_risk += 1
        mfe_at = _parse_time(mfe_time)
        mae_at = _parse_time(mae_time)
        if mfe_at is None or mae_at is None:
            continue
        ordered += 1
        if (
            int(qty or 0) >= int(min_qty)
            and mfe is not None
            and float(mfe) >= float(trigger_r) * float(risk["initial_risk_pct"])
            and mfe_at < mae_at
        ):
            trigger_candidates += 1
    return {
        "total_closed_net": len(rows),
        "entry_stop_risk_known_n": known_risk,
        "entry_stop_risk_missing_n": missing_risk,
        "mfe_mae_time_coverage_n": ordered,
        "trigger_candidate_n": trigger_candidates,
        "trigger_r": trigger_r,
        "min_qty": min_qty,
        "uses_realized_mae_as_initial_risk": False,
        "counterfactual_ready": False,
        "counterfactual_return_emitted": False,
        "required_next": [
            "ordered minute bars from fill through evaluation horizon",
            "first 2R crossing price and timestamp",
            "integer sell quantity that recovers initial risk KRW",
            "post-trigger breakeven-stop and residual-exit replay",
            "sharp-reversal state known at trigger",
        ],
        "verdict": (
            "ordered MFE/MAE timestamps unavailable; wiring versus forward-wait remains unverified"
            if ordered == 0
            else "eligibility audit only; exact minute-path counterfactual required before profit claims"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="risk-recovery runner eligibility audit")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--event-db", type=Path, default=DEFAULT_EVENT_DB)
    parser.add_argument("--trigger-r", type=float, default=2.0)
    parser.add_argument("--min-qty", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(
        review(args.db, args.event_db, args.trigger_r, args.min_qty),
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
