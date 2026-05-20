from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path


WARNING_ONLY_ISSUES = {
    "broker_position_removed",
    "broker_position_injected",
    "broker_qty_corrected",
    "quote_outlier",
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass


def _date_from_payload_or_name(path: Path, payload: dict[str, Any]) -> str:
    raw = (
        payload.get("session_date")
        or payload.get("date")
        or (payload.get("actual_result") or {}).get("session_date")
        or (payload.get("actual_result") or {}).get("date")
    )
    text = str(raw or "").strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    match = re.search(r"live_(\d{8})_([A-Z]{2})", path.name)
    if match:
        day = match.group(1)
        return f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    return ""


def _market_from_payload_or_name(path: Path, payload: dict[str, Any]) -> str:
    raw = payload.get("market") or (payload.get("actual_result") or {}).get("market")
    market = str(raw or "").upper().strip()
    if market in {"KR", "US"}:
        return market
    match = re.search(r"live_\d{8}_([A-Z]{2})", path.name)
    return match.group(1) if match else ""


def _issue_key(issue: Any) -> str:
    text = str(issue or "").strip()
    if text.startswith("sell_failed:"):
        return "sell_failed"
    return text


def _is_learning_excluded(*, contaminated: bool, issues: list[Any], explicit: Any) -> bool:
    if explicit is not None:
        return bool(explicit)
    if not contaminated:
        return False
    if not issues:
        return True
    return any(_issue_key(issue) not in WARNING_ONLY_ISSUES for issue in issues)


def _prompt_policy_exclusion(
    *,
    contaminated: bool,
    learning_excluded: bool,
    explicit: Any,
    explicit_reason: Any,
    selection_evidence_verified: bool,
) -> tuple[bool, str]:
    reason = str(explicit_reason or "").strip()
    if learning_excluded:
        return True, reason or "execution_learning_excluded"
    if explicit is not None:
        excluded = bool(explicit)
        return excluded, reason if excluded else ""
    if selection_evidence_verified:
        return False, ""
    if contaminated:
        return True, reason or "execution_contaminated"
    return False, ""


def load_execution_sources(log_dir: Path, *, market_filter: str = "") -> dict[tuple[str, str], dict[str, Any]]:
    sources: dict[tuple[str, str], dict[str, Any]] = {}
    market_filter = market_filter.upper().strip()
    for path in sorted(log_dir.glob("live_*.json")):
        try:
            payload = _read_json(path)
        except Exception:
            continue
        market = _market_from_payload_or_name(path, payload)
        if market_filter and market != market_filter:
            continue
        date_key = _date_from_payload_or_name(path, payload)
        if not market or not date_key:
            continue
        actual = dict(payload.get("actual_result") or {})
        health = dict(payload.get("execution_health") or {})
        contaminated = bool(actual.get("execution_contaminated", health.get("contaminated", False)))
        issues = list(actual.get("execution_issues") or health.get("reasons") or [])
        explicit_excluded = actual.get("execution_learning_excluded")
        if explicit_excluded is None:
            explicit_excluded = health.get("learning_excluded")
        learning_excluded = _is_learning_excluded(
            contaminated=contaminated,
            issues=issues,
            explicit=explicit_excluded,
        )
        warning = actual.get("execution_warning")
        if warning is None:
            warning = bool(contaminated and not learning_excluded)
        prompt_policy_excluded, policy_exclusion_reason = _prompt_policy_exclusion(
            contaminated=contaminated,
            learning_excluded=learning_excluded,
            explicit=actual.get("prompt_policy_excluded", health.get("prompt_policy_excluded")),
            explicit_reason=actual.get("policy_exclusion_reason", health.get("policy_exclusion_reason")),
            selection_evidence_verified=bool(
                actual.get("selection_evidence_verified", health.get("selection_evidence_verified", False))
            ),
        )
        sources[(market, date_key)] = {
            "market": market,
            "date": date_key,
            "source_file": str(path),
            "execution_contaminated": contaminated,
            "execution_learning_excluded": learning_excluded,
            "prompt_policy_excluded": prompt_policy_excluded,
            "policy_exclusion_reason": policy_exclusion_reason,
            "execution_warning": bool(warning),
            "execution_issues": issues,
            "execution_issue_labels": list(actual.get("execution_issue_labels") or health.get("labels") or []),
            "execution_issue_details": list(actual.get("execution_issue_details") or health.get("details") or []),
        }
    return sources


def reconcile_brain_payload(
    brain: dict[str, Any],
    sources: dict[tuple[str, str], dict[str, Any]],
    *,
    market_filter: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    updated = json.loads(json.dumps(brain, ensure_ascii=False))
    changes: list[dict[str, Any]] = []
    market_filter = market_filter.upper().strip()
    markets = (updated.get("markets") or {})
    for market, market_payload in markets.items():
        market_key = str(market or "").upper()
        if market_filter and market_key != market_filter:
            continue
        recent_days = list((market_payload or {}).get("recent_days") or [])
        for idx, record in enumerate(recent_days):
            if not isinstance(record, dict):
                continue
            date_key = str(record.get("date") or "").strip()
            source = sources.get((market_key, date_key))
            if not source:
                continue
            desired = dict(record)
            for key in (
                "execution_contaminated",
                "execution_learning_excluded",
                "prompt_policy_excluded",
                "policy_exclusion_reason",
                "execution_warning",
                "execution_issues",
                "execution_issue_labels",
                "execution_issue_details",
            ):
                desired[key] = source[key]
            desired["execution_source_file"] = source["source_file"]
            if desired.get("execution_learning_excluded"):
                desired["key_lesson"] = ""
                desired["issue_type"] = ""
            if desired != record:
                changed_fields = sorted(
                    key for key in set(desired) | set(record) if desired.get(key) != record.get(key)
                )
                recent_days[idx] = desired
                changes.append(
                    {
                        "market": market_key,
                        "date": date_key,
                        "source_file": source["source_file"],
                        "execution_learning_excluded": bool(desired.get("execution_learning_excluded")),
                        "prompt_policy_excluded": bool(desired.get("prompt_policy_excluded")),
                        "changed_fields": changed_fields,
                    }
                )
        market_payload["recent_days"] = recent_days
    return updated, changes


def run(
    *,
    brain_path: Path,
    log_dir: Path,
    market: str = "",
    apply: bool = False,
) -> dict[str, Any]:
    brain = _read_json(brain_path)
    sources = load_execution_sources(log_dir, market_filter=market)
    updated, changes = reconcile_brain_payload(brain, sources, market_filter=market)
    backup_path = ""
    if apply and changes:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = brain_path.with_name(f"{brain_path.name}.backup_{stamp}")
        shutil.copy2(brain_path, backup)
        _write_json_atomic(brain_path, updated)
        backup_path = str(backup)
    return {
        "brain_path": str(brain_path),
        "log_dir": str(log_dir),
        "apply": bool(apply),
        "source_count": len(sources),
        "change_count": len(changes),
        "backup_path": backup_path,
        "changes": changes,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile BrainDB execution flags from live daily judgment logs.")
    parser.add_argument("--brain", default=str(get_runtime_path("state", "brain.json")), help="brain.json path")
    parser.add_argument("--logs-dir", default=str(get_runtime_path("logs", "daily_judgment")), help="daily judgment log dir")
    parser.add_argument("--market", default="", choices=["", "KR", "US"], help="optional market filter")
    parser.add_argument("--apply", action="store_true", help="write changes after creating a backup")
    parser.add_argument("--json", action="store_true", help="print JSON summary")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    summary = run(
        brain_path=Path(args.brain),
        log_dir=Path(args.logs_dir),
        market=args.market,
        apply=bool(args.apply),
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"brain_path={summary['brain_path']}")
        print(f"log_dir={summary['log_dir']}")
        print(f"apply={summary['apply']}")
        print(f"source_count={summary['source_count']} change_count={summary['change_count']}")
        if summary.get("backup_path"):
            print(f"backup_path={summary['backup_path']}")
        for change in summary["changes"]:
            fields = ",".join(change["changed_fields"])
            print(
                f"{change['market']} {change['date']} "
                f"excluded={change['execution_learning_excluded']} fields=[{fields}]"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
