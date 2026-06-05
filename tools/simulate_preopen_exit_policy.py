from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))

STOP_EXIT_REASONS = {"hard_stop", "loss_cap", "claude_price_stop"}
STOP_CLOSE_REASONS = {
    "CLOSED_HARD_STOP",
    "CLOSED_LOSS_CAP",
    "CLOSED_CLAUDE_PRICE_STOP",
}
POSITIVE_CLOSE_REASONS = {
    "CLOSED_CLAUDE_PRICE_TARGET",
    "CLOSED_CLAUDE_SELL",
    "CLOSED_CLAUDE_PRICE_PRE_CLOSE",
    "CLOSED_PROFIT_LADDER",
    "CLOSED_TRAILING_STOP",
}


def parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        normalized = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def second_sunday(year: int, month: int) -> date:
    current = date(year, month, 1)
    offset = (6 - current.weekday()) % 7
    return current + timedelta(days=offset + 7)


def first_sunday(year: int, month: int) -> date:
    current = date(year, month, 1)
    offset = (6 - current.weekday()) % 7
    return current + timedelta(days=offset)


def us_regular_open_kst(session_day: date) -> datetime:
    dst_start = second_sunday(session_day.year, 3)
    dst_end = first_sunday(session_day.year, 11)
    open_clock = time(22, 30) if dst_start <= session_day < dst_end else time(23, 30)
    return datetime.combine(session_day, open_clock, tzinfo=KST)


def in_preopen_window(ts: datetime, window_min: int) -> bool:
    regular_open = us_regular_open_kst(ts.date())
    return regular_open - timedelta(minutes=window_min) <= ts < regular_open


def classify_preopen_stop(pnl_pct: float, stop_distance_pct: float | None = None) -> dict[str, Any]:
    if pnl_pct > 0:
        return {
            "severity": "profit_protective_stop",
            "policy_decision": "SELL_NOW",
            "severity_boundary_case": False,
        }
    if pnl_pct <= -2.5 or (stop_distance_pct is not None and stop_distance_pct <= -1.0):
        return {
            "severity": "severe_loss_stop",
            "policy_decision": "SELL_NOW",
            "severity_boundary_case": False,
        }
    if pnl_pct > -1.5 and (stop_distance_pct is None or stop_distance_pct >= -0.75):
        return {
            "severity": "shallow_loss_stop",
            "policy_decision": "DEFER_OPEN_RECHECK",
            "severity_boundary_case": False,
        }
    return {
        "severity": "boundary_loss_stop",
        "policy_decision": "SELL_NOW",
        "severity_boundary_case": True,
    }


def load_live_closed(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("type") or "") != "closed":
            continue
        if str(row.get("market") or "").upper() != "US":
            continue
        ts = parse_dt(row.get("timestamp"))
        if ts is None:
            continue
        row["_ts"] = ts
        rows.append(row)
    return sorted(rows, key=lambda item: item["_ts"])


def load_hold_advisor_rows(log_dir: Path, start: datetime, end: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not log_dir.exists():
        return rows
    current = start.date()
    while current <= end.date():
        path = log_dir / f"decisions_{current.isoformat()}.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("market") or "").upper() != "US":
                    continue
                ts = parse_dt(row.get("ts"))
                if ts is None or not (start <= ts <= end):
                    continue
                row["_ts"] = ts
                rows.append(row)
        current += timedelta(days=1)
    return sorted(rows, key=lambda item: item["_ts"])


def live_preopen_cases(closed_rows: list[dict[str, Any]], window_min: int) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for row in closed_rows:
        exit_reason = str(row.get("exit_reason") or "").strip()
        if exit_reason not in STOP_EXIT_REASONS:
            continue
        ts = row["_ts"]
        if not in_preopen_window(ts, window_min):
            continue
        pnl_pct = safe_float(row.get("pnl_pct"))
        policy = classify_preopen_stop(pnl_pct)
        cases.append(
            {
                "source": "state/live_decisions.jsonl",
                "closed_at": ts.isoformat(timespec="seconds"),
                "ticker": str(row.get("ticker") or "").upper(),
                "path_run_id": str(row.get("pathb_path_run_id") or ""),
                "exit_reason": exit_reason,
                "exit_price": safe_float(row.get("exit_price")),
                "pnl_pct": round(pnl_pct, 6),
                "regular_open_at": us_regular_open_kst(ts.date()).isoformat(timespec="seconds"),
                "old_behavior": "PREOPEN_SELL_EXECUTED",
                **policy,
            }
        )
    return cases


def post_close_review_skips(
    advisor_rows: list[dict[str, Any]],
    closed_rows: list[dict[str, Any]],
    *,
    max_after_close_min: int = 30,
) -> list[dict[str, Any]]:
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in closed_rows:
        by_ticker[str(row.get("ticker") or "").upper()].append(row)
    skips: list[dict[str, Any]] = []
    for row in advisor_rows:
        if str(row.get("decision") or "").upper() != "SELL":
            continue
        ticker = str(row.get("ticker") or "").upper()
        ts = row["_ts"]
        prior = [
            item
            for item in by_ticker.get(ticker, [])
            if item["_ts"] <= ts and ts - item["_ts"] <= timedelta(minutes=max_after_close_min)
        ]
        if not prior:
            continue
        closed = max(prior, key=lambda item: item["_ts"])
        skips.append(
            {
                "source": "logs/hold_advisor",
                "review_at": ts.isoformat(timespec="seconds"),
                "ticker": ticker,
                "decision_stage": str(row.get("decision_stage") or ""),
                "advisor_decision": str(row.get("decision") or ""),
                "pnl_pct_at_review": safe_float(row.get("pnl_pct")),
                "matched_closed_at": closed["_ts"].isoformat(timespec="seconds"),
                "matched_exit_reason": str(closed.get("exit_reason") or ""),
                "policy_decision": "SKIP_STALE_OR_CLOSED",
                "skip_reason": "position_already_closed",
            }
        )
    return skips


def connect_readonly(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def load_db_close_reason_summary(db_path: Path) -> dict[str, Any]:
    conn = connect_readonly(db_path)
    if conn is None:
        return {"available": False, "path": str(db_path)}
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT close_reason, COUNT(*) AS n, AVG(pnl_pct) AS avg_pnl,
                       SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) AS wins
                FROM v2_learning_performance
                WHERE market='US'
                  AND runtime_mode='live'
                  AND path_type='claude_price'
                  AND pnl_pct IS NOT NULL
                  AND (closed=1 OR UPPER(status)='CLOSED')
                GROUP BY close_reason
                ORDER BY n DESC, close_reason
                """
            )
        ]
    finally:
        conn.close()
    total = sum(int(row["n"] or 0) for row in rows)
    positive_reasons = {
        str(row["close_reason"] or ""): {
            "n": int(row["n"] or 0),
            "avg_pnl": round(safe_float(row["avg_pnl"]), 6),
            "wins": int(row["wins"] or 0),
        }
        for row in rows
        if str(row["close_reason"] or "") in POSITIVE_CLOSE_REASONS
    }
    stop_reasons = {
        str(row["close_reason"] or ""): {
            "n": int(row["n"] or 0),
            "avg_pnl": round(safe_float(row["avg_pnl"]), 6),
            "wins": int(row["wins"] or 0),
        }
        for row in rows
        if str(row["close_reason"] or "") in STOP_CLOSE_REASONS
    }
    return {
        "available": True,
        "path": str(db_path),
        "readonly_uri": True,
        "total_us_live_claude_price_closed": total,
        "positive_paths_to_preserve": positive_reasons,
        "stop_paths": stop_reasons,
    }


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    live_path = Path(args.live_decisions)
    log_dir = Path(args.hold_advisor_dir)
    db_path = Path(args.ml_db)
    closed_rows = load_live_closed(live_path)
    if args.date:
        selected_day = date.fromisoformat(args.date)
        closed_rows = [row for row in closed_rows if row["_ts"].date() == selected_day]
        advisor_start = datetime.combine(selected_day, time(0, 0), tzinfo=KST)
        advisor_end = datetime.combine(selected_day, time(23, 59, 59), tzinfo=KST)
    else:
        advisor_start = closed_rows[0]["_ts"] if closed_rows else datetime.now(KST)
        advisor_end = closed_rows[-1]["_ts"] if closed_rows else datetime.now(KST)
    advisor_rows = load_hold_advisor_rows(log_dir, advisor_start, advisor_end)
    preopen_cases = live_preopen_cases(closed_rows, args.window_min)
    stale_skips = post_close_review_skips(advisor_rows, closed_rows)
    decision_counts = Counter(case["policy_decision"] for case in preopen_cases)
    decision_counts.update(case["policy_decision"] for case in stale_skips)
    return {
        "read_only": True,
        "sources": {
            "live_decisions": str(live_path),
            "hold_advisor_dir": str(log_dir),
            "ml_db": str(db_path),
        },
        "policy_thresholds": {
            "defer": "pnl_pct > -1.5 and stop_distance_pct is missing or >= -0.75",
            "sell_now": "pnl_pct <= -2.5 or stop_distance_pct <= -1.0 or pnl_pct > 0",
            "boundary": "SELL_NOW with severity_boundary_case=true",
            "preopen_window_min": args.window_min,
        },
        "summary": {
            "preopen_stop_cases": len(preopen_cases),
            "post_close_review_skips": len(stale_skips),
            "policy_decisions": dict(sorted(decision_counts.items())),
        },
        "preopen_cases": preopen_cases,
        "post_close_review_skips": stale_skips,
        "db_close_reason_summary": load_db_close_reason_summary(db_path),
    }


def print_text(payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    print("Read-only preopen exit policy simulation")
    print(f"preopen_stop_cases={summary['preopen_stop_cases']}")
    print(f"post_close_review_skips={summary['post_close_review_skips']}")
    print(f"policy_decisions={json.dumps(summary['policy_decisions'], ensure_ascii=False, sort_keys=True)}")
    for case in payload["preopen_cases"]:
        print(
            "case "
            f"{case['ticker']} closed_at={case['closed_at']} "
            f"exit={case['exit_reason']} pnl={case['pnl_pct']:+.4f}% "
            f"=> {case['policy_decision']} ({case['severity']})"
        )
    for case in payload["post_close_review_skips"]:
        print(
            "skip "
            f"{case['ticker']} review_at={case['review_at']} "
            f"stage={case['decision_stage']} after_closed={case['matched_closed_at']} "
            f"=> {case['policy_decision']}"
        )
    db_summary = payload["db_close_reason_summary"]
    if db_summary.get("available"):
        print(f"db_us_live_claude_price_closed={db_summary['total_us_live_claude_price_closed']}")
        print(
            "positive_paths_to_preserve="
            + json.dumps(db_summary["positive_paths_to_preserve"], ensure_ascii=False, sort_keys=True)
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only simulation for US PathB preopen stop defer policy."
    )
    parser.add_argument("--date", default="", help="KST date filter, e.g. 2026-06-04")
    parser.add_argument("--window-min", type=int, default=90)
    parser.add_argument("--live-decisions", default=str(ROOT / "state" / "live_decisions.jsonl"))
    parser.add_argument("--hold-advisor-dir", default=str(ROOT / "logs" / "hold_advisor"))
    parser.add_argument("--ml-db", default=str(ROOT / "data" / "ml" / "decisions.db"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    payload = build_payload(args)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_text(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
