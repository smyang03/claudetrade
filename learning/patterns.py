from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any


def grade_pattern(sample_count: int) -> str:
    sample = int(sample_count or 0)
    if sample < 10:
        return "observation_only"
    if sample < 30:
        return "weak_reference"
    if sample < 50:
        return "trusted_candidate"
    return "operating_principle_candidate"


def mark_expired_patterns(patterns: list[dict[str, Any]], *, as_of: date | None = None, expiry_days: int = 90) -> list[dict[str, Any]]:
    today = as_of or date.today()
    out = []
    for pattern in patterns:
        item = dict(pattern)
        last_verified = str(item.get("last_verified_at") or item.get("last_seen_at") or "")[:10]
        expired = False
        if last_verified:
            try:
                expired = today - datetime.strptime(last_verified, "%Y-%m-%d").date() > timedelta(days=expiry_days)
            except ValueError:
                expired = True
        if expired:
            item["quality"] = "SUSPECT"
            item["expiry_reason"] = f"unverified_over_{expiry_days}d"
        item["grade"] = grade_pattern(int(item.get("sample_count", 0) or 0))
        out.append(item)
    return out


def prompt_eligible_patterns(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weak = [p for p in patterns if p.get("grade") == "weak_reference" and p.get("quality") != "SUSPECT"][:3]
    trusted = [p for p in patterns if p.get("grade") in ("trusted_candidate", "operating_principle_candidate") and p.get("quality") != "SUSPECT"]
    return weak + trusted

