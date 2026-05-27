from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from typing import Any

from config.v2 import DEFAULT_V2_CONFIG, V2Config, SAFETY_REASON_CODES
from runtime.market_resolver import infer_ticker_market, normalize_market, resolve_position_market


@dataclass(frozen=True)
class SafetyDecision:
    passed: bool
    reason_code: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SafetyContext:
    market: str
    runtime_mode: str
    ticker: str
    price_krw: float
    qty: int
    order_cost_krw: float
    cash_krw: float
    min_order_krw: float | None = None
    positions: list[dict[str, Any]] = field(default_factory=list)
    pending_orders: list[dict[str, Any]] = field(default_factory=list)
    daily_entry_count: int = 0
    max_daily_entries: int | None = None
    daily_pnl_pct: float = 0.0
    daily_pnl_basis: str = "realized"
    realized_daily_pnl_pct: float | None = None
    equity_daily_pnl_pct: float | None = None
    broker_trust_level: str = "unknown"
    market_open: bool = True
    last_market_data_at: str | None = None
    now: datetime | None = None
    stopped_tickers: set[str] = field(default_factory=set)
    order_unknown_blocked: bool = False
    original_budget_krw: float | None = None
    effective_budget_krw: float | None = None
    early_gate_applied: bool = False
    early_gate_size_mult: float | None = None
    can_buy_1_share: bool | None = None
    fixed_sizing: bool = False
    sizing_reason: str = ""
    sizing_details: dict[str, Any] = field(default_factory=dict)


class SafetyGate:
    def __init__(self, config: V2Config = DEFAULT_V2_CONFIG):
        self.config = config

    def evaluate(self, ctx: SafetyContext) -> SafetyDecision:
        market = str(ctx.market or "").upper()
        ticker = _normalize_ticker(market, ctx.ticker)
        price_krw = float(ctx.price_krw or 0.0)
        qty = int(ctx.qty or 0)
        order_cost_krw = float(ctx.order_cost_krw or 0.0)
        cash_krw = float(ctx.cash_krw or 0.0)
        original_budget_krw = float(ctx.original_budget_krw or 0.0)
        effective_budget_krw = float(ctx.effective_budget_krw or 0.0)
        can_buy_1_share = ctx.can_buy_1_share
        if can_buy_1_share is None:
            can_buy_1_share = bool(price_krw > 0 and cash_krw >= price_krw)
            if original_budget_krw > 0:
                can_buy_1_share = bool(can_buy_1_share and price_krw <= original_budget_krw)
        details = {
            "market": market,
            "ticker": ticker,
            "price_krw": price_krw,
            "qty": qty,
            "order_cost_krw": order_cost_krw,
            "cash_krw": cash_krw,
            "daily_entry_count": int(ctx.daily_entry_count or 0),
            "max_daily_entries": int(ctx.max_daily_entries) if ctx.max_daily_entries is not None else None,
            "daily_pnl_pct": float(ctx.daily_pnl_pct or 0.0),
            "daily_pnl_basis": str(ctx.daily_pnl_basis or "realized"),
            "original_budget_krw": original_budget_krw,
            "effective_budget_krw": effective_budget_krw,
            "early_gate_applied": bool(ctx.early_gate_applied),
            "can_buy_1_share": bool(can_buy_1_share),
            "fixed_sizing": bool(ctx.fixed_sizing),
        }
        if ctx.early_gate_size_mult is not None:
            details["early_gate_size_mult"] = float(ctx.early_gate_size_mult)
        if str(ctx.sizing_reason or "").strip():
            details["sizing_reason"] = str(ctx.sizing_reason or "").strip()
        if isinstance(ctx.sizing_details, dict) and ctx.sizing_details:
            details.update(dict(ctx.sizing_details))
        if ctx.realized_daily_pnl_pct is not None:
            details["realized_daily_pnl_pct"] = float(ctx.realized_daily_pnl_pct)
        if ctx.equity_daily_pnl_pct is not None:
            details["equity_daily_pnl_pct"] = float(ctx.equity_daily_pnl_pct)

        if ctx.order_unknown_blocked:
            return _blocked("ORDER_UNKNOWN_UNRESOLVED", "unresolved ORDER_UNKNOWN blocks new entry", details)
        if not ctx.market_open:
            return _blocked("MARKET_CLOSED", "market is closed for new entry", details)
        broker_trust = str(ctx.broker_trust_level or "").lower()
        if (
            market == "US"
            and _env_bool("US_BROKER_SYNC_QUARANTINE_ENABLED", True)
            and broker_trust in {"degraded", "untrusted"}
        ):
            return _blocked(
                "BROKER_SYNC_QUARANTINE",
                "US broker sync quarantine blocks new entry",
                {
                    **details,
                    "broker_trust_level": broker_trust,
                    "policy_name": "us_broker_trust_quarantine",
                },
            )
        if broker_trust == "untrusted":
            return _blocked("BROKER_UNTRUSTED", "broker state is untrusted", details)
        if price_krw <= 0:
            return _blocked("INVALID_PRICE", "invalid price", details)
        if qty <= 0:
            if original_budget_krw > 0 and price_krw > original_budget_krw:
                return _blocked("HIGH_PRICE_BUDGET_BLOCK", "one-share price exceeds pre-gate budget", details)
            if bool(ctx.early_gate_applied):
                return _blocked("ORDER_SIZE_TOO_SMALL_GATE", "early soft gate reduced order below one share", details)
            return _blocked("INVALID_QTY", "invalid order quantity", details)
        if _is_stale(ctx.last_market_data_at, ctx.now, self.config.stale_market_data_minutes):
            return _blocked("STALE_MARKET_DATA", "market data is stale", details)
        if ticker in {_normalize_ticker(market, t) for t in ctx.stopped_tickers}:
            return _blocked("SAME_DAY_REENTRY_AFTER_STOP", "same-day re-entry after stop is blocked", details)
        if _has_position(market, ticker, ctx.positions):
            return _blocked("ALREADY_HOLDING", "ticker is already held", details)
        if _has_pending(market, ticker, ctx.pending_orders):
            return _blocked("PENDING_ORDER_EXISTS", "ticker already has pending order", details)
        max_positions = self.config.us_max_positions if market == "US" else self.config.kr_max_positions
        if _market_position_count(market, ctx.positions) >= max_positions:
            return _blocked("MAX_POSITIONS", "market max positions reached", {**details, "max_positions": max_positions})
        if ctx.max_daily_entries is not None and int(ctx.daily_entry_count or 0) >= int(ctx.max_daily_entries):
            return _blocked("MAX_DAILY_ENTRIES", "daily entry limit reached", details)
        if float(ctx.daily_pnl_pct or 0.0) <= float(self.config.daily_loss_limit_pct):
            return _blocked("DAILY_LOSS_LIMIT", "daily loss limit reached", details)
        min_order_krw = float(ctx.min_order_krw or 0.0)
        if min_order_krw <= 0 and market == "KR":
            min_order_krw = float(self.config.kr_min_order_krw)
        if min_order_krw > 0 and float(ctx.order_cost_krw or 0.0) < min_order_krw:
            return _blocked("MIN_ORDER_NOT_MET", "order cost is below minimum", {**details, "min_order_krw": min_order_krw})
        if float(ctx.order_cost_krw or 0.0) > float(ctx.cash_krw or 0.0):
            return _blocked("INSUFFICIENT_CASH", "cash is insufficient", details)
        return SafetyDecision(True, details=details)


class PathBSafetyGate:
    def __init__(self, config: V2Config = DEFAULT_V2_CONFIG, base_gate: SafetyGate | None = None):
        self.config = config
        self.base_gate = base_gate or SafetyGate(config)

    def evaluate(
        self,
        ctx: SafetyContext,
        *,
        plan: Any | None,
        patha_holding: bool = False,
        pathb_holding: bool = False,
        pathb_open_positions: int = 0,
        pathb_daily_count: int = 0,
        manually_disabled: bool = False,
        order_unknown_blocked: bool = False,
    ) -> SafetyDecision:
        details = {
            "market": str(ctx.market or "").upper(),
            "ticker": _normalize_ticker(str(ctx.market or "").upper(), ctx.ticker),
            "path_type": "claude_price",
            "pathb_daily_count": int(pathb_daily_count or 0),
            "pathb_open_positions": int(pathb_open_positions or 0),
        }
        mode = str(self.config.pathb_mode or "").strip().lower()
        if bool(self.config.pathb_emergency_disable):
            return _blocked("PATHB_EMERGENCY_DISABLED", "Path B emergency disable is active", details)
        if not bool(self.config.pathb_enabled):
            return _blocked("PATHB_MANUALLY_DISABLED", "Path B is manually disabled", details)
        if manually_disabled:
            return _blocked("PATHB_MANUALLY_DISABLED", "Path B is disabled by operator control", details)
        if mode in {"", "disabled", "off"}:
            return _blocked("PATHB_DISABLED", "Path B mode is disabled", details)
        if plan is None:
            return _blocked("CLAUDE_PRICE_INVALID", "Claude price plan is missing", details)
        try:
            plan_errors = plan.validate(min_confidence=self.config.pathb_min_confidence)
        except Exception as exc:
            plan_errors = [f"plan_validate_error:{exc}"]
        if plan_errors:
            return _blocked("CLAUDE_PRICE_INVALID", "Claude price plan is invalid", {**details, "errors": plan_errors})

        base_decision = self.base_gate.evaluate(
            SafetyContext(
                **{
                    **ctx.__dict__,
                    "order_unknown_blocked": bool(ctx.order_unknown_blocked or order_unknown_blocked),
                }
            )
        )
        if not base_decision.passed:
            return base_decision
        if (patha_holding or pathb_holding) and not bool(self.config.pathb_allow_same_ticker_with_patha):
            return _blocked("PATH_DUPLICATE_HOLDING", "same ticker live overlap is forbidden", details)
        if int(pathb_open_positions or 0) >= int(self.config.pathb_max_positions):
            return _blocked("PATHB_MAX_POSITIONS", "Path B max positions reached", details)
        if int(pathb_daily_count or 0) >= int(self.config.pathb_max_daily_entries):
            return _blocked("PATHB_MAX_DAILY_ENTRIES", "Path B daily entry limit reached", details)
        if float(getattr(plan, "confidence", 0.0) or 0.0) < float(self.config.pathb_min_confidence):
            return _blocked("PATHB_CONFIDENCE_TOO_LOW", "Path B confidence is below minimum", details)
        if bool(order_unknown_blocked) and bool(self.config.pathb_order_unknown_halts_entry):
            return _blocked("PATHB_ORDER_UNKNOWN_HALTED", "ORDER_UNKNOWN blocks Path B entry", details)
        return SafetyDecision(True, details=details)


def validate_reason_code(reason_code: str) -> bool:
    return str(reason_code or "").upper() in set(SAFETY_REASON_CODES)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _blocked(reason_code: str, message: str, details: dict[str, Any]) -> SafetyDecision:
    if not validate_reason_code(reason_code):
        raise ValueError(f"unsupported safety reason code: {reason_code}")
    return SafetyDecision(False, reason_code=reason_code, message=message, details=details)


def _normalize_ticker(market: str, ticker: str) -> str:
    raw = str(ticker or "").strip()
    return raw.upper() if str(market or "").upper() == "US" else raw


def _infer_market(ticker: str) -> str:
    return infer_ticker_market(ticker, unknown="KR")


def _market_position_count(market: str, positions: list[dict[str, Any]]) -> int:
    return sum(1 for pos in positions if resolve_position_market(pos, unknown="") == market)


def _has_position(market: str, ticker: str, positions: list[dict[str, Any]]) -> bool:
    return any(
        resolve_position_market(pos, unknown="") == market
        and _normalize_ticker(market, str(pos.get("ticker", ""))) == ticker
        for pos in positions
    )


def _has_pending(market: str, ticker: str, pending_orders: list[dict[str, Any]]) -> bool:
    for order in pending_orders:
        order_market = normalize_market(order.get("market")) or market
        if order_market == market and _normalize_ticker(market, str(order.get("ticker", ""))) == ticker:
            return True
    return False


def _is_stale(raw_ts: str | None, now: datetime | None, max_age_minutes: int) -> bool:
    if not raw_ts:
        return False
    try:
        parsed = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current - parsed.astimezone(current.tzinfo)).total_seconds() > max_age_minutes * 60
