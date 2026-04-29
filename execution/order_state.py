from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

from config.v2 import DEFAULT_V2_CONFIG, V2Config
from runtime_paths import get_runtime_path


@dataclass(frozen=True)
class PartialFillResult:
    status: str
    filled_qty: int
    remaining_qty: int
    ttl_sec: int
    due_for_cancel: bool


class PartialFillPolicy:
    def __init__(self, config: V2Config = DEFAULT_V2_CONFIG):
        self.config = config

    def ttl_sec(self, market: str) -> int:
        return self.config.us_partial_fill_ttl_sec if str(market).upper() == "US" else self.config.kr_partial_fill_ttl_sec

    def apply(
        self,
        *,
        market: str,
        original_qty: int,
        newly_filled_qty: int,
        first_partial_at: str | None = None,
        now: datetime | None = None,
    ) -> PartialFillResult:
        original = max(0, int(original_qty or 0))
        filled = max(0, int(newly_filled_qty or 0))
        remaining = max(0, original - filled)
        ttl = self.ttl_sec(market)
        current = now or datetime.now(timezone.utc)
        due = False
        if remaining > 0 and first_partial_at:
            try:
                first_dt = datetime.fromisoformat(first_partial_at.replace("Z", "+00:00"))
                if first_dt.tzinfo is None:
                    first_dt = first_dt.replace(tzinfo=timezone.utc)
                due = (current - first_dt.astimezone(current.tzinfo or timezone.utc)).total_seconds() >= ttl
            except ValueError:
                due = True
        status = "FILLED" if remaining == 0 and filled > 0 else "PARTIAL_FILLED" if filled > 0 else "UNFILLED"
        return PartialFillResult(status=status, filled_qty=filled, remaining_qty=remaining, ttl_sec=ttl, due_for_cancel=due)


class OrderUnknownEscalator:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else get_runtime_path("state", "v2_order_unknown.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load()

    def record_unknown(self, *, market: str, ticker: str, execution_id: str = "", detail: str = "") -> dict[str, Any]:
        market_key = str(market or "").upper()
        ticker_key = str(ticker or "").strip().upper() if market_key == "US" else str(ticker or "").strip()
        order_no = str(execution_id or "").strip()
        state = self.state
        state.setdefault("paused_tickers", {}).setdefault(market_key, {})
        state.setdefault("market_consecutive_unknown", {})
        state.setdefault("orders", {})
        state.setdefault("events", [])
        state["paused_tickers"][market_key][ticker_key] = {
            "execution_id": execution_id,
            "detail": detail,
            "recorded_at": _now(),
        }
        if order_no:
            state["orders"][self._order_key(market_key, order_no)] = {
                "order_no": order_no,
                "market": market_key,
                "ticker": ticker_key,
                "execution_id": execution_id,
                "detail": detail,
                "local_pending": True,
                "broker_open": False,
                "cancel_requested_at": "",
                "cancel_attempts": 0,
                "last_checked_at": _now(),
                "next_check_at": "",
                "resolution": "ORDER_UNKNOWN_UNRESOLVED",
                "resolved_at": "",
                "recorded_at": _now(),
            }
        state["market_consecutive_unknown"][market_key] = int(state["market_consecutive_unknown"].get(market_key, 0) or 0) + 1
        if int(state["market_consecutive_unknown"][market_key]) >= 2:
            state.setdefault("paused_markets", {})[market_key] = {
                "reason": "two_consecutive_order_unknown",
                "recorded_at": _now(),
            }
        paused_markets = state.get("paused_markets", {})
        if "KR" in paused_markets and "US" in paused_markets:
            state["global_halt"] = {
                "reason": "all_markets_broker_untrusted",
                "recorded_at": _now(),
            }
        state["events"].append(
            {
                "type": "ORDER_UNKNOWN",
                "market": market_key,
                "ticker": ticker_key,
                "execution_id": execution_id,
                "detail": detail,
                "recorded_at": _now(),
            }
        )
        self._save()
        return self.block_state(market=market_key, ticker=ticker_key)

    def record_recovered(self, *, market: str, ticker: str, execution_id: str = "") -> None:
        market_key = str(market or "").upper()
        ticker_key = str(ticker or "").strip().upper() if market_key == "US" else str(ticker or "").strip()
        order_no = str(execution_id or "").strip()
        self.state.setdefault("paused_tickers", {}).setdefault(market_key, {}).pop(ticker_key, None)
        self.state.setdefault("market_consecutive_unknown", {})[market_key] = 0
        if order_no:
            order_key = self._order_key(market_key, order_no)
            order_state = dict(self.state.setdefault("orders", {}).get(order_key) or {})
            if order_state:
                order_state["resolution"] = "RECOVERED"
                order_state["resolved_at"] = _now()
                order_state["last_checked_at"] = _now()
                self.state["orders"][order_key] = order_state
        self.state.setdefault("events", []).append(
            {
                "type": "ORDER_RECOVERED",
                "market": market_key,
                "ticker": ticker_key,
                "execution_id": execution_id,
                "recorded_at": _now(),
            }
        )
        self._save()

    def record_broker_open_order(
        self,
        *,
        market: str,
        ticker: str,
        order_no: str,
        side: str = "",
        qty: int = 0,
        remaining_qty: int = 0,
        reason: str = "",
    ) -> dict[str, Any]:
        market_key = str(market or "").upper()
        ticker_key = str(ticker or "").strip().upper() if market_key == "US" else str(ticker or "").strip()
        order_no_key = str(order_no or "").strip()
        state = self.state
        state.setdefault("orders", {})
        state.setdefault("paused_tickers", {}).setdefault(market_key, {})
        state.setdefault("paused_markets", {})
        state.setdefault("events", [])
        if order_no_key:
            existing = dict(state["orders"].get(self._order_key(market_key, order_no_key)) or {})
            existing.update(
                {
                    "order_no": order_no_key,
                    "market": market_key,
                    "ticker": ticker_key,
                    "side": side,
                    "qty": int(qty or existing.get("qty", 0) or 0),
                    "remaining_qty": int(remaining_qty or existing.get("remaining_qty", 0) or 0),
                    "local_pending": bool(existing.get("local_pending", False)),
                    "broker_open": True,
                    "last_checked_at": _now(),
                    "next_check_at": "",
                    "resolution": "BROKER_ONLY_OPEN_ORDER",
                    "resolved_at": "",
                    "detail": reason,
                    "recorded_at": existing.get("recorded_at") or _now(),
                }
            )
            state["orders"][self._order_key(market_key, order_no_key)] = existing
        state["paused_tickers"][market_key][ticker_key] = {
            "execution_id": order_no_key,
            "detail": reason or "broker_only_open_order",
            "recorded_at": _now(),
        }
        state["paused_markets"][market_key] = {
            "reason": "broker_only_open_order",
            "recorded_at": _now(),
        }
        state["events"].append(
            {
                "type": "BROKER_ONLY_OPEN_ORDER",
                "market": market_key,
                "ticker": ticker_key,
                "execution_id": order_no_key,
                "detail": reason,
                "recorded_at": _now(),
            }
        )
        self._save()
        return self.block_state(market=market_key, ticker=ticker_key)

    def record_duplicate_open_orders(
        self,
        *,
        market: str,
        ticker: str,
        order_nos: list[str],
        reason: str = "",
    ) -> dict[str, Any]:
        market_key = str(market or "").upper()
        ticker_key = str(ticker or "").strip().upper() if market_key == "US" else str(ticker or "").strip()
        state = self.state
        state.setdefault("paused_tickers", {}).setdefault(market_key, {})
        state.setdefault("paused_markets", {})
        state.setdefault("events", [])
        clean_order_nos = [str(o or "").strip() for o in order_nos or [] if str(o or "").strip()]
        state["paused_tickers"][market_key][ticker_key] = {
            "execution_id": ",".join(clean_order_nos),
            "detail": reason or "duplicate_open_orders",
            "recorded_at": _now(),
        }
        state["paused_markets"][market_key] = {
            "reason": "duplicate_open_orders",
            "recorded_at": _now(),
        }
        state["events"].append(
            {
                "type": "DUPLICATE_OPEN_ORDERS",
                "market": market_key,
                "ticker": ticker_key,
                "execution_id": ",".join(clean_order_nos),
                "detail": reason,
                "recorded_at": _now(),
            }
        )
        self._save()
        return self.block_state(market=market_key, ticker=ticker_key)

    def record_cancel_requested(
        self,
        *,
        market: str,
        ticker: str,
        order_no: str,
        qty: int = 0,
        reason: str = "",
    ) -> dict[str, Any]:
        market_key = str(market or "").upper()
        ticker_key = str(ticker or "").strip().upper() if market_key == "US" else str(ticker or "").strip()
        order_no_key = str(order_no or "").strip()
        now = _now()
        state = self.state
        state.setdefault("orders", {})
        state.setdefault("paused_tickers", {}).setdefault(market_key, {})
        if order_no_key:
            key = self._order_key(market_key, order_no_key)
            existing = dict(state["orders"].get(key) or {})
            existing.update(
                {
                    "order_no": order_no_key,
                    "market": market_key,
                    "ticker": ticker_key,
                    "qty": int(qty or existing.get("qty", 0) or 0),
                    "cancel_requested_at": existing.get("cancel_requested_at") or now,
                    "cancel_attempts": int(existing.get("cancel_attempts", 0) or 0) + 1,
                    "last_checked_at": now,
                    "next_check_at": "",
                    "resolution": "CANCEL_REQUESTED",
                    "resolved_at": "",
                    "detail": reason,
                    "recorded_at": existing.get("recorded_at") or now,
                }
            )
            state["orders"][key] = existing
        state["paused_tickers"][market_key][ticker_key] = {
            "execution_id": order_no_key,
            "detail": reason or "cancel_requested",
            "recorded_at": now,
        }
        state.setdefault("events", []).append(
            {
                "type": "CANCEL_REQUESTED",
                "market": market_key,
                "ticker": ticker_key,
                "execution_id": order_no_key,
                "detail": reason,
                "recorded_at": now,
            }
        )
        self._save()
        return self.block_state(market=market_key, ticker=ticker_key)

    def record_cancel_resolved(
        self,
        *,
        market: str,
        ticker: str,
        order_no: str,
        resolution: str = "CANCEL_CONFIRMED",
    ) -> None:
        market_key = str(market or "").upper()
        ticker_key = str(ticker or "").strip().upper() if market_key == "US" else str(ticker or "").strip()
        order_no_key = str(order_no or "").strip()
        now = _now()
        if order_no_key:
            key = self._order_key(market_key, order_no_key)
            existing = dict(self.state.setdefault("orders", {}).get(key) or {})
            if existing:
                existing["resolution"] = str(resolution or "CANCEL_CONFIRMED")
                existing["resolved_at"] = now
                existing["last_checked_at"] = now
                existing["broker_open"] = False
                self.state["orders"][key] = existing
        self.state.setdefault("paused_tickers", {}).setdefault(market_key, {}).pop(ticker_key, None)
        self.state.setdefault("events", []).append(
            {
                "type": str(resolution or "CANCEL_CONFIRMED"),
                "market": market_key,
                "ticker": ticker_key,
                "execution_id": order_no_key,
                "recorded_at": now,
            }
        )
        self._save()

    def clear_manual_resume(self, *, market: str | None = None) -> None:
        if market:
            market_key = str(market or "").upper()
            self.state.setdefault("paused_markets", {}).pop(market_key, None)
            self.state.setdefault("market_consecutive_unknown", {})[market_key] = 0
        else:
            self.state["paused_markets"] = {}
            self.state["market_consecutive_unknown"] = {}
            self.state.pop("global_halt", None)
        self._save()

    def block_state(self, *, market: str, ticker: str = "") -> dict[str, Any]:
        market_key = str(market or "").upper()
        ticker_key = str(ticker or "").strip().upper() if market_key == "US" else str(ticker or "").strip()
        if self.state.get("global_halt"):
            return {"blocked": True, "scope": "global", "reason": "ORDER_UNKNOWN_UNRESOLVED"}
        if market_key in self.state.get("paused_markets", {}):
            return {"blocked": True, "scope": "market", "reason": "ORDER_UNKNOWN_UNRESOLVED"}
        if ticker_key and ticker_key in self.state.get("paused_tickers", {}).get(market_key, {}):
            return {"blocked": True, "scope": "ticker", "reason": "ORDER_UNKNOWN_UNRESOLVED"}
        return {"blocked": False}

    def should_block_market(self, market: str) -> bool:
        market_key = str(market or "").upper()
        return bool(self.state.get("global_halt") or market_key in self.state.get("paused_markets", {}))

    def should_block_global(self) -> bool:
        return bool(self.state.get("global_halt"))

    @staticmethod
    def _order_key(market: str, order_no: str) -> str:
        return f"{str(market or '').upper()}:{str(order_no or '').strip()}"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"paused_tickers": {}, "paused_markets": {}, "market_consecutive_unknown": {}, "orders": {}, "events": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            data.setdefault("paused_tickers", {})
            data.setdefault("paused_markets", {})
            data.setdefault("market_consecutive_unknown", {})
            data.setdefault("orders", {})
            data.setdefault("events", [])
            return data
        except Exception:
            return {"paused_tickers": {}, "paused_markets": {}, "market_consecutive_unknown": {}, "orders": {}, "events": []}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
