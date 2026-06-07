from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.rehearsal.context import RehearsalGuardError, create_rehearsal_context
from runtime.rehearsal.fixtures import all_scenarios, fixture_for_scenario
from runtime.rehearsal.scenarios import run_rehearsal_scenario


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run live-semantics ops rehearsal with fixture backends.")
    parser.add_argument("--profile", default="live", choices=["live"])
    parser.add_argument("--backend", default="fixture", choices=["fixture"])
    parser.add_argument("--scenario", default="kr_patha_buy", choices=all_scenarios())
    parser.add_argument("--all", action="store_true", help="run all built-in rehearsal scenarios")
    parser.add_argument("--runtime-root", default="", help="optional sandbox root; --all appends scenario name")
    parser.add_argument("--json", action="store_true", help="print JSON summary")
    return parser


def _context_for(args: argparse.Namespace, scenario: str):
    root = Path(args.runtime_root).expanduser() if args.runtime_root else None
    if root is not None and args.all:
        root = root / scenario
    return create_rehearsal_context(
        scenario=scenario,
        runtime_root=root,
        profile=args.profile,
        backend=args.backend,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    scenarios = all_scenarios() if args.all else [args.scenario]
    results: list[dict[str, Any]] = []
    try:
        for scenario in scenarios:
            context = _context_for(args, scenario)
            fixture = fixture_for_scenario(scenario)
            results.append(run_rehearsal_scenario(context, fixture))
    except RehearsalGuardError as exc:
        payload = {"ok": False, "error": str(exc), "results": results}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"ops_rehearsal failed: {exc}", file=sys.stderr)
        return 2

    payload = {"ok": True, "results": results}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        for item in results:
            print(
                f"ok scenario={item['scenario']} intents={len(item.get('order_intents') or [])} "
                f"sandbox={item['sandbox_root']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
