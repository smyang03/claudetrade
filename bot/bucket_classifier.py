from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime_paths import get_runtime_path


BUCKET_PRIORITY: tuple[str, ...] = (
    "pre_move_setup",
    "pullback_watch",
    "volume_surge",
    "near_breakout",
    "prev_strength",
    "momentum_now",
    "liquidity_leader",
    "sector_lagging_leader",
)

WINNER_THRESHOLDS: dict[str, dict[str, float]] = {
    "KR": {"winner_30m": 2.0, "winner_60m": 3.0, "winner_close_runup": 5.0, "winner_close_return": 3.0},
    "US": {"winner_30m": 1.0, "winner_60m": 1.5, "winner_close_runup": 2.5, "winner_close_return": 1.5},
}

_KR_HIGH_TURNOVER = 10_000_000_000.0
_KR_MIN_TRADABLE_TURNOVER = 1_000_000_000.0
_US_HIGH_TURNOVER = 300_000_000.0
_US_MIN_TRADABLE_TURNOVER = 50_000_000.0
_VOL_RATIO_CAP = 8.0


def classify_candidate_bucket(candidate: dict[str, Any], market: str) -> dict[str, Any]:
    market_key = str(market or "").upper()
    row = candidate or {}
    price = _num(row.get("price"))
    volume = _num(row.get("volume"))
    turnover = _num(row.get("turnover"), price * volume)
    change_rate = _num(row.get("change_rate"))
    vol_ratio = _num(row.get("vol_ratio"), _num(row.get("volume_ratio"), 1.0))
    from_high_pct = _optional_num(row.get("from_high_pct"))
    from_high_bucket = _text(row.get("from_high_bucket")).lower()
    liquidity_bucket = _text(row.get("liquidity_bucket")).lower()
    category = _text(row.get("category")).lower()
    above_ma60 = _boolish(row.get("above_ma60"))
    recent_strength = _first_num(
        row,
        (
            "recent_strength_pct",
            "relative_strength_3d",
            "relative_strength_5d",
            "rs_3d",
            "rs_5d",
            "prev_change_rate",
            "prev_change_pct",
            "prev_day_change_rate",
        ),
    )
    sector_strength = _first_num(row, ("sector_strength_pct", "sector_leader_change_pct", "sector_rank_score"))

    matched: set[str] = set()
    reasons: dict[str, str] = {}
    data_gaps: list[str] = []
    min_turnover = _KR_MIN_TRADABLE_TURNOVER if market_key == "KR" else _US_MIN_TRADABLE_TURNOVER
    high_turnover = _KR_HIGH_TURNOVER if market_key == "KR" else _US_HIGH_TURNOVER

    if change_rate >= (7.0 if market_key == "KR" else 4.0) or (
        change_rate >= (5.0 if market_key == "KR" else 3.0) and turnover >= min_turnover
    ):
        matched.add("momentum_now")
        reasons["momentum_now"] = f"change_rate={change_rate:.2f}, turnover={turnover:.0f}"

    if vol_ratio >= (4.0 if market_key == "KR" else 2.0):
        matched.add("volume_surge")
        reasons["volume_surge"] = f"vol_ratio={vol_ratio:.2f}"

    if turnover >= high_turnover or liquidity_bucket == "high" or category == "most_actives":
        matched.add("liquidity_leader")
        reasons["liquidity_leader"] = f"turnover={turnover:.0f}, liquidity={liquidity_bucket or category or '-'}"

    if recent_strength is None:
        data_gaps.append("recent_strength_unavailable")
    elif recent_strength >= (5.0 if market_key == "KR" else 2.5):
        matched.add("prev_strength")
        reasons["prev_strength"] = f"recent_strength={recent_strength:.2f}"

    near_breakout = from_high_bucket in {"near_high", "at_high"} or (
        from_high_pct is not None and -2.0 <= from_high_pct <= 1.0
    )
    if near_breakout:
        matched.add("near_breakout")
        reasons["near_breakout"] = f"from_high={from_high_bucket or from_high_pct}"

    pullback = (
        from_high_pct is not None
        and -10.0 <= from_high_pct <= -3.0
        and above_ma60 is not False
        and turnover >= min_turnover
    )
    if pullback:
        matched.add("pullback_watch")
        reasons["pullback_watch"] = f"from_high_pct={from_high_pct:.2f}, above_ma60={above_ma60}"

    pre_move = (
        recent_strength is not None
        and recent_strength >= (3.0 if market_key == "KR" else 1.5)
        and -1.0 <= change_rate <= 3.0
        and vol_ratio >= (1.3 if market_key == "KR" else 1.1)
        and above_ma60 is not False
        and turnover >= min_turnover
    )
    if pre_move:
        matched.add("pre_move_setup")
        reasons["pre_move_setup"] = (
            f"recent_strength={recent_strength:.2f}, change_rate={change_rate:.2f}, vol_ratio={vol_ratio:.2f}"
        )

    if sector_strength is None:
        data_gaps.append("sector_strength_unavailable")
    elif sector_strength >= (3.0 if market_key == "KR" else 1.5):
        matched.add("sector_lagging_leader")
        reasons["sector_lagging_leader"] = f"sector_strength={sector_strength:.2f}"

    primary = "unclassified"
    for bucket in BUCKET_PRIORITY:
        if bucket in matched:
            primary = bucket
            break
    secondary = [bucket for bucket in BUCKET_PRIORITY if bucket in matched and bucket != primary]

    scores = shadow_scores(candidate, market_key)
    return {
        "primary_bucket": primary,
        "secondary_buckets": secondary,
        "bucket_reasons": reasons,
        "bucket_data_gaps": sorted(set(data_gaps)),
        "shadow_scores": scores,
        **scores,
    }


def shadow_scores(candidate: dict[str, Any], market: str = "") -> dict[str, float]:
    row = candidate or {}
    price = max(0.0, _num(row.get("price")))
    volume = max(0.0, _num(row.get("volume")))
    turnover = max(0.0, _num(row.get("turnover"), price * volume))
    # Shadow scores measure movement intensity, not bullish direction.
    # A falling -5% candidate can rank high here because that is useful for
    # diagnosing volatile/noisy screener inputs separately from trade logic.
    change_rate_abs = abs(_num(row.get("change_rate")))
    vol_ratio = max(0.0, _num(row.get("vol_ratio"), _num(row.get("volume_ratio"), 1.0)))
    current = _optional_num(row.get("screen_score"))
    if current is None:
        current = math.log1p(turnover) + (change_rate_abs * 2.0) + (vol_ratio * 4.0)
    capped = math.log1p(turnover) + (change_rate_abs * 2.0) + (min(vol_ratio, _VOL_RATIO_CAP) * 4.0)
    log_weighted = math.log1p(turnover) + (change_rate_abs * 2.0) + (math.log1p(vol_ratio) * 8.0)
    turnover_weighted = (math.log1p(turnover) * 1.5) + (change_rate_abs * 2.0) + (min(vol_ratio, _VOL_RATIO_CAP) * 2.0)
    return {
        "score_current": round(float(current), 4),
        "score_vol_ratio_capped": round(float(capped), 4),
        "score_vol_ratio_log": round(float(log_weighted), 4),
        "score_turnover_weighted": round(float(turnover_weighted), 4),
    }


def annotate_bucket_detection_times(
    candidates: list[dict[str, Any]],
    *,
    market: str,
    session_date: str,
    detected_at: datetime,
    state_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    path = Path(state_path) if state_path else get_runtime_path("state", "bucket_detection_state.json")
    state = _load_state(path)
    records = state.setdefault("records", {})
    changed = False
    output: list[dict[str, Any]] = []
    market_key = str(market or "").upper()
    date_key = str(session_date or detected_at.date().isoformat())
    ts = detected_at.isoformat(timespec="seconds")

    for candidate in candidates or []:
        row = dict(candidate or {})
        ticker = normalize_ticker(market_key, row.get("ticker"))
        primary = str(row.get("primary_bucket") or "unclassified")
        key = _state_key(date_key, market_key, ticker, primary)
        rec = records.get(key)
        if not isinstance(rec, dict):
            rec = {
                "session_date": date_key,
                "market": market_key,
                "ticker": ticker,
                "primary_bucket": primary,
                "first_bucket_detected_at": ts,
                "last_bucket_detected_at": ts,
                "bucket_seen_count": 0,
            }
            records[key] = rec
            changed = True
        if rec.get("last_bucket_detected_at") != ts:
            rec["last_bucket_detected_at"] = ts
            changed = True
        rec["bucket_seen_count"] = int(rec.get("bucket_seen_count") or 0) + 1
        changed = True

        earliest = _earliest_for_ticker(records, date_key, market_key, ticker)
        row["first_bucket_detected_at"] = rec.get("first_bucket_detected_at", ts)
        row["last_bucket_detected_at"] = rec.get("last_bucket_detected_at", ts)
        row["bucket_seen_count"] = int(rec.get("bucket_seen_count") or 1)
        row["earliest_bucket_detected_at"] = earliest or row["first_bucket_detected_at"]
        output.append(row)

    if changed:
        _write_state_atomic(path, state)
    return output


def annotate_candidates_with_bucket_metadata(
    candidates: list[dict[str, Any]],
    *,
    market: str,
    session_date: str,
    detected_at: datetime,
    state_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    enriched = []
    for candidate in candidates or []:
        row = dict(candidate or {})
        row.update(classify_candidate_bucket(row, market))
        enriched.append(row)
    return annotate_bucket_detection_times(
        enriched,
        market=market,
        session_date=session_date,
        detected_at=detected_at,
        state_path=state_path,
    )


def normalize_ticker(market: str, ticker: Any) -> str:
    raw = str(ticker or "").strip()
    return raw.upper() if str(market or "").upper() == "US" else raw


def _state_key(session_date: str, market: str, ticker: str, primary_bucket: str) -> str:
    return "|".join([session_date, market, ticker, primary_bucket])


def _earliest_for_ticker(records: dict[str, Any], session_date: str, market: str, ticker: str) -> str:
    values = []
    prefix = "|".join([session_date, market, ticker, ""])
    for key, rec in records.items():
        if not str(key).startswith(prefix) or not isinstance(rec, dict):
            continue
        first = str(rec.get("first_bucket_detected_at") or "")
        if first:
            values.append(first)
    return min(values) if values else ""


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "records": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        if isinstance(data, dict) and isinstance(data.get("records"), dict):
            data.setdefault("schema_version", 1)
            return data
    except Exception:
        pass
    return {"schema_version": 1, "records": {}}


def _write_state_atomic(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return float(default)
        return float(str(value).replace(",", "").strip())
    except Exception:
        return float(default)


def _optional_num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def _first_num(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _optional_num(row.get(key))
        if value is not None:
            return value
    return None


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "ok"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None
