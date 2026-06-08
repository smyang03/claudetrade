from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from preopen.news_enrichment import enrich_candidates_with_news, load_preopen_news_payload
from preopen.scheduler import regular_open_dt
from runtime_paths import get_runtime_path


PROMPT_STRICT_LOSS_FILTER_V1 = "strict_loss_filter_v1"
PROMPT_MARKET_BALANCED_V2 = "market_balanced_v2"
PROMPT_MARKET_GROWTH_TAPE_V3 = "market_growth_tape_v3"
PROMPT_US_LIQUID_QUALITY_V4 = "us_liquid_quality_v4"
PROMPT_US_EDGE_HUNTER_V5 = "us_edge_hunter_v5"
PROMPT_US_SLATE_ADAPTIVE_V6 = "us_slate_adaptive_v6"
SUPPORTED_PROMPT_VERSIONS = {
    PROMPT_STRICT_LOSS_FILTER_V1,
    PROMPT_MARKET_BALANCED_V2,
    PROMPT_MARKET_GROWTH_TAPE_V3,
    PROMPT_US_LIQUID_QUALITY_V4,
    PROMPT_US_EDGE_HUNTER_V5,
    PROMPT_US_SLATE_ADAPTIVE_V6,
}
DEFAULT_PROMPT_VERSION = PROMPT_STRICT_LOSS_FILTER_V1
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_CANDIDATES = 60
DEFAULT_PROMOTE_LIMIT = 5
DEFAULT_KEEP_WATCH_LIMIT = 8
FUTURE_PREFIXES = ("outcome_", "post_open_")
FUTURE_FIELDS = {
    "actual_ordered",
    "actual_rejection_reason",
    "actual_selected",
    "actual_selection_rank",
    "actual_trade_ready",
    "regular_open_price",
    "last_price",
    "last_price_at",
    "last_outcome_offset_min",
    "last_outcome_price",
    "outcome_samples",
    "max_drawdown_pct",
    "max_runup_pct",
    "open_to_high_pct",
    "open_to_close_pct",
    "open_volume_confirmation",
}
ALLOWED_CANDIDATE_FIELDS = [
    "ticker",
    "name",
    "market",
    "session_date",
    "captured_at",
    "source",
    "provider",
    "provider_rank",
    "shadow_preopen_rank",
    "preopen_score",
    "preopen_grade",
    "screen_score",
    "source_overlap_count",
    "data_quality",
    "quality_tags",
    "risk_tags",
    "pattern_tags",
    "preopen_reason",
    "price",
    "extended_price",
    "extended_change_pct",
    "extended_volume",
    "extended_dollar_volume",
    "prior_day_traded_value",
    "change_rate",
    "gap_pct",
    "volume_ratio",
    "bid",
    "ask",
    "spread_pct",
    "quote_timestamp",
    "anchor_price",
    "anchor_price_source",
    "anchor_price_at",
    "news_or_earnings_flag",
    "news_or_earnings_count",
    "news_or_earnings_sample_title",
    "news_or_earnings_sources",
    "news_date_quality",
    "news_quality",
    "news_quality_tags",
    "news_prompt_eligible",
    "news_signal_type",
    "news_score",
    "news_prompt_summary",
    "risk_news_summary",
    "scored_news_count",
    "excluded_news_counts",
    "preopen_news_edge",
    "preopen_news_policy",
    "preopen_news_edge_reason",
    "preopen_pinned",
    "preopen_pin_tier",
    "preopen_pin_require_confirmation",
    "preopen_pin_reason",
    "preopen_pin_source",
    "preopen_pin_turnover",
]


class ReplayError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReplayCase:
    market: str
    session_date: str


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def _int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(str(value).replace(",", "")))
    except Exception:
        return None


def _safe_avg(values: Iterable[Any]) -> float | None:
    nums = [_num(value) for value in values]
    nums = [value for value in nums if value is not None]
    return round(mean(nums), 4) if nums else None


def _win_rate(values: Iterable[Any]) -> float | None:
    nums = [_num(value) for value in values]
    nums = [value for value in nums if value is not None]
    if not nums:
        return None
    return round(sum(1 for value in nums if value > 0) / len(nums) * 100.0, 2)


def _runtime_mode(mode: str) -> str:
    return "live" if str(mode or "").strip().lower() == "live" else "paper"


def _market_key(market: str) -> str:
    key = str(market or "").strip().upper()
    if key not in {"KR", "US"}:
        raise ReplayError(f"unsupported_market:{market}")
    return key


def _yyyymmdd(session_date: str) -> str:
    return str(session_date).replace("-", "")


def _log_path(kind: str, market: str, session_date: str, *, mode: str = "live") -> Path:
    suffix = "" if _runtime_mode(mode) == "live" else f"_{_runtime_mode(mode)}"
    return get_runtime_path(
        "logs",
        "preopen",
        f"{_yyyymmdd(session_date)}_{_market_key(market)}_{kind}{suffix}.jsonl",
        make_parents=False,
    )


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def sanitize_candidate(row: dict[str, Any]) -> dict[str, Any]:
    item = {
        key: row.get(key)
        for key in ALLOWED_CANDIDATE_FIELDS
        if key in row and row.get(key) not in (None, "", [])
    }
    leaks = [key for key in item if key in FUTURE_FIELDS or key.startswith(FUTURE_PREFIXES)]
    if leaks:
        raise ReplayError(f"future_field_leak:{row.get('ticker')}:{','.join(leaks)}")
    return item


def _dt_before_or_at_open(value: Any, open_dt: datetime) -> bool | None:
    parsed = _parse_dt(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=open_dt.tzinfo)
    return parsed.astimezone(open_dt.tzinfo) <= open_dt


def _news_payload_is_preopen_available(
    market: str,
    session_date: str,
    payload: dict[str, Any],
    source_path: str,
) -> bool:
    if not payload:
        return False
    open_dt = regular_open_dt(_market_key(market), session_date)
    for key in ("snapshot_written_at", "collected_at"):
        before_open = _dt_before_or_at_open(payload.get(key), open_dt)
        if before_open is not None:
            return bool(before_open)
    return bool(payload.get("preopen_snapshot")) or str(source_path or "").endswith("_preopen.json")


def _apply_preopen_news_overlay(
    market: str,
    session_date: str,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not candidates:
        return candidates
    news_root = get_runtime_path("data", "news", make_parents=False)
    payload, source_path = load_preopen_news_payload(_market_key(market), session_date, news_root=news_root)
    if not _news_payload_is_preopen_available(market, session_date, payload, source_path):
        return candidates
    enriched, summary = enrich_candidates_with_news(
        _market_key(market),
        candidates,
        session_date=session_date,
        news_payload=payload,
        news_path=source_path,
        allow_rank_reorder=False,
    )
    if summary.get("status") != "ok":
        return candidates
    return [sanitize_candidate(row) for row in enriched]


def load_candidate_snapshot(
    market: str,
    session_date: str,
    *,
    mode: str = "live",
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> tuple[str, list[dict[str, Any]]]:
    market_key = _market_key(market)
    path = _log_path("candidates", market_key, session_date, mode=mode)
    rows = load_jsonl(path)
    if not rows:
        raise ReplayError(f"candidate_log_missing_or_empty:{path}")
    open_dt = regular_open_dt(market_key, session_date)
    eligible: list[dict[str, Any]] = []
    for row in rows:
        captured = _parse_dt(row.get("captured_at"))
        if captured is not None and captured <= open_dt:
            eligible.append(row)
    if not eligible:
        raise ReplayError(f"no_candidate_snapshot_before_open:{path}")
    latest_at = max(str(row.get("captured_at") or "") for row in eligible)
    latest_rows = [row for row in eligible if str(row.get("captured_at") or "") == latest_at]
    by_ticker: dict[str, dict[str, Any]] = {}
    for row in latest_rows:
        ticker = str(row.get("ticker") or "").strip()
        if ticker:
            by_ticker[ticker] = sanitize_candidate(row)
    candidates = sorted(
        by_ticker.values(),
        key=lambda item: (
            _int(item.get("shadow_preopen_rank")) or 9999,
            _int(item.get("provider_rank")) or 9999,
            str(item.get("ticker") or ""),
        ),
    )
    candidates = _apply_preopen_news_overlay(market_key, session_date, candidates)
    return latest_at, candidates[: int(max_candidates)]


def _return_from_row(row: dict[str, Any], offset: int) -> Any:
    samples = row.get("outcome_samples") or []
    for sample in samples:
        if isinstance(sample, dict) and _int(sample.get("offset_min")) == int(offset):
            value = sample.get("return_pct")
            if value is not None:
                return value
    return row.get(f"post_open_{int(offset)}m_return_pct")


def _available_return_offsets(row: dict[str, Any]) -> list[int]:
    offsets: set[int] = set()
    for sample in row.get("outcome_samples") or []:
        if isinstance(sample, dict) and sample.get("return_pct") is not None:
            offset = _int(sample.get("offset_min"))
            if offset is not None:
                offsets.add(int(offset))
    for key, value in row.items():
        if value is None:
            continue
        match = re.match(r"post_open_(\d+)m_return_pct$", str(key))
        if match:
            offsets.add(int(match.group(1)))
    return sorted(offsets)


def _last_return_from_row(row: dict[str, Any]) -> tuple[int | None, Any]:
    offsets = _available_return_offsets(row)
    if not offsets:
        return None, None
    offset = max(offsets)
    return offset, _return_from_row(row, offset)


def load_outcomes(market: str, session_date: str, *, mode: str = "live") -> dict[str, dict[str, Any]]:
    path = _log_path("outcome", market, session_date, mode=mode)
    rows = load_jsonl(path)
    outcomes: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").strip()
        if not ticker:
            continue
        close_offset, close_return = _last_return_from_row(row)
        outcomes[ticker] = {
            "name": row.get("name"),
            "ret_5m": _return_from_row(row, 5),
            "ret_30m": _return_from_row(row, 30),
            "ret_60m": _return_from_row(row, 60),
            "ret_120m": _return_from_row(row, 120),
            "ret_close": close_return,
            "close_offset_min": close_offset,
            "mfe": row.get("post_open_mfe_pct"),
            "mae": row.get("post_open_mae_pct"),
        }
    if not outcomes:
        raise ReplayError(f"outcome_log_missing_or_empty:{path}")
    return outcomes


def decision_tool_schema() -> list[dict[str, Any]]:
    code_pattern = "^[A-Z0-9_]{2,80}$"
    return [
        {
            "name": "preopen_shadow_decision",
            "description": "Return preopen candidate replay decisions.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "promote": {
                        "type": "array",
                        "maxItems": DEFAULT_PROMOTE_LIMIT,
                        "items": {
                            "type": "object",
                            "properties": {
                                "ticker": {"type": "string"},
                                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                "edge_code": {"type": "string", "pattern": code_pattern},
                                "risk_code": {"type": "string", "pattern": code_pattern},
                            },
                            "required": ["ticker", "confidence", "edge_code", "risk_code"],
                            "additionalProperties": False,
                        },
                    },
                    "keep_watch": {
                        "type": "array",
                        "maxItems": DEFAULT_KEEP_WATCH_LIMIT,
                        "items": {
                            "type": "object",
                            "properties": {
                                "ticker": {"type": "string"},
                                "reason_code": {"type": "string", "pattern": code_pattern},
                            },
                            "required": ["ticker", "reason_code"],
                            "additionalProperties": False,
                        },
                    },
                    "reject_summary": {
                        "type": "array",
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "properties": {
                                "reason_code": {"type": "string", "pattern": code_pattern},
                                "count": {"type": "integer", "minimum": 0},
                            },
                            "required": ["reason_code", "count"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["promote", "keep_watch", "reject_summary"],
                "additionalProperties": False,
            },
        }
    ]


def build_prompt(
    *,
    market: str,
    session_date: str,
    snapshot_at: str,
    candidates: list[dict[str, Any]],
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> str:
    if prompt_version not in SUPPORTED_PROMPT_VERSIONS:
        raise ReplayError(f"unsupported_prompt_version:{prompt_version}")
    market_key = _market_key(market)
    payload = {
        "as_of": regular_open_dt(market, session_date).isoformat(timespec="seconds"),
        "candidate_snapshot_captured_at": snapshot_at,
        "market": market_key,
        "session_date": session_date,
        "input_rule": (
            "Only fields available before regular open are included. "
            "No post-open outcome, return, actual selection, or later price fields are included."
        ),
        "candidates": candidates,
    }
    if prompt_version == PROMPT_MARKET_BALANCED_V2:
        body = _market_balanced_rules(market_key)
    elif prompt_version == PROMPT_MARKET_GROWTH_TAPE_V3:
        body = _market_growth_tape_rules(market_key)
    elif prompt_version == PROMPT_US_LIQUID_QUALITY_V4:
        body = _us_liquid_quality_rules(market_key)
    elif prompt_version == PROMPT_US_EDGE_HUNTER_V5:
        body = _us_edge_hunter_rules(market_key)
    elif prompt_version == PROMPT_US_SLATE_ADAPTIVE_V6:
        body = _us_slate_adaptive_rules(market_key)
    else:
        body = _strict_loss_filter_rules(market_key)
    return (
        f"{market_key} preopen buy-candidate shadow replay at regular open. No orders.\n"
        "Use ONLY the candidate JSON. Do not assume post-open price action.\n"
        f"Prompt version: {prompt_version}.\n"
        + body
        + "\n"
        f"Limit PROMOTE to at most {DEFAULT_PROMOTE_LIMIT}. It is acceptable to promote fewer than {DEFAULT_PROMOTE_LIMIT}.\n"
        f"Limit KEEP_WATCH to at most {DEFAULT_KEEP_WATCH_LIMIT}. All other candidates are implicit DROP.\n"
        "Use the tool schema exactly. Use ASCII uppercase reason codes only.\n"
        "Candidate JSON:\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _strict_loss_filter_rules(market: str) -> str:
    return (
        "Your goal is NOT to fill a quota. Your goal is to avoid promoting likely losers.\n"
        "Default decision is DROP unless a candidate has a clear edge.\n\n"
        "PROMOTE only if one of these strict conditions is true:\n"
        "1. SPECIFIC_CATALYST: concrete company-specific catalyst in news_or_earnings_sample_title. "
        "Generic market schedule, broad theme, index/mega-cap noise, or vague sector mention is not enough. "
        "The catalyst must plausibly create same-day continuation.\n"
        "2. EXCEPTIONAL_PREOPEN_TAPE: no catalyst is allowed only if provider_rank <= 1 AND volume_ratio >= 0.10 "
        "AND screen_score is among the strongest candidates. This is an exception, not a general rule.\n\n"
        "VETO rules:\n"
        "- If reason is mainly NO_CATALYST + LOW_LIQUIDITY, do not PROMOTE.\n"
        "- If catalyst is negative governance, disclosure penalty, failed sale, investigation, or uncertainty, DROP.\n"
        "- If the only argument is provider_rank, screen_score, or popular theme name, KEEP_WATCH or DROP, not PROMOTE.\n"
        "- If preopen traded value is thin and there is no specific catalyst, DROP.\n"
        "- Do not promote generic mega-cap entries unless the catalyst is specific and strong.\n"
        "- Do not infer a theme from company name alone.\n"
        "- No-catalyst candidates must not be PROMOTE unless provider_rank <= 1 and volume_ratio >= 0.10.\n\n"
    )


def _market_balanced_rules(market: str) -> str:
    if market == "US":
        return (
            "Your goal is to select a small buy-candidate queue, not to require news on every ticker.\n"
            "PROMOTE candidates with a clear same-day edge from either catalyst quality or tape quality.\n\n"
            "US PROMOTE rules:\n"
            "1. EARNINGS_OR_GUIDANCE: earnings, guidance, analyst, FDA, contract, product, or company-specific catalyst with clean risk.\n"
            "2. GROWTH_THEME_TAPE: AI, semiconductor, data center, power, crypto, fintech, or software leaders are allowed without explicit news if rank/score/tape are strong.\n"
            "3. EXCEPTIONAL_TAPE: provider_rank <= 5 or shadow_preopen_rank <= 8 with strong screen_score and no obvious binary/governance risk.\n\n"
            "US VETO rules:\n"
            "- Drop thin/noisy microcaps, broken names, bankruptcy/liquidity stress, and binary biotech without clear positive catalyst.\n"
            "- Do not over-penalize mega-cap/growth leaders solely for generic news if tape/rank is strong.\n"
            "- If the edge is plausible but not enough for PROMOTE, use KEEP_WATCH for open confirmation.\n"
        )
    return (
        "Your goal is to select KR names that can plausibly continue after the open while avoiding thin one-off noise.\n"
        "PROMOTE can come from either a specific catalyst or exceptional opening tape. KEEP_WATCH is for names that need 5m confirmation.\n\n"
        "KR PROMOTE rules:\n"
        "1. SPECIFIC_CATALYST: company-specific news/disclosure that plausibly drives same-day continuation, unless it is negative governance or uncertainty.\n"
        "2. EXCEPTIONAL_TAPE: provider_rank <= 3 or shadow_preopen_rank <= 8 with high screen_score and usable preopen value/volume, even if news is absent.\n"
        "3. CATALYST_PLUS_TAPE: a thinner catalyst name may be PROMOTE if rank and screen_score are strong enough to justify a small watch queue slot.\n\n"
        "KR VETO rules:\n"
        "- Drop negative governance, failed sale, disclosure penalty, investigation, or stale/low-quality data.\n"
        "- Do not promote purely generic market schedule or broad theme noise.\n"
        "- If liquidity is thin but edge is interesting, KEEP_WATCH instead of DROP when 5m confirmation could matter.\n"
    )


def _market_growth_tape_rules(market: str) -> str:
    if market == "US":
        return (
            "Your goal is to find profitable continuation candidates, accepting that some will fail. Do not be empty unless the whole slate is poor.\n"
            "Select the best 3 to 5 PROMOTE candidates by expected intraday continuation.\n\n"
            "US PROMOTE rules:\n"
            "1. Strong premarket/open tape: high provider_rank or shadow_preopen_rank, high screen_score, and recognizable liquidity.\n"
            "2. Growth momentum: AI, semiconductors, data center, infrastructure, software, fintech, crypto, space, high-beta leaders, or earnings/revision catalysts.\n"
            "3. Large liquid growth leaders can be PROMOTE with strong rank/tape even if news is generic.\n\n"
            "US VETO rules:\n"
            "- Drop obvious distressed/bankruptcy/low-float pump names, weak tape, and binary clinical names without a positive catalyst.\n"
            "- Penalize huge downside risk, but do not require zero risk.\n"
            "- Use KEEP_WATCH for volatile names with good theme but weak tape.\n"
        )
    return (
        "Your goal is to find profitable KR continuation candidates, accepting that some will fail. Do not be empty unless the whole slate is poor.\n"
        "Select the best 3 to 5 PROMOTE candidates by expected intraday continuation.\n\n"
        "KR PROMOTE rules:\n"
        "1. Specific catalyst with same-day relevance, including AI, semiconductor, robotics, contract, supply, regulatory, or product catalyst.\n"
        "2. Exceptional tape: provider_rank <= 3, shadow_preopen_rank <= 10, or standout screen_score/volume_ratio relative to the slate.\n"
        "3. Theme plus tape can be enough when the name is near the top of the slate; do not require perfect liquidity.\n\n"
        "KR VETO rules:\n"
        "- Drop negative governance, failed sale, disclosure penalty, investigation, stale data, or purely generic schedule noise.\n"
        "- Use KEEP_WATCH for thin but interesting names instead of over-dropping them.\n"
        "- Avoid selecting only defensive mega-caps unless the catalyst/tape is clearly strong.\n"
    )


def _us_liquid_quality_rules(market: str) -> str:
    if market != "US":
        return _market_growth_tape_rules(market)
    return (
        "Your goal is to avoid chasing fragile no-news US day_gainers. Select only liquid, durable candidates.\n"
        "PROMOTE 0 to 5 names. It is acceptable to promote none on a weak slate.\n\n"
        "US PROMOTE rules:\n"
        "1. LIQUID_QUALITY_TAPE: quality_tags include most_actives, extended_dollar_volume is large, and the company is a liquid recognizable large/mid cap.\n"
        "2. SPECIFIC_POSITIVE_CATALYST: company-specific earnings, guidance, product, contract, FDA, analyst, or corporate catalyst with clean risk.\n"
        "3. CONTROLLED_PULLBACK: day_losers can be PROMOTE only if the name is liquid/recognizable and selloff looks like a controlled pullback, not distress.\n\n"
        "US VETO rules:\n"
        "- Do not PROMOTE no-news day_gainers with extended_change_pct >= 8 unless they are liquid leaders with an obvious durable theme.\n"
        "- Do not PROMOTE fragile high-beta names solely because rank is high: small biotech, quantum, space, low-float hardware, or distressed EV/solar.\n"
        "- Do not PROMOTE if the only edge is provider_rank/screen order.\n"
        "- Prefer KEEP_WATCH over PROMOTE for high-beta growth names needing 5m confirmation.\n"
        "- If the slate is broadly speculative, PROMOTE fewer names.\n"
    )


def _us_edge_hunter_rules(market: str) -> str:
    if market != "US":
        return _market_growth_tape_rules(market)
    return (
        "Do not copy KR rules. US profit candidates can come from momentum, liquid leadership, or oversold reversal.\n"
        "Your job is to select the best risk/reward candidates from this slate, not only the safest names.\n"
        "PROMOTE 3 to 5 names unless the slate is clearly untradable.\n\n"
        "Classify each PROMOTE into exactly one edge_code:\n"
        "- LIQUID_LEADER: liquid large/mid cap, most_actives, recognizable institutionally traded name, enough move to matter.\n"
        "- MOMENTUM_CONTINUATION: strong day_gainer or high-beta growth/AI/semiconductor/data-center/software/crypto/fintech/space leader with credible tape.\n"
        "- OVERSOLD_REVERSAL: day_loser or recent selloff candidate that is liquid enough and looks like a tradable reversal, not distress.\n"
        "- REAL_CATALYST: earnings, guidance, analyst, FDA, contract, product, partnership, or other company-specific positive catalyst.\n\n"
        "PROMOTE rules:\n"
        "- No-news is allowed if source/tape/liquidity/theme are strong enough.\n"
        "- Day_losers are allowed when they look like liquid oversold reversal candidates.\n"
        "- Most_actives are allowed when they are liquid leaders with enough movement to matter.\n"
        "- Day_gainers are allowed when they are not just thin overextended pumps.\n"
        "- Do not over-penalize volatility; many US winners are volatile.\n\n"
        "VETO rules:\n"
        "- Avoid tiny illiquid pumps, bankruptcy/distress, weak tape, and names where rank is the only edge.\n"
        "- Avoid binary biotech unless the catalyst is clearly positive and same-day relevant.\n"
        "- Avoid high-beta theme names with no liquidity, no catalyst, and no clear leader status.\n"
        "- If the edge is plausible but risk/reward is not strong enough, use KEEP_WATCH instead of PROMOTE.\n"
    )


def _us_slate_adaptive_rules(market: str) -> str:
    if market != "US":
        return _market_growth_tape_rules(market)
    return (
        "US preopen slates alternate between real momentum and speculative gap-fade traps. Adapt to the slate.\n"
        "PROMOTE 0 to 5 names. Your first job is to decide whether high-beta momentum is tradable today.\n\n"
        "Slate adaptation:\n"
        "- If the strongest names are mostly no-news day_gainers/high-beta growth with extended gaps, treat slate as GAP_FADE_RISK.\n"
        "- In GAP_FADE_RISK, do not PROMOTE high-beta no-catalyst growth. Put them in KEEP_WATCH for 5m confirmation.\n"
        "- In GAP_FADE_RISK, PROMOTE only liquid defensive/quality most_actives, controlled pullback leaders, or real catalysts.\n"
        "- If the slate has broad clean leadership, allow momentum/growth PROMOTE.\n\n"
        "PROMOTE edge_code must be one of:\n"
        "- QUALITY_MOST_ACTIVE: liquid recognizable large/mid cap most_actives with stable risk/reward.\n"
        "- CONTROLLED_REVERSAL: liquid day_loser/pullback candidate that is not distressed and can mean-revert.\n"
        "- REAL_CATALYST: specific positive earnings/guidance/analyst/product/FDA/contract catalyst.\n"
        "- CLEAN_MOMENTUM: high-beta/growth momentum only when not overextended and not just no-news hype.\n\n"
        "VETO rules:\n"
        "- Avoid no-news high-beta day_gainers when risk_code would be HIGH_BETA_NO_CATALYST or OVEREXTENDED_GAP.\n"
        "- Avoid small biotech, quantum, space, EV/solar, low-float hardware, or distressed names unless real catalyst is clear.\n"
        "- Avoid names where first 5 minutes would be required to prove they are not fading; those belong in KEEP_WATCH.\n"
        "- If uncertain between PROMOTE and KEEP_WATCH, choose KEEP_WATCH.\n"
    )


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_env() -> None:
    _load_env_file(ROOT / ".env.live")
    _load_env_file(ROOT / ".env")


def call_claude_tool(prompt: str, *, model: str, max_tokens: int = 1800) -> tuple[dict[str, Any], dict[str, int]]:
    import anthropic

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ReplayError("ANTHROPIC_API_KEY_missing")
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        tools=decision_tool_schema(),
        tool_choice={"type": "tool", "name": "preopen_shadow_decision"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", "") == "tool_use" and getattr(block, "name", "") == "preopen_shadow_decision":
            usage = getattr(resp, "usage", None)
            return dict(getattr(block, "input", {}) or {}), {
                "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            }
    raise ReplayError("claude_tool_response_missing")


def _decision_ticker(row: Any) -> str:
    if isinstance(row, dict):
        return str(row.get("ticker") or "").strip()
    if isinstance(row, list) and row:
        return str(row[0] or "").strip()
    return ""


def validate_decision(decision: dict[str, Any], candidate_tickers: set[str]) -> dict[str, Any]:
    clean = {
        "promote": [dict(row) for row in decision.get("promote") or [] if isinstance(row, dict)],
        "keep_watch": [dict(row) for row in decision.get("keep_watch") or [] if isinstance(row, dict)],
        "reject_summary": [dict(row) for row in decision.get("reject_summary") or [] if isinstance(row, dict)],
    }
    seen: set[str] = set()
    for bucket in ("promote", "keep_watch"):
        rows: list[dict[str, Any]] = []
        for row in clean[bucket]:
            ticker = _decision_ticker(row)
            if not ticker or ticker not in candidate_tickers or ticker in seen:
                continue
            seen.add(ticker)
            if bucket == "promote":
                confidence = max(0.0, min(1.0, float(_num(row.get("confidence")) or 0.0)))
                rows.append({
                    "ticker": ticker,
                    "confidence": round(confidence, 4),
                    "edge_code": str(row.get("edge_code") or "UNKNOWN")[:80],
                    "risk_code": str(row.get("risk_code") or "UNKNOWN")[:80],
                })
            else:
                rows.append({
                    "ticker": ticker,
                    "reason_code": str(row.get("reason_code") or "UNKNOWN")[:80],
                })
        clean[bucket] = rows
    clean["drop"] = [
        {"ticker": ticker, "reason_code": "IMPLICIT_DROP"}
        for ticker in sorted(candidate_tickers - seen)
    ]
    return clean


def _stats_for(tickers: list[str], outcomes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(tickers),
        "avg_5m": _safe_avg(outcomes.get(ticker, {}).get("ret_5m") for ticker in tickers),
        "avg_30m": _safe_avg(outcomes.get(ticker, {}).get("ret_30m") for ticker in tickers),
        "avg_60m": _safe_avg(outcomes.get(ticker, {}).get("ret_60m") for ticker in tickers),
        "avg_120m": _safe_avg(outcomes.get(ticker, {}).get("ret_120m") for ticker in tickers),
        "avg_close": _safe_avg(outcomes.get(ticker, {}).get("ret_close") for ticker in tickers),
        "win_close_pct": _win_rate(outcomes.get(ticker, {}).get("ret_close") for ticker in tickers),
        "avg_mfe": _safe_avg(outcomes.get(ticker, {}).get("mfe") for ticker in tickers),
        "avg_mae": _safe_avg(outcomes.get(ticker, {}).get("mae") for ticker in tickers),
    }


def evaluate_decision(
    *,
    candidates: list[dict[str, Any]],
    decision: dict[str, Any],
    outcomes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    candidate_tickers = [str(row.get("ticker") or "") for row in candidates if row.get("ticker")]
    promote = [_decision_ticker(row) for row in decision.get("promote") or []]
    keep = [_decision_ticker(row) for row in decision.get("keep_watch") or []]
    drop = [_decision_ticker(row) for row in decision.get("drop") or []]
    promote = [ticker for ticker in promote if ticker]
    keep = [ticker for ticker in keep if ticker]
    drop = [ticker for ticker in drop if ticker]
    return {
        "all_candidates": _stats_for(candidate_tickers, outcomes),
        "promote": _stats_for(promote, outcomes),
        "keep_watch": _stats_for(keep, outcomes),
        "drop": _stats_for(drop, outcomes),
        "promote_rows": [
            {
                "ticker": ticker,
                "name": outcomes.get(ticker, {}).get("name"),
                **outcomes.get(ticker, {}),
            }
            for ticker in promote
        ],
        "keep_watch_rows": [
            {
                "ticker": ticker,
                "name": outcomes.get(ticker, {}).get("name"),
                **outcomes.get(ticker, {}),
            }
            for ticker in keep
        ],
    }


def run_case(
    case: ReplayCase,
    *,
    mode: str = "live",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    model: str = DEFAULT_MODEL,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    dry_run: bool = False,
) -> dict[str, Any]:
    snapshot_at, candidates = load_candidate_snapshot(
        case.market,
        case.session_date,
        mode=mode,
        max_candidates=max_candidates,
    )
    outcomes = load_outcomes(case.market, case.session_date, mode=mode)
    prompt = build_prompt(
        market=case.market,
        session_date=case.session_date,
        snapshot_at=snapshot_at,
        candidates=candidates,
        prompt_version=prompt_version,
    )
    usage = {"input_tokens": 0, "output_tokens": 0}
    if dry_run:
        raw_decision = {"promote": [], "keep_watch": [], "reject_summary": []}
    else:
        raw_decision, usage = call_claude_tool(prompt, model=model)
    decision = validate_decision(raw_decision, {str(row["ticker"]) for row in candidates if row.get("ticker")})
    evaluation = evaluate_decision(candidates=candidates, decision=decision, outcomes=outcomes)
    return {
        "market": _market_key(case.market),
        "session_date": case.session_date,
        "mode": _runtime_mode(mode),
        "snapshot_at": snapshot_at,
        "prompt_version": prompt_version,
        "model": model,
        "candidate_count": len(candidates),
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "decision": decision,
        "evaluation": evaluation,
    }


def discover_recent_cases(markets: list[str], *, mode: str = "live", recent_per_market: int = 5) -> list[ReplayCase]:
    cases: list[ReplayCase] = []
    preopen_dir = get_runtime_path("logs", "preopen", make_parents=False)
    for market in markets:
        market_key = _market_key(market)
        candidate_dates: set[str] = set()
        outcome_dates: set[str] = set()
        for path in preopen_dir.glob(f"*_{market_key}_candidates*.jsonl"):
            match = re.match(r"(\d{8})_", path.name)
            if match and (_runtime_mode(mode) == "live" or f"_{_runtime_mode(mode)}." in path.name):
                candidate_dates.add(match.group(1))
        for path in preopen_dir.glob(f"*_{market_key}_outcome*.jsonl"):
            match = re.match(r"(\d{8})_", path.name)
            if match and (_runtime_mode(mode) == "live" or f"_{_runtime_mode(mode)}." in path.name):
                outcome_dates.add(match.group(1))
        complete = sorted(candidate_dates & outcome_dates, reverse=True)
        for raw_date in complete[: int(recent_per_market)]:
            cases.append(ReplayCase(market_key, f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"))
    return cases


def _aggregate(rows: list[dict[str, Any]], bucket: str) -> dict[str, Any]:
    if not rows:
        return {}
    counts = [int(row["evaluation"][bucket]["count"] or 0) for row in rows]
    return {
        "case_count": len(rows),
        "total_count": sum(counts),
        "avg_count_per_case": round(sum(counts) / len(counts), 2) if counts else None,
        "avg_5m": _safe_avg(row["evaluation"][bucket].get("avg_5m") for row in rows),
        "avg_30m": _safe_avg(row["evaluation"][bucket].get("avg_30m") for row in rows),
        "avg_60m": _safe_avg(row["evaluation"][bucket].get("avg_60m") for row in rows),
        "avg_120m": _safe_avg(row["evaluation"][bucket].get("avg_120m") for row in rows),
        "avg_close": _safe_avg(row["evaluation"][bucket].get("avg_close") for row in rows),
        "avg_win_close_pct": _safe_avg(row["evaluation"][bucket].get("win_close_pct") for row in rows),
        "avg_mfe": _safe_avg(row["evaluation"][bucket].get("avg_mfe") for row in rows),
        "avg_mae": _safe_avg(row["evaluation"][bucket].get("avg_mae") for row in rows),
    }


def aggregate_results(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    by_market: dict[str, dict[str, Any]] = {}
    for market in sorted({row["market"] for row in case_results}):
        rows = [row for row in case_results if row["market"] == market]
        by_market[market] = {
            "all_candidates": _aggregate(rows, "all_candidates"),
            "promote": _aggregate(rows, "promote"),
            "keep_watch": _aggregate(rows, "keep_watch"),
            "drop": _aggregate(rows, "drop"),
        }
    return {
        "case_count": len(case_results),
        "markets": by_market,
        "overall": {
            "all_candidates": _aggregate(case_results, "all_candidates"),
            "promote": _aggregate(case_results, "promote"),
            "keep_watch": _aggregate(case_results, "keep_watch"),
            "drop": _aggregate(case_results, "drop"),
        },
        "tokens": {
            "input": sum(int(row.get("input_tokens") or 0) for row in case_results),
            "output": sum(int(row.get("output_tokens") or 0) for row in case_results),
            "total": sum(int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0) for row in case_results),
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Preopen Candidate Replay Report",
        "",
        f"- run_id: `{payload.get('run_id')}`",
        f"- prompt_version: `{payload.get('prompt_version')}`",
        f"- model: `{payload.get('model')}`",
        f"- cases: {payload.get('aggregate', {}).get('case_count')}",
        f"- tokens: {payload.get('aggregate', {}).get('tokens')}",
        "",
    ]

    def add_stats(title: str, stats_by_bucket: dict[str, Any]) -> None:
        lines.extend([
            f"## {title}",
            "",
            "| bucket | cases | total | avg_count | 5m | 30m | 60m | 120m | close | win_close | mfe | mae |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for bucket in ("all_candidates", "promote", "keep_watch", "drop"):
            stats = stats_by_bucket.get(bucket) or {}
            lines.append(
                "| {bucket} | {case_count} | {total_count} | {avg_count_per_case} | {avg_5m} | {avg_30m} | {avg_60m} | {avg_120m} | {avg_close} | {avg_win_close_pct} | {avg_mfe} | {avg_mae} |".format(
                    bucket=bucket,
                    case_count=stats.get("case_count"),
                    total_count=stats.get("total_count"),
                    avg_count_per_case=stats.get("avg_count_per_case"),
                    avg_5m=stats.get("avg_5m"),
                    avg_30m=stats.get("avg_30m"),
                    avg_60m=stats.get("avg_60m"),
                    avg_120m=stats.get("avg_120m"),
                    avg_close=stats.get("avg_close"),
                    avg_win_close_pct=stats.get("avg_win_close_pct"),
                    avg_mfe=stats.get("avg_mfe"),
                    avg_mae=stats.get("avg_mae"),
                )
            )
        lines.append("")

    aggregate = payload.get("aggregate") or {}
    add_stats("Overall", aggregate.get("overall") or {})
    for market, stats in (aggregate.get("markets") or {}).items():
        add_stats(str(market), stats)

    lines.extend([
        "## Cases",
        "",
        "| market | date | snapshot | candidates | promote | promote_close | all_close | keep_close | drop_close |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    for row in payload.get("cases") or []:
        ev = row.get("evaluation") or {}
        lines.append(
            "| {market} | {session_date} | {snapshot_at} | {candidate_count} | {promote_count} | {promote_close} | {all_close} | {keep_close} | {drop_close} |".format(
                market=row.get("market"),
                session_date=row.get("session_date"),
                snapshot_at=row.get("snapshot_at"),
                candidate_count=row.get("candidate_count"),
                promote_count=ev.get("promote", {}).get("count"),
                promote_close=ev.get("promote", {}).get("avg_close"),
                all_close=ev.get("all_candidates", {}).get("avg_close"),
                keep_close=ev.get("keep_watch", {}).get("avg_close"),
                drop_close=ev.get("drop", {}).get("avg_close"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def write_outputs(payload: dict[str, Any], *, output_json: str | None = None, output_md: str | None = None) -> dict[str, str]:
    today = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths: dict[str, str] = {}
    if not output_json:
        output_json = str(get_runtime_path("docs", "reports", f"preopen_candidate_replay_{today}.json"))
    if not output_md:
        output_md = str(get_runtime_path("docs", "reports", f"preopen_candidate_replay_{today}.md"))
    json_path = Path(output_json)
    md_path = Path(output_md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    paths["json"] = str(json_path)
    paths["markdown"] = str(md_path)
    return paths


def parse_cases(args: argparse.Namespace) -> list[ReplayCase]:
    if args.cases:
        parsed: list[ReplayCase] = []
        for raw in args.cases.split(","):
            item = raw.strip()
            if not item:
                continue
            market, date_value = item.split(":", 1)
            parsed.append(ReplayCase(_market_key(market), date_value.strip()))
        return parsed
    markets = [_market_key(value.strip()) for value in args.markets.split(",") if value.strip()]
    return discover_recent_cases(markets, mode=args.mode, recent_per_market=args.recent_per_market)


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay preopen candidates through Claude shadow prompt and score outcomes")
    parser.add_argument("--mode", choices=["live", "paper"], default="live")
    parser.add_argument("--markets", default="KR,US")
    parser.add_argument("--cases", help="Comma-separated MARKET:YYYY-MM-DD cases")
    parser.add_argument("--recent-per-market", type=int, default=5)
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES)
    parser.add_argument("--prompt-version", default=DEFAULT_PROMPT_VERSION)
    parser.add_argument("--model", default=os.getenv("ANTHROPIC_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args()

    load_env()
    cases = parse_cases(args)
    if not cases:
        raise ReplayError("no_replay_cases")
    results = []
    for case in cases:
        result = run_case(
            case,
            mode=args.mode,
            prompt_version=args.prompt_version,
            model=args.model,
            max_candidates=args.max_candidates,
            dry_run=args.dry_run,
        )
        results.append(result)
        print(
            json.dumps(
                {
                    "market": result["market"],
                    "session_date": result["session_date"],
                    "candidate_count": result["candidate_count"],
                    "promote_count": result["evaluation"]["promote"]["count"],
                    "promote_close": result["evaluation"]["promote"]["avg_close"],
                    "all_close": result["evaluation"]["all_candidates"]["avg_close"],
                    "input_tokens": result["input_tokens"],
                    "output_tokens": result["output_tokens"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    payload = {
        "run_id": uuid.uuid4().hex[:12],
        "mode": _runtime_mode(args.mode),
        "prompt_version": args.prompt_version,
        "model": args.model,
        "cases": results,
        "aggregate": aggregate_results(results),
    }
    paths = write_outputs(payload, output_json=args.output_json, output_md=args.output_md)
    print(json.dumps({"outputs": paths, "aggregate": payload["aggregate"]}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
