from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _norm_market(market: str) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


def _norm_ticker(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if _norm_market(market) == "US" else text


def _event_kind(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if text in {"order_sent", "order"}:
        return "ORDER_SENT"
    if text in {"filled", "order_filled", "fill"}:
        return "FILLED"
    if text in {"closed", "close"}:
        return "CLOSED"
    return text.upper()


def _entry_timing_path(root: Path, session_date: str, market: str, runtime_mode: str) -> Path:
    day = str(session_date or "").replace("-", "")
    return root / "logs" / "entry_timing" / f"{runtime_mode}_{day}_{_norm_market(market)}.jsonl"


def load_entry_timing_events(
    *,
    root: Path = ROOT,
    session_date: str,
    market: str,
    runtime_mode: str = "live",
) -> list[dict[str, Any]]:
    path = _entry_timing_path(root, session_date, market, runtime_mode)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = _event_kind(payload.get("event"))
        if kind not in {"ORDER_SENT", "FILLED", "CLOSED"}:
            continue
        state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
        ticker = _norm_ticker(market, payload.get("ticker") or state.get("ticker"))
        order_no = (
            state.get("order_no")
            or state.get("fill_order_no")
            or state.get("close_order_no")
            or payload.get("order_no")
            or ""
        )
        rows.append(
            {
                "event_type": kind,
                "ticker": ticker,
                "order_no": str(order_no or "").strip(),
                "occurred_at": payload.get("occurred_at") or state.get("occurred_at") or "",
                "source": str(path),
            }
        )
    return rows


def load_v2_lifecycle_events(
    *,
    root: Path = ROOT,
    session_date: str,
    market: str,
    runtime_mode: str = "live",
) -> list[dict[str, Any]]:
    db_path = root / "data" / "v2_event_store.db"
    if not db_path.exists():
        return []
    market_key = _norm_market(market)
    rows: list[dict[str, Any]] = []
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(
            """
            SELECT event_type, ticker, execution_id, occurred_at, payload_json
            FROM lifecycle_events
            WHERE market=? AND runtime_mode=? AND session_date=?
              AND event_type IN ('ORDER_SENT','FILLED','CLOSED')
            ORDER BY occurred_at
            """,
            (market_key, runtime_mode, session_date),
        ):
            payload: dict[str, Any] = {}
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            payload_order_no = str(payload.get("order_no") or payload.get("broker_order_no") or "").strip()
            execution_id = str(row["execution_id"] or "").strip()
            rows.append(
                {
                    "event_type": row["event_type"],
                    "ticker": _norm_ticker(market_key, row["ticker"]),
                    "order_no": payload_order_no or execution_id,
                    "broker_order_no": payload_order_no,
                    "execution_id": execution_id,
                    "occurred_at": row["occurred_at"],
                    "source": str(db_path),
                }
            )
    finally:
        conn.close()
    return rows


def _key(row: dict[str, Any], market: str) -> tuple[str, str, str]:
    return (
        str(row.get("event_type") or ""),
        _norm_ticker(market, row.get("ticker")),
        str(row.get("order_no") or ""),
    )


def _gap_severity(event_type: Any) -> tuple[str, str]:
    event = str(event_type or "").strip().upper()
    if event in {"FILLED", "CLOSED"}:
        return "HIGH", "filled_or_closed_event_missing_in_v2_lifecycle"
    if event == "ORDER_SENT":
        return "MEDIUM", "order_sent_event_missing_in_v2_lifecycle"
    return "LOW", "metadata_event_missing_in_v2_lifecycle"


def find_lifecycle_gaps(
    *,
    root: Path = ROOT,
    session_date: str,
    market: str,
    runtime_mode: str = "live",
) -> dict[str, Any]:
    market_key = _norm_market(market)
    timing_rows = load_entry_timing_events(
        root=root,
        session_date=session_date,
        market=market_key,
        runtime_mode=runtime_mode,
    )
    lifecycle_rows = load_v2_lifecycle_events(
        root=root,
        session_date=session_date,
        market=market_key,
        runtime_mode=runtime_mode,
    )
    lifecycle_keys = {_key(row, market_key) for row in lifecycle_rows}
    lifecycle_loose = {(row.get("event_type"), _norm_ticker(market_key, row.get("ticker"))) for row in lifecycle_rows}
    gaps: list[dict[str, Any]] = []
    for row in timing_rows:
        strict_key = _key(row, market_key)
        loose_key = (row.get("event_type"), _norm_ticker(market_key, row.get("ticker")))
        if strict_key in lifecycle_keys or (not row.get("order_no") and loose_key in lifecycle_loose):
            continue
        gaps.append(
            {
                "event_type": row.get("event_type"),
                "ticker": row.get("ticker"),
                "order_no": row.get("order_no"),
                "occurred_at": row.get("occurred_at"),
                "reason": "entry_timing_missing_in_v2_lifecycle",
                "severity": _gap_severity(row.get("event_type"))[0],
                "impact": _gap_severity(row.get("event_type"))[1],
            }
        )
    severity_counts: dict[str, int] = {}
    for gap in gaps:
        severity = str(gap.get("severity") or "LOW")
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
    return {
        "session_date": session_date,
        "market": market_key,
        "runtime_mode": runtime_mode,
        "entry_timing_events": len(timing_rows),
        "v2_lifecycle_events": len(lifecycle_rows),
        "gap_count": len(gaps),
        "severity_counts": severity_counts,
        "gaps": gaps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only QA for entry_timing vs v2 lifecycle gaps.")
    parser.add_argument("--session-date", required=True)
    parser.add_argument("--market", required=True, choices=["KR", "US"])
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    report = find_lifecycle_gaps(
        root=Path(args.root),
        session_date=args.session_date,
        market=args.market,
        runtime_mode=args.runtime_mode,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["gap_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
