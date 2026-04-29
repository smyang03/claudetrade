from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config.v2 import DEFAULT_V2_CONFIG
from lifecycle.models import PathType, make_path_run_id, utc_now_iso


VALID_PROMPT_STAGES = {
    "PRE_SESSION",
    "OPEN_CHECK",
    "NEAR_ENTRY",
    "POST_FILL",
    "INTRADAY_REVIEW",
    "PRE_CLOSE",
}


def _as_float(value: Any) -> float:
    if isinstance(value, str):
        value = value.replace(",", "").replace("$", "").replace("₩", "").strip()
    return float(value)


def _as_int(value: Any) -> int:
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    return int(float(value))


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()][:8]
    text = str(value).strip()
    return [text] if text else []


@dataclass(frozen=True)
class PricePlan:
    decision_id: str
    path_run_id: str
    ticker: str
    market: str
    session_date: str
    buy_zone_low: float
    buy_zone_high: float
    sell_target: float
    stop_loss: float
    hold_days: int
    confidence: float
    cancel_if_open_above: float | None = None
    entry_rationale: str = ""
    exit_rationale: str = ""
    rationale: str = ""
    entry_basis_tags: list[str] = field(default_factory=list)
    exit_basis_tags: list[str] = field(default_factory=list)
    invalidation_conditions: list[str] = field(default_factory=list)
    prompt_stage: str = "PRE_SESSION"
    prompt_version: str = "pathb_price_v1"
    created_at: str = field(default_factory=utc_now_iso)

    def validate(self, *, min_confidence: float | None = None, min_reward_risk: float = 1.2) -> list[str]:
        errors: list[str] = []
        if self.market not in {"KR", "US"}:
            errors.append("invalid_market")
        if not self.decision_id:
            errors.append("missing_decision_id")
        if not self.path_run_id:
            errors.append("missing_path_run_id")
        if not self.ticker:
            errors.append("missing_ticker")
        if self.buy_zone_low <= 0:
            errors.append("buy_zone_low_nonpositive")
        if self.buy_zone_high < self.buy_zone_low:
            errors.append("buy_zone_high_below_low")
        if self.sell_target <= self.buy_zone_high:
            errors.append("sell_target_not_above_buy_zone")
        if self.stop_loss >= self.buy_zone_low:
            errors.append("stop_loss_not_below_buy_zone")
        if self.hold_days < 1:
            errors.append("hold_days_below_one")
        if not (0.0 < self.confidence <= 1.0):
            errors.append("confidence_out_of_range")
        threshold = DEFAULT_V2_CONFIG.pathb_min_confidence if min_confidence is None else float(min_confidence)
        if self.confidence < threshold:
            errors.append("confidence_below_minimum")
        if self.prompt_stage not in VALID_PROMPT_STAGES:
            errors.append("invalid_prompt_stage")
        risk = self.buy_zone_low - self.stop_loss
        reward = self.sell_target - self.buy_zone_high
        if risk <= 0:
            errors.append("risk_nonpositive")
        elif reward / risk < min_reward_risk:
            errors.append("reward_risk_below_minimum")
        return errors

    @property
    def is_valid(self) -> bool:
        return not self.validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "path_run_id": self.path_run_id,
            "ticker": self.ticker,
            "market": self.market,
            "session_date": self.session_date,
            "buy_zone_low": self.buy_zone_low,
            "buy_zone_high": self.buy_zone_high,
            "sell_target": self.sell_target,
            "stop_loss": self.stop_loss,
            "hold_days": self.hold_days,
            "confidence": self.confidence,
            "cancel_if_open_above": self.cancel_if_open_above,
            "entry_rationale": self.entry_rationale,
            "exit_rationale": self.exit_rationale,
            "rationale": self.rationale,
            "entry_basis_tags": list(self.entry_basis_tags),
            "exit_basis_tags": list(self.exit_basis_tags),
            "invalidation_conditions": list(self.invalidation_conditions),
            "prompt_stage": self.prompt_stage,
            "prompt_version": self.prompt_version,
            "created_at": self.created_at,
        }


def make_price_plan(
    *,
    decision_id: str,
    ticker: str,
    market: str,
    session_date: str,
    buy_zone_low: float,
    buy_zone_high: float,
    sell_target: float,
    stop_loss: float,
    hold_days: int,
    confidence: float,
    cancel_if_open_above: float | None = None,
    entry_rationale: str = "",
    exit_rationale: str = "",
    rationale: str = "",
    entry_basis_tags: list[str] | None = None,
    exit_basis_tags: list[str] | None = None,
    invalidation_conditions: list[str] | None = None,
    prompt_stage: str = "PRE_SESSION",
    prompt_version: str = "pathb_price_v1",
) -> PricePlan:
    market_value = str(market or "").upper()
    ticker_value = str(ticker or "").strip().upper() if market_value == "US" else str(ticker or "").strip()
    return PricePlan(
        decision_id=str(decision_id or "").strip(),
        path_run_id=make_path_run_id(PathType.CLAUDE_PRICE, market_value, session_date, ticker_value),
        ticker=ticker_value,
        market=market_value,
        session_date=str(session_date),
        buy_zone_low=float(buy_zone_low),
        buy_zone_high=float(buy_zone_high),
        sell_target=float(sell_target),
        stop_loss=float(stop_loss),
        hold_days=int(hold_days),
        confidence=float(confidence),
        cancel_if_open_above=float(cancel_if_open_above) if cancel_if_open_above not in (None, "") else None,
        entry_rationale=str(entry_rationale or "")[:240],
        exit_rationale=str(exit_rationale or "")[:240],
        rationale=str(rationale or "")[:360],
        entry_basis_tags=list(entry_basis_tags or [])[:8],
        exit_basis_tags=list(exit_basis_tags or [])[:8],
        invalidation_conditions=list(invalidation_conditions or [])[:8],
        prompt_stage=str(prompt_stage or "PRE_SESSION"),
        prompt_version=str(prompt_version or "pathb_price_v1"),
    )


def parse_plan_from_claude(
    *,
    decision_id: str,
    ticker: str,
    market: str,
    session_date: str,
    raw: dict[str, Any],
    prompt_stage: str = "PRE_SESSION",
    prompt_version: str = "pathb_price_v1",
    min_confidence: float | None = None,
) -> tuple[PricePlan | None, list[str]]:
    if not isinstance(raw, dict):
        return None, ["raw_plan_not_object"]
    required = ("buy_zone_low", "buy_zone_high", "sell_target", "stop_loss", "hold_days", "confidence")
    missing = [key for key in required if key not in raw]
    if missing:
        return None, [f"missing_{key}" for key in missing]
    try:
        plan = make_price_plan(
            decision_id=decision_id,
            ticker=ticker,
            market=market,
            session_date=session_date,
            buy_zone_low=_as_float(raw.get("buy_zone_low")),
            buy_zone_high=_as_float(raw.get("buy_zone_high")),
            sell_target=_as_float(raw.get("sell_target")),
            stop_loss=_as_float(raw.get("stop_loss")),
            hold_days=_as_int(raw.get("hold_days")),
            confidence=_as_float(raw.get("confidence")),
            cancel_if_open_above=(
                _as_float(raw.get("cancel_if_open_above"))
                if raw.get("cancel_if_open_above") not in (None, "")
                else None
            ),
            entry_rationale=str(raw.get("entry_rationale", "") or ""),
            exit_rationale=str(raw.get("exit_rationale", "") or ""),
            rationale=str(raw.get("rationale", "") or ""),
            entry_basis_tags=_as_str_list(raw.get("entry_basis_tags")),
            exit_basis_tags=_as_str_list(raw.get("exit_basis_tags")),
            invalidation_conditions=_as_str_list(raw.get("invalidation_conditions")),
            prompt_stage=prompt_stage,
            prompt_version=prompt_version,
        )
    except Exception as exc:
        return None, [f"parse_error:{exc}"]
    errors = plan.validate(min_confidence=min_confidence)
    if errors:
        return None, errors
    return plan, []
