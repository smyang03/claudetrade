from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.candidate_counterfactual_store import CandidateCounterfactualStore
from runtime_paths import get_runtime_path


def _metadata(row: dict[str, Any], **updates: Any) -> str:
    try:
        meta = json.loads(row.get("metadata_json") or "{}")
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    meta.update(updates)
    return json.dumps(meta, ensure_ascii=False, sort_keys=True, default=str)


def update_counterfactual_outcomes(
    *,
    db_path: str | Path | None = None,
    session_date: str = "",
    market: str = "",
    retry_missing: bool = False,
) -> dict[str, Any]:
    store = CandidateCounterfactualStore(db_path or get_runtime_path("data", "audit", "candidate_audit.db"))
    rows = store.fetch_rows(session_date=session_date, market=market)
    filled = 0
    missing = 0
    skipped = 0
    for row in rows:
        status = str(row.get("status") or "")
        if status == "OUTCOME_FILLED":
            skipped += 1
            continue
        if status == "DATA_MISSING" and not retry_missing:
            skipped += 1
            continue
        # This CLI intentionally does not invent labels. It marks rows as
        # missing when no collected minute/daily source has populated outcomes.
        if row.get("entry_price") is None or row.get("trigger_time") is None:
            store.mark_outcome(
                int(row["id"]),
                status="DATA_MISSING",
                metadata_json=_metadata(
                    row,
                    missing_fields=["entry_price", "trigger_time"],
                    source_attempts=["intraday_cache", "kis_yfinance", "daily_close"],
                    final_attempt_at=datetime.now().isoformat(timespec="seconds"),
                    reason="entry_or_trigger_missing",
                ),
            )
            missing += 1
            continue
        store.mark_outcome(
            int(row["id"]),
            status="DATA_MISSING",
            metadata_json=_metadata(
                row,
                missing_fields=["outcome_30m_pct", "outcome_60m_pct", "outcome_close_pct"],
                source_attempts=["intraday_cache", "kis_yfinance", "daily_close"],
                final_attempt_at=datetime.now().isoformat(timespec="seconds"),
                reason="minute_data_not_available",
            ),
        )
        missing += 1
    return {"ok": True, "rows": len(rows), "filled": filled, "data_missing": missing, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill counterfactual path outcomes when local price labels exist.")
    parser.add_argument("--db-path", default=str(get_runtime_path("data", "audit", "candidate_audit.db")))
    parser.add_argument("--date", default="")
    parser.add_argument("--market", default="")
    parser.add_argument("--retry-missing", action="store_true")
    args = parser.parse_args(argv)
    payload = update_counterfactual_outcomes(
        db_path=args.db_path,
        session_date=args.date,
        market=args.market,
        retry_missing=args.retry_missing,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
