from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.kr_disclosure_observer import refresh_kr_disclosure_observer
from runtime_paths import get_runtime_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh research-only KR DART disclosure observer cache"
    )
    parser.add_argument("--days-back", type=int, default=7)
    parser.add_argument("--max-pages-per-type", type=int, default=8)
    parser.add_argument("--timeout-sec", type=float, default=15.0)
    args = parser.parse_args()
    result = refresh_kr_disclosure_observer(
        days_back=args.days_back,
        max_pages_per_type=args.max_pages_per_type,
        timeout_sec=args.timeout_sec,
    )
    status_path = get_runtime_path("state", "kr_disclosure_observer_status.json")
    status_path.parent.mkdir(parents=True, exist_ok=True)
    temp = status_path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(
            {
                **result,
                "error": "" if result.get("ok") else str(result.get("reason") or "refresh_failed"),
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    temp.replace(status_path)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
