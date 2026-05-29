from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from runtime_paths import get_runtime_path


KST = ZoneInfo("Asia/Seoul") if ZoneInfo is not None else None
MARKETS = ("KR", "US")
log = logging.getLogger("trading")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def age_seconds(value: Any, *, now: datetime | None = None) -> float | None:
    dt = parse_dt(value)
    if dt is None:
        return None
    current = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (current - dt.astimezone(current.tzinfo or timezone.utc)).total_seconds())


def _env_int(name: str, default: int, *, min_value: int, max_value: int) -> int:
    try:
        value = int(float(os.getenv(name, str(default)) or default))
    except Exception:
        value = int(default)
    return max(min_value, min(max_value, value))


def _env_float(name: str, default: float, *, min_value: float, max_value: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except Exception:
        value = float(default)
    return max(min_value, min(max_value, value))


def _is_snapshot_write_contention(exc: OSError) -> bool:
    winerror = getattr(exc, "winerror", None)
    errno = getattr(exc, "errno", None)
    return isinstance(exc, PermissionError) or winerror in {5, 32} or errno in {13, 32}


def _market_template(ttl_sec: int = 60, *, missing: bool = True) -> dict[str, Any]:
    return {
        "missing": bool(missing),
        "stale": bool(missing),
        "last_success_at": "",
        "last_attempt_at": "",
        "ttl_sec": int(ttl_sec or 60),
        "error": "",
        "account_summary": {},
        "positions": [],
        "open_orders": [],
        "today_fills": [],
        "source": "broker_truth_snapshot",
    }


def empty_snapshot(runtime_mode: str = "live", *, ttl_by_market: dict[str, int] | None = None) -> dict[str, Any]:
    ttl_by_market = ttl_by_market or {}
    return {
        "generated_at": "",
        "runtime_mode": str(runtime_mode or "live"),
        "schema_version": 1,
        "markets": {
            market: _market_template(ttl_by_market.get(market, 60), missing=True)
            for market in MARKETS
        },
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value or "").replace(",", ""))
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "")))
    except Exception:
        return int(default)


def _orderable_cash_fields(market: str, balance: dict[str, Any]) -> tuple[float, str, bool]:
    cash = _safe_float(balance.get("cash", 0))
    has_orderable = "orderable_cash" in balance and balance.get("orderable_cash") not in (None, "")
    orderable = _safe_float(balance.get("orderable_cash", 0)) if has_orderable else 0.0
    if orderable > 0:
        return orderable, "orderable_cash", str(market or "").upper() == "US"
    if cash > 0:
        return cash, "cash_fallback", False
    return 0.0, "missing", False


def _first_value(row: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        for candidate in (key, key.upper(), key.lower()):
            value = row.get(candidate)
            if value not in (None, ""):
                return value
    return default


def mask_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            key_s = str(key)
            if re.search(r"(token|secret|app_?key|authorization)", key_s, re.I):
                masked[key_s] = "***"
            elif key_s.lower() not in {"account_summary"} and re.search(r"(account|cano|acnt|acct)", key_s, re.I):
                text = str(item or "")
                masked[key_s] = text[:2] + "***" + text[-2:] if len(text) > 4 else "***"
            else:
                masked[key_s] = mask_sensitive(item)
        return masked
    if isinstance(value, list):
        return [mask_sensitive(item) for item in value]
    if isinstance(value, str):
        text = re.sub(r"(?i)(CANO=)[^&\s'\"<>]+", r"\1***", value)
        text = re.sub(r"(?i)(ACNT_PRDT_CD=)[^&\s'\"<>]+", r"\1***", text)
        text = re.sub(r"(?i)(account_no=)[^&\s'\"<>]+", r"\1***", text)
        return text
    return value


def normalize_position(market: str, row: dict[str, Any]) -> dict[str, Any]:
    market_key = str(market or "").upper()
    ticker = str(row.get("ticker") or row.get("pdno") or row.get("ovrs_pdno") or "").strip()
    if market_key == "US":
        ticker = ticker.upper()
    qty = _safe_int(row.get("qty", row.get("hldg_qty", row.get("ovrs_cblc_qty", 0))))
    avg = _safe_float(row.get("avg_price", row.get("entry", row.get("pchs_avg_pric", 0))))
    current = _safe_float(row.get("current_price", row.get("eval_price", row.get("prpr", row.get("now_pric2", avg)))))
    pnl = _safe_float(row.get("pnl", row.get("eval_profit", row.get("evlu_pfls_amt", 0))))
    cost = avg * qty
    pnl_pct = _safe_float(row.get("pnl_pct", row.get("profit_rate", 0.0)))
    if not pnl_pct and cost > 0:
        pnl_pct = pnl / cost * 100.0
    return {
        "market": market_key,
        "ticker": ticker,
        "name": str(row.get("name") or row.get("prdt_name") or row.get("ovrs_item_name") or "").strip(),
        "qty": qty,
        "avg_price": avg,
        "current_price": current,
        "eval_amount": _safe_float(row.get("eval_amount"), current * qty),
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "source": "broker",
    }


def normalize_order(market: str, row: dict[str, Any]) -> dict[str, Any]:
    market_key = str(market or "").upper()
    ticker = str(_first_value(row, ("ticker", "pdno", "ovrs_pdno"), "") or "").strip()
    if market_key == "US":
        ticker = ticker.upper()
    order_qty = _safe_int(_first_value(row, ("order_qty", "ft_ord_qty", "ord_qty", "qty"), 0))
    if order_qty <= 0:
        order_qty = _safe_int(_first_value(row, ("ft_ord_qty", "ord_qty", "qty"), 0))
    filled_qty = _safe_int(_first_value(row, ("filled_qty", "ft_ccld_qty", "ccld_qty", "tot_ccld_qty"), 0))
    rejected_qty = _safe_int(_first_value(row, ("rejected_qty", "rjct_qty"), 0))
    cancelled_qty = _safe_int(_first_value(row, ("cancelled_qty", "cncl_cfrm_qty"), 0))
    remaining_raw = _first_value(row, ("remaining_qty", "nccs_qty", "rmn_qty", "ord_rmn_qty"), None)
    if remaining_raw is None:
        remaining_qty = max(0, order_qty - filled_qty - rejected_qty - cancelled_qty)
    else:
        remaining_qty = max(0, _safe_int(remaining_raw))
        if remaining_qty <= 0:
            remaining_qty = max(remaining_qty, _safe_int(_first_value(row, ("nccs_qty", "rmn_qty", "ord_rmn_qty"), 0)))
    order_price = _safe_float(
        _first_value(row, ("order_price", "limit_price", "ft_ord_unpr3", "ovrs_ord_unpr", "ord_unpr"), 0)
    )
    if order_price <= 0:
        order_price = _safe_float(_first_value(row, ("ft_ord_unpr3", "ovrs_ord_unpr", "ord_unpr"), 0))
    avg_price = _safe_float(_first_value(row, ("avg_price", "fill_price", "price", "ft_ccld_unpr3", "ft_ccld_unpr"), 0))
    if avg_price <= 0:
        avg_price = order_price
    side = str(row.get("side") or "").strip().lower()
    if not side:
        side_code = str(_first_value(row, ("sll_buy_dvsn", "sll_buy_dvsn_cd"), "") or "").strip()
        side = {"01": "sell", "02": "buy"}.get(side_code, "")
    order_status = str(row.get("order_status") or "").strip().lower()
    if not order_status:
        if rejected_qty > 0 and remaining_qty == 0 and filled_qty == 0:
            order_status = "rejected"
        elif cancelled_qty > 0 and remaining_qty == 0:
            order_status = "cancelled" if filled_qty == 0 else "partial_cancelled"
        elif remaining_qty > 0:
            order_status = "partial_open" if filled_qty > 0 else "open"
        else:
            order_status = "filled"
    normalized = {
        "market": market_key,
        "ticker": ticker,
        "name": str(row.get("name") or "").strip(),
        "order_no": str(_first_value(row, ("order_no", "odno", "ord_no"), "") or "").strip(),
        "side": side,
        "order_status": order_status,
        "qty": order_qty,
        "order_qty": order_qty,
        "filled_qty": filled_qty,
        "remaining_qty": remaining_qty,
        "rejected_qty": rejected_qty,
        "cancelled_qty": cancelled_qty,
        "avg_price": avg_price,
        "order_price": order_price,
        "limit_price": order_price,
        "current_price": _safe_float(row.get("current_price", 0)),
        "eval_amount": _safe_float(row.get("eval_amount", 0)),
        "pnl": _safe_float(row.get("pnl", 0)),
        "pnl_pct": _safe_float(row.get("pnl_pct", 0)),
        "order_date": str(row.get("order_date") or "").strip(),
        "order_time": str(row.get("order_time") or "").strip(),
        "fill_time": str(row.get("fill_time") or row.get("order_time") or "").strip(),
        "source": "broker",
    }
    flags = open_order_integrity_flags(normalized)
    if flags:
        normalized["integrity_flags"] = flags
    return normalized


def open_order_integrity_flags(order: dict[str, Any]) -> list[str]:
    if _safe_int(order.get("remaining_qty", 0)) <= 0:
        return []
    flags: list[str] = []
    if _safe_int(order.get("order_qty", order.get("qty", 0))) <= 0:
        flags.append("order_qty_zero")
    if _safe_float(order.get("order_price", order.get("limit_price", 0))) <= 0:
        flags.append("order_price_zero")
    if not str(order.get("order_no") or "").strip():
        flags.append("order_no_missing")
    if not str(order.get("ticker") or "").strip():
        flags.append("ticker_missing")
    if not str(order.get("side") or "").strip():
        flags.append("side_missing")
    return flags


class BrokerTruthSnapshot:
    def __init__(
        self,
        *,
        runtime_mode: str = "live",
        path: str | Path | None = None,
        token_provider: Callable[..., str] | None = None,
        balance_provider: Callable[[str, bool], dict[str, Any]] | None = None,
        ccld_provider: Callable[[str, str], list[dict[str, Any]]] | None = None,
        date_provider: Callable[[str], str] | None = None,
    ):
        self.runtime_mode = str(runtime_mode or "live")
        self.path = Path(path) if path else get_runtime_path("state", f"{self.runtime_mode}_broker_truth_snapshot.json")
        self.token_provider = token_provider
        self.balance_provider = balance_provider
        self.ccld_provider = ccld_provider
        self.date_provider = date_provider

    def load_snapshot(self, *, ttl_by_market: dict[str, int] | None = None) -> dict[str, Any]:
        if not self.path.exists():
            return empty_snapshot(self.runtime_mode, ttl_by_market=ttl_by_market)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except Exception as exc:
            return self._load_last_good_or_broken(
                ttl_by_market=ttl_by_market,
                error=f"snapshot_json_error:{exc}",
            )
        if not isinstance(data, dict):
            return self._load_last_good_or_broken(
                ttl_by_market=ttl_by_market,
                error="snapshot_root_not_object",
            )
        return self._normalize_snapshot(data, ttl_by_market=ttl_by_market)

    def _last_good_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.last_good")

    def _load_last_good_or_broken(
        self,
        *,
        ttl_by_market: dict[str, int] | None,
        error: str,
    ) -> dict[str, Any]:
        last_good = self._last_good_path()
        if last_good.exists():
            try:
                data = json.loads(last_good.read_text(encoding="utf-8") or "{}")
                if isinstance(data, dict):
                    normalized = self._normalize_snapshot(data, ttl_by_market=ttl_by_market)
                    normalized["loaded_from_last_good"] = True
                    normalized["snapshot_source"] = "last_good"
                    normalized["primary_snapshot_error"] = error
                    for market_data in (normalized.get("markets") or {}).values():
                        if isinstance(market_data, dict):
                            market_data["snapshot_source"] = "last_good"
                    return normalized
                error = f"{error};last_good_root_not_object"
            except Exception as exc:
                error = f"{error};last_good_json_error:{exc}"
        snap = empty_snapshot(self.runtime_mode, ttl_by_market=ttl_by_market)
        snap["broken"] = True
        snap["error"] = error
        snap["snapshot_source"] = "empty_fail_closed"
        return snap

    def _normalize_snapshot(
        self,
        data: dict[str, Any],
        *,
        ttl_by_market: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        data.setdefault("runtime_mode", self.runtime_mode)
        data.setdefault("schema_version", 1)
        data.setdefault("markets", {})
        for market in MARKETS:
            existing = data["markets"].get(market)
            if not isinstance(existing, dict):
                existing = _market_template((ttl_by_market or {}).get(market, 60), missing=True)
            ttl_override = (ttl_by_market or {}).get(market)
            if ttl_override is not None:
                existing["ttl_sec"] = int(ttl_override or 60)
            else:
                existing.setdefault("ttl_sec", int(existing.get("ttl_sec", 60) or 60))
            existing.setdefault("positions", [])
            existing.setdefault("open_orders", [])
            existing.setdefault("today_fills", [])
            existing.setdefault("account_summary", {})
            existing.setdefault("error", "")
            existing.setdefault("last_success_at", "")
            existing.setdefault("last_attempt_at", "")
            existing.setdefault("missing", not bool(existing.get("last_success_at")))
            existing["stale"] = self._is_market_stale(existing)
            data["markets"][market] = existing
        return data

    def market_snapshot(self, market: str, *, ttl_sec: int | None = None) -> dict[str, Any]:
        ttl_by_market = {str(market or "").upper(): int(ttl_sec)} if ttl_sec is not None else None
        snap = self.load_snapshot(ttl_by_market=ttl_by_market)
        market_key = str(market or "").upper()
        return dict((snap.get("markets") or {}).get(market_key) or _market_template(ttl_sec or 60, missing=True))

    def is_market_stale(self, market: str, *, ttl_sec: int | None = None) -> bool:
        return bool(self.market_snapshot(market, ttl_sec=ttl_sec).get("stale"))

    def refresh_all(self, *, force: bool = False, ttl_by_market: dict[str, int] | None = None) -> dict[str, Any]:
        snap = self.load_snapshot(ttl_by_market=ttl_by_market)
        for market in MARKETS:
            ttl = int((ttl_by_market or {}).get(market, 30))
            snap = self.refresh_market(market, force=force, ttl_sec=ttl, snapshot=snap)
        return snap

    def refresh_market(
        self,
        market: str,
        *,
        force: bool = False,
        ttl_sec: int = 30,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        market_key = str(market or "").upper()
        if market_key not in MARKETS:
            raise ValueError(f"unsupported market: {market}")
        snap = snapshot or self.load_snapshot(ttl_by_market={market_key: ttl_sec})
        markets = snap.setdefault("markets", {})
        previous = markets.get(market_key) if isinstance(markets.get(market_key), dict) else _market_template(ttl_sec)
        previous["ttl_sec"] = int(ttl_sec or previous.get("ttl_sec", 30) or 30)
        if not force and not self._is_market_stale(previous):
            markets[market_key] = previous
            return snap
        attempt_at = utc_now_iso()
        try:
            collected = self._collect_market(market_key, force=force)
            collected.update(
                {
                    "missing": False,
                    "stale": False,
                    "last_attempt_at": attempt_at,
                    "last_success_at": utc_now_iso(),
                    "ttl_sec": int(ttl_sec or 30),
                    "error": "",
                    "source": "broker",
                }
            )
            markets[market_key] = collected
        except Exception as exc:
            failed = dict(previous or _market_template(ttl_sec))
            failed.update(
                {
                    "missing": not bool(failed.get("last_success_at")),
                    "stale": True,
                    "last_attempt_at": attempt_at,
                    "ttl_sec": int(ttl_sec or 30),
                    "error": str(exc),
                    "source": "broker_error_previous_snapshot",
                }
            )
            markets[market_key] = failed
        snap["generated_at"] = utc_now_iso()
        snap["runtime_mode"] = self.runtime_mode
        snap["schema_version"] = 1
        write_status = self.write_snapshot(snap)
        if isinstance(write_status, dict) and not bool(write_status.get("ok", True)):
            snap["snapshot_write_status"] = write_status
            snap["snapshot_write_error"] = str(write_status.get("write_error") or "")
            snap["snapshot_write_attempts"] = int(write_status.get("write_attempts") or 0)
        return snap

    def write_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if self.path.exists():
            try:
                existing = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
            incoming_markets = snapshot.setdefault("markets", {})
            existing_markets = existing.get("markets") if isinstance(existing, dict) else {}
            if isinstance(existing_markets, dict):
                for market in MARKETS:
                    incoming_market = incoming_markets.get(market)
                    existing_market = existing_markets.get(market)
                    if not isinstance(incoming_market, dict) or not isinstance(existing_market, dict):
                        continue
                    incoming_last = parse_dt(incoming_market.get("last_success_at"))
                    existing_last = parse_dt(existing_market.get("last_success_at"))
                    if existing_last is not None and (
                        incoming_last is None or existing_last > incoming_last
                    ):
                        incoming_markets[market] = existing_market
        safe = mask_sensitive(snapshot)
        text = json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True)
        json.loads(text)
        market_names = ",".join(sorted((safe.get("markets") or {}).keys())) if isinstance(safe.get("markets"), dict) else ""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        attempts = _env_int("BROKER_TRUTH_SNAPSHOT_WRITE_ATTEMPTS", 4, min_value=1, max_value=10)
        delay_sec = _env_float("BROKER_TRUTH_SNAPSHOT_WRITE_RETRY_DELAY_SEC", 0.15, min_value=0.0, max_value=2.0)
        last_error = ""
        for attempt in range(1, attempts + 1):
            tmp = self.path.with_name(f"{self.path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
            try:
                tmp.write_text(text, encoding="utf-8")
                os.replace(tmp, self.path)
                self._write_last_good_snapshot_text(text)
                return {"ok": True, "write_attempts": attempt, "path": str(self.path)}
            except OSError as exc:
                last_error = str(exc)
                if attempt >= attempts or not _is_snapshot_write_contention(exc):
                    last_good_status = self._write_last_good_snapshot_text(text)
                    log.warning(
                        "[broker truth snapshot write failed] markets=%s path=%s attempts=%s error=%s last_good_ok=%s",
                        market_names,
                        self.path,
                        attempt,
                        exc,
                        last_good_status.get("ok"),
                    )
                    return {
                        "ok": False,
                        "path": str(self.path),
                        "write_attempts": attempt,
                        "write_error": last_error,
                        "write_error_type": type(exc).__name__,
                        "last_good": last_good_status,
                        "stale": True,
                    }
                time.sleep(delay_sec * attempt)
            finally:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except Exception:
                    pass
        last_good_status = self._write_last_good_snapshot_text(text)
        return {
            "ok": False,
            "path": str(self.path),
            "write_attempts": attempts,
            "write_error": last_error or "unknown_write_failure",
            "last_good": last_good_status,
            "stale": True,
        }

    def _write_last_good_snapshot_text(self, text: str) -> dict[str, Any]:
        fallback = self._last_good_path()
        fallback.parent.mkdir(parents=True, exist_ok=True)
        tmp = fallback.with_name(f"{fallback.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
        try:
            json.loads(text)
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, fallback)
            return {"ok": True, "path": str(fallback)}
        except Exception as exc:
            log.warning("[broker truth last_good write failed] path=%s error=%s", fallback, exc)
            return {"ok": False, "path": str(fallback), "error": str(exc), "error_type": type(exc).__name__}
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass

    def _is_market_stale(self, market_data: dict[str, Any]) -> bool:
        if bool(market_data.get("missing")):
            return True
        last = market_data.get("last_success_at")
        if not last:
            return True
        age = age_seconds(last)
        if age is None:
            return True
        return age > int(market_data.get("ttl_sec") or 60)

    def _collect_market(self, market: str, *, force: bool) -> dict[str, Any]:
        balance = self._get_balance(market, force=force)
        fills = self._get_today_orders(market)
        open_orders = [row for row in fills if int(row.get("remaining_qty", 0) or 0) > 0]
        today_fills = [row for row in fills if int(row.get("filled_qty", 0) or 0) > 0]
        open_order_integrity_issues = self._open_order_integrity_issues(market, open_orders)
        if open_order_integrity_issues:
            log.warning(
                f"[broker truth 미체결 identity 경고] {market} "
                f"issues={open_order_integrity_issues[:5]}"
            )
        orderable_cash, orderable_source, orderable_nets_open_orders = _orderable_cash_fields(market, balance)
        account_summary = {
            "cash": _safe_float(balance.get("cash", 0)),
            "orderable_cash": orderable_cash,
            "orderable_cash_source": orderable_source,
            "orderable_cash_nets_open_orders": bool(orderable_nets_open_orders),
            "total_eval": _safe_float(balance.get("total_eval", 0)),
            "total_profit": _safe_float(balance.get("total_profit", 0)),
            "profit_rate": _safe_float(balance.get("profit_rate", 0)),
            "currency": str(balance.get("currency") or ("USD" if market == "US" else "KRW")),
        }
        for key in (
            "asset_cash",
            "asset_total_krw",
            "net_asset_krw",
            "total_asset_krw",
            "cash_settlement_krw",
            "d1_settlement_krw",
            "d2_settlement_krw",
            "today_buy_amount_krw",
            "today_sell_amount_krw",
            "kis_total_asset_krw",
            "kis_domestic_cash_krw",
            "market_asset_krw",
            "asset_cash_krw",
            "total_eval_krw",
            "purchase_amount_krw",
            "total_profit_krw",
            "kis_exchange_rate",
        ):
            if key in balance:
                account_summary[key] = _safe_float(balance.get(key, 0))
        return {
            "account_summary": account_summary,
            "positions": [normalize_position(market, row) for row in balance.get("stocks", []) or []],
            "open_orders": open_orders,
            "today_fills": today_fills,
            "open_order_integrity_issues": open_order_integrity_issues,
        }

    def _open_order_integrity_issues(self, market: str, open_orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        market_key = str(market or "").upper()
        for row in open_orders:
            flags = list(row.get("integrity_flags") or open_order_integrity_flags(row))
            if not flags:
                continue
            issues.append(
                {
                    "market": market_key,
                    "ticker": str(row.get("ticker") or "").strip(),
                    "order_no": str(row.get("order_no") or "").strip(),
                    "side": str(row.get("side") or "").strip(),
                    "remaining_qty": _safe_int(row.get("remaining_qty", 0)),
                    "order_qty": _safe_int(row.get("order_qty", row.get("qty", 0))),
                    "order_price": _safe_float(row.get("order_price", row.get("limit_price", 0))),
                    "flags": flags,
                }
            )
        return issues

    def _get_balance(self, market: str, *, force: bool) -> dict[str, Any]:
        if self.balance_provider is not None:
            return self.balance_provider(market, force)
        import kis_api

        token = self._token(market)
        return kis_api.get_balance(token, market=market, force_refresh=force)

    def _get_today_orders(self, market: str) -> list[dict[str, Any]]:
        if self.ccld_provider is not None:
            today = self._trade_date_yyyymmdd(market)
            rows = self.ccld_provider(market, today)
            return self._filter_orders_for_query_date([normalize_order(market, row) for row in rows], today)
        import kis_api

        token = self._token(market)
        today = self._trade_date_yyyymmdd(market)
        if market == "US":
            rows = kis_api.inquire_ccnl_us(token, start_date=today, end_date=today, filled_code="00")
        else:
            rows = kis_api.inquire_daily_ccld_kr(token, start_date=today, end_date=today, filled_code="00")
        return self._filter_orders_for_query_date([normalize_order(market, row) for row in rows], today)

    @staticmethod
    def _filter_orders_for_query_date(rows: list[dict[str, Any]], query_date: str) -> list[dict[str, Any]]:
        query_digits = re.sub(r"[^0-9]", "", str(query_date or ""))[:8]
        if not query_digits:
            return rows
        filtered: list[dict[str, Any]] = []
        for row in rows:
            order_digits = re.sub(r"[^0-9]", "", str(row.get("order_date") or ""))[:8]
            if order_digits and order_digits != query_digits:
                continue
            filtered.append(row)
        return filtered

    def _token(self, market: str = "KR") -> str:
        if self.token_provider is not None:
            try:
                return str(self.token_provider(str(market or "").upper()) or "")
            except TypeError:
                return str(self.token_provider() or "")
        import kis_api

        return kis_api.get_access_token(market=str(market or "KR").upper())

    def _trade_date_yyyymmdd(self, market: str) -> str:
        if self.date_provider is not None:
            raw = str(self.date_provider(str(market or "").upper()) or "").strip()
            digits = re.sub(r"[^0-9]", "", raw)
            if len(digits) >= 8:
                return digits[:8]
        return self._session_yyyymmdd(market)

    @staticmethod
    def _session_yyyymmdd(market: str = "KR") -> str:
        now = datetime.now(KST) if KST is not None else datetime.now()
        # US 세션은 KST 기준 전날 밤 시작 → KST 06:00 이전이면 전날 날짜(ET 당일)
        if str(market).upper() == "US" and now.hour < 6:
            now = now - timedelta(days=1)
        return now.strftime("%Y%m%d")


def load_broker_truth_snapshot(runtime_mode: str = "live", *, path: str | Path | None = None) -> dict[str, Any]:
    return BrokerTruthSnapshot(runtime_mode=runtime_mode, path=path).load_snapshot()


def market_broker_truth(runtime_mode: str, market: str, *, path: str | Path | None = None) -> dict[str, Any]:
    return BrokerTruthSnapshot(runtime_mode=runtime_mode, path=path).market_snapshot(market)
