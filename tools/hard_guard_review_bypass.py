from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from runtime_paths import get_runtime_path


KST = timezone(timedelta(hours=9))
HARD_GUARD_REVIEW_BYPASS_EVENTS = (
    "hold_advisor_cache_hard_guard_bypass",
    "auto_sell_review_cooldown_hard_guard_bypass",
    "auto_sell_review_cooldown_hard_guard_active",
)


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


def _funnel_dir(log_dir: str | Path | None = None) -> Path:
    if log_dir:
        return Path(log_dir)
    return get_runtime_path("logs", "funnel", "placeholder", make_parents=False).parent


def _event_paths(*, event_type: str, session_date: str, market: str, log_dir: Path) -> list[Path]:
    market_key = str(market or "").strip().upper()
    markets = [market_key] if market_key in {"KR", "US"} else ["KR", "US"]
    day = str(session_date or "").replace("-", "").strip()
    paths: list[Path] = []
    for market_item in markets:
        pattern = f"{event_type}_{day}_{market_item}.jsonl" if day else f"{event_type}_*_{market_item}.jsonl"
        paths.extend(sorted(log_dir.glob(pattern)))
    return paths


def summarize_hard_guard_review_bypass(
    *,
    session_date: str = "",
    market: str = "",
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    log_dir: str | Path | None = None,
) -> dict[str, Any]:
    root = _funnel_dir(log_dir)
    event_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    ticker_counts: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    latest_event: dict[str, Any] = {}

    if root.exists():
        for event_type in HARD_GUARD_REVIEW_BYPASS_EVENTS:
            for path in _event_paths(event_type=event_type, session_date=session_date, market=market, log_dir=root):
                try:
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(row, dict):
                        continue
                    ts = _parse_dt(row.get("written_at") or row.get("at") or row.get("timestamp"))
                    if start_at is not None and ts is not None and ts < start_at.astimezone(KST):
                        continue
                    if end_at is not None and ts is not None and ts > end_at.astimezone(KST):
                        continue
                    event_name = str(row.get("event_type") or event_type)
                    event_counts[event_name] += 1
                    reason = str(row.get("reason") or "unknown")
                    ticker = str(row.get("ticker") or "unknown").upper()
                    reason_counts[reason] += 1
                    ticker_counts[ticker] += 1
                    sample = {
                        "event_type": event_name,
                        "written_at": row.get("written_at", ""),
                        "market": row.get("market", ""),
                        "ticker": ticker,
                        "reason": reason,
                        "detail": str(row.get("detail") or "")[:240],
                        "hard_guard_source": row.get("hard_guard_source") or row.get("source") or "",
                        "hard_guard_current": row.get("hard_guard_current"),
                        "hard_guard_stop": row.get("hard_guard_stop"),
                    }
                    samples.append(sample)
                    if not latest_event or (ts is not None and ts >= (_parse_dt(latest_event.get("written_at")) or datetime.min.replace(tzinfo=KST))):
                        latest_event = dict(sample)

    return {
        "available": True,
        "log_dir": str(root),
        "session_date": session_date,
        "market": str(market or "").upper(),
        "event_counts": dict(event_counts),
        "total_count": int(sum(event_counts.values())),
        "by_reason": dict(reason_counts),
        "by_ticker": dict(ticker_counts),
        "latest_event": latest_event,
        "samples": samples[-20:],
        "policy_change_allowed": False,
    }
