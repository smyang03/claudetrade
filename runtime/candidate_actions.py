from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math
from typing import Any


CLAUDE_ACTIONS = {
    "WATCH",
    "PROBE_READY",
    "BUY_READY",
    "ADD_READY",
    "PULLBACK_WAIT",
    "AVOID",
}

DEFAULT_TTL_MINUTES = {
    "WATCH": 30,
    "PROBE_READY": 5,
    "BUY_READY": 3,
    "ADD_READY": 3,
    "PULLBACK_WAIT": 30,
    "AVOID": 30,
}


def candidate_action_prompt_contract(*, enabled: bool = True) -> str:
    if not enabled:
        return ""
    return """Candidate action contract schema:
- Keep the legacy watchlist/trade_ready fields exactly as requested.
- Also return candidate_actions for every watchlist ticker.
- Use schema_version="candidate_actions.v2" when the prompt includes evidence/action ceilings.
- Claude may only use these actions: WATCH, PROBE_READY, BUY_READY, ADD_READY, PULLBACK_WAIT, AVOID.
- Claude must not output HARD_BLOCK. Hard safety blocks are system-owned.
- PROBE_READY means small initial entry only; BUY_READY means normal entry; PULLBACK_WAIT requires price_targets.
- Every v2 candidate_action, including WATCH, must include action_ceiling_ack.
- WATCH/PULLBACK_WAIT/AVOID must include reason_code and blocking_factors when evidence blocks immediate execution.
- BUY_READY/PROBE_READY must include why_not_watch, freshness_verdict, setup_maturity, and action_ceiling_ack.
- If local evidence says action_ceiling=WATCH, Claude may override only with soft_gate_overrides backed by actual input evidence.
- If exec feas shows a soft/mutable blocker (macd_not_ready, gap_below_min, breakout_not_ready, orp_entry_window_expired, volume_low) AND price structure is bullish (positive returns, OR break, above VWAP), prefer PULLBACK_WAIT over WATCH. PULLBACK_WAIT creates a PathB waiting plan that enters when price pulls back to the buy zone.
- valid_until is required for v2 BUY_READY/PROBE_READY unless runtime TTL should be shorter.
- For PULLBACK_WAIT, price_targets must include buy_zone_low, buy_zone_high, sell_target, stop_loss, hold_days, confidence. buy_zone must be BELOW current price. stop_loss below buy_zone_low. sell_target above current price.
- ADD_READY is valid only for an already-held position.
- Include invalidation_condition for every non-WATCH action.
- valid_until is optional; runtime will cap it with a shorter TTL.
- If valid_until is uncertain, omit it; runtime ignores valid_until values that are earlier than created_at.

candidate_actions example:
[
  {
    "ticker":"code1",
    "schema_version":"candidate_actions.v2",
    "action":"PROBE_READY",
    "confidence":0.64,
    "entry_type":"confirmed_continuation",
    "freshness_verdict":"FRESH",
    "setup_maturity":"CONFIRMED",
    "why_not_watch":"fresh VWAP reclaim with positive 3m/5m returns",
    "action_ceiling_ack":"PROBE_READY",
    "soft_gate_overrides":[],
    "required_confirmations":[],
    "size_intent":"probe",
    "reason_code":"FRESH_CONTINUATION_PROBE",
    "reason":"early strength with risk control",
    "invalidation_condition":"breaks opening range low",
    "price_targets":{"buy_zone_low":100,"buy_zone_high":102,"sell_target":108,"stop_loss":97,"hold_days":1,"confidence":0.64},
    "valid_until":"2026-05-06T09:10:00"
  }
]"""


@dataclass
class CandidateAction:
    ticker: str
    market: str
    action: str
    confidence: float = 0.0
    size_intent: str = "none"
    strategy: str = ""
    reason: str = ""
    invalidation_condition: str = ""
    price_targets: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    expires_at: str = ""
    source_prompt_id: str = ""
    schema_version: str = "candidate_actions.v1"
    legacy_schema: bool = False
    reason_code: str = ""
    risk_tags: list[str] = field(default_factory=list)
    entry_type: str = ""
    freshness_verdict: str = ""
    setup_maturity: str = ""
    why_not_watch: str = ""
    action_ceiling_ack: str = ""
    blocking_factors: list[str] = field(default_factory=list)
    soft_gate_overrides: list[str] = field(default_factory=list)
    required_confirmations: list[str] = field(default_factory=list)
    max_entry_price: float = 0.0
    max_chase_pct: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "market": self.market,
            "action": self.action,
            "confidence": self.confidence,
            "size_intent": self.size_intent,
            "strategy": self.strategy,
            "reason": self.reason,
            "invalidation_condition": self.invalidation_condition,
            "price_targets": self.price_targets,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "source_prompt_id": self.source_prompt_id,
            "schema_version": self.schema_version,
            "legacy_schema": self.legacy_schema,
            "reason_code": self.reason_code,
            "risk_tags": list(self.risk_tags),
            "entry_type": self.entry_type,
            "freshness_verdict": self.freshness_verdict,
            "setup_maturity": self.setup_maturity,
            "why_not_watch": self.why_not_watch,
            "action_ceiling_ack": self.action_ceiling_ack,
            "blocking_factors": list(self.blocking_factors),
            "soft_gate_overrides": list(self.soft_gate_overrides),
            "required_confirmations": list(self.required_confirmations),
            "max_entry_price": self.max_entry_price,
            "max_chase_pct": self.max_chase_pct,
            "contract_warnings": list(self.warnings),
            "warnings": list(self.warnings),
        }


def _parse_dt(value: Any) -> datetime:
    text = str(value or "").strip()
    if text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.now()


def _expiry(created_at: datetime, action: str) -> str:
    ttl = DEFAULT_TTL_MINUTES.get(action, 30)
    return (created_at + timedelta(minutes=ttl)).isoformat(timespec="seconds")


def _min_expiry(created_at: datetime, action: str, raw_valid_until: Any = None) -> tuple[str, list[str]]:
    runtime_expiry = _parse_dt(_expiry(created_at, action))
    text = str(raw_valid_until or "").strip()
    if not text:
        return runtime_expiry.isoformat(timespec="seconds"), []
    try:
        raw_expiry = datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        if raw_expiry <= created_at:
            return runtime_expiry.isoformat(timespec="seconds"), ["raw_valid_until_before_created_ignored"]
        return min(runtime_expiry, raw_expiry).isoformat(timespec="seconds"), []
    except Exception:
        return runtime_expiry.isoformat(timespec="seconds"), ["raw_valid_until_parse_failed"]


def _price_target_for(meta: dict[str, Any], ticker: str, market: str) -> dict[str, Any]:
    price_targets = meta.get("price_targets") or {}
    if not isinstance(price_targets, dict):
        return {}
    keys = [str(ticker)]
    if str(market).upper() == "US":
        keys.append(str(ticker).upper())
    for key in keys:
        raw = price_targets.get(key)
        if isinstance(raw, dict):
            return dict(raw)
    if str(market).upper() == "US":
        for key, raw in price_targets.items():
            if str(key).upper() == str(ticker).upper() and isinstance(raw, dict):
                return dict(raw)
    return {}


def legacy_selection_to_candidate_actions(
    *,
    market: str,
    selection_meta: dict[str, Any],
    created_at: Any = None,
    source_prompt_id: str = "",
) -> list[dict[str, Any]]:
    meta = dict(selection_meta or {})
    watchlist = list(dict.fromkeys(meta.get("watchlist") or []))
    trade_ready = set(str(t) for t in (meta.get("trade_ready") or []))
    created = _parse_dt(created_at)
    created_text = created.isoformat(timespec="seconds")
    actions: list[dict[str, Any]] = []
    for ticker in watchlist:
        ticker_text = str(ticker)
        action = "BUY_READY" if ticker_text in trade_ready else "WATCH"
        size_intent = "normal" if action == "BUY_READY" else "none"
        reason = "legacy_trade_ready" if action == "BUY_READY" else "legacy_watchlist"
        item = CandidateAction(
            ticker=ticker_text,
            market=str(market).upper(),
            action=action,
            confidence=0.5 if action == "BUY_READY" else 0.0,
            size_intent=size_intent,
            reason=reason,
            invalidation_condition="runtime gate invalidates stale legacy action",
            price_targets=_price_target_for(meta, ticker_text, market),
            created_at=created_text,
            expires_at=_expiry(created, action),
            source_prompt_id=source_prompt_id,
            legacy_schema=True,
            warnings=["legacy_schema"],
        )
        actions.append(item.to_dict())
    return actions


def normalize_candidate_action(
    raw: dict[str, Any],
    *,
    market: str,
    created_at: Any = None,
    source_prompt_id: str = "",
) -> dict[str, Any]:
    created = _parse_dt(created_at)
    created_text = created.isoformat(timespec="seconds")
    ticker = str(raw.get("ticker") or raw.get("t") or "").strip()
    schema_version = str(raw.get("schema_version") or ("candidate_actions.v2" if "t" in raw or "a" in raw else "candidate_actions.v1")).strip() or "candidate_actions.v1"
    is_v2 = schema_version == "candidate_actions.v2"
    action = str(raw.get("action") or raw.get("a") or "WATCH").strip().upper()
    warnings: list[str] = []
    if action not in CLAUDE_ACTIONS:
        warnings.append(f"invalid_action:{action or 'EMPTY'}")
        action = "WATCH"
    _raw_conf = raw.get("confidence", raw.get("c"))
    try:
        confidence_raw = float(_raw_conf or 0.0)
        if not math.isfinite(confidence_raw):
            raise ValueError("non_finite_confidence")
        confidence = max(0.0, min(1.0, confidence_raw))
    except (TypeError, ValueError, OverflowError):
        confidence = 0.0
        warnings.append(f"invalid_confidence:{_raw_conf!r}")
    raw_price_targets = raw.get("price_targets") if isinstance(raw.get("price_targets"), dict) else raw.get("pt")
    if isinstance(raw_price_targets, dict) and any(key in raw_price_targets for key in ("ref", "lo", "hi", "tgt", "stp", "d", "cf")):
        price_targets = {}
        key_map = {
            "ref": "reference_price",
            "lo": "buy_zone_low",
            "hi": "buy_zone_high",
            "tgt": "sell_target",
            "stp": "stop_loss",
            "d": "hold_days",
            "cf": "confidence",
        }
        for short_key, full_key in key_map.items():
            if short_key in raw_price_targets:
                price_targets[full_key] = raw_price_targets.get(short_key)
    else:
        price_targets = raw_price_targets if isinstance(raw_price_targets, dict) else {}
    if action == "WATCH" and price_targets:
        warnings.append("watch_price_targets_ignored")
        price_targets = {}
    reason_code = str(raw.get("reason_code") or raw.get("rc") or "").strip()
    freshness = str(raw.get("freshness_verdict") or raw.get("fr") or "").strip()
    maturity = str(raw.get("setup_maturity") or raw.get("mat") or "").strip()
    why_not_watch = str(raw.get("why_not_watch") or "").strip()
    if is_v2 and action in {"BUY_READY", "PROBE_READY"} and not why_not_watch:
        why_not_watch = ":".join(part for part in (reason_code, freshness, maturity) if part)
    action_ceiling_ack = str(raw.get("action_ceiling_ack") or raw.get("action_ceiling") or raw.get("ceil") or "").strip().upper()
    if is_v2 and not action_ceiling_ack:
        action_ceiling_ack = action
        warnings.append("v2_missing_action_ceiling_ack_defaulted")
    if is_v2 and action in {"BUY_READY", "PROBE_READY"}:
        if not why_not_watch:
            warnings.append("v2_missing_why_not_watch_demoted")
            action = "WATCH"
            price_targets = {}
    expires_at, expiry_warnings = _min_expiry(created, action, raw.get("valid_until") or raw.get("expires_at"))
    warnings.extend(expiry_warnings)

    def _list_str(value: Any, limit: int = 8) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value[:limit]]
        if value in (None, ""):
            return []
        return [str(value)]

    def _float_nonneg(value: Any) -> float:
        try:
            parsed = float(value or 0.0)
            if not math.isfinite(parsed):
                return 0.0
            return max(0.0, parsed)
        except Exception:
            return 0.0

    item = CandidateAction(
        ticker=ticker,
        market=str(market or raw.get("market") or "").upper(),
        action=action,
        confidence=confidence,
        size_intent=str(raw.get("size_intent") or ("normal" if action == "BUY_READY" else ("probe" if action == "PROBE_READY" else "none"))),
        strategy=str(raw.get("strategy") or raw.get("s") or ""),
        reason=str(raw.get("reason") or reason_code or ""),
        invalidation_condition=str(raw.get("invalidation_condition") or raw.get("inv") or ""),
        price_targets=dict(price_targets),
        created_at=created_text,
        expires_at=expires_at,
        source_prompt_id=source_prompt_id,
        schema_version=schema_version,
        legacy_schema=False,
        reason_code=reason_code,
        risk_tags=_list_str(raw.get("risk_tags"), 8),
        entry_type=str(raw.get("entry_type") or raw.get("s") or ""),
        freshness_verdict=freshness,
        setup_maturity=maturity,
        why_not_watch=why_not_watch,
        action_ceiling_ack=action_ceiling_ack,
        blocking_factors=_list_str(raw.get("blocking_factors", raw.get("blk")), 8),
        soft_gate_overrides=_list_str(raw.get("soft_gate_overrides"), 8),
        required_confirmations=_list_str(raw.get("required_confirmations"), 8),
        max_entry_price=_float_nonneg(raw.get("max_entry_price")),
        max_chase_pct=_float_nonneg(raw.get("max_chase_pct")),
        warnings=warnings,
    )
    return item.to_dict()


def candidate_actions_from_response(
    response: dict[str, Any],
    *,
    market: str,
    created_at: Any = None,
    source_prompt_id: str = "",
) -> list[dict[str, Any]]:
    raw_actions = (response or {}).get("candidate_actions")
    if isinstance(raw_actions, list):
        return [
            normalize_candidate_action(dict(item or {}), market=market, created_at=created_at, source_prompt_id=source_prompt_id)
            for item in raw_actions
            if isinstance(item, dict)
        ]
    return legacy_selection_to_candidate_actions(
        market=market,
        selection_meta=response or {},
        created_at=created_at,
        source_prompt_id=source_prompt_id,
    )


def action_counts(candidate_actions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in candidate_actions or []:
        action = str((item or {}).get("action") or "UNKNOWN")
        counts[action] = counts.get(action, 0) + 1
    return counts
