from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.post_open_features import filter_future_returns, infer_momentum_state


RETURN_FIELD_MAP = {
    "ret_3m_pct": "post_open_3m_return_pct",
    "ret_5m_pct": "post_open_5m_return_pct",
    "ret_10m_pct": "post_open_10m_return_pct",
    "ret_30m_pct": "post_open_30m_return_pct",
}


def _market_open_at(market: str, session_date: str) -> datetime:
    day = datetime.fromisoformat(str(session_date)[:10])
    if str(market).upper() == "US":
        return day.replace(hour=22, minute=30, second=0, microsecond=0)
    return day.replace(hour=9, minute=0, second=0, microsecond=0)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _candidate_returns(candidate: dict[str, Any]) -> dict[str, float | None]:
    returns: dict[str, float | None] = {}
    for target_key, source_key in RETURN_FIELD_MAP.items():
        returns[target_key] = _as_float(candidate.get(source_key))
    return returns


def build_replay(
    preopen_state: dict[str, Any],
    *,
    market: str,
    decision_minutes: int,
    min_ret_5m: float = 1.0,
    min_ret_30m: float | None = None,
    max_items: int = 10,
) -> dict[str, Any]:
    session_date = str(
        preopen_state.get("session_date")
        or preopen_state.get("date")
        or datetime.now().date().isoformat()
    )[:10]
    anchor_at = _market_open_at(market, session_date)
    known_at = anchor_at + timedelta(minutes=int(decision_minutes))
    rows: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    for idx, candidate in enumerate(preopen_state.get("candidates") or [], start=1):
        ticker = str(candidate.get("ticker") or "").strip()
        known_returns = filter_future_returns(
            _candidate_returns(candidate),
            known_at=known_at.isoformat(timespec="seconds"),
            anchor_at=anchor_at.isoformat(timespec="seconds"),
        )
        state = infer_momentum_state(**known_returns)
        outcome_360m = _as_float(candidate.get("post_open_360m_return_pct"))
        row = {
            "rank": candidate.get("rank") or candidate.get("shadow_preopen_rank") or idx,
            "ticker": ticker,
            "name": candidate.get("name", ""),
            "known_at": known_at.isoformat(timespec="seconds"),
            "known_returns": known_returns,
            "momentum_state": state,
            "outcome_360m_pct": outcome_360m,
            "selected": False,
            "selection_reason": "",
        }
        ret5 = known_returns.get("ret_5m_pct")
        ret30 = known_returns.get("ret_30m_pct")
        passes = ret5 is not None and ret5 >= float(min_ret_5m)
        if min_ret_30m is not None:
            passes = passes and ret30 is not None and ret30 >= float(min_ret_30m)
        if passes:
            row["selected"] = True
            row["selection_reason"] = (
                f"ret_5m>={min_ret_5m}"
                + (f" and ret_30m>={min_ret_30m}" if min_ret_30m is not None else "")
            )
            selected.append(row)
        rows.append(row)
    selected = selected[:max_items]
    selected_tickers = {row["ticker"] for row in selected}
    for row in rows:
        row["selected"] = row["ticker"] in selected_tickers
    outcomes = [row["outcome_360m_pct"] for row in selected if row["outcome_360m_pct"] is not None]
    avg_outcome = round(sum(outcomes) / len(outcomes), 4) if outcomes else None
    return {
        "market": str(market).upper(),
        "session_date": session_date,
        "decision_minutes": int(decision_minutes),
        "known_at": known_at.isoformat(timespec="seconds"),
        "rule": {
            "min_ret_5m": min_ret_5m,
            "min_ret_30m": min_ret_30m,
            "max_items": max_items,
        },
        "candidate_count": len(rows),
        "selected_count": len(selected),
        "selected_avg_outcome_360m_pct": avg_outcome,
        "selected": selected,
        "rows": rows,
        "future_blind_note": "Selection used only returns known by known_at. outcome_360m_pct is evaluation only.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Future-blind replay for preopen candidate files.")
    parser.add_argument("--market", required=True, choices=["KR", "US"])
    parser.add_argument("--preopen-file", required=True)
    parser.add_argument("--decision-minutes", type=int, default=5)
    parser.add_argument("--min-ret-5m", type=float, default=1.0)
    parser.add_argument("--min-ret-30m", type=float, default=None)
    parser.add_argument("--max-items", type=int, default=10)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    payload = json.loads(Path(args.preopen_file).read_text(encoding="utf-8"))
    replay = build_replay(
        payload,
        market=args.market,
        decision_minutes=args.decision_minutes,
        min_ret_5m=args.min_ret_5m,
        min_ret_30m=args.min_ret_30m,
        max_items=args.max_items,
    )
    text = json.dumps(replay, ensure_ascii=False, indent=2)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
