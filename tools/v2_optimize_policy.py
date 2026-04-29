from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.v2_policy_optimizer import OptimizerConfig, build_policy_optimization_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Optimize V2 operating policy from previous audit trade artifacts.")
    parser.add_argument("--min-trades", type=int, default=60)
    parser.add_argument("--min-validation-trades", type=int, default=20)
    parser.add_argument("--validation-ratio", type=float, default=0.3)
    parser.add_argument("--min-validation-pf", type=float, default=1.05)
    parser.add_argument("--min-validation-avg-pct", type=float, default=0.02)
    parser.add_argument("--min-positive-source-ratio", type=float, default=0.45)
    parser.add_argument("--usd-krw", type=float, default=1400.0)
    args = parser.parse_args()

    paths = build_policy_optimization_report(
        ROOT,
        config=OptimizerConfig(
            min_trades=args.min_trades,
            min_validation_trades=args.min_validation_trades,
            validation_ratio=args.validation_ratio,
            min_validation_pf=args.min_validation_pf,
            min_validation_avg_pct=args.min_validation_avg_pct,
            min_positive_source_ratio=args.min_positive_source_ratio,
            usd_krw=args.usd_krw,
        ),
    )
    print(f"json: {paths['json']}")
    print(f"markdown: {paths['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
