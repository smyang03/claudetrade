from __future__ import annotations

from typing import Any
import os
import time

from claude_memory import brain as BrainDB
from config.v2 import DEFAULT_V2_CONFIG
from decision.registry import DecisionRegistry
from execution.order_state import OrderUnknownEscalator, PartialFillPolicy
from execution.path_arbiter import ArbiterDecision, PathExecutionArbiter, SameDayReentryGuard
from execution.safety_gate import SafetyContext, SafetyGate
from execution.sizing import FixedSizer
from learning.brain_snapshot import BrainSnapshotStore
from logger import get_trading_logger
from runtime.rate_limiter import V2RateLimiter
from runtime.risk_profile import build_risk_profile
from runtime_paths import get_runtime_path


log = get_trading_logger()


def v2_close_reason(reason: str) -> str:
    mapping = {
        "loss_cap": "CLOSED_LOSS_CAP",
        "profit_floor": "CLOSED_PROFIT_FLOOR",
        "soft_exit_floor_price": "CLOSED_SOFT_EXIT_FLOOR",
        "CLOSED_LOSS_CAP": "CLOSED_LOSS_CAP",
        "CLOSED_PROFIT_FLOOR": "CLOSED_PROFIT_FLOOR",
        "CLOSED_SOFT_EXIT_FLOOR": "CLOSED_SOFT_EXIT_FLOOR",
        "stop_loss": "CLOSED_HARD_STOP",
        "trail_stop": "CLOSED_TRAILING_STOP",
        "max_hold": "CLOSED_TIME_STOP",
        "max_hold_final": "CLOSED_TIME_STOP",
        "recovery_micro_time_stop": "CLOSED_TIME_STOP",
        "tp_analyst_sell": "CLOSED_CLAUDE_SELL",
        "tuner_reverse": "CLOSED_CLAUDE_SELL",
        "manual": "CLOSED_USER_MANUAL",
        "panic": "CLOSED_PANIC",
        "session_force": "CLOSED_SESSION_FORCE",
        "broker_sync": "CLOSED_BROKER_SYNC",
        "claude_price_target": "CLOSED_CLAUDE_PRICE_TARGET",
        "claude_sell_target": "CLOSED_CLAUDE_PRICE_TARGET",
        "claude_price_stop": "CLOSED_CLAUDE_PRICE_STOP",
        "claude_stop_loss": "CLOSED_CLAUDE_PRICE_STOP",
        "mfe_breakeven": "CLOSED_MFE_BREAKEVEN",
        "CLOSED_MFE_BREAKEVEN": "CLOSED_MFE_BREAKEVEN",
        "pre_close": "CLOSED_CLAUDE_PRICE_PRE_CLOSE",
        "pathb_kill": "CLOSED_PANIC",
        "pathb_closeall": "CLOSED_USER_MANUAL",
    }
    return mapping.get(str(reason or ""), "CLOSED_USER_MANUAL")


def _fresh_brain_policy_enabled() -> bool:
    policy = str(os.getenv("V2_BRAIN_POLICY", "") or "").strip().lower()
    if policy in {"fresh", "fresh_v2", "fresh_v2_reference_v1"}:
        return True
    return str(os.getenv("V2_FRESH_BRAIN_START", "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
        "on",
    )


class V2LifecycleRuntime:
    def __init__(self, bot: Any, *, is_paper: bool):
        self.bot = bot
        self.enabled = str(os.getenv("V2_LIFECYCLE_ENABLED", "true")).strip().lower() in (
            "1",
            "true",
            "yes",
            "y",
            "on",
        )
        self.registry = DecisionRegistry() if self.enabled else None
        self.brain_snapshot_store = BrainSnapshotStore() if self.enabled else None
        self.decision_ids: dict[str, dict[str, str]] = {"KR": {}, "US": {}}
        self.brain_snapshot_ids: dict[str, str] = {"KR": "", "US": ""}
        self.fixed_sizing_enabled = (
            self.enabled
            and str(os.getenv("V2_FIXED_SIZING_ENABLED", "true")).strip().lower()
            in ("1", "true", "yes", "y", "on")
        )
        self.fixed_sizer = FixedSizer(DEFAULT_V2_CONFIG) if self.enabled else None
        self.safety_gate = SafetyGate(DEFAULT_V2_CONFIG) if self.enabled else None
        self.partial_fill_policy = PartialFillPolicy(DEFAULT_V2_CONFIG) if self.enabled else None
        self.order_unknown = (
            OrderUnknownEscalator(
                get_runtime_path("state", f"{'paper' if is_paper else 'live'}_v2_order_unknown.json")
            )
            if self.enabled
            else None
        )
        self.order_rate_limiter = (
            V2RateLimiter(
                max_calls=int(os.getenv("V2_ORDER_RATE_LIMIT_COUNT", "10")),
                per_seconds=float(os.getenv("V2_ORDER_RATE_LIMIT_SECONDS", "60")),
            )
            if self.enabled
            else None
        )
        self.path_arbiter = (
            PathExecutionArbiter(self.registry.store, DEFAULT_V2_CONFIG)
            if self.enabled and self.registry is not None
            else None
        )
        self.reentry_guard = (
            SameDayReentryGuard(self.registry.store, DEFAULT_V2_CONFIG)
            if self.enabled and self.registry is not None
            else None
        )
        self.risk_profiles = {}
        if self.enabled:
            runtime_mode = "paper" if is_paper else "live"
            usd_krw = float(os.getenv("USD_KRW_RATE", "1400") or 1400)
            self.risk_profiles = {
                "KR": build_risk_profile("KR", runtime_mode, usd_krw=usd_krw),
                "US": build_risk_profile("US", runtime_mode, usd_krw=usd_krw),
            }
        self.same_day_stop_tickers: dict[str, set[str]] = {"KR": set(), "US": set()}
        self.install_legacy_attrs()

    def install_legacy_attrs(self) -> None:
        bot = self.bot
        bot.v2_lifecycle_enabled = self.enabled
        bot.v2_registry = self.registry
        bot.v2_brain_snapshot_store = self.brain_snapshot_store
        bot.v2_decision_ids = self.decision_ids
        bot.v2_brain_snapshot_ids = self.brain_snapshot_ids
        bot.v2_fixed_sizing_enabled = self.fixed_sizing_enabled
        bot.v2_fixed_sizer = self.fixed_sizer
        bot.v2_safety_gate = self.safety_gate
        bot.v2_partial_fill_policy = self.partial_fill_policy
        bot.v2_order_unknown = self.order_unknown
        bot.v2_order_rate_limiter = self.order_rate_limiter
        bot.v2_path_arbiter = self.path_arbiter
        bot.v2_reentry_guard = self.reentry_guard
        bot.v2_risk_profiles = self.risk_profiles
        bot._v2_same_day_stop_tickers = self.same_day_stop_tickers

    def prompt_version(self) -> str:
        return str(getattr(DEFAULT_V2_CONFIG, "prompt_version", "v2") or "v2")

    def brain_snapshot_id(self, market: str) -> str:
        existing = str(self.brain_snapshot_ids.get(market) or "").strip()
        if existing:
            return existing
        session_date = self.bot._current_session_date_str(market).replace("-", "")
        snapshot_id = f"brain_{self.bot._mode}_{market}_{session_date}_pending"
        if self.brain_snapshot_store is not None:
            try:
                fresh_brain = _fresh_brain_policy_enabled()
                if fresh_brain:
                    patterns = []
                else:
                    brain_data = BrainDB.load()
                    patterns = {
                        "execution_patterns": brain_data.get("execution_patterns", {}),
                        "strategy_patterns": brain_data.get("strategy_patterns", {}),
                        "correction_guide": brain_data.get("correction_guide", {}).get(market, {}),
                    }
                snapshot = self.brain_snapshot_store.create_snapshot(
                    prompt_version=self.prompt_version(),
                    market=market,
                    session_date=self.bot._current_session_date_str(market),
                    runtime_mode=self.bot._mode,
                    patterns=patterns,
                    metadata={
                        "source": "trading_bot_v2_judge",
                        "brain_policy": "fresh_v2_reference_v1" if fresh_brain else "legacy_v1_prompt_context",
                        "legacy_brain_excluded": fresh_brain,
                    },
                )
                snapshot_id = snapshot.brain_snapshot_id
            except Exception as exc:
                log.warning(f"[V2 brain snapshot] create failed {market}: {exc}")
        self.brain_snapshot_ids[market] = snapshot_id
        return snapshot_id

    def register_trade_ready(self, market: str, meta: dict) -> dict[str, str]:
        if not self.enabled or self.registry is None:
            return {}
        ready = list(dict.fromkeys(meta.get("trade_ready") or []))
        if not ready:
            return {}
        try:
            ids = self.registry.register_trade_ready_batch(
                market=market,
                runtime_mode=self.bot._mode,
                session_date=self.bot._current_session_date_str(market),
                tickers=ready,
                prompt_version=self.prompt_version(),
                brain_snapshot_id=self.brain_snapshot_id(market),
                selection_meta=meta,
                reuse_existing=True,
            )
            self.decision_ids.setdefault(market, {}).update(ids)
            return ids
        except Exception as exc:
            log.warning(f"[V2 lifecycle] trade_ready decision_id register failed {market}: {exc}")
            return {}

    def decision_id_for_ticker(self, market: str, ticker: str) -> str:
        lookup = str(ticker or "").strip().upper() if market == "US" else str(ticker or "").strip()
        market_ids = self.decision_ids.get(market) or {}
        if lookup in market_ids:
            return str(market_ids[lookup])
        if market == "US":
            for raw_key, value in market_ids.items():
                if str(raw_key).upper() == lookup:
                    return str(value)
        meta = getattr(self.bot, "selection_meta", {}).get(market, {}) if hasattr(self.bot, "selection_meta") else {}
        meta_ids = (meta or {}).get("v2_decision_ids") or {}
        if lookup in meta_ids:
            return str(meta_ids[lookup])
        return ""

    def record_event(
        self,
        event_type: str,
        market: str,
        ticker: str,
        *,
        decision_id: str = "",
        execution_id: str = "",
        position_id: str = "",
        reason_code: str = "",
        payload: dict | None = None,
    ) -> None:
        if not self.enabled or self.registry is None:
            return
        resolved_decision_id = decision_id or self.decision_id_for_ticker(market, ticker)
        if not resolved_decision_id:
            return
        try:
            enriched_payload = self._enrich_route_attribution(
                event_type,
                market,
                ticker,
                decision_id=resolved_decision_id,
                execution_id=execution_id,
                position_id=position_id,
                payload=payload or {},
            )
            self.registry.record_event(
                event_type=event_type,
                market=market,
                runtime_mode=self.bot._mode,
                session_date=self.bot._current_session_date_str(market),
                ticker=ticker,
                decision_id=resolved_decision_id,
                prompt_version=self.prompt_version(),
                brain_snapshot_id=self.brain_snapshot_id(market),
                execution_id=execution_id or None,
                position_id=position_id or None,
                reason_code=reason_code or None,
                payload=enriched_payload,
            )
        except Exception as exc:
            log.warning(f"[V2 lifecycle] append failed {event_type} {market} {ticker}: {exc}")

    def _selection_snapshot_ts(self, market: str) -> str:
        try:
            meta = getattr(self.bot, "selection_meta", {}).get(market, {}) or {}
        except Exception:
            meta = {}
        for key in ("selection_snapshot_ts", "_selection_snapshot_ts", "selected_at", "created_at", "updated_at"):
            value = str((meta or {}).get(key) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _route_from_payload(payload: dict[str, Any]) -> str:
        route = str((payload or {}).get("entry_route", "") or "").strip()
        if route:
            return route
        if str((payload or {}).get("path_type", "") or "") == "claude_price":
            return "path_b"
        if str((payload or {}).get("path_run_id", "") or (payload or {}).get("pathb_path_run_id", "") or "").strip():
            return "path_b"
        if (payload or {}).get("path_a_lifecycle_evidence"):
            return "recovered_unknown"
        return ""

    def _enrich_route_attribution(
        self,
        event_type: str,
        market: str,
        ticker: str,
        *,
        decision_id: str,
        execution_id: str = "",
        position_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = dict(payload or {})
        event = str(event_type or "").upper()
        route = self._route_from_payload(data)
        path_run_id = str(data.get("path_run_id") or data.get("pathb_path_run_id") or "").strip()

        if not route and self.registry is not None:
            try:
                carried = self.registry.store.latest_event_attribution(
                    market=market,
                    runtime_mode=self.bot._mode,
                    session_date=self.bot._current_session_date_str(market),
                    ticker=ticker,
                    execution_id=str(execution_id or data.get("order_no", "") or ""),
                    position_id=str(position_id or ""),
                    decision_id=str(decision_id or ""),
                )
            except Exception:
                carried = {}
            if carried:
                route = str(carried.get("entry_route") or "")
                path_run_id = path_run_id or str(carried.get("path_run_id") or "")
                for key in (
                    "parent_decision_id",
                    "selection_snapshot_ts",
                    "strategy_used",
                    "route_source",
                    "attribution_source_event_id",
                    "attribution_source_event_type",
                ):
                    if carried.get(key) and not data.get(key):
                        data[key] = carried.get(key)

        if not route and event == "ORDER_SENT":
            route = "path_b" if path_run_id or str(data.get("path_type", "") or "") == "claude_price" else "plan_a"
        if not route and data.get("path_a_lifecycle_evidence"):
            route = "recovered_unknown"
        if not route:
            route = "unknown"

        data.setdefault("entry_route", route)
        if path_run_id:
            data.setdefault("path_run_id", path_run_id)
        data.setdefault("parent_decision_id", str(data.get("parent_decision_id") or decision_id or ""))
        data.setdefault("selection_snapshot_ts", self._selection_snapshot_ts(market))
        if route == "path_b":
            data.setdefault("strategy_used", "claude_price")
            data.setdefault("route_source", "buy_zone_hit")
        else:
            data.setdefault("strategy_used", str(data.get("strategy") or data.get("source_strategy") or ""))
            if route == "plan_a":
                data.setdefault("route_source", "signal_entry")
        return data

    def fixed_size_entry(self, market: str, risk_price_krw: float):
        if not self.fixed_sizing_enabled or self.fixed_sizer is None:
            return None
        try:
            return self.fixed_sizer.size(
                market=market,
                price_krw=float(risk_price_krw or 0),
                usd_krw=float(getattr(self.bot, "usd_krw_rate", 0) or 0),
                cash_krw=float(getattr(self.bot.risk, "cash", 0) or 0),
            )
        except Exception as exc:
            log.warning(f"[V2 sizing] fixed sizing failed {market}: {exc}")
            return None

    def daily_entry_count(self, market: str) -> int:
        count = 0
        for trade in getattr(getattr(self.bot, "risk", None), "trade_log", []) or []:
            if trade.get("side") != "buy":
                continue
            ticker = str(trade.get("ticker", "") or "")
            if ticker and self.bot._ticker_market(ticker) == market:
                count += 1
        for order in getattr(self.bot, "pending_orders", []) or []:
            ticker = str(order.get("ticker", "") or "")
            if ticker and str(order.get("market", market) or market) == market:
                count += 1
        return count

    def max_daily_entries(self, market: str | None = None) -> int | None:
        market_key = str(market or "").strip().upper()
        market_env = ""
        if market_key in {"KR", "US"}:
            market_env = str(os.getenv(f"{market_key}_DAILY_ENTRY_CAP", "") or "").strip()
        default_cap = "2" if market_key in {"KR", "US"} else "0"
        raw = market_env or str(os.getenv("V2_MAX_DAILY_ENTRIES", os.getenv("MAX_DAILY_ENTRIES", default_cap)) or default_cap).strip()
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if value > 0 else None

    def order_unknown_blocked(self, market: str, ticker: str) -> bool:
        if not self.enabled or self.order_unknown is None:
            return False
        try:
            return bool(self.order_unknown.block_state(market=market, ticker=ticker).get("blocked"))
        except Exception:
            return False

    def order_unknown_block_state(self, market: str, ticker: str) -> dict:
        if not self.enabled or self.order_unknown is None:
            return {"blocked": False}
        try:
            return dict(self.order_unknown.block_state(market=market, ticker=ticker) or {"blocked": False})
        except Exception:
            return {"blocked": False}

    def arbitrate_path_a_entry(
        self,
        market: str,
        ticker: str,
        *,
        current_price: float | None = None,
        strategy: str = "",
    ) -> ArbiterDecision | None:
        if not self.enabled or self.path_arbiter is None:
            return None
        try:
            return self.path_arbiter.evaluate_path_a_entry(
                market=market,
                runtime_mode=self.bot._mode,
                session_date=self.bot._current_session_date_str(market),
                ticker=ticker,
                current_price=current_price,
                strategy=strategy,
            )
        except Exception as exc:
            log.warning(f"[Path arbiter] evaluate failed {market} {ticker}: {exc}")
            return None

    def same_day_reentry_decision(self, market: str, ticker: str) -> ArbiterDecision | None:
        if not self.enabled or self.reentry_guard is None:
            return None
        try:
            return self.reentry_guard.evaluate(
                market=market,
                runtime_mode=self.bot._mode,
                session_date=self.bot._current_session_date_str(market),
                ticker=ticker,
            )
        except Exception as exc:
            log.warning(f"[Reentry cooldown] evaluate failed {market} {ticker}: {exc}")
            return None

    def safety_decision(
        self,
        market: str,
        ticker: str,
        *,
        risk_price_krw: float,
        qty: int,
        order_cost_krw: float,
        min_order_krw: float,
    ):
        if not self.enabled or self.safety_gate is None:
            return None
        broker_state = getattr(self.bot, "_broker_state", {}).get(market, {}) if hasattr(self.bot, "_broker_state") else {}
        try:
            equity_daily_pnl_pct = float(self.bot._market_daily_return_pct(market))
        except Exception:
            try:
                equity_daily_pnl_pct = float(self.bot._daily_pnl_pct(market))
            except Exception:
                equity_daily_pnl_pct = 0.0
        try:
            realized_daily_pnl_pct = float(self.bot._market_realized_daily_return_pct(market))
        except Exception:
            realized_daily_pnl_pct = equity_daily_pnl_pct
        ctx = SafetyContext(
            market=market,
            runtime_mode=self.bot._mode,
            ticker=ticker,
            price_krw=float(risk_price_krw or 0),
            qty=int(qty or 0),
            order_cost_krw=float(order_cost_krw or 0),
            cash_krw=float(getattr(self.bot.risk, "cash", 0) or 0),
            min_order_krw=float(min_order_krw or 0),
            positions=list(getattr(self.bot.risk, "positions", []) or []),
            pending_orders=list(getattr(self.bot, "pending_orders", []) or []),
            daily_entry_count=self.daily_entry_count(market),
            max_daily_entries=self.max_daily_entries(market),
            daily_pnl_pct=realized_daily_pnl_pct,
            daily_pnl_basis="realized",
            realized_daily_pnl_pct=realized_daily_pnl_pct,
            equity_daily_pnl_pct=equity_daily_pnl_pct,
            broker_trust_level=str(broker_state.get("trust_level", "unknown") or "unknown"),
            market_open=bool(
                getattr(self.bot, "session_active", False) and getattr(self.bot, "current_market", None) == market
            ),
            stopped_tickers=set(self.same_day_stop_tickers.get(market, set()) or set()),
            order_unknown_blocked=self.order_unknown_blocked(market, ticker),
        )
        try:
            return self.safety_gate.evaluate(ctx)
        except Exception as exc:
            log.warning(f"[V2 safety] evaluate failed {market} {ticker}: {exc}")
            return None

    def record_order_unknown(self, market: str, ticker: str, order: dict, detail: str) -> None:
        execution_id = str(order.get("v2_execution_id", "") or order.get("order_no", "") or "")
        self.record_event(
            "ORDER_UNKNOWN",
            market,
            ticker,
            decision_id=str(order.get("v2_decision_id", "") or ""),
            execution_id=execution_id,
            reason_code="ORDER_UNKNOWN_UNRESOLVED",
            payload={
                "detail": detail,
                "order_no": order.get("order_no", ""),
                "qty": int(order.get("qty", 0) or 0),
                "entry_route": order.get("entry_route", ""),
                "path_run_id": order.get("path_run_id", order.get("pathb_path_run_id", "")),
                "pathb_path_run_id": order.get("pathb_path_run_id", ""),
                "parent_decision_id": order.get("parent_decision_id", order.get("v2_decision_id", "")),
                "selection_snapshot_ts": order.get("selection_snapshot_ts", ""),
                "strategy_used": order.get("strategy_used", order.get("strategy", "")),
                "route_source": order.get("route_source", ""),
            },
        )
        if self.order_unknown is not None:
            try:
                self.order_unknown.record_unknown(
                    market=market,
                    ticker=ticker,
                    execution_id=execution_id,
                    detail=detail,
                )
            except Exception as exc:
                log.warning(f"[V2 ORDER_UNKNOWN] escalation failed {market} {ticker}: {exc}")

    def allow_order_now(self, key: str) -> bool:
        if self.order_rate_limiter is None:
            return True
        return bool(self.order_rate_limiter.allow(key, time.time()))
