from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from preopen.continuation_shadow import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_SOURCE_LIMIT,
    backfill_outcomes,
    collect_candidates,
    init_schema,
    record_feature_snapshots,
    resolve_offset_min,
    run_eval,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="KR/US preopen continuation shadow pipeline")
    parser.add_argument("--market", default="US")
    parser.add_argument("--date", dest="session_date")
    parser.add_argument("--mode", choices=["live", "paper"], default="live")
    parser.add_argument(
        "--step",
        choices=["init", "collect", "feature", "eval", "backfill-outcome", "all"],
        default="collect",
    )
    parser.add_argument("--offset", "--offset-min", dest="offset_min", default="30")
    parser.add_argument("--db-path")
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES)
    parser.add_argument("--source-limit", type=int, default=DEFAULT_SOURCE_LIMIT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-claude", action="store_true")
    parser.add_argument("--ticker-selection-db")
    parser.add_argument("--candidate-audit-db")
    parser.add_argument("--ml-decisions-db")
    args = parser.parse_args()

    def resolved_offset() -> int:
        return resolve_offset_min(args.offset_min, market=args.market, session_date=args.session_date)

    if args.step == "init":
        if not args.dry_run:
            init_schema(args.db_path)
        result = {"status": "ok", "step": "init", "dry_run": bool(args.dry_run), "db_path": args.db_path}
    elif args.step == "collect":
        result = collect_candidates(
            args.market,
            session_date=args.session_date,
            mode=args.mode,
            db_path=args.db_path,
            source_limit=args.source_limit,
            dry_run=args.dry_run,
        )
    elif args.step == "feature":
        result = record_feature_snapshots(
            args.market,
            session_date=args.session_date,
            mode=args.mode,
            db_path=args.db_path,
            offset_min=resolved_offset(),
            dry_run=args.dry_run,
        )
    elif args.step == "eval":
        result = run_eval(
            args.market,
            session_date=args.session_date,
            mode=args.mode,
            db_path=args.db_path,
            offset_min=resolved_offset(),
            max_candidates=args.max_candidates,
            dry_run=args.dry_run,
            no_claude=args.no_claude,
        )
    elif args.step == "backfill-outcome":
        result = backfill_outcomes(
            args.market,
            session_date=args.session_date,
            mode=args.mode,
            db_path=args.db_path,
            ticker_selection_db_path=args.ticker_selection_db,
            candidate_audit_db_path=args.candidate_audit_db,
            ml_decisions_db_path=args.ml_decisions_db,
        )
    else:
        offset_value = resolved_offset()
        result = {
            "collect": collect_candidates(
                args.market,
                session_date=args.session_date,
                mode=args.mode,
                db_path=args.db_path,
                source_limit=args.source_limit,
                dry_run=args.dry_run,
            ),
            "feature": record_feature_snapshots(
                args.market,
                session_date=args.session_date,
                mode=args.mode,
                db_path=args.db_path,
                offset_min=offset_value,
                dry_run=args.dry_run,
            ),
            "eval": run_eval(
                args.market,
                session_date=args.session_date,
                mode=args.mode,
                db_path=args.db_path,
                offset_min=offset_value,
                max_candidates=args.max_candidates,
                dry_run=args.dry_run,
                no_claude=True if args.dry_run else args.no_claude,
            ),
            "backfill_outcome": backfill_outcomes(
                args.market,
                session_date=args.session_date,
                mode=args.mode,
                db_path=args.db_path,
                ticker_selection_db_path=args.ticker_selection_db,
                candidate_audit_db_path=args.candidate_audit_db,
                ml_decisions_db_path=args.ml_decisions_db,
            )
            if not args.dry_run
            else {"dry_run": True},
        }

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
