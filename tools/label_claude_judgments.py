from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minority_report.lesson_quality import QUALITY_VERSION
from tools.build_claude_decision_facts import warn_if_fact_data_stale


DEFAULT_FACT_DB = ROOT / "data" / "ml" / "claude_decision_facts.db"
DEFAULT_LESSON_OUTPUT_DIR = ROOT / "docs" / "reports"

POSITIVE_ACTIONS = {"BUY_READY", "TRADE_READY", "PROBE_READY", "ADD_READY"}
WAIT_ACTIONS = {"WATCH", "PULLBACK_WAIT", "WATCH_CONFIRM"}
NEGATIVE_ACTIONS = {"AVOID", "SKIP", "DO_NOT_TRADE", "HARD_BLOCK", "BLOCKED"}

RISK_VETO_KEYWORDS = {
    "low_liquidity",
    "liquidity_bad",
    "blackout",
    "same_day_reentry",
    "broker_untrusted",
    "broker_quarantine",
    "affordability_fail",
    "hard_risk_block",
    "late_session",
    "order_unknown",
    "halt",
    "data_degraded",
}

DEFAULT_LABEL_CONFIG: dict[str, float] = {
    "positive_forward_3d_pct": 3.0,
    "strong_forward_3d_pct": 5.0,
    "false_positive_1d_pct": -3.0,
    "false_positive_3d_pct": -5.0,
    "missed_runup_3d_pct": 5.0,
    "kr_strong_missed_runup_3d_pct": 10.0,
    "bad_drawdown_3d_pct": -5.0,
    "execution_issue_forward_3d_pct": 3.0,
    "execution_issue_actual_pnl_pct": -1.0,
}

DECISION_LABEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_labels (
    label_key TEXT PRIMARY KEY,
    selection_key TEXT NOT NULL,
    runtime_mode TEXT NOT NULL,
    session_date TEXT NOT NULL,
    market TEXT NOT NULL,
    ticker TEXT NOT NULL,

    label TEXT NOT NULL,
    owner TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    label_rule TEXT NOT NULL,
    improvement_hint TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decision_labels_session
    ON decision_labels(runtime_mode, market, session_date, ticker);
CREATE INDEX IF NOT EXISTS idx_decision_labels_label_owner
    ON decision_labels(label, owner, market, session_date);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(data: Any) -> str:
    return json.dumps(data if data is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(db_path: str | Path = DEFAULT_FACT_DB) -> None:
    with closing(_connect(Path(db_path))) as conn:
        conn.executescript(DECISION_LABEL_SCHEMA)
        conn.commit()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


def _flatten(value: Any) -> list[str]:
    decoded = _json_value(value)
    out: list[str] = []
    if decoded is None:
        return out
    if isinstance(decoded, dict):
        for key, item in decoded.items():
            out.append(str(key))
            out.extend(_flatten(item))
        return out
    if isinstance(decoded, (list, tuple, set)):
        for item in decoded:
            out.extend(_flatten(item))
        return out
    return [str(decoded)]


def _market(value: str) -> str:
    key = str(value or "").strip().upper()
    return key if key in {"KR", "US", "ALL"} else key


def _resolve_dates(date: str = "", start_date: str = "", end_date: str = "") -> tuple[str, str]:
    if date:
        return date, date
    return start_date, end_date


def _scope_where(alias: str, start_date: str, end_date: str, market: str, runtime_mode: str) -> tuple[str, list[Any]]:
    prefix = f"{alias}." if alias else ""
    where = [f"{prefix}runtime_mode=?"]
    params: list[Any] = [runtime_mode]
    if start_date:
        where.append(f"{prefix}session_date>=?")
        params.append(start_date)
    if end_date:
        where.append(f"{prefix}session_date<=?")
        params.append(end_date)
    if market and market != "ALL":
        where.append(f"{prefix}market=?")
        params.append(market)
    return " AND ".join(where), params


def _normalize_action(row: dict[str, Any]) -> str:
    for key in ("final_action", "normalized_action", "raw_action"):
        action = str(row.get(key) or "").strip().upper()
        if action:
            return action
    if _as_bool(row.get("trade_ready")) or _as_bool(row.get("claude_trade_ready")):
        return "TRADE_READY"
    if str(row.get("classification") or "").strip().lower() == "watch_only":
        return "WATCH"
    return "UNKNOWN"


def _action_family(action: str) -> str:
    if action in POSITIVE_ACTIONS:
        return "positive"
    if action in WAIT_ACTIONS:
        return "wait"
    if action in NEGATIVE_ACTIONS:
        return "negative"
    return "unknown"


def _has_outcome(row: dict[str, Any]) -> bool:
    fields = (
        "forward_30m_pct",
        "forward_60m_pct",
        "forward_1d_pct",
        "forward_3d_pct",
        "forward_5d_pct",
        "max_runup_3d_pct",
        "max_drawdown_3d_pct",
        "max_runup_5d_pct",
        "max_drawdown_5d_pct",
    )
    return any(_as_float(row.get(field)) is not None for field in fields)


def _clean_execution(row: dict[str, Any]) -> bool:
    if _as_bool(row.get("learning_allowed")):
        return True
    quality = str(row.get("quality_grade") or "").upper()
    return "CLEAN" in quality


def _risk_terms(row: dict[str, Any]) -> list[str]:
    text_parts: list[str] = []
    for field in (
        "risk_tags_json",
        "hard_blocks_json",
        "soft_gates_json",
        "data_quality_flags_json",
        "route_reason",
        "route_runtime_gate_reason",
        "prompt_excluded_reason",
        "data_quality",
        "evidence_data_state",
        "source_refs_json",
    ):
        text_parts.extend(_flatten(row.get(field)))
    raw = " ".join(text_parts).lower()
    canonical = re.sub(r"[^a-z0-9]+", "_", raw)
    found = sorted(
        keyword
        for keyword in RISK_VETO_KEYWORDS
        if keyword in canonical or keyword.replace("_", " ") in raw
    )
    return found


def _data_quality_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for field in ("session_date", "market", "ticker", "selection_key"):
        if not str(row.get(field) or "").strip():
            reasons.append(f"missing_{field}")
    match_quality = str(row.get("match_quality") or "")
    execution_quality = str(row.get("execution_source_quality") or "")
    if match_quality.startswith("ambiguous") or execution_quality == "ambiguous_match":
        reasons.append("ambiguous_execution_match")
    if not _has_outcome(row):
        reasons.append("missing_outcome")
    return reasons


def _evidence(row: dict[str, Any], action: str, risk_terms: list[str], config: dict[str, float]) -> dict[str, Any]:
    return {
        "selection": {
            "selection_key": row.get("selection_key"),
            "source": row.get("source"),
            "source_quality": row.get("selection_source_quality"),
            "raw_action": row.get("raw_action"),
            "normalized_action": row.get("normalized_action"),
            "final_action": row.get("final_action"),
            "action_used": action,
            "classification": row.get("classification"),
            "prompt_included": row.get("prompt_included"),
            "final_prompt_included": row.get("final_prompt_included"),
            "input_to_claude_reported": row.get("input_to_claude_reported"),
            "route_reason": row.get("route_reason"),
            "route_runtime_gate_reason": row.get("route_runtime_gate_reason"),
            "gap_pct": _as_float(row.get("gap_pct")),
            "from_high_pct": _as_float(row.get("from_high_pct")),
            "volume_ratio": _as_float(row.get("volume_ratio")),
            "change_pct": _as_float(row.get("change_pct")),
        },
        "outcome": {
            "forward_1d_pct": _as_float(row.get("forward_1d_pct")),
            "forward_3d_pct": _as_float(row.get("forward_3d_pct")),
            "forward_5d_pct": _as_float(row.get("forward_5d_pct")),
            "max_runup_3d_pct": _as_float(row.get("max_runup_3d_pct")),
            "max_drawdown_3d_pct": _as_float(row.get("max_drawdown_3d_pct")),
            "outcome_status": row.get("outcome_status"),
            "outcome_source": row.get("outcome_source"),
            "source_quality": row.get("outcome_source_quality"),
        },
        "risk": {
            "matched_terms": risk_terms,
            "risk_tags": _flatten(row.get("risk_tags_json")),
            "hard_blocks": _flatten(row.get("hard_blocks_json")),
            "soft_gates": _flatten(row.get("soft_gates_json")),
            "data_quality_flags": _flatten(row.get("data_quality_flags_json")),
            "prompt_excluded_reason": row.get("prompt_excluded_reason"),
            "data_quality": row.get("data_quality"),
            "evidence_data_state": row.get("evidence_data_state"),
        },
        "execution": {
            "match_quality": row.get("match_quality"),
            "source_quality": row.get("execution_source_quality"),
            "v2_decision_id": row.get("v2_decision_id"),
            "filled": row.get("filled"),
            "closed": row.get("closed"),
            "pnl_pct": _as_float(row.get("pnl_pct")),
            "mfe_pct": _as_float(row.get("mfe_pct")),
            "mae_pct": _as_float(row.get("mae_pct")),
            "quality_grade": row.get("quality_grade"),
            "learning_allowed": row.get("learning_allowed"),
        },
        "thresholds": config,
    }


def _label_row(row: dict[str, Any], config: dict[str, float], now: str) -> dict[str, Any]:
    action = _normalize_action(row)
    family = _action_family(action)
    risk = _risk_terms(row)
    f1 = _as_float(row.get("forward_1d_pct"))
    f3 = _as_float(row.get("forward_3d_pct"))
    runup3 = _as_float(row.get("max_runup_3d_pct"))
    drawdown3 = _as_float(row.get("max_drawdown_3d_pct"))
    pnl = _as_float(row.get("pnl_pct"))
    data_reasons = _data_quality_reasons(row)
    evidence = _evidence(row, action, risk, config)
    evidence["data_quality_reasons"] = data_reasons

    label = "unknown"
    owner = "unknown"
    confidence = 0.35
    rule = "unknown"
    hint = ""

    if data_reasons:
        label = "data_quality_issue"
        owner = "data_quality"
        confidence = 0.95
        rule = "data_quality_issue"
        hint = "Fix missing outcome or ambiguous execution linkage before using this row for learning."
    elif family in {"wait", "negative"} and risk and runup3 is not None and runup3 >= config["missed_runup_3d_pct"]:
        label = "risk_justified_miss"
        owner = "risk_policy"
        confidence = 0.9
        rule = "risk_justified_miss"
        hint = "Keep this out of Claude selection lessons; the missed opportunity had an explicit risk veto."
    elif (
        ((f3 is not None and f3 >= config["execution_issue_forward_3d_pct"]) or (runup3 is not None and runup3 >= config["strong_forward_3d_pct"]))
        and pnl is not None
        and pnl <= config["execution_issue_actual_pnl_pct"]
        and _clean_execution(row)
    ):
        label = "execution_issue"
        owner = "execution"
        confidence = 0.88
        rule = "execution_issue"
        hint = "Selection was plausible; inspect entry timing, exits, trailing, PathB plan fit, and broker truth."
    elif (
        family == "positive"
        and (
            (f1 is not None and f1 <= config["false_positive_1d_pct"])
            or (f3 is not None and f3 <= config["false_positive_3d_pct"])
            or (drawdown3 is not None and drawdown3 <= config["bad_drawdown_3d_pct"])
        )
    ):
        label = "false_positive"
        owner = "claude_selection"
        confidence = 0.82
        rule = "false_positive"
        hint = "Demote overextended gap/high-ATR/top-chase candidates to PROBE_READY or WATCH_CONFIRM."
    elif family in {"wait", "negative"} and not risk and runup3 is not None and runup3 >= config["missed_runup_3d_pct"]:
        label = "false_negative"
        owner = "claude_selection"
        confidence = 0.82
        rule = "false_negative"
        hint = "Review WATCH/AVOID candidates with strong runup and no explicit veto for earlier promotion."
    elif family == "positive" and (
        (f3 is not None and f3 >= config["positive_forward_3d_pct"])
        or (runup3 is not None and runup3 >= config["strong_forward_3d_pct"])
    ):
        label = "correct_positive"
        owner = "none"
        confidence = 0.86
        rule = "correct_positive"
        hint = "Preserve this positive selection pattern."
    elif family in {"wait", "negative"} and f3 is not None and f3 <= 0 and (runup3 is None or runup3 < config["missed_runup_3d_pct"]):
        label = "correct_negative"
        owner = "none"
        confidence = 0.8
        rule = "correct_negative"
        hint = "Preserve this defensive WATCH/AVOID behavior."

    return {
        "label_key": f"label:{row['selection_key']}",
        "selection_key": row["selection_key"],
        "runtime_mode": row["runtime_mode"],
        "session_date": row["session_date"],
        "market": row["market"],
        "ticker": row["ticker"],
        "label": label,
        "owner": owner,
        "confidence": confidence,
        "label_rule": rule,
        "improvement_hint": hint,
        "evidence_json": _json(evidence),
        "created_at": now,
        "updated_at": now,
    }


def _load_rows(
    conn: sqlite3.Connection,
    *,
    start_date: str,
    end_date: str,
    market: str,
    runtime_mode: str,
    latest_only: bool,
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "fact_selection"):
        return []
    where, params = _scope_where("s", start_date, end_date, market, runtime_mode)
    if latest_only:
        where += " AND COALESCE(s.latest_rank, 1)=1"
    sql = f"""
        SELECT
            s.selection_key, s.runtime_mode, s.session_date, s.market, s.ticker,
            s.source, s.source_quality AS selection_source_quality,
            s.raw_action, s.normalized_action, s.final_action, s.classification,
            s.prompt_included, s.final_prompt_included, s.input_to_claude_reported,
            s.trade_ready, s.claude_trade_ready, s.route_reason, s.route_runtime_gate_reason,
            s.prompt_excluded_reason, s.risk_tags_json, s.hard_blocks_json, s.soft_gates_json,
            s.data_quality_flags_json, s.data_quality, s.evidence_data_state,
            s.gap_pct, s.from_high_pct, s.volume_ratio, s.change_pct, s.source_refs_json,

            o.forward_30m_pct, o.forward_60m_pct, o.forward_1d_pct, o.forward_3d_pct,
            o.forward_5d_pct, o.max_runup_3d_pct, o.max_drawdown_3d_pct,
            o.max_runup_5d_pct, o.max_drawdown_5d_pct,
            o.outcome_status, o.outcome_source, o.source_quality AS outcome_source_quality,

            e.v2_decision_id, e.filled, e.closed, e.pnl_pct, e.mfe_pct, e.mae_pct,
            e.quality_grade, e.learning_allowed, e.match_quality,
            e.source_quality AS execution_source_quality
        FROM fact_selection s
        LEFT JOIN fact_forward_outcome o ON o.selection_key=s.selection_key
        LEFT JOIN fact_execution e ON e.selection_key=s.selection_key
        WHERE {where}
        ORDER BY s.session_date, s.market, s.ticker, s.selection_key
    """
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _upsert_label(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    columns = list(row.keys())
    placeholders = ", ".join(f":{column}" for column in columns)
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns if column != "label_key")
    conn.execute(
        f"""
        INSERT INTO decision_labels ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(label_key) DO UPDATE SET {updates}
        """,
        row,
    )


def _merge_config(overrides: argparse.Namespace | None = None, config: dict[str, float] | None = None) -> dict[str, float]:
    merged = dict(DEFAULT_LABEL_CONFIG)
    if config:
        merged.update({key: float(value) for key, value in config.items() if value is not None})
    if overrides:
        for key in DEFAULT_LABEL_CONFIG:
            value = getattr(overrides, key, None)
            if value is not None:
                merged[key] = float(value)
    return merged


def _proposal_bucket(label: dict[str, Any]) -> str:
    evidence = _json_value(label.get("evidence_json")) or {}
    if not isinstance(evidence, dict):
        return "general"
    selection = evidence.get("selection") if isinstance(evidence.get("selection"), dict) else {}
    outcome = evidence.get("outcome") if isinstance(evidence.get("outcome"), dict) else {}
    gap = _as_float(selection.get("gap_pct"))
    from_high = _as_float(selection.get("from_high_pct"))
    runup = _as_float(outcome.get("max_runup_3d_pct"))
    if label.get("label") == "false_positive":
        if gap is not None and gap >= 5:
            return "gap_chase"
        if from_high is not None and from_high >= -3:
            return "near_high_chase"
        return "positive_failed"
    if label.get("label") == "false_negative":
        if runup is not None and runup >= 10:
            return "strong_missed_runup"
        return "missed_runup"
    return "general"


def build_lesson_candidate_proposals(
    labels: list[dict[str, Any]],
    *,
    min_sample: int = 3,
    generated_at: str | None = None,
) -> dict[str, Any]:
    generated = generated_at or _utc_now()
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for label in labels:
        if label.get("owner") != "claude_selection":
            continue
        if label.get("label") not in {"false_positive", "false_negative"}:
            continue
        groups[(str(label.get("market") or ""), str(label.get("label") or ""), _proposal_bucket(label))].append(label)

    markets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (market, label_name, bucket), items in sorted(groups.items()):
        sample_count = len(items)
        if sample_count < int(min_sample):
            continue
        severity = "high" if sample_count >= max(int(min_sample) * 2, 6) else "medium"
        confidence = min(0.95, 0.65 + sample_count / 100.0)
        if label_name == "false_negative":
            metric_key = "watch_only_missed_runup_ratio"
            action_hint = (
                "WATCH/AVOID candidates with strong 3-day runup and no explicit risk veto should be reviewed "
                "for earlier BUY_READY or PROBE_READY promotion."
            )
            summary = "Missed winner pattern without a clear risk veto."
        else:
            metric_key = "trade_ready_false_positive_ratio"
            action_hint = (
                "Positive selections that quickly draw down should be demoted when gap, near-high chase, "
                "or weak confirmation evidence is present."
            )
            summary = "Positive selection false-positive pattern."
        markets[market].append(
            {
                "id": f"claude_label_{market.lower()}_{label_name}_{bucket}",
                "market": market,
                "metric_key": metric_key,
                "scope": "selection",
                "bucket": bucket,
                "summary": summary,
                "action_hint": action_hint,
                "breached": True,
                "severity": severity,
                "confidence": round(confidence, 2),
                "sample_count": sample_count,
                "min_sample": int(min_sample),
                "claude_actionable": True,
                "ops_flag": False,
                "quality_version": QUALITY_VERSION,
                "source": "claude_decision_labels",
                "generated_at": generated,
                "selection_keys": [str(item.get("selection_key") or "") for item in items[:20]],
            }
        )
    return {"generated_at": generated, "source": "claude_decision_labels", "markets": dict(markets)}


def _has_mojibake_text(text: str) -> bool:
    return bool(re.search(r"[\ufffd\u0080-\u009f\u3130-\u318f]", text))


def write_lesson_candidate_proposals(payload: dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if _has_mojibake_text(text):
        raise ValueError("Refusing to write lesson proposal with suspicious mojibake text.")
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text + "\n")
    return path


def label_claude_judgments(
    *,
    db_path: str | Path = DEFAULT_FACT_DB,
    date: str = "",
    start_date: str = "",
    end_date: str = "",
    market: str = "ALL",
    runtime_mode: str = "live",
    latest_only: bool = False,
    write: bool = False,
    config: dict[str, float] | None = None,
    write_lesson_candidates: bool = False,
    lesson_output: str | Path | None = None,
    lesson_min_sample: int = 3,
) -> dict[str, Any]:
    start_date, end_date = _resolve_dates(date=date, start_date=start_date, end_date=end_date)
    market_key = _market(market or "ALL")
    runtime_key = str(runtime_mode or "live").strip().lower()
    merged_config = _merge_config(config=config)
    now = _utc_now()
    summary: dict[str, Any] = {
        "db_path": str(db_path),
        "start_date": start_date,
        "end_date": end_date,
        "market": market_key,
        "runtime_mode": runtime_key,
        "dry_run": not bool(write),
        "latest_only": bool(latest_only),
        "fact_rows": 0,
        "labels_generated": 0,
        "labels_written": 0,
        "by_label": {},
        "by_owner": {},
        "lesson_candidate_proposals": 0,
        "lesson_output": "",
        "status": "OK",
    }
    db = Path(db_path)
    if not db.exists():
        summary["status"] = "MISSING_FACT_DB"
        return summary

    if write:
        init_schema(db)
    with closing(_connect(db)) as conn:
        rows = _load_rows(
            conn,
            start_date=start_date,
            end_date=end_date,
            market=market_key,
            runtime_mode=runtime_key,
            latest_only=bool(latest_only),
        )
        labels = [_label_row(row, merged_config, now) for row in rows]
        summary["fact_rows"] = len(rows)
        summary["labels_generated"] = len(labels)
        summary["by_label"] = dict(Counter(row["label"] for row in labels))
        summary["by_owner"] = dict(Counter(row["owner"] for row in labels))
        if write:
            with conn:
                for label in labels:
                    _upsert_label(conn, label)
            summary["labels_written"] = len(labels)

        if write_lesson_candidates:
            payload = build_lesson_candidate_proposals(labels, min_sample=lesson_min_sample, generated_at=now)
            proposal_count = sum(len(items) for items in (payload.get("markets") or {}).values())
            summary["lesson_candidate_proposals"] = proposal_count
            output = (
                Path(lesson_output)
                if lesson_output
                else DEFAULT_LESSON_OUTPUT_DIR / f"lesson_candidate_proposals_{(end_date or start_date or now[:10]).replace('-', '')}.json"
            )
            write_lesson_candidate_proposals(payload, output)
            summary["lesson_output"] = str(output)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Label Claude selection, execution, risk, and data-quality judgments.")
    parser.add_argument("--db", default=str(DEFAULT_FACT_DB))
    parser.add_argument("--date", default="")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--market", default="ALL")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--latest-only", action="store_true")
    parser.add_argument("--write", action="store_true", help="write labels to decision_labels; default is dry-run")
    parser.add_argument("--dry-run", action="store_true", help="explicit dry-run alias")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-lesson-candidates", action="store_true")
    parser.add_argument("--lesson-output", default="")
    parser.add_argument("--lesson-min-sample", type=int, default=3)
    parser.add_argument("--allow-stale", action="store_true",
                        help="fact_* 가 묵었어도 라벨링 강행(기본은 stale이면 차단)")
    for key, default in DEFAULT_LABEL_CONFIG.items():
        parser.add_argument(f"--{key.replace('_', '-')}", dest=key, type=float, default=None)
    args = parser.parse_args(argv)

    if warn_if_fact_data_stale(args.db, allow_stale=bool(args.allow_stale)):
        print("[fact-freshness] fact 데이터가 stale이라 라벨링을 중단했다. "
              "build_claude_decision_facts.py 실행 후 재시도하거나 --allow-stale 사용.", file=sys.stderr)
        return 2

    summary = label_claude_judgments(
        db_path=args.db,
        date=args.date,
        start_date=args.start_date,
        end_date=args.end_date,
        market=args.market,
        runtime_mode=args.runtime_mode,
        latest_only=bool(args.latest_only),
        write=bool(args.write) and not bool(args.dry_run),
        config=_merge_config(args),
        write_lesson_candidates=bool(args.write_lesson_candidates),
        lesson_output=args.lesson_output or None,
        lesson_min_sample=int(args.lesson_min_sample),
    )
    if args.json:
        print(_json(summary))
    else:
        print(
            "claude judgment labels: "
            f"rows={summary['fact_rows']} generated={summary['labels_generated']} "
            f"written={summary['labels_written']} by_label={summary['by_label']}"
        )
        if summary.get("lesson_output"):
            print(f"lesson proposals: {summary['lesson_output']}")
    return 0 if summary.get("status") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
