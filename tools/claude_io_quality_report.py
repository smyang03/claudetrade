from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path

KST = timezone(timedelta(hours=9))

MOJIBAKE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("replacement_character", re.compile("\ufffd")),
    ("c1_control_text", re.compile(r"[\u0080-\u009f]")),
    ("hangul_compat_jamo", re.compile(r"[\u3130-\u318f]")),
    ("escaped_mojibake_byte", re.compile(r"\\x[89a-fA-F][0-9a-fA-F]")),
    ("common_korean_mojibake", re.compile(r"\?[\uac00-\ud7a3]|[\uac00-\ud7a3]\?{2,}|\?{2,}[\uac00-\ud7a3]")),
    ("double_question_mark", re.compile(r"(?<!\?)\?\?(?!\?)")),
)
HANGUL_COMPAT_JAMO_ALLOWLIST = {"\u318d"}  # U+318D 아래아: 정상 한국어 특수문자

SELECTION_LABELS = {"select_tickers", "selection", "pathb_selection"}
STRICT_JSON_LABEL_PREFIXES = (
    "select_tickers",
    "hold_advisor",
    "quick_exit",
    "postmortem",
    "pathb",
)
HOLD_BOUNDARY_KEYS = ("protective_stop", "invalid_if", "next_review_min")
HOLD_ACTION_KEYS = ("action", "decision", "category", "final_category")
PROMPT_SECTION_MARKERS: tuple[tuple[str, str], ...] = (
    ("candidates", "Candidates:"),
    ("runtime_evidence", "Runtime evidence pack"),
    ("market_context", "Market context:"),
    ("digest_news", "Digest news excerpt:"),
    ("active_lessons", "[active lessons]"),
    ("selection_feedback", "recent selection feedback"),
    ("tuning_contract", "Tuning feedback contract"),
    ("decision_contract", "Decision contract:"),
    ("hard_soft_boundary", "Hard/soft rule boundary:"),
    ("output_contract", "MACHINE-COMPACT OUTPUT CONTRACT"),
    ("rules", "Rules:"),
)
PROMPT_CANDIDATE_LINE_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}\s+chg=")


def _now_kst() -> datetime:
    return datetime.now(KST)


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


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


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


def _tail_path(path: str | Path) -> str:
    raw = str(path or "")
    return raw.replace(str(ROOT), "").lstrip("\\/")


def _within_window(ts: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    if ts is None:
        return False
    if start is not None and ts < start:
        return False
    if end is not None and ts > end:
        return False
    return True


def _tokens(row: dict[str, Any]) -> tuple[int, int]:
    tokens = row.get("tokens") if isinstance(row.get("tokens"), dict) else {}
    return (
        _safe_int(tokens.get("input") or tokens.get("input_tokens") or row.get("input_tokens")),
        _safe_int(tokens.get("output") or tokens.get("output_tokens") or row.get("output_tokens")),
    )


def _has_duration_ms(row: dict[str, Any]) -> bool:
    return row.get("duration_ms") not in (None, "")


def _starts_with_strict_json(raw_response: str) -> bool:
    stripped = str(raw_response or "").lstrip()
    if not stripped:
        return False
    if not stripped.startswith(("{", "[")):
        return False
    try:
        json.loads(stripped)
        return True
    except Exception:
        return False


def _jsonish_response(raw_response: str) -> bool:
    text = str(raw_response or "")
    return "{" in text and "}" in text


def _mojibake_matches(text: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for reason, pattern in MOJIBAKE_PATTERNS:
        found = []
        for match in pattern.finditer(text or ""):
            sample = match.group(0)
            if reason == "hangul_compat_jamo" and all(ch in HANGUL_COMPAT_JAMO_ALLOWLIST for ch in sample):
                continue
            found.append(sample)
            if len(found) >= 3:
                break
        if found:
            codepoints = sorted({f"U+{ord(ch):04X}" for sample in found for ch in sample})
            matches.append({"reason": reason, "samples": found, "codepoints": codepoints})
    return matches


def _mojibake_reasons(text: str) -> list[str]:
    return [str(item.get("reason") or "") for item in _mojibake_matches(text)]


def _duplicates(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    counts = Counter(str(item) for item in values if str(item or "").strip())
    return sorted([key for key, count in counts.items() if count > 1])


def _is_selection_call(label: str, parsed: dict[str, Any]) -> bool:
    normalized = parsed.get("_normalized") if isinstance(parsed.get("_normalized"), dict) else {}
    if "wl" in parsed or "tr" in parsed or "ca" in parsed:
        return True
    if "watchlist" in parsed or "trade_ready" in parsed:
        return True
    if normalized and ("watchlist" in normalized or "trade_ready" in normalized):
        return True
    return any(label.startswith(prefix) for prefix in SELECTION_LABELS)


def _selection_quality_issues(parsed: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    normalized = parsed.get("_normalized") if isinstance(parsed.get("_normalized"), dict) else {}
    wl = parsed.get("wl")
    tr = parsed.get("tr")
    ca = parsed.get("ca")
    if wl is None and normalized:
        wl = normalized.get("watchlist")
    if tr is None and normalized:
        tr = normalized.get("trade_ready")

    wl_list = list(wl) if isinstance(wl, list) else []
    tr_list = list(tr) if isinstance(tr, list) else []
    wl_set = {str(item) for item in wl_list if str(item or "").strip()}
    tr_set = {str(item) for item in tr_list if str(item or "").strip()}

    if _duplicates(wl_list):
        issues.append("duplicate_watchlist_ticker")
    if _duplicates(tr_list):
        issues.append("duplicate_trade_ready_ticker")
    if tr_set and not tr_set.issubset(wl_set):
        issues.append("trade_ready_not_in_watchlist")

    if isinstance(ca, list):
        ca_tickers = [str(row.get("t") or row.get("ticker") or "") for row in ca if isinstance(row, dict)]
        ca_set = {ticker for ticker in ca_tickers if ticker}
        if _duplicates(ca_tickers):
            issues.append("duplicate_candidate_action")
        if wl_set and ca_set != wl_set:
            issues.append("candidate_actions_not_one_per_watchlist")
        for row in ca:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("t") or row.get("ticker") or "")
            action = str(row.get("a") or row.get("action") or "").upper()
            if ticker in tr_set and action not in {"BUY_READY", "PROBE_READY"}:
                issues.append("trade_ready_action_not_buy_or_probe")
                break
    elif wl_set and ("wl" in parsed or "ca" in parsed):
        issues.append("candidate_actions_missing")

    return sorted(set(issues))


def _hold_action(parsed: dict[str, Any]) -> str:
    for key in HOLD_ACTION_KEYS:
        action = str(parsed.get(key) or "").upper()
        if action:
            return action
    return ""


def _hold_quality_issues(parsed: dict[str, Any]) -> list[str]:
    action = _hold_action(parsed)
    issues: list[str] = []
    if not action:
        issues.append("hold_advisor_action_missing")
    confidence = parsed.get("confidence")
    if confidence in (None, "") or not 0.0 <= _safe_float(confidence) <= 1.0:
        issues.append("confidence_missing_or_out_of_range")
    if action == "HOLD":
        for key in HOLD_BOUNDARY_KEYS:
            value = parsed.get(key)
            if value in (None, "", 0, 0.0):
                issues.append(f"hold_boundary_missing_{key}")
    if action == "HOLD" and parsed.get("trail_pct") not in (None, ""):
        trail = _safe_float(parsed.get("trail_pct"))
        if trail < 0.0 or trail > 0.10:
            issues.append("trail_pct_out_of_range")
    return sorted(set(issues))


def _fallback_or_timeout(row: dict[str, Any], parsed: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    fields = [row, parsed, extra]
    for payload in fields:
        if bool(payload.get("fallback")):
            issues.append("fallback")
        if bool(payload.get("timeout")):
            issues.append("timeout")
        kind = str(payload.get("error_kind") or payload.get("fallback_reason") or "").upper()
        if "TIMEOUT" in kind:
            issues.append("timeout")
    if _safe_int(row.get("duration_ms")) >= 30000:
        issues.append("slow_call_30s")
    return sorted(set(issues))


def _fallback_authority_metadata(row: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    normalized = parsed.get("_normalized") if isinstance(parsed.get("_normalized"), dict) else {}
    fallback = any(
        bool(payload.get("fallback") or payload.get("_fallback_mode") or payload.get("triage_parse_error"))
        for payload in (row, parsed, extra, normalized)
        if isinstance(payload, dict)
    )
    action = str(
        parsed.get("action")
        or parsed.get("decision")
        or parsed.get("category")
        or parsed.get("final_category")
        or ""
    ).upper()
    trade_ready = normalized.get("trade_ready") if isinstance(normalized, dict) else []
    if not isinstance(trade_ready, list):
        trade_ready = parsed.get("trade_ready") if isinstance(parsed.get("trade_ready"), list) else parsed.get("tr")
    has_selection_authority = bool(trade_ready) if isinstance(trade_ready, list) else False
    has_hold_sell_authority = action in {"SELL", "STOP_LOSS"}
    computed_authority = bool(has_selection_authority or has_hold_sell_authority)
    explicit = None
    for payload in (extra, parsed, row):
        if isinstance(payload, dict) and "fallback_created_execution_authority" in payload:
            explicit = bool(payload.get("fallback_created_execution_authority"))
            break
    created = bool(explicit) if explicit is not None else bool(fallback and computed_authority)
    return {
        "fallback": fallback,
        "declared": explicit is not None,
        "fallback_created_execution_authority": created,
        "computed_execution_authority": computed_authority,
        "action": action,
        "trade_ready_count": len(trade_ready) if isinstance(trade_ready, list) else 0,
    }


def _quality_issues_for_call(row: dict[str, Any]) -> tuple[list[str], list[str]]:
    label = str(row.get("label") or "unknown")
    prompt = str(row.get("prompt") or "")
    raw_response = str(row.get("raw_response") or "")
    parsed = row.get("parsed") if isinstance(row.get("parsed"), dict) else {}
    input_tokens, output_tokens = _tokens(row)

    input_issues: list[str] = []
    output_issues: list[str] = []

    for reason in _mojibake_reasons(prompt):
        input_issues.append(f"prompt_mojibake_{reason}")
    for reason in _mojibake_reasons(raw_response):
        output_issues.append(f"response_mojibake_{reason}")
    if input_tokens >= 12000:
        input_issues.append("prompt_input_tokens_ge_12000")
    elif input_tokens >= 8000:
        input_issues.append("prompt_input_tokens_ge_8000")
    if any(label.startswith(prefix) for prefix in STRICT_JSON_LABEL_PREFIXES):
        contract_present = "strict JSON" in prompt or "JSON" in prompt or "MACHINE-COMPACT OUTPUT CONTRACT" in prompt
        if not contract_present:
            input_issues.append("json_contract_not_visible")

    if bool(row.get("parse_error")):
        output_issues.append("parse_error")
    if any(label.startswith(prefix) for prefix in STRICT_JSON_LABEL_PREFIXES):
        if not _starts_with_strict_json(raw_response):
            output_issues.append("response_not_strict_json")
        if raw_response.lstrip().startswith("```"):
            output_issues.append("response_fenced_json")
        if _jsonish_response(raw_response) and not raw_response.lstrip().startswith(("{", "[")):
            output_issues.append("response_has_preamble_or_wrapper")
    if output_tokens >= 2000:
        output_issues.append("output_tokens_ge_2000")
    output_issues.extend(_fallback_or_timeout(row, parsed))
    fallback_authority = _fallback_authority_metadata(row, parsed)
    if fallback_authority["fallback"] and not fallback_authority["declared"]:
        output_issues.append("fallback_authority_not_declared")
    if fallback_authority["fallback_created_execution_authority"]:
        output_issues.append("fallback_created_execution_authority")

    if _is_selection_call(label, parsed):
        output_issues.extend(_selection_quality_issues(parsed))
    if label.startswith("hold_advisor"):
        output_issues.extend(_hold_quality_issues(parsed))

    return sorted(set(input_issues)), sorted(set(output_issues))


def _prompt_section_name(line: str) -> str | None:
    stripped = line.strip()
    for name, marker in PROMPT_SECTION_MARKERS:
        if stripped.startswith(marker):
            return name
    return None


def _prompt_section_breakdown(prompt: str, *, limit: int = 8) -> list[dict[str, Any]]:
    if not prompt:
        return []
    lines = prompt.splitlines()
    sections: list[dict[str, Any]] = []
    current_name = "header"
    current_lines: list[str] = []

    def flush() -> None:
        if not current_lines:
            return
        text = "\n".join(current_lines)
        sections.append({"section": current_name, "lines": len(current_lines), "chars": len(text)})

    for line in lines:
        marker_name = _prompt_section_name(line)
        if marker_name is not None:
            flush()
            current_name = marker_name
            current_lines = [line]
        else:
            current_lines.append(line)
    flush()
    sections.sort(key=lambda row: (-_safe_int(row.get("chars")), str(row.get("section") or "")))
    return sections[:limit]


def _prompt_warning_sample(row: dict[str, Any], input_issues: list[str]) -> dict[str, Any] | None:
    if not any(issue.startswith("prompt_input_tokens_ge_") for issue in input_issues):
        return None
    prompt = str(row.get("prompt") or "")
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    candidate_lines = sum(1 for line in prompt.splitlines() if PROMPT_CANDIDATE_LINE_RE.match(line.strip()))
    active_lessons = extra.get("active_lessons") if isinstance(extra.get("active_lessons"), dict) else {}
    input_tokens, output_tokens = _tokens(row)
    return {
        "timestamp": row.get("_timestamp_kst") or row.get("timestamp"),
        "label": str(row.get("label") or "unknown"),
        "model": str(row.get("model") or "unknown"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_issues": input_issues,
        "prompt_chars": len(prompt),
        "candidate_lines": candidate_lines,
        "evidence_requested_count": _safe_int(extra.get("evidence_requested_count")),
        "evidence_pack_count": _safe_int(extra.get("evidence_pack_count")),
        "evidence_omitted_count": _safe_int(extra.get("evidence_omitted_count")),
        "compact_schema_enabled": bool(extra.get("compact_schema_enabled")),
        "compact_evidence_pack_enabled": bool(extra.get("compact_evidence_pack_enabled")),
        "active_lesson_count": _safe_int(active_lessons.get("count") or active_lessons.get("lesson_count")),
        "active_lesson_chars": _safe_int(active_lessons.get("chars") or active_lessons.get("lesson_chars")),
        "top_prompt_sections": _prompt_section_breakdown(prompt),
        "path": _tail_path(row.get("_path")),
    }


def _time_bucket_key(value: Any, *, minutes: int = 30) -> str:
    ts = _parse_dt(value)
    if ts is None:
        return "unknown"
    bucket_minute = (ts.minute // minutes) * minutes
    bucket = ts.replace(minute=bucket_minute, second=0, microsecond=0)
    return bucket.isoformat(timespec="minutes")


def load_raw_calls(
    *,
    raw_dir: Path,
    market: str,
    start: datetime | None,
    end: datetime | None,
) -> list[dict[str, Any]]:
    if not raw_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    market_key = str(market or "").upper()
    for path in sorted(raw_dir.glob("*.json")):
        data = _read_json(path)
        if not data:
            continue
        row_market = str(data.get("market") or "").upper()
        if market_key and row_market and row_market != market_key:
            continue
        ts = _parse_dt(data.get("timestamp") or data.get("created_at") or data.get("date"))
        if not _within_window(ts, start, end):
            continue
        data["_path"] = str(path)
        data["_timestamp_kst"] = ts.isoformat(timespec="seconds") if ts else ""
        rows.append(data)
    return rows


def build_quality_report(
    *,
    raw_dir: str | Path = get_runtime_path("logs", "raw_calls"),
    market: str = "US",
    start: str = "",
    end: str = "",
) -> dict[str, Any]:
    start_dt = _parse_dt(start) if start else None
    end_dt = _parse_dt(end) if end else None
    rows = load_raw_calls(raw_dir=Path(raw_dir), market=market, start=start_dt, end=end_dt)

    by_label: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "duration_ms": 0,
            "duration_observed_calls": 0,
            "duration_missing_calls": 0,
            "parse_errors": 0,
            "input_issues": Counter(),
            "output_issues": Counter(),
        }
    )
    by_model: Counter[str] = Counter()
    input_issues_total: Counter[str] = Counter()
    output_issues_total: Counter[str] = Counter()
    by_time_bucket: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "duration_ms": 0,
            "duration_observed_calls": 0,
            "duration_missing_calls": 0,
            "parse_errors": 0,
            "labels": Counter(),
            "input_issues": Counter(),
            "output_issues": Counter(),
        }
    )
    issue_samples: list[dict[str, Any]] = []
    slow_samples: list[dict[str, Any]] = []
    prompt_warning_samples: list[dict[str, Any]] = []
    fallback_authority_samples: list[dict[str, Any]] = []
    mojibake_samples: list[dict[str, Any]] = []

    total_input = 0
    total_output = 0
    total_duration = 0
    duration_observed_calls = 0
    duration_missing_calls = 0
    parse_errors = 0

    for row in rows:
        label = str(row.get("label") or "unknown")
        model = str(row.get("model") or "unknown")
        input_tokens, output_tokens = _tokens(row)
        duration_observed = _has_duration_ms(row)
        duration_ms = _safe_int(row.get("duration_ms")) if duration_observed else 0
        input_issues, output_issues = _quality_issues_for_call(row)
        fallback_authority = _fallback_authority_metadata(
            row,
            row.get("parsed") if isinstance(row.get("parsed"), dict) else {},
        )
        parse_error = bool(row.get("parse_error"))

        item = by_label[label]
        item["calls"] += 1
        item["input_tokens"] += input_tokens
        item["output_tokens"] += output_tokens
        item["duration_ms"] += duration_ms
        item["duration_observed_calls"] += 1 if duration_observed else 0
        item["duration_missing_calls"] += 0 if duration_observed else 1
        item["parse_errors"] += 1 if parse_error else 0
        item["input_issues"].update(input_issues)
        item["output_issues"].update(output_issues)

        bucket_key = _time_bucket_key(row.get("_timestamp_kst") or row.get("timestamp"))
        bucket = by_time_bucket[bucket_key]
        bucket["calls"] += 1
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["duration_ms"] += duration_ms
        bucket["duration_observed_calls"] += 1 if duration_observed else 0
        bucket["duration_missing_calls"] += 0 if duration_observed else 1
        bucket["parse_errors"] += 1 if parse_error else 0
        bucket["labels"].update([label])
        bucket["input_issues"].update(input_issues)
        bucket["output_issues"].update(output_issues)

        by_model[model] += 1
        input_issues_total.update(input_issues)
        output_issues_total.update(output_issues)
        total_input += input_tokens
        total_output += output_tokens
        total_duration += duration_ms
        duration_observed_calls += 1 if duration_observed else 0
        duration_missing_calls += 0 if duration_observed else 1
        parse_errors += 1 if parse_error else 0

        if input_issues or output_issues:
            issue_samples.append(
                {
                    "timestamp": row.get("_timestamp_kst") or row.get("timestamp"),
                    "label": label,
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "duration_ms": duration_ms,
                    "input_issues": input_issues,
                    "output_issues": output_issues,
                    "path": _tail_path(row.get("_path")),
                }
            )
        if fallback_authority["fallback"]:
            fallback_authority_samples.append(
                {
                    "timestamp": row.get("_timestamp_kst") or row.get("timestamp"),
                    "label": label,
                    "fallback_created_execution_authority": fallback_authority["fallback_created_execution_authority"],
                    "declared": fallback_authority["declared"],
                    "computed_execution_authority": fallback_authority["computed_execution_authority"],
                    "action": fallback_authority["action"],
                    "trade_ready_count": fallback_authority["trade_ready_count"],
                    "path": _tail_path(row.get("_path")),
                }
            )
        prompt_mojibake = _mojibake_matches(str(row.get("prompt") or ""))
        response_mojibake = _mojibake_matches(str(row.get("raw_response") or ""))
        if prompt_mojibake or response_mojibake:
            mojibake_samples.append(
                {
                    "timestamp": row.get("_timestamp_kst") or row.get("timestamp"),
                    "label": label,
                    "prompt": prompt_mojibake,
                    "response": response_mojibake,
                    "path": _tail_path(row.get("_path")),
                }
            )
        prompt_warning = _prompt_warning_sample(row, input_issues)
        if prompt_warning is not None:
            prompt_warning_samples.append(prompt_warning)
        if duration_ms >= 15000:
            slow_samples.append(
                {
                    "timestamp": row.get("_timestamp_kst") or row.get("timestamp"),
                    "label": label,
                    "duration_ms": duration_ms,
                    "path": _tail_path(row.get("_path")),
                }
            )

    label_rows = []
    for label, item in by_label.items():
        calls = int(item["calls"] or 0)
        label_rows.append(
            {
                "label": label,
                "calls": calls,
                "input_tokens": int(item["input_tokens"] or 0),
                "output_tokens": int(item["output_tokens"] or 0),
                "total_tokens": int(item["input_tokens"] or 0) + int(item["output_tokens"] or 0),
                "avg_input_tokens": round(int(item["input_tokens"] or 0) / calls, 1) if calls else 0.0,
                "avg_output_tokens": round(int(item["output_tokens"] or 0) / calls, 1) if calls else 0.0,
                "avg_duration_ms": (
                    round(int(item["duration_ms"] or 0) / int(item["duration_observed_calls"] or 1), 1)
                    if int(item["duration_observed_calls"] or 0)
                    else 0.0
                ),
                "duration_observed_calls": int(item["duration_observed_calls"] or 0),
                "duration_missing_calls": int(item["duration_missing_calls"] or 0),
                "parse_errors": int(item["parse_errors"] or 0),
                "input_issues": dict(item["input_issues"].most_common()),
                "output_issues": dict(item["output_issues"].most_common()),
            }
        )
    label_rows.sort(key=lambda row: (-int(row["input_tokens"] + row["output_tokens"]), str(row["label"])))

    time_bucket_rows: list[dict[str, Any]] = []
    for bucket_key, item in sorted(by_time_bucket.items()):
        calls = int(item["calls"] or 0)
        input_tokens = int(item["input_tokens"] or 0)
        output_tokens = int(item["output_tokens"] or 0)
        time_bucket_rows.append(
            {
                "bucket_start": bucket_key,
                "calls": calls,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "avg_input_tokens": round(input_tokens / calls, 1) if calls else 0.0,
                "avg_output_tokens": round(output_tokens / calls, 1) if calls else 0.0,
                "avg_duration_ms": (
                    round(int(item["duration_ms"] or 0) / int(item["duration_observed_calls"] or 1), 1)
                    if int(item["duration_observed_calls"] or 0)
                    else 0.0
                ),
                "duration_observed_calls": int(item["duration_observed_calls"] or 0),
                "duration_missing_calls": int(item["duration_missing_calls"] or 0),
                "parse_errors": int(item["parse_errors"] or 0),
                "top_labels": dict(item["labels"].most_common(5)),
                "input_issues": dict(item["input_issues"].most_common()),
                "output_issues": dict(item["output_issues"].most_common()),
            }
        )

    recommendations = _recommendations(
        input_issues=input_issues_total,
        output_issues=output_issues_total,
        calls=len(rows),
        total_input_tokens=total_input,
    )
    return {
        "generated_at": _now_kst().isoformat(timespec="seconds"),
        "raw_dir": str(raw_dir),
        "market": str(market or "").upper(),
        "start_at": start_dt.isoformat(timespec="seconds") if start_dt else "",
        "end_at": end_dt.isoformat(timespec="seconds") if end_dt else "",
        "calls": len(rows),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "avg_input_tokens": round(total_input / len(rows), 1) if rows else 0.0,
        "avg_output_tokens": round(total_output / len(rows), 1) if rows else 0.0,
        "avg_duration_ms": round(total_duration / duration_observed_calls, 1) if duration_observed_calls else 0.0,
        "duration_observed_calls": duration_observed_calls,
        "duration_missing_calls": duration_missing_calls,
        "parse_errors": parse_errors,
        "by_model": dict(by_model.most_common()),
        "by_label": label_rows,
        "by_time_bucket": time_bucket_rows,
        "input_issue_counts": dict(input_issues_total.most_common()),
        "output_issue_counts": dict(output_issues_total.most_common()),
        "issue_samples": issue_samples[:80],
        "prompt_warning_samples": sorted(prompt_warning_samples, key=lambda row: -_safe_int(row.get("input_tokens")))[:80],
        "fallback_authority_samples": fallback_authority_samples[:80],
        "mojibake_samples": mojibake_samples[:80],
        "slow_call_samples": slow_samples[:40],
        "recommendations": recommendations,
    }


def _recommendations(
    *,
    input_issues: Counter[str],
    output_issues: Counter[str],
    calls: int,
    total_input_tokens: int,
) -> list[dict[str, str]]:
    recs: list[dict[str, str]] = []
    if not calls:
        recs.append(
            {
                "priority": "P2",
                "area": "observability",
                "recommendation": "No Claude raw calls were observed in the window; verify the bot was active and raw call logging is enabled.",
            }
        )
        return recs
    if any(key.startswith("prompt_mojibake_") for key in input_issues):
        recs.append(
            {
                "priority": "P1",
                "area": "input_quality",
                "recommendation": "Fix the prompt text encoding path before changing trading policy; garbled Korean weakens evidence interpretation and review rationale.",
            }
        )
    if any(key.startswith("response_mojibake_") for key in output_issues):
        recs.append(
            {
                "priority": "P2",
                "area": "output_quality",
                "recommendation": "Audit response encoding for affected labels; garbled rationale should be kept out of operator-facing review text or clearly flagged.",
            }
        )
    if output_issues.get("response_not_strict_json", 0) > 0:
        recs.append(
            {
                "priority": "P1",
                "area": "output_contract",
                "recommendation": "Tighten JSON-only enforcement for affected labels or route non-strict responses through a bounded retry; parser recovery should remain fail-safe.",
            }
        )
    if output_issues.get("parse_error", 0) > 0:
        recs.append(
            {
                "priority": "P1",
                "area": "parser_safety",
                "recommendation": "Review parse-error samples and confirm fallback decisions cannot create BUY/SELL authority without runtime gates.",
            }
        )
    if output_issues.get("fallback_created_execution_authority", 0) > 0:
        recs.append(
            {
                "priority": "P1",
                "area": "fallback_authority",
                "recommendation": "Inspect fallback-created execution authority samples; fallback parsing must not create BUY/SELL authority without an explicit runtime owner.",
            }
        )
    if output_issues.get("fallback_authority_not_declared", 0) > 0:
        recs.append(
            {
                "priority": "P2",
                "area": "fallback_observability",
                "recommendation": "Add fallback_created_execution_authority=false to safe fallback raw-call metadata so parser recovery authority is auditable.",
            }
        )
    if any(key.startswith("hold_boundary_missing_") for key in output_issues):
        recs.append(
            {
                "priority": "P1",
                "area": "hold_advisor",
                "recommendation": "For HOLD advice, require protective_stop, invalid_if, and next_review_min; missing boundaries should stay bounded and rechecked.",
            }
        )
    if output_issues.get("duplicate_watchlist_ticker", 0) > 0 or output_issues.get("candidate_actions_not_one_per_watchlist", 0) > 0:
        recs.append(
            {
                "priority": "P2",
                "area": "selection_schema",
                "recommendation": "Add a compact-schema self-check or post-parse warning for duplicate watchlist entries and candidate-action coverage gaps.",
            }
        )
    if output_issues.get("slow_call_30s", 0) > 0 or output_issues.get("timeout", 0) > 0:
        recs.append(
            {
                "priority": "P2",
                "area": "latency_cost",
                "recommendation": "Separate timeout-prone labels from normal review flow and keep cache/cooldown guards active for repeated HOLD reviews.",
            }
        )
    avg_input = total_input_tokens / max(1, calls)
    if (
        avg_input >= 8000
        or input_issues.get("prompt_input_tokens_ge_8000", 0) > 0
        or input_issues.get("prompt_input_tokens_ge_12000", 0) > 0
    ):
        recs.append(
            {
                "priority": "P2",
                "area": "token_cost",
                "recommendation": "Reduce high-token prompts by trimming repeated calibration blocks and limiting evidence pack rows before model invocation; review labels with 8k+ input tokens even when the session average looks acceptable.",
            }
        )
    return recs


def write_markdown(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Claude I/O Quality Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- scope: {report.get('market')} {report.get('start_at')} ~ {report.get('end_at')}",
        f"- raw_calls: {report.get('calls')}",
        f"- tokens: input={report.get('input_tokens')} output={report.get('output_tokens')} total={report.get('total_tokens')}",
        f"- averages: input={report.get('avg_input_tokens')} output={report.get('avg_output_tokens')} duration_ms={report.get('avg_duration_ms')}",
        f"- duration_coverage: observed={report.get('duration_observed_calls')} missing={report.get('duration_missing_calls')}",
        f"- parse_errors: {report.get('parse_errors')}",
        "",
        "## Calls By Label",
        "",
    ]
    labels = list(report.get("by_label") or [])
    if labels:
        lines.append("| label | calls | input | output | total | avg input | avg output | avg ms | ms observed | ms missing | parse errors |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for row in labels:
            lines.append(
                f"| {row.get('label')} | {row.get('calls')} | {row.get('input_tokens')} | {row.get('output_tokens')} | "
                f"{row.get('total_tokens')} | {row.get('avg_input_tokens')} | {row.get('avg_output_tokens')} | {row.get('avg_duration_ms')} | "
                f"{row.get('duration_observed_calls')} | {row.get('duration_missing_calls')} | {row.get('parse_errors')} |"
            )
    else:
        lines.append("- no calls observed")
    timeline = list(report.get("by_time_bucket") or [])
    lines.extend(["", "## Usage Timeline", ""])
    if timeline:
        lines.append("| bucket start | calls | input | output | total | avg input | top labels | input issues | output issues |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |")
        for row in timeline:
            lines.append(
                f"| {row.get('bucket_start')} | {row.get('calls')} | {row.get('input_tokens')} | {row.get('output_tokens')} | "
                f"{row.get('total_tokens')} | {row.get('avg_input_tokens')} | "
                f"{json.dumps(row.get('top_labels') or {}, ensure_ascii=False, sort_keys=True)} | "
                f"{json.dumps(row.get('input_issues') or {}, ensure_ascii=False, sort_keys=True)} | "
                f"{json.dumps(row.get('output_issues') or {}, ensure_ascii=False, sort_keys=True)} |"
            )
    else:
        lines.append("- no time-bucket rows captured")
    lines.extend(["", "## Input Issues", ""])
    input_counts = report.get("input_issue_counts") or {}
    if input_counts:
        for key, count in input_counts.items():
            lines.append(f"- {key}: {count}")
    else:
        lines.append("- none")
    lines.extend(["", "## Output Issues", ""])
    output_counts = report.get("output_issue_counts") or {}
    if output_counts:
        for key, count in output_counts.items():
            lines.append(f"- {key}: {count}")
    else:
        lines.append("- none")
    lines.extend(["", "## Recommendations", ""])
    recs = report.get("recommendations") or []
    if recs:
        for row in recs:
            lines.append(f"- {row.get('priority')} {row.get('area')}: {row.get('recommendation')}")
    else:
        lines.append("- no action required from observed calls")
    samples = report.get("issue_samples") or []
    if samples:
        lines.extend(["", "## Issue Samples", ""])
        for row in samples[:30]:
            lines.append(
                f"- {row.get('timestamp')} {row.get('label')} input={row.get('input_issues')} "
                f"output={row.get('output_issues')} path={row.get('path')}"
            )
    fallback_samples = report.get("fallback_authority_samples") or []
    if fallback_samples:
        lines.extend(["", "## Fallback Authority Samples", ""])
        for row in fallback_samples[:30]:
            lines.append(
                f"- {row.get('timestamp')} {row.get('label')} declared={row.get('declared')} "
                f"created_authority={row.get('fallback_created_execution_authority')} "
                f"computed_authority={row.get('computed_execution_authority')} action={row.get('action')} "
                f"trade_ready_count={row.get('trade_ready_count')} path={row.get('path')}"
            )
    mojibake_samples = report.get("mojibake_samples") or []
    if mojibake_samples:
        lines.extend(["", "## Mojibake Samples", ""])
        for row in mojibake_samples[:30]:
            lines.append(
                f"- {row.get('timestamp')} {row.get('label')} "
                f"prompt={json.dumps(row.get('prompt') or [], ensure_ascii=False)} "
                f"response={json.dumps(row.get('response') or [], ensure_ascii=False)} "
                f"path={row.get('path')}"
            )
    prompt_warnings = report.get("prompt_warning_samples") or []
    if prompt_warnings:
        lines.extend(["", "## Prompt Warning Samples", ""])
        lines.append("| time | label | input | chars | candidates | evidence requested | evidence pack | lessons chars | top sections | path |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |")
        for row in prompt_warnings[:30]:
            sections = ", ".join(
                f"{section.get('section')}:{section.get('chars')}"
                for section in list(row.get("top_prompt_sections") or [])[:4]
                if isinstance(section, dict)
            )
            lines.append(
                f"| {row.get('timestamp')} | {row.get('label')} | {row.get('input_tokens')} | {row.get('prompt_chars')} | "
                f"{row.get('candidate_lines')} | {row.get('evidence_requested_count')} | {row.get('evidence_pack_count')} | "
                f"{row.get('active_lesson_chars')} | {sections} | {row.get('path')} |"
            )
    slow = report.get("slow_call_samples") or []
    if slow:
        lines.extend(["", "## Slow Calls", ""])
        for row in slow[:20]:
            lines.append(f"- {row.get('timestamp')} {row.get('label')} duration_ms={row.get('duration_ms')} path={row.get('path')}")
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def write_json(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a read-only Claude input/output quality report from raw call logs.")
    parser.add_argument("--raw-dir", default=str(get_runtime_path("logs", "raw_calls")))
    parser.add_argument("--market", default="US")
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--out-json", default="")
    parser.add_argument("--out-md", default="")
    args = parser.parse_args(argv)

    report = build_quality_report(
        raw_dir=Path(args.raw_dir),
        market=str(args.market or "US").upper(),
        start=str(args.start or ""),
        end=str(args.end or ""),
    )
    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "docs" / "reports" / f"claude_io_quality_{_now_kst().strftime('%Y%m%d_%H%M%S')}"
    out_json = Path(args.out_json) if args.out_json else out_dir / "claude_io_quality.json"
    out_md = Path(args.out_md) if args.out_md else out_dir / "claude_io_quality.md"
    write_json(report, out_json)
    write_markdown(report, out_md)
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), "calls": report["calls"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
