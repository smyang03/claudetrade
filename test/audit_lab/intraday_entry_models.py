"""Intraday entry models for audit-lab experiments."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd


INTRADAY_ENTRY_MODELS = ("opening_range_reclaim", "vwap_reclaim")


@dataclass(frozen=True)
class IntradayEntry:
    entry_timestamp: str
    entry_price: float
    model: str
    reason: str
    opening_low_breach: int
    minutes_from_open: int

    def to_dict(self) -> dict:
        return asdict(self)


def _valid_price(value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number > 0 and number == number


def _pnl_pct(base: float, value: float) -> float:
    if base <= 0:
        return 0.0
    return (value / base - 1.0) * 100.0


def _session_frame(intraday: pd.DataFrame, entry_date: object) -> pd.DataFrame:
    if intraday is None or intraday.empty or "date" not in intraday.columns:
        return pd.DataFrame()
    frame = intraday.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    target = pd.to_datetime(entry_date).date()
    frame = frame[frame["date"].dt.date == target]
    return frame.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def _with_minutes(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    start = pd.to_datetime(frame["date"].iloc[0])
    out = frame.copy()
    out["minutes_from_open"] = (pd.to_datetime(out["date"]) - start).dt.total_seconds() // 60
    return out


def _opening_context(
    session: pd.DataFrame,
    *,
    opening_minutes: int,
    stop_loss_pct: float,
    signal_close: float,
    max_gap_pct: float,
) -> tuple[pd.DataFrame, pd.DataFrame, float, int] | None:
    session = _with_minutes(session)
    if session.empty:
        return None
    day_open = float(session.iloc[0].get("open", 0) or 0)
    if not _valid_price(day_open):
        return None
    if _valid_price(signal_close) and _pnl_pct(float(signal_close), day_open) > float(max_gap_pct):
        return None
    opening = session[session["minutes_from_open"] < int(opening_minutes)]
    after = session[session["minutes_from_open"] >= int(opening_minutes)]
    if opening.empty or after.empty:
        return None
    opening_low = float(opening["low"].min())
    opening_low_breach = 1 if opening_low <= day_open * (1.0 - float(stop_loss_pct)) else 0
    if opening_low_breach:
        return None
    return opening, after, day_open, opening_low_breach


def diagnose_intraday_entry(
    intraday: pd.DataFrame,
    *,
    model: str,
    entry_date: object,
    signal_close: float,
    stop_loss_pct: float,
    opening_minutes: int = 30,
    deadline_minutes: int = 180,
    max_gap_pct: float = 1.5,
) -> dict:
    """Return one signal's intraday entry status and first blocking reason."""

    session = _session_frame(intraday, entry_date)
    if session.empty:
        return {"status": "blocked", "reason": "NO_INTRADAY_SESSION", "entry_created": 0}
    session = _with_minutes(session)
    day_open = float(session.iloc[0].get("open", 0) or 0)
    if not _valid_price(day_open):
        return {"status": "blocked", "reason": "INVALID_DAY_OPEN", "entry_created": 0}
    gap_pct = _pnl_pct(float(signal_close), day_open) if _valid_price(signal_close) else 0.0
    if _valid_price(signal_close) and gap_pct > float(max_gap_pct):
        return {
            "status": "blocked",
            "reason": "MAX_GAP_EXCEEDED",
            "entry_created": 0,
            "gap_pct": round(gap_pct, 6),
        }
    opening = session[session["minutes_from_open"] < int(opening_minutes)]
    after = session[session["minutes_from_open"] >= int(opening_minutes)]
    if opening.empty:
        return {"status": "blocked", "reason": "NO_OPENING_WINDOW", "entry_created": 0}
    if after.empty:
        return {"status": "blocked", "reason": "NO_AFTER_OPENING_WINDOW", "entry_created": 0}
    opening_low = float(opening["low"].min())
    stop_line = day_open * (1.0 - float(stop_loss_pct))
    if opening_low <= stop_line:
        return {
            "status": "blocked",
            "reason": "OPENING_STOP_BREACH",
            "entry_created": 0,
            "gap_pct": round(gap_pct, 6),
            "opening_low": round(opening_low, 6),
            "stop_line": round(stop_line, 6),
        }

    entry = find_intraday_entry(
        intraday,
        model=model,
        entry_date=entry_date,
        signal_close=signal_close,
        stop_loss_pct=stop_loss_pct,
        opening_minutes=opening_minutes,
        deadline_minutes=deadline_minutes,
        max_gap_pct=max_gap_pct,
    )
    if entry is not None:
        return {
            "status": "entered",
            "reason": entry.reason,
            "entry_created": 1,
            "gap_pct": round(gap_pct, 6),
            "entry_timestamp": entry.entry_timestamp,
            "entry_price": entry.entry_price,
            "minutes_from_open": entry.minutes_from_open,
        }
    if model == "opening_range_reclaim":
        reason = "OPENING_RANGE_RECLAIM_FAILED"
    elif model == "vwap_reclaim":
        reason = "VWAP_RECLAIM_FAILED"
    else:
        reason = "ENTRY_MODEL_FAILED"
    return {
        "status": "blocked",
        "reason": reason,
        "entry_created": 0,
        "gap_pct": round(gap_pct, 6),
        "deadline_minutes": int(deadline_minutes),
    }


def opening_range_reclaim_entry(
    intraday: pd.DataFrame,
    *,
    entry_date: object,
    signal_close: float,
    stop_loss_pct: float,
    opening_minutes: int = 30,
    deadline_minutes: int = 150,
    max_gap_pct: float = 1.5,
    reclaim_buffer_pct: float = 0.0,
) -> IntradayEntry | None:
    session = _session_frame(intraday, entry_date)
    context = _opening_context(
        session,
        opening_minutes=opening_minutes,
        stop_loss_pct=stop_loss_pct,
        signal_close=signal_close,
        max_gap_pct=max_gap_pct,
    )
    if context is None:
        return None
    opening, after, _day_open, opening_low_breach = context
    opening_high = float(opening["high"].max())
    reclaim_price = opening_high * (1.0 + float(reclaim_buffer_pct) / 100.0)
    candidates = after[
        (after["minutes_from_open"] <= int(deadline_minutes))
        & (after["close"].astype(float) >= reclaim_price)
    ]
    if candidates.empty:
        return None
    row = candidates.iloc[0]
    entry_price = float(row.get("close", 0) or 0)
    if not _valid_price(entry_price):
        return None
    return IntradayEntry(
        entry_timestamp=pd.to_datetime(row["date"]).isoformat(),
        entry_price=round(entry_price, 6),
        model="opening_range_reclaim",
        reason="opening_range_high_reclaimed",
        opening_low_breach=opening_low_breach,
        minutes_from_open=int(row.get("minutes_from_open", 0) or 0),
    )


def vwap_reclaim_entry(
    intraday: pd.DataFrame,
    *,
    entry_date: object,
    signal_close: float,
    stop_loss_pct: float,
    opening_minutes: int = 30,
    deadline_minutes: int = 180,
    max_gap_pct: float = 1.5,
    min_vwap_buffer_pct: float = 0.0,
) -> IntradayEntry | None:
    session = _session_frame(intraday, entry_date)
    context = _opening_context(
        session,
        opening_minutes=opening_minutes,
        stop_loss_pct=stop_loss_pct,
        signal_close=signal_close,
        max_gap_pct=max_gap_pct,
    )
    if context is None:
        return None
    _opening, after, day_open, opening_low_breach = context
    full = _with_minutes(session)
    typical_price = (full["high"].astype(float) + full["low"].astype(float) + full["close"].astype(float)) / 3.0
    volume = full["volume"].astype(float).clip(lower=0.0)
    full["vwap"] = (typical_price * volume).cumsum() / volume.replace(0, pd.NA).cumsum()
    full["vwap"] = full["vwap"].ffill()
    after = full[full["minutes_from_open"] >= int(opening_minutes)]
    candidates = after[
        (after["minutes_from_open"] <= int(deadline_minutes))
        & (after["close"].astype(float) >= after["vwap"].astype(float) * (1.0 + float(min_vwap_buffer_pct) / 100.0))
        & (after["close"].astype(float) >= day_open)
    ]
    if candidates.empty:
        return None
    row = candidates.iloc[0]
    entry_price = float(row.get("close", 0) or 0)
    if not _valid_price(entry_price):
        return None
    return IntradayEntry(
        entry_timestamp=pd.to_datetime(row["date"]).isoformat(),
        entry_price=round(entry_price, 6),
        model="vwap_reclaim",
        reason="vwap_reclaimed",
        opening_low_breach=opening_low_breach,
        minutes_from_open=int(row.get("minutes_from_open", 0) or 0),
    )


def find_intraday_entry(
    intraday: pd.DataFrame,
    *,
    model: str,
    entry_date: object,
    signal_close: float,
    stop_loss_pct: float,
    opening_minutes: int = 30,
    deadline_minutes: int = 180,
    max_gap_pct: float = 1.5,
) -> IntradayEntry | None:
    if model == "opening_range_reclaim":
        return opening_range_reclaim_entry(
            intraday,
            entry_date=entry_date,
            signal_close=signal_close,
            stop_loss_pct=stop_loss_pct,
            opening_minutes=opening_minutes,
            deadline_minutes=deadline_minutes,
            max_gap_pct=max_gap_pct,
        )
    if model == "vwap_reclaim":
        return vwap_reclaim_entry(
            intraday,
            entry_date=entry_date,
            signal_close=signal_close,
            stop_loss_pct=stop_loss_pct,
            opening_minutes=opening_minutes,
            deadline_minutes=deadline_minutes,
            max_gap_pct=max_gap_pct,
        )
    raise ValueError(f"unknown intraday entry model: {model}")
