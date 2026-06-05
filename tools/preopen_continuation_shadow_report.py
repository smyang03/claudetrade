from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from preopen.continuation_shadow import build_report_payload, render_report_markdown, write_report_markdown


def main() -> int:
    parser = argparse.ArgumentParser(description="Build KR/US preopen continuation shadow report")
    parser.add_argument("--market", default="US")
    parser.add_argument("--mode", choices=["live", "paper"], default="live")
    parser.add_argument("--from", dest="date_from")
    parser.add_argument("--to", dest="date_to")
    parser.add_argument("--db-path")
    parser.add_argument("--output")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = build_report_payload(
        args.db_path,
        market=args.market,
        mode=args.mode,
        date_from=args.date_from,
        date_to=args.date_to,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.dry_run:
        print(render_report_markdown(payload))
        return 0
    if (payload.get("missing_db") or payload.get("schema_error")) and not args.output:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    path = write_report_markdown(payload, args.output)
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
