from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - python<3.9 fallback
    ZoneInfo = None  # type: ignore

from runtime_paths import get_runtime_path


KST = ZoneInfo("Asia/Seoul") if ZoneInfo is not None else timezone(timedelta(hours=9))


def _now_kst() -> datetime:
    return datetime.now(KST)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _pct_change(start: Any, end: Any) -> Optional[float]:
    start_f = _safe_float(start)
    end_f = _safe_float(end)
    if start_f <= 0 or end_f <= 0:
        return None
    return (end_f / start_f - 1.0) * 100.0


def _parse_dt(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except Exception:
        return None


def _minutes_between(start: Any, end: Any) -> Optional[float]:
    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end)
    if not start_dt or not end_dt:
        return None
    return round((end_dt - start_dt).total_seconds() / 60.0, 4)


def _seconds_between(start: Any, end: Any) -> Optional[float]:
    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end)
    if not start_dt or not end_dt:
        return None
    return round((end_dt - start_dt).total_seconds(), 3)


def _market_key(market: str) -> str:
    return str(market or "").upper() or "KR"


def _ticker_key(ticker: str, market: str) -> str:
    raw = str(ticker or "").strip()
    return raw.upper() if _market_key(market) == "US" else raw


class EntryTimingTracker:
    """Tracks Path A candidate-to-order timing without changing entry decisions."""

    def __init__(
        self,
        *,
        runtime_mode: str,
        log_dir: Optional[Path] = None,
        now_func: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.runtime_mode = str(runtime_mode or "live").lower()
        self.log_dir = log_dir
        self._now_func = now_func or _now_kst
        self._state: Dict[str, Dict[str, Dict[str, Any]]] = {"KR": {}, "US": {}}

    def mark_candidates(
        self,
        market: str,
        tickers: Iterable[str],
        *,
        source: str,
        session_date: str,
        price_by_ticker: Optional[Dict[str, Any]] = None,
    ) -> None:
        market_key = _market_key(market)
        price_by_ticker = price_by_ticker or {}
        for ticker in tickers or []:
            ticker_key = _ticker_key(str(ticker), market_key)
            if not ticker_key:
                continue
            state = self._ensure_state(market_key, ticker_key, session_date, source)
            now_iso = self._now_iso()
            if not state.get("candidate_detected_at"):
                state["candidate_detected_at"] = now_iso
                state["candidate_source"] = source
                price = _safe_float(
                    price_by_ticker.get(ticker_key, price_by_ticker.get(str(ticker), 0.0))
                )
                if price > 0:
                    state["candidate_detected_price"] = price
                self._write_event("candidate_detected", market_key, ticker_key, session_date, state)
            else:
                state["last_candidate_seen_at"] = now_iso
                state["last_candidate_source"] = source

    def mark_signal_check(
        self,
        market: str,
        ticker: str,
        *,
        session_date: str,
        price: Any = None,
    ) -> None:
        market_key = _market_key(market)
        ticker_key = _ticker_key(ticker, market_key)
        if not ticker_key:
            return
        state = self._ensure_state(market_key, ticker_key, session_date, "runtime_unknown")
        now_iso = self._now_iso()
        state["last_signal_checked_at"] = now_iso
        state["signal_check_count"] = int(state.get("signal_check_count", 0) or 0) + 1
        price_f = _safe_float(price)
        if price_f > 0:
            state["last_signal_checked_price"] = price_f
            if not state.get("candidate_detected_price"):
                state["candidate_detected_price"] = price_f
        if not state.get("first_signal_checked_at"):
            state["first_signal_checked_at"] = now_iso
            self._write_event("first_signal_checked", market_key, ticker_key, session_date, state)

    def mark_signal_fired(
        self,
        market: str,
        ticker: str,
        *,
        session_date: str,
        price: Any,
        strategy: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        market_key = _market_key(market)
        ticker_key = _ticker_key(ticker, market_key)
        state = self._ensure_state(market_key, ticker_key, session_date, "runtime_unknown")
        now_iso = self._now_iso()
        state["last_signal_fired_at"] = now_iso
        if not state.get("signal_fired_at"):
            state["signal_fired_at"] = now_iso
            state["signal_fired_price"] = _safe_float(price)
            state["signal_strategy"] = str(strategy or "")
            state["signal_reason"] = str(reason or "")[:500]
        self._write_event("signal_fired", market_key, ticker_key, session_date, state)
        return self.snapshot(market_key, ticker_key, session_date)

    def mark_order_sent(
        self,
        market: str,
        ticker: str,
        *,
        session_date: str,
        price: Any,
        order_no: str = "",
        strategy: str = "",
        qty: Any = None,
        intraday_high: Any = None,
    ) -> Dict[str, Any]:
        market_key = _market_key(market)
        ticker_key = _ticker_key(ticker, market_key)
        state = self._ensure_state(market_key, ticker_key, session_date, "runtime_unknown")
        state["order_sent_at"] = self._now_iso()
        state["order_sent_price"] = _safe_float(price)
        state["order_no"] = str(order_no or "")
        state["order_strategy"] = str(strategy or state.get("signal_strategy") or "")
        if qty is not None:
            state["order_qty"] = int(_safe_float(qty))
        high_f = _safe_float(intraday_high)
        if high_f > 0:
            state["intraday_high_at_order"] = high_f
        self._write_event("order_sent", market_key, ticker_key, session_date, state)
        return self.snapshot(market_key, ticker_key, session_date)

    def mark_filled(
        self,
        market: str,
        ticker: str,
        *,
        session_date: str,
        fill_price: Any,
        order_no: str = "",
        qty: Any = None,
        partial: bool = False,
    ) -> Dict[str, Any]:
        market_key = _market_key(market)
        ticker_key = _ticker_key(ticker, market_key)
        state = self._ensure_state(market_key, ticker_key, session_date, "runtime_unknown")
        state["filled_at"] = self._now_iso()
        state["filled_price"] = _safe_float(fill_price)
        state["fill_order_no"] = str(order_no or state.get("order_no") or "")
        if qty is not None:
            state["filled_qty"] = int(_safe_float(qty))
        state["partial_fill"] = bool(partial)
        self._write_event("partial_filled" if partial else "filled", market_key, ticker_key, session_date, state)
        return self.snapshot(market_key, ticker_key, session_date)

    def snapshot(self, market: str, ticker: str, session_date: str) -> Dict[str, Any]:
        market_key = _market_key(market)
        ticker_key = _ticker_key(ticker, market_key)
        state = dict(self._state.get(market_key, {}).get(self._state_key(session_date, ticker_key), {}) or {})
        state.update(self._derived_metrics(state))
        return state

    def _ensure_state(self, market: str, ticker: str, session_date: str, source: str) -> Dict[str, Any]:
        market_key = _market_key(market)
        ticker_key = _ticker_key(ticker, market_key)
        session_key = str(session_date or self._now_func().date().isoformat())
        key = self._state_key(session_key, ticker_key)
        market_state = self._state.setdefault(market_key, {})
        if key not in market_state:
            market_state[key] = {
                "runtime_mode": self.runtime_mode,
                "market": market_key,
                "ticker": ticker_key,
                "session_date": session_key,
                "candidate_detected_at": "",
                "candidate_source": str(source or ""),
                "signal_check_count": 0,
            }
        return market_state[key]

    def _write_event(
        self,
        event: str,
        market: str,
        ticker: str,
        session_date: str,
        state: Dict[str, Any],
    ) -> None:
        payload = {
            "event": event,
            "occurred_at": self._now_iso(),
            "runtime_mode": self.runtime_mode,
            "market": _market_key(market),
            "ticker": _ticker_key(ticker, market),
            "session_date": str(session_date or ""),
            "state_key": self._state_key(str(session_date or ""), _ticker_key(ticker, market)),
            "state": self.snapshot(market, ticker, session_date),
        }
        path = self._log_path(market, session_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n")

    def _log_path(self, market: str, session_date: str) -> Path:
        day = str(session_date or self._now_func().date().isoformat()).replace("-", "")
        file_name = f"{self.runtime_mode}_{day}_{_market_key(market)}.jsonl"
        if self.log_dir is not None:
            return self.log_dir / file_name
        return get_runtime_path("logs", "entry_timing", file_name)

    def _now_iso(self) -> str:
        return self._now_func().astimezone(KST).isoformat(timespec="seconds")

    @staticmethod
    def _state_key(session_date: str, ticker: str) -> str:
        return f"{session_date}:{ticker}"

    @staticmethod
    def _derived_metrics(state: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        out["candidate_to_signal_delay_min"] = _minutes_between(
            state.get("candidate_detected_at"), state.get("signal_fired_at")
        )
        out["candidate_to_first_signal_check_delay_min"] = _minutes_between(
            state.get("candidate_detected_at"), state.get("first_signal_checked_at")
        )
        out["signal_to_order_delay_min"] = _minutes_between(
            state.get("signal_fired_at"), state.get("order_sent_at")
        )
        out["candidate_to_order_delay_min"] = _minutes_between(
            state.get("candidate_detected_at"), state.get("order_sent_at")
        )
        out["order_to_fill_delay_sec"] = _seconds_between(
            state.get("order_sent_at"), state.get("filled_at")
        )
        out["price_change_candidate_to_order_pct"] = _pct_change(
            state.get("candidate_detected_price"), state.get("order_sent_price")
        )
        out["price_change_signal_to_order_pct"] = _pct_change(
            state.get("signal_fired_price"), state.get("order_sent_price")
        )
        high = _safe_float(state.get("intraday_high_at_order"))
        order_price = _safe_float(state.get("order_sent_price"))
        out["entry_vs_intraday_high_pct"] = _pct_change(high, order_price) if high > 0 else None
        return out


def build_entry_timing_summary(
    *,
    market: Optional[str] = None,
    runtime_mode: Optional[str] = None,
    session_date: Optional[str] = None,
    log_dir: Optional[Path] = None,
    recent_limit: int = 20,
) -> Dict[str, Any]:
    runtime_key = str(runtime_mode or "live").lower()
    session_key = str(session_date or _now_kst().date().isoformat())
    markets = [_market_key(market)] if market else ["KR", "US"]
    rows: List[Dict[str, Any]] = []
    files: List[str] = []
    for market_key in markets:
        path = _entry_timing_log_path(runtime_key, session_key, market_key, log_dir)
        files.append(str(path))
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    rows.append(row)

    if not rows:
        return {
            "missing": True,
            "runtime_mode": runtime_key,
            "market": _market_key(market) if market else "ALL",
            "session_date": session_key,
            "files": files,
            "row_count": 0,
            "events": {},
            "averages": {},
            "recent": [],
        }

    latest_by_key: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("state_key") or "")
        if not key:
            continue
        latest_by_key[key] = row

    states = [dict((row.get("state") or {})) for row in latest_by_key.values()]
    counts = Counter(str(row.get("event") or "") for row in rows)
    averages = {
        "candidate_to_first_signal_check_delay_min": _average_metric(states, "candidate_to_first_signal_check_delay_min"),
        "candidate_to_signal_delay_min": _average_metric(states, "candidate_to_signal_delay_min"),
        "candidate_to_order_delay_min": _average_metric(states, "candidate_to_order_delay_min"),
        "signal_to_order_delay_min": _average_metric(states, "signal_to_order_delay_min"),
        "order_to_fill_delay_sec": _average_metric(states, "order_to_fill_delay_sec"),
        "price_change_candidate_to_order_pct": _average_metric(states, "price_change_candidate_to_order_pct"),
        "entry_vs_intraday_high_pct": _average_metric(states, "entry_vs_intraday_high_pct"),
    }
    important = [
        row for row in rows
        if str(row.get("event") or "") in {"signal_fired", "order_sent", "partial_filled", "filled"}
    ]
    important.sort(key=lambda r: str(r.get("occurred_at") or ""))
    recent = [_compact_row(row) for row in important[-max(1, int(recent_limit)):]]
    return {
        "missing": False,
        "runtime_mode": runtime_key,
        "market": _market_key(market) if market else "ALL",
        "session_date": session_key,
        "files": files,
        "row_count": len(rows),
        "events": dict(counts),
        "averages": averages,
        "recent": recent,
    }


def _entry_timing_log_path(runtime_mode: str, session_date: str, market: str, log_dir: Optional[Path]) -> Path:
    day = str(session_date or _now_kst().date().isoformat()).replace("-", "")
    name = f"{runtime_mode}_{day}_{_market_key(market)}.jsonl"
    if log_dir is not None:
        return log_dir / name
    return get_runtime_path("logs", "entry_timing", name)


def _average_metric(states: Iterable[Dict[str, Any]], key: str) -> Optional[float]:
    values = []
    for state in states:
        value = state.get(key)
        if value is None or value == "":
            continue
        try:
            values.append(float(value))
        except Exception:
            continue
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _compact_row(row: Dict[str, Any]) -> Dict[str, Any]:
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    return {
        "event": row.get("event", ""),
        "occurred_at": row.get("occurred_at", ""),
        "market": row.get("market", ""),
        "ticker": row.get("ticker", ""),
        "candidate_source": state.get("candidate_source", ""),
        "strategy": state.get("signal_strategy") or state.get("order_strategy") or "",
        "signal_check_count": state.get("signal_check_count", 0),
        "candidate_to_first_signal_check_delay_min": state.get("candidate_to_first_signal_check_delay_min"),
        "candidate_to_signal_delay_min": state.get("candidate_to_signal_delay_min"),
        "candidate_to_order_delay_min": state.get("candidate_to_order_delay_min"),
        "signal_to_order_delay_min": state.get("signal_to_order_delay_min"),
        "order_to_fill_delay_sec": state.get("order_to_fill_delay_sec"),
        "price_change_candidate_to_order_pct": state.get("price_change_candidate_to_order_pct"),
        "entry_vs_intraday_high_pct": state.get("entry_vs_intraday_high_pct"),
        "order_no": state.get("order_no") or state.get("fill_order_no") or "",
    }
