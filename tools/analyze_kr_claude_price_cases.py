from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path


def _iter_jsonl(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            yield row


def _session(row: dict[str, Any]) -> str:
    return str(row.get("session_date") or str(row.get("timestamp") or "")[:10] or "")


def _in_range(session_date: str, *, date_arg: str = "", from_date: str = "", to_date: str = "") -> bool:
    if date_arg:
        return session_date == str(date_arg)[:10]
    if from_date and session_date < str(from_date)[:10]:
        return False
    if to_date and session_date > str(to_date)[:10]:
        return False
    return True


def _is_claude_price(row: dict[str, Any]) -> bool:
    fields = (
        row.get("strategy"),
        row.get("source_strategy"),
        row.get("path_type"),
        row.get("selected_reason"),
    )
    return any("claude_price" in str(value or "").lower() for value in fields)


def analyze_kr_claude_price_cases(
    *,
    decisions_path: str | Path | None = None,
    date_arg: str = "",
    from_date: str = "",
    to_date: str = "",
    limit: int = 0,
) -> dict[str, Any]:
    path = Path(decisions_path) if decisions_path else get_runtime_path("state", "live_decisions.jsonl")
    rows = [
        row
        for row in (_iter_jsonl(path) or [])
        if str(row.get("market") or "").upper() == "KR"
        and _is_claude_price(row)
        and _in_range(_session(row), date_arg=date_arg, from_date=from_date, to_date=to_date)
    ]
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("pathb_path_run_id") or row.get("path_run_id") or "")
        if not key:
            key = f"{_session(row)}:{row.get('ticker', '')}:{row.get('order_no', '')}"
        case = by_key.setdefault(
            key,
            {
                "case_id": key,
                "session_date": _session(row),
                "ticker": str(row.get("ticker") or ""),
                "strategy_hint": str(row.get("strategy") or row.get("source_strategy") or ""),
                "entry": {},
                "closed": {},
            },
        )
        if row.get("type") == "entry":
            case["entry"] = row
        elif row.get("type") == "closed":
            case["closed"] = row
    cases = []
    for case in by_key.values():
        entry = case.get("entry") or {}
        closed = case.get("closed") or {}
        cases.append(
            {
                "case_id": case["case_id"],
                "session_date": case["session_date"],
                "ticker": case["ticker"],
                "plan_created_time": entry.get("timestamp", ""),
                "strategy_hint": case.get("strategy_hint", ""),
                "timing_style": entry.get("timing_style", "missing"),
                "reference_price": entry.get("reference_price", entry.get("selection_reference_target", "missing")),
                "buy_zone_low": entry.get("buy_zone_low", "missing"),
                "buy_zone_high": entry.get("buy_zone_high", "missing"),
                "target": entry.get("target_price", entry.get("pathb_reference_target", "missing")),
                "stop": closed.get("strategy_stop_price", entry.get("stop_price", "missing")),
                "fill_price": entry.get("entry_price_native", entry.get("entry_price", "missing")),
                "fill_time": entry.get("timestamp", ""),
                "exit_price": closed.get("exit_price_native", closed.get("exit_price", "missing")),
                "exit_time": closed.get("timestamp", ""),
                "exit_reason": closed.get("exit_reason", ""),
                "pnl_pct": closed.get("pnl_pct"),
                "mfe_pct": closed.get("position_mfe_pct", closed.get("peak_pnl_pct")),
                "mae_pct": closed.get("position_mae_pct"),
                "loss_cap_price": closed.get("loss_cap_price"),
                "effective_stop_price": closed.get("effective_stop_price"),
                "broker_fill_source": entry.get("broker_fill_source", closed.get("broker_fill_source", "")),
            }
        )
    cases.sort(key=lambda row: (row.get("session_date", ""), row.get("ticker", ""), row.get("fill_time", "")))
    if limit and limit > 0:
        cases = cases[:limit]
    losses = [row for row in cases if row.get("pnl_pct") is not None and float(row.get("pnl_pct") or 0) < 0]
    return {
        "input": str(path),
        "case_count": len(cases),
        "loss_count": len(losses),
        "cases": cases,
        "warnings": [] if path.exists() else [f"missing input: {path}"],
    }


def write_output(summary: dict[str, Any], out: str = "", fmt: str = "json") -> str:
    if not out:
        return ""
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "md":
        lines = ["# KR Claude Price Case Review", "", f"- case_count: {summary.get('case_count', 0)}", ""]
        for row in summary.get("cases", []):
            lines.append(
                f"- {row.get('session_date')} {row.get('ticker')} pnl={row.get('pnl_pct')} exit={row.get('exit_reason')}"
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze KR claude_price case outcomes from live decisions.")
    parser.add_argument("--decisions", default="", help="live_decisions.jsonl path")
    parser.add_argument("--date", default="", help="session date YYYY-MM-DD")
    parser.add_argument("--from-date", default="")
    parser.add_argument("--to-date", default="")
    parser.add_argument("--market", default="KR", help="accepted for CLI symmetry; KR only")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", default="")
    parser.add_argument("--format", choices=["json", "md"], default="json")
    args = parser.parse_args(argv)
    summary = analyze_kr_claude_price_cases(
        decisions_path=args.decisions or None,
        date_arg=args.date,
        from_date=args.from_date,
        to_date=args.to_date,
        limit=args.limit,
    )
    written = write_output(summary, args.out, args.format)
    if written:
        summary["output"] = written
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
