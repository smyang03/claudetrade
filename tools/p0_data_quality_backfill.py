from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phase1_trainer.digest_builder import build_breadth_summary

SUPPLEMENT_DIR = ROOT / "data" / "supplement"
DAILY_DIGEST_DIR = ROOT / "data" / "daily_digest"
BACKUP_ROOT = ROOT / "data" / "backups"
LOG_ROOT = ROOT / "logs" / "p0_backfill"

US_SUPPLEMENT_FIELDS = ("vix", "dxy", "oil_wti")
KR_SUPPLEMENT_FIELDS = ("usd_krw", "vkospi")
SUPPLEMENT_MARKETS = ("KR", "US")
BACKFILL_VERSION = "p0_data_quality_backfill_v1"


@dataclass
class FileReport:
    path: str
    kind: str
    market: str
    date: str
    status: str
    changes: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    backup_path: str | None = None
    sha256_before: str | None = None
    sha256_after: str | None = None
    new_payload: dict[str, Any] | None = field(default=None, repr=False)


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _file_mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return _now_iso()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _repo_rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"json_parse_error:{type(exc).__name__}"
    if not isinstance(data, dict):
        return None, "json_root_not_object"
    return data, None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _valid_positive(value: Any) -> bool:
    parsed = _float_or_none(value)
    return parsed is not None and parsed > 0


def _valid_usd_krw(value: Any) -> bool:
    parsed = _float_or_none(value)
    return parsed is not None and 100 <= parsed <= 3000


def _field_valid(field_name: str, value: Any) -> bool:
    if field_name == "usd_krw":
        return _valid_usd_krw(value)
    return _valid_positive(value)


def _add_unique(items: list[Any], value: str) -> bool:
    if value not in items:
        items.append(value)
        return True
    return False


def _ensure_dict(payload: dict[str, Any], key: str, changes: list[str]) -> dict[str, Any]:
    had_key = key in payload
    value = payload.get(key)
    if not isinstance(value, dict):
        payload[key] = {}
        if value is not None or not had_key:
            _add_unique(changes, f"{key}_normalized")
    return payload[key]


def _ensure_list(payload: dict[str, Any], key: str, changes: list[str]) -> list[Any]:
    had_key = key in payload
    value = payload.get(key)
    if isinstance(value, list):
        return value
    if value is None:
        payload[key] = []
    else:
        payload[key] = [value]
    if value is not None or not had_key:
        _add_unique(changes, f"{key}_normalized")
    return payload[key]


def _ensure_supplement_metadata(
    payload: dict[str, Any],
    path: Path,
    date_text: str,
    fields: tuple[str, ...],
    changes: list[str],
) -> tuple[dict[str, Any], dict[str, Any], list[Any]]:
    if not payload.get("date"):
        payload["date"] = date_text
        _add_unique(changes, "date_added")
    if not payload.get("collected_at"):
        payload["collected_at"] = _file_mtime_iso(path)
        _add_unique(changes, "collected_at_added")

    sources = _ensure_dict(payload, "sources", changes)
    fallback_used = _ensure_dict(payload, "fallback_used", changes)
    flags = _ensure_list(payload, "data_quality_flags", changes)
    _ensure_list(payload, "collection_errors", changes)

    for field_name in fields:
        if field_name not in sources:
            sources[field_name] = "historical" if _field_valid(field_name, payload.get(field_name)) else "backfill_offline"
            _add_unique(changes, f"sources.{field_name}_added")
        if field_name not in fallback_used:
            fallback_used[field_name] = False
            _add_unique(changes, f"fallback_used.{field_name}_added")

    return sources, fallback_used, flags


def _normalize_supplement(
    payload: dict[str, Any],
    path: Path,
    market: str,
    date_text: str,
) -> tuple[dict[str, Any], list[str], list[str]]:
    updated = copy.deepcopy(payload)
    changes: list[str] = []
    issues: list[str] = []
    fields = US_SUPPLEMENT_FIELDS if market == "US" else KR_SUPPLEMENT_FIELDS
    sources, fallback_used, flags = _ensure_supplement_metadata(updated, path, date_text, fields, changes)

    for field_name in fields:
        if _field_valid(field_name, updated.get(field_name)):
            continue

        if updated.get(field_name) is not None or field_name not in updated:
            updated[field_name] = None
            _add_unique(changes, f"{field_name}_null")

        sources[field_name] = "backfill_offline"
        fallback_used[field_name] = False
        if _add_unique(flags, f"{field_name}_missing"):
            _add_unique(changes, f"{field_name}_missing_flag")

    if changes:
        updated["backfilled_at"] = _now_iso()
        updated["backfill_version"] = BACKFILL_VERSION

    return updated, changes, issues


def _date_allowed(date_text: str, start_date: str | None, end_date: str | None) -> bool:
    if start_date and date_text < start_date:
        return False
    if end_date and date_text > end_date:
        return False
    return True


def _market_allowed(market: str, selected: str) -> bool:
    return selected == "ALL" or selected == market


def _supplement_date_from_path(path: Path) -> str:
    return path.stem


def _digest_meta_from_path(path: Path) -> tuple[str, str] | None:
    stem = path.stem
    if "_" not in stem:
        return None
    date_text, market = stem.rsplit("_", 1)
    market = market.upper()
    if market not in SUPPLEMENT_MARKETS:
        return None
    return date_text, market


def _scan_supplement_file(path: Path, market: str, args: argparse.Namespace) -> FileReport:
    date_text = _supplement_date_from_path(path)
    rel = _repo_rel(path)
    if not _date_allowed(date_text, args.date_from, args.date_to):
        return FileReport(rel, "supplement", market, date_text, "skipped_by_date_range")

    payload, error = _load_json(path)
    if error:
        return FileReport(rel, "supplement", market, date_text, "parse_error", issues=[error])

    assert payload is not None
    if args.verify:
        issues = _verify_supplement_payload(payload, market)
        status = "verify_error" if issues else "verified"
        return FileReport(rel, "supplement", market, date_text, status, issues=issues)

    updated, changes, issues = _normalize_supplement(payload, path, market, date_text)
    if changes:
        return FileReport(rel, "supplement", market, date_text, "needs_change", changes=changes, issues=issues, new_payload=updated)
    return FileReport(rel, "supplement", market, date_text, "already_clean", issues=issues)


def _verify_supplement_payload(payload: dict[str, Any], market: str) -> list[str]:
    issues: list[str] = []
    fields = US_SUPPLEMENT_FIELDS if market == "US" else KR_SUPPLEMENT_FIELDS
    for field_name in fields:
        value = payload.get(field_name)
        if value is None:
            continue
        if not _field_valid(field_name, value):
            issues.append(f"{field_name}_invalid")

    if market == "US" and "usd_krw" in payload and payload.get("usd_krw") is not None:
        if not _valid_usd_krw(payload.get("usd_krw")):
            issues.append("usd_krw_invalid")
    return issues


def _scan_digest_file(path: Path, args: argparse.Namespace) -> FileReport | None:
    meta = _digest_meta_from_path(path)
    if meta is None:
        return None
    date_text, market = meta
    if not _market_allowed(market, args.market):
        return None
    rel = _repo_rel(path)
    if not _date_allowed(date_text, args.date_from, args.date_to):
        return FileReport(rel, "daily_digest", market, date_text, "skipped_by_date_range")

    payload, error = _load_json(path)
    if error:
        return FileReport(rel, "daily_digest", market, date_text, "parse_error", issues=[error])

    assert payload is not None
    if args.verify:
        issues = _verify_digest_payload(payload)
        status = "verify_error" if issues else "verified"
        return FileReport(rel, "daily_digest", market, date_text, status, issues=issues)

    existing = payload.get("breadth_summary")
    if existing and not args.refresh_breadth:
        issues = []
        try:
            json.dumps(existing, ensure_ascii=False)
        except Exception as exc:
            issues.append(f"breadth_summary_not_serializable:{type(exc).__name__}")
        status = "blocked" if issues else "already_clean"
        return FileReport(rel, "daily_digest", market, date_text, status, issues=issues)

    technicals = payload.get("technicals")
    if not technicals:
        return FileReport(rel, "daily_digest", market, date_text, "blocked", issues=["digest_technicals_missing"])

    updated = copy.deepcopy(payload)
    updated["breadth_summary"] = build_breadth_summary(market, technicals, payload.get("context") or {})
    return FileReport(
        rel,
        "daily_digest",
        market,
        date_text,
        "needs_change",
        changes=["breadth_summary_refreshed" if args.refresh_breadth and existing else "breadth_summary_added"],
        new_payload=updated,
    )


def _verify_digest_payload(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not payload.get("breadth_summary"):
        issues.append("breadth_summary_missing")
    try:
        json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        issues.append(f"json_not_serializable:{type(exc).__name__}")
    return issues


def _iter_supplement_files(args: argparse.Namespace) -> list[tuple[Path, str]]:
    results: list[tuple[Path, str]] = []
    for market in SUPPLEMENT_MARKETS:
        if not _market_allowed(market, args.market):
            continue
        subdir = SUPPLEMENT_DIR / market.lower()
        for path in sorted(subdir.glob("*.json")):
            results.append((path, market))
    return results


def _iter_digest_files() -> list[Path]:
    return sorted(DAILY_DIGEST_DIR.glob("*.json"))


def scan(args: argparse.Namespace) -> list[FileReport]:
    reports: list[FileReport] = []
    for path, market in _iter_supplement_files(args):
        reports.append(_scan_supplement_file(path, market, args))
    for path in _iter_digest_files():
        report = _scan_digest_file(path, args)
        if report is not None:
            reports.append(report)
    return reports


def _backup_destination(path: Path, backup_dir: Path) -> Path:
    rel = path.resolve().relative_to(ROOT.resolve())
    return backup_dir / rel


def apply_writes(reports: list[FileReport], args: argparse.Namespace, stamp: str) -> Path | None:
    pending = [report for report in reports if report.status == "needs_change" and report.new_payload is not None]
    if not pending:
        return None

    backup_dir = BACKUP_ROOT / f"p0_backfill_{stamp}"
    manifest_entries: list[dict[str, Any]] = []

    for report in pending:
        src = ROOT / report.path
        report.sha256_before = _sha256(src)
        dst = _backup_destination(src, backup_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        report.backup_path = _repo_rel(dst)
        manifest_entries.append(
            {
                "original_path": report.path,
                "backup_path": report.backup_path,
                "sha256_before": report.sha256_before,
                "mutation_type": report.kind,
                "changed_fields": report.changes,
                "timestamp": _now_iso(),
            }
        )

    for report in pending:
        src = ROOT / report.path
        assert report.new_payload is not None
        _write_json(src, report.new_payload)
        report.sha256_after = _sha256(src)
        report.status = "changed"
        for entry in manifest_entries:
            if entry["original_path"] == report.path:
                entry["sha256_after"] = report.sha256_after

    manifest = {
        "version": BACKFILL_VERSION,
        "created_at": _now_iso(),
        "args": vars(args),
        "entries": manifest_entries,
    }
    manifest_path = backup_dir / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(manifest_path, manifest)
    return manifest_path


def _git_status_for_outputs() -> str:
    try:
        proc = subprocess.run(
            ["git", "status", "--short", "--", "data/backups", "logs/p0_backfill"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        return f"git_status_error:{type(exc).__name__}"
    return proc.stdout.strip()


def build_summary(
    reports: list[FileReport],
    args: argparse.Namespace,
    stamp: str,
    manifest_path: Path | None,
    global_issues: list[str] | None = None,
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for report in reports:
        status_counts[report.status] = status_counts.get(report.status, 0) + 1

    return {
        "version": BACKFILL_VERSION,
        "created_at": _now_iso(),
        "mode": "verify" if args.verify else ("write" if args.write else "dry_run"),
        "args": vars(args),
        "counts": {
            "scanned": len(reports),
            "needs_change": sum(1 for report in reports if report.changes),
            "changed": status_counts.get("changed", 0),
            "already_clean": status_counts.get("already_clean", 0),
            "verified": status_counts.get("verified", 0),
            "parse_error": status_counts.get("parse_error", 0),
            "verify_error": status_counts.get("verify_error", 0),
            "blocked": status_counts.get("blocked", 0),
            "skipped_by_date_range": status_counts.get("skipped_by_date_range", 0),
        },
        "status_counts": status_counts,
        "manifest_path": _repo_rel(manifest_path) if manifest_path else None,
        "global_issues": global_issues or [],
        "reports": [asdict(report) | {"new_payload": None} for report in reports],
    }


def write_summary(summary: dict[str, Any], stamp: str) -> Path:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    path = LOG_ROOT / f"p0_backfill_{stamp}_{summary['mode']}.json"
    _write_json(path, summary)
    return path


def print_human_summary(summary: dict[str, Any], summary_path: Path) -> None:
    counts = summary.get("counts") or {}
    print(f"mode={summary.get('mode')} scanned={counts.get('scanned')} needs_change={counts.get('needs_change')} changed={counts.get('changed')}")
    print(f"already_clean={counts.get('already_clean')} verified={counts.get('verified')} blocked={counts.get('blocked')} parse_error={counts.get('parse_error')} verify_error={counts.get('verify_error')}")
    if summary.get("manifest_path"):
        print(f"manifest={summary['manifest_path']}")
    print(f"summary={_repo_rel(summary_path)}")

    interesting = [
        report
        for report in summary.get("reports", [])
        if report.get("changes") or report.get("issues") or report.get("status") in {"changed", "verify_error", "parse_error", "blocked"}
    ]
    for report in interesting[:100]:
        details = []
        if report.get("changes"):
            details.append("changes=" + ",".join(report["changes"]))
        if report.get("issues"):
            details.append("issues=" + ",".join(report["issues"]))
        print(f"{report['status']} {report['path']} {' '.join(details)}".rstrip())
    if len(interesting) > 100:
        print(f"... {len(interesting) - 100} more records in summary JSON")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit and backfill P0 data quality fields.")
    parser.add_argument("--from", dest="date_from", help="Start date, YYYY-MM-DD.")
    parser.add_argument("--to", dest="date_to", help="End date, YYYY-MM-DD.")
    parser.add_argument("--market", choices=("KR", "US", "ALL"), default="ALL")
    parser.add_argument("--write", action="store_true", help="Mutate files after creating backups.")
    parser.add_argument("--verify", action="store_true", help="Verify that historical files satisfy the P0 contract.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary JSON.")
    parser.add_argument("--refresh-breadth", action="store_true", help="Overwrite existing digest breadth_summary.")
    parser.add_argument("--online-refresh", action="store_true", help="Reserved for explicit future network refresh mode.")
    parser.add_argument("--max-calls", type=int, default=0, help="Reserved budget for future online refresh mode.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    stamp = _now_stamp()

    global_issues: list[str] = []
    if args.online_refresh:
        global_issues.append("online_refresh_not_implemented")

    reports = scan(args)
    manifest_path = None
    if args.write and not args.verify and not global_issues:
        manifest_path = apply_writes(reports, args, stamp)

    if args.verify:
        git_status = _git_status_for_outputs()
        if git_status:
            global_issues.append(f"git_status_outputs_not_ignored:{git_status}")

    summary = build_summary(reports, args, stamp, manifest_path, global_issues)
    summary_path = write_summary(summary, stamp)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print_human_summary(summary, summary_path)

    if global_issues:
        return 2
    if args.verify and (summary["counts"]["verify_error"] or summary["counts"]["parse_error"] or summary["counts"]["blocked"]):
        return 1
    if summary["counts"]["parse_error"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
