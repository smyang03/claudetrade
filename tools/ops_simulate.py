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
from runtime.rehearsal.simulation import (
    all_simulation_scenarios,
    load_price_tape,
    load_simulation_batch,
    run_guarded_simulation,
    simulation_case_from_tape,
    simulation_case_for_scenario,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run analysis simulations in live-semantics rehearsal sandbox.")
    parser.add_argument("--profile", default="live", choices=["live"])
    parser.add_argument("--backend", default="fixture", choices=["fixture"])
    parser.add_argument("--scenario", default="us_pathb_buy_zone_replay", choices=all_simulation_scenarios())
    parser.add_argument("--all", action="store_true", help="run all built-in analysis scenarios")
    parser.add_argument("--tape-file", default="", help="CSV/JSON/JSONL price tape for a single case")
    parser.add_argument("--batch-file", default="", help="JSON batch file containing scenarios and/or tape cases")
    parser.add_argument("--market", default="US", choices=["KR", "US"], help="market for --tape-file")
    parser.add_argument("--ticker", default="NVDA", help="ticker for --tape-file")
    parser.add_argument("--path-type", default="", help="optional path type for --tape-file")
    parser.add_argument("--name", default="", help="optional simulation case name")
    parser.add_argument("--runtime-root", default="", help="optional sandbox root")
    parser.add_argument("--report-out", default="", help="JSON output path inside sandbox; relative paths are sandbox-relative")
    parser.add_argument("--csv-out", default="", help="CSV output path inside sandbox; relative paths are sandbox-relative")
    parser.add_argument("--set", dest="sets", action="append", default=[], help="override parameter, e.g. confidence_threshold=0.6")
    parser.add_argument("--sweep", action="append", default=[], help="sweep parameter values, e.g. confidence_threshold=0.4,0.5,0.7")
    parser.add_argument("--json", action="store_true", help="print full JSON report")
    return parser


def _parse_scalar(value: str) -> Any:
    raw = str(value).strip()
    lower = raw.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in {"none", "null"}:
        return None
    try:
        if "." not in raw and "e" not in lower:
            return int(raw)
        return float(raw)
    except ValueError:
        return raw


def _parse_assignments(items: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise RehearsalGuardError(f"invalid --set assignment: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise RehearsalGuardError(f"invalid empty --set key: {item}")
        parsed[key] = _parse_scalar(value)
    return parsed


def _parse_sweep(items: list[str]) -> dict[str, list[Any]]:
    parsed: dict[str, list[Any]] = {}
    for item in items:
        if "=" not in item:
            raise RehearsalGuardError(f"invalid --sweep assignment: {item}")
        key, values = item.split("=", 1)
        key = key.strip()
        if not key:
            raise RehearsalGuardError(f"invalid empty --sweep key: {item}")
        choices = [_parse_scalar(part) for part in values.split(",") if part.strip()]
        if not choices:
            raise RehearsalGuardError(f"empty --sweep values: {item}")
        parsed[key] = choices
    return parsed


def _context_for(args: argparse.Namespace):
    if args.batch_file:
        scenario_name = f"ops_simulation_batch_{Path(args.batch_file).stem}"
    elif args.tape_file:
        scenario_name = f"ops_simulation_tape_{Path(args.tape_file).stem}"
    else:
        scenario_name = "ops_simulation_all" if args.all else f"ops_simulation_{args.scenario}"
    root = Path(args.runtime_root).expanduser() if args.runtime_root else None
    return create_rehearsal_context(
        scenario=scenario_name,
        runtime_root=root,
        profile=args.profile,
        backend=args.backend,
    )


def _cases_and_config(args: argparse.Namespace):
    batch_overrides: dict[str, Any] = {}
    batch_sweep: dict[str, list[Any]] = {}
    if args.batch_file:
        cases, batch_overrides, batch_sweep = load_simulation_batch(Path(args.batch_file))
    elif args.tape_file:
        tape_path = Path(args.tape_file)
        tape = load_price_tape(tape_path)
        cases = [
            simulation_case_from_tape(
                name=args.name or tape_path.stem,
                market=args.market,
                ticker=args.ticker,
                path_type=args.path_type,
                price_tape=tape,
            )
        ]
    else:
        names = all_simulation_scenarios() if args.all else [args.scenario]
        cases = [simulation_case_for_scenario(name) for name in names]

    overrides = dict(batch_overrides)
    overrides.update(_parse_assignments(args.sets))
    sweep = dict(batch_sweep)
    sweep.update(_parse_sweep(args.sweep))
    return cases, overrides, sweep


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        context = _context_for(args)
        cases, overrides, sweep = _cases_and_config(args)
        csv_path = Path(args.csv_out) if args.csv_out else Path("reports") / f"{context.scenario}_simulation.csv"
        report = run_guarded_simulation(
            context,
            cases,
            overrides=overrides,
            sweep=sweep,
            report_path=Path(args.report_out) if args.report_out else None,
            csv_path=csv_path,
        )
    except RehearsalGuardError as exc:
        payload = {"ok": False, "error": str(exc)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"ops_simulate failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        summary = report.get("summary") or {}
        best = report.get("best") or {}
        worst = report.get("worst") or {}
        print(
            "ok "
            f"cases={summary.get('case_count')} entered={summary.get('entered_count')} "
            f"blocked={summary.get('blocked_count')} avg_score={summary.get('avg_score')} "
            f"report={report.get('report_path')} csv={report.get('csv_path')}"
        )
        print(f"best scenario={best.get('scenario')} score={best.get('score')} sweep={best.get('sweep')}")
        print(f"worst scenario={worst.get('scenario')} score={worst.get('score')} sweep={worst.get('sweep')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
