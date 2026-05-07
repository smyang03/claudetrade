from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
    return """Candidate action shadow schema:
- Keep the legacy watchlist/trade_ready fields exactly as requested.
- Also return candidate_actions for every watchlist ticker.
- Claude may only use these actions: WATCH, PROBE_READY, BUY_READY, ADD_READY, PULLBACK_WAIT, AVOID.
- Claude must not output HARD_BLOCK. Hard safety blocks are system-owned.
- PROBE_READY means small initial entry only; BUY_READY means normal entry; PULLBACK_WAIT requires price_targets.
- For PULLBACK_WAIT, price_targets must include buy_zone_low, buy_zone_high, sell_target, stop_loss, hold_days, confidence.
- ADD_READY is valid only for an already-held position.
- Include invalidation_condition for every non-WATCH action.
- valid_until is optional; runtime will cap it with a shorter TTL.
- If valid_until is uncertain, omit it; runtime ignores valid_until values that are earlier than created_at.

candidate_actions example:
[
  {
    "ticker":"code1",
    "action":"PROBE_READY",
    "confidence":0.64,
    "size_intent":"probe",
    "reason":"early strength but extended spread",
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
    reason: str = ""
    invalidation_condition: str = ""
    price_targets: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    expires_at: str = ""
    source_prompt_id: str = ""
    schema_version: str = "candidate_actions.v1"
    legacy_schema: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "market": self.market,
            "action": self.action,
            "confidence": self.confidence,
            "size_intent": self.size_intent,
            "reason": self.reason,
            "invalidation_condition": self.invalidation_condition,
            "price_targets": self.price_targets,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "source_prompt_id": self.source_prompt_id,
            "schema_version": self.schema_version,
            "legacy_schema": self.legacy_schema,
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
    ticker = str(raw.get("ticker") or "").strip()
    action = str(raw.get("action") or "WATCH").strip().upper()
    warnings: list[str] = []
    if action not in CLAUDE_ACTIONS:
        warnings.append(f"invalid_action:{action or 'EMPTY'}")
        action = "WATCH"
    _raw_conf = raw.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(_raw_conf or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
        warnings.append(f"invalid_confidence:{_raw_conf!r}")
    price_targets = raw.get("price_targets") if isinstance(raw.get("price_targets"), dict) else {}
    expires_at, expiry_warnings = _min_expiry(created, action, raw.get("valid_until") or raw.get("expires_at"))
    warnings.extend(expiry_warnings)
    item = CandidateAction(
        ticker=ticker,
        market=str(market or raw.get("market") or "").upper(),
        action=action,
        confidence=confidence,
        size_intent=str(raw.get("size_intent") or ("normal" if action == "BUY_READY" else "none")),
        reason=str(raw.get("reason") or ""),
        invalidation_condition=str(raw.get("invalidation_condition") or ""),
        price_targets=dict(price_targets),
        created_at=created_text,
        expires_at=expires_at,
        source_prompt_id=source_prompt_id,
        legacy_schema=False,
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
