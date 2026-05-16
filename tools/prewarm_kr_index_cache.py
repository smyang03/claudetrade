from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.kr_index_cache import load_kr_index_history


def prewarm(boards: list[str], *, lookback_days: int = 120) -> dict:
    results = {}
    for board in boards:
        frame = load_kr_index_history(board, lookback_days=lookback_days)
        results[board] = {
            "rows": int(len(frame)),
            "ok": bool(len(frame) > 0),
        }
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "lookback_days": lookback_days,
        "results": results,
        "ok": all(item["ok"] for item in results.values()) if results else False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prewarm KR KOSPI/KOSDAQ index history cache.")
    parser.add_argument("--boards", default="KOSPI,KOSDAQ")
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    boards = [item.strip().upper() for item in str(args.boards or "").split(",") if item.strip()]
    payload = prewarm(boards, lookback_days=max(1, int(args.lookback_days or 120)))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for board, item in payload["results"].items():
            print(f"{board}: rows={item['rows']} ok={str(item['ok']).lower()}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
