from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lifecycle.event_store import EventStore
from runtime.broker_truth_snapshot import BrokerTruthSnapshot
from runtime.execution_advisor_runtime import ExecutionAdvisorRuntime
from runtime_paths import get_runtime_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Execution Advisor simulation")
    parser.add_argument("--market", choices=["KR", "US"], default="US")
    parser.add_argument("--mode", choices=["live", "paper"], default="live")
    parser.add_argument("--snapshot", default="")
    parser.add_argument("--event-store", default="")
    parser.add_argument("--include-existing-noop", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot) if args.snapshot else get_runtime_path("state", f"{args.mode}_broker_truth_snapshot.json")
    store_path = Path(args.event_store) if args.event_store else get_runtime_path("data", "v2_event_store.db")
    runtime = ExecutionAdvisorRuntime(
        runtime_mode=args.mode,
        event_store=EventStore(store_path),
        broker_truth=BrokerTruthSnapshot(runtime_mode=args.mode, path=snapshot_path),
        append_events=False,
        enabled=True,
    )
    result = runtime.scan_market(args.market, force=True, include_existing_noop=args.include_existing_noop)
    result["simulation"] = {
        "read_only": True,
        "orders_submitted": 0,
        "events_appended": 0,
        "snapshot": str(snapshot_path),
        "event_store": str(store_path),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        print(_format_text(result))
    return 0 if result.get("ok") else 1


def _format_text(result: dict[str, Any]) -> str:
    decisions = list(result.get("decisions") or [])
    lines = [
        f"Execution Advisor simulation market={result.get('market')} mode={result.get('runtime_mode')}",
        f"broker_truth_fresh={result.get('broker_truth_fresh')} decisions={len(decisions)}",
    ]
    for decision in decisions:
        lines.append(
            "{ticker} action={action} reason={reason}".format(
                ticker=decision.get("ticker", ""),
                action=decision.get("action", ""),
                reason=decision.get("reason_code", ""),
            )
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
