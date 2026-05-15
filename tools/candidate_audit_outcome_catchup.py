from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.update_candidate_audit_outcomes import DEFAULT_HORIZONS, update_candidate_audit_outcomes


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(str(value)[:10]).date()


def _iter_session_dates(*, date_arg: str = "", from_date: str = "", to_date: str = "", days: int = 0) -> list[str]:
    if date_arg:
        return [_parse_date(date_arg).isoformat()]
    if from_date or to_date:
        start = _parse_date(from_date or to_date)
        end = _parse_date(to_date or from_date)
        if end < start:
            start, end = end, start
        out: list[str] = []
        cursor = start
        while cursor <= end:
            out.append(cursor.isoformat())
            cursor += timedelta(days=1)
        return out
    span = max(1, int(days or 5))
    end = date.today()
    start = end - timedelta(days=span - 1)
    return [(start + timedelta(days=idx)).isoformat() for idx in range(span)]


def _parse_horizons(raw: str) -> tuple[int, ...]:
    values = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        values.append(int(part))
    return tuple(values) or DEFAULT_HORIZONS


def build_catchup_plan(
    *,
    date_arg: str = "",
    from_date: str = "",
    to_date: str = "",
    days: int = 0,
    market: str = "",
) -> list[dict[str, str]]:
    markets = [str(market).upper()] if str(market or "").strip() else ["KR", "US"]
    markets = [m for m in markets if m in {"KR", "US"}]
    return [
        {"session_date": session_date, "market": market_key}
        for session_date in _iter_session_dates(date_arg=date_arg, from_date=from_date, to_date=to_date, days=days)
        for market_key in markets
    ]


def run_catchup(
    *,
    db_path: str | Path | None = None,
    date_arg: str = "",
    from_date: str = "",
    to_date: str = "",
    days: int = 0,
    market: str = "",
    runtime_mode: str = "live",
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    dry_run: bool = False,
    write_report: bool = False,
    report_dir: str | Path | None = None,
) -> dict[str, Any]:
    plan = build_catchup_plan(date_arg=date_arg, from_date=from_date, to_date=to_date, days=days, market=market)
    results: list[dict[str, Any]] = []
    if not dry_run:
        for item in plan:
            results.append(
                update_candidate_audit_outcomes(
                    db_path=db_path,
                    session_date=item["session_date"],
                    market=item["market"],
                    runtime_mode=runtime_mode,
                    horizons=horizons,
                )
            )
    summary = {
        "dry_run": bool(dry_run),
        "runtime_mode": str(runtime_mode or "live").lower(),
        "horizons": list(horizons),
        "planned": plan,
        "results": results,
        "total_outcome_rows": sum(int(row.get("outcome_rows") or 0) for row in results),
    }
    if write_report:
        summary["report_path"] = str(_write_catchup_report(summary, report_dir=report_dir))
    return summary


def _write_catchup_report(summary: dict[str, Any], *, report_dir: str | Path | None = None) -> Path:
    out_dir = Path(report_dir) if report_dir else ROOT / "data" / "v2_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"candidate_audit_outcome_catchup_{stamp}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Catch up candidate audit outcome labels over sessions.")
    parser.add_argument("--db", default="", help="candidate audit DB path")
    parser.add_argument("--date", default="", help="single session date YYYY-MM-DD")
    parser.add_argument("--from-date", default="", help="first session date YYYY-MM-DD")
    parser.add_argument("--to-date", default="", help="last session date YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=5, help="recent calendar days when no date range is supplied")
    parser.add_argument("--market", default="", help="KR, US, or empty for both")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--horizons", default="30,60", help="comma-separated minute horizons")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    summary = run_catchup(
        db_path=args.db or None,
        date_arg=args.date,
        from_date=args.from_date,
        to_date=args.to_date,
        days=args.days,
        market=args.market,
        runtime_mode=args.runtime_mode,
        horizons=_parse_horizons(args.horizons),
        dry_run=args.dry_run,
        write_report=True,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
