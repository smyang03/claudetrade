from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path
from tools.analyze_candidate_audit import analyze_candidate_audit
from tools.analyze_hold_advisor_latency import analyze_hold_advisor_latency

KST = timezone(timedelta(hours=9))


def _read_json(path: Path, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return default


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _candidate_bucket_source_score_coverage(
    *,
    db_path: Path,
    session_date: str,
    market: str,
    runtime_mode: str,
) -> dict[str, Any]:
    if not db_path.exists():
        return {"available": False, "reason": "db_missing", "db_path": str(db_path)}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "audit_candidate_rows"):
            return {"available": False, "reason": "table_missing", "db_path": str(db_path)}
        columns = _columns(conn, "audit_candidate_rows")
        where = ["runtime_mode=?"]
        params: list[Any] = [runtime_mode]
        if session_date:
            where.append("session_date=?")
            params.append(session_date)
        if market:
            where.append("market=?")
            params.append(market.upper())
        pieces = ["COUNT(*) AS rows"]
        checks = {
            "blank_primary_bucket": "primary_bucket",
            "empty_source_tags": "source_tags_json",
            "null_raw_score_current": "raw_score_current",
            "null_trainer_score_rank": "trainer_score_rank",
            "empty_data_quality_flags": "data_quality_flags_json",
        }
        for label, column in checks.items():
            if column in columns:
                pieces.append(
                    f"SUM(CASE WHEN {column} IS NULL OR {column}='' OR {column}='[]' THEN 1 ELSE 0 END) AS {label}"
                )
            else:
                pieces.append(f"0 AS {label}")
        row = conn.execute(
            f"SELECT {', '.join(pieces)} FROM audit_candidate_rows WHERE {' AND '.join(where)}",
            params,
        ).fetchone()
        invalid_price_reason_counts: dict[str, int] = {}
        if "invalid_price_reason" in columns:
            invalid_price_reason_counts = {
                str(item["reason"] or "unknown"): int(item["rows"] or 0)
                for item in conn.execute(
                    f"""
                    SELECT COALESCE(NULLIF(invalid_price_reason,''), 'unknown') AS reason, COUNT(*) AS rows
                    FROM audit_candidate_rows
                    WHERE {' AND '.join(where)}
                      AND invalid_price_reason IS NOT NULL
                      AND invalid_price_reason<>''
                    GROUP BY COALESCE(NULLIF(invalid_price_reason,''), 'unknown')
                    ORDER BY rows DESC
                    """,
                    params,
                )
            }
        return {
            "available": True,
            "db_path": str(db_path),
            "rows": int(row["rows"] or 0) if row else 0,
            "blank_primary_bucket": int(row["blank_primary_bucket"] or 0) if row else 0,
            "empty_source_tags": int(row["empty_source_tags"] or 0) if row else 0,
            "null_raw_score_current": int(row["null_raw_score_current"] or 0) if row else 0,
            "null_trainer_score_rank": int(row["null_trainer_score_rank"] or 0) if row else 0,
            "empty_data_quality_flags": int(row["empty_data_quality_flags"] or 0) if row else 0,
            "invalid_price_reason_counts": invalid_price_reason_counts,
            "performance_conclusion_allowed": False,
        }
    finally:
        conn.close()


def _kis_token_status(mode: str) -> dict[str, Any]:
    try:
        from tools.live_preflight import _token_checks

        checks = _token_checks(mode)
        rows = []
        for item in checks:
            payload = asdict(item) if is_dataclass(item) else dict(item)
            details = dict(payload.get("data") or payload.get("details") or {})
            details.pop("access_token", None)
            details.pop("app_key", None)
            details.pop("app_secret", None)
            details.pop("account_no", None)
            payload["data"] = details
            rows.append(payload)
        return {"available": True, "checks": rows}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def _v2_learning_gate_report(db_path: Path, *, market: str, runtime_mode: str) -> dict[str, Any]:
    if not db_path.exists():
        return {"available": False, "reason": "db_missing", "db_path": str(db_path)}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "v2_canonical_performance"):
            return {"available": False, "reason": "table_missing", "db_path": str(db_path)}
        columns = _columns(conn, "v2_canonical_performance")
        where = ["runtime_mode=?"]
        params: list[Any] = [runtime_mode]
        if market:
            where.append("market=?")
            params.append(market.upper())
        grade_col = "quality_grade" if "quality_grade" in columns else "''"
        rows_by_grade = {
            str(row["quality_grade"] or "unknown"): int(row["rows"] or 0)
            for row in conn.execute(
                f"""
                SELECT COALESCE(NULLIF({grade_col},''), 'unknown') AS quality_grade, COUNT(*) AS rows
                FROM v2_canonical_performance
                WHERE {' AND '.join(where)}
                GROUP BY COALESCE(NULLIF({grade_col},''), 'unknown')
                ORDER BY rows DESC
                """,
                params,
            )
        }
        learning_allowed = 0
        learning_excluded = 0
        if "learning_allowed" in columns:
            row = conn.execute(
                f"""
                SELECT
                  SUM(CASE WHEN COALESCE(learning_allowed, 0)=1 THEN 1 ELSE 0 END) AS allowed,
                  SUM(CASE WHEN COALESCE(learning_allowed, 0)=0 THEN 1 ELSE 0 END) AS excluded
                FROM v2_canonical_performance
                WHERE {' AND '.join(where)}
                """,
                params,
            ).fetchone()
            learning_allowed = int(row["allowed"] or 0)
            learning_excluded = int(row["excluded"] or 0)
        reason_counts: Counter[str] = Counter()
        reason_table = ""
        reason_columns: set[str] = set()
        if _table_exists(conn, "v2_learning_performance"):
            learning_columns = _columns(conn, "v2_learning_performance")
            if "quality_reasons_json" in learning_columns:
                reason_table = "v2_learning_performance"
                reason_columns = learning_columns
        if not reason_table and "quality_reasons_json" in columns:
            reason_table = "v2_canonical_performance"
            reason_columns = columns
        if reason_table:
            reason_where = ["runtime_mode=?"]
            reason_params: list[Any] = [runtime_mode]
            if market and "market" in reason_columns:
                reason_where.append("market=?")
                reason_params.append(market.upper())
            for row in conn.execute(
                f"SELECT quality_reasons_json FROM {reason_table} WHERE {' AND '.join(reason_where)}",
                reason_params,
            ):
                try:
                    reasons = json.loads(str(row["quality_reasons_json"] or "[]"))
                except Exception:
                    reasons = []
                if isinstance(reasons, list):
                    reason_counts.update(str(item) for item in reasons if str(item).strip())
        synced_at = ""
        if "synced_at" in columns:
            row = conn.execute(
                f"SELECT MAX(synced_at) AS synced_at FROM v2_canonical_performance WHERE {' AND '.join(where)}",
                params,
            ).fetchone()
            synced_at = str(row["synced_at"] or "") if row else ""
        return {
            "available": True,
            "db_path": str(db_path),
            "rows_by_quality_grade": rows_by_grade,
            "learning_allowed": learning_allowed,
            "learning_excluded": learning_excluded,
            "top_quality_reasons": dict(reason_counts.most_common(20)),
            "last_synced_at": synced_at,
            "policy_change_allowed": False,
        }
    finally:
        conn.close()


def _pead_manual_review_report(
    *,
    state_path: Path,
    log_dir: Path,
    required_trading_days: int = 5,
) -> dict[str, Any]:
    state = _read_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    rows: list[dict[str, Any]] = []
    if log_dir.exists():
        for path in sorted([*log_dir.glob("*_shadow.json"), *log_dir.glob("*_shadow.jsonl")]):
            try:
                if path.suffix == ".jsonl":
                    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                        if line.strip():
                            item = json.loads(line)
                            if isinstance(item, dict):
                                rows.append(item)
                else:
                    item = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(item, list):
                        rows.extend(row for row in item if isinstance(row, dict))
                    elif isinstance(item, dict):
                        rows.append(item)
            except Exception:
                continue
    trading_days_observed = int(state.get("trading_days_observed") or state.get("observed_trading_days") or 0)
    prompt_enabled = bool(state.get("prompt_surprise_enabled") or state.get("pead_prompt_surprise_enabled"))
    checklist = state.get("manual_review_checklist") if isinstance(state.get("manual_review_checklist"), dict) else {}
    prompt_leaks = [
        {
            "ticker": row.get("ticker"),
            "market": row.get("market"),
            "session_date": row.get("session_date") or row.get("date"),
        }
        for row in rows
        if bool(row.get("prompt_applied")) and not prompt_enabled
    ][:20]
    surprise_known = sum(1 for row in rows if str(row.get("surprise_sign") or "unknown") != "unknown")
    prompt_applied = sum(1 for row in rows if bool(row.get("prompt_applied")))
    checklist_complete = bool(checklist) and all(bool(value) for value in checklist.values())
    return {
        "state_path": str(state_path),
        "log_dir": str(log_dir),
        "state_found": state_path.exists(),
        "shadow_rows": len(rows),
        "trading_days_observed": trading_days_observed,
        "required_trading_days": required_trading_days,
        "prompt_surprise_enabled": prompt_enabled,
        "manual_review_checklist": checklist,
        "surprise_known_count": surprise_known,
        "prompt_applied_count": prompt_applied,
        "prompt_leak_candidates": prompt_leaks,
        "promotion_gate_state": "pass"
        if prompt_enabled and trading_days_observed >= required_trading_days and not prompt_leaks and checklist_complete
        else "blocked_manual_review",
        "policy_change_allowed": False,
    }


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        try:
            parsed = datetime.fromisoformat(raw[:19])
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _hold_advisor_cache_shadow(
    *,
    decision_dir: Path,
    start_date: str,
    end_date: str,
    market: str,
    ttl_minutes: int = 10,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if decision_dir.exists():
        for path in sorted(decision_dir.glob("decisions_*.jsonl")):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if not isinstance(item, dict):
                    continue
                ts = _parse_dt(item.get("ts") or item.get("timestamp"))
                day = ts.date().isoformat() if ts else str(item.get("date") or "")[:10]
                if start_date and day and day < start_date:
                    continue
                if end_date and day and day > end_date:
                    continue
                market_key = str(market or "").upper()
                if market_key and market_key != "ALL" and str(item.get("market") or "").upper() != market_key:
                    continue
                rows.append({**item, "_parsed_ts": ts})
    rows.sort(key=lambda row: row.get("_parsed_ts") or datetime.min.replace(tzinfo=KST))
    ttl = timedelta(minutes=max(int(ttl_minutes or 0), 0))
    last_by_key: dict[tuple[str, str, str, str], datetime] = {}
    would_hit = 0
    would_expire = 0
    missing_time = 0
    for row in rows:
        ts = row.get("_parsed_ts")
        if not isinstance(ts, datetime):
            missing_time += 1
            continue
        key = (
            str(row.get("market") or "").upper(),
            str(row.get("ticker") or "").upper(),
            str(row.get("decision_stage") or "unknown"),
            str(row.get("review_reason") or row.get("reason") or "unknown"),
        )
        previous = last_by_key.get(key)
        if previous is not None and ts - previous <= ttl:
            would_hit += 1
        else:
            if previous is not None:
                would_expire += 1
            last_by_key[key] = ts
    return {
        "decision_dir": str(decision_dir),
        "ttl_minutes": max(int(ttl_minutes or 0), 0),
        "requests": len(rows),
        "would_hit": would_hit,
        "would_expire": would_expire,
        "missing_time": missing_time,
        "estimated_request_reduction": would_hit,
        "cache_enable_allowed": False,
        "policy_change_allowed": False,
    }


def _write_report(payload: dict[str, Any], report_dir: str | Path | None) -> dict[str, str]:
    out_dir = Path(report_dir) if report_dir else get_runtime_path("data", "v2_reports", "monitoring_ops")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"monitoring_ops_report_{stamp}.json"
    md_path = out_dir / f"monitoring_ops_report_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# Monitoring Ops Report",
        "",
        f"- generated_at: {payload.get('generated_at', '')}",
        f"- mode: {payload.get('mode', '')}",
        f"- market: {payload.get('market', '') or '*'}",
        f"- session_date: {payload.get('session_date', '') or '*'}",
        "",
        "## Gates",
        "",
    ]
    for name, row in (payload.get("gate_summary") or {}).items():
        lines.append(f"- {name}: {row}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def build_monitoring_ops_report(
    *,
    candidate_db: str | Path | None = None,
    learning_db: str | Path | None = None,
    mode: str = "live",
    session_date: str = "",
    market: str = "",
    horizon_min: int = 60,
    pead_state: str | Path | None = None,
    pead_log_dir: str | Path | None = None,
    hold_decision_dir: str | Path | None = None,
    hold_start_date: str = "",
    hold_end_date: str = "",
    hold_cache_ttl_minutes: int = 10,
    report_dir: str | Path | None = None,
    write_report: bool = False,
) -> dict[str, Any]:
    runtime_mode = str(mode or "live").lower()
    market_key = str(market or "").upper()
    candidate_path = Path(candidate_db) if candidate_db else get_runtime_path("data", "audit", "candidate_audit.db")
    learning_path = Path(learning_db) if learning_db else get_runtime_path("data", "ml", "decisions.db")
    candidate_analysis: dict[str, Any]
    if candidate_path.exists():
        try:
            candidate_analysis = analyze_candidate_audit(
                db_path=candidate_path,
                session_date=session_date,
                market=market_key,
                runtime_mode=runtime_mode,
                horizon_min=int(horizon_min),
            )
        except Exception as exc:
            candidate_analysis = {"available": False, "error": str(exc)}
    else:
        candidate_analysis = {"available": False, "reason": "db_missing", "db_path": str(candidate_path)}
    hold_decisions = Path(hold_decision_dir) if hold_decision_dir else get_runtime_path("logs", "hold_advisor")
    hold_latency = analyze_hold_advisor_latency(
        decision_dir=hold_decisions,
        start_date=hold_start_date or session_date,
        end_date=hold_end_date or session_date,
        market=market_key or "ALL",
        source="auto",
    )
    payload = {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "mode": runtime_mode,
        "market": market_key,
        "session_date": session_date,
        "horizon_min": int(horizon_min),
        "candidate_analysis": candidate_analysis,
        "candidate_bucket_source_score_coverage": _candidate_bucket_source_score_coverage(
            db_path=candidate_path,
            session_date=session_date,
            market=market_key,
            runtime_mode=runtime_mode,
        ),
        "kis_token_status": _kis_token_status(runtime_mode),
        "v2_learning_gate": _v2_learning_gate_report(learning_path, market=market_key, runtime_mode=runtime_mode),
        "hold_advisor_latency": hold_latency,
        "hold_advisor_cache_shadow": _hold_advisor_cache_shadow(
            decision_dir=hold_decisions,
            start_date=hold_start_date or session_date,
            end_date=hold_end_date or session_date,
            market=market_key or "ALL",
            ttl_minutes=hold_cache_ttl_minutes,
        ),
        "pead_manual_review": _pead_manual_review_report(
            state_path=Path(pead_state) if pead_state else get_runtime_path("state", "pead_shadow_state.json"),
            log_dir=Path(pead_log_dir) if pead_log_dir else get_runtime_path("logs", "pead"),
        ),
    }
    payload["gate_summary"] = {
        "actual_prompt_visibility": (
            candidate_analysis.get("consistency") or candidate_analysis.get("candidate_consistency") or {}
        )
        if isinstance(candidate_analysis, dict)
        else {},
        "bucket_source_score_performance_allowed": False,
        "watch_trigger_policy_change_allowed": False,
        "learning_policy_change_allowed": False,
        "pead_policy_change_allowed": False,
    }
    if write_report:
        payload["report_paths"] = _write_report(payload, report_dir)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build read-only monitoring operations report.")
    parser.add_argument("--candidate-db", default="")
    parser.add_argument("--learning-db", default="")
    parser.add_argument("--mode", default="live", choices=["live", "paper"])
    parser.add_argument("--date", default="")
    parser.add_argument("--market", default="")
    parser.add_argument("--horizon-min", type=int, default=60)
    parser.add_argument("--pead-state", default="")
    parser.add_argument("--pead-log-dir", default="")
    parser.add_argument("--hold-decision-dir", default="")
    parser.add_argument("--hold-start-date", default="")
    parser.add_argument("--hold-end-date", default="")
    parser.add_argument("--hold-cache-ttl-minutes", type=int, default=10)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-dir", default="")
    args = parser.parse_args(argv)
    payload = build_monitoring_ops_report(
        candidate_db=args.candidate_db or None,
        learning_db=args.learning_db or None,
        mode=args.mode,
        session_date=args.date,
        market=args.market,
        horizon_min=args.horizon_min,
        pead_state=args.pead_state or None,
        pead_log_dir=args.pead_log_dir or None,
        hold_decision_dir=args.hold_decision_dir or None,
        hold_start_date=args.hold_start_date,
        hold_end_date=args.hold_end_date,
        hold_cache_ttl_minutes=args.hold_cache_ttl_minutes,
        write_report=args.write_report,
        report_dir=args.report_dir or None,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
