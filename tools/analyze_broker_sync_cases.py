from __future__ import annotations

import argparse
import json
import sys
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


def _is_broker_sync(row: dict[str, Any]) -> bool:
    fields = (
        row.get("strategy"),
        row.get("source_strategy"),
        row.get("path_type"),
        row.get("broker_fill_source"),
        row.get("execution_link_source"),
    )
    return any("broker_sync" in str(value or "").lower() for value in fields)


def analyze_broker_sync_cases(
    *,
    decisions_path: str | Path | None = None,
    date_arg: str = "",
    from_date: str = "",
    to_date: str = "",
    market: str = "",
    limit: int = 0,
) -> dict[str, Any]:
    path = Path(decisions_path) if decisions_path else get_runtime_path("state", "live_decisions.jsonl")
    market_key = str(market or "").upper()
    rows = [
        row
        for row in (_iter_jsonl(path) or [])
        if _in_range(_session(row), date_arg=date_arg, from_date=from_date, to_date=to_date)
        and (not market_key or str(row.get("market") or "").upper() == market_key)
    ]
    broker_rows = [row for row in rows if _is_broker_sync(row)]
    reviews_by_ticker_session: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("type") != "auto_sell_review":
            continue
        key = (str(row.get("market") or "").upper(), _session(row), str(row.get("ticker") or "").upper())
        reviews_by_ticker_session.setdefault(key, []).append(row)
    cases = []
    for row in broker_rows:
        key = (str(row.get("market") or "").upper(), _session(row), str(row.get("ticker") or "").upper())
        reviews = reviews_by_ticker_session.get(key, [])
        latest_review = reviews[-1] if reviews else {}
        cases.append(
            {
                "session_date": _session(row),
                "market": str(row.get("market") or "").upper(),
                "ticker": str(row.get("ticker") or "").upper(),
                "type": row.get("type", ""),
                "timestamp": row.get("timestamp", ""),
                "strategy": row.get("strategy", ""),
                "pnl_pct": row.get("pnl_pct"),
                "pnl_krw": row.get("pnl_krw"),
                "exit_reason": row.get("exit_reason", ""),
                "broker_fill_source": row.get("broker_fill_source", ""),
                "broker_position_injected": bool(row.get("broker_position_injected")),
                "pending_sell_reconcile": bool(row.get("pending_sell_reconcile")),
                "broker_truth_unavailable": bool(row.get("broker_truth_unavailable")),
                "auto_sell_review_action": row.get("auto_sell_review_action") or latest_review.get("auto_sell_review_action", ""),
                "auto_sell_review_detail": row.get("auto_sell_review_detail") or latest_review.get("auto_sell_review_detail", ""),
                "order_no": row.get("order_no", ""),
                "position_id": row.get("position_id", ""),
            }
        )
    cases.sort(key=lambda row: (row.get("session_date", ""), row.get("market", ""), row.get("ticker", ""), row.get("timestamp", "")))
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
        lines = ["# Broker Sync Case Review", "", f"- case_count: {summary.get('case_count', 0)}", ""]
        for row in summary.get("cases", []):
            lines.append(
                f"- {row.get('session_date')} {row.get('market')} {row.get('ticker')} pnl={row.get('pnl_pct')} exit={row.get('exit_reason')}"
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze broker_sync case outcomes from live decisions.")
    parser.add_argument("--decisions", default="", help="live_decisions.jsonl path")
    parser.add_argument("--date", default="", help="session date YYYY-MM-DD")
    parser.add_argument("--from-date", default="")
    parser.add_argument("--to-date", default="")
    parser.add_argument("--market", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", default="")
    parser.add_argument("--format", choices=["json", "md"], default="json")
    args = parser.parse_args(argv)
    summary = analyze_broker_sync_cases(
        decisions_path=args.decisions or None,
        date_arg=args.date,
        from_date=args.from_date,
        to_date=args.to_date,
        market=args.market,
        limit=args.limit,
    )
    written = write_output(summary, args.out, args.format)
    if written:
        summary["output"] = written
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
