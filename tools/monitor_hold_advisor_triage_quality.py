from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KST = timezone(timedelta(hours=9))
ROLES = ("bull", "bear", "neutral")


def now_kst() -> datetime:
    return datetime.now(KST)


def parse_dt(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.lower() == "now":
        return now_kst()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        try:
            parsed = datetime.fromisoformat(text[:19])
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def apply_start_config_env(path: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    overrides = data.get("env_overrides")
    if not isinstance(overrides, dict):
        return
    for key, value in overrides.items():
        os.environ[str(key)] = str(value)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def iter_decisions(log_dir: Path, start: datetime, end: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current = start.date()
    while current <= end.date():
        path = log_dir / f"decisions_{current.isoformat()}.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                ts = parse_dt(row.get("ts"))
                if ts is None or not (start <= ts <= end):
                    continue
                row["_parsed_ts"] = ts.isoformat()
                rows.append(row)
        current += timedelta(days=1)
    rows.sort(key=lambda item: item.get("_parsed_ts", ""))
    return rows


def decision_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            str(row.get("ts") or ""),
            str(row.get("market") or ""),
            str(row.get("ticker") or ""),
            str(row.get("decision_stage") or ""),
            str(row.get("duration_ms") or ""),
        ]
    )


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def path_kind(row: dict[str, Any]) -> str:
    votes = row.get("votes") if isinstance(row.get("votes"), dict) else {}
    keys = set(votes)
    if "triage" in keys and "challenge" in keys:
        return "triage_challenge"
    if "triage" in keys:
        return "triage_direct"
    if keys.intersection(ROLES):
        return "legacy_three_role"
    return "unknown"


def boundary_ok(row: dict[str, Any]) -> bool:
    if str(row.get("decision") or "").upper() != "HOLD":
        return True
    votes = row.get("votes") if isinstance(row.get("votes"), dict) else {}
    sources: list[dict[str, Any]] = []
    if "challenge" in votes:
        sources.append(votes.get("challenge") if isinstance(votes.get("challenge"), dict) else {})
    if "triage" in votes:
        sources.append(votes.get("triage") if isinstance(votes.get("triage"), dict) else {})
    if not sources:
        sources = [v for v in votes.values() if isinstance(v, dict) and str(v.get("action") or "").upper() == "HOLD"]
    for source in sources:
        next_review_min = safe_int(source.get("next_review_min"), 30)
        if (
            safe_float(source.get("protective_stop")) > 0
            and bool(str(source.get("invalid_if") or "").strip())
            and 5 <= next_review_min <= 240
        ):
            return True
    return False


def row_issues(row: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    kind = path_kind(row)
    votes = row.get("votes") if isinstance(row.get("votes"), dict) else {}
    if kind == "legacy_three_role":
        issues.append("legacy_three_role_seen_after_triage_enable")
    if kind == "unknown":
        issues.append("unknown_vote_shape")
    if any(role in votes for role in ROLES) and "triage" in votes:
        issues.append("mixed_legacy_and_triage_votes")
    triage = row.get("triage") if isinstance(row.get("triage"), dict) else {}
    if bool(triage.get("parse_error")):
        issues.append("triage_parse_error")
    if "challenge" in votes:
        challenge_vote = votes.get("challenge") if isinstance(votes.get("challenge"), dict) else {}
        if str(challenge_vote.get("reason") or "") == "challenge_error":
            issues.append("challenge_error_vote")
    if not boundary_ok(row):
        issues.append("hold_boundary_missing")
    return issues


def reconstruct_position(row: dict[str, Any]) -> dict[str, Any]:
    market = str(row.get("market") or "").upper()
    entry = safe_float(row.get("entry"))
    current = safe_float(row.get("current"))
    tp = safe_float(row.get("tp_price"))
    pos = {
        "ticker": row.get("ticker", "-"),
        "market": market,
        "entry": entry,
        "current_price": current,
        "tp": tp,
        "qty": row.get("qty", 1),
        "held_days": row.get("held_days", 0),
        "strategy": row.get("strategy", row.get("source_strategy", "shadow_compare")),
        "advisor_context_v2": row.get("advisor_context_v2") if isinstance(row.get("advisor_context_v2"), dict) else {},
        "decision_stage": row.get("decision_stage", "MANUAL_REVIEW"),
        "default_policy": row.get("default_policy", ""),
    }
    if market == "US":
        pos["display_avg_price"] = entry
        pos["display_current_price"] = current
        pos["display_tp_price"] = tp
        pos["display_currency"] = "USD"
    return pos


def aggregate_legacy(votes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    hold_score = sum(safe_float(v.get("confidence")) for v in votes.values() if str(v.get("action")).upper() == "HOLD")
    sell_score = sum(safe_float(v.get("confidence")) for v in votes.values() if str(v.get("action")).upper() == "SELL")
    action = "SELL" if sell_score > hold_score and sell_score >= 0.7 else "HOLD"
    action_voters = [v for v in votes.values() if str(v.get("action")).upper() == action]
    confidence = max((safe_float(v.get("confidence")) for v in action_voters), default=0.0)
    reason = ""
    for vote in action_voters:
        reason = str(vote.get("reason") or "")
        if reason:
            break
    return {
        "action": action,
        "confidence": round(confidence, 4),
        "hold_score": round(hold_score, 4),
        "sell_score": round(sell_score, 4),
        "reason": reason[:500],
    }


def run_legacy_compare(row: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return {
            "skipped": True,
            "reason": "dry_run",
            "votes": {},
            "aggregate": {"action": "DRY_RUN", "confidence": 0.0, "hold_score": 0.0, "sell_score": 0.0},
        }
    from minority_report import hold_advisor

    pos = reconstruct_position(row)
    market = str(row.get("market") or pos.get("market") or "").upper()
    stage = str(row.get("decision_stage") or "MANUAL_REVIEW")
    default_policy = str(row.get("default_policy") or "")
    digest_prompt = (
        "Shadow legacy comparison for live hold_advisor triage decision. "
        "This is read-only monitoring and must not infer order sizing. "
        f"Live decision={row.get('decision')} stage={stage} pnl_pct={row.get('pnl_pct')}."
    )
    votes: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        votes[role] = hold_advisor._ask_one(
            role,
            pos,
            market,
            digest_prompt,
            "",
            decision_stage=stage,
            default_policy=default_policy,
            minutes_to_close=None,
            force_exit_window=False,
        )
        time.sleep(0.2)
    return {"skipped": False, "votes": votes, "aggregate": aggregate_legacy(votes)}


def summarize(comparisons: list[dict[str, Any]], observations: list[dict[str, Any]]) -> dict[str, Any]:
    sampled = [item for item in comparisons if not item.get("legacy", {}).get("skipped")]
    agree = 0
    disagree = 0
    for item in sampled:
        if item.get("live_action") == item.get("legacy", {}).get("aggregate", {}).get("action"):
            agree += 1
        else:
            disagree += 1
    by_kind: dict[str, int] = {}
    issue_counts: dict[str, int] = {}
    for item in observations:
        by_kind[item.get("path_kind", "unknown")] = by_kind.get(item.get("path_kind", "unknown"), 0) + 1
        for issue in item.get("issues", []):
            issue_counts[issue] = issue_counts.get(issue, 0) + 1
    return {
        "observed_decisions": len(observations),
        "observed_by_path_kind": by_kind,
        "issue_counts": issue_counts,
        "sampled_comparisons": len(sampled),
        "action_agree": agree,
        "action_disagree": disagree,
        "comparison_api_calls": len(sampled) * 3,
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    summary = payload.get("summary") or {}
    lines = [
        "# Hold Advisor Triage Quality Monitor",
        "",
        f"- generated_at: {payload.get('generated_at')}",
        f"- window: {payload.get('start_at')} -> {payload.get('end_at')}",
        f"- observed_decisions: {summary.get('observed_decisions', 0)}",
        f"- observed_by_path_kind: {summary.get('observed_by_path_kind', {})}",
        f"- issue_counts: {summary.get('issue_counts', {})}",
        f"- sampled_comparisons: {summary.get('sampled_comparisons', 0)}",
        f"- action_agree: {summary.get('action_agree', 0)}",
        f"- action_disagree: {summary.get('action_disagree', 0)}",
        f"- comparison_api_calls: {summary.get('comparison_api_calls', 0)}",
        "",
        "## Disagreements",
    ]
    for item in payload.get("comparisons", []):
        legacy = item.get("legacy") or {}
        if item.get("live_action") == (legacy.get("aggregate") or {}).get("action"):
            continue
        lines.append(
            f"- {item.get('ts')} {item.get('market')} {item.get('ticker')} "
            f"{item.get('stage')}: live={item.get('live_action')} "
            f"legacy={(legacy.get('aggregate') or {}).get('action')} "
            f"kind={item.get('path_kind')} reason={item.get('sample_reason')}"
        )
    lines.append("")
    lines.append("## Issues")
    for item in payload.get("observations", []):
        issues = item.get("issues") or []
        if not issues:
            continue
        lines.append(
            f"- {item.get('ts')} {item.get('market')} {item.get('ticker')} "
            f"{item.get('stage')}: {', '.join(issues)}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    load_env_file(ROOT / ".env.live")
    load_env_file(ROOT / ".env")
    apply_start_config_env(ROOT / "config" / "v2_start_config.json")

    start = parse_dt(args.start_at) or now_kst()
    end = parse_dt(args.end_at)
    if end is None:
        today_end = now_kst().replace(hour=6, minute=0, second=0, microsecond=0)
        end = today_end if today_end > now_kst() else now_kst()
    out_dir = ROOT / "logs" / "hold_advisor_monitor"
    state_path = ROOT / "state" / f"hold_advisor_quality_monitor_{start.strftime('%Y%m%d_%H%M%S')}.json"
    event_path = out_dir / f"triage_quality_events_{start.strftime('%Y%m%d_%H%M%S')}.jsonl"
    report_json = out_dir / f"triage_quality_report_{start.strftime('%Y%m%d_%H%M%S')}.json"
    report_md = out_dir / f"triage_quality_report_{start.strftime('%Y%m%d_%H%M%S')}.md"
    rng = random.Random(args.seed)
    state = read_json(state_path, {"seen": [], "comparisons": [], "observations": []})
    seen = set(state.get("seen") or [])
    comparisons: list[dict[str, Any]] = list(state.get("comparisons") or [])
    observations: list[dict[str, Any]] = list(state.get("observations") or [])

    append_jsonl(
        event_path,
        {
            "event": "monitor_start",
            "ts": now_kst().isoformat(),
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "sample_rate": args.sample_rate,
            "dry_run": bool(args.dry_run),
        },
    )

    while True:
        now = now_kst()
        rows = iter_decisions(ROOT / "logs" / "hold_advisor", start, min(now, end))
        for row in rows:
            key = decision_key(row)
            if key in seen:
                continue
            seen.add(key)
            kind = path_kind(row)
            issues = row_issues(row)
            observation = {
                "key": key,
                "ts": row.get("ts"),
                "ticker": row.get("ticker"),
                "market": row.get("market"),
                "stage": row.get("decision_stage"),
                "live_action": row.get("decision"),
                "path_kind": kind,
                "issues": issues,
            }
            observations.append(observation)
            append_jsonl(event_path, {"event": "observation", **observation})

            eligible = kind in {"triage_direct", "triage_challenge"}
            should_sample = eligible and (rng.random() < args.sample_rate or (args.sample_first and not comparisons))
            if should_sample:
                sample_reason = "first_sample" if args.sample_first and not comparisons else "random_sample"
                compare_started = now_kst()
                try:
                    legacy = run_legacy_compare(row, dry_run=bool(args.dry_run))
                    error = ""
                except Exception as exc:
                    legacy = {"skipped": True, "reason": "compare_error", "aggregate": {"action": "ERROR"}}
                    error = str(exc)[:300]
                comparison = {
                    "key": key,
                    "ts": row.get("ts"),
                    "ticker": row.get("ticker"),
                    "market": row.get("market"),
                    "stage": row.get("decision_stage"),
                    "path_kind": kind,
                    "live_action": row.get("decision"),
                    "live_confidence": row.get("triage", {}).get("confidence") if isinstance(row.get("triage"), dict) else None,
                    "sample_reason": sample_reason,
                    "started_at": compare_started.isoformat(),
                    "finished_at": now_kst().isoformat(),
                    "legacy": legacy,
                    "error": error,
                }
                comparisons.append(comparison)
                append_jsonl(event_path, {"event": "comparison", **comparison})

        payload = {
            "generated_at": now_kst().isoformat(),
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "sample_rate": args.sample_rate,
            "dry_run": bool(args.dry_run),
            "summary": summarize(comparisons, observations),
            "observations": observations,
            "comparisons": comparisons,
            "event_path": str(event_path),
        }
        write_json(report_json, payload)
        write_markdown(report_md, payload)
        write_json(state_path, {"seen": sorted(seen), "comparisons": comparisons, "observations": observations})

        if args.once or now >= end:
            break
        time.sleep(max(5, int(args.poll_sec)))

    append_jsonl(event_path, {"event": "monitor_end", "ts": now_kst().isoformat(), "report_json": str(report_json), "report_md": str(report_md)})
    print(json.dumps({"report_json": str(report_json), "report_md": str(report_md), "summary": payload["summary"]}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-at", default="now")
    parser.add_argument("--end-at", default="")
    parser.add_argument("--poll-sec", type=int, default=60)
    parser.add_argument("--sample-rate", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=20260530)
    parser.add_argument("--sample-first", action="store_true", default=True)
    parser.add_argument("--no-sample-first", dest="sample_first", action="store_false")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
