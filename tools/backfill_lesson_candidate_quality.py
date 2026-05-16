from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minority_report.lesson_quality import (
    KNOWN_METRIC_KEYS,
    apply_lesson_conflict_guards,
    lesson_quality_fields,
)


DEFAULT_PATH = ROOT / "state" / "lesson_candidates.json"


def _metric_key(row: dict[str, Any]) -> str:
    key = str(row.get("metric_key") or "").strip()
    if key:
        return key
    if str(row.get("id") or "") == "affordability_fail_cluster":
        return "affordability_fail_count"
    return ""


def _backfill_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    updated = json.loads(json.dumps(payload, ensure_ascii=False))
    changes: list[dict[str, Any]] = []
    markets = updated.setdefault("markets", {})
    for market, rows in list(markets.items()):
        if not isinstance(rows, list):
            continue
        before_rows = [
            json.loads(json.dumps(row, ensure_ascii=False))
            if isinstance(row, dict) else row
            for row in rows
        ]
        for row in rows:
            if not isinstance(row, dict):
                continue
            metric_key = _metric_key(row)
            fields = lesson_quality_fields(
                metric_key,
                str(row.get("scope") or ""),
                row.get("metric_value"),
                int(row.get("sample_count") or 0),
            )
            row.update(fields)
        apply_lesson_conflict_guards(rows)
        for before, row in zip(before_rows, rows):
            if not isinstance(before, dict) or not isinstance(row, dict):
                continue
            metric_key = _metric_key(row)
            keys = [
                "quality_version",
                "claude_actionable",
                "ops_flag",
                "action_hint",
                "min_sample",
                "quality_conflict_suppressed",
                "quality_conflict_winner",
            ]
            old = {key: before.get(key) for key in keys}
            new = {key: row.get(key) for key in keys}
            if old != new:
                changes.append(
                    {
                        "market": market,
                        "id": row.get("id"),
                        "metric_key": metric_key,
                        "known_metric": metric_key in KNOWN_METRIC_KEYS,
                        "old": old,
                        "new": new,
                    }
                )
    return updated, changes


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill active lesson quality fields.")
    parser.add_argument("--path", default=str(DEFAULT_PATH))
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing. Default behavior.")
    parser.add_argument("--write", action="store_true", help="Write changes and create a backup.")
    parser.add_argument("--backup", action="store_true", help="Accepted for compatibility; --write always backs up.")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.is_absolute():
        path = ROOT / path
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    updated, changes = _backfill_payload(payload)
    result = {
        "path": str(path),
        "write": bool(args.write),
        "change_count": len(changes),
        "changes": changes,
    }
    if args.write:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_name(f"{path.stem}.backup_{stamp}{path.suffix}")
        shutil.copy2(path, backup_path)
        path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["backup_path"] = str(backup_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
