from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KST = timezone(timedelta(hours=9))


def _now_kst() -> datetime:
    return datetime.now(KST)


def _parse_dt(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        try:
            value = datetime.fromisoformat(text[:19])
        except Exception:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=KST)
    return value.astimezone(KST)


def _default_session_date(now: datetime) -> str:
    if now.hour < 9:
        return (now.date() - timedelta(days=1)).isoformat()
    return now.date().isoformat()


def _compact_day(session_date: str) -> str:
    return str(session_date or "").replace("-", "")


def _read_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    if limit is not None and limit > 0:
        lines = lines[-limit:]
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except Exception:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def _safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return ""


def _short(value: Any, max_len: int = 42) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "~"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,),
    ).fetchone()
    return bool(row)


def _candidate_snapshot(db_path: Path, market: str, session_date: str, mode: str = "live") -> dict[str, Any]:
    empty = {
        "available": False,
        "rows": 0,
        "counts": {},
        "action_counts": {},
        "route_counts": {},
        "plan_a": [],
        "pathb": [],
        "watch": [],
        "hard_blocks": [],
        "latest_known_at": "",
        "latest_updated_at": "",
    }
    if not db_path.exists():
        return {**empty, "error": "candidate_audit_db_missing"}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "audit_candidate_latest_rows"):
            conn.close()
            return {**empty, "error": "audit_candidate_latest_rows_missing"}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(audit_candidate_latest_rows)").fetchall()}
        mode_clause = "AND runtime_mode=?" if "runtime_mode" in columns else ""
        mode_params = (mode,) if "runtime_mode" in columns else ()
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT ticker, known_at, updated_at, claude_action, claude_watchlist,
                       claude_trade_ready, recommended_strategy, route_original_action,
                       route_final_action, route_route, route_reason,
                       route_runtime_gate_reason, path_run_count, decision_count,
                       buy_signal_count, no_signal_count, watch_only_count, filled_count,
                       first_signal_at, first_fill_at, entry_price, pnl_pct, exit_reason,
                       strategy_used
                FROM audit_candidate_latest_rows
                WHERE market=? AND session_date=? {mode_clause}
                ORDER BY COALESCE(updated_at, known_at, '') DESC, ticker
                """,
                (market, session_date) + mode_params,
            )
        ]
        conn.close()
    except Exception as exc:
        return {**empty, "error": f"candidate_db_error:{exc}"}

    action_counts = Counter(str(row.get("route_final_action") or "(blank)") for row in rows)
    route_counts = Counter(str(row.get("route_route") or "(blank)") for row in rows)
    tickers = {str(row.get("ticker") or "").upper() for row in rows if row.get("ticker")}
    watch_rows = [
        row
        for row in rows
        if _safe_int(row.get("claude_watchlist")) != 0
        or str(row.get("claude_action") or "").upper() in {"WATCH", "BUY_READY", "PROBE_READY", "PULLBACK_WAIT"}
    ]
    ready_rows = [row for row in rows if _safe_int(row.get("claude_trade_ready")) != 0]
    plan_a = [
        row
        for row in rows
        if str(row.get("route_route") or "") == "PlanA.buy"
        or str(row.get("route_final_action") or "").upper() in {"BUY_READY", "PROBE_READY"}
    ]
    pathb = [
        row
        for row in rows
        if "PATHB" in str(row.get("route_route") or "").upper()
        or str(row.get("route_final_action") or "").upper() == "PULLBACK_WAIT"
        or _safe_int(row.get("path_run_count")) > 0
        or str(row.get("strategy_used") or "").lower() == "claude_price"
    ]
    hard_blocks = [row for row in rows if str(row.get("route_final_action") or "").upper() == "HARD_BLOCK"]
    latest_known = max((str(row.get("known_at") or "") for row in rows), default="")
    latest_updated = max((str(row.get("updated_at") or "") for row in rows), default="")
    return {
        "available": True,
        "rows": len(rows),
        "counts": {
            "tickers": len(tickers),
            "watch": len({str(row.get("ticker") or "").upper() for row in watch_rows}),
            "trade_ready": len({str(row.get("ticker") or "").upper() for row in ready_rows}),
            "plan_a": len({str(row.get("ticker") or "").upper() for row in plan_a}),
            "pathb": len({str(row.get("ticker") or "").upper() for row in pathb}),
            "hard_block": len({str(row.get("ticker") or "").upper() for row in hard_blocks}),
            "filled": len({str(row.get("ticker") or "").upper() for row in rows if _safe_int(row.get("filled_count")) > 0}),
            "signal_checked": len({str(row.get("ticker") or "").upper() for row in rows if _safe_int(row.get("decision_count")) > 0}),
        },
        "action_counts": dict(action_counts.most_common()),
        "route_counts": dict(route_counts.most_common()),
        "plan_a": plan_a[:12],
        "pathb": pathb[:14],
        "watch": watch_rows[:16],
        "hard_blocks": hard_blocks[:10],
        "latest_known_at": latest_known,
        "latest_updated_at": latest_updated,
    }


def _preopen_snapshot(market: str, session_date: str) -> dict[str, Any]:
    compact = _compact_day(session_date)
    path = ROOT / "logs" / "preopen" / f"{compact}_{market}_candidates.jsonl"
    rows = _read_jsonl(path)
    by_ticker: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper().strip()
        if ticker:
            by_ticker[ticker] = row
    latest_at = max((str(row.get("captured_at") or row.get("detected_at") or "") for row in rows), default="")
    ranked = sorted(
        by_ticker.values(),
        key=lambda row: (
            _safe_float(row.get("preopen_score")),
            -_safe_int(row.get("shadow_preopen_rank")),
        ),
        reverse=True,
    )
    return {
        "path": str(path),
        "exists": path.exists(),
        "rows": len(rows),
        "tickers": len(by_ticker),
        "latest_at": latest_at,
        "top": ranked[:12],
    }


def _screener_snapshot(market: str, session_date: str) -> dict[str, Any]:
    compact = _compact_day(session_date)
    path = ROOT / "logs" / "screener_quality" / f"{compact}_{market}_candidates.jsonl"
    rows = _read_jsonl(path, limit=3000)
    if not rows:
        return {"path": str(path), "exists": path.exists(), "rows": 0, "tickers": 0, "latest_at": "", "status_counts": {}, "phase_counts": {}, "top_watch": []}
    latest_at = max(str(row.get("timestamp") or row.get("captured_at") or "") for row in rows)
    latest_rows = [row for row in rows if str(row.get("timestamp") or row.get("captured_at") or "") == latest_at]
    status_counts = Counter(str(row.get("status") or "(blank)") for row in latest_rows)
    phase_counts = Counter(str(row.get("phase") or "(blank)") for row in rows)
    by_ticker = {str(row.get("ticker") or "").upper(): row for row in latest_rows if row.get("ticker")}
    top_watch = sorted(
        [row for row in by_ticker.values() if str(row.get("status") or "").upper() in {"WATCH", "BUY_READY", "TRADE_READY"}],
        key=lambda row: (_safe_float(row.get("candidate_quality_score")), _safe_float(row.get("score_current"))),
        reverse=True,
    )[:12]
    return {
        "path": str(path),
        "exists": path.exists(),
        "rows": len(rows),
        "tickers": len(by_ticker),
        "latest_at": latest_at,
        "status_counts": dict(status_counts.most_common()),
        "phase_counts": dict(phase_counts.most_common()),
        "top_watch": top_watch,
    }


def _route_shadow_snapshot(market: str, session_date: str) -> dict[str, Any]:
    compact = _compact_day(session_date)
    path = ROOT / "logs" / "funnel" / f"action_routing_shadow_{compact}_{market}.jsonl"
    rows = _read_jsonl(path, limit=30)
    if not rows:
        return {"path": str(path), "exists": path.exists(), "available": False}
    latest = rows[-1]
    routes = latest.get("routes") if isinstance(latest.get("routes"), list) else []
    counts = Counter(str((row or {}).get("final_action") or "(blank)") for row in routes if isinstance(row, dict))
    route_counts = Counter(str((row or {}).get("route") or "(blank)") for row in routes if isinstance(row, dict))
    plan_a = [row for row in routes if isinstance(row, dict) and str(row.get("route") or "") == "PlanA.buy"]
    pathb = [
        row
        for row in routes
        if isinstance(row, dict)
        and ("PATHB" in str(row.get("route") or "").upper() or str(row.get("final_action") or "").upper() == "PULLBACK_WAIT")
    ]
    return {
        "path": str(path),
        "exists": path.exists(),
        "available": True,
        "written_at": latest.get("written_at"),
        "route_count": latest.get("route_count"),
        "action_counts": dict(counts.most_common()),
        "route_counts": dict(route_counts.most_common()),
        "plan_a": plan_a[:12],
        "pathb": pathb[:12],
    }


def _positions_snapshot(market: str) -> dict[str, Any]:
    path = ROOT / "state" / "live_open_positions.json"
    positions = _read_json(path, [])
    if not isinstance(positions, list):
        positions = []
    market_positions = [
        row
        for row in positions
        if isinstance(row, dict) and str(row.get("market") or "").upper() == market
    ]

    def is_pathb(row: dict[str, Any]) -> bool:
        return bool(
            row.get("pathb_path_run_id")
            or row.get("path_run_id")
            or str(row.get("path_type") or "").lower() == "claude_price"
            or str(row.get("strategy") or "").lower() == "claude_price"
            or str(row.get("source_strategy") or "").lower() == "claude_price"
        )

    pathb = [row for row in market_positions if is_pathb(row)]
    plan_a = [row for row in market_positions if not is_pathb(row)]
    return {
        "path": str(path),
        "total": len(market_positions),
        "plan_a": plan_a,
        "pathb": pathb,
        "pending_sells": [row for row in market_positions if row.get("pathb_pending_sell_order_no")],
    }


def _decision_events(market: str, session_date: str) -> list[dict[str, Any]]:
    path = ROOT / "state" / "live_decisions.jsonl"
    rows = _read_jsonl(path, limit=300)
    out = [
        row
        for row in rows
        if str(row.get("market") or "").upper() == market
        and str(row.get("session_date") or "") == session_date
        and str(row.get("type") or "").lower() in {"entry", "closed"}
    ]
    return out[-20:]


def _entry_timing_events(market: str, session_date: str) -> list[dict[str, Any]]:
    compact = _compact_day(session_date)
    path = ROOT / "logs" / "entry_timing" / f"live_{compact}_{market}.jsonl"
    rows = _read_jsonl(path, limit=200)
    return rows[-12:]


def _path_label(row: dict[str, Any]) -> str:
    if row.get("pathb_path_run_id") or str(row.get("path_type") or "").lower() == "claude_price":
        return "PathB"
    if str(row.get("strategy") or "").lower() == "claude_price":
        return "PathB"
    return "PlanA"


def _render_rows(label: str, rows: list[dict[str, Any]], *, kind: str) -> list[str]:
    lines = [label]
    if not rows:
        lines.append("  - 없음")
        return lines
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        if kind == "candidate":
            action = str(row.get("route_final_action") or row.get("claude_action") or "")
            route = str(row.get("route_route") or "")
            strategy = str(row.get("recommended_strategy") or row.get("strategy_used") or "")
            reason = row.get("route_runtime_gate_reason") or row.get("route_reason") or ""
            signals = f"sig={_safe_int(row.get('buy_signal_count'))}/{_safe_int(row.get('decision_count'))}"
            lines.append(f"  - {ticker} {action} {route} {strategy} {signals} {_short(reason)}")
        elif kind == "preopen":
            score = row.get("preopen_score")
            rank = row.get("shadow_preopen_rank")
            source = row.get("source") or row.get("provider") or ""
            change = _fmt_pct(row.get("change_rate") if row.get("change_rate") is not None else row.get("gap_pct"))
            lines.append(f"  - {ticker} rank={rank} score={score} {source} {change}")
        elif kind == "position":
            qty = row.get("qty")
            pnl = _fmt_pct(row.get("pnl_pct"))
            strategy = row.get("strategy") or row.get("source_strategy") or ""
            pending = " pending_sell" if row.get("pathb_pending_sell_order_no") else ""
            lines.append(f"  - {ticker} qty={qty} {pnl} {strategy}{pending}")
        elif kind == "event":
            event_type = row.get("type") or row.get("event")
            ts = row.get("timestamp") or row.get("occurred_at") or ""
            route = _path_label(row)
            strategy = row.get("strategy") or row.get("source_strategy") or ""
            exit_reason = row.get("exit_reason") or ""
            pnl = _fmt_pct(row.get("pnl_pct"))
            lines.append(f"  - {ts} {route} {event_type} {ticker} {strategy} {exit_reason} {pnl}")
        elif kind == "entry_timing":
            event = row.get("event")
            ts = row.get("occurred_at") or ""
            state = row.get("state") if isinstance(row.get("state"), dict) else {}
            source = state.get("candidate_source") or state.get("last_candidate_source") or ""
            checks = state.get("signal_check_count")
            lines.append(f"  - {ts} {event} {ticker} source={source} signal_checks={checks}")
    return lines


def build_snapshot(market: str, session_date: str, mode: str = "live") -> dict[str, Any]:
    return {
        "now": _now_kst().isoformat(timespec="seconds"),
        "market": market,
        "session_date": session_date,
        "candidate": _candidate_snapshot(ROOT / "data" / "audit" / "candidate_audit.db", market, session_date, mode=mode),
        "preopen": _preopen_snapshot(market, session_date),
        "screener": _screener_snapshot(market, session_date),
        "route_shadow": _route_shadow_snapshot(market, session_date),
        "positions": _positions_snapshot(market),
        "events": _decision_events(market, session_date),
        "entry_timing": _entry_timing_events(market, session_date),
    }


def render(snapshot: dict[str, Any]) -> str:
    candidate = snapshot["candidate"]
    preopen = snapshot["preopen"]
    screener = snapshot["screener"]
    route_shadow = snapshot["route_shadow"]
    positions = snapshot["positions"]
    lines = [
        "=" * 96,
        f"[{snapshot['now']}] {snapshot['market']} session={snapshot['session_date']} selection monitor",
        "-" * 96,
    ]
    if candidate.get("available") and candidate.get("rows"):
        counts = candidate.get("counts") or {}
        lines.append(
            "후보/선정: "
            f"latest_rows={candidate.get('rows')} tickers={counts.get('tickers')} "
            f"watch={counts.get('watch')} trade_ready={counts.get('trade_ready')} "
            f"PlanA={counts.get('plan_a')} PathB={counts.get('pathb')} "
            f"signal_checked={counts.get('signal_checked')} filled={counts.get('filled')} "
            f"hard_block={counts.get('hard_block')}"
        )
        lines.append(f"최근 selection known_at={candidate.get('latest_known_at')} updated_at={candidate.get('latest_updated_at')}")
        lines.append(f"route_final_action={candidate.get('action_counts')}")
        lines.append(f"route={candidate.get('route_counts')}")
        lines.extend(_render_rows("PlanA 보고 있는 종목", candidate.get("plan_a") or [], kind="candidate"))
        lines.extend(_render_rows("PathB 보고 있는 종목", candidate.get("pathb") or [], kind="candidate"))
        lines.extend(_render_rows("Watch 상위", candidate.get("watch") or [], kind="candidate"))
        if candidate.get("hard_blocks"):
            lines.extend(_render_rows("Hard block", candidate.get("hard_blocks") or [], kind="candidate"))
    else:
        lines.append(
            "후보/선정: 아직 오늘 정규장 selection DB row 없음 "
            f"(error={candidate.get('error') or ''})"
        )
        lines.append(
            f"preopen 후보: exists={preopen.get('exists')} rows={preopen.get('rows')} "
            f"tickers={preopen.get('tickers')} latest={preopen.get('latest_at')}"
        )
        lines.extend(_render_rows("preopen 상위", preopen.get("top") or [], kind="preopen"))
        lines.append(
            f"screener latest={screener.get('latest_at')} tickers={screener.get('tickers')} "
            f"status={screener.get('status_counts')} phase={screener.get('phase_counts')}"
        )
        lines.extend(_render_rows("screener WATCH/READY", screener.get("top_watch") or [], kind="preopen"))
    if route_shadow.get("available"):
        lines.append(
            f"최근 routing shadow: at={route_shadow.get('written_at')} "
            f"route_count={route_shadow.get('route_count')} action={route_shadow.get('action_counts')}"
        )
    lines.append(
        "보유/청산 감시: "
        f"US open={positions.get('total')} PlanA={len(positions.get('plan_a') or [])} "
        f"PathB={len(positions.get('pathb') or [])} pending_sells={len(positions.get('pending_sells') or [])}"
    )
    lines.extend(_render_rows("PlanA 보유", positions.get("plan_a") or [], kind="position"))
    lines.extend(_render_rows("PathB 보유", positions.get("pathb") or [], kind="position"))
    lines.extend(_render_rows("최근 매수/매도 이벤트", snapshot.get("events") or [], kind="event"))
    lines.extend(_render_rows("최근 후보/신호 진행", snapshot.get("entry_timing") or [], kind="entry_timing"))
    return "\n".join(lines)


def _append_log(path: Path | None, text: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)
        f.write("\n")


def run(args: argparse.Namespace) -> int:
    start_at = _parse_dt(args.start_at)
    end_at = _parse_dt(args.end_at)
    market = str(args.market or "US").upper()
    session_date = str(args.session_date or _default_session_date(_now_kst()))
    mode = str(args.mode or "live").strip().lower()
    log_path = Path(args.log_file) if args.log_file else None
    while True:
        now = _now_kst()
        if start_at and now < start_at:
            wait_line = f"[{now.isoformat(timespec='seconds')}] {start_at.isoformat(timespec='seconds')}까지 대기 중"
            print(wait_line, flush=True)
            _append_log(log_path, wait_line)
            time.sleep(min(max(1, args.interval_sec), max(1, int((start_at - now).total_seconds()))))
            continue
        text = render(build_snapshot(market, session_date, mode=mode))
        print(text, flush=True)
        _append_log(log_path, text)
        if args.once:
            return 0
        if end_at and _now_kst() >= end_at:
            done = f"[{_now_kst().isoformat(timespec='seconds')}] monitor completed"
            print(done, flush=True)
            _append_log(log_path, done)
            return 0
        time.sleep(max(1, args.interval_sec))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only US live selection/PlanA/PathB console monitor.")
    parser.add_argument("--market", default="US")
    parser.add_argument("--mode", default="live", choices=["live", "paper"])
    parser.add_argument("--session-date", default="")
    parser.add_argument("--start-at", default="")
    parser.add_argument("--end-at", default="")
    parser.add_argument("--interval-sec", type=int, default=60)
    parser.add_argument("--log-file", default="")
    parser.add_argument("--once", action="store_true")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
