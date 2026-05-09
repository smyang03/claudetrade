from __future__ import annotations

import json
import os
import re
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
    ticker = str(row.get("ticker") or row.get("pdno") or row.get("ovrs_pdno") or "").strip()
    if market_key == "US":
        ticker = ticker.upper()
    order_qty = _safe_int(row.get("order_qty", row.get("qty", 0)))
    filled_qty = _safe_int(row.get("filled_qty", 0))
    remaining_qty = max(0, _safe_int(row.get("remaining_qty", order_qty - filled_qty)))
    return {
        "market": market_key,
        "ticker": ticker,
        "name": str(row.get("name") or "").strip(),
        "order_no": str(row.get("order_no") or "").strip(),
        "side": str(row.get("side") or "").strip().lower(),
        "order_status": str(row.get("order_status") or ("open" if remaining_qty > 0 else "filled")),
        "qty": order_qty,
        "order_qty": order_qty,
        "filled_qty": filled_qty,
        "remaining_qty": remaining_qty,
        "avg_price": _safe_float(row.get("avg_price", row.get("fill_price", row.get("price", 0)))),
        "current_price": _safe_float(row.get("current_price", 0)),
        "eval_amount": _safe_float(row.get("eval_amount", 0)),
        "pnl": _safe_float(row.get("pnl", 0)),
        "pnl_pct": _safe_float(row.get("pnl_pct", 0)),
        "order_time": str(row.get("order_time") or "").strip(),
        "fill_time": str(row.get("fill_time") or row.get("order_time") or "").strip(),
        "source": "broker",
    }


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
            snap = empty_snapshot(self.runtime_mode, ttl_by_market=ttl_by_market)
            snap["broken"] = True
            snap["error"] = f"snapshot_json_error:{exc}"
            return snap
        if not isinstance(data, dict):
            snap = empty_snapshot(self.runtime_mode, ttl_by_market=ttl_by_market)
            snap["broken"] = True
            snap["error"] = "snapshot_root_not_object"
            return snap
        data.setdefault("runtime_mode", self.runtime_mode)
        data.setdefault("schema_version", 1)
        data.setdefault("markets", {})
        for market in MARKETS:
            existing = data["markets"].get(market)
            if not isinstance(existing, dict):
                existing = _market_template((ttl_by_market or {}).get(market, 60), missing=True)
            existing.setdefault("ttl_sec", int((ttl_by_market or {}).get(market, existing.get("ttl_sec", 60)) or 60))
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
        self.write_snapshot(snap)
        return snap

    def write_snapshot(self, snapshot: dict[str, Any]) -> None:
        safe = mask_sensitive(snapshot)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
        try:
            tmp.write_text(json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)
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
        return {
            "account_summary": {
                "cash": _safe_float(balance.get("cash", 0)),
                "orderable_cash": _safe_float(balance.get("orderable_cash", balance.get("cash", 0))),
                "total_eval": _safe_float(balance.get("total_eval", 0)),
                "total_profit": _safe_float(balance.get("total_profit", 0)),
                "profit_rate": _safe_float(balance.get("profit_rate", 0)),
                "currency": str(balance.get("currency") or ("USD" if market == "US" else "KRW")),
            },
            "positions": [normalize_position(market, row) for row in balance.get("stocks", []) or []],
            "open_orders": open_orders,
            "today_fills": today_fills,
        }

    def _get_balance(self, market: str, *, force: bool) -> dict[str, Any]:
        if self.balance_provider is not None:
            return self.balance_provider(market, force)
        import kis_api

        token = self._token(market)
        return kis_api.get_balance(token, market=market, force_refresh=force)

    def _get_today_orders(self, market: str) -> list[dict[str, Any]]:
        if self.ccld_provider is not None:
            return [normalize_order(market, row) for row in self.ccld_provider(market, self._trade_date_yyyymmdd(market))]
        import kis_api

        token = self._token(market)
        today = self._trade_date_yyyymmdd(market)
        if market == "US":
            rows = kis_api.inquire_ccnl_us(token, start_date=today, end_date=today, filled_code="00")
        else:
            rows = kis_api.inquire_daily_ccld_kr(token, start_date=today, end_date=today, filled_code="00")
        return [normalize_order(market, row) for row in rows]

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
