"""Backfill and review the ops-only lesson action registry."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minority_report.lesson_actions import (  # noqa: E402
    AUTHORITY,
    classify_lesson_action,
    record_lesson_actions,
)
from runtime_paths import get_runtime_path  # noqa: E402


_FILE_RE = re.compile(r"^(?P<mode>live|paper)_(?P<day>\d{8})_(?P<market>KR|US)\.json$")


def _date_text(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:8]}"


def _records(
    *,
    mode: str,
    market: str,
    start_date: str,
    end_date: str,
) -> list[tuple[Path, dict[str, Any]]]:
    base = get_runtime_path("logs", "daily_judgment", make_parents=False)
    output: list[tuple[Path, dict[str, Any]]] = []
    if not base.exists():
        return output
    for path in sorted(base.glob(f"{mode}_*.json")):
        match = _FILE_RE.match(path.name)
        if not match:
            continue
        record_market = match.group("market")
        day = _date_text(match.group("day"))
        if market != "ALL" and record_market != market:
            continue
        if start_date and day < start_date:
            continue
        if end_date and day > end_date:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        # Intraday snapshots do not yet contain evidence for a post-session
        # action.  They will be picked up after session close.
        if not isinstance(payload, dict) or not payload.get("actual_result"):
            continue
        output.append((path, payload))
    return output


def build_actions(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    actions: list[dict[str, Any]] = []
    sources: list[str] = []
    for path, record in _records(
        mode=args.mode,
        market=args.market,
        start_date=args.start_date,
        end_date=args.end_date,
    ):
        market = str(record.get("market") or path.stem.rsplit("_", 1)[-1]).upper()
        day = str(record.get("date") or "")[:10]
        if not day:
            match = _FILE_RE.match(path.name)
            day = _date_text(match.group("day")) if match else ""
        actions.append(
            classify_lesson_action(
                market=market,
                session_date=day,
                postmortem=record.get("postmortem"),
                actual_result=record.get("actual_result"),
                trade_log=record.get("trades"),
                ops_review_snapshot=record.get("ops_review_snapshot"),
                runtime_safety_summary=record.get("runtime_safety_summary"),
                judgment_eval=record.get("judgment_eval"),
            )
        )
        sources.append(str(path))
    return actions, sources


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("live", "paper"), default="live")
    parser.add_argument("--market", choices=("ALL", "KR", "US"), default="ALL")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    actions, sources = build_actions(args)
    if args.dry_run:
        counts: dict[str, int] = {}
        for action in actions:
            root = str(action.get("root_cause") or "OBSERVATION")
            counts[root] = counts.get(root, 0) + 1
        result: dict[str, Any] = {
            "authority": AUTHORITY,
            "dry_run": True,
            "records": len(actions),
            "root_cause_counts": dict(sorted(counts.items())),
            "sources": sources,
            "actions": actions,
        }
    else:
        registry = record_lesson_actions(actions, path=args.output)
        result = {
            "authority": AUTHORITY,
            "dry_run": False,
            "records": len(actions),
            "output": str(args.output or get_runtime_path("state", "lesson_action_registry.json")),
            "summary": registry.get("summary", {}),
            "patterns": registry.get("patterns", {}),
        }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"authority={result['authority']} records={result['records']} dry_run={result['dry_run']}")
        summary = result.get("summary") or {"root_cause_counts": result.get("root_cause_counts", {})}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
