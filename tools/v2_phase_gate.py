from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lifecycle.validation import V2PhaseValidator, render_report, report_to_json
from research.v2_simulation_report import build_simulation_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run cumulative V2 phase validation gates.")
    parser.add_argument("--phase", type=int, required=True, choices=range(1, 7))
    parser.add_argument("--qa", action="store_true", help="Run final QA checks after cumulative phase checks.")
    parser.add_argument(
        "--simulation-report",
        action="store_true",
        help="Generate/read V2 simulation baseline report and require it during QA.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    args = parser.parse_args()

    if args.simulation_report:
        build_simulation_report(ROOT)

    report = V2PhaseValidator(ROOT).validate(
        args.phase,
        qa=args.qa,
        simulation_report=args.simulation_report,
    )
    print(report_to_json(report) if args.json else render_report(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

