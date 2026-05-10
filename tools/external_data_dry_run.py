from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phase1_trainer.external_data_collectors import collect_ready_sources_dry_run
from phase1_trainer.external_data_store import DEFAULT_DB_PATH


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dry-run ready external data APIs and store normalized rows.")
    parser.add_argument("--env", default=str(ROOT / ".env.live"), help="dotenv file to load")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path")
    parser.add_argument("--date", default="", help="target date, YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--no-write", action="store_true", help="check APIs without writing DB rows")
    parser.add_argument("--json", action="store_true", help="print JSON summary")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    summary = collect_ready_sources_dry_run(
        db_path=args.db,
        env_path=args.env,
        target_date=args.date or None,
        write_db=not args.no_write,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"db_path={summary['db_path']}")
        print(f"write_db={summary['write_db']}")
        for check in summary["checks"]:
            fields = ",".join(check.get("received_fields", [])[:12])
            missing = ",".join(check.get("missing_fields", []))
            print(
                f"{check['source']}:{check['endpoint']} target={check['target']} "
                f"status={check['status']} rows={check['row_count']} "
                f"columns_ok={check['columns_ok']} missing=[{missing}] fields=[{fields}]"
            )
            if check.get("error"):
                print(f"  error={check['error']}")
        if summary.get("table_counts"):
            print("table_counts=" + json.dumps(summary["table_counts"], ensure_ascii=False, sort_keys=True))
    failed = [c for c in summary["checks"] if c["status"] == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
