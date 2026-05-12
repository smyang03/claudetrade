from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime_paths import get_runtime_path


WEAKEN_READY_COUNT = {"KR": 3, "US": 3}
WEAKEN_MAE_PCT = {"KR": -2.0, "US": -2.5}
WEAKEN_MFE_MAX_PCT = {"KR": 1.0, "US": 1.0}
FAILED_READY_DROP_PCT = {"KR": -3.0, "US": -3.0}
STRONG_READY_GAIN_PCT = {"KR": 2.0, "US": 1.5}
WATCH_MOVE_PCT = {"KR": 2.0, "US": 1.5}

INTERESTING_STATES = {
    "STRONG_READY",
    "WEAKENING_READY",
    "FAILED_READY",
    "WATCH_STRENGTHENING",
    "WATCH_WEAK",
}


def normalize_ticker(market: str, ticker: Any) -> str:
    raw = str(ticker or "").strip()
    return raw.upper() if str(market or "").upper() == "US" else raw


def compact_session_date(session_date: str) -> str:
    return str(session_date or "").replace("-", "")


def candidate_health_path(market: str, session_date: str) -> Path:
    market_key = _market_key(market)
    day = compact_session_date(session_date)
    return get_runtime_path("state", f"candidate_health_{market_key}_{day}.json")


class CandidateHealthTracker:
    schema_version = 1

    def __init__(
        self,
        market: str,
        session_date: str,
        *,
        path: str | Path | None = None,
    ) -> None:
        self.market = _market_key(market)
        self.session_date = str(session_date or "")
        self.path = Path(path) if path else candidate_health_path(self.market, self.session_date)
        self.data = self._load()

    def update_selection(
        self,
        *,
        watchlist: list[Any],
        trade_ready: list[Any],
        price_by_ticker: dict[str, Any] | None = None,
        ready_failure_reasons: dict[str, list[str]] | None = None,
        phase: str = "",
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        ts = (now or datetime.now()).isoformat(timespec="seconds")
        prices = {
            normalize_ticker(self.market, ticker): _safe_float(price)
            for ticker, price in (price_by_ticker or {}).items()
        }
        watch_order = _unique_norm(self.market, watchlist)
        ready_order = _unique_norm(self.market, trade_ready)
        touched: list[str] = []
        seen_in_event: set[str] = set()

        for ticker in watch_order:
            self._touch_seen(ticker, prices.get(ticker, 0.0), ts, phase, status="WATCH")
            touched.append(ticker)
            seen_in_event.add(ticker)

        for ticker in ready_order:
            if ticker not in seen_in_event:
                self._touch_seen(ticker, prices.get(ticker, 0.0), ts, phase, status="WATCH")
                touched.append(ticker)
                seen_in_event.add(ticker)
            self._touch_ready(ticker, prices.get(ticker, 0.0), ts, phase)
            if ticker not in touched:
                touched.append(ticker)

        for raw_ticker, reasons in (ready_failure_reasons or {}).items():
            ticker = normalize_ticker(self.market, raw_ticker)
            if not ticker:
                continue
            if ticker not in seen_in_event:
                self._touch_seen(ticker, prices.get(ticker, 0.0), ts, phase, status="WATCH")
                seen_in_event.add(ticker)
            self._touch_ready_failure(ticker, reasons, ts, phase)
            if ticker not in touched:
                touched.append(ticker)

        unique_touched = list(dict.fromkeys(touched))
        self._enforce_invariants(unique_touched)
        self.data["updated_at"] = ts
        self.data["last_phase"] = str(phase or "")
        self.save()
        return [self.state_for(ticker) for ticker in unique_touched]

    def state_for(self, ticker: Any) -> dict[str, Any]:
        ticker_key = normalize_ticker(self.market, ticker)
        rec = dict((self.data.get("tickers") or {}).get(ticker_key) or {})
        if not rec:
            return {"ticker": ticker_key, "health_state": "OBSERVE"}

        first_ready_price = _safe_float(rec.get("first_ready_price"))
        first_seen_price = _safe_float(rec.get("first_seen_price"))
        last_price = _safe_float(rec.get("last_price"))
        ready_count = int(rec.get("ready_count") or 0)
        seen_count = int(rec.get("seen_count") or 0)
        mae_pct = _safe_float(rec.get("mae_pct"))
        mfe_pct = _safe_float(rec.get("mfe_pct"))
        current_ready = _pct(last_price, first_ready_price)
        current_seen = _pct(last_price, first_seen_price)
        recovered = bool(rec.get("recovered_first_ready"))

        state = "OBSERVE"
        if ready_count > 0:
            if (
                ready_count >= _threshold(WEAKEN_READY_COUNT, self.market)
                and current_ready is not None
                and current_ready <= _threshold(FAILED_READY_DROP_PCT, self.market)
                and not recovered
            ):
                state = "FAILED_READY"
            elif (
                ready_count >= _threshold(WEAKEN_READY_COUNT, self.market)
                and current_ready is not None
                and current_ready < 0
                and mfe_pct < _threshold(WEAKEN_MFE_MAX_PCT, self.market)
                and mae_pct <= _threshold(WEAKEN_MAE_PCT, self.market)
            ):
                state = "WEAKENING_READY"
            elif (
                ready_count >= 2
                and current_ready is not None
                and current_ready >= _threshold(STRONG_READY_GAIN_PCT, self.market)
                and mae_pct > _threshold(WEAKEN_MAE_PCT, self.market)
            ):
                state = "STRONG_READY"
            else:
                state = "STABLE_READY"
        elif seen_count >= 2 and current_seen is not None:
            if current_seen >= _threshold(WATCH_MOVE_PCT, self.market):
                state = "WATCH_STRENGTHENING"
            elif current_seen <= _threshold(WEAKEN_MAE_PCT, self.market):
                state = "WATCH_WEAK"

        rec.update(
            {
                "ticker": ticker_key,
                "market": self.market,
                "session_date": self.session_date,
                "current_vs_first_ready_pct": _round_pct(current_ready),
                "current_vs_first_seen_pct": _round_pct(current_seen),
                "health_state": state,
            }
        )
        return rec

    def states_for(self, tickers: list[Any]) -> list[dict[str, Any]]:
        return [self.state_for(ticker) for ticker in _unique_norm(self.market, tickers)]

    def interesting_states(self, states: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
        out = [state for state in states if str(state.get("health_state") or "") in INTERESTING_STATES]
        out.sort(key=_interesting_sort_key)
        return out[: max(0, int(limit or 0))]

    def state_counts(self, states: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for state in states or []:
            key = str(state.get("health_state") or "OBSERVE")
            counts[key] = counts.get(key, 0) + 1
        return counts

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
                if isinstance(data, dict):
                    data.setdefault("schema_version", self.schema_version)
                    data.setdefault("market", self.market)
                    data.setdefault("session_date", self.session_date)
                    data.setdefault("tickers", {})
                    return data
            except Exception:
                pass
        return {
            "schema_version": self.schema_version,
            "market": self.market,
            "session_date": self.session_date,
            "updated_at": "",
            "last_phase": "",
            "tickers": {},
        }

    def _record(self, ticker: str) -> dict[str, Any]:
        tickers = self.data.setdefault("tickers", {})
        rec = tickers.get(ticker)
        if not isinstance(rec, dict):
            rec = {
                "first_seen_at": "",
                "first_seen_price": 0.0,
                "first_ready_at": "",
                "first_ready_price": 0.0,
                "last_seen_at": "",
                "last_price": 0.0,
                "seen_count": 0,
                "ready_count": 0,
                "mfe_pct": 0.0,
                "mae_pct": 0.0,
                "recovered_first_ready": False,
                "failed_ready_count": 0,
                "stale_cycle_count": 0,
                "failed_ready_reasons": [],
                "last_failed_ready_at": "",
                "last_status": "",
            }
            tickers[ticker] = rec
        return rec

    def _touch_seen(self, ticker: str, price: float, ts: str, phase: str, *, status: str) -> None:
        rec = self._record(ticker)
        if not rec.get("first_seen_at"):
            rec["first_seen_at"] = ts
        if _safe_float(rec.get("first_seen_price")) <= 0 and price > 0:
            rec["first_seen_price"] = float(price)
        rec["seen_count"] = int(rec.get("seen_count") or 0) + 1
        rec["last_seen_at"] = ts
        rec["last_status"] = status
        rec["last_phase"] = str(phase or "")
        self._update_price_metrics(rec, price)

    def _touch_ready(self, ticker: str, price: float, ts: str, phase: str) -> None:
        rec = self._record(ticker)
        if not rec.get("first_ready_at"):
            rec["first_ready_at"] = ts
        if _safe_float(rec.get("first_ready_price")) <= 0 and price > 0:
            rec["first_ready_price"] = float(price)
        rec["ready_count"] = int(rec.get("ready_count") or 0) + 1
        rec["last_seen_at"] = ts
        rec["last_status"] = "TRADE_READY"
        rec["last_phase"] = str(phase or "")
        self._update_price_metrics(rec, price)

    def _touch_ready_failure(self, ticker: str, reasons: list[str] | None, ts: str, phase: str) -> None:
        rec = self._record(ticker)
        cleaned = [
            str(reason or "").strip()
            for reason in (reasons or [])
            if str(reason or "").strip()
        ]
        if not cleaned:
            cleaned = ["ready_route_failed"]
        rec["failed_ready_count"] = int(rec.get("failed_ready_count") or 0) + 1
        rec["stale_cycle_count"] = int(rec.get("stale_cycle_count") or 0) + 1
        rec["failed_ready_reasons"] = list(dict.fromkeys(list(rec.get("failed_ready_reasons") or []) + cleaned))[-12:]
        rec["last_failed_ready_at"] = ts
        rec["last_seen_at"] = ts
        rec["last_status"] = "READY_FAILED"
        rec["last_phase"] = str(phase or "")

    def _update_price_metrics(self, rec: dict[str, Any], price: float) -> None:
        if price <= 0:
            return
        rec["last_price"] = float(price)
        first_ready = _safe_float(rec.get("first_ready_price"))
        if first_ready <= 0:
            return
        current = ((float(price) / first_ready) - 1.0) * 100.0
        rec["mfe_pct"] = round(max(_safe_float(rec.get("mfe_pct")), current), 4)
        rec["mae_pct"] = round(min(_safe_float(rec.get("mae_pct")), current), 4)
        if _safe_float(rec.get("mae_pct")) < 0 and current >= 0:
            rec["recovered_first_ready"] = True

    def _enforce_invariants(self, tickers: list[str]) -> None:
        for ticker in tickers:
            rec = self._record(ticker)
            seen = int(rec.get("seen_count") or 0)
            ready = int(rec.get("ready_count") or 0)
            if seen < ready:
                rec["seen_count"] = ready


def _market_key(market: str) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value or "").replace(",", ""))
    except Exception:
        return float(default)


def _pct(current: float, base: float) -> float | None:
    if current <= 0 or base <= 0:
        return None
    return ((current / base) - 1.0) * 100.0


def _round_pct(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _unique_norm(market: str, values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        ticker = normalize_ticker(market, value)
        if not ticker or ticker in seen:
            continue
        out.append(ticker)
        seen.add(ticker)
    return out


def _threshold(table: dict[str, float], market: str) -> float:
    return float(table.get(_market_key(market), table.get("KR", 0.0)))


def _interesting_sort_key(state: dict[str, Any]) -> tuple[int, float]:
    order = {
        "FAILED_READY": 0,
        "WEAKENING_READY": 1,
        "STRONG_READY": 2,
        "WATCH_WEAK": 3,
        "WATCH_STRENGTHENING": 4,
    }
    return (
        order.get(str(state.get("health_state") or ""), 99),
        _safe_float(state.get("current_vs_first_ready_pct"), _safe_float(state.get("current_vs_first_seen_pct"))),
    )

