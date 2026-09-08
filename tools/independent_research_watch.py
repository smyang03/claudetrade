"""Research-only input consumer. Missing verified inputs produce visible BLOCKED status."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime.independent_research_strategies import evaluate


def validate_calendar(snapshot, now):
    from preopen.scheduler import _exchange_session_close_dt, _exchange_session_open_dt
    from tools.canary_materializer import _next_session
    from runtime.independent_research_strategies import stamp
    expected = _next_session("US", now)
    if snapshot["entry_session"] != expected:
        raise ValueError("entry_calendar_mismatch")
    closes = []
    for offset in range(15):
        day = (now.date() - timedelta(days=offset)).isoformat()
        close = _exchange_session_close_dt("US", day)
        if close is not None and close <= now:
            closes.append((day, close))
    if not closes or snapshot["asof_session"] != max(closes)[0]:
        raise ValueError("not_latest_completed_session")
    if _next_session("US", max(closes)[1]) != expected:
        raise ValueError("first_entry_open_already_passed")
    if stamp(snapshot["cutoff_at"]) < max(closes)[1]:
        raise ValueError("cutoff_before_close")
    opening = _exchange_session_open_dt("US", expected)
    # An opening already passed must not become an assumed fill tomorrow.
    if opening is None or opening <= now:
        raise ValueError("entry_not_in_future")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ROOT / "data/shadow/independent_research_inputs_v1.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/analysis/independent_research_report.json")
    parser.add_argument("--ledger", type=Path, default=ROOT / "data/shadow/independent_research_observations.jsonl")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    error = None
    snapshot = {}
    try:
        snapshot = json.loads(args.input.read_text(encoding="utf-8"))
        validate_calendar(snapshot, now)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        error = str(exc)
        if error != "first_entry_open_already_passed":
            snapshot = {}
    result = evaluate(snapshot, now)
    if error == "first_entry_open_already_passed":
        for strategy in result["strategies"]:
            strategy["signals"] = []
            strategy["rejections"].append({"reason": error})
            if strategy["strategy_id"] == "r_multiasset_trend_v1" and strategy["status"] != "BLOCKED":
                strategy["status"] = "OBSERVING_ENTRY_WINDOW_CLOSED"
            else:
                strategy["status"] = "BLOCKED"
    result["input_error"] = error
    result["input_path"] = str(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(args.output)
    observation_id = ":".join([now.date().isoformat(), result["input_hash"]] +
                              [s["contract_hash"] for s in result["strategies"]])
    previous = set()
    if args.ledger.exists():
        # Fail visibly on a damaged ledger; do not silently discard audit history.
        previous = {json.loads(line)["observation_id"] for line in args.ledger.read_text(encoding="utf-8").splitlines() if line.strip()}
    if observation_id not in previous:
        args.ledger.parent.mkdir(parents=True, exist_ok=True)
        with args.ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"observation_id": observation_id, "kind": "RESEARCH_OBSERVATION_NOT_TRADE",
                                 "input_snapshot": snapshot, **result}, ensure_ascii=False, allow_nan=False) + "\n")
    print("[INDEPENDENT RESEARCH] " + ", ".join(f"{s['strategy_id']}={s['status']}" for s in result["strategies"]))
    return 0  # Data blocking is a research result, not a reason to stop sibling collectors.


if __name__ == "__main__":
    raise SystemExit(main())
