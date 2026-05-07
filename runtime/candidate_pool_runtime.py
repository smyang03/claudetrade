from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable


PROMPT_SCORE_MAX = 100.0
PROMPT_SCORE_MIN = 0.0

SOURCE_BONUSES = {
    "preopen_confirmed": 30.0,
    "opening_fresh": 25.0,
    "intraday_momentum": 20.0,
    "held": 15.0,
    "reentry": 15.0,
    "hard_pin": 20.0,
    "manual_pin": 20.0,
    "soft_pin": 8.0,
}

SOURCE_PENALTIES = {
    "bad_data": 30.0,
    "day_losers": 25.0,
    "overextended": 15.0,
}

DEFERRED_SOURCE_TAGS = {"intraday_momentum", "late_mover"}
GRADE_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}


@dataclass
class CandidateRecord:
    ticker: str
    market: str
    name: str | None = None
    sources: list[str] = field(default_factory=list)
    source_ranks: dict[str, int] = field(default_factory=dict)
    source_scores: dict[str, float] = field(default_factory=dict)
    first_seen_at: str = ""
    last_seen_at: str = ""
    preopen_anchor_at: str | None = None
    preopen_price: float | None = None
    current_price: float | None = None
    grade: str | None = None
    prompt_score: float = 0.0
    prompt_score_components: dict[str, float] = field(default_factory=dict)
    feature_snapshot_ref: str | None = None
    latest_features: dict[str, Any] = field(default_factory=dict)
    policy_tags: list[str] = field(default_factory=list)
    screen_bucket: str | None = None
    status: str = "active"

    def key(self) -> tuple[str, str]:
        return (self.market.upper(), normalize_ticker(self.ticker, self.market))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "market": self.market,
            "name": self.name,
            "sources": list(self.sources),
            "source_ranks": dict(self.source_ranks),
            "source_scores": dict(self.source_scores),
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "preopen_anchor_at": self.preopen_anchor_at,
            "preopen_price": self.preopen_price,
            "current_price": self.current_price,
            "grade": self.grade,
            "prompt_score": self.prompt_score,
            "prompt_score_components": dict(self.prompt_score_components),
            "feature_snapshot_ref": self.feature_snapshot_ref,
            "latest_features": dict(self.latest_features),
            "policy_tags": list(self.policy_tags),
            "screen_bucket": self.screen_bucket,
            "status": self.status,
        }


@dataclass
class CandidatePoolResult:
    full_pool: list[CandidateRecord]
    prompt_pool: list[CandidateRecord]
    excluded_from_prompt: list[dict[str, Any]]
    deferred_sources: list[str] = field(default_factory=list)

    def to_summary(self) -> dict[str, Any]:
        return {
            "full_pool_count": len(self.full_pool),
            "prompt_pool_count": len(self.prompt_pool),
            "excluded_count": len(self.excluded_from_prompt),
            "deferred_sources": list(self.deferred_sources),
        }


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_ticker(ticker: Any, market: str) -> str:
    text = str(ticker or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except Exception:
        return default


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)]


def _best_grade(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    return left if GRADE_RANK.get(str(left).upper(), 0) >= GRADE_RANK.get(str(right).upper(), 0) else right


def _min_text_time(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    return min(left, right)


def _max_text_time(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    return max(left, right)


def _unique_extend(existing: list[str], values: Iterable[str]) -> list[str]:
    seen = set(existing)
    out = list(existing)
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def candidate_from_raw(raw: dict[str, Any], *, market: str, source: str | None = None, seen_at: str | None = None) -> CandidateRecord:
    source_name = str(source or raw.get("source") or "base_universe").strip()
    market_text = str(market or raw.get("market") or "").upper()
    ticker = normalize_ticker(raw.get("ticker"), market_text)
    seen = str(seen_at or raw.get("detected_at") or raw.get("captured_at") or now_iso())
    latest_features = dict(raw.get("latest_features") or {})
    for key in (
        "post_open_3m_return_pct",
        "post_open_5m_return_pct",
        "post_open_10m_return_pct",
        "post_open_30m_return_pct",
        "post_open_mfe_pct",
        "post_open_mae_pct",
        "momentum_state",
        "data_quality",
    ):
        if key in raw and key not in latest_features:
            latest_features[key] = raw.get(key)
    rank = _as_int(raw.get("source_rank", raw.get("shadow_preopen_rank", raw.get("provider_rank", 0))), 0)
    score = _as_float(raw.get("source_score", raw.get("preopen_score", raw.get("screen_score", 0.0))), 0.0)
    policy_tags = _unique_extend(_as_list(raw.get("policy_tags")), _as_list(raw.get("risk_tags")))
    return CandidateRecord(
        ticker=ticker,
        market=market_text,
        name=raw.get("name"),
        sources=[source_name] if source_name else [],
        source_ranks={source_name: rank} if source_name else {},
        source_scores={source_name: score} if source_name else {},
        first_seen_at=str(raw.get("first_seen_at") or seen),
        last_seen_at=str(raw.get("last_seen_at") or seen),
        preopen_anchor_at=raw.get("anchor_price_at") or raw.get("preopen_anchor_at"),
        preopen_price=_as_float(raw.get("anchor_price", raw.get("preopen_price")), 0.0) or None,
        current_price=_as_float(raw.get("current_price", raw.get("price")), 0.0) or None,
        grade=raw.get("preopen_grade") or raw.get("grade"),
        feature_snapshot_ref=raw.get("feature_snapshot_ref"),
        latest_features=latest_features,
        policy_tags=policy_tags,
        screen_bucket=raw.get("screen_bucket"),
        status=str(raw.get("status") or "active"),
    )


def merge_candidate(left: CandidateRecord, right: CandidateRecord) -> CandidateRecord:
    if left.key() != right.key():
        raise ValueError("cannot merge different candidate keys")
    left.sources = _unique_extend(left.sources, right.sources)
    left.policy_tags = _unique_extend(left.policy_tags, right.policy_tags)
    left.source_ranks.update({k: v for k, v in right.source_ranks.items() if v})
    left.source_scores.update({k: v for k, v in right.source_scores.items() if v is not None})
    left.first_seen_at = _min_text_time(left.first_seen_at, right.first_seen_at)
    left.last_seen_at = _max_text_time(left.last_seen_at, right.last_seen_at)
    left.grade = _best_grade(left.grade, right.grade)
    left.name = left.name or right.name
    left.preopen_anchor_at = left.preopen_anchor_at or right.preopen_anchor_at
    left.preopen_price = left.preopen_price or right.preopen_price
    left.current_price = right.current_price or left.current_price
    left.feature_snapshot_ref = right.feature_snapshot_ref or left.feature_snapshot_ref
    left.latest_features.update(right.latest_features)
    left.screen_bucket = left.screen_bucket or right.screen_bucket
    if right.status and right.status != "active":
        left.status = right.status
    return left


def _preopen_is_confirmed(record: CandidateRecord) -> bool:
    features = record.latest_features or {}
    ret5 = features.get("ret_5m_pct", features.get("post_open_5m_return_pct"))
    ret30 = features.get("ret_30m_pct", features.get("post_open_30m_return_pct"))
    state = str(features.get("momentum_state") or "").lower()
    return (
        "preopen" in record.sources
        and (
            _as_float(ret5, 0.0) > 0
            or _as_float(ret30, 0.0) > 0
            or state in {"early_strength", "sustained"}
        )
    )


def score_candidate(record: CandidateRecord, *, deferred_sources: set[str] | None = None) -> CandidateRecord:
    deferred = set(DEFERRED_SOURCE_TAGS if deferred_sources is None else deferred_sources)
    components: dict[str, float] = {}
    if _preopen_is_confirmed(record):
        components["preopen_confirmed"] = SOURCE_BONUSES["preopen_confirmed"]
    for source in record.sources:
        if source in deferred:
            continue
        if source in SOURCE_BONUSES:
            components[source] = max(components.get(source, 0.0), SOURCE_BONUSES[source])
    tags = set(record.policy_tags)
    data_quality = str((record.latest_features or {}).get("data_quality") or "").lower()
    if "bad_data" in tags or data_quality in {"bad", "missing", "stale"}:
        components["bad_data"] = -SOURCE_PENALTIES["bad_data"]
    if "day_losers" in record.sources:
        components["day_losers"] = -SOURCE_PENALTIES["day_losers"]
    if "overextended" in tags or str((record.latest_features or {}).get("momentum_state") or "").lower() == "overextended":
        components["overextended"] = -SOURCE_PENALTIES["overextended"]
    score = max(PROMPT_SCORE_MIN, min(PROMPT_SCORE_MAX, sum(components.values())))
    record.prompt_score_components = components
    record.prompt_score = score
    return record


def build_candidate_pool(
    raw_candidates: Iterable[dict[str, Any] | CandidateRecord],
    *,
    market: str,
    prompt_cap: int = 30,
    deferred_sources: set[str] | None = None,
) -> CandidatePoolResult:
    records_by_key: dict[tuple[str, str], CandidateRecord] = {}
    for item in raw_candidates:
        record = item if isinstance(item, CandidateRecord) else candidate_from_raw(dict(item or {}), market=market)
        key = record.key()
        if key in records_by_key:
            records_by_key[key] = merge_candidate(records_by_key[key], record)
        else:
            records_by_key[key] = record
    records = [score_candidate(record, deferred_sources=deferred_sources) for record in records_by_key.values()]
    active_records = [record for record in records if record.status not in {"hard_block", "blocked"} and "hard_safety" not in record.policy_tags]
    hard_excluded = [
        {"ticker": record.ticker, "reason": "hard_safety", "prompt_score": record.prompt_score}
        for record in records
        if record not in active_records
    ]
    active_records.sort(key=lambda item: (-item.prompt_score, min(item.source_ranks.values() or [999999]), item.ticker))
    cap = max(0, int(prompt_cap or 0))
    prompt_pool = active_records[:cap] if cap else []
    excluded = hard_excluded + [
        {"ticker": record.ticker, "reason": "prompt_cap", "prompt_score": record.prompt_score}
        for record in active_records[cap:]
    ]
    deferred_seen = sorted({source for record in records for source in record.sources if source in (deferred_sources or DEFERRED_SOURCE_TAGS)})
    return CandidatePoolResult(
        full_pool=records,
        prompt_pool=prompt_pool,
        excluded_from_prompt=excluded,
        deferred_sources=deferred_seen,
    )
